"""The Java rule pack (ARB-061): the precision decisions the corpus can't show.

fixtures/planted/java proves every rule fires on its planted defect and stays
silent on the correct twin. These tests pin what was decided against real
code (HikariCP, caffeine, commons-lang, gson, resilience4j, WebGoat,
spring-petclinic), so a later edit that widens a rule back into those false
positives fails here.
"""
from __future__ import annotations

import pytest
from helpers import _scan_text

from arbiter import ast as ts
from arbiter import probes  # noqa: F401 - registers the packs before lang_rules is imported
from arbiter.lang_rules import load_packs

PACK = "java"
RULES = {
    "check-then-act-unsynchronized", "unsynchronized-counter", "http-client-no-timeout",
    "http-request-no-timeout", "url-connection-no-timeout", "rest-template-no-timeout",
    "retry-without-backoff", "worker-loop-no-stop", "swallowed-exception", "sql-string-built",
    "command-injection", "object-input-stream", "xml-decoder", "jackson-default-typing",
    "stub-method", "test-without-assertion", "trust-all-certificates",
    "hostname-verifier-disabled", "weak-random-for-secret",
}

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")

MAIN = "src/main/java/app/Svc.java"
TEST = "src/test/java/app/SvcTest.java"


def rules_in(tmp_path, code: str, name: str = MAIN) -> set[str]:
    found = _scan_text(tmp_path, name, code, only=["lang_java"])
    return {f.rule_id.removeprefix("arbiter/java.") for f in found}


def test_pack_has_every_rule_with_full_metadata():
    by_id = {r["id"]: r for r in load_packs()[PACK]["rules"]}
    assert set(by_id) == RULES
    for rule in by_id.values():
        for key in ("title", "severity", "confidence", "cwe", "description", "remediation", "tags"):
            assert rule.get(key), f"{rule['id']} lacks {key}"
        assert all(c.startswith("CWE-") for c in rule["cwe"])


# --- races -----------------------------------------------------------------

_WORKER = """package app;
import java.util.*;
import java.util.concurrent.*;
class Svc implements Runnable {
    private final Map<String, String> cache = new HashMap<>();
    private int processed;
    private final ExecutorService pool = Executors.newFixedThreadPool(2);
    %s
}
"""


def test_conditional_remove_after_a_check_is_atomic(tmp_path):
    # caffeine EventDispatcher: `if (q.get(k) == f) q.remove(k, f);`
    code = _WORKER % ("public void run() { String v = pick(); "
                      "if (cache.get(\"k\") == v) { cache.remove(\"k\", v); } }")
    assert "check-then-act-unsynchronized" not in rules_in(tmp_path, code)


def test_check_then_put_on_a_shared_map_is_reported(tmp_path):
    code = _WORKER % "public void run() { if (!cache.containsKey(\"k\")) { cache.put(\"k\", \"v\"); } }"
    assert "check-then-act-unsynchronized" in rules_in(tmp_path, code)


def test_a_local_counter_declared_with_var_is_not_shared(tmp_path):
    # HikariPool housekeeper: `var maxToRemove = n; ... maxToRemove--;`
    code = _WORKER % ("void bump() { processed++; }\n"
                      "    public void run() { var left = processed; while (left > 0) { left--; } }")
    assert "unsynchronized-counter" not in rules_in(tmp_path, code)


def test_a_for_loop_index_does_not_hide_a_shared_counter(tmp_path):
    code = _WORKER % "public void run() { for (int i = 0; i < 3; i++) { processed++; } }"
    assert "unsynchronized-counter" in rules_in(tmp_path, code)


def test_test_support_outside_src_test_is_not_production(tmp_path):
    # caffeine's src/jmh, src/jsr166Test, guava/src/compatibilityTest: the
    # inventory calls them source; their imports say otherwise.
    code = _WORKER.replace("import java.util.*;", "import java.util.*;\nimport org.junit.Test;") % (
        "public void run() { processed++; if (!cache.containsKey(\"k\")) cache.put(\"k\", \"v\"); }")
    found = rules_in(tmp_path, code, "src/jmh/java/app/Svc.java")
    assert not found & {"unsynchronized-counter", "check-then-act-unsynchronized"}


# --- retries and errors ----------------------------------------------------

