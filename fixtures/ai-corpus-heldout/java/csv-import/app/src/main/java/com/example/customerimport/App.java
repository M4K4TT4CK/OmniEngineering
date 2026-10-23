package com.example.customerimport;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.sql.SQLException;
import java.time.Clock;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * HTTP front end for {@link ImportService}.
 *
 * <pre>
 * POST /imports        upload a CSV (raw body, or multipart/form-data with a "file" part) -> 202 + import id
 * GET  /imports/{id}   progress and per-row errors (?errorsOffset=0&amp;errorsLimit=100)
 * </pre>
 */
public final class App {

    static final int DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
    static final int DEFAULT_ERRORS_LIMIT = 100;
    static final int MAX_ERRORS_LIMIT = 1000;

    private static final Logger LOG = Logger.getLogger(App.class.getName());
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final String IMPORTS_PREFIX = "/imports/";

    record Accepted(String importId, String status, String statusUrl) {}

    record ErrorBody(String error) {}

    private final ImportService service;
    private final int maxUploadBytes;
    private final HttpServer server;
    private final ExecutorService requestExecutor = Executors.newFixedThreadPool(8);

    public App(ImportService service, InetSocketAddress address, int maxUploadBytes) throws IOException {
        this.service = service;
        this.maxUploadBytes = maxUploadBytes;
        this.server = HttpServer.create(address, 0);
        server.createContext("/", this::handle);
        server.setExecutor(requestExecutor);
    }

