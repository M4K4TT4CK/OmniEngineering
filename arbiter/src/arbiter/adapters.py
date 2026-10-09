"""Declarative adapters for external analyzers.

An adapter is a TOML manifest: how to invoke a tool, how to parse it, and how
to map its output onto Finding. No Python is written per tool, which is what
keeps the long tail of languages tractable.

A tool that is not installed is not a failure. The adapter reports `skipped`
with the missing binary named, and the coverage figure drops accordingly.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import Finding, Location
from .probes import Probe, ProbeContext, register

PACKS = Path(__file__).parent / "packs" / "adapters"


def _toml_loads(text: str) -> dict:
    """Parse TOML with the standard library where it has a parser (3.11+)
    and with `tomli`, the same code published separately, below that. This
    is the one place either module is imported, so Python 3.10 support is
    decided here and nowhere else (REQ-040)."""
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
        import tomli as tomllib  # type: ignore[no-redef] - the 3.10 fallback rebinds the same name on purpose
    return tomllib.loads(text)


def cache_dir() -> Path:
    """Where an adapter's own fetched-at-install-time content lives.

    Not the tool binary (that goes wherever pip/the OS puts it) and not
    `{workdir}` (that's the thing being scanned) -- this is for content an
    adapter needs alongside the binary, such as semgrep's rules, fetched once
    by `tools/install_tools.sh` rather than over the network on every scan.
    `ARBITER_CACHE_DIR` overrides it for a machine that wants it elsewhere;
    the default sits under the operator's home directory on every platform
    rather than following each OS's own convention, because one path that
    bash, PowerShell and Python all agree on beats a "correct" one that three
    scripts each compute differently.
    """
    base = os.environ.get("ARBITER_CACHE_DIR")
    return Path(base) if base else Path.home() / ".cache" / "arbiter"


# ---------------------------------------------------------------------------
# Tool-version memo (ARB-045)
# ---------------------------------------------------------------------------

VERSION_MEMO_FILE = "tool-versions.json"
VERSION_MEMO_SCHEMA = 1
_SEP = "\x1f"


def version_memo_path() -> Path:
    return cache_dir() / VERSION_MEMO_FILE


def _version_stamp(version_argv: list[str]) -> list[list]:
    """What the memo is keyed on: the path, mtime and size of the resolved
    executable, and of any other argument that is itself a file.

    The second part is for a tool invoked through an interpreter
    (`python script.py --version`): the interpreter is argv[0], but the
    version is the script's, and replacing the script must re-probe. A
    manifest that names a bare binary gets one stamp, the binary's.
    """
    stamp: list[list] = []
    for i, arg in enumerate(version_argv):
        if i and not os.path.isabs(arg):
            continue
        try:
            st = os.stat(arg)
        except OSError:
            if i == 0:
                return []
            continue
        if i and not os.path.isfile(arg):
            continue
        stamp.append([arg, st.st_mtime_ns, st.st_size])
    return stamp


def _read_version_memo() -> dict:
    """A missing, corrupt or unreadable memo is an empty one: the cost is one
    probe per tool, never an error."""
    try:
        doc = json.loads(version_memo_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("schema_version") != VERSION_MEMO_SCHEMA:
        return {}
    tools = doc.get("tools")
    return tools if isinstance(tools, dict) else {}


def _write_version_memo(memo_key: str, entry: dict) -> bool:
    """Read-merge-write the memo whole, to a temporary name and then renamed
    into place, so a concurrent reader sees the old file or the new one. A
    directory that cannot be written costs nothing but the next probe."""
    tools = _read_version_memo()
    tools[memo_key] = entry
    path = version_memo_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tool-versions-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"schema_version": VERSION_MEMO_SCHEMA, "tools": tools}, fh,
                          sort_keys=True, indent=1)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Minimal selector: $, $.a.b, $.a[0].b, trailing [*] to fan out
# ---------------------------------------------------------------------------

def select(doc: Any, expr: str) -> list[Any]:
    if not expr or expr == "$":
        return doc if isinstance(doc, list) else [doc]
    e = expr[2:] if expr.startswith("$.") else (expr[1:] if expr.startswith("$") else expr)
    cur: Any = doc
    fan = e.endswith("[*]")
    if fan:
        e = e[:-3]
    for part in [p for p in re.split(r"\.(?![^\[]*\])", e) if p]:
        m = re.fullmatch(r"([^\[]*)\[(\d+)\]", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if key:
                cur = cur.get(key) if isinstance(cur, dict) else None
            if isinstance(cur, list) and idx < len(cur):
                cur = cur[idx]
            else:
                return []
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            # A tool (checkov, across multiple detected frameworks) can emit a
            # list of sibling result objects instead of one. Fan the key
            # lookup across them and flatten, rather than failing the whole
            # path because *a* list turned up where a dict was expected.
            nxt: list[Any] = []
            for item in cur:
                if not isinstance(item, dict):
                    continue
                v = item.get(part)
                if isinstance(v, list):
                    nxt.extend(v)
                elif v is not None:
                    nxt.append(v)
            cur = nxt
        else:
            return []
        if cur is None:
            return []
    if fan:
        return cur if isinstance(cur, list) else []
    return cur if isinstance(cur, list) else [cur]


def dig(obj: Any, path: str, default: Any = None) -> Any:
    if not path:
        return default
    cur = obj
    for part in path.split("."):
        m = re.fullmatch(r"([^\[]*)\[(\d+)\]", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if key:
                cur = cur.get(key) if isinstance(cur, dict) else None
            if isinstance(cur, list) and idx < len(cur):
                cur = cur[idx]
            else:
                return default
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return default
        if cur is None:
            return default
    return cur


@dataclass
class Adapter:
    name: str
    dimensions: list[str] = field(default_factory=lambda: ["quality"])
    stacks: list[str] | None = None
    binaries: list[str] = field(default_factory=list)
    network: bool = False
    timeout: int = 300
    argv: list[str] = field(default_factory=list)
    cwd: str = "{workdir}"
    parse: str = "json"
    ok_exit: list[int] = field(default_factory=lambda: [0, 1])
    checks: int = 10
    mapping: dict = field(default_factory=dict)
    version_argv: list[str] = field(default_factory=list)
    # Declared in the manifest, never inherited. Every shipped adapter says
    # "repo", and that is a statement about evidence rather than about the
    # tool: none of these has been measured for subset-exactness, so none may
    # claim it. A manifest that omits the key still gets "repo", because the
    # conservative answer is the only safe one to assume on someone's behalf.
    scope: str = "repo"
    raw: dict = field(default_factory=dict)

    # -- lifecycle ---------------------------------------------------------
    def missing_binaries(self) -> list[str]:
        return [b for b in self.binaries if shutil.which(b) is None]

    def tool_version(self) -> str:
        """The analyzer's own version string, probed once per installed binary.

        Every `arbiter` invocation registers every adapter, and registering
        meant running each tool's `--version`: 27.5 s on one machine, paid
        even by a partial scan that never runs an adapter (ARB-045). The
        answer only changes when the binary does, so it is memoised at
        `cache_dir()/tool-versions.json` under the resolved executable's
        path, mtime and size (see `_version_stamp`). A binary that is not
        installed is never probed: there is nothing to ask. `--no-cache`
        leaves this alone -- it is not a result cache -- and
        `ARBITER_NO_VERSION_MEMO=1` switches it off.
        """
        if not self.version_argv:
            return ""
        version_argv = list(self.version_argv)
        resolved = shutil.which(version_argv[0])
        if resolved is None:
            return ""
        version_argv[0] = resolved
        memo_on = os.environ.get("ARBITER_NO_VERSION_MEMO", "") != "1"
        stamp = _version_stamp(version_argv) if memo_on else []
        memo_key = _SEP.join(version_argv)
        if memo_on:
            entry = _read_version_memo().get(memo_key)
            if isinstance(entry, dict) and entry.get("stamp") == stamp \
                    and isinstance(entry.get("version"), str):
                return entry["version"]
        version = self._probe_version(version_argv)
        if memo_on:
            _write_version_memo(memo_key, {"stamp": stamp, "version": version})
        return version

    @staticmethod
    def _probe_version(version_argv: list[str]) -> str:
        try:
            r = subprocess.run(version_argv, capture_output=True, text=True, timeout=20)
            return (r.stdout or r.stderr).strip().split("\n")[0][:60]
        except (OSError, subprocess.SubprocessError, ValueError):
            return ""

    def invoke(self, workdir: str) -> tuple[str, int]:
        """Run the tool, and make sure a timeout actually stops it.

        `subprocess.run(timeout=...)` kills the process it started and nothing
        else. Several of these analyzers fan out with multiprocessing, so a
        timeout left a pool of orphaned workers alive. Observed after checkov
        deadlocked on a one-million-line repository: the parent was killed on
        timeout, and four workers were still resident twenty minutes later,
        competing for CPU with every scan that followed.

        Starting the tool in its own process group and signalling the group is
        what makes the timeout mean what it says.
        """
        # `{report_file}` is for a tool that has no "write JSON to stdout"
        # mode at all -- gitleaks' `--report-path` takes only a real filename,
        # unlike bandit/checkov/ruff/semgrep, which default to stdout. Giving
        # it the conventional `-` did not mean stdout to this tool: it created
        # a file *literally named* `-` inside the repository being scanned,
        # where gitleaks' own JSON output (full of strings that look exactly
        # like the secrets it exists to find) sat as content for the next
        # scan to read -- of any repo, not just this one, since the adapter
        # never set `cwd` and `invoke()`'s default is `{workdir}`. A private
        # temp path here, read back after the process exits, is what "connect
        # this tool's output to Arbiter" has to mean for one that insists on
        # a real file.
        report_file = None
        if any("{report_file}" in a for a in self.argv):
            fd, report_file = tempfile.mkstemp(prefix="arbiter-report-", suffix=".json")
            os.close(fd)
        argv = [a.replace("{workdir}", workdir).replace("{cache}", str(cache_dir()))
                .replace("{report_file}", report_file or "")
                for a in self.argv]
        if argv:
            # On Windows, Popen(shell=False) calls CreateProcess directly,
            # which -- unlike cmd.exe -- does not search PATHEXT for a bare
            # name. A tool whose console-script entry point is a .cmd/.bat
            # shim (checkov) resolves fine via shutil.which() in
            # missing_binaries() but then fails every invocation with
            # WinError 2. Resolving to the full, extensioned path here makes
            # invocation match the availability check.
            argv[0] = shutil.which(argv[0]) or argv[0]
        cwd = (self.cwd.replace("{workdir}", workdir).replace("{cache}", str(cache_dir()))
               if self.cwd else None)
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # PYTHONIOENCODING covers stdio; it does not cover a Python tool's own
        # Path.read_text() calls, which fall back to the OS codepage on
        # Windows. Semgrep reads its rule YAML that way, so a rule file with a
        # non-ASCII byte (an em dash, a smart quote) crashed it here with
        # `'charmap' codec can't decode byte ...` on an otherwise fine config.
        # PYTHONUTF8 makes UTF-8 the default everywhere the caller didn't ask
        # for something else, for every adapter, not only the Python-based
        # ones -- ruff and gitleaks just ignore it.
        env.setdefault("PYTHONUTF8", "1")

        popen_kwargs: dict = {}
        if hasattr(os, "setsid"):
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            # `text=True` alone decodes the child's output using *this*
            # process's locale encoding -- cp1252 on Windows -- no matter what
            # PYTHONUTF8 tells the child to write. Every adapter here emits
            # JSON, which is UTF-8 by its own spec, so decoding as anything
            # else is never correct, only sometimes lucky.
            encoding="utf-8", errors="replace",
            cwd=cwd, env=env, **popen_kwargs,
        )
        try:
            out, _err = proc.communicate(timeout=self.timeout)
            if report_file:
                try:
                    out = Path(report_file).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass  # tool crashed before writing it; stdout/stderr already tell that story
            return out, proc.returncode
        except BaseException:  # KeyboardInterrupt included: the tool's process group dies with us, then re-raised
            self._kill_group(proc)
            raise
        finally:
            if report_file:
                Path(report_file).unlink(missing_ok=True)

    @staticmethod
    def _kill_group(proc: "subprocess.Popen") -> None:
        """SIGTERM the whole group, then SIGKILL whatever ignored it."""
        import signal as _signal
        if not hasattr(os, "killpg"):
            Adapter._kill_tree(proc)
            return
        for sig in (_signal.SIGTERM, _signal.SIGKILL):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                break
            try:
                proc.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                continue
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    @staticmethod
    def _kill_tree(proc: "subprocess.Popen") -> None:
        """Kill a process and its descendants where there is no process group.

        Windows has neither `os.killpg` nor `SIGKILL`, so the group signalling
        above has nothing to signal — reaching it there raised AttributeError
        and the analyzer outlived its own timeout. `taskkill /T` walks the child
        tree from the parent PID, which is the nearest equivalent: killing only
        the direct child leaves the fanned-out workers running, which is the
        failure this whole path exists to prevent.
        """
        taskkill = shutil.which("taskkill")
        if taskkill:
            try:
                subprocess.run(
                    [taskkill, "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                proc.kill()
        else:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    # -- normalize ---------------------------------------------------------
    def parse_output(self, out: str) -> list[Any]:
        out = out.strip()
        if not out:
            return []
        if self.parse == "jsonlines":
            rows = []
            for line in out.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
            return rows
        try:
            doc = json.loads(out)
        except ValueError:
            # tools sometimes emit a banner before the JSON body
            m = re.search(r"[\[{]", out)
            if not m:
                return []
            try:
                doc = json.loads(out[m.start():])
            except ValueError:
                return []
        expr = self.mapping.get("findings", "$")
        rows = select(doc, expr)
        # Some tools (checkov with several frameworks) emit a LIST of result
        # objects rather than one. Fan the selector across the elements.
        if not rows and isinstance(doc, list) and expr.startswith("$."):
            for element in doc:
                rows.extend(select(element, expr))
        return rows

    def _dig_remediation(self, row: Any, m: dict, field_key: str, format_key: str) -> tuple[str, str]:
        """Returns (text, source). source is "link" whenever the dug value is
        itself a URL -- a pointer elsewhere, however it's worded -- and
        "field" for an inline value (an actual action or code, not a
        pointer). Judged by the value's shape, not by which config key
        supplied it, so a custom format string around a URL still reads as
        a link rather than masquerading as tool-provided fix text.
        """
        value = str(dig(row, m.get(field_key, ""), "") or "").strip()
        if not value:
            return "", ""
        source = "link" if value.startswith(("http://", "https://")) else "field"
        if m.get(format_key):
            return m[format_key].format(value=value), source
        if source == "link":
            return f"See {self.name}'s guidance: {value}", source
        return value, source

    def to_findings(self, rows: list[Any], repo_id: str, workdir: str) -> list[Finding]:
        m = self.mapping
        sev_table = {str(k).lower(): v for k, v in (m.get("severity_table") or {}).items()}
        remediation_table = {str(k): v for k, v in (m.get("remediation_table") or {}).items()}
        out: list[Finding] = []
        for row in rows:
            rule = str(dig(row, m.get("rule_id", ""), "") or "unknown")
            title = str(dig(row, m.get("title", ""), "") or rule)
            path = str(dig(row, m.get("path", ""), "") or "")
            if path.startswith(workdir):
                path = os.path.relpath(path, workdir)
            # Tools invoked on Windows (checkov, bandit) hand back a
            # backslash-separated path -- checkov's own relative output even
            # starts with a bare leading backslash. Every suppress-rule glob
            # and every other Location.path in this codebase assumes "/", so
            # a rule like `path: "fixtures/**"` silently matched zero Windows
            # findings from either tool without this.
            path = path.replace("\\", "/")
            # removeprefix, not lstrip: lstrip("./") eats the dot in ".github"
            while path.startswith("./"):
                path = path[2:]
            path = path.lstrip("/")
            line = dig(row, m.get("line", ""), 0)
            try:
                line = int(line or 0)
            except (TypeError, ValueError):
                line = 0
            if m.get("severity_const"):
                sev = m["severity_const"]
            else:
                raw_sev = str(dig(row, m.get("severity", ""), "") or "").lower()
                sev = sev_table.get(raw_sev, m.get("severity_default", "medium"))
            logical = str(dig(row, m.get("logical", ""), "") or "")
            desc = str(dig(row, m.get("description", ""), "") or "")
            # A curated, per-rule fix (remediation_table) beats a raw dug
            # field: "see the tool's docs" is not a fix, and a specific rule
            # id maps to one well-known mitigation, not a URL to go read.
            remediation = remediation_table.get(rule, "")
            remediation_source = "table" if remediation else ""
            if not remediation:
                remediation, remediation_source = self._dig_remediation(
                    row, m, "remediation", "remediation_format")
            # Some tools (ruff, semgrep) put an actual tool-generated fix in
            # one field and a docs link in another. Only fall back to the
            # link -- still labelled as a link, not presented as a fix --
            # when the primary field has nothing.
            if not remediation and m.get("remediation_secondary"):
                remediation, remediation_source = self._dig_remediation(
                    row, m, "remediation_secondary", "remediation_secondary_format")
            if not remediation:
                remediation = (m.get("remediation_default", "") or "").format(rule_id=rule)
                remediation_source = "default"
            out.append(Finding(
                rule_id=f"{self.name}/{rule}",
                title=title[:200],
                dimension=m.get("dimension", self.dimensions[0]),
                severity=sev,
                confidence=m.get("confidence", "high"),
                repo_id=repo_id,
                probe=self.name,
                location=Location(path=path, start_line=line, logical=logical),
                description=desc[:1000],
                remediation=remediation[:500],
                remediation_source=remediation_source,
                evidence=f"{rule}@{path}:{logical}" if logical else f"{rule}@{path}",
                tags=["external-tool", self.name],
            ))
        return out

    def run(self, ctx: ProbeContext) -> list[Finding]:
        findings: list[Finding] = []
        for repo in ctx.repos:
            try:
                out, code = self.invoke(repo.path)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"{self.name} timed out after {self.timeout}s")
            if code not in self.ok_exit and not out.strip():
                raise RuntimeError(f"{self.name} exited {code}")
            produced = self.to_findings(self.parse_output(out), repo.id, repo.path)
            known = {f.path for f in ctx.inventory.by_repo.get(repo.id, [])}
            findings.extend(in_scope(produced, known))
        return findings


def in_scope(findings: list[Finding], known_paths: set[str]) -> list[Finding]:
    """Keep only findings on files the inventory decided to scan.

    Every external analyzer walks the tree itself, and each one has its own
    idea of what to skip. The inventory is Arbiter's single answer to that
    question -- SKIP_DIRS, git-ignored paths, generated roles -- and REQ-026
    and REQ-029 taught bandit, checkov and semgrep to honour it through their
    own exclusion flags. gitleaks was not taught, and the first self-gate
    reported four critical secrets: fixture token shapes it found in a
    git-ignored graph file and in __pycache__. Rather than teach each tool
    separately and miss the next one, the boundary is enforced once, here,
    on the way in. A finding with no path (a tool-level verdict) is kept.
    """
    out: list[Finding] = []
    for f in findings:
        path = f.location.path
        if not path or path in known_paths:
            out.append(f)
    return out


SCOPE_REASON = ("no measurement establishes that this external analyzer returns "
                "the same findings from a subset of the files")


def _scope_of(data: dict) -> str:
    """Read a declared scope, and refuse a value that is not one of the two.

    A typo would otherwise sail through as a scope no partial scan recognises,
    which is the quiet kind of wrong this whole mechanism exists to prevent.
    """
    scope = data.get("scope", "repo")
    if scope not in ("file", "repo"):
        raise ValueError(
            f"adapter {data.get('name', '?')!r} declares scope {scope!r}; "
            "it must be 'file' or 'repo'")
    return scope


def load_adapter(path: Path) -> Adapter:
    data = _toml_loads(path.read_text(encoding="utf-8"))
    req = data.get("requires", {})
    inv = data.get("invoke", {})
    sel = data.get("selects", {})
    return Adapter(
        name=data["name"],
        dimensions=data.get("dimensions", ["quality"]),
        stacks=sel.get("stacks"),
        binaries=req.get("binaries", []),
        network=req.get("network", False),
        timeout=int(req.get("timeout", 300)),
        argv=inv.get("argv", []),
        cwd=inv.get("cwd", "{workdir}"),
        parse=inv.get("parse", "json"),
        ok_exit=inv.get("ok_exit", [0, 1]),
        version_argv=inv.get("version_argv", []),
        checks=int(data.get("checks", 10)),
        mapping=data.get("map", {}),
        scope=_scope_of(data),
        raw=data,
    )


def load_all(extra_dirs: list[str] | None = None) -> list[Adapter]:
    out: list[Adapter] = []
    dirs = [PACKS] + [Path(d) for d in (extra_dirs or [])]
    for d in dirs:
        if not d.is_dir():
            continue
        # `.adapter.toml`, not `.toml`: a bare `ruff.toml` anywhere in a scanned
        # tree is picked up by ruff itself as its own configuration and breaks it.
        for p in sorted(d.glob("*.adapter.toml")):
            try:
                out.append(load_adapter(p))
            except Exception:  # noqa: BLE001 - one malformed manifest must not take the other adapters down
                continue
    return out


_REGISTERED: set[str] = set()


def register_adapters(extra_dirs: list[str] | None = None) -> list[Adapter]:
    """Idempotent: the CLI and the library API can both call this safely.

    Registration matters even for tools that are not installed — an adapter
    that never registers is an adapter that never counts against coverage,
    which would quietly flatter a thin scan.
    """
    adapters = [a for a in load_all(extra_dirs) if a.name not in _REGISTERED]
    for a in adapters:
        _REGISTERED.add(a.name)
        register(Probe(
            name=a.name,
            dimensions=a.dimensions,
            checks=a.checks,
            run=a.run,
            stacks=a.stacks,
            binaries=a.binaries,
            network=a.network,
            scope=a.scope,
            scope_reason=SCOPE_REASON,
            version=a.tool_version() or "",
            external=True,
        ))
    return adapters