_RETRY = """package app;
import java.util.concurrent.*;
import java.util.concurrent.locks.*;
class Svc {
    private final ReentrantLock lock = new ReentrantLock();
    %s
}
"""


def test_reentering_a_wait_after_an_interrupt_is_not_a_retry_storm(tmp_path):
    # caffeine BoundedLocalCache.lock(), commons-lang Memoizer.compute()
    code = _RETRY % ("void acquire(Future<String> f) { for (;;) { try { f.get(); return; } "
                     "catch (InterruptedException e) { Thread.currentThread().interrupt(); } } }")
    assert "retry-without-backoff" not in rules_in(tmp_path, code)


def test_try_lock_with_a_timeout_is_a_wait(tmp_path):
    code = _RETRY % ("void acquire() throws Exception { while (true) { try { "
                     "boolean ok = lock.tryLock(1, TimeUnit.SECONDS); return; } catch (RuntimeException e) { } } }")
    assert "retry-without-backoff" not in rules_in(tmp_path, code)


def test_hot_retry_on_failure_is_reported(tmp_path):
    code = _RETRY % ("String fetch(Callable<String> c) { while (true) { try { return c.call(); } "
                     "catch (Exception e) { System.err.println(e); } } }")
    assert "retry-without-backoff" in rules_in(tmp_path, code)


_LOOP = """package app;
class Svc {
    void each(java.util.List<Runnable> tasks) {
        %s
    }
}
"""


def test_a_catch_that_says_why_it_ignores_is_not_reported_outside_a_while(tmp_path):
    # HikariConfig/PropertyElf `// continue`, commons-lang ReflectionDiffBuilder
    code = _LOOP % "for (Runnable t : tasks) { try { t.run(); } catch (Exception e) { // best effort\n } }"
    assert "swallowed-exception" not in rules_in(tmp_path, code)


def test_a_commented_catch_in_a_worker_while_loop_is_reported(tmp_path):
    code = _LOOP % ("while (!tasks.isEmpty()) { try { tasks.remove(0).run(); } "
                    "catch (Exception e) { // ignore\n } }")
    assert "swallowed-exception" in rules_in(tmp_path, code)


def test_an_empty_broad_catch_is_reported_anywhere(tmp_path):
    code = _LOOP % "try { tasks.get(0).run(); } catch (Exception e) { }"
    assert "swallowed-exception" in rules_in(tmp_path, code)


# --- stubs -----------------------------------------------------------------

_STUB = """package app;
import java.util.Iterator;
public class Svc implements Iterator<String> {
    public boolean hasNext() { return false; }
    public String next() { return null; }
    %s
}
"""


@pytest.mark.parametrize("method", [
    # Iterator.remove / Map.Entry.setValue: the JDK's optional-operation idiom
    "@Override public void remove() { throw new UnsupportedOperationException(); }",
    # gson ReflectionHelper, resilience4j SemaphoreBasedRateLimiter
    'public long reserve() { throw new UnsupportedOperationException("not supported in this implementation"); }',
    "protected Object hook() { throw new UnsupportedOperationException(); }",
])
def test_unsupported_operation_idioms_are_not_stubs(tmp_path, method):
    assert "stub-method" not in rules_in(tmp_path, _STUB % method)


def test_interface_defaults_are_not_stubs(tmp_path):
    # caffeine CacheLoader.loadAll, Policy.getEntryIfPresentQuietly
    code = ("package app;\npublic interface Svc {\n"
            "    default String loadAll() { throw new UnsupportedOperationException(); }\n}\n")
    assert "stub-method" not in rules_in(tmp_path, code)


@pytest.mark.parametrize("method", [
    "public String charge() { throw new UnsupportedOperationException(); }",
    'public String charge() { throw new UnsupportedOperationException("TODO"); }',
    'public String charge() { throw new UnsupportedOperationException("not implemented yet"); }',
])
def test_a_public_method_that_only_throws_is_a_stub(tmp_path, method):
    assert "stub-method" in rules_in(tmp_path, _STUB % method)


# --- tests -----------------------------------------------------------------

_TEST = """package app;
import org.junit.jupiter.api.Test;
class SvcTest {
    private final Svc svc = new Svc();
    @Test
    void roundTrips() {
        %s
    }
    private void roundTrip(String s) { org.junit.jupiter.api.Assertions.assertNotNull(s); }
}
"""


