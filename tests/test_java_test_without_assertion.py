"""java.test-without-assertion: the by-design shapes from the 2026-10-21 gold
triage (ARB-065), each beside a calls-only test that must stay reported.

Kept apart from tests/test_lang_java.py so the precision decisions for this
one rule are read together.
"""
from __future__ import annotations

import pytest
from helpers import _scan_text

from arbiter import ast as ts

pytestmark = pytest.mark.skipif(not ts.available(), reason="tree-sitter not installed")

TEST = "src/test/java/app/SvcTest.java"


def lines(tmp_path, code: str, name: str = TEST) -> list[int]:
    found = _scan_text(tmp_path, name, code, only=["lang_java"])
    return sorted(f.location.start_line for f in found
                  if f.rule_id == "arbiter/java.test-without-assertion")


BY_DESIGN = """package app;
import org.junit.jupiter.api.*;
class SvcTest extends Base {
    Svc svc = new Svc();
    @Override @Test public void testNoArgConstructor() { Object o = new Svc(); }
    @Test void testEpollCanBeLoaded() { Epoll.ensureAvailability(); }
    @Test void resolveWhenHeaderIsInvalidThenNoneExceptionIsThrown() { svc.name(); }
    @Test void testSoLingerNoAssertError() { svc.close(); }
    @Test void webFluxConfigurationDoNotCauseCircularReference() { svc.name(); }
    @Test void constructorWhenAuthoritiesIsNullThenCreated() { new Svc(null); }
    @Test void preAuthorizeAdminRoleGranted() { svc.admin(); }
    @Test void securedInterfaceMethodAllowsAllAccess() { svc.secured(); }
    @Test void targetShouldAllowProtectedMethodInvocationWithCorrectRole() { svc.secured(); }
    @Test void buildWhenScopeIsEmptyThenScopeNotRequired() { svc.build(); }
    @Test void decodeWhenRSAPublicKeyThenSuccess() { svc.decode(); }
    @Test void testCanBeInstancedWhenOpenSslIsNotAvailable() { new Svc(); }
    @Test void useIsSerializable() { svc.write(); }
    @Test void multiPermissionEvaluatorConfig() {
        svc.register();
        // no exception
    }
    @Test void testSetCharacterEncodingNull() {
        // Should not fail with an NPE
        svc.setCharacterEncoding(null);
    }
    @Test void testToStringFromNull() { svc.toString(); }
}
"""


def test_by_design_shapes_are_silent_and_a_calls_only_test_is_not(tmp_path):
    assert lines(tmp_path, BY_DESIGN) == [26]


def test_override_of_a_nested_anonymous_class_does_not_excuse_the_test(tmp_path):
    # netty IoUringIoHandlerTest: the @Override belongs to an anonymous class
    # inside the test body, and the comment to a lambda; the test asserts nothing.
    code = """package app;
import org.junit.jupiter.api.*;
class SvcTest {
    @Test public void testOptions() {
        Runnable r = new Runnable() {
            @Override public void run() {
                // no exception
            }
        };
        r.run();
    }
    @Test void allowedNothingHere() { new Thread(() -> { /* nope */ }).start(); }
    @Test void callsOnly() { new Svc().name(); }
}
"""
    assert lines(tmp_path, code) == [4, 13]


@pytest.mark.parametrize("cls", ["TesterFastHttpDateFormatPerformance", "Benchmarks",
                                 "QueueBenchmark", "ParserPerformanceTest"])
def test_performance_harness_classes_are_silent(tmp_path, cls):
    code = f"""package app;
import org.junit.jupiter.api.*;
class {cls} {{
    @Test public void testGetCurrentDateConcurrent() {{ new Svc().name(); }}
}}
"""
    assert lines(tmp_path, code, f"src/test/java/app/{cls}.java") == []


def test_a_name_merely_containing_allow_or_created_words_is_still_reported(tmp_path):
    code = """package app;
import org.junit.jupiter.api.*;
class SvcTest {
    @Test void shallowCopy() { new Svc().copy(); }
    @Test void recreatedCache() { new Svc().reset(); }
    @Test void testCreatedAt() { new Svc().createdAt(); }
    @Test void testShallowed() { new Svc().name(); }
}
"""
    assert lines(tmp_path, code) == [4, 5, 6, 7]
