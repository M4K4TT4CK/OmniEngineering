"""One service layer, two front doors.

## Why this exists separately from the front doors

Arbiter is growing a second caller. The MCP surface lets an agent run a scan on
a machine where Arbiter is already installed; the hosted API lets a caller run
one where it is not. Those differ only in transport. Everything that matters --
where output may be written, whether the network is reachable, whether the
scanned tree is ever executed, and what the caller is forbidden to do -- is
identical, and a rule written twice is a rule that will eventually be written
differently.

So the containment lives here and the front doors are thin.

## The operation that does not exist

There is no function here that records an adjudication verdict, and the hosted
surface must never grow one.

Calibration reads a single ledger: findings a person looked at and judged.
Injection trials measure whether a rule fires against faults Arbiter generated
itself, and corpus discrimination measures whether a rule separates broken code
from working code -- both are Arbiter measuring Arbiter. The adjudication ledger
is the one signal in the system the system did not generate, and
`learn.record()` refuses to re-adjudicate a fingerprint, so a wrong verdict is
permanent.

An automated caller marking verdicts in a loop would convert measured precision
into the tool's opinion of itself, at machine speed and irreversibly. So
`review_queue` writes a queue with every mark blank and stops there. A person
marks it and runs `arbiter review --apply` locally.

`review_draft` is the one step past that, and it stays on the right side of the
line: it writes the same queue with marks an assistant proposed and a reason
under each, as a file a person reads, and nothing else. No ledger is opened,
the knowledge file is never saved, and the draft is a verdict only once a
person has read it and run `arbiter review --apply` with their name on it. The
difference between proposing and recording is the difference between a file
and the ledger, and only the CLI, in a person's hands, crosses it.

## Custody, which is the part hosting actually changes

Running the scan somewhere the customer does not control means their source
lives, however briefly, on someone else's disk. Three consequences are designed
for rather than assumed away:

  * A workspace is created outside the server's own tree, is readable only by
    the process that made it, and is removed when the scan ends -- including
    when it fails.
  * Uploaded archives are treated as hostile input. A member that is absolute,
    escapes the extraction root, is a link, is a device node, or is
    implausibly large is refused rather than written. Python's `tarfile` grew a
    `data` filter that does most of this, but only in 3.12, and this package
    supports 3.11.
  * Reports are sensitive even after redaction. `report.py` already refuses to
    reprint a secret's value -- `test_no_output_format_reprints_a_secret` holds
    it to that -- but a report still names the file and the kind of credential,
    which is a map to what to steal. Retention is therefore the caller's
    decision to make explicitly, not a default this layer picks.

## Why it shells out

`run_scan` takes fifteen parameters and `arbiter/__init__.py` exports only
`__version__`, so there is no stable Python API to bind a service to yet.
Shelling out to the console script also keeps each scan in its own process: a
probe that wedges or dies takes down a subprocess, not the server.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from .policy import PROFILES

# Long enough for a large repository, short enough that a wedged probe does not
# hold a caller forever. The adapters' own timeouts are tighter.
DEFAULT_TIMEOUT = 900

# Bounds on an uploaded archive. An archive that exceeds any of them is refused
# whole rather than partially extracted, because a partial extraction is a scan
# of something the caller did not send.
MAX_MEMBERS = 100_000
MAX_MEMBER_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

EXIT_OK, EXIT_GATE_FAIL, EXIT_ERROR = 0, 1, 2


class ServiceError(RuntimeError):
    """An operation was refused or failed. The message is meant for the caller."""


# --------------------------------------------------------------------------
# Containment
# --------------------------------------------------------------------------

def resolve_within(candidate: str, root: str, label: str = "path") -> Path:
    """Resolve `candidate` and refuse it if it escapes `root`.

    Symlinks are resolved before the comparison, so a link pointing out of the
    sandbox is caught rather than followed.
    """
    base = Path(root).expanduser().resolve()
    target = Path(candidate).expanduser()
    if not target.is_absolute():
        target = base / target
    target = target.resolve()
    if target != base and base not in target.parents:
        raise ServiceError(f"{label} must stay beneath {base}; {target} is outside it")
    return target


def check_profile(profile: str, allow_network: bool = False) -> str:
    """Refuse a profile that reaches the network unless the operator allowed it.

    The `connected` and `audit` profiles declare `network: True`. That is a
    reasonable thing for an engineer to ask for at their own terminal and an
    unreasonable thing to grant a remote caller against source they uploaded,
    so the hosted front door never passes `allow_network`.
    """
    caps = PROFILES.get(profile)
    if caps is None:
        known = ", ".join(sorted(PROFILES))
        raise ServiceError(f"unknown profile {profile!r}; known profiles are {known}")
    if caps.get("network") and not allow_network:
        raise ServiceError(
            f"profile {profile!r} enables network access, which this surface does not grant"
        )
    return profile


class Workspace:
    """A scratch directory that is not inside the server's tree and does not outlive the scan.

    `source` and `output` are siblings on purpose. `engine.py` excludes the
    output directory from the inventory, but only when `--out` is passed, and a
    scan whose output lands inside the scanned tree reports on its own previous
    HTML -- that was 27.7% of unsuppressed findings when it happened. Keeping
    them apart means the guard is not the only thing standing between a caller
    and a self-referential report.
    """

    def __init__(self, root: str | None = None, keep: bool = False) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="arbiter-ws-", dir=root)).resolve()
        # Owner-only. On Windows this is advisory; the temp directory is already
        # per-user there.
        os.chmod(self.path, stat.S_IRWXU)
        self.source = self.path / "source"
        self.output = self.path / "output"
        self.source.mkdir()
        self.output.mkdir()
        self.keep = keep

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Remove the workspace, including after a failure.

        `keep` exists for debugging a reproducible scan and must stay off in a
        hosted deployment: it leaves customer source on disk.
        """
        if self.keep:
            return
        shutil.rmtree(self.path, ignore_errors=True)


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------

