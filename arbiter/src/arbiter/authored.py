"""Defects characteristic of machine-authored code.

## Why this is its own probe

A large and growing share of new code is drafted by a language model, and
model-drafted code fails in ways human-drafted code mostly does not. The
failures are not subtle bugs in tricky logic; they are confident, fluent,
plausible-looking code that refers to things which do not exist.

The awkward part is that a model is the worst available reviewer for this.
Asked to check its own output for hallucinated imports, it reads the import,
finds it plausible — it generated it precisely because it was plausible — and
passes. These failures are, however, almost all *decidable*: a package is
either in the manifest or it is not, a function either returns a real value or
a hardcoded one. So they belong in a deterministic probe, not in the
judgement pass.

## What is checked

1. IMPORTS OF PACKAGES NOTHING DECLARES. A third-party module imported by the
   code, absent from every dependency manifest in the repository, and not in
   the standard library. Usually a stale manifest; sometimes a package that
   does not exist at all, which is worse than a broken build because the name
   is now available for somebody else to register and publish. That attack has
   a name -- slopsquatting -- and the only defence is noticing before the
   install.

2. STUBS ON PRODUCTION PATHS. `return True  # TODO`, `raise NotImplementedError`,
   `pass` as a whole function body, a hardcoded return where a lookup belongs.
   Fine in a scaffold, quietly catastrophic when the function is named
   `verify_signature`.

3. SECURITY CHECKS TURNED OFF. `verify=False`, `rejectUnauthorized: false`,
   `InsecureSkipVerify: true`, `strict: False`, disabled host-key checking.
   Frequently generated to make an example work and then never removed.

4. PROSE THAT DESCRIBES ABSENT CODE. A docstring or comment promising
   validation, retry, caching or authentication in a body that does none of
   it. Reported narrowly and at low confidence, because a comment can be right
   about intent and wrong about the current state without being a defect.

## What this is not

Not a style checker, and not an attempt to detect whether a model wrote the
code. Provenance is unknowable from the text and does not matter: a hardcoded
`return True` in `verify_token` is the same defect whoever typed it. The
patterns are simply weighted toward the failures that became common when
drafting got cheap.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from .core import Finding, Location
from .probes import ProbeContext, _line_of, _read

# ---------------------------------------------------------------------------
# 1. Imports nothing declares
# ---------------------------------------------------------------------------
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w]*)|import\s+([A-Za-z_][\w]*))", re.M)

# Prose is full of sentences that begin "import the ..." and a line-anchored
# regex reads them as imports. The first run of this check reported a package
# called `the`, lifted out of a docstring in psf/requests. Strings and comments
# have to go before anything else is believed.
_PY_DOCSTRING = re.compile(r'("""|\'\'\')(?:.|\n)*?\1')
_PY_COMMENT = re.compile(r"#[^\n]*")


def _strip_prose(text: str) -> str:
    """Blank out docstrings and comments, preserving line numbering."""
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    return _PY_COMMENT.sub(blank, _PY_DOCSTRING.sub(blank, text))


# A module name is not a package name often enough that a table is unavoidable.
# Every entry here is a real import that a correct manifest declares under a
# different name, and without them the check reports well-maintained code.
_MODULE_TO_PACKAGE = {
    "openssl": "pyopenssl", "yaml": "pyyaml", "pil": "pillow", "cv2": "opencv-python",
    "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "jwt": "pyjwt",
    "sklearn": "scikit-learn", "serial": "pyserial", "usb": "pyusb",
    "docx": "python-docx", "pptx": "python-pptx", "fitz": "pymupdf",
    "magic": "python-magic", "dotenv": "python-dotenv", "attr": "attrs",
    "google": "google-api-python-client", "OpenGL": "pyopengl",
    "win32com": "pywin32", "zoneinfo": "backports-zoneinfo", "redis": "redis-py",
    "psycopg2": "psycopg2-binary", "MySQLdb": "mysqlclient", "Crypto": "pycryptodome",
    "jose": "python-jose", "multipart": "python-multipart", "pkg_resources": "setuptools",
    "setuptools": "setuptools", "grpc": "grpcio", "faker": "faker",
    "aws_cdk": "aws-cdk-lib",
}

