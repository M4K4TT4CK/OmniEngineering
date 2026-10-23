"""App-level defects found in AI-written services (ARB-083).

## Why this is its own probe

The language packs see one file and one function. Three defects a reviewer
labelled again and again in model-written services (fixtures/ai-corpus) need
more than that: whether the *application* authenticates anyone, whether it
can send mail, and whether a map a request fills is ever emptied anywhere
else in the file. Each is a judgement about relationships between files or
functions, so the probe is repository-scoped.

## What is checked (Python, TypeScript/JavaScript, Go, Java, Ruby)

1. UNAUTHENTICATED DESTRUCTIVE ROUTE (`aiapp.unauthenticated-destructive-route`).
   A DELETE route, or a route under `/admin`, `/purge`, `/reset`, `/wipe`,
   `/flush`, in either of two situations:
   * the repository has no sign of authentication anywhere in its source
     (no `login_required`, `requireAuth`, `Bearer`, JWT, passport, API key,
     `current_user`, `@PreAuthorize`, HMAC signature check, ...): anyone who
     can reach the port can delete what the service stores; or
   * other routes in the same file pass an auth middleware or decorator
     inline (`a.authed(h)`, `requireAdmin`, `@login_required`,
     `Depends(current_user)`, `@PreAuthorize`) and this one does not.

2. OPEN MAIL RELAY (`aiapp.open-mail-relay`). A write route (POST, PUT,
   PATCH, or an `HttpServer` context) in a repository that sends mail
   (smtplib, net/smtp, nodemailer, Jakarta/JavaMail, Net::SMTP, ActionMailer)
   and has no sign of authentication anywhere: whoever reaches the port can
   send mail through the service's SMTP account.

3. STORE THAT ONLY GROWS (`aiapp.store-never-evicted`). A map held by an
   object or module (`self._jobs`, `q.jobs`, `@jobs`, `this.jobs`, a Java
   `Map` field) that is filled with freshly generated ids (the file calls
   uuid4, randomUUID, SecureRandom, crypto/rand, ...) and that nothing in
   the file outside the inserting function ever removes from, clears, bounds
   by size, or expires. A delete in the inserting function itself (rolling
   back a rejected submit) is not eviction. Reported at the insert.

## What this is not

Authentication is recognised by name. Auth done outside the repository (an
API gateway, a service mesh, a reverse proxy) is invisible here, which is why
both route findings are advisory, medium confidence, and say so. The store
check reads one file; a cleanup job in another file is not seen.
"""
from __future__ import annotations

import re

from .core import Finding, Location
from .probes import ProbeContext, _line_of, _read

_LANGS = {"python", "typescript", "javascript", "go", "java", "ruby"}
_SKIP_ROLES = {"generated", "docs", "data", "test", "config", "ci", "iac"}

# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

# Any of these in a repository's source means it authenticates someone.
# Outbound credentials (smtp.PlainAuth, PasswordAuthentication,
# `authentication: :plain`, nodemailer's `auth:`) deliberately do not match.
_AUTH_SIGNAL = re.compile(
    r"\blogin_required\b|\brequire_?(?:auth|login|user|admin|token|session)\w*"
    r"|\bauthenticate(?:d|s|_\w+)?\b|\bauthori[sz](?:e|ed|es|ation|ing|er)\w*|\bAuthori[sz]ation\b"
    r"|\bBearer\b|\bjwt\b|\bjsonwebtoken\b|\bpassport\b|\bapi[_-]?key\b|x-api-key"
    r"|\bcurrent_user\b|\bcurrentUser\b|@PreAuthorize|@Secured|@RolesAllowed|SecurityFilterChain"
    r"|http_basic_authenticate|\bBasicAuth\b|\bbasic_auth\b|\bverify_password\b|\bcheck_password\b"
    r"|\bpassword_hash\b|\bbcrypt\b|\bhmac\b|\bsession\[|\bOAuth|\bis_authenticated\b"
    r"|\bget_current_user\b|\bHTTPBearer\b|\bOAuth2PasswordBearer\b",
    re.IGNORECASE,
)

_MAIL_SIGNAL = re.compile(
    r"\bsmtplib\b|\"net/smtp\"|\bnodemailer\b|\bjakarta\.mail\b|\bjavax\.mail\b|\bNet::SMTP\b"
    r"|\bActionMailer\b|\bdelivery_method\b|\bMail\.new\b|\bsmtp\.SendMail\b|\bsendgrid\b|\bmailgun\b"
)

# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

