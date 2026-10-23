package tenantnotes;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import tenantnotes.Service.Note;
import tenantnotes.Service.Role;
import tenantnotes.Service.ServiceError;
import tenantnotes.Service.User;

/** JSON-over-HTTP front end for {@link Service}. Authenticate with an {@code Authorization: Bearer} token. */
public class Server {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final TypeReference<Map<String, Object>> OBJECT = new TypeReference<>() {};

    private record Reply(int status, Object body) {}

    private final Service service;
    private final HttpServer http;

    public Server(Service service, int port) throws IOException {
        this.service = service;
        this.http = HttpServer.create(new InetSocketAddress(port), 0);
        http.createContext("/", this::handle);
    }

    public int port() {
        return http.getAddress().getPort();
    }

    public void start() {
        http.start();
    }

    public void stop() {
        http.stop(0);
    }

    public static void main(String[] args) throws Exception {
        String dbUrl = System.getenv().getOrDefault("NOTES_DB_URL", "jdbc:sqlite:notes.db");
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        Service service = new Service(dbUrl);
        Server server = new Server(service, port);
        server.start();
        System.out.println("tenant-notes listening on port " + server.port());
    }

    private void handle(HttpExchange exchange) throws IOException {
        try {
            Reply reply = route(exchange);
            if (reply.body() == null) {
                exchange.sendResponseHeaders(reply.status(), -1);
            } else {
                respond(exchange, reply.status(), reply.body());
            }
        } catch (ServiceError e) {
            respond(exchange, e.status(), Map.of("error", e.getMessage()));
        } catch (RuntimeException e) {
            System.err.println("unhandled error: " + e);
            respond(exchange, 500, Map.of("error", "internal error"));
        } finally {
            exchange.close();
        }
    }

    private Reply route(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        List<String> path = Arrays.stream(exchange.getRequestURI().getPath().split("/"))
                .filter(s -> !s.isEmpty())
                .toList();
        Map<String, String> query = parseQuery(exchange.getRequestURI().getRawQuery());

        if (is(method, "POST", path, "orgs")) {
            Map<String, Object> body = readBody(exchange);
            User admin = service.createOrganisation(
                    text(body, "organisation"), text(body, "email"), text(body, "password"));
            return new Reply(201, userJson(admin));
        }
        if (is(method, "POST", path, "login")) {
            Map<String, Object> body = readBody(exchange);
            String token = service.login(text(body, "email"), text(body, "password"));
            return new Reply(200, Map.of("token", token));
        }
        if (is(method, "POST", path, "invitations")) {
            User actor = authenticate(exchange);
            Map<String, Object> body = readBody(exchange);
            String token = service.invite(actor, text(body, "email"), role(body));
            return new Reply(201, Map.of("invitationToken", token));
        }
        if (is(method, "POST", path, "invitations", "accept")) {
            Map<String, Object> body = readBody(exchange);
            String token = service.acceptInvitation(text(body, "token"), text(body, "password"));
            return new Reply(200, Map.of("token", token));
        }
        if (method.equals("PUT") && path.size() == 3 && path.get(0).equals("users") && path.get(2).equals("role")) {
            User actor = authenticate(exchange);
            Map<String, Object> body = readBody(exchange);
            service.changeRole(actor, id(path.get(1)), role(body));
            return new Reply(204, null);
        }
        if (is(method, "GET", path, "notes")) {
            User actor = authenticate(exchange);
            String q = query.get("q");
            List<Note> notes = q == null ? service.listNotes(actor) : service.searchNotes(actor, q);
            return new Reply(200, notes.stream().map(Server::noteJson).toList());
        }
        if (is(method, "POST", path, "notes")) {
            User actor = authenticate(exchange);
            Map<String, Object> body = readBody(exchange);
            return new Reply(201, noteJson(service.createNote(actor, text(body, "title"), text(body, "body"))));
        }
        if (path.size() == 2 && path.get(0).equals("notes")) {
            long id = id(path.get(1));
            User actor = authenticate(exchange);
            if (method.equals("GET")) {
                return new Reply(200, noteJson(service.getNote(actor, id)));
            }
            if (method.equals("PUT")) {
                Map<String, Object> body = readBody(exchange);
                Note note = service.updateNote(actor, id, text(body, "title"), text(body, "body"));
                return new Reply(200, noteJson(note));
            }
            if (method.equals("DELETE")) {
                service.deleteNote(actor, id);
                return new Reply(204, null);
            }
        }
        throw new ServiceError(404, "no such route");
    }

    private static boolean is(String method, String expected, List<String> path, String... segments) {
        return method.equals(expected) && path.equals(List.of(segments));
    }

    private User authenticate(HttpExchange exchange) {
        String header = exchange.getRequestHeaders().getFirst("Authorization");
        if (header == null || !header.startsWith("Bearer ")) {
            throw new ServiceError(401, "missing bearer token");
        }
        return service.authenticate(header.substring("Bearer ".length()).trim());
    }

    private static Map<String, Object> readBody(HttpExchange exchange) throws IOException {
        byte[] bytes = exchange.getRequestBody().readAllBytes();
        if (bytes.length == 0) {
            return Map.of();
        }
        try {
            Map<String, Object> body = JSON.readValue(bytes, OBJECT);
            return body == null ? Map.of() : body;
        } catch (JsonProcessingException e) {
            throw new ServiceError(400, "request body must be a JSON object");
        }
    }

    /** Returns the string value for {@code key}, or null when absent or not a string. */
    private static String text(Map<String, Object> body, String key) {
        return body.get(key) instanceof String s ? s : null;
    }

    private static Role role(Map<String, Object> body) {
        String value = text(body, "role");
        try {
            return Role.valueOf(value == null ? "" : value.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            throw new ServiceError(400, "role must be admin, editor or viewer");
        }
    }

    private static long id(String segment) {
        try {
            return Long.parseLong(segment);
        } catch (NumberFormatException e) {
            throw new ServiceError(404, "not found");
        }
    }

    private static Map<String, String> parseQuery(String raw) {
        Map<String, String> params = new HashMap<>();
        if (raw == null) {
            return params;
        }
        for (String pair : raw.split("&")) {
            int eq = pair.indexOf('=');
            String key = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            params.put(URLDecoder.decode(key, StandardCharsets.UTF_8), URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return params;
    }

    private static Map<String, Object> userJson(User user) {
        return Map.of(
                "id", user.id(),
                "organisationId", user.orgId(),
                "email", user.email(),
                "role", user.role().name());
    }

    private static Map<String, Object> noteJson(Note note) {
        return Map.of(
                "id", note.id(),
                "title", note.title(),
                "body", note.body(),
                "createdBy", note.createdBy(),
                "updatedAt", note.updatedAt());
    }

    private static void respond(HttpExchange exchange, int status, Object body) throws IOException {
        byte[] bytes = JSON.writeValueAsBytes(body);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }
}