# An import inside try/except is the conventional way to declare an optional
# dependency. Its absence from the manifest is the point, not a defect.
_OPTIONAL_IMPORT_BLOCK = re.compile(r"^[ \t]*try:[^\n]*\n(?:[ \t]+[^\n]*\n|\s*\n)*", re.M)

# Build scripts declare their own build-time requirements elsewhere (PEP 518),
# so an import here says nothing about the runtime manifest.
_BUILD_SCRIPT = re.compile(r"(^|/)(setup\.py|conftest\.py|noxfile\.py|tasks\.py)$")
_JS_IMPORT = re.compile(
    r"""^\s*import\s+(?:[^'"]*from\s+)?['"]([^'".][^'"]*)['"]"""
    r"""|\brequire\s*\(\s*['"]([^'".][^'"]*)['"]\s*\)""",
    re.M,
)

_MANIFESTS = {
    "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
    "setup.cfg", "Pipfile", "environment.yml", "constraints.txt",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
}

# Names that look like third-party imports and are not: the repository's own
# packages, and the conventional local-source roots.
_LOCAL_ROOTS = {"src", "lib", "app", "tests", "test", "internal", "pkg",
                "scripts", "tools", "utils", "common", "core", "api"}

# Where a missing dependency does not matter: nothing installs from here.
_NOT_SHIPPED = re.compile(r"(^|/)(tests?|docs?|examples?|samples?|benchmarks?|"
                          r"scripts?|migrations)(/|$)")

# ---------------------------------------------------------------------------
# 2. Stubs on production paths
# ---------------------------------------------------------------------------
_STUB_BODY = re.compile(
    r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)"
    r"(?:\s*->\s*[^:]+)?:\s*\n"
    r"(?P<body>(?:(?P=indent)[ \t]+.*\n|\s*\n){1,8})",
    re.M,
)
_STUB_MARKERS = re.compile(
    r"\braise\s+NotImplementedError\b"
    r"|^\s*pass\s*(?:#.*)?$"
    r"|^\s*\.\.\.\s*(?:#.*)?$"
    r"|\breturn\s+(?:True|False|None|\[\]|\{\}|0|\"\"|'')\s*#\s*(?:TODO|FIXME|stub|placeholder|for now|temporary)",
    re.M | re.I,
)
# Names where a stub is not a scaffold but a hole in something load-bearing.
_SENSITIVE_NAME = re.compile(
    r"(?i)(verify|validate|authenticate|authorize|check_?auth|is_?valid|is_?allowed|"
    r"has_?permission|decrypt|encrypt|sign|sanitiz|escape|audit|permit|access)"
)

# ---------------------------------------------------------------------------
# 3. Security checks turned off
# ---------------------------------------------------------------------------
_DISABLED_CHECK = [
    (re.compile(r"\bverify\s*=\s*False\b"), "TLS certificate verification disabled", "high"),
    (re.compile(r"\brejectUnauthorized\s*:\s*false\b"), "TLS certificate verification disabled", "high"),
    (re.compile(r"\bInsecureSkipVerify\s*:\s*true\b"), "TLS certificate verification disabled", "high"),
    (re.compile(r"\bNODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0"), "TLS verification disabled process-wide", "high"),
    (re.compile(r"\bssl\._create_unverified_context\b"), "Unverified TLS context", "high"),
    (re.compile(r"\bStrictHostKeyChecking[= ]+no\b"), "SSH host-key checking disabled", "high"),
    (re.compile(r"\bAutoAddPolicy\s*\(\s*\)"), "SSH host keys accepted automatically", "medium"),
    (re.compile(r"\bcheck_hostname\s*=\s*False\b"), "TLS hostname check disabled", "high"),
    (re.compile(r"\bcurl\b[^\n]*\s(?:-k|--insecure)\b"), "curl invoked without certificate checking", "medium"),
    (re.compile(r"\bwget\b[^\n]*--no-check-certificate\b"), "wget invoked without certificate checking", "medium"),
    (re.compile(r"\bCSRF_?(?:ENABLED|PROTECTION)\s*[:=]\s*(?:False|false|0)\b"), "CSRF protection disabled", "high"),
    (re.compile(r"\bDEBUG\s*=\s*True\b"), "Debug mode enabled", "medium"),
]
# A disabled check inside a test or a local-development fixture is usually
# deliberate. It is still reported, downgraded, exactly as fixture secrets are.
_DEV_CONTEXT = re.compile(r"(^|/)(tests?|testing|fixtures?|examples?|samples?|"
                          r"local|dev|development|docker-compose\.(?:dev|local))")

