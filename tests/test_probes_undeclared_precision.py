"""authored.undeclared-import on exemplary code (ARB-065).

The gold benchmark (tools/gold_benchmark.py) reported 2,325 undeclared imports
on 24 exemplary repositories, about 99% false. Each test pins one manifest-lookup
cause and keeps an import that must still be reported beside it.
"""
from __future__ import annotations

from pathlib import Path

from arbiter.engine import run_scan
from arbiter.policy import load_config


def _scan(tmp_path: Path, files: dict[str, str], probe: str):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    rep = run_scan([str(tmp_path)], load_config(None), only=[probe], use_adapters=False,
                   use_cache=False)
    return rep.active()


def _undeclared(tmp_path, files):
    return {f.evidence.rsplit(":", 1)[-1] for f in _scan(tmp_path, files, "authored")
            if f.rule_id == "arbiter/authored.undeclared-import"}


# --- undeclared-import: Python requirement layouts ---------------------------

def test_requirements_directory_and_variant_names_are_manifests(tmp_path):
    """celery declares click and vine in requirements/default.txt (88 on gold)."""
    names = _undeclared(tmp_path, {
        "pyproject.toml": "[project]\nname = \"pkg\"\n",
        "requirements/default.txt": "billiard>=4.2\nclick>=8.1,<9\n",
        "requirements/extras/redis.txt": "redis>=4.5\n",
        "requirements-test.txt": "pytest-timeout\n",
        "requirements.in": "vine\n",
        "pkg/worker.py": ("import click\nimport billiard\nimport redis\nimport vine\n"
                          "import pytest_timeout\nimport boto3\n"),
    })
    assert names == {"boto3"}, "only the import nothing declares is left"


def test_pyproject_dependency_groups_and_extras_declare(tmp_path):
    names = _undeclared(tmp_path, {
        "pyproject.toml": ("[project]\nname = \"pkg\"\ndependencies = [\"attrs>=23\"]\n"
                           "[project.optional-dependencies]\nfast = [\"orjson>=3\", \"uvloop\"]\n"
                           "[dependency-groups]\ndev = [\"hypothesis>=6\"]\n"),
        "pkg/a.py": "import attrs\nimport orjson\nimport uvloop\nimport hypothesis\nimport httpx\n",
    })
    assert names == {"httpx"}


# --- undeclared-import: Go modules ---------------------------------------------

def test_go_file_resolves_against_its_nearest_go_mod(tmp_path):
    """grpc-go's gcp/observability is its own module and declares
    go.opencensus.io, a module path with no slash in it (17 on gold)."""
    names = _undeclared(tmp_path, {
        "go.mod": "module google.golang.org/grpc\n\ngo 1.22\n\nrequire golang.org/x/net v0.30.0\n",
        "gcp/observability/go.mod": (
            "module google.golang.org/grpc/gcp/observability\n\ngo 1.22\n\nrequire (\n"
            "\tgo.opencensus.io v0.24.0\n"
            "\tcontrib.go.opencensus.io/exporter/stackdriver v0.13.15\n)\n"),
        "gcp/observability/oc.go": (
            "package observability\n\nimport (\n\t\"go.opencensus.io/stats/view\"\n"
            "\t\"contrib.go.opencensus.io/exporter/stackdriver\"\n"
            "\t\"google.golang.org/grpc\"\n\t\"golang.org/x/net/http2\"\n)\n"),
        "server.go": ("package grpc\n\nimport (\n\t\"golang.org/x/net/http2\"\n"
                      "\t\"go.opencensus.io/trace\"\n)\n"),
    })
    # The nested module imports golang.org/x/net without requiring it; the
    # root imports opencensus, which only the nested module requires. Both are
    # real gaps once each file answers to its own go.mod.
    assert names == {"golang.org/x/net/http2", "go.opencensus.io/trace"}


# --- undeclared-import: Node --------------------------------------------------

def test_node_builtins_without_the_node_scheme_are_not_packages(tmp_path):
    """nest imports `http`, `net` and `crypto` bare (79 on gold)."""
    names = _undeclared(tmp_path, {
        "package.json": '{"name": "app", "dependencies": {"express": "4.19.2"}}',
        "src/a.ts": ("import * as http from 'http';\nimport { readFile } from 'fs/promises';\n"
                     "import { createHash } from \"crypto\";\nconst cp = require('child_process');\n"
                     "import { pipeline } from 'stream/promises';\nimport express from 'express';\n"
                     "import dayjs from 'dayjs';\n"),
    })
    assert names == {"dayjs"}