_WRITE = {"post", "put", "patch", "delete", "any"}
_INLINE_GUARD_LANGS = {"go", "typescript", "javascript", "python"}
_DESTRUCTIVE_PATH = re.compile(r"(?i)/(?:admin|purge|wipe|flush)(?:/|$|\b)")
# An inline middleware or wrapper that reads as access control.
_AUTH_WRAPPER = re.compile(
    r"(?i)\b\w*(?:auth|admin|login|jwt|token|permission|role|protect|guard|session|secur|signed|current_?user|deny|acl|polic|verif|user_?id)\w*\b"
    r"|\brequire\w+")

# (language, regex). Each yields method, path and the rest of the line.
_ROUTES: list[tuple[set[str], re.Pattern]] = [
    # net/http 1.22 patterns: mux.HandleFunc("DELETE /files/{name}", s.remove)
    ({"go"}, re.compile(r"\.(?:HandleFunc|Handle)\(\s*\"(?:(?P<m>GET|POST|PUT|PATCH|DELETE)\s+)?(?P<p>/[^\"]*)\"\s*,(?P<rest>.*)$", re.M)),
    # gin / echo / chi / gorilla-style method helpers
    ({"go"}, re.compile(r"\b\w+\.(?P<m>GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete)\(\s*\"(?P<p>/[^\"]*)\"\s*,(?P<rest>.*)$", re.M)),
    # Flask / FastAPI / Starlette decorators
    ({"python"}, re.compile(r"^[ \t]*@\w+\.(?P<m>get|post|put|patch|delete)\(\s*[rf]?[\"'](?P<p>/[^\"']*)[\"'](?P<rest>.*)$", re.M)),
    ({"python"}, re.compile(r"^[ \t]*@\w+\.route\(\s*[rf]?[\"'](?P<p>/[^\"']*)[\"'](?P<rest>.*)$", re.M)),
    # Express / Koa routers and Javalin
    ({"typescript", "javascript", "java"}, re.compile(
        r"\b(?:app|router|api|server|routes|r|\w+Router|\w+_router)\.(?P<m>get|post|put|patch|delete)\(\s*[\"'`](?P<p>/[^\"'`]*)[\"'`]\s*,?(?P<rest>.*)$", re.M)),
    # bare node:http dispatch: if (req.method === 'POST' && url.pathname === '/jobs')
    ({"typescript", "javascript"}, re.compile(
        r"\breq(?:uest)?\.method\s*===?\s*[\"'](?P<m>POST|PUT|PATCH|DELETE)[\"'](?P<rest>[^\n]*?[\"'`](?P<p>/[^\"'`]*)[\"'`])?", re.M)),
    # Spring
    ({"java"}, re.compile(r"^[ \t]*@(?P<m>Get|Post|Put|Patch|Delete)Mapping\b(?:\(\s*(?:value\s*=\s*|path\s*=\s*)?\"?(?P<p>/[^\"]*)?\"?)?(?P<rest>.*)$", re.M)),
    # com.sun.net.httpserver: the method is decided inside the handler
    ({"java"}, re.compile(r"\.createContext\(\s*\"(?P<p>/[^\"]*)\"(?P<rest>.*)$", re.M)),
    # Sinatra
    ({"ruby"}, re.compile(r"^[ \t]*(?P<m>get|post|put|patch|delete)\s+[\"'](?P<p>/[^\"']*)[\"'](?P<rest>.*)$", re.M)),
]


def _route_method(lang: str, m: re.Match) -> str:
    gd = m.groupdict()
    meth = (gd.get("m") or "").lower()
    if meth:
        return meth
    if "createContext" in m.group(0):
        return "any"
    if lang == "python" and ".route(" in m.group(0):
        methods = re.search(r"methods\s*=\s*[\[(]([^\])]*)", gd.get("rest") or "")
        if not methods:
            return "get"
        found = [x.lower() for x in re.findall(r"[\"'](\w+)[\"']", methods.group(1))]
        for pref in ("delete", "post", "put", "patch"):
            if pref in found:
                return pref
        return "get"
    return "get" if lang == "go" else "any"


def _decorator_block(text: str, start: int) -> str:
    """The decorators above and the `def` line below a Python route
    decorator, or the annotations around a Spring mapping: where an auth
    decorator or `Depends(...)` would sit."""
    lines = text[:start].splitlines()[-6:]
    after = text[start:].splitlines()[:6]
    block = []
    for ln in reversed(lines):
        if ln.strip().startswith("@"):
            block.append(ln)
        else:
            break
    for ln in after:
        block.append(ln)
        if ln.rstrip().endswith((":", "{")) and not ln.lstrip().startswith("@"):
            break
    return "\n".join(block)


