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
from arbiter.lang_rules import load_packs

PACK = "java"
RULES = {
    "check-then-act-unsynchronized", "unsynchronized-counter", "http-client-no-timeout",
    "http-request-no-timeout", "url-connection-no-timeout", "rest-template-no-timeout",
    "retry-without-backoff", "worker-loop-no-stop", "swallowed-exception", "sql-string-built",
    "command-injection", "object-input-stream", "xml-decoder", "jackson-default-typing",
    "stub-method", "test-without-assertion", "trust-all-certificates",
    "hostname-verifier-disabled", "weak-random-for-secret",
    # CWE Top 25, round 1 (ARB-062)
    "jwt-unsigned-accepted", "jwt-decode-without-verify", "hardcoded-signing-key",
    "hardcoded-db-password", "csrf-disabled", "csrf-disabled-stateless",
    "stack-trace-to-client", "exception-message-to-client",
    # CWE Top 25, round 2 (ARB-062)
    "xss-unescaped-html", "ssrf-request-url", "argument-injection", "parsed-number-narrowed",
    "parsed-count-overflow", "upload-client-filename",
    # AI-written-code defect classes (ARB-083)
    "mail-starttls-not-required", "mail-ssl-trust-all", "retry-constant-delay",
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
    # An `@Override` test replaces an inherited one whose assertion the parent
    # owns, so it is not reported either way (ARB-065 follow-up). A plain test
    # that only calls the code is still reported.
    assert lines_of(tmp_path, _GOLD_TEST, "test-without-assertion", TEST) == [13, 16]


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


# --- CWE Top 25, round 1 (ARB-062) -----------------------------------------

_JWT_PEEK = """package app;
import com.auth0.jwt.JWT;
import com.nimbusds.jwt.JWTParser;
class Svc {
    String issuer(String token) throws Exception {
        return JWTParser.parse(token).getJWTClaimsSet().getIssuer();
    }
    String kid(String token) { return JWT.decode(token).getKeyId(); }
    String user(String token) { return JWT.decode(token).getSubject(); }
}
"""


def test_a_jwt_peek_at_issuer_or_key_id_is_not_a_decode_without_verify(tmp_path):
    # spring-security's JwtIssuerAuthenticationManagerResolver reads `iss`
    # to pick the verifier; reading the subject is trusting the token.
    assert lines_of(tmp_path, _JWT_PEEK, "jwt-decode-without-verify") == [9]


def test_a_file_that_verifies_the_token_is_not_reported(tmp_path):
    code = _JWT_PEEK.replace(
        "String user(String token) {",
        "String user(String token) { verifier.verify(token);")
    assert lines_of(tmp_path, code, "jwt-decode-without-verify") == []


_KEYS = """package app;
import com.auth0.jwt.algorithms.Algorithm;
import java.sql.DriverManager;
class Svc {
    Object a() { return Algorithm.HMAC256("changeme"); }
    Object b() { return Algorithm.HMAC256("your-secret-here"); }
    Object c() throws Exception { return DriverManager.getConnection("jdbc:h2:mem:x", "sa", ""); }
    Object d() { return Algorithm.HMAC256(secret); }
    Object e() { return Algorithm.HMAC256("hunter2-prod"); }
    Object f() throws Exception { return DriverManager.getConnection("jdbc:mysql://db/app?user=app&password=s3cret"); }
}
"""


def test_placeholders_and_empty_passwords_are_not_hardcoded_credentials(tmp_path):
    assert lines_of(tmp_path, _KEYS, "hardcoded-signing-key") == [9]
    assert lines_of(tmp_path, _KEYS, "hardcoded-db-password") == [10]


def test_a_literal_key_in_test_code_is_not_reported(tmp_path):
    assert lines_of(tmp_path, _KEYS, "hardcoded-signing-key", name=TEST) == []


def test_a_finding_does_not_carry_the_secret(tmp_path):
    found = _scan_text(tmp_path, MAIN, _KEYS, only=["lang_java"])
    for f in found:
        assert "hunter2" not in f.title + f.evidence and "s3cret" not in f.title + f.evidence


_CSRF = """package app;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
class Svc {
    void chain(HttpSecurity http) throws Exception {
        http.csrf(c -> c.disable())
            .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
    }
}
"""


def test_csrf_off_in_a_stateless_config_is_a_low_confidence_finding(tmp_path):
    # Decision (docs/cwe-top25.md, Java 352): a stateless bearer-token API
    # turns CSRF off by design, so it is reported apart, at low/low.
    assert lines_of(tmp_path, _CSRF, "csrf-disabled") == []
    assert lines_of(tmp_path, _CSRF, "csrf-disabled-stateless") == [6]
    stateful = _CSRF.replace("SessionCreationPolicy.STATELESS", "SessionCreationPolicy.IF_REQUIRED")
    assert lines_of(tmp_path, stateful, "csrf-disabled") == [6]


_ERRORS = """package app;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
class Svc {
    ResponseEntity<String> a() {
        try { return ResponseEntity.ok(run()); }
        catch (IllegalArgumentException e) { return ResponseEntity.badRequest().body(e.getMessage()); }
    }
    ResponseEntity<String> b() {
        try { return ResponseEntity.ok(run()); }
        catch (RuntimeException e) { return ResponseEntity.status(500).body(e.getMessage()); }
    }
    @ExceptionHandler(Exception.class)
    String c(Exception ex) { return ex.getMessage(); }
    @ExceptionHandler(OrderNotFound.class)
    String d(OrderNotFound ex) { return ex.getMessage(); }
    ResponseEntity<String> e(Result r) { return ResponseEntity.ok(r.toString()); }
}
"""


def test_only_a_broad_exceptions_message_is_reported(tmp_path):
    # The text of a validation or domain exception is written for the caller;
    # a RuntimeException's or an Exception handler's is not.
    assert lines_of(tmp_path, _ERRORS, "exception-message-to-client") == [11, 14]
    assert lines_of(tmp_path, _ERRORS, "stack-trace-to-client") == []


_TOMCAT = """package app;
class Svc {
    boolean service(Request req, Response response) throws Exception {
        try { req.read(); } catch (IOException e) { log(e); }
        try { parseCookies(req); }
        catch (IllegalArgumentException e) { response.sendError(400, e.getMessage()); }
        try { decode(req); }
        catch (IOException ioe) { response.sendError(400, sm.getString("invalidUri", ioe.getMessage())); }
        try { run(req); }
        catch (IOException ioe) { response.sendError(500, ioe.getMessage()); }
        return true;
    }
}
"""


def test_a_message_counts_only_for_its_own_broad_catch(tmp_path):
    # tomcat CoyoteAdapter: every catch parameter is `e`, so an earlier
    # `catch (IOException e)` must not lend its type to a later validation
    # catch; a message wrapped in a bundle template was chosen for the caller.
    assert lines_of(tmp_path, _TOMCAT, "exception-message-to-client") == [10]


def test_a_nested_call_in_the_issuer_peek_is_still_a_peek(tmp_path):
    code = _JWT_PEEK.replace("JWTParser.parse(token)", "JWTParser.parse(token.trim())")
    assert lines_of(tmp_path, code, "jwt-decode-without-verify") == [9]


# --- CWE Top 25, round 2 (ARB-062) -----------------------------------------

_XSS = """package app;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.util.HtmlUtils;
@RestController
class Svc {
    @GetMapping("/a") String a(@RequestParam String q) { return "<b>" + q + "</b>"; }
    @GetMapping("/b") String b(@RequestParam String q) { return "<b>" + q.length() + "</b>"; }
    @GetMapping("/c") String c(@PathVariable Long id) { return "<b>" + id + "</b>"; }
    @GetMapping("/d") String d(@RequestParam String q) { return "{\\"q\\": " + q.length() + "}"; }
    @GetMapping("/e") String e(@RequestParam String q) { String t = title(); return "<h1>" + t + "</h1>"; }
}
"""


def test_xss_needs_the_bound_string_beside_html_and_no_encoder(tmp_path):
    # b prints only a length, c binds a number, d builds no HTML, e splices a
    # value that is not the request parameter.
    assert lines_of(tmp_path, _XSS, "xss-unescaped-html") == [6]
    # An encoder anywhere in the file skips it: OWASP Benchmark escapes in an
    # inner-class helper the value passes through, which no method-scoped
    # check can follow.
    helper = _XSS.replace("class Svc {", "class Svc {\n    String esc(String s) { return HtmlUtils.htmlEscape(s); }")
    assert lines_of(tmp_path, helper, "xss-unescaped-html") == []


_XSS_SERVLET = """package app;
import javax.servlet.http.*;
class Svc extends HttpServlet {
    protected void doGet(HttpServletRequest request, HttpServletResponse response) throws java.io.IOException {
        String who = request.getParameter("who");
        response.setContentType("%s");
        java.io.PrintWriter out = response.getWriter();
        out.println("Hello, " + who);
        out.println("static text");
    }
}
"""


def test_servlet_xss_needs_an_html_page(tmp_path):
    assert lines_of(tmp_path, _XSS_SERVLET % "text/html", "xss-unescaped-html") == [8]
    assert lines_of(tmp_path, _XSS_SERVLET % "text/plain", "xss-unescaped-html") == []
    encoded = (_XSS_SERVLET % "text/html").replace('" + who', '" + Encode.forHtml(who)')
    assert lines_of(tmp_path, encoded, "xss-unescaped-html") == []


_SSRF = """package app;
import java.net.*;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;
@RestController
class Svc {
    RestTemplate rest = new RestTemplate();
    @GetMapping("/a") String a(@RequestParam String url) { return rest.getForObject(url, String.class); }
    @GetMapping("/b") String b(@RequestParam String id) { return rest.getForObject("https://api.example.com/items/" + id, String.class); }
    @GetMapping("/c") String c(@RequestParam String url) { return URI.create(url).getScheme(); }
    @GetMapping("/d") String d(@RequestParam String url) {
        if (!ALLOWED_HOSTS.contains(URI.create(url).getHost())) throw new IllegalArgumentException();
        return rest.getForObject(url, String.class);
    }
    String e(String url) { return rest.getForObject(url, String.class); }
    @GetMapping("/f") String f(@RequestParam String host) { return rest.getForObject("http://" + host + "/x", String.class); }
}
"""


def test_ssrf_needs_the_caller_to_pick_the_host(tmp_path):
    # b only picks a path on a fixed host, c only parses, d checks the host,
    # e is not bound to the request in this method.
    assert lines_of(tmp_path, _SSRF, "ssrf-request-url") == [8, 16]


_ARGV = """package app;
import java.util.List;
import org.springframework.web.bind.annotation.*;
@RestController
class Svc {
    @PostMapping("/a") void a(@RequestParam String url) throws Exception { new ProcessBuilder("git", "clone", url).start(); }
    @PostMapping("/b") void b(@RequestParam String url) throws Exception { new ProcessBuilder("git", "clone", "--", url).start(); }
    @PostMapping("/c") void c(@RequestParam String name) throws Exception { new ProcessBuilder("echo", name).start(); }
    void d(String url) throws Exception { new ProcessBuilder("git", "clone", url).start(); }
    @PostMapping("/e") void e(@RequestParam String host) throws Exception { Runtime.getRuntime().exec(new String[] {"ping", "-c", "1", host}); }
    @PostMapping("/f") void f(@RequestParam String src) throws Exception { new ProcessBuilder(List.of("rsync", "-a", "--", src, "/srv")).start(); }
}
"""


def test_argument_injection_needs_a_request_value_before_double_dash(tmp_path):
    # b and f end the options first; echo parses none; d is not request-bound.
    assert lines_of(tmp_path, _ARGV, "argument-injection") == [6, 10]


_NARROW = """package app;
class Svc {
    byte a(String s) { return (byte) Integer.parseInt(s); }
    byte b(String hex) { return (byte) Integer.parseInt(hex, 16); }
    int c(String s) { return (int) Long.parseLong(s); }
    long d(String s) { return Long.parseLong(s); }
    int e(String s) { return Math.toIntExact(Long.parseLong(s)); }
    char f(String s) { return (char) Integer.parseInt(s, 16); }
}
"""


def test_a_narrowing_cast_of_a_parse_is_reported_but_not_the_hex_byte_idiom(tmp_path):
    assert lines_of(tmp_path, _NARROW, "parsed-number-narrowed") == [3, 5]


_COUNT = """package app;
class Svc {
    byte[] a(String s) { int n = Integer.parseInt(s); return new byte[n * 16]; }
    byte[] b(String s) { int n = Integer.parseInt(s); if (n > 1024) throw new IllegalArgumentException(); return new byte[n * 16]; }
    byte[] c(String s) { int n = Integer.parseInt(s); return new byte[Math.multiplyExact(n, 16)]; }
    byte[] d(int n) { return new byte[n * 16]; }
    java.nio.ByteBuffer e(String s) { int n = Integer.parseInt(s); return java.nio.ByteBuffer.allocate(16 * n); }
    byte[] f(String s) { int n = Integer.parseInt(s); n = Math.min(n, 64); return new byte[n * 16]; }
}
"""


def test_a_parsed_count_in_an_allocation_needs_a_range_check(tmp_path):
    assert lines_of(tmp_path, _COUNT, "parsed-count-overflow") == [3, 7]


_UPLOAD = """package app;
import java.io.File;
import java.nio.file.*;
import org.apache.commons.io.FilenameUtils;
import org.springframework.web.multipart.MultipartFile;
class Svc {
    void a(MultipartFile f) throws Exception { f.transferTo(new File(DIR, f.getOriginalFilename())); }
    void b(MultipartFile f) throws Exception { f.transferTo(new File(DIR, FilenameUtils.getName(f.getOriginalFilename()))); }
    void c(MultipartFile f) throws Exception { f.transferTo(Paths.get(DIR, java.util.UUID.randomUUID().toString())); }
    void d(MultipartFile f) throws Exception {
        String name = f.getOriginalFilename();
        Files.copy(f.getInputStream(), Paths.get(DIR).resolve(name));
    }
    String e(MultipartFile f) { return "uploaded " + f.getOriginalFilename(); }
}
"""


def test_an_upload_is_reported_once_and_only_under_the_clients_name(tmp_path):
    # a: one finding though two sinks nest; b strips the directory; c picks
    # the name; e only echoes the name.
    assert lines_of(tmp_path, _UPLOAD, "upload-client-filename") == [7, 12]


_WEBGOAT = """package app;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
@RestController
class Svc {
    @PostMapping("/a") String a(@RequestParam String password) {
        StringBuilder out = new StringBuilder();
        out.append("<b>Length: </b>" + password.length() + "</br>");
        return out.toString();
    }
    @PostMapping("/b") void b(@RequestParam("file") MultipartFile file, String username) throws Exception {
        var dir = new java.io.File(ROOT, username);
        try (var in = file.getInputStream()) {
            java.nio.file.Files.copy(in, dir.toPath().resolve("avatar.png"));
        }
        log.debug("saved to {}", new java.io.File(dir, file.getOriginalFilename()));
        var zip = new java.util.zip.ZipFile(dir);
        var e = zip.entries().nextElement();
        java.nio.file.Files.copy(zip.getInputStream(e), new java.io.File(dir, e.getName()).toPath());
    }
}
"""


def test_webgoat_lookalikes_are_not_xss_or_client_named_uploads(tmp_path):
    # WebGoat SecurePasswordsAssignment prints only the password's length;
    # FileServer names a directory after the user and logs the client's name;
    # ProfileZipSlip's entry name is zip slip (CWE-22), not the upload's name.
    assert lines_of(tmp_path, _WEBGOAT, "xss-unescaped-html") == []
    assert lines_of(tmp_path, _WEBGOAT, "upload-client-filename") == []


_NUMBER = """package app;
class Svc extends Number {
    public int intValue() { try { return Integer.parseInt(value); } catch (NumberFormatException e) { return (int) Long.parseLong(value); } }
    void colours(String[] el, byte[] colors) { for (int i = 0; i < 4; i++) { colors[i] = (byte) Integer.parseInt(el[i]); } }
    short port(String s) { return (short) Integer.parseInt(s); }
}
"""


def test_number_contracts_and_byte_buffers_narrow_on_purpose(tmp_path):
    # gson LazilyParsedNumber.intValue, tomcat drawboard DrawMessage colours.
    assert lines_of(tmp_path, _NUMBER, "parsed-number-narrowed") == [5]