def _reject(name: str, why: str) -> ServiceError:
    return ServiceError(f"refused archive member {name!r}: {why}")


def _check_name(name: str, dest: Path) -> Path:
    """Validate one member path before anything is written for it."""
    if not name or name in (".", "/"):
        raise _reject(name, "empty path")
    pure = Path(name.replace("\\", "/"))
    if pure.is_absolute() or (len(name) > 1 and name[1] == ":"):
        raise _reject(name, "absolute path")
    if ".." in pure.parts:
        raise _reject(name, "parent-directory traversal")
    target = (dest / pure).resolve()
    if target != dest and dest not in target.parents:
        raise _reject(name, "escapes the extraction root")
    return target


def extract_archive(archive: str | Path, dest: str | Path) -> Path:
    """Extract an uploaded tar or zip into `dest`, refusing hostile members.

    Refused outright: absolute paths, parent traversal, symlinks, hard links,
    device nodes and fifos, members over `MAX_MEMBER_BYTES`, archives over
    `MAX_MEMBERS` entries or `MAX_TOTAL_BYTES` uncompressed.

    Links are refused rather than resolved because a link is the cheapest way
    to make a scan read `/etc/shadow` and quote it back in a finding's evidence
    snippet.
    """
    src = Path(archive)
    out = Path(dest).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if tarfile.is_tarfile(src):
        return _extract_tar(src, out)
    if zipfile.is_zipfile(src):
        return _extract_zip(src, out)
    raise ServiceError(f"{src} is neither a tar nor a zip archive")


def _extract_tar(src: Path, out: Path) -> Path:
    total = 0
    with tarfile.open(src, "r:*") as tf:
        for count, member in enumerate(tf, start=1):
            if count > MAX_MEMBERS:
                raise ServiceError(f"archive has more than {MAX_MEMBERS} entries")
            if member.issym() or member.islnk():
                raise _reject(member.name, "archive links are not extracted")
            if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                raise _reject(member.name, "device and fifo entries are not extracted")
            if not (member.isfile() or member.isdir()):
                raise _reject(member.name, "unsupported member type")
            if member.size > MAX_MEMBER_BYTES:
                raise _reject(member.name, f"exceeds {MAX_MEMBER_BYTES} bytes")
            total += member.size
            if total > MAX_TOTAL_BYTES:
                raise ServiceError(f"archive expands past {MAX_TOTAL_BYTES} bytes")
            target = _check_name(member.name, out)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is None:  # pragma: no cover - guarded by isfile above
                raise _reject(member.name, "unreadable")
            with extracted, open(target, "wb") as fh:
                shutil.copyfileobj(extracted, fh, length=1024 * 1024)
    return out


