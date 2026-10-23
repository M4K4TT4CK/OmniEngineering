"""Java undeclared-import precision (ARB-061): found on caffeine, resilience4j,
gson and HikariCP, where 2,295 findings were nearly all false."""
from arbiter import ecosystems as eco
from arbiter.inventory import classify


def test_gradle_version_catalog_is_a_manifest():
    assert "libs.versions.toml" in eco.MANIFESTS_OF["java"]
    toml = (
        '[libraries]\n'
        'guava = { module = "com.google.guava:guava", version.ref = "guava" }\n'
        'jspecify = { group = "org.jspecify", name = "jspecify", version = "1.0.0" }\n'
    )
    declared = eco.java_declared(toml)
    assert {"com.google.guava", "org.jspecify"} <= declared


def test_packages_named_differently_from_their_group_resolve():
    declared = {"org.projectlombok", "com.google.guava", "org.junit.jupiter", "com.github.spotbugs"}
    for name in ("lombok.Data", "com.google.common.collect.ImmutableList",
                 "org.junit.jupiter.api.Test", "edu.umd.cs.findbugs.annotations.Nullable"):
        assert eco.java_resolves(name, declared, set()), name


def test_an_alias_only_resolves_when_its_group_is_declared():
    assert not eco.java_resolves("lombok.Data", {"com.google.guava"}, set())
    assert not eco.java_resolves("com.google.common.base.Strings", set(), set())


def test_gradle_test_source_sets_are_tests():
    for path in ("src/jmh/java/Bench.java", "src/testFixtures/java/Fake.java",
                 "src/it/java/ItCase.java", "src/jsr166Test/java/Tck.java"):
        assert classify(path, "java") == "test", path
    assert classify("src/main/java/App.java", "java") != "test"


# --- round 2 gold precision (ARB-065) ----------------------------------------

def test_a_groupid_given_as_a_maven_property_is_declared():
    # netty: `<groupId>${conscrypt.groupId}</groupId>`, the value in <properties>.
    pom = ("<properties><conscrypt.groupId>org.conscrypt</conscrypt.groupId></properties>\n"
           "<dependency><groupId>${conscrypt.groupId}</groupId></dependency>\n")
    assert eco.java_resolves("org.conscrypt.Conscrypt", eco.java_declared(pom), set())


def test_packages_that_arrive_with_their_provider_resolve_only_with_it():
    cases = [
        ("net.jpountz.lz4.LZ4Compressor", "at.yawk.lz4"),            # netty's lz4 fork
        ("com.oracle.svm.core.annotate.Alias", "org.graalvm.nativeimage"),
        ("gnu.io.SerialPort", "org.rxtx"),
        ("org.openqa.selenium.By", "org.seleniumhq.selenium"),       # keycloak test-framework
        ("reactor.blockhound.BlockHound", "io.projectreactor.tools"),  # sub-group of a listed one
        ("jakarta.inject.Inject", "io.quarkus"),
        ("io.javaoperatorsdk.operator.api.Context", "io.quarkiverse.operatorsdk"),
        ("org.jgroups.JChannel", "org.infinispan"),
        ("io.netty.buffer.ByteBuf", "io.rsocket"),
        ("jakarta.ws.rs.Produces", "org.apache.cxf"),
    ]
    for name, group in cases:
        assert eco.java_resolves(name, {group}, set()), name
        assert not eco.java_resolves(name, {"com.example"}, set()), name
    # A provider known by its artifactId: httpcore5-jackson3 brings Jackson 3.
    declared = eco.java_declared("<artifactId>httpcore5-jackson3</artifactId>")
    assert eco.java_resolves("tools.jackson.databind.ObjectMapper", declared, set())


def test_a_gradle_build_file_named_for_its_module_is_a_manifest():
    # spring-security's `oauth2/oauth2-client/spring-security-oauth2-client.gradle`.
    from arbiter.authored import is_manifest_path
    assert is_manifest_path("oauth2/oauth2-client/spring-security-oauth2-client.gradle")
    assert is_manifest_path("app/build.gradle.kts")


def test_jakarta_mail_arrives_with_angus_mail():
    declared = eco.java_declared("<groupId>org.eclipse.angus</groupId>"
                                 "<artifactId>angus-mail</artifactId>")
    assert eco.java_resolves("jakarta.mail.Session", declared, set())
    assert not eco.java_resolves("jakarta.mail.Session", {"org.example"}, set())
