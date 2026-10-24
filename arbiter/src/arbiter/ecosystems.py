"""Imports and manifests for Go, Java and Ruby (Python and JS live in authored.py).

Each ecosystem answers three questions for the undeclared-import check:
which names does a file import, which does the repository declare, and which
are standard library or the repository's own code. Every answer is a plain
function of the files, so the check stays deterministic and offline.

Go:   `import "x"` / import blocks; stdlib has no dot in its first path element;
      every go.mod `module` is local; a file's imports are declared by the
      `require` lines of its nearest enclosing go.mod.
Java: `import a.b.C;`; java./javax./jdk./sun./org.w3c./org.xml. are the JDK;
      the repository's own `package` declarations are local; pom.xml groupIds
      and Gradle "group:artifact" coordinates and plugin ids are declared, a
      package named for a declared artifactId resolves, and a short table of
      guaranteed transitive packages (_JAVA_TRANSITIVE) resolves through its
      provider.
Ruby: `require "x"` (never require_relative); a fixed stdlib list; files under
      lib/ are local; Gemfile `gem` and Gemfile.lock specs are declared.
"""
from __future__ import annotations

import re
from pathlib import Path

MANIFESTS = {"go.mod", "go.sum", "pom.xml", "build.gradle", "build.gradle.kts",
             "libs.versions.toml", "Gemfile", "Gemfile.lock", "gems.rb"}
# A gem's runtime dependencies live in its .gemspec, which the Gemfile pulls in
# with a bare `gemspec` line: Rails declares concurrent-ruby only there.
GEMSPEC_SUFFIX = ".gemspec"
# A Gradle subproject's build file may be named for the module rather than
# `build.gradle`: spring-security's `spring-security-oauth2-client.gradle`
# declares spring-r2dbc, and was never read (round 2, ARB-065).
GRADLE_SUFFIXES = (".gradle", ".gradle.kts")

ECOSYSTEM_OF = {"go": "go", "java": "maven", "ruby": "rubygems"}
MANIFESTS_OF = {"go": {"go.mod"}, "java": {"pom.xml", "build.gradle", "build.gradle.kts", "libs.versions.toml"},
                "ruby": {"Gemfile", "gems.rb", "Gemfile.lock"}}

# --- Go ----------------------------------------------------------------------
_GO_SINGLE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)
_GO_BLOCK = re.compile(r"^\s*import\s*\((.*?)\)", re.MULTILINE | re.DOTALL)
_GO_SPEC = re.compile(r'^\s*(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)


def go_imports(text: str) -> list[str]:
    names = [m.group(1) for m in _GO_SINGLE.finditer(text)]
    for block in _GO_BLOCK.finditer(text):
        names += [m.group(1) for m in _GO_SPEC.finditer(block.group(1))]
    return names


def go_declared(manifest_text: str) -> tuple[set[str], set[str]]:
    """(declared module paths, the module's own path) from go.mod."""
    own = {m.group(1) for m in re.finditer(r"^\s*module\s+(\S+)", manifest_text, re.MULTILINE)}
    # A module path need not contain a slash: `go.opencensus.io v0.24.0` is a
    # whole module path, and requiring one made grpc-go's observability module
    # report its own declared dependency (ARB-065).
    req = set(re.findall(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-]+(?:/\S+)?)\s+v\S+",
                         manifest_text, re.MULTILINE))
    return req, own


def nearest_manifest(path: str, manifest_dirs: list[str]) -> str | None:
    """The deepest directory in `manifest_dirs` that contains `path`.

    Go resolves an import against the go.mod of the module the file belongs
    to, which is the nearest one above it: grpc-go has eleven, and
    `gcp/observability/go.mod` is the only one that declares its exporters.
    Directories are repository-relative, "" for the root.
    """
    best: str | None = None
    for d in manifest_dirs:
        if (d == "" or path.startswith(d + "/")) and (best is None or len(d) > len(best)):
            best = d
    return best


def go_is_stdlib(path: str) -> bool:
    return "." not in path.split("/")[0]


def go_resolves(path: str, declared: set[str], own: set[str]) -> bool:
    if go_is_stdlib(path):
        return True
    return any(path == d or path.startswith(d + "/") for d in declared | own)