# Comments, in every syntax these patterns can appear in. Without this, code
# that has been commented OUT still reports, and so does a comment DISCUSSING
# the setting. Traefik's healthcheck has a whole commented-out TLS block; it
# was reported as a high-severity disabled check. This is the same mistake the
# import scanner made with docstrings, in a second place.
_COMMENT_SYNTAX = [
    (re.compile(r"/\*.*?\*/", re.S), None),      # C-family block
    (re.compile(r"(?m)^[ \t]*//[^\n]*"), None),  # C-family line, whole-line only
    (re.compile(r"(?m)//[^\n]*$"), "trailing"),  # C-family trailing
    (re.compile(r"(?m)^[ \t]*#[^\n]*"), None),   # shell/python/yaml line
    (re.compile(r"(?m)#[^\n]*$"), "trailing"),
    (re.compile(r"(?m)^[ \t]*--[^\n]*"), None),  # sql/lua
]


def _blank_comments(text: str, language: str) -> str:
    """Blank out comments, preserving line and column positions."""
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    hash_langs = {"python", "shell", "yaml", "ruby", "perl", "r", "toml",
                  "dockerfile", "makefile", "terraform", "hcl"}
    slash_langs = {"go", "javascript", "typescript", "java", "c", "cpp",
                   "csharp", "rust", "kotlin", "scala", "php", "json"}
    out = text
    if language in slash_langs or language == "unknown":
        out = _COMMENT_SYNTAX[0][0].sub(blank, out)
        out = _COMMENT_SYNTAX[1][0].sub(blank, out)
        out = _COMMENT_SYNTAX[2][0].sub(blank, out)
    if language in hash_langs or language == "unknown":
        out = _COMMENT_SYNTAX[3][0].sub(blank, out)
        out = _COMMENT_SYNTAX[4][0].sub(blank, out)
    if language == "python":
        # A docstring is prose, and prose that LISTS the dangerous settings is
        # exactly what a security module's own documentation looks like: this
        # file's docstring names `verify=False` and reported itself as three
        # high findings. The import scanner already blanks docstrings for the
        # same reason (`_strip_prose`); this is the same fix in the second
        # place it was needed.
        out = _PY_DOCSTRING.sub(blank, out)
    return out


# An insecure mode the operator has to ask for is a feature, not a defect. The
# shape is always the same: a conditional naming the opt-in, and the unsafe
# setting inside it.
#
# HOLDOUT NOTE, recorded rather than quietly skipped: this pattern was observed
# only on argo-cd, which is a HELD-OUT repository. No repository in the tuning
# set contains it. The fix is principled rather than repo-specific -- a
# flag-guarded insecure mode is opt-in by construction -- but the held-out
# status of `authored.security-check-disabled` is spent, and its held-out
# numbers must not be quoted as independent evidence until a new repository
# takes argo-cd's place for this rule.
_OPT_IN_GUARD = re.compile(
    r"(?im)^[ \t]*(?:if|elif|unless|when)\b[^\n]{0,120}?"
    r"\b(insecure|skip_?verify|skip_?tls|no_?verify|allow_?insecure|"
    r"disable_?tls|dev_?mode|development|debug|self_?signed|trust_?all)\b"
)
_GUARD_WINDOW = 6   # lines above the finding to look for the guard

# A compensating control. Disabling the library's hostname check while pinning
# a specific CA or presenting a client certificate is a deliberate mutual-TLS
# arrangement, not an absence of verification -- Traefik's Consul Connect
# transport is exactly this, and says so in a comment. The resource rules
# already honour compensating controls through `unless_truthy`; this is the
# same idea for code.
_COMPENSATING_TLS = re.compile(
    r"(?i)\b(RootCAs|ca_certs?|cafile|ca_bundle|CABundle|ServerName|"
    r"Certificates\s*:|ClientCert|cert_?file|VerifyPeerCertificate|"
    r"VerifyConnection|pinned?_?(?:cert|key))\b"
)
_COMPENSATION_WINDOW = 8   # lines after the finding