    public static void main(String[] args) throws Exception {
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        String dbPath = System.getenv().getOrDefault("DB_PATH", "customers.db");
        ImportService service = new ImportService("jdbc:sqlite:" + dbPath, Clock.systemUTC(), 10);
        App app = new App(service, new InetSocketAddress(port), DEFAULT_MAX_UPLOAD_BYTES);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            app.stop();
            service.close();
        }));
        app.start();
        LOG.info("Listening on port " + app.port() + ", database " + dbPath);
    }

    public void start() {
        server.start();
    }

    public int port() {
        return server.getAddress().getPort();
    }

    public void stop() {
        server.stop(0);
        requestExecutor.shutdownNow();
    }

    private void handle(HttpExchange exchange) {
        try {
            route(exchange);
        } catch (Exception e) {
            LOG.log(Level.SEVERE, "Request failed: " + exchange.getRequestMethod() + " " + exchange.getRequestURI(), e);
            try {
                sendError(exchange, 500, "internal server error");
            } catch (IOException | RuntimeException ignored) {
                // Response headers were already sent; nothing more we can do.
            }
        } finally {
            exchange.close();
        }
    }

    private void route(HttpExchange exchange) throws IOException, SQLException {
        String path = exchange.getRequestURI().getPath();
        String method = exchange.getRequestMethod();
        if (path.equals("/imports")) {
            if (!method.equals("POST")) {
                methodNotAllowed(exchange, "POST");
                return;
            }
            upload(exchange);
        } else if (path.startsWith(IMPORTS_PREFIX) && path.length() > IMPORTS_PREFIX.length()
                && path.indexOf('/', IMPORTS_PREFIX.length()) < 0) {
            if (!method.equals("GET")) {
                methodNotAllowed(exchange, "GET");
                return;
            }
            status(exchange, path.substring(IMPORTS_PREFIX.length()));
        } else {
            sendError(exchange, 404, "not found");
        }
    }

    private void upload(HttpExchange exchange) throws IOException, SQLException {
        String declaredLength = exchange.getRequestHeaders().getFirst("Content-Length");
        if (declaredLength != null) {
            try {
                if (Long.parseLong(declaredLength.trim()) > maxUploadBytes) {
                    sendError(exchange, 413, "upload exceeds " + maxUploadBytes + " bytes");
                    return;
                }
            } catch (NumberFormatException e) {
                sendError(exchange, 400, "invalid Content-Length");
                return;
            }
        }
        byte[] body = readAtMost(exchange.getRequestBody(), maxUploadBytes);
        if (body == null) {
            sendError(exchange, 413, "upload exceeds " + maxUploadBytes + " bytes");
            return;
        }

        String contentType = Optional.ofNullable(exchange.getRequestHeaders().getFirst("Content-Type")).orElse("");
        String filename = null;
        byte[] csv = body;
        if (contentType.toLowerCase(Locale.ROOT).startsWith("multipart/form-data")) {
            Multipart.Part part;
            try {
                part = Multipart.findPart(body, contentType, "file");
            } catch (IllegalArgumentException e) {
                sendError(exchange, 400, e.getMessage());
                return;
            }
            if (part == null) {
                sendError(exchange, 400, "multipart upload must contain a part named 'file'");
                return;
            }
            filename = part.filename();
            csv = part.content();
        }
        if (csv.length == 0) {
            sendError(exchange, 400, "uploaded file is empty");
            return;
        }

        String id;
        try {
            id = service.submit(filename, csv);
        } catch (ImportService.QueueFullException e) {
            exchange.getResponseHeaders().set("Retry-After", "30");
            sendError(exchange, 503, e.getMessage());
            return;
        }
        String statusUrl = IMPORTS_PREFIX + id;
        exchange.getResponseHeaders().set("Location", statusUrl);
        sendJson(exchange, 202, new Accepted(id, ImportService.Status.QUEUED.name(), statusUrl));
    }

    private void status(HttpExchange exchange, String id) throws IOException, SQLException {
        Map<String, String> query = parseQuery(exchange.getRequestURI().getRawQuery());
        int offset;
        int limit;
        try {
            offset = Integer.parseInt(query.getOrDefault("errorsOffset", "0"));
            limit = Integer.parseInt(query.getOrDefault("errorsLimit", String.valueOf(DEFAULT_ERRORS_LIMIT)));
        } catch (NumberFormatException e) {
            sendError(exchange, 400, "errorsOffset and errorsLimit must be integers");
            return;
        }
        if (offset < 0 || limit < 1 || limit > MAX_ERRORS_LIMIT) {
            sendError(exchange, 400, "errorsOffset must be >= 0 and errorsLimit between 1 and " + MAX_ERRORS_LIMIT);
            return;
        }

        Optional<ImportService.ImportStatus> status = service.getImport(id, offset, limit);
        if (status.isEmpty()) {
            sendError(exchange, 404, "import not found");
            return;
        }
        sendJson(exchange, 200, status.get());
    }

    /** Reads the stream fully, or returns null if it holds more than {@code limit} bytes. */
    static byte[] readAtMost(InputStream in, int limit) throws IOException {
        byte[] data = in.readNBytes(limit + 1);
        return data.length > limit ? null : data;
    }

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> params = new HashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return params;
        }
        for (String pair : rawQuery.split("&")) {
            int eq = pair.indexOf('=');
            String key = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            params.put(URLDecoder.decode(key, StandardCharsets.UTF_8), URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return params;
    }

    private static void methodNotAllowed(HttpExchange exchange, String allowed) throws IOException {
        exchange.getResponseHeaders().set("Allow", allowed);
        sendError(exchange, 405, "method not allowed");
    }

    private static void sendError(HttpExchange exchange, int status, String message) throws IOException {
        sendJson(exchange, status, new ErrorBody(message));
    }

    private static void sendJson(HttpExchange exchange, int status, Object body) throws IOException {
        byte[] bytes = JSON.writeValueAsBytes(body);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }

    /** Just enough multipart/form-data parsing to pull one part out of an in-memory body. */
    static final class Multipart {

        record Part(String name, String filename, byte[] content) {}

        private static final byte[] CRLF = {'\r', '\n'};
        private static final byte[] HEADERS_END = {'\r', '\n', '\r', '\n'};

        private Multipart() {}

        static Part findPart(byte[] body, String contentType, String name) {
            String boundary = boundary(contentType);
            if (boundary == null) {
                throw new IllegalArgumentException("multipart Content-Type has no boundary");
            }
            byte[] delimiter = ("--" + boundary).getBytes(StandardCharsets.ISO_8859_1);
            byte[] nextDelimiter = ("\r\n--" + boundary).getBytes(StandardCharsets.ISO_8859_1);

            int pos = indexOf(body, delimiter, 0);
            while (pos >= 0) {
                int cursor = pos + delimiter.length;
                if (cursor + 1 < body.length && body[cursor] == '-' && body[cursor + 1] == '-') {
                    return null; // closing delimiter
                }
                int lineEnd = indexOf(body, CRLF, cursor);
                if (lineEnd < 0) {
                    return null;
                }
                int headersStart = lineEnd + CRLF.length;
                int headersEnd = indexOf(body, HEADERS_END, headersStart);
                if (headersEnd < 0) {
                    throw new IllegalArgumentException("multipart body is malformed");
                }
                String headers = new String(body, headersStart, headersEnd - headersStart, StandardCharsets.UTF_8);
                int contentStart = headersEnd + HEADERS_END.length;
                int contentEnd = indexOf(body, nextDelimiter, contentStart);
                if (contentEnd < 0) {
                    throw new IllegalArgumentException("multipart body is truncated");
                }
                String disposition = header(headers, "content-disposition");
                if (disposition != null && name.equals(param(disposition, "name"))) {
                    return new Part(name, param(disposition, "filename"),
                            Arrays.copyOfRange(body, contentStart, contentEnd));
                }
                pos = contentEnd + CRLF.length;
            }
            return null;
        }

        static String boundary(String contentType) {
            for (String param : contentType.split(";")) {
                String p = param.trim();
                if (p.toLowerCase(Locale.ROOT).startsWith("boundary=")) {
                    String value = p.substring("boundary=".length());
                    if (value.length() >= 2 && value.startsWith("\"") && value.endsWith("\"")) {
                        value = value.substring(1, value.length() - 1);
                    }
                    return value.isEmpty() ? null : value;
                }
            }
            return null;
        }

        private static String header(String headers, String name) {
            for (String line : headers.split("\r\n")) {
                int colon = line.indexOf(':');
                if (colon > 0 && line.substring(0, colon).trim().equalsIgnoreCase(name)) {
                    return line.substring(colon + 1).trim();
                }
            }
            return null;
        }

        private static String param(String headerValue, String name) {
            Matcher m = Pattern.compile("(?i)(?:^|;)\\s*" + Pattern.quote(name) + "=(?:\"([^\"]*)\"|([^;\\s]*))")
                    .matcher(headerValue);
            if (!m.find()) {
                return null;
            }
            return m.group(1) != null ? m.group(1) : m.group(2);
        }

        private static int indexOf(byte[] data, byte[] target, int from) {
            outer:
            for (int i = Math.max(from, 0); i <= data.length - target.length; i++) {
                for (int j = 0; j < target.length; j++) {
                    if (data[i + j] != target[j]) {
                        continue outer;
                    }
                }
                return i;
            }
            return -1;
        }
    }
}
