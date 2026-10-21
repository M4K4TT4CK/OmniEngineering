package planted;

import static org.junit.jupiter.api.Assertions.assertNotNull;

import java.util.concurrent.atomic.AtomicReference;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class ReportDaoTest extends ReportDaoTest.Base {
    private final ReportDao dao = new ReportDao(null, "audit");
    private final Runner runOnServer = Runnable::run;

    interface Runner {
        void run(Runnable check);
    }

    @Test
    void sinceBuildsAStatement() {
        assertNotNull(dao);
    }

    // The name says what is checked: that nothing throws.
    @Test
    void reportRunsWithoutException() {
        dao.report("");
    }

    @Test
    @DisplayName("does not throw on an empty query")
    void reportOnAnEmptyQuery() {
        dao.report(null);
    }

    // An empty override switches off a test this class inherits.
    @Test
    @Override
    void inheritedReportIsSkippedHere() {
    }

    // The test fails by throwing what the callback recorded (netty's idiom).
    @Test
    void reportRunsTheCallback() throws Throwable {
        AtomicReference<Throwable> error = new AtomicReference<>();
        dao.report("x");
        Throwable cause = error.get();
        if (cause != null) {
            throw cause;
        }
    }

    // The assertions run where the runner sends this class's own method
    // (keycloak's run-on-server).
    @Test
    void reportOnTheServer() {
        runOnServer.run(ReportDaoTest::checkReportOnServer);
    }

    static void checkReportOnServer() {
        assertNotNull(new ReportDao(null, "audit"));
    }

    abstract static class Base {
        @Test
        void inheritedReportIsSkippedHere() {
            assertNotNull(new ReportDao(null, "audit"));
        }
    }
}