# ---------------------------------------------------------------------------
# 4. Prose describing absent code
# ---------------------------------------------------------------------------
_PROMISE = re.compile(
    r"(?i)\b(validates?|verifies|sanitiz\w+|authenticates?|authorizes?|"
    r"retries|retry|caches?|rate[- ]?limits?|encrypts?|hashes)\b"
)
_PROMISE_EVIDENCE = {
    "validat": re.compile(r"(?i)\b(if|assert|raise|throw|match|schema|valid)"),
    "verif": re.compile(r"(?i)\b(if|assert|raise|throw|compare|hmac|signature|==)"),
    "sanitiz": re.compile(r"(?i)\b(replace|escape|strip|re\.|regex|encode|quote)"),
    "authenticat": re.compile(r"(?i)\b(token|session|password|credential|login|jwt|oauth)"),
    "authoriz": re.compile(r"(?i)\b(role|permission|scope|policy|acl|allow|deny)"),
    "retr": re.compile(r"(?i)\b(for|while|range|backoff|sleep|attempt|retry)"),
    "cach": re.compile(r"(?i)\b(cache|memo|store|get|set|ttl|lru)"),
    "rate": re.compile(r"(?i)\b(limit|token|bucket|sleep|window|throttle)"),
    "encrypt": re.compile(r"(?i)\b(cipher|aes|key|encrypt|fernet|nacl|crypto)"),
    "hash": re.compile(r"(?i)\b(hash|digest|sha|bcrypt|argon|scrypt|md5)"),
}


def _declared_dependencies(ctx: ProbeContext, repo_id: str) -> tuple[set[str], bool]:
    """Every package name any manifest in this repository declares."""
    names: set[str] = set()
    seen_manifest = False
    for f in ctx.inventory.text_files():
        if f.repo_id != repo_id or Path(f.path).name not in _MANIFESTS:
            continue
        text = _read(f)
        if not text:
            continue
        seen_manifest = True
        for m in re.finditer(r"^\s*\"?([A-Za-z0-9_.@/-]{2,})\"?\s*[:=<>~^\s\"]", text, re.M):
            raw = m.group(1).strip().strip('"')
            names.add(raw.lower().replace("_", "-"))
            names.add(raw.lower().split("/")[-1].replace("_", "-"))
        for m in re.finditer(r"^\s*([A-Za-z0-9_.-]{2,})\s*(?:==|>=|<=|~=|$)", text, re.M):
            names.add(m.group(1).lower().replace("_", "-"))
        # A requirement quoted inside a list on one line, as a PEP 621 extra
        # often is (`ast = ["tree-sitter>=0.23", ...]`): the two patterns above
        # read only the start of a line, so every name past the first was
        # undeclared (FAIL-049).
        for m in re.finditer(r"[\"']([A-Za-z0-9_.-]{2,})(?:\[[^\]]*\])?\s*(?:[<>=~!;]|[\"'])", text):
            names.add(m.group(1).lower().replace("_", "-"))
    return names, seen_manifest


def _workspace_packages(ctx: ProbeContext, repo_id: str) -> set[str]:
    """Names a package.json in this repository claims for ITSELF.

    In a monorepo, `@juice-shop/models` is imported by three files and appears
    in no dependency list, because it is a sibling package rather than a
    dependency. Without this, every workspace import is a finding.
    """
    import json as _json
    out: set[str] = set()
    for f in ctx.inventory.text_files():
        if f.repo_id != repo_id or Path(f.path).name != "package.json":
            continue
        try:
            doc = _json.loads(_read(f) or "{}")
        except ValueError:
            continue
        if isinstance(doc.get("name"), str):
            out.add(doc["name"].lower())
        # A workspaces declaration means sibling packages exist even where
        # their own package.json was not inventoried.
        ws = doc.get("workspaces")
        entries = ws.get("packages", []) if isinstance(ws, dict) else (ws or [])
        for pattern in entries if isinstance(entries, list) else []:
            head = str(pattern).split("/")[0].strip("@")
            if head and "*" not in head:
                out.add(head.lower())
                out.add(f"@{head.lower()}")
    # An organisation scope used by any local package is local for all of them,
    # in both the bare and the @-scoped spelling: a package.json named
    # "juice-shop" is the sibling that `@juice-shop/models` refers to.
    out |= {n.split("/")[0] for n in list(out) if n.startswith("@")}
    out |= {f"@{n}" for n in list(out) if not n.startswith("@")}
    return out