# --- Java --------------------------------------------------------------------
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)
_JAVA_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_JDK = ("java.", "javax.", "jdk.", "sun.", "com.sun.", "org.w3c.", "org.xml.", "org.ietf.")


def java_imports(text: str) -> list[str]:
    return [m.group(1) for m in _JAVA_IMPORT.finditer(text)]


def java_package(text: str) -> str | None:
    m = _JAVA_PACKAGE.search(text)
    return m.group(1) if m else None


# A Gradle build that compiles its own plugins (`buildSrc`, `java-gradle-plugin`,
# `groovy-gradle-plugin`, `kotlin-dsl`, `gradleApi()`) gets the Gradle API on
# its classpath without declaring it. spring-security's buildSrc imports
# `org.gradle.*` 22 times (ARB-065).
_GRADLE_PLUGIN_BUILD = re.compile(
    r"\b(?:java-gradle-plugin|groovy-gradle-plugin|kotlin-dsl|gradleApi\s*\(\s*\))")
ARTIFACT = "artifact:"   # marks an artifactId in the declared set


def java_declared(manifest_text: str, path: str = "") -> set[str]:
    """groupIds, Gradle plugin ids and `artifact:`-prefixed artifactIds."""
    groups = set(re.findall(r"<groupId>\s*([\w.\-]+)\s*</groupId>", manifest_text))
    # A groupId given as a property (`<groupId>${conscrypt.groupId}</groupId>`
    # with `<conscrypt.groupId>org.conscrypt</conscrypt.groupId>` in the
    # parent's <properties>, netty): the property's value is the group.
    groups |= set(re.findall(r"<[\w.\-]+\.groupId>\s*([\w.\-]+)\s*</[\w.\-]+\.groupId>",
                             manifest_text))
    coords = re.findall(r"""['"]([\w.\-]+):([\w.\-]+)(?::[\w.\-]+)?['"]""", manifest_text)
    groups |= {g for g, _ in coords}
    groups |= set(re.findall(r"group\s*[:=]\s*['\"]([\w.\-]+)['\"]", manifest_text))
    # Plugin ids are groups too: `id 'io.spring.nullability'` brings in the
    # annotations that plugin enforces (see _JAVA_TRANSITIVE).
    groups |= set(re.findall(r"""\bid\s*\(?\s*['"]([\w-]+(?:\.[\w-]+)+)['"]""", manifest_text))
    artifacts = set(re.findall(r"<artifactId>\s*([\w.\-]+)\s*</artifactId>", manifest_text))
    artifacts |= {a for _, a in coords}
    groups |= {ARTIFACT + a.lower() for a in artifacts}
    if _GRADLE_PLUGIN_BUILD.search(manifest_text) or path.startswith("buildSrc/") \
            or "/buildSrc/" in path:
        groups.add("org.gradle")
    return groups


# Packages whose name does not start with their groupId. Each resolves when any
# of the listed groupIds is declared. Found on caffeine, resilience4j, gson and
# HikariCP, where these made up most of 2,295 false reports.
_JAVA_PACKAGE_GROUPS = {
    "lombok": ("org.projectlombok",),
    "edu.umd.cs.findbugs": ("com.github.spotbugs", "com.google.code.findbugs"),
    "junit": ("junit",),
    "org.junit": ("junit", "org.junit", "org.junit.jupiter", "org.junit.platform", "org.junit.vintage"),
    "com.google.common": ("com.google.guava",),
    "com.google.errorprone": ("com.google.errorprone",),
    "net.jcip": ("net.jcip",),
    "kotlin": ("org.jetbrains.kotlin", "org.jetbrains.kotlinx"),
    "kotlinx": ("org.jetbrains.kotlinx",),
    "reactor": ("io.projectreactor", "io.projectreactor.addons"),
    "io.reactivex": ("io.reactivex.rxjava2", "io.reactivex.rxjava3", "io.reactivex"),
    "groovy": ("org.codehaus.groovy", "org.apache.groovy"),
    "spock": ("org.spockframework",),
    "feign": ("io.github.openfeign", "com.netflix.feign"),
    "retrofit2": ("com.squareup.retrofit2",),
    "okhttp3": ("com.squareup.okhttp3",),
    "dagger": ("com.google.dagger",),
    "org.openjdk.jmh": ("org.openjdk.jmh",),
    "net.jpountz": ("org.lz4", "at.yawk.lz4"),
    # Round 2 gold (ARB-065): packages named for neither their groupId nor
    # their artifactId. netty's lz4 fork `at.yawk.lz4:lz4-java`, GraalVM's
    # `org.graalvm.nativeimage:svm`, RXTX, Selenium (keycloak's
    # test-framework, 44 of its 158), uap-java.
    "com.oracle.svm": ("org.graalvm.nativeimage", "org.graalvm.sdk"),
    "gnu.io": ("org.rxtx",),
    "org.openqa.selenium": ("org.seleniumhq.selenium",),
    "ua_parser": ("com.github.ua-parser",),
    # spring-security: HtmlUnit 2 and Saxon under their SourceForge groups,
    # and the Netscape LDAP SDK published as `ldapsdk:ldapsdk`.
    "com.gargoylesoftware.htmlunit": ("net.sourceforge.htmlunit",),
    "net.sf.saxon": ("net.sourceforge.saxon", "net.sf.saxon"),
    "netscape.ldap": ("ldapsdk",),
}