def _routes(f, text: str) -> list[dict]:
    out = []
    seen: set[int] = set()
    for langs, rx in _ROUTES:
        if f.language not in langs:
            continue
        for m in rx.finditer(text):
            line = _line_of(text, m.start())
            if line in seen:
                continue
            seen.add(line)
            rest = m.groupdict().get("rest") or ""
            path = m.groupdict().get("p") or ""
            context = rest
            if f.language in ("python", "java") and m.group(0).lstrip().startswith("@"):
                block = _decorator_block(text, m.start())
                if f.language == "python":
                    # Other decorators, and what the signature's Depends()/
                    # Security() call; not names (`def create_session`).
                    decos = [ln for ln in block.splitlines()
                             if ln.lstrip().startswith("@") and ln.strip() != m.group(0).strip()]
                    deps = re.findall(r"\b(?:Depends|Security)\(([^()]*(?:\([^()]*\))?[^()]*)\)", block)
                    context = "\n".join(decos + deps + [rest])
                else:
                    context = block
            # Trailing comments say nothing about the code (and a comment
            # saying "unauthenticated" is not a guard).
            context = "\n".join(re.sub(r"\s(?://|#).*$", "", ln) for ln in context.splitlines())
            guarded = bool(_AUTH_WRAPPER.search(context.replace(path, "")))
            out.append({"line": line, "method": _route_method(f.language, m), "path": path,
                        "text": " ".join(m.group(0).split())[:100], "guarded": guarded})
    return out


# --------------------------------------------------------------------------
# Stores that only grow
# --------------------------------------------------------------------------

_FRESH_ID = re.compile(
    r"\buuid4\b|\buuid1\b|\buuid\.uuid7\b|\brandomUUID\b|\bSecureRandom\b|\bcrypto/rand\"|\brand\.Read\("
    r"|\brandomBytes\b|\bnanoid\b|\buuid\.New(?:String)?\(|\bulid\b|\btoken_hex\b|\btoken_urlsafe\b"
    r"|\bsecrets\.token_\w+|\bUUID\.randomUUID\b|\bcuid\b|\bv4 as uuid|\buuidv4\b")
# The key is the id of the object being stored (`job.id`, `job.ID`,
# `job.getId()`): one entry per item created, not a bounded key space.
_KEY_IS_ID = re.compile(r"^[A-Za-z_]\w*\.(?:id|ID|Id|uuid|UUID|getId\(\)|id\(\))$")
_LIFETIME_WORDS = re.compile(
    r"(?i:\b\w*(?:evict|expire|expiry|prune|sweep|purge|cleanup|clean_up|reap|retention)\w*\s*\()"
    r"|(?<![A-Za-z])(?:ttl|TTL)(?![a-z])|Ttl\b"
    r"|removeEldestEntry|\bCaffeine\b|\bCacheBuilder\b|\bcachetools\b|\bTTLCache\b|\bLRUCache\b|\blru_cache\b"
    r"|\bLruCache\b|\bExpiringMap\b|\blru-cache\b")