def _local_modules(ctx: ProbeContext, repo_id: str) -> set[str]:
    """Top-level names that resolve to something inside this repository."""
    out: set[str] = set(_LOCAL_ROOTS)
    # Which names resolve locally is a question about every path in the
    # repository, so a narrowed inventory answers it from the whole one.
    whole = getattr(ctx.inventory, "whole", None) or ctx.inventory
    for f in whole.text_files():
        if f.repo_id != repo_id:
            continue
        parts = f.path.split("/")
        out.add(parts[0].lower())
        if len(parts) > 1:
            out.add(parts[1].lower())
        stem = Path(f.path).stem.lower()
        out.add(stem)
        if Path(f.path).name == "__init__.py" and len(parts) > 1:
            out.add(parts[-2].lower())
    return out


def probe_authored(ctx: ProbeContext) -> list[Finding]:
    # Registry lookups happen only where the profile allows network access.
    # The probe itself is always applicable; what changes is how much it can
    # prove, and the finding says which.
    from .policy import PROFILES
    profile = ctx.config.get("profile") or "offline"
    online = bool(PROFILES.get(profile, PROFILES["offline"])["network"])
    out: list[Finding] = []
    for repo in ctx.repos:
        declared, has_manifest = _declared_dependencies(ctx, repo.id)
        local = _local_modules(ctx, repo.id)
        workspace = _workspace_packages(ctx, repo.id)
        for f in ctx.inventory.text_files():
            if f.repo_id != repo.id or f.role in ("generated", "data", "docs"):
                continue
            text = _read(f)
            if not text:
                continue
            out.extend(_undeclared_imports(f, text, declared, local, has_manifest,
                                           workspace, online))
            out.extend(_stubs(f, text))
            out.extend(_disabled_checks(f, text))
            out.extend(_unkept_promises(f, text))
    return out


def _undeclared_imports(f, text, declared, local, has_manifest,
                        workspace=frozenset(), online=False) -> list[Finding]:
    # With no manifest at all there is nothing to be missing from, and every
    # import would be reported. That is a repository shape, not a defect.
    if not has_manifest or _NOT_SHIPPED.search(f.path):
        return []
    out: list[Finding] = []
    if f.language == "python":
        if _BUILD_SCRIPT.search(f.path):
            return []
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        clean = _strip_prose(text)
        # Spans covered by a try: block, where an absent package is deliberate.
        optional = [(m.start(), m.end()) for m in _OPTIONAL_IMPORT_BLOCK.finditer(clean)]
        for m in _PY_IMPORT.finditer(clean):
            name = m.group(1) or m.group(2)
            if not name or name.startswith("_"):
                continue
            if any(a <= m.start() < b for a, b in optional):
                continue
            low = name.lower().replace("_", "-")
            alias = _MODULE_TO_PACKAGE.get(name.lower(), _MODULE_TO_PACKAGE.get(name))
            if (low in declared or name in stdlib or low in local
                    or name.lower() in local or (alias and alias in declared)
                    or (alias and alias not in declared and alias == low)):
                continue
            # An aliased module whose package is simply missing is still worth
            # reporting, but the alias means the name in the manifest differs.
            if alias and alias in declared:
                continue
            out.append(_import_finding(f, text, name, "python",
                                       package_exists("python", name) if online else None))
    elif f.language in ("javascript", "typescript"):
        names = {(m.group(1) or m.group(2)) for m in _JS_IMPORT.finditer(text)}
        for spec in sorted(n for n in names if n):
            if spec.startswith((".", "/", "#", "node:")):
                continue
            pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
            low = pkg.lower()
            if low in declared or low.split("/")[-1] in declared or low in local:
                continue
            # A monorepo's own workspace packages are declared by the
            # package.json that NAMES them, not by one that depends on them.
            # `@juice-shop/models` is a sibling of the package named
            # "juice-shop", so the SCOPE is what identifies it as local.
            scope = low.split("/")[0] if low.startswith("@") else ""
            if low in workspace or (scope and scope in workspace):
                continue
            out.append(_import_finding(f, text, pkg, "node",
                                       package_exists("node", pkg) if online else None))
    return out