def _extract_zip(src: Path, out: Path) -> Path:
    total = 0
    with zipfile.ZipFile(src) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ServiceError(f"archive has more than {MAX_MEMBERS} entries")
        for info in infos:
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise _reject(info.filename, "archive links are not extracted")
            if info.file_size > MAX_MEMBER_BYTES:
                raise _reject(info.filename, f"exceeds {MAX_MEMBER_BYTES} bytes")
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ServiceError(f"archive expands past {MAX_TOTAL_BYTES} bytes")
            target = _check_name(info.filename, out)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as extracted, open(target, "wb") as fh:
                shutil.copyfileobj(extracted, fh, length=1024 * 1024)
    return out


# --------------------------------------------------------------------------
# Running arbiter
# --------------------------------------------------------------------------

def _console_command() -> list[str]:
    """How to invoke arbiter as a separate process.

    Prefers the installed console script, falling back to the module so a source
    checkout works without `pip install -e .`.
    """
    found = shutil.which("arbiter")
    return [found] if found else [sys.executable, "-m", "arbiter.cli"]


def _run(argv: list[str], timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run arbiter in its own process. Never runs anything from the scanned tree."""
    env = dict(os.environ)
    src = Path(__file__).resolve().parent.parent
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else str(src)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        return subprocess.run(
            _console_command() + argv, capture_output=True, text=True,
            timeout=timeout, env=env, encoding="utf-8", errors="replace",
            # Left unredirected, the child inherits this process's stdin. Over
            # MCP's stdio transport that handle is the live pipe the protocol
            # itself reads from -- inheriting it into a subprocess that never
            # touches it holds the pipe open and the request that spawned this
            # process never sees its response.
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError(f"arbiter did not finish within {timeout}s") from exc
    except FileNotFoundError as exc:  # pragma: no cover - environment specific
        raise ServiceError(f"could not launch arbiter: {exc}") from exc


def _scan_argv(target: str, out_dir: Path, profile: str, only: str, skip: str) -> list[str]:
    argv = [target, "--out", str(out_dir), "--profile", profile, "--format", "json"]
    if only:
        argv += ["--only", only]
    if skip:
        argv += ["--skip", skip]
    return argv


def _read_report(out_dir: Path) -> dict:
    path = out_dir / "report.json"
    if not path.exists():
        raise ServiceError(f"arbiter wrote no report at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ServiceError(f"report at {path} is not valid JSON: {exc}") from exc


# --------------------------------------------------------------------------
# Operations
# --------------------------------------------------------------------------

def scan(target: str, output_dir: str, profile: str = "offline", only: str = "",
         skip: str = "", allow_network: bool = False,
         timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Analyse a repository and return the report.

    `output_dir` is required and is the sandbox: nothing is written outside it.
    """
    profile = check_profile(profile, allow_network)
    out = resolve_within("report", output_dir, "output_dir")
    out.mkdir(parents=True, exist_ok=True)
    proc = _run(["scan"] + _scan_argv(target, out, profile, only, skip), timeout)
    if proc.returncode == EXIT_ERROR:
        raise ServiceError(f"scan failed: {(proc.stderr or proc.stdout).strip()[:800]}")
    report = _read_report(out)
    return {
        "report": report,
        "report_path": str(out / "report.json"),
        "finding_count": len(report.get("findings") or []),
    }


def gate(target: str, output_dir: str, profile: str = "ci", only: str = "",
         skip: str = "", allow_network: bool = False,
         timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run the policy gate. Returns pass or fail with the claim ledger.

    A failing gate is exit 1 and is an answer, not an error. Only exit 2 means
    Arbiter could not run.
    """
    profile = check_profile(profile, allow_network)
    out = resolve_within("report", output_dir, "output_dir")
    out.mkdir(parents=True, exist_ok=True)
    proc = _run(["gate"] + _scan_argv(target, out, profile, only, skip), timeout)
    if proc.returncode == EXIT_ERROR:
        raise ServiceError(f"gate failed: {(proc.stderr or proc.stdout).strip()[:800]}")
    report = _read_report(out)
    return {
        "passed": bool((report.get("gate") or {}).get("passed")),
        "exit_code": proc.returncode,
        "gate": report.get("gate") or {},
        "claims": report.get("claims") or [],
        "report_path": str(out / "report.json"),
    }


def review_queue(report_path: str, output_dir: str, limit: int = 20, rule: str = "",
                 timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Generate a queue of findings for a person to adjudicate.

    Every mark is blank and stays blank. There is no parameter that records a
    verdict and `--apply` is never passed; see the module docstring for why that
    is structural rather than unfinished.
    """
    out = resolve_within("review.md", output_dir, "output_dir")
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ["review", report_path, "--out", str(out), "--limit", str(limit)]
    if rule:
        argv += ["--rule", rule]
    proc = _run(argv, timeout)
    if proc.returncode == EXIT_ERROR:
        raise ServiceError(f"review failed: {(proc.stderr or proc.stdout).strip()[:800]}")
    if not out.exists():
        # Nothing to review is an answer, not a failure. `arbiter review` exits 0
        # and writes no file when a report holds no findings, or when every one
        # of them has already been adjudicated. Treating that as an error told a
        # hosted caller their clean report was a bad request, and named a server
        # temporary directory in the message while doing it.
        return {
            "queue_path": "",
            "queue_markdown": "",
            "entry_count": 0,
            "recorded": False,
            "note": ("Nothing to review: this report has no findings, or every "
                     "finding in it has already been adjudicated."),
        }
    text = out.read_text(encoding="utf-8")
    return {
        "queue_path": str(out),
        "queue_markdown": text,
        "entry_count": sum(1 for line in text.split("\n") if line.startswith("[ ] ")),
        "recorded": False,
        "note": ("Marks are blank by design. A person edits this file and runs "
                 "`arbiter review --apply <file>` locally; no remote surface "
                 "records verdicts."),
    }


# --------------------------------------------------------------------------
# A draft is a file a person reads, not a verdict
# --------------------------------------------------------------------------

DRAFT_FILE = "review-draft.md"
DRAFT_MARKS = ("y", "n", "?")

# The first line is the whole warning, so a person who reads nothing else
# still reads that.
DRAFT_HEADER = """\
# Review draft: every mark below was proposed by an assistant and nothing has been recorded.

An assistant drew this queue and proposed each mark, with its reason on the
line beneath. No verdict exists yet, and this file cannot create one. The only
way to record a mark is for a person to read every entry and then run:

    arbiter review {report} --apply {path} --reviewer <your name>

Change any mark you disagree with first. The reviewer named on that command
answers for every mark it records, and the ledger refuses re-adjudication, so
a wrong mark is permanent.

    [y]  a real problem — I would act on this
    [n]  not a real problem — the rule is wrong here
    [?]  the assistant could not tell; decide, or leave it and it is skipped
    [ ]  skip; nothing is recorded

---
"""


def _proposals(verdicts: object) -> dict[str, tuple[str, str]]:
    """Validate the proposed marks. Malformed input is refused outright; an id
    that is not in the queue is not an error, and is reported instead."""
    if not isinstance(verdicts, list):
        raise ServiceError("verdicts must be a list of {id, mark, reason} objects")
    out: dict[str, tuple[str, str]] = {}
    for i, item in enumerate(verdicts):
        if not isinstance(item, dict):
            raise ServiceError(f"verdicts[{i}] is not an object")
        fid = str(item.get("id") or "").strip()
        mark = str(item.get("mark") or "").strip().lower()
        # One line, so the reason can never read as a mark line itself.
        reason = " ".join(str(item.get("reason") or "").split())
        if not fid:
            raise ServiceError(f"verdicts[{i}] has no id")
        if mark not in DRAFT_MARKS:
            raise ServiceError(f"verdicts[{i}] ({fid}): mark must be y, n or ?, not {mark!r}")
        if mark != "?" and not reason:
            raise ServiceError(f"verdicts[{i}] ({fid}): a {mark} mark needs a reason; a person reads it")
        if fid in out:
            raise ServiceError(f"{fid} is listed twice")
        out[fid] = (mark, reason)
    return out


def _load_report(report_path: str):
    from .core import Report

    path = Path(report_path)
    if not path.is_file():
        raise ServiceError(f"no report at {path}")
    try:
        return Report.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as exc:
        raise ServiceError(f"report at {path} is not a report: {exc}") from exc


def review_draft(report_path: str, output_dir: str, verdicts: list[dict],
                 limit: int = 20, rule: str | None = None) -> dict:
    """Write the review queue with marks an assistant proposed, for a person to read.

    The queue is the one `review_queue` draws -- same report, same selection,
    same knowledge read from the same place -- written to `output_dir` in the
    format `arbiter review --apply` reads, with each proposed mark filled in and
    its reason on the line beneath. Findings the proposals do not name stay
    blank. The result also carries, as text, the failure-ledger entries the `y`
    marks would draft, so the person sees what applying would set in motion.

    Nothing is recorded. The knowledge file is read and never saved, no ledger
    is written, and the draft's first line says so. A person records the marks,
    after reading them, with the CLI and their name.
    """
    from .learn import Knowledge
    from .ledger import preview_entries
    from .review import MARK, render, select

    proposals = _proposals(verdicts)
    out = resolve_within(DRAFT_FILE, output_dir, "output_dir")
    report = _load_report(report_path)
    # Read from where `arbiter review` reads it, and only read.
    knowledge = Knowledge.load(None)
    picked = select(report.findings, knowledge, limit=limit, rule=rule or None)
    if not picked:
        return {
            "draft_path": "", "draft_markdown": "", "entry_count": 0,
            "proposed": {"y": 0, "n": 0, "?": 0}, "unknown_ids": sorted(proposals),
            "ledger_entries_text": "", "recorded": False,
            "note": ("Nothing to review: this report has no findings, or every "
                     "finding in it has already been adjudicated. No draft was written."),
        }

    queue = render(picked, knowledge, str(out))
    # One renderer and one parser: the draft is the queue with its header
    # swapped and the marks filled, never a second format.
    body = queue.split("---\n", 1)[1]
    filled: dict[str, str] = {}
    lines: list[str] = []
    for line in body.split("\n"):
        m = MARK.match(line)
        if m and m.group(2) in proposals:
            mark, reason = proposals[m.group(2)]
            lines.append(line[:m.start(1)] + mark + line[m.end(1):])
            if reason:
                lines.append(f"    reason: {reason}")
            filled[m.group(2)] = mark
        else:
            lines.append(line)
    draft = DRAFT_HEADER.format(report=report_path, path=str(out)) + "\n".join(lines)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(draft, encoding="utf-8")

    proposed = {mark: sum(1 for v in filled.values() if v == mark) for mark in DRAFT_MARKS}
    would_draft = preview_entries(picked, [fid for fid, v in filled.items() if v == "y"])
    return {
        "draft_path": str(out),
        "draft_markdown": draft,
        "entry_count": len(picked),
        "proposed": proposed,
        "unknown_ids": sorted(fid for fid in proposals if fid not in filled),
        "ledger_entries_text": (json.dumps(would_draft, indent=2, ensure_ascii=False)
                                if would_draft else ""),
        "recorded": False,
        "note": ("Nothing has been recorded. A person reads the draft and runs "
                 "`arbiter review <report> --apply <draft> --reviewer <name>` locally; "
                 "that is the only way a mark reaches the ledger. The ledger entries "
                 "above are what the y marks would draft with --ledger, numbered from "
                 "an empty ledger; none has been written. An unknown id is one that "
                 "is not in this queue, and the draft carries no mark for it."),
    }


# Named so a test can assert on it: these are every operation either front door
# may expose, and nothing here writes to the calibration ledger.
OPERATIONS = {"scan": scan, "gate": gate, "review_queue": review_queue,
              "review_draft": review_draft}
