package shop;

import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.sqlite.SQLiteConfig;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Admin reporting service for the shop database.
 *
 * <pre>
 *   GET /reports/sales?from=YYYY-MM-DD&amp;to=YYYY-MM-DD&amp;group_by=day|week|month
 *   GET /reports/top-products?limit=N[&amp;from=..&amp;to=..]
 *   GET /orders/export?format=csv|json[&amp;status=..][&amp;customer=ID][&amp;from=..][&amp;to=..]
 *                      [&amp;sort=COLUMN][&amp;order=asc|desc][&amp;limit=N]
 * </pre>
 *
 * Every request must carry an admin API key in {@code X-API-Key} or {@code Authorization: Bearer}.
 */
public final class AdminReportService {

    private static final Logger LOG = Logger.getLogger(AdminReportService.class.getName());

    static final Set<String> STATUSES = Set.of("pending", "paid", "shipped", "delivered", "cancelled", "refunded");
    /** Orders in these states do not count towards revenue. */
    private static final String REVENUE_FILTER = "o.status NOT IN ('cancelled', 'refunded')";

    static final int MAX_EXPORT_ROWS = 10_000;
    static final int DEFAULT_EXPORT_ROWS = 1_000;
    static final int MAX_TOP_PRODUCTS = 100;
    static final long MAX_RANGE_DAYS = 3_660;
    private static final int MAX_API_KEY_LENGTH = 256;

    // User input selects a key; only these fixed SQL fragments are ever concatenated into a query.
    private static final Map<String, String> SORT_COLUMNS = Map.of(
            "id", "o.id",
            "created_at", "o.created_at",
            "status", "o.status",
            "total", "o.total_cents",
            "customer_id", "o.customer_id",
            "customer_name", "c.name");

    private static final Map<String, String> PERIODS = Map.of(
            "day", "date(o.created_at)",
            "week", "date(o.created_at, 'weekday 0', '-6 days')", // Monday of the ISO week
            "month", "strftime('%Y-%m-01', o.created_at)");

    private static final List<String> EXPORT_COLUMNS = List.of(
            "id", "customer_id", "customer_name", "customer_email", "status", "created_at", "total_cents");

    private final String jdbcUrl;
    private final HttpServer server;
    private final ExecutorService executor;

    public AdminReportService(String jdbcUrl, InetSocketAddress address) throws IOException {
        this.jdbcUrl = jdbcUrl;
        this.server = HttpServer.create(address, 0);
        this.executor = Executors.newFixedThreadPool(8);
        server.createContext("/", this::handle);
        server.setExecutor(executor);
    }

    public void start() {
        server.start();
    }

    public void stop() {
        server.stop(0);
        executor.shutdownNow();
    }

    public int port() {
        return server.getAddress().getPort();
    }

    // ---------------------------------------------------------------- request handling

    private void handle(HttpExchange ex) {
        try {
            if (!"GET".equals(ex.getRequestMethod())) {
                ex.getResponseHeaders().set("Allow", "GET");
                sendError(ex, 405, "method not allowed");
                return;
            }
            if (authenticate(ex) == null) {
                ex.getResponseHeaders().set("WWW-Authenticate", "Bearer");
                sendError(ex, 401, "missing or invalid API key");
                return;
            }
            Map<String, String> q = parseQuery(ex.getRequestURI().getRawQuery());
            switch (ex.getRequestURI().getPath()) {
                case "/reports/sales" -> sendJson(ex, 200, salesReport(q));
                case "/reports/top-products" -> sendJson(ex, 200, topProducts(q));
                case "/orders/export" -> exportOrders(ex, q);
                default -> sendError(ex, 404, "not found");
            }
        } catch (BadRequest e) {
            sendErrorQuietly(ex, 400, e.getMessage());
        } catch (Exception e) {
            LOG.log(Level.SEVERE, "request failed: " + ex.getRequestURI().getPath(), e);
            sendErrorQuietly(ex, 500, "internal error");
        } finally {
            ex.close();
        }
    }