def _import_finding(f, text, name, ecosystem, exists: bool | None = None) -> Finding:
    """One finding, two very different claims depending on what was verifiable.

    Offline, all this check knows is that a manifest does not declare a module
    the code imports. That is real -- it is a phantom or transitive dependency,
    and the build is not reproducible -- but it is mostly a stale manifest, and
    grading it as a security finding would be claiming more than was checked.
    So offline it reports as `info`: on the record, zero weight.

    Connected, the registry answers the question that actually matters: does
    this package exist at all? A name that resolves to nothing is a different
    class of problem. The build cannot work, so the name was never installed,
    so it was almost certainly never real -- and an unclaimed package name sitting
    in a shipped import is an open invitation to whoever registers it first.
    That is critical, and it is a finding no other tool in this pipeline makes.
    """
    m = re.search(rf"\b{re.escape(name)}\b", text)
    loc = Location(path=f.path, start_line=_line_of(text, m.start()) if m else 1,
                   repo_id=f.repo_id)
    if exists is False:
        return Finding(
            rule_id="arbiter/authored.import-of-nonexistent-package",
            title=f"`{name}` is imported and does not exist on the registry",
            dimension="supply_chain", severity="critical", confidence="high",
            repo_id=f.repo_id, probe="authored", location=loc,
            description=(
                f"The code imports `{name}`, nothing declares it, and the "
                f"{ecosystem} registry has no package by that name. The import "
                "cannot ever have worked, so the name was invented — by a person "
                "misremembering, or by a model generating something plausible. "
                "The risk is not the broken build. It is that the name is "
                "unclaimed: anyone may register it and publish whatever they like, "
                "and the next person who fixes the build by installing it gets that."
            ),
            remediation=(
                f"Remove the import. Do not register `{name}` yourself as a "
                "placeholder — check whether anything has been published under it "
                "since this code was written."
            ),
            evidence=f"nonexistent:{ecosystem}:{name}",
            controls=["NIST-800-218:PW.4", "NIST-800-53r5:SR-3", "NIST-800-53r5:SR-11"],
            tags=["supply-chain", "dependency", "hallucinated", ecosystem],
        )
    verified = " The registry has a package by that name, so this is a manifest gap." \
        if exists else ""
    return Finding(
        rule_id="arbiter/authored.undeclared-import",
        title=f"`{name}` is imported but declared in no manifest",
        dimension="supply_chain",
        # Informational offline, because offline this cannot tell a stale
        # manifest from an invented package, and only one of those is serious.
        # Saying "medium" would be claiming a distinction that was not checked.
        severity="low" if exists else "info",
        confidence="medium" if exists else "low",
        repo_id=f.repo_id, probe="authored", location=loc,
        description=(
            f"The code imports `{name}`, no dependency manifest in this repository "
            "declares it, and it is not in the standard library. Most often the "
            "manifest is stale, or the package arrives transitively through "
            "something else — which still means the build is not reproducible, "
            "because the version that gets installed is nobody's decision." + verified
            + " Run with a connected profile to check whether the package exists at "
              "all; that is the distinction that matters and it needs the registry."
        ),
        remediation=f"Declare `{name}` explicitly, or remove the import.",
        evidence=f"undeclared:{ecosystem}:{name}",
        controls=["NIST-800-218:PW.4", "NIST-800-53r5:CM-2"],
        tags=["supply-chain", "dependency", ecosystem],
    )


# ---------------------------------------------------------------------------
# Registry existence, connected profile only
# ---------------------------------------------------------------------------

_REGISTRY = {
    "python": "https://pypi.org/pypi/{name}/json",
    "node": "https://registry.npmjs.org/{name}",
}
_EXISTENCE_CACHE: dict[tuple[str, str], bool | None] = {}


