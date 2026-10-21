package planted;

import org.junit.jupiter.api.Test;

class ReportDaoTest {
    private final ReportDao dao = new ReportDao(null, "audit");

    @Test
    void sinceBuildsAStatement() throws Exception { // expect: arbiter/java.test-without-assertion
        dao.report("all");
    }

    @Test
    void reportRuns() { // expect: arbiter/java.test-without-assertion
        dao.report("");
    }

    @Test
    void reportOnAnEmptyQuery() { // expect: arbiter/java.test-without-assertion
        dao.report(null);
    }

    @Test
    void inheritedReportIsSkippedHere() { // expect: arbiter/java.test-without-assertion
        dao.report("x");
    }

    @Test
    void reportOnTheServer() { // expect: arbiter/java.test-without-assertion
        Runnable onServer = () -> dao.report("x");
        onServer.run();
    }
}