# Per language: insert regex (field f, key k), declaration regex (field f),
# and the removal / bound templates for one field name.
_STORE = {
    "python": dict(
        insert=re.compile(r"\bself\.(?P<f>_?\w+)\[(?P<k>[^\]\n]+)\]\s*=(?!=)"),
        decl=r"\bself\.{f}\s*(?::[^=\n]+)?=\s*(?:\{{\s*\}}|dict\(\)|OrderedDict\(\)|defaultdict\()",
        remove=r"\bdel\s+self\.{f}\[|\bself\.{f}\.(?:pop|popitem|clear)\(|\bself\.{f}\s*=(?!=)",
        bound=r"\blen\(\s*self\.{f}\s*\)\s*(?:>=|>|==)",
    ),
    "go": dict(
        insert=re.compile(r"\b\w+\.(?P<f>\w+)\[(?P<k>[^\]\n]+)\]\s*=(?!=)"),
        decl=r"(?m)^\s*{f}\s+map\[|\b{f}\s*:\s*(?:make\(\s*)?map\[",
        remove=r"\bdelete\(\s*\w+\.{f}\s*,|\b\w+\.{f}\s*=(?!=)\s*(?:make|map)|\bclear\(\s*\w+\.{f}\s*\)",
        bound=r"\blen\(\s*\w+\.{f}\s*\)\s*(?:>=|>|==)",
    ),
    "ruby": dict(
        insert=re.compile(r"@(?P<f>\w+)\[(?P<k>[^\]\n]+)\]\s*=(?!=)"),
        decl=r"@{f}\s*=\s*(?:\{{\s*\}}|Hash\.new|Concurrent::(?:Map|Hash)\.new)",
        remove=r"@{f}\.(?:delete|delete_if|reject!|select!|keep_if|filter!|clear|shift)\b|@{f}\s*=(?!=)",
        bound=r"@{f}\.(?:size|length|count)\s*(?:>=|>|==)",
    ),
    "typescript": dict(
        insert=re.compile(r"\bthis\.(?P<f>#?\w+)\.set\(\s*(?P<k>[^,\n]+),"),
        decl=r"\b{f}\s*(?::[^=\n;]+)?=\s*new\s+Map\b",
        remove=r"\bthis\.{f}\.(?:delete|clear)\(|\bthis\.{f}\s*=(?!=)",
        bound=r"\bthis\.{f}\.size\s*(?:>=|>|===|==)",
    ),
    "java": dict(
        insert=re.compile(r"(?:\bthis\.)?\b(?P<f>\w+)\.(?:put|putIfAbsent)\(\s*(?P<k>[^,\n]+),"),
        decl=r"(?m)^[ \t]*(?:(?:private|protected|public|static|final)\s+)+[\w.]*Map<[^;=\n]*>\s+{f}\s*=\s*new\s+\w*Map\b",
        remove=r"\b{f}\.(?:remove|clear|removeIf|compute|computeIfPresent)\(|\b{f}\.(?:entrySet|values|keySet)\(\)\.(?:removeIf|remove|clear|retainAll)\(",
        bound=r"\b{f}\.size\(\)\s*(?:>=|>|==)",
    ),
}
_STORE["javascript"] = _STORE["typescript"]


def _function_ranges(f) -> list[tuple[int, int]]:
    from . import ast as ts
    try:
        return [(fn.start_line, fn.end_line) for fn in ts.functions(f.abspath, f.language)]
    except Exception:  # noqa: BLE001 - no grammar: treat the file as one function
        return []


def _innermost(ranges: list[tuple[int, int]], line: int) -> tuple[int, int] | None:
    best = None
    for lo, hi in ranges:
        if lo <= line <= hi and (best is None or hi - lo < best[1] - best[0]):
            best = (lo, hi)
    return best


def _stores(f, text: str) -> list[Finding]:
    spec = _STORE.get(f.language)
    if spec is None or not _FRESH_ID.search(text) or _LIFETIME_WORDS.search(text):
        return []
    out: list[Finding] = []
    done: set[str] = set()
    ranges = None
    for m in spec["insert"].finditer(text):
        field, key = m.group("f"), m.group("k").strip()
        if field in done or not _KEY_IS_ID.search(key):
            continue
        fe = re.escape(field)
        if not re.search(spec["decl"].format(f=fe), text):
            continue
        if re.search(spec["bound"].format(f=fe), text):
            done.add(field)
            continue
        if ranges is None:
            ranges = _function_ranges(f)
        line = _line_of(text, m.start())
        home = _innermost(ranges, line)
        evicted = False
        for r in re.finditer(spec["remove"].format(f=fe), text):
            rl = _line_of(text, r.start())
            where = _innermost(ranges, rl)
            # A removal in the inserting function (rolling back a rejected
            # submit) or in the constructor is not eviction.
            if where == home:
                continue
            if where is not None and re.search(
                    r"(?i)\b(__init__|initialize|constructor|New\w*|reset_for_tests?)\b",
                    text.splitlines()[where[0] - 1]):
                continue
            evicted = True
            break
        done.add(field)
        if evicted:
            continue
        snippet = " ".join(m.group(0).split())[:80]
        out.append(Finding(
            rule_id="arbiter/aiapp.store-never-evicted",
            title=f"`{field}` gains an entry per submitted item and nothing ever removes one",
            dimension="quality", severity="medium", confidence="medium",
            repo_id=f.repo_id, probe="aiapp",
            location=Location(path=f.path, start_line=line, repo_id=f.repo_id),
            description=(
                f"`{snippet}` stores each new item, keyed by a freshly generated id, in a map "
                "this object keeps for its lifetime. Nothing in the file outside the inserting "
                "function deletes from it, clears it, caps its size or expires entries, so "
                "memory grows with every request until the process is killed. A client that "
                "submits in a loop gets there quickly."),
            remediation=("Evict finished entries after a retention period (a TTL sweep or an "
                         "expiring cache), cap the map and refuse new work when full, or keep "
                         "results in an external store."),
            evidence=f"store-never-evicted:{f.path}:{field}",
            controls=["NIST-800-218:PW.5"], cwe=["CWE-770", "CWE-401"],
            tags=["unbounded-memory", "ai-code"],
        ))
    return out


