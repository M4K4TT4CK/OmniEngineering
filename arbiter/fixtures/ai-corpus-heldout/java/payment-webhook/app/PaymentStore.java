import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.util.Optional;
import java.util.Set;

/**
 * SQLite-backed store for orders and processed webhook events.
 *
 * Exactly-once application is guaranteed by recording the event id in
 * {@code processed_events} (PRIMARY KEY) in the same transaction that updates
 * the order. Either both happen or neither does, so a retry after a crash or
 * error re-applies cleanly, and a retry after success is detected as a duplicate.
 */
public final class PaymentStore implements AutoCloseable {

    public static final String PAYMENT_SUCCEEDED = "payment.succeeded";
    public static final String PAYMENT_FAILED = "payment.failed";
    public static final String REFUND_CREATED = "refund.created";
    public static final Set<String> EVENT_TYPES = Set.of(PAYMENT_SUCCEEDED, PAYMENT_FAILED, REFUND_CREATED);

    public enum Outcome { APPLIED, DUPLICATE, CONFLICT }

    /** Amounts are integer minor units (e.g. cents). */
    public record Event(String id, String type, String orderId, long amount) {}

    public record Order(String orderId, String status, long amountPaid, long amountRefunded, String updatedAt) {}

    // A single connection guarded by this object's monitor serialises all writes
    // within the process; SQLite's own locking covers other processes.
    private final Connection conn;

    public PaymentStore(String jdbcUrl) throws SQLException {
        conn = DriverManager.getConnection(jdbcUrl);
        try (Statement s = conn.createStatement()) {
            s.execute("PRAGMA busy_timeout = 5000");
            s.execute("PRAGMA journal_mode = WAL");
            s.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id        TEXT PRIMARY KEY,
                    status          TEXT NOT NULL,
                    amount_paid     INTEGER NOT NULL,
                    amount_refunded INTEGER NOT NULL,
                    payment_failed  INTEGER NOT NULL,
                    updated_at      TEXT NOT NULL
                )""");
            s.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id   TEXT PRIMARY KEY,
                    type       TEXT NOT NULL,
                    order_id   TEXT NOT NULL,
                    amount     INTEGER NOT NULL,
                    applied_at TEXT NOT NULL
                )""");
        }
        conn.setAutoCommit(false);
    }

    /**
     * Applies the event to its order unless it was applied before.
     * Returns CONFLICT if the id was already used for an event with different content.
     */
    public synchronized Outcome apply(Event e) throws SQLException {
        if (!EVENT_TYPES.contains(e.type())) {
            throw new IllegalArgumentException("unknown event type: " + e.type());
        }
        try {
            Optional<Event> previous = findEvent(e.id());
            if (previous.isPresent()) {
                conn.rollback();
                return previous.get().equals(e) ? Outcome.DUPLICATE : Outcome.CONFLICT;
            }

            String now = Instant.now().toString();
            try (PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO processed_events (event_id, type, order_id, amount, applied_at) VALUES (?, ?, ?, ?, ?)")) {
                ps.setString(1, e.id());
                ps.setString(2, e.type());
                ps.setString(3, e.orderId());
                ps.setLong(4, e.amount());
                ps.setString(5, now);
                ps.executeUpdate();
            }

            long paid = 0;
            long refunded = 0;
            boolean failed = false;
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT amount_paid, amount_refunded, payment_failed FROM orders WHERE order_id = ?")) {
                ps.setString(1, e.orderId());
                try (ResultSet rs = ps.executeQuery()) {
                    if (rs.next()) {
                        paid = rs.getLong(1);
                        refunded = rs.getLong(2);
                        failed = rs.getInt(3) != 0;
                    }
                }
            }

            switch (e.type()) {
                case PAYMENT_SUCCEEDED -> paid = Math.addExact(paid, e.amount());
                case PAYMENT_FAILED -> failed = true;
                case REFUND_CREATED -> refunded = Math.addExact(refunded, e.amount());
                default -> throw new IllegalStateException();
            }

            try (PreparedStatement ps = conn.prepareStatement("""
                    INSERT INTO orders (order_id, status, amount_paid, amount_refunded, payment_failed, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        status = excluded.status,
                        amount_paid = excluded.amount_paid,
                        amount_refunded = excluded.amount_refunded,
                        payment_failed = excluded.payment_failed,
                        updated_at = excluded.updated_at""")) {
                ps.setString(1, e.orderId());
                ps.setString(2, status(paid, refunded, failed));
                ps.setLong(3, paid);
                ps.setLong(4, refunded);
                ps.setInt(5, failed ? 1 : 0);
                ps.setString(6, now);
                ps.executeUpdate();
            }

            conn.commit();
            return Outcome.APPLIED;
        } catch (SQLException | RuntimeException ex) {
            rollbackQuietly(ex);
            throw ex;
        }
    }

    public synchronized Optional<Order> findOrder(String orderId) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT order_id, status, amount_paid, amount_refunded, updated_at FROM orders WHERE order_id = ?")) {
            ps.setString(1, orderId);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) {
                    return Optional.empty();
                }
                return Optional.of(new Order(rs.getString(1), rs.getString(2), rs.getLong(3), rs.getLong(4), rs.getString(5)));
            }
        } finally {
            conn.rollback(); // end the read transaction
        }
    }

    /**
     * Status is derived from accumulated totals rather than from the last event,
     * so the result does not depend on the order in which events are delivered.
     */
    static String status(long paid, long refunded, boolean failed) {
        if (paid > 0) {
            if (refunded >= paid) return "REFUNDED";
            if (refunded > 0) return "PARTIALLY_REFUNDED";
            return "PAID";
        }
        return failed ? "PAYMENT_FAILED" : "PENDING";
    }

    private Optional<Event> findEvent(String eventId) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT event_id, type, order_id, amount FROM processed_events WHERE event_id = ?")) {
            ps.setString(1, eventId);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) {
                    return Optional.empty();
                }
                return Optional.of(new Event(rs.getString(1), rs.getString(2), rs.getString(3), rs.getLong(4)));
            }
        }
    }

    private void rollbackQuietly(Exception cause) {
        try {
            conn.rollback();
        } catch (SQLException rollbackFailure) {
            cause.addSuppressed(rollbackFailure);
        }
    }

    @Override
    public synchronized void close() throws SQLException {
        conn.close();
    }
}
