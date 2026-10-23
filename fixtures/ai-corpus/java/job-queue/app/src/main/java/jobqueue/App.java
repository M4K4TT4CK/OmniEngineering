package jobqueue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;

/**
 * HTTP API:
 *   POST /jobs        {"type": "...", "payload": {...}}  -> 202 {"id": "..."}
 *   GET  /jobs/{id}   -> 200 {"id","type","status","attempts","result","error"}
 */
public final class App {
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final int MAX_BODY_BYTES = 1 << 20;

    public static void main(String[] args) throws IOException {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        int workers = Integer.parseInt(System.getenv().getOrDefault("WORKERS", "4"));
        JobQueue queue = new JobQueue(Handlers.defaults(System.getenv()), workers, 3, 1000);
        HttpServer server = createServer(queue, port);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.stop(1);
            queue.close();
        }));
        server.start();
        System.out.println("Listening on port " + server.getAddress().getPort() + " with " + workers + " workers");
    }

    /** Creates (but does not start) the server. Port 0 picks a free port. */
    public static HttpServer createServer(JobQueue queue, int port) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.setExecutor(Executors.newFixedThreadPool(8));
        server.createContext("/jobs", ex -> {
            try {
                route(queue, ex);
            } catch (Exception e) {
                send(ex, 500, error("Internal error"));
            } finally {
                ex.close();
            }
        });
        return server;
    }

    private static void route(JobQueue queue, HttpExchange ex) throws IOException {
        String path = ex.getRequestURI().getPath();
        String method = ex.getRequestMethod();

        if (path.equals("/jobs") || path.equals("/jobs/")) {
            if (!method.equals("POST")) {
                ex.getResponseHeaders().set("Allow", "POST");
                send(ex, 405, error("Method not allowed"));
                return;
            }
            submit(queue, ex);
        } else if (path.startsWith("/jobs/") && path.indexOf('/', 6) < 0) {
            if (!method.equals("GET")) {
                ex.getResponseHeaders().set("Allow", "GET");
                send(ex, 405, error("Method not allowed"));
                return;
            }
            JobQueue.Job job = queue.get(path.substring(6));
            if (job == null) {
                send(ex, 404, error("Job not found"));
            } else {
                send(ex, 200, describe(job));
            }
        } else {
            send(ex, 404, error("Not found"));
        }
    }

    private static void submit(JobQueue queue, HttpExchange ex) throws IOException {
        byte[] body;
        try (InputStream in = ex.getRequestBody()) {
            body = in.readNBytes(MAX_BODY_BYTES + 1);
        }
        if (body.length > MAX_BODY_BYTES) {
            send(ex, 413, error("Request body too large"));
            return;
        }
        JsonNode req;
        try {
            req = JSON.readTree(body);
        } catch (IOException e) {
            send(ex, 400, error("Invalid JSON"));
            return;
        }
        if (req == null || !req.isObject() || !req.path("type").isTextual()) {
            send(ex, 400, error("Body must be an object with a string \"type\" and an object \"payload\""));
            return;
        }
        try {
            JobQueue.Job job = queue.submit(req.get("type").asText(), req.get("payload"));
            ObjectNode out = JSON.createObjectNode();
            out.put("id", job.id);
            send(ex, 202, out);
        } catch (IllegalArgumentException e) {
            send(ex, 400, error(e.getMessage()));
        }
    }

    private static ObjectNode describe(JobQueue.Job job) {
        ObjectNode n = JSON.createObjectNode();
        n.put("id", job.id);
        n.put("type", job.type);
        n.put("status", job.status().name());
        n.put("attempts", job.attempts());
        n.set("result", JSON.valueToTree(job.result()));
        n.put("error", job.error());
        return n;
    }

    private static ObjectNode error(String message) {
        return JSON.createObjectNode().put("error", message);
    }

    private static void send(HttpExchange ex, int code, JsonNode body) throws IOException {
        byte[] bytes = JSON.writeValueAsString(body).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(code, bytes.length);
        ex.getResponseBody().write(bytes);
    }
}
