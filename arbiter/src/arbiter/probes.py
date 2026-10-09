"""Native probes.

Every probe here runs with nothing installed but Python. That matters for two
reasons: the offline profile has to work in an air-gapped environment where
you cannot pip-install a scanner, and the A/B harness needs a baseline arm
that is always available to compare an external tool against.

A probe declares what it needs. The planner decides whether it can run, and a
probe that cannot run is recorded as `skipped` with a reason — never as a
silent pass.
"""
from __future__ import annotations

import codecs
import fnmatch
import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core import Finding, Location
from .graph import Resource

PACKS = Path(__file__).parent / "packs"


@dataclass
class ProbeContext:
    repos: list = field(default_factory=list)
    inventory: Any = None
    graph: list[Resource] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    system: dict = field(default_factory=dict)
    # Repo id -> relative posix paths changed since the base of a `--changed`
    # scan. Empty in a full scan, which is how a change-scoped probe tells
    # "nothing changed" from "nobody asked about a change" (REQ-038).
    changed: dict[str, set[str]] = field(default_factory=dict)
    changed_since: str | None = None
    # Where this run writes its report (`--out`). A document that names a
    # file under it is describing output, not a file the repository is
    # missing (ARB-048). Empty when the caller gave no output directory.
    out_dir: str = ""

    def repo_ids(self) -> list[str]:
        return [r.id for r in self.repos]


@dataclass
class Probe:
    name: str
    dimensions: list[str]
    checks: int
    run: Callable[[ProbeContext], list[Finding]]
    stacks: list[str] | None = None       # None = always applicable
    binaries: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    network: bool = False
    model: bool = False
    multi_repo_only: bool = False
    # Can this probe answer honestly from a SUBSET of the files?
    #
    # "file" means every finding depends only on the file it is in, so running
    # over the changed files gives a complete answer FOR THOSE FILES.
    # "repo" means the answer depends on relationships between files -- which
    # routes exist anywhere, what every manifest declares, how many
    # suppressions the repository contains -- so a subset gives a wrong answer
    # rather than a partial one. Those are reported as not assessed in an
    # incremental scan, never as a pass.
    # "change" means the probe reads a small, fixed set of files it names
    # itself (a ledger, a registry) plus the set of changed paths, so a
    # partial scan is its natural habitat and a full scan is answered with the
    # empty change. It runs in both modes.
    #
    # Conservative by default: a probe is "repo" unless it is known not to be.
    scope: str = "repo"
    # Why this probe is repo-scoped, in the words a report will print. The
    # default is true of the native repo probes and false of an external
    # analyzer, which is held back because nobody has measured it rather than
    # because it reasons across files -- so an adapter supplies its own.
    scope_reason: str = "this check reads relationships between files"
    version: str = "0.1.0"
    # May this probe's per-file results be stored in the persistent result
    # cache (cache.py) and served on the next scan without running it?
    #
    # Only when every finding it produces carries a path AND depends on that
    # file alone: the same file, under the same configuration, must yield the
    # same findings whether or not any other file is in the inventory. That
    # is stricter than scope="file". `house_rules` is file-scoped and NOT
    # cacheable: a file_exists or file_absent rule reports on the repository,
    # with no path to attribute the result to. A probe whose answer for a
    # file depends on something outside it -- the manifests, for a probe that
    # checks whether an import is declared -- may still be cacheable if it
    # declares that dependency as `cache_context`, a function of the probe
    # context whose digest is folded into the cache key.
    #
    # The declaration is a claim, and tools/integrity.py (CI-13) tests it:
    # every cacheable probe is run on a file alone and beside another file,
    # and must give the same answer for it.
    cacheable: bool = False
    cache_context: Callable[["ProbeContext"], str] | None = None
    # Registered from an adapter manifest (adapters.py), so `run` starts an
    # external process and waits on it rather than reading files in this
    # one. The engine runs these in a thread pool, where a native probe --
    # which shares the read cache and the probe context -- would race, and
    # memoises each one whole in the result cache (ARB-047).
    external: bool = False


    # A probe's own answer to "is there anything here for me to check": a
    # ledger to read, a registry to compare against. Consulted after the
    # stack and multi-repo checks. Saying no records the probe as not
    # applicable, which leaves the coverage denominator; it must never be
    # used for a dependency this machine lacks (that is `prevented`).
    applies: Callable[[ProbeContext], tuple[bool, str]] | None = None

    def prevented(self) -> tuple[bool, str]:
        """A dependency this machine lacks. Different fact from `applicable`:
        the probe had a question to answer here and could not. It stays in
        the coverage denominator, like a missing binary. The Windows job
        found the two conflated: tree-sitter absent read as "not applicable"
        and the AST probes left coverage instead of lowering it."""
        for mod in self.modules:
            import importlib.util
            if importlib.util.find_spec(mod) is None:
                return True, f"missing python package: {mod}"
        return False, ""

    def applicable(self, ctx: ProbeContext) -> tuple[bool, str]:
        if self.multi_repo_only and len(ctx.repos) < 2:
            return False, "system has a single repo; no seams to check"
        if self.stacks is not None:
            present = set(getattr(ctx.inventory, "stacks", set()) or set())
            if not (set(self.stacks) & present):
                return False, f"no {'/'.join(self.stacks)} detected in target"
        if self.applies is not None:
            ok, why = self.applies(ctx)
            if not ok:
                return False, why
        return True, ""


REGISTRY: list[Probe] = []


def register(p: Probe) -> Probe:
    REGISTRY.append(p)
    return p


# Every probe reads every file it cares about, and there are now a dozen
# probes. On a 450,000-line repository that meant the same bytes coming off
# disk and through the UTF-8 decoder twenty-odd times. The inventory is fixed
# for the duration of a scan and files are capped at 2 MB, so caching by
# absolute path is safe and bounded.
#
# The key includes the file's modification time and size, not just its path.
#
# The first version keyed on path alone and relied on every caller clearing the
# cache between scans. That lasted about an hour. The injection harness does
# not call run_scan -- it invokes probes directly and writes every one of
# twenty thousand generated cases to the SAME path -- so case two was served
# case one's bytes, and recall on four rules fell from 1.0000 to 0.0000 while
# the harness reported the numbers with a straight face. It bought a 20%
# speedup and would have quietly invalidated every piece of training evidence.
#
# A cache that cannot be wrong is worth more than one that is faster and
# depends on callers remembering something. stat() is cheap next to reading and
# decoding the file, and no caller has to know this exists.
_READ_CACHE: dict[tuple[str, int, int], str] = {}
_READ_CACHE_MAX = 20_000

# Byte-order marks, longest first: the UTF-32 marks start with the UTF-16-LE
# mark, so testing short-first would decode UTF-32-LE as UTF-16-LE.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def _decode(raw: bytes) -> str:
    """Decode by declared byte-order mark, falling back to UTF-8.

    Decoding everything as UTF-8 does not fail loudly on a UTF-16 file -- the
    NUL between every character is a valid code point, so `errors="replace"`
    yields `a\\x00w\\x00s\\x00...`, no pattern matches it, and the file is
    reported clean rather than unread. A credential in a file saved as
    "Unicode" by a Windows editor was invisible, which is the worst shape a
    miss can take: a confident pass on a file that was never actually read.

    Only a declared BOM is honoured. Guessing at headerless UTF-16 means
    guessing about every file, and a wrong guess turns readable source into
    mojibake; a file that declares nothing is still read as UTF-8.

    Line endings are normalized because reading bytes does not do it and
    `read_text` did: a pattern anchored at end of line sees a trailing `\\r`
    on every CRLF file otherwise, which silenced six secret formats on
    Windows the moment this function replaced `read_text`.
    """
    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            text = raw.decode(encoding, errors="replace")
            break
    else:
        text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read(f) -> str:
    path = getattr(f, "abspath", "") or ""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return ""
    cached = _READ_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        text = _decode(Path(path).read_bytes())
    except OSError:
        text = ""
    if len(_READ_CACHE) >= _READ_CACHE_MAX:
        _READ_CACHE.clear()
    _READ_CACHE[key] = text
    return text


def clear_read_cache() -> None:
    _READ_CACHE.clear()


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _mask(value: str) -> str:
    """Never put a live secret in a report."""
    v = value.strip().strip("\"'")
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * (len(v) - 8) + v[-4:]


# ===========================================================================
# secrets
# ===========================================================================