def test_tsconfig_path_aliases_and_workspace_globs_are_local(tmp_path):
    names = _undeclared(tmp_path, {
        "package.json": '{"name": "root", "private": true, "workspaces": ["packages/*"]}',
        "packages/core/package.json": '{"name": "@nestjs/core", "dependencies": {"tslib": "2.6.2"}}',
        "packages/common/package.json": '{"name": "@nestjs/common"}',
        "tsconfig.json": ('{\n  // comments are allowed here\n  "compilerOptions": {\n'
                          '    "paths": {"@app/*": ["src/*"], "~shared": ["lib/shared/index.ts"]}\n'
                          '  }\n}\n'),
        "packages/core/router.ts": ("import { Injectable } from '@nestjs/common';\n"
                                    "import { x } from '@app/config';\nimport s from '~shared';\n"
                                    "import tslib from 'tslib';\nimport rxjs from 'rxjs';\n"),
    })
    assert names == {"rxjs"}


# --- undeclared-import: Java ---------------------------------------------------

def test_java_transitive_artifact_named_and_gradle_api_packages_resolve(tmp_path):
    """spring-security: org.jspecify (1,174 on gold) via spring-core and the
    io.spring.nullability plugin, org.aopalliance inside spring-aop, the Gradle
    API in buildSrc. keycloak: picocli and liquibase, whose packages are named
    for the artifactId, not the groupId."""
    names = _undeclared(tmp_path, {
        "build.gradle": ("plugins { id 'io.spring.nullability' }\n"
                         "dependencies { implementation 'org.springframework:spring-aop:7.0.0'\n"
                         "  implementation 'info.picocli:picocli:4.7.6' }\n"),
        "buildSrc/build.gradle": "plugins { id 'groovy-gradle-plugin' }\n",
        "pom.xml": ("<project><dependencies><dependency><groupId>org.liquibase</groupId>"
                    "<artifactId>liquibase-core</artifactId></dependency></dependencies></project>\n"),
        "core/src/main/java/a/A.java": (
            "package a;\nimport org.jspecify.annotations.Nullable;\n"
            "import org.aopalliance.intercept.MethodInterceptor;\n"
            "import picocli.CommandLine;\nimport liquibase.Liquibase;\n"
            "import org.gradle.api.Plugin;\n"
            "import com.google.common.collect.ImmutableList;\n"
            "import org.jose4j.jwk.JsonWebKey;\nclass A {}\n"),
    })
    # Guava is declared nowhere, and jose4j only resolves through elytron.
    assert names == {"com.google.common.collect.ImmutableList", "org.jose4j.jwk.JsonWebKey"}


def test_requirement_lines_with_extras_and_namespace_packages_declare(tmp_path):
    """celery: `pytest-celery[all]>=1.3.0` in requirements/test.txt."""
    names = _undeclared(tmp_path, {
        "pyproject.toml": "[project]\nname = \"pkg\"\n",
        "requirements/test.txt": ("pytest-celery[all]>=1.3.0\nuvloop; sys_platform != 'win32'\n"
                                  "backports.zoneinfo>=0.2\n"),
        "pkg/a.py": "import pytest_celery\nimport uvloop\nimport backports\nimport pymongo\n",
    })
    assert names == {"pymongo"}


# --- undeclared-import: Ruby ---------------------------------------------------

def test_gemspec_dependencies_and_renamed_requires_resolve(tmp_path):
    """Rails declares concurrent-ruby in activesupport.gemspec, and it is
    required as `concurrent` (26 on gold)."""
    names = _undeclared(tmp_path, {
        "Gemfile": "source 'https://rubygems.org'\ngemspec\ngem 'nio4r'\n",
        "activesupport/activesupport.gemspec": (
            'Gem::Specification.new do |s|\n  s.add_dependency "concurrent-ruby", "~> 1.0"\n'
            '  s.add_runtime_dependency("rubyzip")\nend\n'),
        "activesupport/lib/active_support/x.rb": (
            'require "concurrent/map"\nrequire "nio"\nrequire "zip"\nrequire "pty"\n'
            'require "bundler"\nrequire "httparty"\n'),
    })
    assert names == {"httparty"}
