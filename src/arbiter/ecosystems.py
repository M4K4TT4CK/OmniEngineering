"""Imports and manifests for Go, Java and Ruby (Python and JS live in authored.py).

Each ecosystem answers three questions for the undeclared-import check:
which names does a file import, which does the repository declare, and which
are standard library or the repository's own code. Every answer is a plain
function of the files, so the check stays deterministic and offline.

Go:   `import "x"` / import blocks; stdlib has no dot in its first path element;
      go.mod `module` is local, `require` lines are declared.
Java: `import a.b.C;`; java./javax./jdk./sun./org.w3c./org.xml. are the JDK;
      the repository's own `package` declarations are local; pom.xml groupIds
      and Gradle "group:artifact" coordinates are declared.
Ruby: `require "x"` (never require_relative); a fixed stdlib list; files under
      lib/ are local; Gemfile `gem` and Gemfile.lock specs are declared.
"""
from __future__ import annotations

import re
from pathlib import Path

MANIFESTS = {"go.mod", "go.sum", "pom.xml", "build.gradle", "build.gradle.kts",
             "libs.versions.toml", "Gemfile", "Gemfile.lock", "gems.rb"}

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
    req = set(re.findall(r"^\s*(?:require\s+)?([\w.\-]+\.[\w.\-]+/\S+)\s+v\S+", manifest_text, re.MULTILINE))
    return req, own


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


def java_declared(manifest_text: str) -> set[str]:
    groups = set(re.findall(r"<groupId>\s*([\w.\-]+)\s*</groupId>", manifest_text))
    groups |= set(re.findall(r"""['"]([\w.\-]+):[\w.\-]+(?::[\w.\-]+)?['"]""", manifest_text))
    groups |= set(re.findall(r"group\s*[:=]\s*['\"]([\w.\-]+)['\"]", manifest_text))
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
}


def java_resolves(name: str, declared: set[str], local: set[str]) -> bool:
    if name.startswith(_JDK):
        return True
    for pkg, groups in _JAVA_PACKAGE_GROUPS.items():
        if (name == pkg or name.startswith(pkg + ".")) and any(g in declared for g in groups):
            return True
    for prefix in declared | local:
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
}


def ruby_imports(text: str) -> list[str]:
    return [m.group(1) for m in _RUBY_REQUIRE.finditer(text)]


def ruby_declared(manifest_text: str) -> set[str]:
    gems = set(re.findall(r"""^\s*gem\s+['"]([\w.\-]+)['"]""", manifest_text, re.MULTILINE))
    gems |= set(re.findall(r"^\s{4}([\w.\-]+) \(", manifest_text, re.MULTILINE))  # Gemfile.lock specs
    return {g.lower() for g in gems}


def ruby_resolves(path: str, declared: set[str], local: set[str]) -> bool:
    head = path.split("/")[0]
    if head in RUBY_STDLIB or path in local or head in local:
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