def package_exists(ecosystem: str, name: str, timeout: int = 8) -> bool | None:
    """True, False, or None when the registry could not be reached.

    None is load-bearing: a network failure must not read as "does not exist".
    """
    key = (ecosystem, name.lower())
    if key in _EXISTENCE_CACHE:
        return _EXISTENCE_CACHE[key]
    url = _REGISTRY.get(ecosystem)
    if not url:
        return None
    import urllib.error
    import urllib.request
    result: bool | None
    try:
        req = urllib.request.Request(
            url.format(name=urllib.request.quote(name, safe="@/")),
            headers={"User-Agent": "arbiter"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = 200 <= r.status < 300
    except urllib.error.HTTPError as e:
        # 404 is the answer. Anything else is the registry having a bad day,
        # and must not be read as absence.
        result = False if e.code == 404 else None
    except Exception:  # noqa: BLE001 - any other failure is "unknown", never a finding
        result = None
    _EXISTENCE_CACHE[key] = result
    return result



def _stub_marker_at_body_level(body: str) -> bool:
    """True when a stub marker is a statement of the function body itself.

    The markers used to be searched anywhere in the first eight lines, so a
    real function whose early lines held `except OSError:` followed by `pass`
    was reported as a stub (FAIL-042: `store_mcp_probe_memo` in
    OmniEngineering's `make_ai.py`, which writes a file and swallows a failed
    write on purpose). A `pass` under an `except`, an `if` or a `with` is a
    branch, not a body: only a marker at the body's own indentation, the
    indentation of its first statement after the docstring, says the function
    does nothing.
    """
    base: int | None = None
    quote: str | None = None
    for line in body.split("\n"):
        stripped = line.strip()
        if quote is not None:
            if quote in stripped:
                quote = None
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if base is None and stripped[:3] in ('"""', "'''"):
            if not (len(stripped) >= 6 and stripped.endswith(stripped[:3])):
                quote = stripped[:3]
            continue
        indent = len(line) - len(line.lstrip())
        if base is None:
            base = indent
        if indent == base and _STUB_MARKERS.search(line):
            return True
    return False

def _stubs(f, text) -> list[Finding]:
    if f.language != "python" or f.role in ("test", "docs"):
        return []
    out: list[Finding] = []
    for m in _STUB_BODY.finditer(text):
        name, body = m.group("name"), m.group("body")
        if not _stub_marker_at_body_level(body):
            continue
        sensitive = bool(_SENSITIVE_NAME.search(name))
        out.append(Finding(
            rule_id="arbiter/authored.stub-on-production-path",
            title=(f"`{name}` is a stub"
                   + (" on a security-relevant path" if sensitive else "")),
            dimension="security" if sensitive else "quality",
            severity="high" if sensitive else "low",
            confidence="high" if sensitive else "medium",
            repo_id=f.repo_id, probe="authored",
            location=Location(path=f.path, start_line=_line_of(text, m.start()),
                              repo_id=f.repo_id),
            description=(
                "The body does no work — it passes, raises NotImplementedError, or "
                "returns a constant marked temporary."
                + (" The name says this function decides whether something is allowed "
                   "or genuine, so a stub here is not an unfinished feature but an "
                   "always-yes." if sensitive else
                   " Fine in a scaffold; worth knowing about if it shipped.")
            ),
            remediation="Implement it, or make the caller fail loudly instead.",
            evidence=f"stub:{name}:{body.strip()[:60]}",
            controls=["NIST-800-218:PW.5"] + (["NIST-800-53r5:AC-3"] if sensitive else []),
            tags=["stub"] + (["security-relevant"] if sensitive else []),
        ))
    return out


def _disabled_checks(f, text) -> list[Finding]:
    if f.role in ("docs",):
        return []
    dev = bool(_DEV_CONTEXT.search(f.path)) or f.role == "test"
    code = _blank_comments(text, getattr(f, "language", "unknown"))
    lines = code.split("\n")
    out: list[Finding] = []
    for pattern, title, sev in _DISABLED_CHECK:
        for m in pattern.finditer(code):
            n = _line_of(code, m.start())
            window = "\n".join(lines[max(0, n - 1 - _GUARD_WINDOW): n])
            guarded = bool(_OPT_IN_GUARD.search(window))
            after = "\n".join(lines[n: n + _COMPENSATION_WINDOW])
            compensated = bool(_COMPENSATING_TLS.search(after)) and "TLS" in title
            out.append(Finding(
                rule_id="arbiter/authored.security-check-disabled",
                title=title + (" (test or local-development path)" if dev else
                               " (behind an opt-in flag)" if guarded else
                               " (a pinned CA or client certificate is configured)"
                               if compensated else ""),
                dimension="security",
                severity=("low" if (dev or guarded) else
                          "medium" if compensated else sev),
                confidence=("low" if dev else "medium" if (guarded or compensated)
                            else "high"),
                repo_id=f.repo_id, probe="authored",
                location=Location(path=f.path, start_line=n, repo_id=f.repo_id),
                description=(
                    "A protection that is on by default has been explicitly switched "
                    "off. This is the commonest way a snippet written to make an "
                    "example work reaches production: it is a single token, it fixes "
                    "the error in front of you, and nothing complains afterwards."
                    + (" This one sits under a test or local-development path, so it "
                       "is probably deliberate." if dev else "")
                    + (" It sits inside a conditional that names the opt-in, so it is "
                       "an insecure mode somebody has to ask for rather than a "
                       "default. Still worth knowing the mode exists." if guarded
                       else "")
                    + (" A pinned CA or client certificate is configured alongside "
                       "it, which is what a deliberate mutual-TLS arrangement looks "
                       "like: verification is being done differently rather than not "
                       "at all. Confirm the pinning is what you think it is."
                       if compensated else "")
                ),
                remediation="Remove it, or scope it to local development explicitly.",
                evidence=f"disabled:{m.group(0)[:70]}",
                controls=["NIST-800-53r5:SC-8", "NIST-800-53r5:SC-23"],
                tags=(["disabled-check"] + (["dev-context"] if dev else [])
                      + (["opt-in-guarded"] if guarded else [])
                      + (["compensated"] if compensated else [])),
            ))
    return out


def _unkept_promises(f, text) -> list[Finding]:
    """A docstring promising work the body does not do.

    Reported narrowly and at low confidence on purpose. A comment can be right
    about intent and out of date about the present without that being a defect,
    so this only fires when the body is short enough to read in full and
    contains nothing resembling the promised behaviour.
    """
    if f.language != "python" or f.role in ("test", "docs"):
        return []
    out: list[Finding] = []
    for m in re.finditer(
        r'^(?P<indent>[ \t]*)def\s+(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)(?:\s*->[^:]+)?:\s*\n'
        r'(?P=indent)[ \t]+(?P<q>"""|\'\'\')(?P<doc>.*?)(?P=q)\s*\n'
        r'(?P<body>(?:(?P=indent)[ \t]+.*\n|\s*\n){0,12})',
        text, re.M | re.S,
    ):
        doc, body, name = m.group("doc"), m.group("body"), m.group("name")
        if len(body.strip().split("\n")) > 10:
            continue
        for pm in _PROMISE.finditer(doc):
            word = pm.group(1).lower()
            key = next((k for k in _PROMISE_EVIDENCE if word.startswith(k)), None)
            if key is None or _PROMISE_EVIDENCE[key].search(body):
                continue
            out.append(Finding(
                rule_id="arbiter/authored.docstring-promises-absent-behaviour",
                title=f"`{name}` documents {word} that the body does not do",
                dimension="drift", severity="low", confidence="low",
                repo_id=f.repo_id, probe="authored",
                location=Location(path=f.path, start_line=_line_of(text, m.start()),
                                  repo_id=f.repo_id),
                description=(
                    f"The docstring says this function {word}, and the body contains "
                    "nothing that resembles doing so. Callers read the docstring. "
                    "Reported at low confidence because a comment describing intent "
                    "rather than current behaviour is common and not always wrong."
                ),
                remediation="Implement it, or correct the docstring.",
                evidence=f"promise:{name}:{word}",
                controls=["NIST-800-218:PW.7"],
                tags=["drift", "docstring"],
            ))
            break
    return out


def register_authored() -> None:
    from .cache import make_context_digest
    from .probes import Probe, register
    register(Probe(
        name="authored",
        # File-scoped only because a partial inventory always retains every
        # manifest and workspace file (see inventory.CONTEXT_ALWAYS). Without
        # that guarantee this probe would invent undeclared imports.
        scope="file",
        dimensions=["supply_chain", "security", "quality", "drift"],
        checks=4,
        run=probe_authored,
        # Cacheable with a declared context: whether an import is declared is
        # decided by the manifests, and whether a name is local by which
        # paths exist, so both are folded into the cache key. An unchanged
        # file is served from the cache only while neither has moved.
        cacheable=True,
        cache_context=make_context_digest(_MANIFESTS),
    ))
