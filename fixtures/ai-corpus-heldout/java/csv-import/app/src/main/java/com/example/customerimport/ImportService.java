package com.example.customerimport;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Clock;
import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.util.regex.Pattern;
import org.sqlite.SQLiteConfig;

/**
 * Runs customer CSV imports on a background worker and records their progress and per-row
 * errors in SQLite.
 *
 * <p>Imports run one at a time on a single worker thread. SQLite allows only one writer, so
 * parallel imports would just contend for the write lock.
 */
public final class ImportService implements AutoCloseable {

    public enum Status { QUEUED, RUNNING, COMPLETED, FAILED }

    /** A validation problem on one CSV row. {@code field} is null when the whole row is bad. */
    public record RowError(int line, String field, String message) {}

    public record ImportStatus(
            String importId,
            Status status,
            String filename,
            Integer totalRows,
            int processedRows,
            int insertedRows,
            int updatedRows,
            int failedRows,
            String failureReason,
            String createdAt,
            String startedAt,
            String finishedAt,
            int errorCount,
            int errorsOffset,
            int errorsLimit,
            List<RowError> errors) {}

    public static final class QueueFullException extends Exception {
        QueueFullException(String message) {
            super(message);
        }
    }

    record Customer(String name, String email, String phone, String country, LocalDate signupDate) {}

    static final List<String> COLUMNS = List.of("name", "email", "phone", "country", "signup_date");
    static final int BATCH_SIZE = 500;

    private static final Logger LOG = Logger.getLogger(ImportService.class.getName());
    private static final Pattern EMAIL = Pattern.compile("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$");
    private static final Pattern PHONE = Pattern.compile("^\\+?[0-9 ().-]+$");
    private static final Set<String> COUNTRIES = Set.of(Locale.getISOCountries());

    private final String jdbcUrl;
    private final Clock clock;
    private final ThreadPoolExecutor executor;

    /**
     * @param maxQueuedImports how many imports may wait behind the running one. Uploads are held
     *     in memory until processed, so this bounds memory use.
     */
    public ImportService(String jdbcUrl, Clock clock, int maxQueuedImports) throws SQLException {
        this.jdbcUrl = jdbcUrl;
        this.clock = clock;
        initSchema();
        this.executor = new ThreadPoolExecutor(1, 1, 0L, TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(maxQueuedImports), runnable -> {
                    Thread thread = new Thread(runnable, "csv-import-worker");
                    thread.setDaemon(true);
                    return thread;
                });
    }

