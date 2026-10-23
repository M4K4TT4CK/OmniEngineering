package com.example.customerimport;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import com.example.customerimport.ImportService.ImportStatus;
import com.example.customerimport.ImportService.Status;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class ImportServiceTest {

    static final Clock CLOCK = Clock.fixed(Instant.parse("2025-06-01T12:00:00Z"), ZoneOffset.UTC);
    static final String HEADER = "name,email,phone,country,signup_date\n";

    @TempDir
    Path dir;

    String url;
    ImportService service;

    @BeforeEach
    void setUp() throws Exception {
        url = "jdbc:sqlite:" + dir.resolve("test.db");
        service = new ImportService(url, CLOCK, 10);
    }

    @AfterEach
    void tearDown() {
        service.close();
    }

    @Test
    void importsValidRows() throws Exception {
        ImportStatus status = importCsv(HEADER
                + "Ada Lovelace,ada@example.com,+44 20 7946 0958,GB,2024-01-15\n"
                + "Grace Hopper,grace@example.com,,us,2024-03-09\n");

        assertEquals(Status.COMPLETED, status.status());
        assertEquals(Integer.valueOf(2), status.totalRows());
        assertEquals(2, status.processedRows());
        assertEquals(2, status.insertedRows());
        assertEquals(0, status.failedRows());
        assertEquals(0, status.errorCount());

        Map<String, Map<String, String>> customers = customers();
        assertEquals("Ada Lovelace", customers.get("ada@example.com").get("name"));
        assertEquals("+44 20 7946 0958", customers.get("ada@example.com").get("phone"));
        assertEquals("US", customers.get("grace@example.com").get("country"));
        assertNull(customers.get("grace@example.com").get("phone"));
        assertEquals("2024-03-09", customers.get("grace@example.com").get("signup_date"));
    }

    @Test
    void updatesExistingCustomersByEmailIgnoringCase() throws Exception {
        importCsv(HEADER + "Ada Lovelace,ada@example.com,,GB,2024-01-15\n");
        ImportStatus status = importCsv(HEADER + "Ada King,ADA@Example.com,555-123-4567,US,2024-01-16\n");

        assertEquals(0, status.insertedRows());
        assertEquals(1, status.updatedRows());
        Map<String, Map<String, String>> customers = customers();
        assertEquals(1, customers.size());
        assertEquals("Ada King", customers.get("ada@example.com").get("name"));
        assertEquals("US", customers.get("ada@example.com").get("country"));
        assertEquals("555-123-4567", customers.get("ada@example.com").get("phone"));
    }

    @Test
    void laterDuplicateRowInTheSameFileWins() throws Exception {
        ImportStatus status = importCsv(HEADER
                + "First,dup@example.com,,GB,2024-01-01\n"
                + "Second,dup@example.com,,FR,2024-02-02\n");

        assertEquals(1, status.insertedRows());
        assertEquals(1, status.updatedRows());
        assertEquals("Second", customers().get("dup@example.com").get("name"));
    }

    @Test
    void recordsPerRowErrorsAndKeepsValidRows() throws Exception {
        ImportStatus status = importCsv(HEADER
                + "Ada Lovelace,ada@example.com,,GB,2024-01-15\n"     // line 2: ok
                + "Bad Email,not-an-email,,US,2024-01-01\n"           // line 3
                + ",,555,XX,2024-13-01\n"                             // line 4: five problems
                + "Future,future@example.com,,US,2025-06-02\n"        // line 5
                + "Short,short@example.com\n"                         // line 6
                + "Grace Hopper,grace@example.com,,US,2024-03-09\n"); // line 7: ok

        assertEquals(Status.COMPLETED, status.status());
        assertEquals(6, status.processedRows());
        assertEquals(2, status.insertedRows());
        assertEquals(4, status.failedRows());
        List<String> errors = status.errors().stream().map(e -> e.line() + ":" + e.field()).toList();
        assertEquals(List.of(
                "3:email",
                "4:name", "4:email", "4:phone", "4:country", "4:signup_date",
                "5:signup_date",
                "6:null"), errors);
        assertEquals(8, status.errorCount());
        assertEquals("expected 5 fields but found 2", status.errors().get(7).message());
        assertEquals(2, customers().size());
    }

    @Test
    void acceptsReorderedHeadersQuotedFieldsBomAndCrlf() throws Exception {
        ImportStatus status = importCsv("﻿Email,Signup Date,Name,Country,Phone,Notes\r\n"
                + "\"LOVELACE@Example.com\",2024-01-15,\"Lovelace, Ada\",gb,,\"multi\r\nline\"\r\n"
                + "grace@example.com,2024-03-09,Grace Hopper,US,,\r\n");

        assertEquals(Status.COMPLETED, status.status());
        assertEquals(2, status.insertedRows());
        assertEquals("Lovelace, Ada", customers().get("lovelace@example.com").get("name"));
    }

    @Test
    void reportsRowLevelCsvSyntaxErrors() throws Exception {
        ImportStatus status = importCsv(HEADER
                + "\"Ada\"x,ada@example.com,,GB,2024-01-15\n"
                + "Grace Hopper,grace@example.com,,US,2024-03-09\n");

        assertEquals(1, status.failedRows());
        assertEquals(1, status.insertedRows());
        assertEquals(2, status.errors().get(0).line());
        assertTrue(status.errors().get(0).message().contains("closing quote"));
    }

    @Test
    void failsWhenARequiredColumnIsMissing() throws Exception {
        ImportStatus status = importCsv("name,email,country,signup_date\nAda,ada@example.com,GB,2024-01-15\n");

        assertEquals(Status.FAILED, status.status());
        assertTrue(status.failureReason().contains("phone"), status.failureReason());
        assertEquals(0, customers().size());
    }

    @Test
    void failsWhenFileIsNotUtf8() throws Exception {
        byte[] latin1 = (HEADER + "René,rene@example.com,,FR,2024-01-01\n").getBytes(StandardCharsets.ISO_8859_1);
        ImportStatus status = await(service.submit("latin1.csv", latin1));

        assertEquals(Status.FAILED, status.status());
        assertEquals("file is not valid UTF-8", status.failureReason());
    }

    @Test
    void headerOnlyFileCompletesWithNoRows() throws Exception {
        ImportStatus status = importCsv(HEADER);

        assertEquals(Status.COMPLETED, status.status());
        assertEquals(Integer.valueOf(0), status.totalRows());
    }

    @Test
    void processesFilesLargerThanOneBatch() throws Exception {
        int rows = ImportService.BATCH_SIZE * 2 + 1;
        StringBuilder csv = new StringBuilder(HEADER);
        for (int i = 0; i < rows; i++) {
            csv.append("Customer ").append(i).append(",c").append(i).append("@example.com,,DE,2023-05-05\n");
        }
        ImportStatus status = importCsv(csv.toString());

        assertEquals(Status.COMPLETED, status.status());
        assertEquals(rows, status.processedRows());
        assertEquals(rows, status.insertedRows());
        assertEquals(rows, customers().size());
    }

    @Test
    void marksImportsInterruptedByARestartAsFailed() throws Exception {
        service.close();
        try (Connection c = DriverManager.getConnection(url); Statement s = c.createStatement()) {
            s.executeUpdate("INSERT INTO imports (id, status, created_at) VALUES ('stale', 'RUNNING', '2025-01-01T00:00:00Z')");
        }

        service = new ImportService(url, CLOCK, 10);

        ImportStatus status = service.getImport("stale", 0, 10).orElseThrow();
        assertEquals(Status.FAILED, status.status());
        assertTrue(status.failureReason().contains("restarted"));
    }

    @Test
    void unknownImportIsEmpty() throws Exception {
        assertTrue(service.getImport("does-not-exist", 0, 10).isEmpty());
    }

    private ImportStatus importCsv(String csv) throws Exception {
        return await(service.submit("test.csv", csv.getBytes(StandardCharsets.UTF_8)));
    }

    private ImportStatus await(String id) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (true) {
            ImportStatus status = service.getImport(id, 0, 100).orElseThrow();
            if (status.status() == Status.COMPLETED || status.status() == Status.FAILED) {
                return status;
            }
            if (System.nanoTime() > deadline) {
                fail("import did not finish: " + status);
            }
            Thread.sleep(20);
        }
    }

    private Map<String, Map<String, String>> customers() throws Exception {
        Map<String, Map<String, String>> byEmail = new HashMap<>();
        try (Connection c = DriverManager.getConnection(url);
             Statement s = c.createStatement();
             ResultSet rs = s.executeQuery("SELECT name, email, phone, country, signup_date FROM customers")) {
            while (rs.next()) {
                Map<String, String> row = new HashMap<>();
                for (String column : List.of("name", "email", "phone", "country", "signup_date")) {
                    row.put(column, rs.getString(column));
                }
                byEmail.put(rs.getString("email"), row);
            }
        }
        return byEmail;
    }
}