def test_a_test_that_delegates_to_a_helper_is_not_reported(tmp_path):
    # gson `assertRoundTrip(...)`, commons-lang `testFixture(false)`
    assert "test-without-assertion" not in rules_in(tmp_path, _TEST % "roundTrip(svc.name());", TEST)


def test_a_test_that_only_calls_the_code_is_reported(tmp_path):
    assert "test-without-assertion" in rules_in(tmp_path, _TEST % "svc.name();", TEST)


# --- randomness and deserialization ---------------------------------------

_RANDOM = """package app;
import java.util.Random;
class Svc {
    private final Random random;
    %s
}
"""


def test_a_seeded_random_in_a_constructor_is_a_simulation(tmp_path):
    # caffeine simulator: `this.random = new Random(settings.randomSeed())`
    code = _RANDOM % "Svc(long seed) { this.random = new Random(seed); }"
    assert "weak-random-for-secret" not in rules_in(tmp_path, code)


def test_a_constructor_that_assigns_a_token_is_reported(tmp_path):
    code = _RANDOM % ("private final long token;\n"
                      "    Svc() { this.random = null; this.token = new Random().nextLong(); }")
    assert "weak-random-for-secret" in rules_in(tmp_path, code)


def test_random_in_a_token_method_is_reported(tmp_path):
    code = _RANDOM % ("Svc() { this.random = null; }\n"
                      "    String newToken() { return Long.toHexString(new Random().nextLong()); }")
    assert "weak-random-for-secret" in rules_in(tmp_path, code)


def test_deserialization_round_trip_in_test_support_is_not_reported(tmp_path):
    # caffeine JSR166TestCase / MOAT clone their own objects
    code = ("package app;\nimport java.io.*;\nimport static org.junit.Assert.assertEquals;\n"
            "class Svc { Object clone(byte[] b) throws Exception {\n"
            "  return new ObjectInputStream(new ByteArrayInputStream(b)).readObject(); } }\n")
    assert "object-input-stream" not in rules_in(tmp_path, code, "src/jsr166Test/java/app/Svc.java")
    plain = code.replace("import static org.junit.Assert.assertEquals;\n", "")
    assert "object-input-stream" in rules_in(tmp_path / "p", plain)


# --- the gold corpus and OWASP Benchmark (ARB-065) -------------------------

def lines_of(tmp_path, code: str, rule: str, name: str = MAIN) -> list[int]:
    found = _scan_text(tmp_path, name, code, only=["lang_java"])
    return sorted(f.location.start_line for f in found if f.rule_id == f"arbiter/java.{rule}")


_GOLD_TEST = """package app;
import org.junit.jupiter.api.*;
class SvcTest extends Base {
    private final Svc svc = new Svc();
    @Disabled("Assumes a pooled buffer") @Test @Override public void testMaxFastWritableBytes() {}
    @Test @Override public void testInherited() { }
    @Test void buildWhenScopesIsNullThenDoesNotThrowAnyException() { svc.name(); }
    @Test void contextLoads() { svc.name(); }
    @Test void shouldNotThrowExceptionWhenValueIsBetweenBounds() { svc.name(); }
    @Test void preAuthorizeWhenRoleAdminThenPasses() { svc.name(); }
    @Test @DisplayName("does not throw on an empty name") void emptyName() { svc.name(); }
    @Test void pipeline() throws Throwable { Throwable cause = svc.error(); if (cause != null) { throw cause; } }
    @Test void readerIndexBoundaryCheck() { svc.name(); }
    @Test @Override public void testOverriddenButRuns() { svc.name(); }
    @Test void validateConfiguration() { runOnServer.run(SvcTest::validateOnServer); }
    @Test void forEachName() { svc.names().forEach(System.out::println); }
}
"""


def test_switched_off_and_does_not_throw_tests_are_not_reported(tmp_path):
    # netty AbstractAdaptiveByteBufTest (`@Disabled` empty overrides),
    # spring-security `...ThenDoesNotThrowAnyException` / `contextLoads`,
    # commons-lang `shouldNotThrowExceptionWhen...`,
    # netty DefaultChannelPipelineTest (`if (cause != null) throw cause`),
    # keycloak `runOnServer.run(SvcTest::validateOnServer)`.
    # A test that only calls the code is still reported, override or not.
    assert lines_of(tmp_path, _GOLD_TEST, "test-without-assertion", TEST) == [13, 14, 16]