SECRET_PATTERNS: list[tuple[str, str, str, str]] = [
    # (rule suffix, title, severity, regex)
    ("aws-access-key", "AWS access key ID committed", "critical", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("private-key", "Private key material committed", "critical",
     r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----"),
    ("gh-token", "GitHub token committed", "critical", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("slack-token", "Slack token committed", "high", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    ("jwt", "Hardcoded JWT", "medium", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b"),
    # Provider-issued tokens. These are the highest-confidence secrets there
    # are: the prefix is assigned by the issuer, not chosen by the developer,
    # so a match is a token from that provider or a deliberate imitation of
    # one. They were missing until a pull-request rehearsal planted a live
    # Stripe key in a billing module and the scan came back clean -- the
    # symbol was STRIPE_KEY, and the assigned-credential heuristic does not
    # treat a bare "key" as credential-ish because sort_key and cache_key are
    # everywhere. The name was never the evidence here; the value is.
    ("stripe-key", "Stripe live secret key committed", "critical",
     r"\b[sr]k_live_[0-9A-Za-z]{20,}\b"),
    ("openai-key", "OpenAI API key committed", "critical",
     r"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])"),
    # These end with an explicit lookahead rather than \b: the charset is
    # base64url, and a token ending in "-" has no word boundary after it, so
    # \b silently drops it. Injection put google-api-key at 0.9091 recall
    # until that showed up in the missed-defect list.
    ("anthropic-key", "Anthropic API key committed", "critical",
     r"(?<![A-Za-z0-9_-])sk-ant-(?:api|sid)[0-9]{2}-[A-Za-z0-9_-]{24,}(?![A-Za-z0-9_-])"),
    ("google-api-key", "Google API key committed", "critical",
     r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])"),
    ("gitlab-token", "GitLab token committed", "critical",
     r"(?<![A-Za-z0-9_-])glpat-[0-9A-Za-z_-]{20,}(?![A-Za-z0-9_-])"),
    ("npm-token", "npm access token committed", "critical",
     r"\bnpm_[0-9A-Za-z]{36}\b"),
    ("sendgrid-key", "SendGrid API key committed", "critical",
     r"(?<![A-Za-z0-9_-])SG\.[0-9A-Za-z_-]{16,}\.[0-9A-Za-z_-]{16,}(?![A-Za-z0-9_-])"),
    ("pypi-token", "PyPI upload token committed", "critical",
     r"(?<![A-Za-z0-9_-])pypi-AgEIcHlwaS5vcmc[0-9A-Za-z_-]{50,}(?![A-Za-z0-9_-])"),
    ("slack-webhook", "Slack incoming webhook URL committed", "high",
     r"https://hooks\.slack\.com/services/T[0-9A-Za-z_-]{6,}/B[0-9A-Za-z_-]{6,}/[0-9A-Za-z]{16,}"),
    ("pg-url", "Database URL with inline credentials", "high",
     r"\b(?:postgres(?:ql)?|mysql|mongodb)://[^\s:@/]+:[^\s@/]+@[^\s/]+"),
]

# The symbol name is matched with surrounding word characters allowed, because
# real code writes DB_PASSWORD and apiKeySecret, not a bare `password`.
_ASSIGNED_SECRET = re.compile(
    r"""(?ix)
    ([A-Za-z0-9_]{0,24}
      (?:password|passwd|pwd|secret|token|api[._-]?key|access[._-]?key|
         client[._-]?secret|private[._-]?key|credential)
     [A-Za-z0-9_]{0,24})
    # `:=` must come before the single-character alternatives, or Go's short
    # variable declaration matches the colon and then fails on the equals.
    # Injection testing put this rule's recall at 0.48 until it was fixed.
    \s*(?::=|::|=|:)\s*
    # Backticks count: a JavaScript template literal is an ordinary string, and
    # a secret written in one was invisible until injection testing found it.
    (["'`])([^"'`\n]{8,120})\2
    """
)

# Unquoted values. A .env file, a Kubernetes Secret, a docker-compose file, a
# `export` line and a Dockerfile `ENV` all write secrets without quotes, and
# those are the places secrets most commonly leak. The quoted pattern above saw
# none of them.
#
# Unquoted is riskier, so the value is constrained hard: it must run to the end
# of the line (a trailing comment aside), contain no whitespace and no quote
# characters. That kills nearly all YAML-structure matches before the shared
# filters even run.
_ASSIGNED_SECRET_BARE = re.compile(
    r"""(?ix)
    (?:^|\n)[ \t]*
    (?:export[ \t]+|env[ \t]+|set[ \t]+|-[ \t]+)?
    ([A-Za-z0-9_.-]{0,24}
      (?:password|passwd|pwd|secret|token|api[._-]?key|access[._-]?key|
         client[._-]?secret|private[._-]?key|credential)
     [A-Za-z0-9_.-]{0,24})
    [ \t]*[:=][ \t]*
    ([^\s"'`\#\n]{8,120})
    [ \t]*(?:\#[^\n]*)?(?=\n|$)
    """
)

# A symbol whose name ends this way holds a reference to a secret, not a
# secret: `secretName`, `access_key_status`, `tokenUrl`, `secretKeyRef`.
_NON_SECRET_SUFFIX = re.compile(
    r"(_(?:status|state|id|name|arn|path|file|url|uri|type|enabled|required|"
    r"expiry|expires|rotation|algorithm|version|ref|class|provider|manager|store|hash|selector|policy|config|template|format|scope|prefix|suffix)"
    r"|(?:Status|State|Id|Name|Arn|Path|File|Url|Uri|Type|Enabled|Required|"
    r"Expiry|Expires|Rotation|Algorithm|Version|Ref|Class|Provider|Manager|Store|Hash|Selector|Policy|Config|Template|Format|Scope|Prefix|Suffix))$"
)

_PLACEHOLDER = re.compile(
    r"(?i)^(|x{3,}|\*{3,}|\.{3,}|changeme|todo|none|null|test|dummy|"
    # `your-api-key-here` used to slip through because \w does not match a
    # hyphen, so the anchored alternative never completed. Injection controls
    # put this rule's specificity at 0.975 until the class was widened.
    r"your[-_\w]*|my[-_](?:secret|token|key|password)[-_\w]*|"
    r"[-_\w]*(?:here|placeholder|example|sample|redacted|omitted|fake|notreal)[-_\w]*|"
    r"\$\{\{?[^}]*\}\}?|\{\{[^}]*\}\}|<[^>]*>|process\.env\.[\w.]+|os\.environ.*|"
    r"\$\([^)]*\)|%\([^)]*\)s|@[\w.]+@|\{[a-z_]+\}|"
    r"\$[A-Za-z_][\w]*|!+[^\s]*|~|nil|undefined|_+|-+)$"
)

# Material that exists to be committed: certificates and keys under a test or
# fixture path are still reported, but at a severity that reflects what they
# actually are. Suppressing them outright would hide a real leak in a test dir.
_TEST_MATERIAL = re.compile(
    r"(^|/)(tests?|testing|fixtures?|testdata|__tests__|examples?|samples?|mocks?|demo|benchmarks?"
    # Added after Traefik reported two production-grade criticals for TLS keys
    # under integration/resources/tls -- material committed on purpose so the
    # integration suite has something to serve. Same category as the keys under
    # psf/requests' tests/certs, different word for the directory.
    r"|integration|e2e|acceptance|conformance|hack|scripts?/dev)(/|$)"
    r"|(^|/)[^/]*\.(test|spec)\.[a-z]+$"
)

# A PEM header with nothing behind it. Argo CD's operator manual shows how to
# register a repository credential, and the key body in that example is three
# literal dots. Matching the BEGIN line alone reported it as a critical,
# high-confidence leaked private key -- the worst possible finding, on nothing.
_PEM_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
_PEM_PLACEHOLDER = re.compile(
    r"^(?:\.{2,}|<[^>\n]*>|\[[^\]\n]*\]|\{+[^}\n]*\}+|x+|y+|z+|\*+|"
    r"(?:your|my|the|some|redacted|omitted|snip|truncated|placeholder|insert|paste|add)"
    r"[ _-]?[a-z _-]*)$",
    re.IGNORECASE,
)
# A PEM body is base64 by definition, so that -- not a length guess -- is the
# test. A length threshold was tried first and was wrong: it rejected the
# deliberately truncated keys that test suites commit as fixtures, which are
# short but are still key-shaped and still worth reporting.
_BASE64_BODY = re.compile(r"^[A-Za-z0-9+/=]+$")
# An encrypted PEM's preamble is a bare `Key: value` pair per line (`Proc-Type:
# 4,ENCRYPTED`, `DEK-Info: ...`) -- not prose or code that happens to contain a
# colon somewhere in the next few thousand characters.
_PEM_HEADER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:\s*\S")
_MIN_PEM_BODY = 8


def _pem_has_key_material(text: str, start: int) -> bool:
    """True when an actual key body sits between the BEGIN and END markers.

    Deliberately conservative in the direction that keeps findings: a PEM block
    with no END marker at all, or one whose body cannot be read, is treated as
    real. The only thing rejected is a block that is demonstrably a stand-in --
    a body that is not base64, or is one of the usual written placeholders.

    Line-by-line, not a raw slice: a BEGIN marker quoted or referenced inside
    source code (a PEM parser's own string literals, a library's docstring) has
    ordinary code or prose sitting after it, not a key body, and that text
    almost always contains a colon somewhere within a few thousand characters --
    a type hint, a slice, a docstring's field list. Stopping at the first line
    that is neither base64 nor a header line means that surrounding code cannot
    manufacture a body; only lines that are actually PEM-shaped count towards
    one, so `ecdsa`'s own `keys.py` calling `string.find(b"-----BEGIN EC
    PRIVATE KEY-----")` to parse a real key stops being mistaken for one.
    """
    head_end = text.find("\n", start)
    if head_end == -1:
        return False
    end = _PEM_END.search(text, head_end)
    window_end = end.start() if end else head_end + 4000
    collected: list[str] = []
    for line in text[head_end + 1:window_end].splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue
        if _BASE64_BODY.match(stripped_line) or _PEM_HEADER_LINE.match(stripped_line):
            collected.append(stripped_line)
            continue
        break  # the first line that is not PEM-shaped ends the body
    stripped = "".join(collected)
    if len(stripped) < _MIN_PEM_BODY:
        return False
    if _PEM_PLACEHOLDER.match(stripped):
        return False
    return True


# Passwords that mean "this is a local dev stack", not "this is a secret".
# Found by running the corpus over docker/awesome-compose, where four of the
# four highest-severity findings were postgres://postgres:postgres@db:5432.
TRIVIAL_DB_PASSWORDS = {
    "postgres", "mysql", "root", "admin", "password", "passwd", "secret",
    "example", "test", "changeme", "guest", "user", "db", "local", "dev",
    "mariadb", "mongo", "redis", "docker", "123456", "pass",
}


def _trivial_db_credential(url: str) -> bool:
    m = re.match(r"^[a-z+]+://([^\s:@/]+):([^\s@/]+)@", url, re.I)
    if not m:
        return False
    user, password = m.group(1), m.group(2)
    pw = password.lower()
    return pw in TRIVIAL_DB_PASSWORDS or pw == user.lower() or len(password) < 6


def _entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


# A value shaped like an environment-variable name: upper-case letters, digits
# and underscores only, starting with a letter. Credentials mix cases or carry
# punctuation; `OMNI_GRAPH_SEMANTIC_API_KEY` is the name of where one lives.
_ENV_VAR_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
# A symbol that says it holds the NAME of something (`API_KEY_ENV`,
# `token_env_var`, `secret_name`), not the thing itself.
_NAME_HOLDER_SUFFIX = re.compile(r"(?i)(?:_env|_env_var|_envvar|_var|_name|_key_name)$")


def _credential_finding(f, text: str, offset: int, name: str, value: str,
                        test_material: bool, quoted: bool) -> Finding | None:
    """Shared judgement for a credential-shaped assignment.

    Quoted and unquoted assignments differ only in how they are spotted; what
    makes a value a credential is the same either way, so the filters live in
    one place rather than being duplicated and drifting apart.
    """
    val_s = (value or "").strip()
    if not val_s or _PLACEHOLDER.match(val_s):
        return None
    # `SEMANTIC_API_KEY_ENV = "OMNI_GRAPH_SEMANTIC_API_KEY"` names the variable
    # the key is read FROM; the value is an identifier, not a credential
    # (FAIL-050: reported as a hardcoded credential on OmniEngineering's own
    # scan). An upper-case identifier with underscores has two character
    # classes and enough entropy to pass the gates below, so it is excluded by
    # shape, and so is a symbol whose suffix says it holds a name, not a value.
    if _ENV_VAR_NAME.match(val_s) or _NAME_HOLDER_SUFFIX.search(name):
        return None
    # `access_key_status = "Inactive"` and `secretName: tls-cert` hold a status
    # and a reference. The keyword matched; the suffix says what it holds.
    if _NON_SECRET_SUFFIX.search(name):
        return None
    # `secret_key = "secret_key"` — a literal echoing its own symbol.
    if re.sub(r"[^a-z0-9]", "", val_s.lower()) == re.sub(r"[^a-z0-9]", "", name.lower()):
        return None
    # Real credentials do not contain spaces. Values that do are example
    # passphrases ("keyboard cat") or prose, and were the largest source of
    # false positives on well-maintained code.
    if any(ch.isspace() for ch in val_s):
        return None
    if not quoted:
        # A bare value that is a path, a URL or a version is structure.
        if re.match(r"^(?:[./~]|[a-z][a-z0-9+.-]*://|v?\d+(?:\.\d+)+$)", val_s, re.I):
            return None
        # Brackets, parentheses and commas mean this is an expression being
        # assigned, not a literal: `search_tokens=_get_search_tokens(`.
        if any(ch in val_s for ch in "()[]{},"):
            return None
        # A purely alphabetic unquoted value is a word, not a credential. This
        # is what `secretsmanager: GetSecretValue` is — an IAM action in a
        # CloudFormation policy, which the colon made look like an assignment.
        # Real secrets essentially always carry a digit or a symbol.
        if val_s.isalpha():
            return None
    # Require mixed character classes: a lowercase English word is not a secret
    # however long it is.
    classes = sum([
        any(c.islower() for c in val_s),
        any(c.isupper() for c in val_s),
        any(c.isdigit() for c in val_s),
        any(not c.isalnum() for c in val_s),
    ])
    # Entropy is a weak gate on its own. Raising the floor to silence
    # `keyboard cat` also dropped a real hardcoded Azure SQL password and a real
    # API key, because weak credentials have low entropy by definition. The
    # whitespace, placeholder and character-class filters do the discriminating.
    ent = _entropy(val_s)
    if ent < 2.6 or classes < 2:
        return None
    strong = ent >= 4.2 and classes >= 3 and not test_material
    where = "unquoted " if not quoted else ""
    return Finding(
        rule_id="arbiter/secrets.assigned-credential",
        title=f"Hardcoded credential assigned to `{name}`",
        dimension="security",
        severity="high" if strong else "medium",
        confidence="high" if strong else "low",
        repo_id=f.repo_id,
        probe="secrets",
        location=Location(path=f.path, repo_id=f.repo_id,
                          start_line=_line_of(text, offset)),
        description=(f"High-entropy {where}literal (H={ent:.2f}, {classes} character "
                     f"classes) assigned to a credential-named symbol."),
        remediation="Move the value to a secret manager and inject it at runtime.",
        evidence=f"{name}={_mask(val_s)}",
        controls=["NIST-800-53r5:IA-5"],
        tags=["secret"] + ([] if quoted else ["unquoted"]),
    )


def probe_secrets(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    for f in ctx.inventory.text_files():
        if f.role in ("generated",) or f.language in ("markdown", "rst"):
            continue
        text = _read(f)
        if not text:
            continue
        # role == "docs" joins this list because a credential inside a manual is
        # an illustration of where the credential goes, not a leak. Argo CD's
        # operator manual is the case: real-looking YAML, placeholder values.
        test_material = (bool(_TEST_MATERIAL.search(f.path))
                         or f.role in ("test", "docs"))
        for suffix, title, sev, pattern in SECRET_PATTERNS:
            for m in re.finditer(pattern, text):
                val = m.group(0)
                if suffix == "private-key" and not _pem_has_key_material(text, m.start()):
                    continue
                eff_sev, eff_conf = sev, "high"
                note = ""
                if suffix == "pg-url" and _trivial_db_credential(val):
                    eff_sev, eff_conf = "low", "low"
                    note = (" The password is a well-known local-development default, "
                            "so this is a compose or example connection string rather "
                            "than a leaked credential.")
                elif test_material:
                    eff_sev = "medium" if sev in ("critical", "high") else sev
                    eff_conf = "low"
                    note = (" This file sits under a test or fixture path, so the material is "
                            "probably deliberate — confirm it is not a production credential.")
                out.append(Finding(
                    rule_id=f"arbiter/secrets.{suffix}",
                    title=title + (" (test fixture)" if test_material else ""),
                    dimension="security",
                    severity=eff_sev,
                    confidence=eff_conf,
                    repo_id=f.repo_id,
                    probe="secrets",
                    location=Location(path=f.path, repo_id=f.repo_id,
                                      start_line=_line_of(text, m.start())),
                    description="A credential-shaped literal is present in version-controlled source." + note,
                    remediation="Remove the literal, rotate the credential, and load it from a secret store at runtime.",
                    evidence=f"{suffix}:{_mask(val)}",
                    controls=["NIST-800-53r5:IA-5", "NIST-800-53r5:SC-28"],
                    tags=["secret"],
                ))
        for m in _ASSIGNED_SECRET.finditer(text):
            found = _credential_finding(f, text, m.start(), m.group(1), m.group(3),
                                        test_material, quoted=True)
            if found:
                out.append(found)
        for m in _ASSIGNED_SECRET_BARE.finditer(text):
            found = _credential_finding(f, text, m.start(1), m.group(1), m.group(2),
                                        test_material, quoted=False)
            if found:
                out.append(found)
    return out


register(Probe(name="secrets", scope="file", cacheable=True, dimensions=["security"],
               checks=len(SECRET_PATTERNS) + 1, run=probe_secrets))


# ===========================================================================
# resource policy over the normalized graph
# ===========================================================================

def _load_resource_rules() -> list[dict]:
    path = PACKS / "rules" / "resource.yaml"
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("rules", []) if isinstance(data, dict) else []
    except (OSError, yaml.YAMLError):
        return []


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1", "enabled", "on")
    return bool(v)


# Properties whose VALUE is an identifier rather than a boolean: a key ARN, a
# key resource reference, an encryption-set id. For these, presence is the
# evidence -- wiring a KMS key into a resource is what "encrypted with a
# customer-managed key" looks like in every provider's schema.
#
# This matters because _truthy above only accepts literal "true"/"yes"/"1",
# which is correct for a boolean and silently wrong for an identifier. Until
# this existed, `kms_key_id = aws_kms_key.main.arn` was read as false, and a
# volume encrypted with a customer-managed key was reported HIGH as
# unencrypted -- a high-severity false positive on exactly the configuration
# the rule is asking for. It affected every provider, including AWS; the
# multi-cloud fixture is what made it visible.
#
# The distinction is real and has to be kept. A boolean set from a variable
# (`encrypted = var.encrypt_volumes`) is NOT evidence of encryption, because
# the variable may be false; that case stays exactly as it was.
_REFERENCE_PROPERTY = re.compile(
    r"(?i)(^|[._])(kms_key_id|kms_key_arn|kms_key_name|kms_key_self_link|"
    r"kms_master_key_id|key_vault_key_id|encryption_key_name|"
    r"disk_encryption_set_id|disk_encryption_key|customer_managed_key|"
    r"encryption_settings|default_kms_key_name|encryption_config|"
    r"server_side_encryption_configuration|bucketencryption)$"
)

# A reference to nothing is not evidence. An empty string, an explicit null, or
# a placeholder does not wire a key into anything.
_EMPTY_REFERENCE = {"", "none", "null", "nil", "false", "0", "-", "n/a"}


def _reference_present(name: str, value: Any) -> bool:
    """True when an identifier-valued property actually points at something."""
    if not _REFERENCE_PROPERTY.search(name or ""):
        return False
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _EMPTY_REFERENCE
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(value)


SATISFIED, VIOLATED, UNKNOWN = "satisfied", "violated", "unknown"

INGRESS_KEYS = ("ingress", "Ingress", "SecurityGroupIngress", "ingress_rules", "cidr_blocks", "type")


def _rule_properties(rule: dict) -> list[str]:
    """The property paths an assertion reads."""
    kind = rule.get("assert")
    if kind == "no_open_ingress":
        return list(INGRESS_KEYS)
    if kind == "text_absent":
        return []
    props = rule.get("any_of") or ([rule.get("property")] if rule.get("property") else [])
    return [p for p in props if p]


def _is_referenced_by(res: Resource, dotted: str, all_resources: list) -> bool:
    """True if some other resource's `dotted` property points back at res."""
    for other in all_resources:
        if other is res:
            continue
        val = other.get(dotted)
        if isinstance(val, dict) and val.get("Ref") == res.address:
            return True
        if isinstance(val, str) and val == res.address:
            return True
    return False


def _eval_assert(res: Resource, rule: dict, all_resources: list | None = None) -> str:
    """Three outcomes, not two.

    A plan can leave a value undetermined until apply. Treating that as absent
    is how a scanner reports an encrypted bucket as unencrypted, so an
    assertion that depends on an undetermined value returns UNKNOWN and is
    reported as not assessed rather than as either a pass or a finding.
    """
    # A compensating control satisfies the rule outright. This is how a bucket
    # with a public-access block stops being reported for a public ACL.
    for p in rule.get("unless_truthy", []):
        if _truthy(res.get(p)):
            return SATISFIED

    # A resource can be exempt because of what points AT it rather than what's
    # in its own properties -- e.g. a bucket that IS the delivery destination
    # for other buckets' access logs has no LoggingConfiguration of its own,
    # by design: logging a log bucket to itself is a delivery loop, not a gap.
    for p in rule.get("unless_referenced_by", []):
        if all_resources and _is_referenced_by(res, p, all_resources):
            return SATISFIED

    # Same idea as unless_truthy, but for cases the compensating control isn't
    # a boolean-ish property -- it's a string value nested inside a list, e.g.
    # a listener whose DefaultActions entry has Type=redirect. A port-80
    # listener that only redirects to port 443 never forwards a plaintext
    # request; the plaintext-listener rule shouldn't fire on it.
    for needle in rule.get("unless_text_present", []):
        if str(needle).lower() in res.flat_text():
            return SATISFIED

    kind = rule.get("assert")
    referenced = _rule_properties(rule)
    undetermined = [p for p in referenced if res.is_unknown(p)]

    def inconclusive(outcome: str) -> str:
        return UNKNOWN if (outcome == VIOLATED and undetermined) else outcome

    if kind == "property_truthy":
        for p in referenced:
            val = res.get(p)
            if _truthy(val) or _reference_present(p, val):
                return SATISFIED
        return inconclusive(VIOLATED)
    if kind == "property_absent_or_false":
        for p in referenced:
            if _truthy(res.get(p)):
                return VIOLATED
        return UNKNOWN if undetermined else SATISFIED
    if kind == "property_equals":
        if undetermined:
            return UNKNOWN
        return SATISFIED if str(res.get(rule.get("property", ""))) == str(rule.get("value")) else VIOLATED
    if kind == "property_not_in":
        bad = set(str(x) for x in rule.get("values", []))
        for p in referenced:
            val = res.get(p)
            if val is None:
                continue
            vals = val if isinstance(val, list) else [val]
            if any(str(v) in bad for v in vals if v is not None):
                return VIOLATED
        return UNKNOWN if undetermined else SATISFIED
    if kind == "text_absent":
        needles = [str(n).lower() for n in rule.get("values", [])]
        blob = res.flat_text()
        return VIOLATED if any(n in blob for n in needles) else SATISFIED
    if kind == "text_present":
        needles = [str(n).lower() for n in rule.get("values", [])]
        blob = res.flat_text()
        return SATISFIED if any(n in blob for n in needles) else VIOLATED
    if kind == "no_open_ingress":
        if _has_open_ingress(res):
            return VIOLATED
        return UNKNOWN if undetermined else SATISFIED
    return SATISFIED


OPEN_CIDRS = ("0.0.0.0/0", "::/0")


def _has_open_ingress(res: Resource) -> bool:
    """True when an ingress rule accepts the whole internet.

    Egress to 0.0.0.0/0 is ordinary, so this deliberately inspects ingress
    blocks only rather than grepping the whole resource.
    """
    candidates: list[Any] = []
    for key in ("ingress", "Ingress", "SecurityGroupIngress", "ingress_rules"):
        v = res.get(key)
        if v:
            candidates.extend(v if isinstance(v, list) else [v])
    # aws_security_group_rule is a standalone resource with a type field
    if res.native.endswith("security_group_rule"):
        if str(res.get("type", "")).lower() == "ingress":
            candidates.append(res.properties)
    if not candidates:
        return False
    for block in candidates:
        if not isinstance(block, dict):
            continue
        for key in ("cidr_blocks", "CidrIp", "cidr_ipv6", "CidrIpv6", "ipv6_cidr_blocks"):
            v = block.get(key)
            if v is None:
                continue
            vals = v if isinstance(v, list) else [v]
            if any(str(x).strip() in OPEN_CIDRS for x in vals):
                return True
    return False


def probe_resource_policy(ctx: ProbeContext) -> list[Finding]:
    rules = _load_resource_rules()
    out: list[Finding] = []
    undetermined: list[tuple[str, str]] = []   # (rule id, resource address)

    for rule in rules:
        kinds = set(rule.get("match_kinds", []))
        natives = set(rule.get("match_native", []))
        # Some resources of a provider simply do not express the control. Azure
        # SQL always enforces TLS and has no property saying so, so a rule that
        # looks for one reports every Azure SQL database ever written. This is
        # the same shape as the Kubernetes PersistentVolumeClaim episode, one
        # level finer: not the provider, the specific resource type.
        excluded_natives = set(rule.get("exclude_native", []))
        # Provider scoping exists because a normalized kind can hide a
        # different property model. A Kubernetes PersistentVolumeClaim is a
        # block_store, but encryption lives on its StorageClass, so an AWS
        # encryption rule fires on every PVC ever written.
        providers = set(rule.get("match_providers", []))
        excluded = set(rule.get("exclude_providers", []))
        for res in ctx.graph:
            if kinds and res.kind not in kinds:
                continue
            if natives and res.native not in natives:
                continue
            if providers and res.provider not in providers:
                continue
            if res.provider in excluded:
                continue
            if res.native in excluded_natives:
                continue
            verdict = _eval_assert(res, rule, ctx.graph)
            if verdict == SATISFIED:
                continue
            if verdict == UNKNOWN:
                undetermined.append((rule["id"], res.address))
                if rule.get("severity") in ("critical", "high"):
                    out.append(Finding(
                        rule_id=f"arbiter/resource.{rule['id']}.not-assessed",
                        title=f"Cannot evaluate: {rule.get('title', rule['id'])}",
                        dimension=rule.get("dimension", "security"),
                        severity="info", confidence="high",
                        repo_id=res.repo_id, probe="resource_policy",
                        location=Location(path=res.origin.path,
                                          start_line=res.origin.start_line,
                                          logical=res.address,
                                          repo_id=res.repo_id),
                        description=(
                            "The plan leaves this value undetermined until apply, so the check "
                            "could not run. It is not a pass — re-evaluate against state after "
                            "apply, or pin the value in configuration."
                        ),
                        remediation="Set the property explicitly, or scan the post-apply state.",
                        evidence=f"unknown:{res.native} {res.address}",
                        tags=["iac", "not-assessed", res.provider],
                    ))
                continue
            # Infrastructure declared under an examples or test path is
            # documentation, not deployment. Reported, but not as though it
            # were running in production.
            demo = bool(_TEST_MATERIAL.search(res.origin.path))
            sev = rule.get("severity", "medium")
            conf = rule.get("confidence", "high")
            if demo:
                sev = {"critical": "medium", "high": "medium", "medium": "low"}.get(sev, sev)
                conf = "low"
            out.append(Finding(
                rule_id=f"arbiter/resource.{rule['id']}",
                title=rule.get("title", rule["id"]) + (" (example code)" if demo else ""),
                dimension=rule.get("dimension", "security"),
                severity=sev,
                confidence=conf,
                repo_id=res.repo_id,
                probe="resource_policy",
                location=Location(
                    path=res.origin.path,
                    start_line=res.origin.start_line,
                    logical=res.address,
                    repo_id=res.repo_id,
                ),
                description=rule.get("description", ""),
                remediation=rule.get("remediation", ""),
                evidence=f"{res.native} {res.address}",
                controls=rule.get("controls", []),
                tags=["iac", res.provider],
            ))

    # Say plainly which reading the verdicts rest on. A literal source read
    # cannot resolve variables, for_each or module composition, so a clean
    # result from one is a weaker claim than a clean result from a plan.
    for repo in ctx.repos:
        in_repo = [r for r in ctx.graph if r.repo_id == repo.id]
        if not in_repo:
            continue
        planned = [r for r in in_repo if r.source in ("plan", "state")]
        literal = [r for r in in_repo if r.source == "hcl"]
        if literal and not planned:
            out.append(Finding(
                rule_id="arbiter/resource.source-is-literal-hcl",
                title=f"Terraform evaluated from source, not from a plan ({len(literal)} resources)",
                dimension="compliance", severity="info", confidence="high",
                repo_id=repo.id, probe="resource_policy",
                location=Location(path=literal[0].origin.path, repo_id=repo.id),
                description=(
                    "Values were read literally from .tf files. Variables, for_each, count and "
                    "module composition are not resolved, so a resource whose configuration is "
                    "computed reads as whatever is written inline."
                ),
                remediation=(
                    "terraform plan -out=tfplan.bin && terraform show -json tfplan.bin > tfplan.json, "
                    "then scan with --tfplan tfplan.json"
                ),
                evidence=f"source=hcl,resources={len(literal)}",
                tags=["iac", "coverage"],
            ))
        if undetermined and planned:
            in_repo_unknown = [u for u in undetermined if any(r.address == u[1] for r in in_repo)]
            if in_repo_unknown:
                out.append(Finding(
                    rule_id="arbiter/resource.unknown-until-apply",
                    title=f"{len(in_repo_unknown)} check(s) could not be evaluated before apply",
                    dimension="compliance", severity="info", confidence="high",
                    repo_id=repo.id, probe="resource_policy",
                    location=Location(path=planned[0].origin.path, repo_id=repo.id),
                    description=(
                        "These values are undetermined in the plan: "
                        + ", ".join(f"{rid} on {addr}" for rid, addr in in_repo_unknown[:8])
                        + ("…" if len(in_repo_unknown) > 8 else "")
                        + ". They are recorded as not assessed, not as passes."
                    ),
                    remediation="Re-scan the post-apply state, or pin the values in configuration.",
                    evidence=f"unknown_checks={len(in_repo_unknown)}",
                    tags=["iac", "coverage", "not-assessed"],
                ))
    return out


register(Probe(
    name="resource_policy",
    scope="file",
    dimensions=["security", "compliance"],
    checks=max(1, len(_load_resource_rules())),
    run=probe_resource_policy,
    stacks=["terraform", "cloudformation", "aws_cdk", "kubernetes"],
))


# ===========================================================================
# quality
# ===========================================================================

_TODO = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")


def probe_quality(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    cfg = (ctx.config.get("quality") or {})
    max_file = int(cfg.get("max_file_lines", 800))

    source_files = [f for f in ctx.inventory.text_files() if f.role in ("source", "iac")]
    test_files = [f for f in ctx.inventory.text_files() if f.role == "test"]

    for f in source_files:
        if f.lines > max_file:
            out.append(Finding(
                rule_id="arbiter/quality.file-too-long",
                title=f"File is {f.lines} lines (limit {max_file})",
                dimension="quality", severity="low", confidence="high",
                repo_id=f.repo_id, probe="quality",
                location=Location(path=f.path, start_line=1, repo_id=f.repo_id),
                description="Long files concentrate change risk and slow review.",
                remediation="Split along the seams that already exist in the file.",
                evidence=f"lines={f.lines}",
                tags=["size"],
            ))
        text = _read(f)
        if not text:
            continue
        todos = list(_TODO.finditer(text))
        if len(todos) >= 5:
            out.append(Finding(
                rule_id="arbiter/quality.todo-density",
                title=f"{len(todos)} unresolved TODO/FIXME markers in one file",
                dimension="quality", severity="low", confidence="medium",
                repo_id=f.repo_id, probe="quality",
                location=Location(path=f.path, repo_id=f.repo_id,
                                  start_line=_line_of(text, todos[0].start())),
                description="A cluster of deferred work markers usually means an unfinished refactor.",
                remediation="Convert them to tracked issues or resolve them.",
                evidence=f"markers={len(todos)}",
                tags=["debt"],
            ))
    for repo in ctx.repos:
        src = [f for f in source_files if f.repo_id == repo.id and f.language in
               ("python", "javascript", "typescript", "go", "java", "ruby", "rust")]
        tst = [f for f in test_files if f.repo_id == repo.id]
        if len(src) >= 5 and not tst:
            out.append(Finding(
                rule_id="arbiter/quality.no-tests",
                title="No test files found in this repository",
                dimension="quality", severity="medium", confidence="high",
                repo_id=repo.id, probe="quality",
                location=Location(path=".", repo_id=repo.id),
                description=f"{len(src)} source files and no recognizable tests.",
                remediation="Add a test directory and cover the highest-risk module first.",
                evidence=f"source_files={len(src)}",
                tags=["tests"],
            ))
    return out


register(Probe(name="quality", dimensions=["quality"], checks=3, run=probe_quality))


# ===========================================================================
# ast metrics — real function boundaries, every supported language
# ===========================================================================

def probe_ast_metrics(ctx: ProbeContext) -> list[Finding]:
    from . import ast as ts

    cfg = (ctx.config.get("quality") or {})
    max_func = int(cfg.get("max_function_lines", 120))
    max_cx = int(cfg.get("max_complexity", 20))
    max_depth = int(cfg.get("max_nesting_depth", 6))

    out: list[Finding] = []
    for f in ctx.inventory.text_files():
        if f.role in ("generated", "docs", "data"):
            continue
        if not ts.ts_name(f.language):
            continue
        for fn in ts.functions(f.abspath, f.language):
            loc = Location(path=f.path, start_line=fn.start_line,
                           end_line=fn.end_line, logical=fn.name,
                           repo_id=f.repo_id)
            if fn.lines > max_func:
                out.append(Finding(
                    rule_id="arbiter/ast.function-too-long",
                    title=f"`{fn.name}` is {fn.lines} lines (limit {max_func})",
                    dimension="quality", severity="low", confidence="high",
                    repo_id=f.repo_id, probe="ast_metrics", location=loc,
                    description="Measured from the parse tree, not from indentation.",
                    remediation="Extract the distinct steps into named helpers.",
                    evidence=f"{fn.name}:len={fn.lines}",
                    tags=["size", f.language],
                ))
            if fn.complexity > max_cx:
                out.append(Finding(
                    rule_id="arbiter/ast.high-complexity",
                    title=f"`{fn.name}` has cyclomatic complexity {fn.complexity} (limit {max_cx})",
                    dimension="quality", severity="low", confidence="high",
                    repo_id=f.repo_id, probe="ast_metrics", location=loc,
                    description="Counted from branch nodes in the parse tree.",
                    remediation="Split the decision logic, or replace a branch chain with a lookup.",
                    evidence=f"{fn.name}:cx={fn.complexity}",
                    tags=["complexity", f.language],
                ))
            if fn.max_depth > max_depth:
                out.append(Finding(
                    rule_id="arbiter/ast.deep-nesting",
                    title=f"`{fn.name}` nests {fn.max_depth} levels deep (limit {max_depth})",
                    dimension="quality", severity="low", confidence="high",
                    repo_id=f.repo_id, probe="ast_metrics", location=loc,
                    description="Deeply nested code is hard to read and harder to cover.",
                    remediation="Invert conditions and return early.",
                    evidence=f"{fn.name}:depth={fn.max_depth}",
                    tags=["complexity", f.language],
                ))
    return out


register(Probe(
    name="ast_metrics", scope="file", cacheable=True, dimensions=["quality"], checks=3,
    run=probe_ast_metrics, modules=["tree_sitter_language_pack"],
))


# ===========================================================================
# supply chain
# ===========================================================================

_UNPINNED_PY = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(?:[><~!]=|>|<)?\s*([0-9][^\s;#]*)?\s*$")
_ACTION_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s@]+)@([^\s#]+)", re.M)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def probe_supply_chain(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    for f in ctx.inventory.text_files():
        base = f.path.rsplit("/", 1)[-1]
        text = _read(f)
        if not text:
            continue

        if base in ("requirements.txt", "requirements-dev.txt"):
            for i, line in enumerate(text.split("\n"), 1):
                s = line.strip()
                if not s or s.startswith(("#", "-r", "--")):
                    continue
                if "==" not in s and not s.startswith("git+"):
                    pkg = re.split(r"[<>=!~\[; ]", s, 1)[0].strip()
                    out.append(Finding(
                        rule_id="arbiter/supply.unpinned-python-dep",
                        title=f"Unpinned dependency `{pkg}`",
                        # Severity "info": measured on the corpus, this fires at
                        # 0.0x on deliberately vulnerable Python relative to
                        # well-maintained Python. It does not discriminate, so it
                        # is reported for completeness and scores zero.
                        dimension="supply_chain", severity="info", confidence="high",
                        repo_id=f.repo_id, probe="supply_chain",
                        location=Location(path=f.path, start_line=i, repo_id=f.repo_id),
                        description="An unpinned requirement makes builds non-reproducible. Note that "
                                    "libraries are expected to declare ranges; this matters for "
                                    "applications and deployment manifests.",
                        remediation="Pin with == and manage upgrades deliberately.",
                        evidence=s[:80],
                        controls=["NIST-800-218:PW.4", "NIST-800-53r5:CM-2"],
                        tags=["dependencies"],
                    ))

        if base == "package.json":
            import json as _json
            try:
                doc = _json.loads(text)
            except ValueError:
                continue
            for section in ("dependencies", "devDependencies"):
                for name, spec in (doc.get(section) or {}).items():
                    if isinstance(spec, str) and spec[:1] in ("^", "~", "*") or spec in ("latest", "*"):
                        out.append(Finding(
                            rule_id="arbiter/supply.unpinned-npm-dep",
                            title=f"Floating version range on `{name}` ({spec})",
                            # Severity "info": 0.68/kloc on well-maintained Node
                            # vs 0.73/kloc on deliberately vulnerable Node — a
                            # ratio of 1.1x, which is no signal at all.
                            dimension="supply_chain", severity="info", confidence="high",
                            repo_id=f.repo_id, probe="supply_chain",
                            location=Location(path=f.path, repo_id=f.repo_id,
                                              logical=f"{section}.{name}"),
                            description="A caret or tilde range resolves differently over time.",
                            remediation="Pin exact versions and rely on a lockfile plus a bot for upgrades.",
                            evidence=f"{name}@{spec}",
                            controls=["NIST-800-218:PW.4"],
                            tags=["dependencies"],
                        ))

        if f.role == "ci":
            for m in _ACTION_USES.finditer(text):
                action, ref = m.group(1), m.group(2)
                if action.startswith("./") or _SHA40.match(ref):
                    continue
                out.append(Finding(
                    rule_id="arbiter/supply.unpinned-action",
                    title=f"Action `{action}` pinned to a mutable ref (`{ref}`)",
                    # Stays at "low": unlike the package-manager rules, this one
                    # discriminates — 4.3x on Node, 219x on CloudFormation.
                    dimension="supply_chain", severity="low", confidence="high",
                    repo_id=f.repo_id, probe="supply_chain",
                    location=Location(path=f.path, repo_id=f.repo_id,
                                      start_line=_line_of(text, m.start())),
                    description="Tags and branches can be repointed, so the workflow can execute different code tomorrow.",
                    remediation="Pin the action to a full commit SHA.",
                    evidence=f"{action}@{ref}",
                    controls=["NIST-800-218:PW.4", "NIST-800-53r5:CM-7"],
                    tags=["ci", "supply-chain"],
                ))
            if "pull_request_target" in text:
                idx = text.index("pull_request_target")
                # The trigger alone is not the vulnerability. It becomes one
                # when the workflow also checks out the pull request's head,
                # which is what puts untrusted code next to the secrets. Every
                # well-maintained repository in the tuning corpus used this
                # trigger safely for PR-title and labelling workflows.
                checks_out_head = bool(
                    re.search(r"uses:\s*actions/checkout", text)
                    and re.search(r"ref:\s*\$\{\{\s*github\.event\.pull_request\.head|"
                                  r"ref:\s*\$\{\{\s*github\.head_ref", text)
                )
                out.append(Finding(
                    rule_id="arbiter/supply.pull-request-target",
                    title=("Workflow checks out pull-request code under `pull_request_target`"
                           if checks_out_head else
                           "Workflow triggers on `pull_request_target`"),
                    dimension="security",
                    # The benign form is "info", not "low". Measured across the
                    # corpus, `pull_request_target` used safely -- for labelling
                    # and PR-title workflows -- appears twelve times on
                    # well-maintained repositories and once on the deliberately
                    # vulnerable ones. Scoring it was the only reason this rule
                    # failed its own discrimination test. It is still reported,
                    # because it is worth knowing the trigger is in use before
                    # somebody adds a checkout to that workflow.
                    severity="high" if checks_out_head else "info",
                    confidence="high" if checks_out_head else "low",
                    repo_id=f.repo_id, probe="supply_chain",
                    location=Location(path=f.path, repo_id=f.repo_id,
                                      start_line=_line_of(text, idx)),
                    description=(
                        "The workflow runs with repository secrets in scope and checks out the "
                        "fork's head commit, so untrusted code executes with those secrets."
                        if checks_out_head else
                        "This trigger runs with repository secrets in scope. It is safe as long as "
                        "the workflow never checks out the pull request's head; no such checkout "
                        "was found here."
                    ),
                    remediation="Never check out the PR head in a `pull_request_target` workflow; "
                                "split privileged steps into their own workflow.",
                    evidence=f"trigger=pull_request_target,checkout_head={checks_out_head}",
                    controls=["NIST-800-53r5:CM-7"],
                    tags=["ci"],
                ))
    return out


register(Probe(name="supply_chain", scope="file", cacheable=True,
               dimensions=["supply_chain", "security"], checks=5, run=probe_supply_chain))


# ===========================================================================
# doc drift (deterministic only — no model required)
# ===========================================================================

_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_ENV_SECTION = re.compile(r"^#{1,6}\s*.*\b(environment|env\s+var|configuration|settings)\b.*$",
                          re.I | re.M)
_HEADING = re.compile(r"^#{1,6}\s", re.M)
_LIST_OR_ROW = re.compile(r"^\s*(?:[-*+]|\d+\.|\|)")


def _normalize_relative(path: str) -> str:
    """Resolve `.` and `..` segments without touching the filesystem.

    Doing this with str.replace('./', '') corrupts '../' into '.', which made
    every link to a parent directory look broken.
    """
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            else:
                return ""  # escapes the repository root; not ours to check
            continue
        parts.append(seg)
    return "/".join(parts)
_BACKTICK_PATH = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|tf|ts|js|json|ya?ml|md|sh|sql))`")
_ENV_MENTION = re.compile(r"\b([A-Z][A-Z0-9_]{4,})\b")
_URL_ON_LINE = re.compile(r"https?://")
# The directory `arbiter scan` writes to unless told otherwise. A path with
# this segment in it is a run's output wherever the run was pointed.
_OUTPUT_SEGMENT = "arbiter-out"

# One parsed .gitignore line: the glob, whether it re-includes (`!`), whether
# it names only a directory (trailing `/`), and whether it is anchored to the
# repository root (a `/` anywhere but the end).
IgnorePattern = tuple[str, bool, bool, bool]


def _ignore_patterns(root: Path) -> list[IgnorePattern]:
    """Parse the root .gitignore, as far as the drift probe needs it.

    Comments and blank lines are dropped; `dir/`, `*.ext`, `path/**`, a
    leading `/` and `!` negation are honoured. Nested .gitignore files, escaped
    characters and the finer points of `**` are not: a pattern this misreads
    makes one documented path look missing, which is the finding the person
    was already getting, and a pattern read too broadly hides real drift,
    which is why only these shapes are read at all.
    """
    try:
        lines = (root / ".gitignore").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[IgnorePattern] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        # `**/x` means x at any depth, which is what an unanchored pattern
        # already means here; the prefix is dropped and the anchor with it.
        any_depth = line.startswith("**/")
        if any_depth:
            line = line[3:]
        anchored = "/" in line and not any_depth
        line = line.lstrip("/")
        if line:
            out.append((line, negated, dir_only, anchored))
    return out


def _ignore_match(path: str, pattern: str, anchored: bool) -> bool:
    """One gitignore glob against one relative path.

    Anchored patterns match the whole path from the root. Unanchored ones
    match at any depth: every suffix of the path is tried, so `*.log` finds
    `build/out/x.log` and `.arbiter/cache.json` finds it in any subtree.
    """
    if anchored:
        return fnmatch.fnmatchcase(path, pattern)
    parts = path.split("/")
    return any(fnmatch.fnmatchcase("/".join(parts[i:]), pattern) for i in range(len(parts)))


def _gitignored(root: Path, rel: str, patterns: list[IgnorePattern] | None = None) -> bool:
    """Would git ignore this path? The path and each parent directory are
    tried against every pattern in order, the last match winning, which is
    how git reads the file. A directory-only pattern matches parents alone,
    since a path a document names with an extension is a file."""
    if patterns is None:
        patterns = _ignore_patterns(root)
    if not patterns:
        return False
    parts = rel.split("/")
    targets = [("/".join(parts[:i]), True) for i in range(1, len(parts))] + [(rel, False)]
    ignored = False
    for pattern, negated, dir_only, anchored in patterns:
        for target, is_dir in targets:
            if dir_only and not is_dir:
                continue
            if _ignore_match(target, pattern, anchored):
                ignored = not negated
                break
    return ignored


def _output_dirs(root: Path | None, config: dict, out_dir: str) -> set[str]:
    """Where output lands, relative to the repository: the `out:` key of
    arbiter.yaml, if set, and this run's own output directory when it sits
    inside the tree being scanned."""
    dirs: set[str] = set()
    configured = (config or {}).get("out")
    if isinstance(configured, str) and configured.strip():
        norm = _normalize_relative(configured.strip().replace("\\", "/"))
        if norm:
            dirs.add(norm)
    if out_dir and root is not None:
        try:
            dirs.add(Path(out_dir).resolve().relative_to(root.resolve()).as_posix())
        except (ValueError, OSError):
            pass
    return dirs


def _under_output(rel: str, out_dirs: set[str]) -> bool:
    parents = rel.split("/")[:-1]
    if _OUTPUT_SEGMENT in parents:
        return True
    return any(rel == d or rel.startswith(d + "/") for d in out_dirs)


def _line_has_url(text: str, pos: int) -> bool:
    """Does the line holding `pos`, or the line either side of it in the same
    paragraph, carry an http(s) URL? Prose that names a file beside a link to
    another repository or site is describing that place, not this one. The
    neighbours count because documentation is hard-wrapped: the sentence that
    put `docs/arbiter-integration.md` next to its URL broke between them. A
    blank line ends the paragraph and the search with it."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    end = len(text) if end == -1 else end
    window = [text[start:end]]
    prev_end = start - 1
    if prev_end > 0:
        prev_start = text.rfind("\n", 0, prev_end) + 1
        window.append(text[prev_start:prev_end])
    if end < len(text):
        next_end = text.find("\n", end + 1)
        window.append(text[end + 1:len(text) if next_end == -1 else next_end])
    return any(line.strip() and _URL_ON_LINE.search(line) for line in window)


def _once_tracked(root: Path, rel: str, cache: dict[str, bool]) -> bool:
    """Was this path ever committed? A changelog or decision log is right to
    name a file that was deliberately removed; a README naming one that never
    existed has a typo. Git can tell the two apart, and when there is no git
    the answer is unknown, which reads as never."""
    if rel in cache:
        return cache[rel]
    answer = False
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "log", "--all", "--diff-filter=A", "-1",
             "--format=%H", "--", rel],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10,
        )
        answer = r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        answer = False
    cache[rel] = answer
    return answer


def probe_doc_drift(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    tracked_cache: dict[str, bool] = {}
    # .gitignore is parsed once per repository, not once per document.
    ignore_cache: dict[str, list[IgnorePattern]] = {}
    by_repo_paths: dict[str, set[str]] = {}
    for f in ctx.inventory.files:
        by_repo_paths.setdefault(f.repo_id, set()).add(f.path)

    # The inventory records what the probes were shown, which is a different
    # question from whether a documented file exists. Directories in SKIP_DIRS
    # -- `.arbiter` among them -- hold tracked files that prose legitimately
    # names, and checking the inventory alone reported every one of them as
    # missing. Existence is a question about disk.
    roots = {r.id: Path(r.path) for r in ctx.repos if getattr(r, "path", "")}

    code_blobs: dict[str, str] = {}
    for f in ctx.inventory.text_files():
        if f.role in ("source", "iac", "config", "ci"):
            code_blobs[f.repo_id] = code_blobs.get(f.repo_id, "") + "\n" + _read(f)

    for f in ctx.inventory.text_files():
        if f.language != "markdown":
            continue
        text = _read(f)
        if not text:
            continue
        known = by_repo_paths.get(f.repo_id, set())
        base_dir = f.path.rsplit("/", 1)[0] if "/" in f.path else ""

        has_html = any(k.endswith(".html") for k in known)
        repo_root = roots.get(f.repo_id)
        if f.repo_id not in ignore_cache:
            ignore_cache[f.repo_id] = _ignore_patterns(repo_root) if repo_root else []
        ignore_patterns = ignore_cache[f.repo_id]
        out_dirs = _output_dirs(repo_root, ctx.config, ctx.out_dir)

        def not_ours(rel: str) -> bool:
            # Generated output and git-ignored paths are not files the
            # repository is missing: a document is right to name what a run
            # writes or what a checkout never commits (ARB-048).
            if _under_output(rel, out_dirs):
                return True
            return repo_root is not None and _gitignored(repo_root, rel, ignore_patterns)

        def on_disk(rel: str) -> bool:
            root = roots.get(f.repo_id)
            if not rel or root is None:
                return False
            try:
                return (root / rel).exists()
            except OSError:
                return False

        def resolve(target: str) -> str:
            t = target.split("#")[0].strip()
            if not t or t.startswith(("http://", "https://", "mailto:", "tel:")):
                return ""
            if t.startswith("/"):
                return t.lstrip("/")
            return f"{base_dir}/{t}" if base_dir else t

        seen: set[str] = set()
        for m in _MD_LINK.finditer(text):
            raw_target = m.group(1).split("#")[0].strip()
            # Documentation that is published as a website links to rendered
            # pages that do not exist in the repository. Checking those is
            # checking the wrong artifact.
            if raw_target.endswith((".html", ".htm")) and not has_html:
                continue
            cand = resolve(m.group(1))
            if not cand or cand in seen:
                continue
            seen.add(cand)
            # `git://`, `ssh://` and the like name somewhere else entirely.
            if "://" in cand:
                continue
            norm = _normalize_relative(cand)
            if not norm:
                continue
            stripped = norm.rstrip("/")
            if stripped in known or any(k.startswith(stripped + "/") for k in known):
                continue
            if on_disk(stripped) or not_ours(stripped):
                continue
            out.append(Finding(
                rule_id="arbiter/drift.broken-doc-link",
                title=f"Documentation links to `{m.group(1)}`, which does not exist",
                dimension="drift", severity="medium", confidence="high",
                repo_id=f.repo_id, probe="doc_drift",
                # The Location carries the repo as well as the Finding: `short()`
                # builds its `repo:path` prefix from the Location, so without it a
                # queue spanning 36 repositories prints a bare
                # `python/stepfunctions/README.md`, which names no repository at all.
                location=Location(path=f.path, repo_id=f.repo_id,
                                  start_line=_line_of(text, m.start())),
                description="The documentation references a path that is not in the repository.",
                remediation="Fix the link or restore the file.",
                evidence=f"link={m.group(1)}",
                tags=["docs"],
            ))

        for m in _BACKTICK_PATH.finditer(text):
            # _normalize_relative, not lstrip("./"): lstrip strips a character
            # set rather than a prefix, so `.ai/context-brief.md` collapsed to
            # `ai/context-brief.md` and every dotted path read as missing.
            cand = _normalize_relative(m.group(1))
            if not cand:
                continue
            if cand in known or any(k.endswith("/" + cand) for k in known):
                continue
            if on_disk(cand) or not_ours(cand):
                continue
            # A path named beside a URL -- `docs/handbook.md` in the other
            # repository (https://...) -- describes that repository, not this
            # one, and hard wrapping may put the URL on the next line. Links
            # are not excused this way: a relative link is a claim about this
            # tree whatever else the line says.
            if _line_has_url(text, m.start()):
                continue
            root = roots.get(f.repo_id)
            removed = root is not None and _once_tracked(root, cand, tracked_cache)
            out.append(Finding(
                rule_id="arbiter/drift.doc-references-missing-file",
                title=(f"Documentation describes `{cand}`, which was removed from the repository"
                       if removed else
                       f"Documentation describes `{cand}`, which is not in the repository"),
                dimension="drift", severity="info" if removed else "low", confidence="medium",
                repo_id=f.repo_id, probe="doc_drift",
                location=Location(path=f.path, repo_id=f.repo_id,
                                  start_line=_line_of(text, m.start())),
                description=("The file was tracked once and is gone now. A changelog or decision "
                             "record is right to name it; a guide that still points a reader at "
                             "it is stale."
                             if removed else
                             "A file named in prose has no counterpart on disk — usually a rename the docs missed."),
                remediation=("Confirm the text describes the removal, or drop the reference."
                             if removed else
                             "Update the document, or confirm the file was intentionally removed."),
                evidence=f"path={cand}" + (" removed=true" if removed else ""),
                tags=["docs"] + (["history"] if removed else []),
            ))

        # Environment-variable drift is only checked inside a section that is
        # explicitly about configuration, and only for names written as list
        # items or table rows. Scanning whole documents for SHOUTING_WORDS
        # matched changelog entries, template markers and CLI variables that
        # belong to other tools.
        blob = code_blobs.get(f.repo_id, "")
        base_name = f.path.rsplit("/", 1)[-1].upper()
        if blob and not base_name.startswith(("CHANGELOG", "HISTORY", "NEWS", "UPGRADE", "MIGRAT")):
            for sec in _ENV_SECTION.finditer(text):
                nxt = _HEADING.search(text, sec.end())
                section = text[sec.end():nxt.start() if nxt else len(text)]
                offset = sec.end()
                for m in _ENV_MENTION.finditer(section):
                    name = m.group(1)
                    if name in ("README", "LICENSE", "NOTICE", "TODO", "NOTE", "WARNING",
                                "HTTPS", "HTTP", "JSON", "YAML", "HTML", "TRUE", "FALSE"):
                        continue
                    line_start = section.rfind("\n", 0, m.start()) + 1
                    if not _LIST_OR_ROW.match(section[line_start:m.start() + 1]):
                        continue
                    if name in blob:
                        continue
                    out.append(Finding(
                        rule_id="arbiter/drift.documented-env-var-absent",
                        title=f"Documented environment variable `{name}` is never read in code",
                        dimension="drift", severity="low", confidence="low",
                        repo_id=f.repo_id, probe="doc_drift",
                        location=Location(path=f.path, repo_id=f.repo_id,
                                          start_line=_line_of(text, offset + m.start())),
                        description="A configuration section documents this variable and no source "
                                    "file reads it.",
                        remediation="Remove it from the docs, or wire it up.",
                        evidence=f"env={name}",
                        tags=["docs", "config"],
                    ))
    return out


register(Probe(name="doc_drift", dimensions=["drift"], checks=3, run=probe_doc_drift))


# ===========================================================================
# cross-repo interface checks
# ===========================================================================

_CONST_ASSIGN = re.compile(
    r"""(?m)^\s*(?:export\s+|const\s+|final\s+|let\s+|var\s+)?
        ([A-Z][A-Z0-9_]{3,})\s*[:=]\s*
        ["']?([A-Za-z0-9_.:/-]{1,60})["']?\s*(?:;|$)""",
    re.X,
)
_ENV_READ = re.compile(r"""(?:os\.environ(?:\.get)?\[?\(?|process\.env\.|getenv\()\s*["']?([A-Z][A-Z0-9_]{3,})["']?""")
# Only a QUOTED mapping key counts as provisioning an environment variable.
# A bare `NAME = value` assignment is just a constant, and treating those as
# provisioned made the check fire on every module-level constant in the repo.
_ENV_PROVIDE = re.compile(r"""["']([A-Z][A-Z0-9_]{3,})["']\s*:\s*""")
_ENV_FILE_PROVIDE = re.compile(r"^\s*([A-Z][A-Z0-9_]{3,})\s*=", re.M)

# Ports the application expects to reach.
_URL_PORT = re.compile(r"""https?://[^\s"'<>]+?:(\d{2,5})\b""")
_PORT_ASSIGN = re.compile(r"""\b\w*port\w*\s*[:=]\s*["']?(\d{2,5})\b""", re.I)

# AWS SDK usage, which implies a permission the infrastructure must grant.
_BOTO_CLIENT = re.compile(r"""boto3\s*\.\s*(?:client|resource)\s*\(\s*["']([a-z0-9-]{2,30})["']""")
_SDK_V3 = re.compile(r"""@aws-sdk/client-([a-z0-9-]{2,30})""")
_IAM_ACTION = re.compile(r"""["']([a-z0-9-]{2,20}):([A-Za-z*][A-Za-z0-9*]*)["']""")

# Service names that appear in IAM actions under a different prefix than the
# SDK client name.
SDK_TO_IAM = {
    "dynamodb": "dynamodb", "s3": "s3", "sqs": "sqs", "sns": "sns",
    "secretsmanager": "secretsmanager", "ssm": "ssm", "kms": "kms",
    "bedrock-runtime": "bedrock", "bedrock-agent-runtime": "bedrock",
    "rds-data": "rds-data", "opensearch": "es", "opensearchserverless": "aoss",
    "neptunedata": "neptune-db", "stepfunctions": "states", "sfn": "states",
    "cloudwatch": "cloudwatch", "logs": "logs", "lambda": "lambda",
    "eventbridge": "events", "events": "events", "sts": "sts",
}


def _collect_ports(obj, acc: set[int]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "port" in str(k).lower() and isinstance(v, (int, str)):
                try:
                    acc.add(int(str(v)))
                except (TypeError, ValueError):
                    pass
            _collect_ports(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _collect_ports(v, acc)


def probe_interface(ctx: ProbeContext) -> list[Finding]:
    """Seam checks across repos in one system. Only the deterministic ones."""
    out: list[Finding] = []
    if len(ctx.repos) < 2:
        return out

    # ---- constant agreement -------------------------------------------------
    consts: dict[str, dict[str, tuple[str, Location]]] = {}
    for f in ctx.inventory.text_files():
        if f.role in ("generated", "docs", "data", "test"):
            continue
        text = _read(f)
        for m in _CONST_ASSIGN.finditer(text):
            name, val = m.group(1), m.group(2)
            if name.endswith(("_TEST", "_EXAMPLE")) or len(val) < 1:
                continue
            consts.setdefault(name, {}).setdefault(
                f.repo_id,
                (val, Location(path=f.path, start_line=_line_of(text, m.start()),
                               logical=name, repo_id=f.repo_id)),
            )

    declared = {c.get("name"): c for c in (ctx.system.get("shared_constants") or []) if isinstance(c, dict)}

    for name, per_repo in consts.items():
        if len(per_repo) < 2:
            continue
        values = {v[0] for v in per_repo.values()}
        if len(values) == 1:
            continue
        explicit = name in declared
        items = sorted(per_repo.items())
        first_repo, (_, first_loc) = items[0]
        locs = [v[1] for _, v in items]
        detail = ", ".join(f"{rid}={v[0]}" for rid, v in items)
        out.append(Finding(
            rule_id="arbiter/interface.constant-disagreement",
            title=f"`{name}` has different values in different repositories",
            dimension="interface",
            severity="high" if explicit else "medium",
            confidence="high" if explicit else "medium",
            repo_id=first_repo,
            probe="interface",
            location=first_loc,
            related=locs[1:],
            description=(
                f"{detail}. This constant is declared as a shared contract in the system manifest."
                if explicit else
                f"{detail}. The same named constant is defined with conflicting values across repositories."
            ),
            remediation="Define the value once and surface it to both sides, rather than restating it in each repository.",
            evidence=f"{name}:{detail}",
            tags=["interface", "contract"],
        ))

    # ---- environment contract both ways ------------------------------------
    infra_repos = {r.id for r in ctx.repos if r.role in ("infrastructure", "infra", "platform")}

    reads: dict[str, dict[str, Location]] = {}
    provides: dict[str, dict[str, Location]] = {}
    app_ports: dict[int, Location] = {}
    sdk_services: dict[str, Location] = {}
    granted_services: dict[str, Location] = {}

    for f in ctx.inventory.text_files():
        if f.role in ("generated", "data"):
            continue
        text = _read(f)
        if not text:
            continue
        is_infra = f.repo_id in infra_repos

        if f.role in ("source", "test", "ci", "config", "iac"):
            for m in _ENV_READ.finditer(text):
                reads.setdefault(m.group(1), {}).setdefault(
                    f.repo_id,
                    Location(path=f.path, start_line=_line_of(text, m.start()),
                             logical=m.group(1), repo_id=f.repo_id),
                )
        provider_re = _ENV_FILE_PROVIDE if f.path.endswith((".env", ".env.example")) else _ENV_PROVIDE
        for m in provider_re.finditer(text):
            provides.setdefault(m.group(1), {}).setdefault(
                f.repo_id,
                Location(path=f.path, start_line=_line_of(text, m.start()),
                         logical=m.group(1), repo_id=f.repo_id),
            )

        if not is_infra and f.role in ("source", "config"):
            for rx in (_URL_PORT, _PORT_ASSIGN):
                for m in rx.finditer(text):
                    try:
                        port = int(m.group(1))
                    except ValueError:
                        continue
                    if 1 <= port <= 65535:
                        app_ports.setdefault(
                            port,
                            Location(path=f.path, start_line=_line_of(text, m.start()), repo_id=f.repo_id),
                        )
            for rx in (_BOTO_CLIENT, _SDK_V3):
                for m in rx.finditer(text):
                    svc = SDK_TO_IAM.get(m.group(1), m.group(1))
                    sdk_services.setdefault(
                        svc,
                        Location(path=f.path, start_line=_line_of(text, m.start()), repo_id=f.repo_id),
                    )
        if is_infra:
            for m in _IAM_ACTION.finditer(text):
                # "aws:" is IAM's global condition context key namespace
                # (aws:PrincipalArn, aws:SourceIp, aws:SecureTransport, ...),
                # never a grantable action namespace. The regex can't tell an
                # Action entry from a Condition key by shape alone -- both are
                # "prefix:Suffix" strings -- so exclude the one prefix that's
                # never a real service and always a condition key.
                if m.group(1) == "aws":
                    continue
                # Keep where the grant was written, the way the sibling
                # collections above do. Only the service name was kept before,
                # so the finding had nothing to point at but the string
                # `iam:s3` and had to guess at a repository.
                granted_services.setdefault(
                    m.group(1),
                    Location(path=f.path, start_line=_line_of(text, m.start()),
                             logical=f"iam:{m.group(1)}", repo_id=f.repo_id),
                )

    # env read by someone, provided by nobody
    for name, per_repo in reads.items():
        if name in provides:
            continue
        if not infra_repos:
            continue
        loc = next(iter(per_repo.values()))
        out.append(Finding(
            rule_id="arbiter/interface.env-var-never-provided",
            title=f"`{name}` is read at runtime but nothing in the system provides it",
            dimension="interface", severity="high", confidence="medium",
            repo_id=next(iter(per_repo)), probe="interface", location=loc,
            description="The application reads this variable; no repository in the system sets it.",
            remediation="Add it to the task or function environment in the infrastructure repository, or stop reading it.",
            evidence=f"env={name}", tags=["interface", "config"],
        ))

    # env provided by infrastructure, read by nobody
    for name, per_repo in provides.items():
        if name in reads:
            continue
        infra_sites = {r: loc for r, loc in per_repo.items() if r in infra_repos}
        if not infra_sites:
            continue
        repo_id, loc = next(iter(infra_sites.items()))
        out.append(Finding(
            rule_id="arbiter/interface.env-var-provided-but-unused",
            title=f"`{name}` is provisioned but nothing in the system reads it",
            dimension="interface", severity="low", confidence="medium",
            repo_id=repo_id, probe="interface", location=loc,
            description="The infrastructure sets this variable and no application code consumes it — usually a leftover from a removed feature.",
            remediation="Remove it, or wire up the consumer that was intended to read it.",
            evidence=f"env={name}", tags=["interface", "config"],
        ))

    # ---- endpoint reachability ---------------------------------------------
    infra_ports: set[int] = set()
    for res in ctx.graph:
        if res.repo_id in infra_repos or not infra_repos:
            _collect_ports(res.properties, infra_ports)
    if infra_ports:
        for port, loc in sorted(app_ports.items()):
            if port in infra_ports or port in (80, 443):
                continue
            out.append(Finding(
                rule_id="arbiter/interface.port-not-exposed",
                title=f"Application targets port {port}, which no infrastructure resource exposes",
                dimension="interface", severity="medium", confidence="low",
                repo_id=loc.repo_id or "root", probe="interface", location=loc,
                description=(
                    f"Ports declared in the infrastructure graph: "
                    f"{', '.join(str(p) for p in sorted(infra_ports)[:12])}."
                ),
                remediation="Open the port in the security group, listener or container definition, or correct the client.",
                evidence=f"port={port}", tags=["interface", "network"],
            ))

    # ---- permission symmetry ------------------------------------------------
    if granted_services:
        for svc, loc in sorted(sdk_services.items()):
            if svc in granted_services or "*" in granted_services:
                continue
            out.append(Finding(
                rule_id="arbiter/interface.permission-not-granted",
                title=f"Application calls `{svc}` but no IAM policy in the system grants it",
                dimension="interface", severity="high", confidence="medium",
                repo_id=loc.repo_id or "root", probe="interface", location=loc,
                description=(
                    f"Services granted in infrastructure: "
                    f"{', '.join(sorted(granted_services)[:12])}."
                ),
                remediation="Add the required actions to the task or function role, scoped to the resources it touches.",
                evidence=f"service={svc}", tags=["interface", "iam"],
            ))
        for svc in sorted(set(granted_services) - set(sdk_services) - {"*"}):
            if svc in ("logs", "sts", "ecr", "xray", "cloudwatch", "kms", "ec2"):
                continue  # platform-level grants with no SDK call site
            out.append(Finding(
                rule_id="arbiter/interface.permission-unused",
                title=f"IAM grants `{svc}` but no application code calls it",
                dimension="interface", severity="low", confidence="low",
                # The repository the grant is actually written in, not the
                # alphabetically first infrastructure repository: with more
                # than one infra repo in a system that guess named the wrong
                # one whenever the grant was not in the first.
                repo_id=granted_services[svc].repo_id or "root",
                probe="interface", location=granted_services[svc],
                description="A granted permission with no observed consumer is unused privilege.",
                remediation="Remove the grant, or confirm it is used by something outside this system.",
                evidence=f"unused-service={svc}", tags=["interface", "iam", "least-privilege"],
            ))
    return out


register(Probe(
    name="interface", dimensions=["interface"], checks=6,
    run=probe_interface, multi_repo_only=True,
))


# ===========================================================================
# house rules (driven entirely by arbiter.yaml)
# ===========================================================================

def probe_house_rules(ctx: ProbeContext) -> list[Finding]:
    rules = ctx.config.get("rules") or []
    out: list[Finding] = []
    files = ctx.inventory.text_files()
    # Paths per repository rather than one flat set. Flattened, the question
    # asked was "does any repository have this path", so in a multi-repository
    # scan one repository's LICENSE satisfied the rule for all of them — and
    # the finding had no repository to name. These two rules were the only
    # probe sites in the tree that set no repo_id on the Finding at all, and
    # so reported against the `root` default whatever they had matched.
    paths_by_repo: dict[str, set[str]] = {}
    for rec in ctx.inventory.files:
        paths_by_repo.setdefault(rec.repo_id, set()).add(rec.path)

    def matching(glob_pat: str):
        import fnmatch
        return [f for f in files if fnmatch.fnmatch(f.path, glob_pat)]

    for rule in rules:
        rid = rule.get("id", "unnamed")
        rtype = rule.get("type")
        sev = rule.get("severity", "medium")

        if rtype == "file_exists":
            for pat in rule.get("paths", []):
                import fnmatch
                for repo_id, paths in sorted(paths_by_repo.items()):
                    if any(fnmatch.fnmatch(p, pat) for p in paths):
                        continue
                    out.append(Finding(
                        rule_id=f"house/{rid}",
                        title=rule.get("title", f"Required path missing: {pat}"),
                        dimension="quality", severity=sev, confidence="high",
                        repo_id=repo_id, probe="house_rules",
                        location=Location(path=pat, repo_id=repo_id),
                        description=rule.get("description", ""),
                        remediation=rule.get("remediation", ""),
                        evidence=f"missing={pat}", tags=["house-rule"],
                    ))

        elif rtype == "file_absent":
            for pat in rule.get("paths", []):
                import fnmatch
                for repo_id, paths in sorted(paths_by_repo.items()):
                    for p in sorted(paths):
                        if not fnmatch.fnmatch(p, pat):
                            continue
                        out.append(Finding(
                            rule_id=f"house/{rid}",
                            title=rule.get("title", f"Forbidden path present: {p}"),
                            dimension="quality", severity=sev, confidence="high",
                            repo_id=repo_id, probe="house_rules",
                            location=Location(path=p, repo_id=repo_id),
                            description=rule.get("description", ""),
                            remediation=rule.get("remediation", ""),
                            evidence=f"present={p}", tags=["house-rule"],
                        ))

        elif rtype == "content_match":
            pattern = re.compile(rule.get("pattern", "$^"))
            forbid = rule.get("forbid", True)
            for f in matching(rule.get("files", "**/*")):
                text = _read(f)
                hits = list(pattern.finditer(text))
                if forbid:
                    for m in hits:
                        out.append(Finding(
                            rule_id=f"house/{rid}",
                            title=rule.get("title", f"Forbidden pattern in {f.path}"),
                            dimension=rule.get("dimension", "quality"), severity=sev, confidence="high",
                            repo_id=f.repo_id, probe="house_rules",
                            location=Location(path=f.path, repo_id=f.repo_id,
                                              start_line=_line_of(text, m.start())),
                            description=rule.get("description", ""),
                            remediation=rule.get("remediation", ""),
                            evidence=m.group(0)[:80], tags=["house-rule"],
                        ))
                elif not hits:
                    out.append(Finding(
                        rule_id=f"house/{rid}",
                        title=rule.get("title", f"Required pattern missing in {f.path}"),
                        dimension=rule.get("dimension", "quality"), severity=sev, confidence="high",
                        repo_id=f.repo_id, probe="house_rules",
                        location=Location(path=f.path, repo_id=f.repo_id),
                        description=rule.get("description", ""),
                        remediation=rule.get("remediation", ""),
                        evidence="pattern-absent", tags=["house-rule"],
                    ))

        elif rtype == "reference_integrity":
            refs_spec = rule.get("refs") or {}
            anchors_spec = rule.get("anchors") or {}
            anchor_re = re.compile(anchors_spec.get("pattern", "$^"), re.M)
            anchors: set[str] = set()
            for f in matching(anchors_spec.get("files", "**/*")):
                for m in anchor_re.finditer(_read(f)):
                    val = (m.groupdict().get("anchor") or m.group(m.lastindex or 0) or "").strip()
                    anchors.add(val.lower())
                    anchors.add(re.sub(r"[^a-z0-9]+", "-", val.lower()).strip("-"))
            ref_re = re.compile(refs_spec.get("pattern", "$^"))
            for f in matching(refs_spec.get("files", "**/*")):
                text = _read(f)
                for m in ref_re.finditer(text):
                    val = (m.groupdict().get("ref") or m.group(m.lastindex or 0) or "").strip()
                    key = val.lower()
                    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")
                    if key in anchors or slug in anchors:
                        continue
                    out.append(Finding(
                        rule_id=f"house/{rid}",
                        title=rule.get("title", f"Unresolved reference `{val}`"),
                        dimension="drift", severity=sev, confidence="high",
                        repo_id=f.repo_id, probe="house_rules",
                        location=Location(path=f.path, repo_id=f.repo_id,
                                          start_line=_line_of(text, m.start())),
                        description=rule.get("description", "A pointer in the source does not resolve to any documented anchor."),
                        remediation=rule.get("remediation", "Add the anchor, or correct the pointer."),
                        evidence=f"ref={val}", tags=["house-rule", "reference"],
                    ))

        elif rtype == "metric_threshold":
            metric = rule.get("metric")
            limit = rule.get("max")
            for f in matching(rule.get("files", "**/*")):
                value = {"lines": f.lines, "bytes": f.size}.get(metric)
                if value is None or limit is None or value <= limit:
                    continue
                out.append(Finding(
                    rule_id=f"house/{rid}",
                    title=rule.get("title", f"{metric}={value} exceeds {limit} in {f.path}"),
                    dimension="quality", severity=sev, confidence="high",
                    repo_id=f.repo_id, probe="house_rules",
                    location=Location(path=f.path, repo_id=f.repo_id),
                    description=rule.get("description", ""),
                    remediation=rule.get("remediation", ""),
                    evidence=f"{metric}={value}", tags=["house-rule"],
                ))
    return out


register(Probe(name="house_rules", scope="file", dimensions=["quality", "drift"], checks=1, run=probe_house_rules))


def probe_house_rules_ast(ctx: ProbeContext) -> list[Finding]:
    """House rules expressed as tree-sitter queries.

    Structural rather than textual: `(except_clause) @hit` finds bare excepts
    wherever they are formatted, which a regex cannot promise.
    """
    import fnmatch

    from . import ast as ts

    rules = [r for r in (ctx.config.get("rules") or []) if r.get("type") == "ast_query"]
    if not rules:
        return []

    out: list[Finding] = []
    files = ctx.inventory.text_files()
    for rule in rules:
        rid = rule.get("id", "unnamed")
        langs = set(rule.get("languages") or [])
        glob_pat = rule.get("files", "**/*")
        query_src = rule.get("query", "")
        expect = rule.get("expect", "absent")   # absent = every capture is a finding
        want = rule.get("capture")
        sev = rule.get("severity", "medium")
        if not query_src:
            continue

        for f in files:
            if langs and f.language not in langs:
                continue
            if not ts.ts_name(f.language):
                continue
            if glob_pat != "**/*" and not fnmatch.fnmatch(f.path, glob_pat):
                continue
            parsed = ts.parse_file(f.abspath, f.language)
            if parsed is None:
                continue
            try:
                caps = ts.run_query(f.language, query_src, parsed)
            except ValueError as exc:
                raise RuntimeError(f"house rule '{rid}': {exc}") from exc
            if want:
                caps = [c for c in caps if c["capture"] == want]

            if expect == "present":
                if not caps:
                    out.append(Finding(
                        rule_id=f"house/{rid}",
                        title=rule.get("title", f"Required structure missing in {f.path}"),
                        dimension=rule.get("dimension", "quality"), severity=sev,
                        confidence="high", repo_id=f.repo_id, probe="house_rules_ast",
                        location=Location(path=f.path, repo_id=f.repo_id),
                        description=rule.get("description", ""),
                        remediation=rule.get("remediation", ""),
                        evidence=f"query-absent:{rid}", tags=["house-rule", "ast"],
                    ))
                continue

            for c in caps:
                snippet = " ".join(c["text"].split())[:80]
                out.append(Finding(
                    rule_id=f"house/{rid}",
                    title=rule.get("title", f"Disallowed `{c['type']}` in {f.path}"),
                    dimension=rule.get("dimension", "quality"), severity=sev,
                    confidence="high", repo_id=f.repo_id, probe="house_rules_ast",
                    location=Location(path=f.path, start_line=c["start_line"],
                                      end_line=c["end_line"], repo_id=f.repo_id),
                    description=rule.get("description", ""),
                    remediation=rule.get("remediation", ""),
                    evidence=f"{c['type']}:{snippet}", tags=["house-rule", "ast"],
                ))
    return out


register(Probe(
    name="house_rules_ast", scope="file", cacheable=True, dimensions=["quality", "security"],
    checks=1, run=probe_house_rules_ast, modules=["tree_sitter_language_pack"],
))


# ===========================================================================
# governance: the failure ledger against the change (REQ-038)
# ===========================================================================
#
# A repository that keeps a failure ledger has written down, per defect, which
# files it lived in and which test guards against its return. That is a
# contract the next change can break in two ways without any test failing:
# touch an affected file of a still-open failure without touching its guard,
# or let the guard of a fixed failure quietly disappear. Neither is visible
# to a test runner, because the first is a test that was not run and the
# second is a test that no longer exists.

_LEDGER = ".ai/failures/failure-ledger.json"
_OPEN_STATUSES = ("open", "mitigated")


def _read_ledger(repo_path: str) -> list[dict]:
    import json
    try:
        data = json.loads((Path(repo_path) / _LEDGER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("failures") if isinstance(data, dict) else None
    return [e for e in (entries or []) if isinstance(e, dict)]


def _test_file(ref: str) -> str:
    """The file half of `tests/test_x.py::test_y`, as a posix path."""
    return str(ref).split("::", 1)[0].strip().replace("\\", "/")


def _has_failure_ledger(ctx: ProbeContext) -> tuple[bool, str]:
    if any((Path(r.path) / _LEDGER).is_file() for r in ctx.repos):
        return True, ""
    return False, f"no failure ledger at {_LEDGER}; nothing to govern"


def _open_failures_untested(repo, entries: list[dict], changed: set[str]) -> list[Finding]:
    out: list[Finding] = []
    for e in entries:
        if str(e.get("status") or "").lower() not in _OPEN_STATUSES:
            continue
        fid = str(e.get("id") or "?")
        tests = [_test_file(t) for t in (e.get("regression_tests") or [])]
        if any(t in changed for t in tests):
            # The guard moved with the change; that is the contract honoured.
            continue
        for raw in e.get("affected") or []:
            path = str(raw).replace("\\", "/")
            if path not in changed:
                continue
            out.append(Finding(
                rule_id="arbiter/governance.open-failure-untested",
                title=f"{fid} is {e.get('status')} and {path} changed without its regression test",
                dimension="drift", severity="medium", confidence="high",
                repo_id=repo.id, probe="governance",
                location=Location(path=path, logical=fid, repo_id=repo.id),
                description=(f"The failure ledger lists {path} as affected by {fid}, which is "
                             f"still {e.get('status')}. This change touches the file and none of "
                             f"the entry's regression tests ({', '.join(tests) or 'none recorded'})."),
                remediation=(f"Change or add the regression test named by {fid} in the same "
                             f"change, or close the entry if the failure is fixed."),
                evidence=str(e.get("title") or fid),
                tags=["governance"],
            ))
    return out


def _regression_tests_missing(repo, entries: list[dict]) -> list[Finding]:
    out: list[Finding] = []
    for e in entries:
        if str(e.get("status") or "").lower() != "fixed":
            continue
        fid = str(e.get("id") or "?")
        for ref in e.get("regression_tests") or []:
            file_part = _test_file(ref)
            if not file_part or (Path(repo.path) / file_part).is_file():
                continue
            out.append(Finding(
                rule_id="arbiter/governance.regression-test-missing",
                title=f"{fid} is fixed but its regression test file {file_part} is gone",
                dimension="drift", severity="medium", confidence="high",
                repo_id=repo.id, probe="governance",
                location=Location(path=_LEDGER, logical=fid, repo_id=repo.id),
                description=(f"{fid} ({e.get('title') or 'untitled'}) names {ref} as the test "
                             f"that keeps it fixed, and {file_part} is not in the tree. "
                             "Nothing guards against the failure returning."),
                remediation=("Restore or rename the test and point regression_tests at it, "
                             "or record a no_test_reason in the ledger entry."),
                evidence=str(ref),
                tags=["governance"],
            ))
    return out


def probe_governance(ctx: ProbeContext) -> list[Finding]:
    """The failure ledger, held against the change.

    Two checks. `open-failure-untested` needs the change set and is silent in
    a full scan: the question is about what this change touched. `regression-
    test-missing` is a fact about the tree and runs in both modes. The probe
    is change-scoped because it reads only the ledger and the changed paths,
    so a partial scan is a complete answer rather than a wrong one.
    """
    out: list[Finding] = []
    for repo in ctx.repos:
        entries = _read_ledger(repo.path)
        if not entries:
            continue
        changed = {str(p).replace("\\", "/") for p in ctx.changed.get(repo.id, set())}
        if changed:
            out.extend(_open_failures_untested(repo, entries, changed))
        out.extend(_regression_tests_missing(repo, entries))
    return out


register(Probe(
    name="governance", scope="change", dimensions=["drift"], checks=2,
    run=probe_governance, applies=_has_failure_ledger,
    scope_reason="this check reads the failure ledger and the changed paths",
))


def probe_by_name(name: str) -> Probe | None:
    for p in REGISTRY:
        if p.name == name:
            return p
    return None


# The judgement probe is defined in judgement.py so that the only module able
# to make a network call stays separate and easy to audit. It registers here so
# that it always counts against coverage: an unconfigured provider must show up
# as not-assessed, and a probe that never registers never shows up at all.
try:
    from .judgement import register_judgement as _register_judgement
    _register_judgement()
except Exception:  # noqa: BLE001 - an optional probe module must not break the registry import
    pass

# Assurance asks whether the CHECKING is switched on, rather than whether the
# code is sound. Separate module, separate dimension, weight zero.
try:
    from .assurance import register_assurance as _register_assurance
    _register_assurance()
except Exception:  # noqa: BLE001 - an optional probe module must not break the registry import
    pass


# Defects characteristic of machine-authored code. Decidable ones only -- a
# model is the worst available reviewer for its own hallucinated imports,
# because it generated them precisely because they looked plausible.
try:
    from .authored import register_authored as _register_authored
    _register_authored()
except Exception:  # noqa: BLE001 - an optional probe module must not break the registry import
    pass


# Contracts declared in one artifact and implemented in another: an OpenAPI
# document against the routes actually registered, a migration against the code
# that queries the column. The same idea as the cross-repo seam checks, moved
# inside a single repository.
try:
    from .contract import register_contract as _register_contract
    _register_contract()
except Exception:  # noqa: BLE001 - an optional probe module must not break the registry import
    pass