# Packages that arrive with a declared dependency rather than being declared
# themselves -- shaded into it, or an `api` dependency of it. Each resolves
# when any listed group (or Gradle plugin id) is declared. Every entry is a
# documented dependency of the listed artifact, found on gold (ARB-065):
#   org.jspecify      spring-core 7 has an `api` dependency on jspecify, and
#                     the io.spring.nullability plugin adds it (1,174 on
#                     spring-security);
#   org.aopalliance   repackaged inside spring-aop (76);
#   org.reactivestreams  the API reactor-core and RxJava implement (17);
#   io.netty          reactor-netty and grpc-netty are built on it;
#   net.minidev       json-smart, a dependency of nimbus-jose-jwt;
#   net.shibboleth    the support libraries OpenSAML 4+ depends on;
#   org.jose4j        a dependency of wildfly-elytron's JOSE module (keycloak);
#   org.opentest4j    the assertion-failure types every JUnit 5 artifact depends on.
# A transitive dependency used directly is still a manifest gap in principle;
# these are the ones whose provider guarantees them, so reporting them is noise.
_JAVA_TRANSITIVE = {
    "org.jspecify": ("org.springframework", "io.spring.nullability", "org.springframework.boot",
                     "com.google.guava"),
    "org.aopalliance": ("org.springframework",),
    "org.reactivestreams": ("io.projectreactor", "io.reactivex.rxjava2", "io.reactivex.rxjava3",
                            "org.reactivestreams"),
    "io.netty": ("io.projectreactor.netty", "io.grpc", "io.vertx", "io.quarkus", "io.rsocket"),
    "net.minidev": ("com.nimbusds",),
    "net.shibboleth": ("org.opensaml",),
    "org.jose4j": ("org.wildfly.security", "org.bitbucket.b_c"),
    "org.opentest4j": ("org.junit",),
    # Round 2 gold (ARB-065), keycloak and spring-security: Quarkus ArC and
    # RESTEasy bring the CDI APIs (jakarta.inject, jakarta.annotation);
    # titanium-json-ld and Quarkus bring jakarta.json; the Quarkus Operator
    # SDK extension is built on the Java Operator SDK; Infinispan core
    # depends on JGroups and RxJava 3; quarkus-agroal on Agroal; Undertow on
    # XNIO; Guava 33.4+ on JSpecify; Selenium's HtmlUnit driver on HtmlUnit;
    # rsocket-core on Netty buffers.
    "jakarta.inject": ("io.quarkus", "jakarta.enterprise", "org.jboss.resteasy", "io.quarkiverse"),
    "jakarta.annotation": ("io.quarkus", "jakarta.enterprise", "org.jboss.resteasy",
                           "io.quarkiverse"),
    "jakarta.json": ("com.apicatalog", "io.quarkus", "org.eclipse.parsson", "org.glassfish"),
    "io.javaoperatorsdk": ("io.quarkiverse.operatorsdk",),
    "org.jgroups": ("org.infinispan",),
    "io.reactivex.rxjava3": ("org.infinispan",),
    "io.agroal": ("io.quarkus",),
    "org.xnio": ("io.undertow", "org.jboss.resteasy"),
    "org.htmlunit": ("org.seleniumhq.selenium",),
    # httpclient's jakarta-rest-client: httpcore5-jackson3 depends on Jackson 3
    # (`tools.jackson.databind`); spring-r2dbc on the R2DBC SPI.
    "tools.jackson": (ARTIFACT + "httpcore5-jackson3",),
    "jakarta.ws.rs": ("org.apache.cxf", "org.jboss.resteasy", "org.glassfish.jersey",
                      "io.quarkus"),
    "io.r2dbc": (ARTIFACT + "spring-r2dbc",),
    # Eclipse Angus Mail implements, and depends on, the Jakarta Mail API.
    "jakarta.mail": ("org.eclipse.angus",),
    "jakarta.activation": ("org.eclipse.angus",),
}
# First package segments that are a domain, not a name: for these the second
# segment is the one an artifactId might match (`org.jose4j`).
_DOMAIN_SEGMENTS = {"org", "com", "net", "io", "dev", "me", "info", "edu", "de", "uk", "fr"}


