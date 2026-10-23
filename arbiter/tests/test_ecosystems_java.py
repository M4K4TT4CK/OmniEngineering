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
