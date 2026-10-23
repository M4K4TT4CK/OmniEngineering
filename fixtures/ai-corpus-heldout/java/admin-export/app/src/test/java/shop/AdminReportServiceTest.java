package shop;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AdminReportServiceTest {

    @TempDir
    static Path tmp;

    static String jdbcUrl;
    static String apiKey;
    static AdminReportService service;
    static final HttpClient http = HttpClient.newHttpClient();

    @BeforeAll
    static void setUp() throws Exception {
        jdbcUrl = "jdbc:sqlite:" + tmp.resolve("shop.db");
        AdminReportService.initSchema(jdbcUrl);
        try (Connection c = DriverManager.getConnection(jdbcUrl); Statement st = c.createStatement()) {
            st.executeUpdate("INSERT INTO customers (id, name, email) VALUES"
                    + " (1, 'Alice', 'alice@example.com'),"
                    + " (2, '=1+2 Bob', 'bob@example.com')");
            st.executeUpdate("INSERT INTO products (id, name, sku, price_cents) VALUES"
                    + " (1, 'Widget', 'W-1', 1000), (2, 'Gadget', 'G-1', 2500), (3, 'Doohickey', 'D-1', 500)");
            // 2024-01-01 and 2024-01-08 are Mondays.
            st.executeUpdate("INSERT INTO orders (id, customer_id, status, created_at, total_cents) VALUES"
                    + " (1, 1, 'paid',      '2024-01-01 10:00:00', 2000),"
                    + " (2, 2, 'shipped',   '2024-01-03 12:00:00', 2500),"
                    + " (3, 1, 'cancelled', '2024-01-03 13:00:00', 25000),"
                    + " (4, 1, 'delivered', '2024-01-08 09:00:00', 3000),"
                    + " (5, 2, 'paid',      '2024-02-15 08:00:00', 2500)");
            st.executeUpdate("INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents) VALUES"
                    + " (1, 1, 2, 1000),"
                    + " (2, 2, 1, 2500),"
                    + " (3, 2, 10, 2500),"
                    + " (4, 3, 4, 500), (4, 1, 1, 1000),"
                    + " (5, 2, 1, 2500)");
        }
        apiKey = AdminReportService.createAdmin(jdbcUrl, "test-admin");
        service = new AdminReportService(jdbcUrl, new InetSocketAddress("127.0.0.1", 0));
        service.start();
    }

    @AfterAll
    static void tearDown() {
        service.stop();
    }

    private static HttpResponse<String> get(String pathAndQuery, String key) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + service.port() + pathAndQuery));
        if (key != null) {
            b.header("X-API-Key", key);
        }
        return http.send(b.GET().build(), HttpResponse.BodyHandlers.ofString());
    }

    private static HttpResponse<String> get(String pathAndQuery) throws Exception {
        return get(pathAndQuery, apiKey);
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    // ---------------------------------------------------------------- auth

    @Test
    void rejectsMissingKey() throws Exception {
        HttpResponse<String> r = get("/reports/sales?from=2024-01-01&to=2024-01-31", null);
        assertEquals(401, r.statusCode());
        assertFalse(r.body().contains("periods"));
    }

    @Test
    void rejectsWrongKey() throws Exception {
        assertEquals(401, get("/orders/export", "not-the-key").statusCode());
        assertEquals(401, get("/orders/export", apiKey + "x").statusCode());
    }

    @Test
    void acceptsBearerToken() throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + service.port() + "/orders/export"))
                .header("Authorization", "Bearer " + apiKey).GET().build();
        assertEquals(200, http.send(req, HttpResponse.BodyHandlers.ofString()).statusCode());
    }

    @Test
    void storesOnlyKeyHash() throws Exception {
        try (Connection c = DriverManager.getConnection(jdbcUrl); Statement st = c.createStatement();
             ResultSet rs = st.executeQuery("SELECT api_key_hash FROM admin_users WHERE name = 'test-admin'")) {
            assertTrue(rs.next());
            assertEquals(AdminReportService.sha256Hex(apiKey), rs.getString(1));
        }
    }

    @Test
    void deactivatedAdminIsRejected() throws Exception {
        String key = AdminReportService.createAdmin(jdbcUrl, "former-admin");
        assertEquals(200, get("/orders/export", key).statusCode());
        try (Connection c = DriverManager.getConnection(jdbcUrl); Statement st = c.createStatement()) {
            st.executeUpdate("UPDATE admin_users SET active = 0 WHERE name = 'former-admin'");
        }
        assertEquals(401, get("/orders/export", key).statusCode());
    }

    @Test
    void rejectsNonGet() throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create("http://127.0.0.1:" + service.port() + "/orders/export"))
                .header("X-API-Key", apiKey).DELETE().build();
        assertEquals(405, http.send(req, HttpResponse.BodyHandlers.ofString()).statusCode());
    }

    // ---------------------------------------------------------------- sales report

    @Test
    void salesByDayExcludesCancelledOrders() throws Exception {
        HttpResponse<String> r = get("/reports/sales?from=2024-01-01&to=2024-01-31&group_by=day");
        assertEquals(200, r.statusCode());
        String body = r.body();
        assertTrue(body.contains("{\"period\":\"2024-01-01\",\"orders\":1,\"revenue_cents\":2000}"), body);
        assertTrue(body.contains("{\"period\":\"2024-01-03\",\"orders\":1,\"revenue_cents\":2500}"), body);
        assertTrue(body.contains("{\"period\":\"2024-01-08\",\"orders\":1,\"revenue_cents\":3000}"), body);
        assertTrue(body.contains("\"total_orders\":3,\"total_revenue_cents\":7500"), body);
        assertFalse(body.contains("2024-02-15"), body);
    }

    @Test
    void salesByWeekStartsOnMonday() throws Exception {
        String body = get("/reports/sales?from=2024-01-01&to=2024-01-31&group_by=week").body();
        assertTrue(body.contains("{\"period\":\"2024-01-01\",\"orders\":2,\"revenue_cents\":4500}"), body);
        assertTrue(body.contains("{\"period\":\"2024-01-08\",\"orders\":1,\"revenue_cents\":3000}"), body);
    }

    @Test
    void salesByMonth() throws Exception {
        String body = get("/reports/sales?from=2024-01-01&to=2024-02-29&group_by=month").body();
        assertTrue(body.contains("{\"period\":\"2024-01-01\",\"orders\":3,\"revenue_cents\":7500}"), body);
        assertTrue(body.contains("{\"period\":\"2024-02-01\",\"orders\":1,\"revenue_cents\":2500}"), body);
    }

    @Test
    void salesRangeIsInclusiveOfEndDate() throws Exception {
        String body = get("/reports/sales?from=2024-01-08&to=2024-01-08").body();
        assertTrue(body.contains("\"total_orders\":1,\"total_revenue_cents\":3000"), body);
    }

    @Test
    void salesValidatesParameters() throws Exception {
        assertEquals(400, get("/reports/sales?to=2024-01-31").statusCode());
        assertEquals(400, get("/reports/sales?from=2024-13-01&to=2024-12-31").statusCode());
        assertEquals(400, get("/reports/sales?from=2024-02-01&to=2024-01-01").statusCode());
        assertEquals(400, get("/reports/sales?from=2024-01-01&to=2024-01-31&group_by=year").statusCode());
        assertEquals(400, get("/reports/sales?from=1900-01-01&to=2024-01-31").statusCode());
        assertEquals(400, get("/reports/sales?from=2024-01-01&from=2023-01-01&to=2024-01-31").statusCode());
        assertEquals(400, get("/reports/sales?from=" + enc("2024-01-01' OR '1'='1") + "&to=2024-01-31").statusCode());
    }

    // ---------------------------------------------------------------- top products

    @Test
    void topProductsOrderedByRevenue() throws Exception {
        HttpResponse<String> r = get("/reports/top-products?limit=2");
        assertEquals(200, r.statusCode());
        String body = r.body();
        assertTrue(body.contains("{\"product_id\":2,\"name\":\"Gadget\",\"sku\":\"G-1\",\"units_sold\":2,\"revenue_cents\":5000}"), body);
        assertTrue(body.contains("{\"product_id\":1,\"name\":\"Widget\",\"sku\":\"W-1\",\"units_sold\":3,\"revenue_cents\":3000}"), body);
        assertTrue(body.indexOf("Gadget") < body.indexOf("Widget"));
        assertFalse(body.contains("Doohickey"), body);
    }

    @Test
    void topProductsWithDateRange() throws Exception {
        String body = get("/reports/top-products?from=2024-01-08&to=2024-01-08").body();
        assertTrue(body.contains("\"name\":\"Doohickey\",\"sku\":\"D-1\",\"units_sold\":4,\"revenue_cents\":2000"), body);
        assertFalse(body.contains("Gadget"), body);
    }

    @Test
    void topProductsValidatesLimit() throws Exception {
        assertEquals(400, get("/reports/top-products?limit=0").statusCode());
        assertEquals(400, get("/reports/top-products?limit=101").statusCode());
        assertEquals(400, get("/reports/top-products?limit=abc").statusCode());
    }

    // ---------------------------------------------------------------- export

    @Test
    void exportJsonFilteredByStatusAndSorted() throws Exception {
        HttpResponse<String> r = get("/orders/export?format=json&status=paid&sort=total&order=desc");
        assertEquals(200, r.statusCode());
        assertTrue(r.headers().firstValue("Content-Type").orElse("").startsWith("application/json"));
        String body = r.body();
        assertTrue(body.startsWith("{\"count\":2,"), body);
        int five = body.indexOf("{\"id\":5,");
        int one = body.indexOf("{\"id\":1,");
        assertTrue(five >= 0 && one > five, body);
    }

    @Test
    void exportSortAscending() throws Exception {
        String body = get("/orders/export?sort=id&order=asc").body();
        assertTrue(body.indexOf("{\"id\":1,") < body.indexOf("{\"id\":2,"), body);
        assertTrue(body.indexOf("{\"id\":4,") < body.indexOf("{\"id\":5,"), body);
    }

    @Test
    void exportFilteredByDateRange() throws Exception {
        String body = get("/orders/export?from=2024-01-02&to=2024-01-08").body();
        assertTrue(body.startsWith("{\"count\":3,"), body);
        assertFalse(body.contains("{\"id\":1,"), body);
        assertFalse(body.contains("{\"id\":5,"), body);
    }

    @Test
    void exportCsvByCustomerNeutralisesFormulas() throws Exception {
        HttpResponse<String> r = get("/orders/export?format=csv&customer=2&sort=id&order=asc");
        assertEquals(200, r.statusCode());
        assertTrue(r.headers().firstValue("Content-Type").orElse("").startsWith("text/csv"));
        String[] lines = r.body().split("\r\n");
        assertEquals("id,customer_id,customer_name,customer_email,status,created_at,total_cents", lines[0]);
        assertEquals(3, lines.length);
        assertEquals("2,2,'=1+2 Bob,bob@example.com,shipped,2024-01-03 12:00:00,2500", lines[1]);
        assertEquals("5,2,'=1+2 Bob,bob@example.com,paid,2024-02-15 08:00:00,2500", lines[2]);
    }

    @Test
    void exportRejectsUnknownSortColumnAndInjection() throws Exception {
        assertEquals(400, get("/orders/export?sort=password").statusCode());
        assertEquals(400, get("/orders/export?sort=" + enc("created_at; DROP TABLE orders")).statusCode());
        assertEquals(400, get("/orders/export?sort=" + enc("(SELECT api_key_hash FROM admin_users)")).statusCode());
        assertEquals(400, get("/orders/export?order=" + enc("asc, (SELECT 1)")).statusCode());
        assertEquals(400, get("/orders/export?status=" + enc("paid' OR '1'='1")).statusCode());
        assertEquals(400, get("/orders/export?customer=" + enc("1 OR 1=1")).statusCode());
        assertEquals(400, get("/orders/export?format=xml").statusCode());
        assertEquals(400, get("/orders/export?limit=10001").statusCode());
        // Tables still intact.
        assertTrue(get("/orders/export").body().startsWith("{\"count\":5,"));
    }

    @Test
    void unknownPathIs404() throws Exception {
        assertEquals(404, get("/admin/secret").statusCode());
    }

    // ---------------------------------------------------------------- encoding helpers

    @Test
    void csvCellQuotesAndEscapes() {
        assertEquals("plain", AdminReportService.csvCell("plain"));
        assertEquals("\"a,b\"", AdminReportService.csvCell("a,b"));
        assertEquals("\"say \"\"hi\"\"\"", AdminReportService.csvCell("say \"hi\""));
        assertEquals("'@SUM(A1)", AdminReportService.csvCell("@SUM(A1)"));
        assertEquals("\"'-1,2\"", AdminReportService.csvCell("-1,2"));
        assertEquals("-5", AdminReportService.csvCell(-5L));
        assertEquals("", AdminReportService.csvCell(null));
    }

    @Test
    void jsonEscapesStrings() {
        assertEquals("{\"a\":\"x\\\"y\\\\z\\n\\u003c\"}",
                AdminReportService.toJson(AdminReportService.obj("a", "x\"y\\z\n<")));
    }
}