def _artifact_named(name: str, declared: set[str]) -> bool:
    """`picocli.CommandLine` from info.picocli:picocli, `liquibase.Liquibase`
    from org.liquibase:liquibase-core: the package's distinctive segment is
    the artifactId, or its first dash-separated word, though not the group.
    Generic words (`core`, `api`, `common`) never match."""
    parts = name.split(".")
    seg = parts[1] if parts[0] in _DOMAIN_SEGMENTS and len(parts) > 2 else parts[0]
    if len(seg) < 4 or seg in {"core", "common", "commons", "util", "utils", "test", "main"}:
        return False
    seg = seg.lower()
    if ARTIFACT + seg in declared:
        return True
    return any(a.startswith(ARTIFACT + seg + "-") for a in declared)


def java_resolves(name: str, declared: set[str], local: set[str]) -> bool:
    if name.startswith(_JDK):
        return True
    for pkg, groups in _JAVA_PACKAGE_GROUPS.items():
        if (name == pkg or name.startswith(pkg + ".")) and any(
                g in declared or any(d.startswith(g + ".") for d in declared) for g in groups):
            return True
    for pkg, groups in _JAVA_TRANSITIVE.items():
        if name.startswith(pkg + ".") and any(
                g in declared or any(d.startswith(g + ".") for d in declared) for g in groups):
            return True
    if _artifact_named(name, declared):
        return True
    for prefix in declared | local:
        if prefix.startswith(ARTIFACT):
            continue
        parts = prefix.split(".")
        # A groupId names an organisation, and its artifacts' packages start
        # with it -- or with its first two segments (com.fasterxml.jackson.core
        # ships com.fasterxml.jackson.databind).
        for n in (len(parts), min(len(parts), 2)):
            stem = ".".join(parts[:n])
            if stem and (name == stem or name.startswith(stem + ".")):
                return True
    return False


# --- Ruby --------------------------------------------------------------------
_RUBY_REQUIRE = re.compile(r"""^\s*require\s*\(?\s*['"]([^'"]+)['"]""", re.MULTILINE)
RUBY_STDLIB = {
    "abbrev", "base64", "benchmark", "bigdecimal", "cgi", "coverage", "csv", "date",
    "delegate", "digest", "English", "erb", "etc", "fiber", "fiddle", "fileutils", "find",
    "forwardable", "getoptlong", "io", "ipaddr", "irb", "json", "logger", "matrix",
    "monitor", "mutex_m", "net", "objspace", "observer", "open-uri", "open3", "openssl",
    "optparse", "ostruct", "pathname", "pp", "prettyprint", "prime", "pstore", "psych",
    "racc", "rbconfig", "readline", "reline", "resolv", "ripper", "securerandom", "set",
    "shellwords", "singleton", "socket", "stringio", "strscan", "syslog", "tempfile",
    "thread", "time", "timeout", "tmpdir", "tsort", "un", "uri", "weakref", "yaml", "zlib",
    # Shipped with every Ruby: extension and default libraries (ARB-065).
    "pty", "mkmf", "expect", "nkf", "win32ole", "rubygems", "bundler",
    "continuation", "jruby", "java",
}
# Gems whose require path is not their name: `require "concurrent"` comes from
# concurrent-ruby, `require "nio"` from nio4r, `require "zip"` from rubyzip.
_RUBY_REQUIRE_GEMS = {
    "concurrent": "concurrent-ruby", "nio": "nio4r", "zip": "rubyzip",
    "active_support": "activesupport", "active_record": "activerecord",
    "action_controller": "actionpack", "action_dispatch": "actionpack",
    "action_view": "actionview", "active_model": "activemodel", "active_job": "activejob",
    "action_mailer": "actionmailer", "action_cable": "actioncable",
    "active_storage": "activestorage", "google/protobuf": "google-protobuf",
    "dalli": "dalli", "redis_client": "redis-client", "net/http/persistent": "net-http-persistent",
}