    /** Returns the admin's name, or null if the request is not authenticated. */
    private String authenticate(HttpExchange ex) throws SQLException {
        String key = ex.getRequestHeaders().getFirst("X-API-Key");
        if (key == null) {
            String auth = ex.getRequestHeaders().getFirst("Authorization");
            if (auth != null && auth.regionMatches(true, 0, "Bearer ", 0, 7)) {
                key = auth.substring(7).trim();
            }
        }
        if (key == null || key.isEmpty() || key.length() > MAX_API_KEY_LENGTH) {
            return null;
        }
        // Only SHA-256 hashes of keys are stored; keys are 256-bit random so a fast hash is sufficient.
        try (Connection c = readOnlyConnection();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT name FROM admin_users WHERE api_key_hash = ? AND active = 1")) {
            ps.setString(1, sha256Hex(key));
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    // ---------------------------------------------------------------- endpoints

    private Map<String, Object> salesReport(Map<String, String> q) throws SQLException {
        LocalDate from = requireDate(q, "from");
        LocalDate to = requireDate(q, "to");
        checkRange(from, to);
        String groupBy = q.getOrDefault("group_by", "day");
        String period = PERIODS.get(groupBy);
        if (period == null) {
            throw new BadRequest("group_by must be one of day, week, month");
        }

        String sql = "SELECT " + period + " AS period, COUNT(*) AS orders, SUM(o.total_cents) AS revenue"
                + " FROM orders o WHERE o.created_at >= ? AND o.created_at < ? AND " + REVENUE_FILTER
                + " GROUP BY period ORDER BY period";
        List<Object> rows = new ArrayList<>();
        long totalOrders = 0;
        long totalRevenue = 0;
        try (Connection c = readOnlyConnection(); PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, List.of(from.toString(), to.plusDays(1).toString()));
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    long orders = rs.getLong("orders");
                    long revenue = rs.getLong("revenue");
                    totalOrders += orders;
                    totalRevenue += revenue;
                    rows.add(obj("period", rs.getString("period"), "orders", orders, "revenue_cents", revenue));
                }
            }
        }
        return obj("from", from.toString(), "to", to.toString(), "group_by", groupBy,
                "total_orders", totalOrders, "total_revenue_cents", totalRevenue, "periods", rows);
    }

    private Map<String, Object> topProducts(Map<String, String> q) throws SQLException {
        int limit = intParam(q, "limit", 10, 1, MAX_TOP_PRODUCTS);
        LocalDate from = optionalDate(q, "from");
        LocalDate to = optionalDate(q, "to");
        checkRange(from, to);

        List<String> where = new ArrayList<>(List.of(REVENUE_FILTER));
        List<Object> params = new ArrayList<>();
        addDateFilter(where, params, from, to);
        params.add(limit);

        String sql = "SELECT p.id, p.name, p.sku, SUM(oi.quantity) AS units,"
                + " SUM(oi.quantity * oi.unit_price_cents) AS revenue"
                + " FROM order_items oi"
                + " JOIN orders o ON o.id = oi.order_id"
                + " JOIN products p ON p.id = oi.product_id"
                + " WHERE " + String.join(" AND ", where)
                + " GROUP BY p.id, p.name, p.sku ORDER BY revenue DESC, p.id LIMIT ?";
        List<Object> rows = new ArrayList<>();
        try (Connection c = readOnlyConnection(); PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    rows.add(obj("product_id", rs.getLong("id"), "name", rs.getString("name"),
                            "sku", rs.getString("sku"), "units_sold", rs.getLong("units"),
                            "revenue_cents", rs.getLong("revenue")));
                }
            }
        }
        return obj("from", from == null ? null : from.toString(), "to", to == null ? null : to.toString(),
                "products", rows);
    }

    private void exportOrders(HttpExchange ex, Map<String, String> q) throws SQLException, IOException {
        String format = q.getOrDefault("format", "json");
        if (!format.equals("json") && !format.equals("csv")) {
            throw new BadRequest("format must be csv or json");
        }
        String sortKey = q.getOrDefault("sort", "created_at");
        String sortColumn = SORT_COLUMNS.get(sortKey);
        if (sortColumn == null) {
            throw new BadRequest("sort must be one of " + String.join(", ", SORT_COLUMNS.keySet().stream().sorted().toList()));
        }
        String order = q.getOrDefault("order", "desc").toLowerCase();
        String direction = switch (order) {
            case "asc" -> "ASC";
            case "desc" -> "DESC";
            default -> throw new BadRequest("order must be asc or desc");
        };
        int limit = intParam(q, "limit", DEFAULT_EXPORT_ROWS, 1, MAX_EXPORT_ROWS);
        LocalDate from = optionalDate(q, "from");
        LocalDate to = optionalDate(q, "to");
        checkRange(from, to);

        List<String> where = new ArrayList<>();
        List<Object> params = new ArrayList<>();
        String status = q.get("status");
        if (status != null) {
            if (!STATUSES.contains(status)) {
                throw new BadRequest("unknown status");
            }
            where.add("o.status = ?");
            params.add(status);
        }
        String customer = q.get("customer");
        if (customer != null) {
            where.add("o.customer_id = ?");
            params.add(parseLong(customer, "customer"));
        }
        addDateFilter(where, params, from, to);
        params.add(limit);

        String sql = "SELECT o.id, o.customer_id, c.name AS customer_name, c.email AS customer_email,"
                + " o.status, o.created_at, o.total_cents"
                + " FROM orders o JOIN customers c ON c.id = o.customer_id"
                + (where.isEmpty() ? "" : " WHERE " + String.join(" AND ", where))
                + " ORDER BY " + sortColumn + " " + direction + ", o.id " + direction
                + " LIMIT ?";
        List<Map<String, Object>> rows = new ArrayList<>();
        try (Connection c = readOnlyConnection(); PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    rows.add(obj("id", rs.getLong("id"), "customer_id", rs.getLong("customer_id"),
                            "customer_name", rs.getString("customer_name"),
                            "customer_email", rs.getString("customer_email"),
                            "status", rs.getString("status"), "created_at", rs.getString("created_at"),
                            "total_cents", rs.getLong("total_cents")));
                }
            }
        }

        if (format.equals("json")) {
            sendJson(ex, 200, obj("count", rows.size(), "orders", rows));
            return;
        }
        StringBuilder csv = new StringBuilder(String.join(",", EXPORT_COLUMNS)).append("\r\n");
        for (Map<String, Object> row : rows) {
            List<String> cells = new ArrayList<>();
            for (String col : EXPORT_COLUMNS) {
                cells.add(csvCell(row.get(col)));
            }
            csv.append(String.join(",", cells)).append("\r\n");
        }
        ex.getResponseHeaders().set("Content-Disposition", "attachment; filename=\"orders.csv\"");
        send(ex, 200, "text/csv; charset=utf-8", csv.toString());
    }

    // ---------------------------------------------------------------- parameters

    static final class BadRequest extends RuntimeException {
        BadRequest(String message) {
            super(message);
        }
    }

    static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> params = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return params;
        }
        for (String pair : rawQuery.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            String name;
            String value;
            try {
                name = URLDecoder.decode(eq < 0 ? pair : pair.substring(0, eq), StandardCharsets.UTF_8);
                value = eq < 0 ? "" : URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8);
            } catch (IllegalArgumentException e) {
                throw new BadRequest("malformed query string");
            }
            if (params.putIfAbsent(name, value) != null) {
                throw new BadRequest("duplicate parameter: " + name);
            }
        }
        return params;
    }

    private static LocalDate requireDate(Map<String, String> q, String name) {
        LocalDate d = optionalDate(q, name);
        if (d == null) {
            throw new BadRequest(name + " is required (YYYY-MM-DD)");
        }
        return d;
    }

    private static LocalDate optionalDate(Map<String, String> q, String name) {
        String v = q.get(name);
        if (v == null) {
            return null;
        }
        try {
            return LocalDate.parse(v);
        } catch (DateTimeParseException e) {
            throw new BadRequest(name + " must be a date in YYYY-MM-DD format");
        }
    }

    private static void checkRange(LocalDate from, LocalDate to) {
        if (from != null && to != null) {
            if (to.isBefore(from)) {
                throw new BadRequest("to must not be before from");
            }
            if (ChronoUnit.DAYS.between(from, to) > MAX_RANGE_DAYS) {
                throw new BadRequest("date range too large");
            }
        }
    }

    /** Inclusive [from, to] on calendar days; created_at is stored as 'YYYY-MM-DD HH:MM:SS' UTC. */
    private static void addDateFilter(List<String> where, List<Object> params, LocalDate from, LocalDate to) {
        if (from != null) {
            where.add("o.created_at >= ?");
            params.add(from.toString());
        }
        if (to != null) {
            where.add("o.created_at < ?");
            params.add(to.plusDays(1).toString());
        }
    }

    private static int intParam(Map<String, String> q, String name, int def, int min, int max) {
        String v = q.get(name);
        if (v == null) {
            return def;
        }
        long n = parseLong(v, name);
        if (n < min || n > max) {
            throw new BadRequest(name + " must be between " + min + " and " + max);
        }
        return (int) n;
    }

    private static long parseLong(String v, String name) {
        try {
            return Long.parseLong(v);
        } catch (NumberFormatException e) {
            throw new BadRequest(name + " must be an integer");
        }
    }

    // ---------------------------------------------------------------- database

    private Connection readOnlyConnection() throws SQLException {
        SQLiteConfig config = new SQLiteConfig();
        config.setReadOnly(true);
        config.setBusyTimeout(5_000);
        return DriverManager.getConnection(jdbcUrl, config.toProperties());
    }

    private static void bind(PreparedStatement ps, List<Object> params) throws SQLException {
        for (int i = 0; i < params.size(); i++) {
            ps.setObject(i + 1, params.get(i));
        }
    }

    static void initSchema(String jdbcUrl) throws SQLException {
        try (Connection c = DriverManager.getConnection(jdbcUrl); Statement st = c.createStatement()) {
            st.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS customers (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        email TEXT NOT NULL UNIQUE)""");
            st.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS products (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        sku TEXT NOT NULL UNIQUE,
                        price_cents INTEGER NOT NULL)""");
            st.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS orders (
                        id INTEGER PRIMARY KEY,
                        customer_id INTEGER NOT NULL REFERENCES customers(id),
                        status TEXT NOT NULL CHECK (status IN
                            ('pending','paid','shipped','delivered','cancelled','refunded')),
                        created_at TEXT NOT NULL,
                        total_cents INTEGER NOT NULL)""");
            st.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS order_items (
                        id INTEGER PRIMARY KEY,
                        order_id INTEGER NOT NULL REFERENCES orders(id),
                        product_id INTEGER NOT NULL REFERENCES products(id),
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        unit_price_cents INTEGER NOT NULL)""");
            st.executeUpdate("""
                    CREATE TABLE IF NOT EXISTS admin_users (
                        id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        api_key_hash TEXT NOT NULL UNIQUE,
                        active INTEGER NOT NULL DEFAULT 1)""");
            st.executeUpdate("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)");
            st.executeUpdate("CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id)");
            st.executeUpdate("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)");
            st.executeUpdate("CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id)");
        }
    }

    /** Creates an admin user and returns the new API key. Only its hash is stored. */
    static String createAdmin(String jdbcUrl, String name) throws SQLException {
        byte[] raw = new byte[32];
        new SecureRandom().nextBytes(raw);
        String key = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        try (Connection c = DriverManager.getConnection(jdbcUrl);
             PreparedStatement ps = c.prepareStatement(
                     "INSERT INTO admin_users (name, api_key_hash) VALUES (?, ?)")) {
            ps.setString(1, name);
            ps.setString(2, sha256Hex(key));
            ps.executeUpdate();
        }
        return key;
    }

    static String sha256Hex(String s) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    // ---------------------------------------------------------------- output

    private static void sendJson(HttpExchange ex, int status, Object body) throws IOException {
        send(ex, status, "application/json; charset=utf-8", toJson(body));
    }

    private static void sendError(HttpExchange ex, int status, String message) throws IOException {
        sendJson(ex, status, obj("error", message));
    }

    private static void sendErrorQuietly(HttpExchange ex, int status, String message) {
        try {
            sendError(ex, status, message);
        } catch (IOException | RuntimeException e) {
            // headers already sent or client went away; nothing more to do
        }
    }

    private static void send(HttpExchange ex, int status, String contentType, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        Headers h = ex.getResponseHeaders();
        h.set("Content-Type", contentType);
        h.set("X-Content-Type-Options", "nosniff");
        h.set("Cache-Control", "no-store");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }

    static Map<String, Object> obj(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }

    static String toJson(Object v) {
        StringBuilder sb = new StringBuilder();
        writeJson(sb, v);
        return sb.toString();
    }

    private static void writeJson(StringBuilder sb, Object v) {
        if (v == null) {
            sb.append("null");
        } else if (v instanceof Number || v instanceof Boolean) {
            sb.append(v);
        } else if (v instanceof Map<?, ?> m) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : m.entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeJson(sb, e.getKey().toString());
                sb.append(':');
                writeJson(sb, e.getValue());
            }
            sb.append('}');
        } else if (v instanceof List<?> list) {
            sb.append('[');
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) sb.append(',');
                writeJson(sb, list.get(i));
            }
            sb.append(']');
        } else {
            String s = v.toString();
            sb.append('"');
            for (int i = 0; i < s.length(); i++) {
                char ch = s.charAt(i);
                switch (ch) {
                    case '"' -> sb.append("\\\"");
                    case '\\' -> sb.append("\\\\");
                    case '\n' -> sb.append("\\n");
                    case '\r' -> sb.append("\\r");
                    case '\t' -> sb.append("\\t");
                    default -> {
                        if (ch < 0x20 || ch == '<' || ch == '>' || ch == '&' || ch == ' ' || ch == ' ') {
                            sb.append(String.format("\\u%04x", (int) ch));
                        } else {
                            sb.append(ch);
                        }
                    }
                }
            }
            sb.append('"');
        }
    }

    /** RFC 4180 quoting, plus neutralising spreadsheet formulas in text cells. */
    static String csvCell(Object v) {
        if (v == null) {
            return "";
        }
        String s = v.toString();
        if (v instanceof String && !s.isEmpty() && "=+-@\t\r".indexOf(s.charAt(0)) >= 0) {
            s = "'" + s;
        }
        if (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0 || s.indexOf('\r') >= 0) {
            s = '"' + s.replace("\"", "\"\"") + '"';
        }
        return s;
    }

    // ---------------------------------------------------------------- main

    public static void main(String[] args) throws Exception {
        String dbPath = System.getenv().getOrDefault("SHOP_DB", "shop.db");
        String jdbcUrl = "jdbc:sqlite:" + dbPath;
        if (args.length > 0) {
            switch (args[0]) {
                case "init-db" -> {
                    initSchema(jdbcUrl);
                    System.out.println("Schema ready in " + dbPath);
                }
                case "add-admin" -> {
                    if (args.length != 2) {
                        System.err.println("usage: add-admin <name>");
                        System.exit(2);
                    }
                    System.out.println(createAdmin(jdbcUrl, args[1]));
                }
                default -> {
                    System.err.println("usage: [init-db | add-admin <name>]");
                    System.exit(2);
                }
            }
            return;
        }
        String host = System.getenv().getOrDefault("HOST", "127.0.0.1");
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        AdminReportService service = new AdminReportService(jdbcUrl, new InetSocketAddress(host, port));
        Runtime.getRuntime().addShutdownHook(new Thread(service::stop));
        service.start();
        LOG.info("Admin reporting service listening on " + host + ":" + service.port());
    }
}