_SQL = """package app;
import java.sql.*;
class Svc {
    Connection conn; javax.persistence.EntityManager em; String tableName;
    ResultSet byName(String name) throws SQLException {
        String sql = "SELECT * FROM users WHERE name = '" + name + "'";
        try {
            Statement st = conn.createStatement();
            return st.executeQuery(sql);
        } finally { conn.close(); }
    }
    Object cluster() {
        return em.createNativeQuery("SELECT min(c) FROM " + tableName + " WHERE last_update > ?1").getSingleResult();
    }
    Object realm() throws SQLException {
        return conn.prepareStatement("select ID from " + getTableName("REALM") + " where NAME = ?");
    }
    Object mixed(String id) throws SQLException {
        return conn.prepareStatement("select ID from " + getTableName("REALM") + " where NAME = '" + id + "'");
    }
    void jmx(String queryString) { execute("/../jmxproxy/" + queryString); }
    ResultSet sort(String column) throws SQLException {
        return conn.createStatement().executeQuery("select id from SERVERS order by " + column);
    }
    void schema(String user) throws SQLException { conn.createStatement().execute("SET SCHEMA " + user); }
    void execute(String s) {}
    String getTableName(String t) { return t; }
}
"""


def test_sql_built_before_a_try_is_tracked_and_identifier_splices_are_not(tmp_path):
    # OWASP Benchmark: `String sql = ...` then the sink inside `try { }`.
    # keycloak JpaClusterEventStoreProvider / migrations: only a table name
    # is spliced, values are `?`. tomcat JMXQueryTask: Ant's `execute`.
    # A bare `column` from a request parameter (WebGoat's ORDER BY lesson) is
    # not identifier-shaped enough to trust, and stays reported.
    assert lines_of(tmp_path, _SQL, "sql-string-built") == [9, 19, 23, 25]


_CMD = """package app;
import java.util.*;
class Svc {
    Process a(String bar) throws Exception {
        Runtime r = Runtime.getRuntime();
        return r.exec("echo " + bar);
    }
    Process b(String param) throws Exception {
        List<String> argList = new ArrayList<>();
        argList.add("sh"); argList.add("-c"); argList.add("echo " + param);
        ProcessBuilder pb = new ProcessBuilder();
        pb.command(argList);
        return pb.start();
    }
    Process c(List<String> args) throws Exception {
        return new ProcessBuilder(args).start();
    }
    int d() { Runtime r = Runtime.getRuntime(); return r.availableProcessors(); }
}
"""


def test_runtime_in_a_local_and_a_shell_list_are_command_injection(tmp_path):
    # OWASP Benchmark cmdi: `Runtime r = Runtime.getRuntime(); r.exec(cmd + bar)`
    # and `pb.command(argList)` with `sh -c`. A ProcessBuilder handed a list
    # with no shell in the method runs no shell, and is not reported.
    assert lines_of(tmp_path, _CMD, "command-injection") == [6, 12]


_REMEMBER = """package app;
class Svc {
    String issue() {
        float rand = new java.util.Random().nextFloat();
        String rememberMeKey = Float.toString(rand).substring(2);
        return rememberMeKey;
    }
    String bytes() {
        byte[] b = new byte[8];
        new java.util.Random(1L).nextBytes(b);
        String rememberMeKey = java.util.Base64.getEncoder().encodeToString(b);
        return rememberMeKey;
    }
    double jitter() {
        double value = java.lang.Math.random();
        String label = Double.toString(value);
        return value;
    }
}
"""


def test_a_random_value_that_becomes_a_remember_me_key_is_reported(tmp_path):
    # OWASP Benchmark weakrand: the value is drawn into a local, then turned
    # into `rememberMeKey`; `java.util.Random` written in full and
    # `java.lang.Math.random()` both count. A jitter stays unreported.
    assert lines_of(tmp_path, _REMEMBER, "weak-random-for-secret") == [4, 10]