def ruby_imports(text: str) -> list[str]:
    return [m.group(1) for m in _RUBY_REQUIRE.finditer(text)]


def ruby_import_sites(text: str) -> list[tuple[str, int]]:
    return [(m.group(1), m.start(1)) for m in _RUBY_REQUIRE.finditer(text)]


def ruby_declared(manifest_text: str) -> set[str]:
    gems = set(re.findall(r"""^\s*gem\s+['"]([\w.\-]+)['"]""", manifest_text, re.MULTILINE))
    gems |= set(re.findall(r"^\s{4}([\w.\-]+) \(", manifest_text, re.MULTILINE))  # Gemfile.lock specs
    gems |= set(re.findall(r"""\badd_(?:runtime_|development_)?dependency\s*\(?\s*['"]([\w.\-]+)['"]""",
                           manifest_text))  # .gemspec
    return {g.lower() for g in gems}


# Rails' component gems all arrive with `gem "rails"` (rack's test.rb pulls it
# in inline and requires "action_controller/railtie").
_RAILS_COMPONENTS = {"activesupport", "activerecord", "actionpack", "actionview",
                     "activemodel", "activejob", "actionmailer", "actioncable",
                     "activestorage", "railties"}


def ruby_is_gemfile(name: str) -> bool:
    """Gemfile, gems.rb, and a named Bundler gemfile (`release_gems.rb`, rubygems'
    tool/bundler, used with BUNDLE_GEMFILE) and their lockfiles."""
    return name in MANIFESTS_OF["ruby"] or name.endswith(("_gems.rb", "_gems.rb.lock"))


# A script that declares its own gems with bundler/inline (`gemfile do gem
# "rails" end`) declares them for itself.
_RUBY_INLINE = re.compile(r"""^\s*require\s*\(?\s*['"]bundler/inline['"]""", re.M)
# `begin; require "x"; rescue LoadError` is an optional require.
_RUBY_RESCUED = re.compile(r"(?ms)^[ \t]*begin\b(.*?)^[ \t]*rescue\s+(?:::)?LoadError\b")


def ruby_inline_declared(text: str) -> set[str]:
    return ruby_declared(text) if _RUBY_INLINE.search(text) else set()


def ruby_optional_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(1), m.end(1)) for m in _RUBY_RESCUED.finditer(text)]


def ruby_resolves(path: str, declared: set[str], local: set[str]) -> bool:
    head = path.split("/")[0]
    if head in RUBY_STDLIB or path in local or head in local:
        return True
    gem = _RUBY_REQUIRE_GEMS.get(path) or _RUBY_REQUIRE_GEMS.get(head)
    if gem and (gem in declared or (gem in _RAILS_COMPONENTS and "rails" in declared)):
        return True
    # `require "rack/test"` comes from gem rack-test; `require "rack"` from rack.
    for name in {head.lower(), path.lower().replace("/", "-"), head.lower().replace("_", "-")}:
        if name in declared:
            return True
    return False


def ruby_local(paths: list[str]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        if not p.endswith(".rb"):
            continue
        parts = p.split("/")
        if "lib" in parts:
            rest = parts[parts.index("lib") + 1:]
            if rest:
                out.add("/".join(rest)[:-3])
                out.add(rest[0][:-3] if len(rest) == 1 else rest[0])
        out.add(Path(p).stem)
    return out