    /** Records a new import and queues it for processing. Returns the import id. */
    public String submit(String filename, byte[] content) throws SQLException, QueueFullException {
        String id = UUID.randomUUID().toString();
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "INSERT INTO imports (id, filename, status, created_at) VALUES (?, ?, 'QUEUED', ?)")) {
            ps.setString(1, id);
            ps.setString(2, filename);
            ps.setString(3, now());
            ps.executeUpdate();
        }
        try {
            executor.execute(() -> run(id, content));
        } catch (RejectedExecutionException e) {
            try (Connection c = connect();
                 PreparedStatement ps = c.prepareStatement("DELETE FROM imports WHERE id = ?")) {
                ps.setString(1, id);
                ps.executeUpdate();
            }
            throw new QueueFullException("too many imports are waiting to run; try again later");
        }
        return id;
    }

    /** Returns an import's progress plus one page of its row errors, or empty if the id is unknown. */
    public Optional<ImportStatus> getImport(String id, int errorsOffset, int errorsLimit) throws SQLException {
        try (Connection c = connect()) {
            // Read everything from one snapshot so the counters and the error list agree.
            c.setAutoCommit(false);
            try {
                return readImport(c, id, errorsOffset, errorsLimit);
            } finally {
                c.rollback();
            }
        }
    }

    @Override
    public void close() {
        // Interrupting the worker makes a running import stop at its next batch boundary.
        executor.shutdownNow();
        try {
            executor.awaitTermination(10, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void run(String id, byte[] content) {
        try {
            process(id, content);
        } catch (Exception e) {
            LOG.log(Level.SEVERE, "Import " + id + " failed", e);
            try {
                finishFailed(id, "unexpected error while importing; rows committed before the failure were kept");
            } catch (SQLException inner) {
                LOG.log(Level.SEVERE, "Could not mark import " + id + " as failed", inner);
            }
        }
    }

    private void process(String id, byte[] content) throws SQLException {
        String text;
        try {
            text = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(content))
                    .toString();
        } catch (CharacterCodingException e) {
            finishFailed(id, "file is not valid UTF-8");
            return;
        }
        if (text.startsWith("﻿")) {
            text = text.substring(1);
        }

        List<Csv.Row> rows = Csv.parse(text);
        if (rows.isEmpty()) {
            finishFailed(id, "file has no header row");
            return;
        }
        Map<String, Integer> columns;
        try {
            columns = resolveHeader(rows.get(0));
        } catch (IllegalArgumentException e) {
            finishFailed(id, e.getMessage());
            return;
        }
        int width = rows.get(0).fields().size();
        List<Csv.Row> dataRows = rows.subList(1, rows.size());

        try (Connection c = connect();
             Statement tx = c.createStatement();
             PreparedStatement start = c.prepareStatement(
                     "UPDATE imports SET status = 'RUNNING', total_rows = ?, started_at = ? WHERE id = ?");
             PreparedStatement exists = c.prepareStatement("SELECT 1 FROM customers WHERE email = ?");
             PreparedStatement upsert = c.prepareStatement("""
                     INSERT INTO customers (name, email, phone, country, signup_date, created_at, updated_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?)
                     ON CONFLICT (email) DO UPDATE SET
                       name = excluded.name,
                       phone = excluded.phone,
                       country = excluded.country,
                       signup_date = excluded.signup_date,
                       updated_at = excluded.updated_at""");
             PreparedStatement recordError = c.prepareStatement(
                     "INSERT INTO import_errors (import_id, line, field, message) VALUES (?, ?, ?, ?)");
             PreparedStatement progress = c.prepareStatement("""
                     UPDATE imports SET processed_rows = ?, inserted_rows = ?, updated_rows = ?, failed_rows = ?
                     WHERE id = ?""");
             PreparedStatement complete = c.prepareStatement(
                     "UPDATE imports SET status = 'COMPLETED', finished_at = ? WHERE id = ?")) {

            start.setInt(1, dataRows.size());
            start.setString(2, now());
            start.setString(3, id);
            start.executeUpdate();

            int processed = 0;
            int inserted = 0;
            int updated = 0;
            int failed = 0;
            // Transactions are managed by hand: BEGIN IMMEDIATE takes the write lock up front, which
            // avoids SQLITE_BUSY when another connection writes between our read and write.
            tx.execute("BEGIN IMMEDIATE");
            try {
                for (Csv.Row row : dataRows) {
                    List<RowError> errors = new ArrayList<>();
                    Customer customer = validate(row, columns, width, errors);
                    if (customer == null) {
                        failed++;
                        for (RowError error : errors) {
                            recordError.setString(1, id);
                            recordError.setInt(2, error.line());
                            recordError.setString(3, error.field());
                            recordError.setString(4, error.message());
                            recordError.executeUpdate();
                        }
                    } else if (upsert(exists, upsert, customer)) {
                        inserted++;
                    } else {
                        updated++;
                    }
                    processed++;

                    if (processed % BATCH_SIZE == 0) {
                        writeProgress(progress, id, processed, inserted, updated, failed);
                        tx.execute("COMMIT");
                        if (Thread.currentThread().isInterrupted()) {
                            finishFailed(id, "server shut down during the import; rows processed so far were kept");
                            return;
                        }
                        tx.execute("BEGIN IMMEDIATE");
                    }
                }
                writeProgress(progress, id, processed, inserted, updated, failed);
                complete.setString(1, now());
                complete.setString(2, id);
                complete.executeUpdate();
                tx.execute("COMMIT");
            } catch (SQLException | RuntimeException e) {
                try {
                    tx.execute("ROLLBACK");
                } catch (SQLException ignored) {
                    // No transaction was open.
                }
                throw e;
            }
        }
    }

    /** Inserts or updates a customer by email. Returns true if a new customer was inserted. */
    private boolean upsert(PreparedStatement exists, PreparedStatement upsert, Customer customer) throws SQLException {
        boolean existed;
        exists.setString(1, customer.email());
        try (ResultSet rs = exists.executeQuery()) {
            existed = rs.next();
        }
        String now = now();
        upsert.setString(1, customer.name());
        upsert.setString(2, customer.email());
        upsert.setString(3, customer.phone());
        upsert.setString(4, customer.country());
        upsert.setString(5, customer.signupDate().toString());
        upsert.setString(6, now);
        upsert.setString(7, now);
        upsert.executeUpdate();
        return !existed;
    }

    private static void writeProgress(PreparedStatement progress, String id,
                                      int processed, int inserted, int updated, int failed) throws SQLException {
        progress.setInt(1, processed);
        progress.setInt(2, inserted);
        progress.setInt(3, updated);
        progress.setInt(4, failed);
        progress.setString(5, id);
        progress.executeUpdate();
    }

    /** Maps column name to index. Header names are case-insensitive; unknown columns are ignored. */
    static Map<String, Integer> resolveHeader(Csv.Row header) {
        if (header.error() != null) {
            throw new IllegalArgumentException("header row is malformed: " + header.error());
        }
        Map<String, Integer> columns = new HashMap<>();
        for (int i = 0; i < header.fields().size(); i++) {
            String name = header.fields().get(i).trim().toLowerCase(Locale.ROOT).replaceAll("[\\s-]+", "_");
            if (COLUMNS.contains(name) && columns.putIfAbsent(name, i) != null) {
                throw new IllegalArgumentException("header has duplicate column '" + name + "'");
            }
        }
        List<String> missing = COLUMNS.stream().filter(column -> !columns.containsKey(column)).toList();
        if (!missing.isEmpty()) {
            throw new IllegalArgumentException("header is missing required column(s): " + String.join(", ", missing));
        }
        return columns;
    }

    /** Returns the normalized customer, or null after adding at least one entry to {@code errors}. */
    Customer validate(Csv.Row row, Map<String, Integer> columns, int width, List<RowError> errors) {
        int line = row.line();
        if (row.error() != null) {
            errors.add(new RowError(line, null, row.error()));
            return null;
        }
        if (row.fields().size() != width) {
            errors.add(new RowError(line, null,
                    "expected " + width + " fields but found " + row.fields().size()));
            return null;
        }
        String name = value(row, columns, "name");
        String email = value(row, columns, "email").toLowerCase(Locale.ROOT);
        String phone = value(row, columns, "phone");
        String country = value(row, columns, "country").toUpperCase(Locale.ROOT);
        String signup = value(row, columns, "signup_date");

        if (name.isEmpty()) {
            errors.add(new RowError(line, "name", "name is required"));
        } else if (name.length() > 200) {
            errors.add(new RowError(line, "name", "name must be at most 200 characters"));
        }

        if (email.isEmpty()) {
            errors.add(new RowError(line, "email", "email is required"));
        } else if (email.length() > 254 || !EMAIL.matcher(email).matches()) {
            errors.add(new RowError(line, "email", "email is not a valid email address"));
        }

        if (!phone.isEmpty()) {
            long digits = phone.chars().filter(ch -> ch >= '0' && ch <= '9').count();
            if (!PHONE.matcher(phone).matches() || digits < 7 || digits > 15) {
                errors.add(new RowError(line, "phone",
                        "phone must contain 7 to 15 digits and only digits, spaces, '+', '-', '.', '(' or ')'"));
            }
        }

        if (country.isEmpty()) {
            errors.add(new RowError(line, "country", "country is required"));
        } else if (!COUNTRIES.contains(country)) {
            errors.add(new RowError(line, "country", "country must be an ISO 3166-1 alpha-2 code such as US or GB"));
        }

        LocalDate signupDate = null;
        if (signup.isEmpty()) {
            errors.add(new RowError(line, "signup_date", "signup_date is required"));
        } else {
            try {
                signupDate = LocalDate.parse(signup);
                if (signupDate.isAfter(LocalDate.now(clock))) {
                    errors.add(new RowError(line, "signup_date", "signup_date cannot be in the future"));
                }
            } catch (DateTimeParseException e) {
                errors.add(new RowError(line, "signup_date", "signup_date must be a valid date in YYYY-MM-DD format"));
            }
        }

        if (!errors.isEmpty()) {
            return null;
        }
        return new Customer(name, email, phone.isEmpty() ? null : phone, country, signupDate);
    }

    private static String value(Csv.Row row, Map<String, Integer> columns, String column) {
        return row.fields().get(columns.get(column)).trim();
    }

    private Optional<ImportStatus> readImport(Connection c, String id, int errorsOffset, int errorsLimit)
            throws SQLException {
        int errorCount;
        try (PreparedStatement ps = c.prepareStatement("SELECT COUNT(*) FROM import_errors WHERE import_id = ?")) {
            ps.setString(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                errorCount = rs.getInt(1);
            }
        }

        List<RowError> errors = new ArrayList<>();
        try (PreparedStatement ps = c.prepareStatement(
                "SELECT line, field, message FROM import_errors WHERE import_id = ? ORDER BY id LIMIT ? OFFSET ?")) {
            ps.setString(1, id);
            ps.setInt(2, errorsLimit);
            ps.setInt(3, errorsOffset);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    errors.add(new RowError(rs.getInt("line"), rs.getString("field"), rs.getString("message")));
                }
            }
        }

        try (PreparedStatement ps = c.prepareStatement("SELECT * FROM imports WHERE id = ?")) {
            ps.setString(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) {
                    return Optional.empty();
                }
                int totalRows = rs.getInt("total_rows");
                boolean totalUnknown = rs.wasNull();
                return Optional.of(new ImportStatus(
                        id,
                        Status.valueOf(rs.getString("status")),
                        rs.getString("filename"),
                        totalUnknown ? null : totalRows,
                        rs.getInt("processed_rows"),
                        rs.getInt("inserted_rows"),
                        rs.getInt("updated_rows"),
                        rs.getInt("failed_rows"),
                        rs.getString("failure_reason"),
                        rs.getString("created_at"),
                        rs.getString("started_at"),
                        rs.getString("finished_at"),
                        errorCount,
                        errorsOffset,
                        errorsLimit,
                        errors));
            }
        }
    }

    private void finishFailed(String id, String reason) throws SQLException {
        try (Connection c = connect();
             PreparedStatement ps = c.prepareStatement(
                     "UPDATE imports SET status = 'FAILED', failure_reason = ?, finished_at = ? WHERE id = ?")) {
            ps.setString(1, reason);
            ps.setString(2, now());
            ps.setString(3, id);
            ps.executeUpdate();
        }
    }

    private void initSchema() throws SQLException {
        try (Connection c = connect(); Statement s = c.createStatement()) {
            // WAL lets the status endpoint read while an import is writing.
            s.execute("PRAGMA journal_mode = WAL");
            s.execute("""
                    CREATE TABLE IF NOT EXISTS customers (
                      id          INTEGER PRIMARY KEY,
                      name        TEXT NOT NULL,
                      email       TEXT NOT NULL UNIQUE,
                      phone       TEXT,
                      country     TEXT NOT NULL,
                      signup_date TEXT NOT NULL,
                      created_at  TEXT NOT NULL,
                      updated_at  TEXT NOT NULL
                    )""");
            s.execute("""
                    CREATE TABLE IF NOT EXISTS imports (
                      id             TEXT PRIMARY KEY,
                      filename       TEXT,
                      status         TEXT NOT NULL,
                      total_rows     INTEGER,
                      processed_rows INTEGER NOT NULL DEFAULT 0,
                      inserted_rows  INTEGER NOT NULL DEFAULT 0,
                      updated_rows   INTEGER NOT NULL DEFAULT 0,
                      failed_rows    INTEGER NOT NULL DEFAULT 0,
                      failure_reason TEXT,
                      created_at     TEXT NOT NULL,
                      started_at     TEXT,
                      finished_at    TEXT
                    )""");
            s.execute("""
                    CREATE TABLE IF NOT EXISTS import_errors (
                      id        INTEGER PRIMARY KEY,
                      import_id TEXT NOT NULL REFERENCES imports (id) ON DELETE CASCADE,
                      line      INTEGER NOT NULL,
                      field     TEXT,
                      message   TEXT NOT NULL
                    )""");
            s.execute("CREATE INDEX IF NOT EXISTS import_errors_by_import ON import_errors (import_id, id)");

            // Uploads live only in memory, so an import cut short by a restart cannot be resumed.
            try (PreparedStatement ps = c.prepareStatement("""
                    UPDATE imports SET status = 'FAILED', failure_reason = ?, finished_at = ?
                    WHERE status IN ('QUEUED', 'RUNNING')""")) {
                ps.setString(1, "server restarted before the import finished; rows processed so far were kept");
                ps.setString(2, now());
                ps.executeUpdate();
            }
        }
    }

    private Connection connect() throws SQLException {
        SQLiteConfig config = new SQLiteConfig();
        config.setBusyTimeout(5_000);
        config.enforceForeignKeys(true);
        return DriverManager.getConnection(jdbcUrl, config.toProperties());
    }

    private String now() {
        return clock.instant().toString();
    }
}