# --------------------------------------------------------------------------
# The probe
# --------------------------------------------------------------------------

def _route_finding(rule: str, f, r: dict, title: str, description: str, remediation: str,
                   cwe: list[str]) -> Finding:
    return Finding(
        rule_id=rule, title=title,
        dimension="security", severity="medium", confidence="medium",
        repo_id=f.repo_id, probe="aiapp",
        location=Location(path=f.path, start_line=r["line"], repo_id=f.repo_id),
        description=description, remediation=remediation,
        evidence=f"{rule.rsplit('.', 1)[1]}:{r['method']}:{r['path'] or r['text']}",
        controls=["NIST-800-218:PW.1", "NIST-800-53r5:AC-3"], cwe=cwe,
        tags=["missing-authn", "ai-code"],
    )


def probe_aiapp(ctx: ProbeContext) -> list[Finding]:
    out: list[Finding] = []
    by_repo: dict[str, list] = {}
    for f in ctx.inventory.text_files():
        if f.language in _LANGS and f.role not in _SKIP_ROLES:
            by_repo.setdefault(f.repo_id, []).append(f)
    for repo_id, files in by_repo.items():
        texts = {f.path: _read(f) for f in files}
        has_auth = any(_AUTH_SIGNAL.search(t) for t in texts.values() if t)
        sends_mail = any(_MAIL_SIGNAL.search(t) for t in texts.values() if t)
        for f in files:
            text = texts.get(f.path) or ""
            if not text:
                continue
            out.extend(_stores(f, text))
            routes = _routes(f, text)
            if not routes:
                continue
            file_guards = any(r["guarded"] for r in routes)
            for r in routes:
                destructive = r["method"] == "delete" or (
                    r["method"] != "get" and bool(_DESTRUCTIVE_PATH.search(r["path"])))
                # Java and Ruby services usually protect routes globally (a
                # Spring SecurityFilterChain, a Sinatra/Rails `before` filter),
                # so a mapping without its own annotation proves nothing there.
                mixed = file_guards and not r["guarded"] and f.language in _INLINE_GUARD_LANGS
                if destructive and not r["guarded"] and (not has_auth or mixed):
                    why = ("Nothing in this repository authenticates a caller"
                           if not has_auth else
                           "Other routes in this file pass an auth middleware or decorator; this one does not")
                    out.append(_route_finding(
                        "arbiter/aiapp.unauthenticated-destructive-route", f, r,
                        f"`{r['text'][:60]}` deletes or administers with no authentication",
                        (f"{why}, so anyone who can reach the service can call this "
                         "destructive endpoint and delete or reset what it stores. Auth applied "
                         "outside the repository (a gateway, a proxy) is invisible to this "
                         "check: if that is how the service is protected, say so where the "
                         "route is registered."),
                        "Require an authenticated caller (and ownership or an admin role) on this route before it reaches the handler.",
                        ["CWE-306", "CWE-862"]))
                elif (sends_mail and not has_auth and not r["guarded"]
                      and r["method"] in _WRITE and r["method"] != "delete"):
                    out.append(_route_finding(
                        "arbiter/aiapp.open-mail-relay", f, r,
                        f"`{r['text'][:60]}` is unauthenticated in a service that sends mail",
                        ("This service sends mail through its configured SMTP account and "
                         "nothing in the repository authenticates a caller. Anyone who can reach "
                         "this endpoint can make it send messages, recipients and content their "
                         "choice where the request supplies them: an open relay for spam and "
                         "phishing under the service's sender reputation."),
                        "Authenticate callers of every endpoint that can trigger mail, and fix or allowlist recipients server-side.",
                        ["CWE-306"]))
    return out


def register_aiapp() -> None:
    from .probes import Probe, register
    register(Probe(
        name="aiapp",
        dimensions=["security", "quality"],
        checks=3,
        stacks=None,
        run=probe_aiapp,
        scope="repo",
        scope_reason="whether an application authenticates or sends mail is read across all its files",
    ))

