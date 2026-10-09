"""Command line interface.

The CLI is the engine. The CI gate, the Claude skill and any dashboard are
consumers of the JSON it writes — none of them re-implement analysis.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__, client, history
from .ab import (
    Arm,
    arm_from_dict,
    load_ab_spec,
    render_ab_console,
    render_ab_html,
    run_ab,
)
from .adapters import register_adapters
from .core import SEVERITIES, Report
from .engine import run_scan, write_baseline
from .policy import PROFILES, load_config
from .probes import REGISTRY, ProbeContext
from .report import render_annotations, render_console, write_all

EXIT_OK, EXIT_GATE_FAIL, EXIT_ERROR = 0, 1, 2


def _parse_arm(spec: str) -> Arm:
    """`name;kind=tool;tool=checkov` or `native;only=secrets,quality`."""
    parts = [p for p in spec.split(";") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("empty --arm")
    d: dict = {"name": parts[0].strip()}
    for kv in parts[1:]:
        if "=" not in kv:
            raise argparse.ArgumentTypeError(f"bad arm field '{kv}' (expected key=value)")
        k, v = kv.split("=", 1)
        k, v = k.strip(), v.strip()
        d[k] = [x for x in v.split(",") if x] if k in ("only", "skip") else v
    return arm_from_dict(d)


def _formats(value: str) -> list[str]:
    return [f.strip() for f in value.split(",") if f.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="arbiter",
        description="Evaluate a repository — or a system of repositories — and refuse to grade what it did not inspect.",
    )
    p.add_argument("--version", action="version", version=f"arbiter {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    _add_scan_commands(sub)
    _add_report_commands(sub)
    _add_service_commands(sub)
    return p


def _add_scan_commands(sub) -> None:
    """`scan` and `gate`: the local analyzers, sharing one argument set."""
    def common(sp):
        sp.add_argument("targets", nargs="*", help="paths or git URLs")
        sp.add_argument("--system", help="arbiter-system.yaml describing several repos")
        sp.add_argument("--config", help="arbiter.yaml (defaults to one in the target)")
        sp.add_argument("--profile", choices=sorted(PROFILES), help="capability budget for this run")
        sp.add_argument("--only", default="", help="comma-separated probes to run exclusively")
        sp.add_argument("--skip", default="", help="comma-separated probes to skip")
        sp.add_argument("--baseline", help="baseline JSON for new/existing labelling")
        sp.add_argument("--no-adapters", action="store_true", help="native probes only")
        sp.add_argument("--knowledge", help="knowledge file (default .arbiter/knowledge.json)")
        sp.add_argument("--pin-knowledge", metavar="HASH",
                        help="fail unless the knowledge file is exactly this version")
        sp.add_argument("--changed", metavar="REF", default=None,
                        help="scan only files that differ from REF (plus uncommitted "
                             "work), and record every check that needs the whole "
                             "repository as not assessed. For pull-request gates.")
        sp.add_argument("--only-files", default="", metavar="PATHS",
                        help="comma-separated paths to scan; same partial-scan "
                             "accounting as --changed")
        sp.add_argument("--tfplan", action="append", default=[], metavar="[REPO=]PATH",
                        help="Terraform plan or state JSON; supersedes reading .tf source. "
                             "Repeatable, and prefix with `repo=` in a multi-repo system.")
        sp.add_argument("--no-history", action="store_true",
                        help="do not append this run to <out>/history.jsonl")
        sp.add_argument("--github", action=argparse.BooleanOptionalAction, default=None,
                        help="GitHub Actions mode: print findings as workflow-command "
                             "annotations and append the pull-request comment to "
                             "$GITHUB_STEP_SUMMARY. Default: on when GITHUB_ACTIONS=true.")
        sp.add_argument("--sarif-min-severity", choices=SEVERITIES, default=None, metavar="SEV",
                        help="drop findings below this severity from report.sarif (the code-scanning upload); "
                             "the other formats always carry every active finding")
        sp.add_argument("--no-cache", action="store_true",
                        help="neither read nor write the result cache (.arbiter/cache.json): "
                             "every probe runs over every file and every adapter runs")
        sp.add_argument("--cache-path", default=None, metavar="FILE",
                        help="where the result cache lives (default: "
                             ".arbiter/cache.json under the first target)")
        return sp

    sc = common(sub.add_parser("scan", help="analyze and report"))
    sc.add_argument("--out", default="arbiter-out", help="output directory")
    sc.add_argument("--format", default="json,console",
                    help="json,sarif,html,markdown,console,pr-comment,annotations "
                         "(annotations: GitHub workflow commands, printed after the console)")
    sc.add_argument("--limit", type=int, default=40, help="findings shown on the console")
    sc.add_argument("--open", dest="open", action="store_const", const=True, default=None,
                     help="open the HTML report in the default browser when the scan finishes "
                          "(adds html to --format if it isn't already there); on by default when "
                          "run at a terminal")
    sc.add_argument("--no-open", dest="open", action="store_const", const=False,
                     help="never open the HTML report automatically")
    sc.add_argument("--verify-cache", action="store_true",
                     help="re-run each cacheable probe on a random 5%% sample of its "
                          "cache hits, and one memoised adapter, and report any entry "
                          "that no longer matches")

    gt = common(sub.add_parser("gate", help="analyze and exit non-zero on policy failure"))
    gt.add_argument("--out", default="arbiter-out")
    gt.add_argument("--format", default="json,console",
                    help="json,sarif,html,markdown,console,pr-comment,annotations")


def _add_report_commands(sub) -> None:
    """Everything that reads a report or the knowledge file rather than scanning."""
    db = sub.add_parser("dashboard",
                        help="render the run history as a self-contained trend page")
    db.add_argument("--history", default="arbiter-out/history.jsonl",
                    help="history file every scan and gate appends to")
    db.add_argument("--out", default="arbiter-out/dashboard.html", help="page to write")

    ab = sub.add_parser("ab", help="run two arms over the same target and compare")
    ab.add_argument("--spec", help="A/B spec YAML")
    ab.add_argument("--target", action="append", default=[], help="target path (repeatable)")
    ab.add_argument("--system", help="arbiter-system.yaml")
    ab.add_argument("--config", help="base arbiter.yaml")
    ab.add_argument("--arm", action="append", default=[], type=_parse_arm,
                    help="arm spec, e.g. 'native;only=secrets' or 'checkov;kind=tool;tool=checkov'")
    ab.add_argument("--name", default="", help="label for the comparison")
    ab.add_argument("--out", default="arbiter-ab", help="output directory")
    ab.add_argument("--no-adapters", action="store_true")

    pr = sub.add_parser("probes", help="list probes and whether they can run here")
    pr.add_argument("target", nargs="?", default=".", help="target to assess applicability against")
    pr.add_argument("--no-adapters", action="store_true")

    bl = sub.add_parser("baseline", help="write a baseline from a report")
    bl.add_argument("report", help="path to report.json")
    bl.add_argument("--out", default=".arbiter/baseline.json")

    df = sub.add_parser("diff", help="compare two reports")
    df.add_argument("before", help="earlier report.json")
    df.add_argument("after", help="later report.json")
    df.add_argument("--format", default="console", help="console,json,markdown,pr-comment")
    df.add_argument("--out", default="", help="write the chosen formats into this directory")

    fb = sub.add_parser("feedback", help="adjudicate findings so the tool calibrates")
    fb.add_argument("finding_ids", nargs="+", help="finding ids from a report")
    fb.add_argument("--report", default="arbiter-out/report.json")
    fb.add_argument("--knowledge", help="knowledge file (default .arbiter/knowledge.json)")
    group = fb.add_mutually_exclusive_group(required=True)
    group.add_argument("--false-positive", action="store_true")
    group.add_argument("--true-positive", action="store_true")
    fb.add_argument("--note", default="")
    fb.add_argument("--reviewer", default="",
                    help="who is answerable for these verdicts "
                         "(default: git config user.email)")
    fb.add_argument("--batch", action="store_true",
                    help="record verdicts without a terminal; they are marked "
                         "as a batch import rather than a typed verdict")

    ln = sub.add_parser("learn", help="show what the tool has learned")
    ln.add_argument("--knowledge", help="knowledge file (default .arbiter/knowledge.json)")
    ln.add_argument("--target", type=float, default=1e-6,
                    help="error rate you want to claim, for the sample-size column")

    vf = sub.add_parser("verify", help="check a report's claims against their basis")
    vf.add_argument("report", nargs="?", default="arbiter-out/report.json")
    vf.add_argument("--config", help="arbiter.yaml, for the coverage threshold")
    vf.add_argument("--show-claims", action="store_true", help="print every claim, not just violations")

    rv = sub.add_parser("review",
                        help="adjudicate a batch of findings in one pass")
    rv.add_argument("report", nargs="?", default="arbiter-out/report.json")
    rv.add_argument("--out", default="arbiter-out/review.md",
                    help="where to write the review file")
    rv.add_argument("--limit", type=int, default=20,
                    help="how many findings to put in front of you (default 20)")
    rv.add_argument("--rule", help="only findings whose rule id contains this")
    rv.add_argument("--apply", metavar="FILE",
                    help="read a marked review file back and record the verdicts")
    rv.add_argument("--note", default="", help="note stored with each verdict")
    rv.add_argument("--ledger", metavar="FILE", default="",
                    help="with --apply: draft an open OmniEngineering failure-ledger "
                         "entry for every verdict recorded as a true positive")
    rv.add_argument("--reviewer", default="",
                    help="who is answerable for these verdicts "
                         "(default: git config user.email)")
    rv.add_argument("--knowledge", help="path to knowledge.json")
    rv.add_argument("--html", metavar="FILE", nargs="?", const="arbiter-out/review.html",
                    help="write a self-contained review page instead of markdown")
    rv.add_argument("--interactive", action="store_true",
                    help="walk the findings in the terminal, one keypress each")
    rv.add_argument("--repo", action="append", default=[],
                    help="id=path, so the review can show code context; repeatable")

    ct = sub.add_parser("controls",
                        help="control coverage per framework, including what was NOT assessed")
    ct.add_argument("report", nargs="?", default="arbiter-out/report.json")
    ct.add_argument("--framework", action="append", default=[],
                    help="limit to one framework id; repeatable")
    ct.add_argument("--packs", action="append", default=[],
                    help="extra directory of control packs; repeatable")
    ct.add_argument("--state", action="append", default=[],
                    help="show only controls in this state "
                         "(violated, not_assessed, no_coverage, satisfied, not_automatable)")
    ct.add_argument("--json", action="store_true", help="machine-readable output")
    ct.add_argument("--list", action="store_true", help="list available frameworks and exit")

    ex = sub.add_parser("explain", help="show one finding in full")
    ex.add_argument("finding_id")
    ex.add_argument("--report", default="arbiter-out/report.json")


def _add_service_commands(sub) -> None:
    """The hosted surfaces -- api, mcp, bundle -- and the remote client."""
    ap = sub.add_parser("api",
                        help="serve the hosted API, and issue the keys that reach it")
    ap.add_argument("--keys", help="key file (default ~/.arbiter/keys.json, or $ARBITER_KEYS)")
    api_sub = ap.add_subparsers(dest="api_cmd", required=True)
    sv = api_sub.add_parser("serve", help="run the API over TLS; there is no plaintext mode")
    sv.add_argument("--host", default="127.0.0.1",
                    help="localhost by default; exposing it is a deliberate act")
    sv.add_argument("--port", type=int, default=8443)
    sv.add_argument("--cert", help="TLS certificate file; required, including behind a proxy")
    sv.add_argument("--key", dest="tls_key", help="TLS private key file")
    sv.add_argument("--audit",
                    help="request log (default ~/.arbiter/audit.log, or $ARBITER_AUDIT); "
                         "records who called and how it ended, never their code")
    sv.add_argument("--no-audit", action="store_true",
                    help="keep no record of who called; you will not be able to "
                         "answer what ran for whom")
    sv.add_argument("--limiter", choices=("memory", "file"), default="memory",
                    help="where the per-key caps are counted: in this process "
                         "(default, one process only) or in limiter.db beside the "
                         "key file, shared by every process serving the same keys")
    ky = api_sub.add_parser("key", help="issue, list and revoke access by hand")
    key_sub = ky.add_subparsers(dest="key_cmd", required=True)
    ka = key_sub.add_parser("add", help="mint a key for one user")
    ka.add_argument("--user", required=True,
                    help="the person this key is for; one user holds one key")
    ka.add_argument("--replace", action="store_true",
                    help="revoke the user's current key and issue a new one")
    ka.add_argument("--expires-days", type=int, default=90,
                    help="how long the key lasts (default 90)")
    ka.add_argument("--no-expiry", action="store_true",
                    help="mint a key that never expires; a permanent grant to "
                         "whoever ends up holding it")
    key_sub.add_parser("list", help="show every key, without secrets")
    kr = key_sub.add_parser("revoke", help="revoke a key by its short id")
    kr.add_argument("id")

    mp = sub.add_parser("mcp",
                        help="serve the MCP tools: stdio for one local agent, "
                             "or HTTPS for several people")
    mp.add_argument("--http", action="store_true",
                    help="serve over HTTPS instead of stdio; every call needs a key")
    mp.add_argument("--root",
                    help="directory every caller's paths must stay inside, one "
                         "subdirectory per key; required with --http")
    mp.add_argument("--keys", help="key file (default ~/.arbiter/keys.json, or $ARBITER_KEYS)")
    mp.add_argument("--host", default="127.0.0.1",
                    help="localhost by default; exposing it is a deliberate act")
    mp.add_argument("--port", type=int, default=8444)
    mp.add_argument("--path", default="/mcp", help="URL path to serve the endpoint on")
    mp.add_argument("--allowed-host", action="append", dest="allowed_hosts",
                    help="hostname callers reach this server by; repeatable. "
                         "Needed when a proxy forwards a public hostname, because "
                         "the transport refuses a Host header it was not told to expect")
    mp.add_argument("--cert", help="TLS certificate file; required, including behind a proxy")
    mp.add_argument("--key", dest="tls_key", help="TLS private key file")
    mp.add_argument("--audit",
                    help="request log (default ~/.arbiter/audit.log, or $ARBITER_AUDIT); "
                         "records who called and how it ended, never their code")
    mp.add_argument("--no-audit", action="store_true",
                    help="keep no record of who called; you will not be able to "
                         "answer what ran for whom")
    mp.add_argument("--limiter", choices=("memory", "file"), default="memory",
                    help="where the per-key caps are counted: in this process "
                         "(default, one process only) or in limiter.db beside the "
                         "key file, shared by every process serving the same keys")

    bd = sub.add_parser("bundle",
                        help="build and verify an air-gapped install bundle: "
                             "Arbiter and its dependencies as wheels, no analyzers")
    bundle_sub = bd.add_subparsers(dest="bundle_cmd", required=True)
    bb = bundle_sub.add_parser("build", help="write a bundle into a new directory")
    bb.add_argument("--out", required=True, help="directory to create; must be empty")
    bb.add_argument("--source",
                    help="the Arbiter checkout to build from (default: the one "
                         "this command was imported from)")
    bb.add_argument("--no-deps-download", action="store_true",
                    help="skip downloading the dependency wheels, the one step "
                         "that needs an index; the target must then already "
                         "hold PyYAML")
    bv = bundle_sub.add_parser("verify",
                               help="recompute every hash; fail on a missing, "
                                    "altered or extra file")
    bv.add_argument("bundle_dir")

    # The client half. Everything above runs the scanner here; this runs it
    # somewhere else and renders the answer with the same code, so a person who
    # installed nothing but this package gets the same report.
    rm = sub.add_parser("remote",
                        help="run against a hosted Arbiter; installs nothing locally")
    remote_sub = rm.add_subparsers(dest="remote_cmd", required=True)

    def remote_common(sp):
        sp.add_argument("--server", help="https://host[:port] of the hosted instance")
        sp.add_argument("--key", help="your API key (issued by hand by whoever runs it)")
        sp.add_argument("--cacert", help="certificate to trust, for a self-signed server")
        sp.add_argument("--config", dest="client_config",
                        help=f"client settings file (default {client.DEFAULT_CONFIG})")
        return sp

    rs = remote_common(remote_sub.add_parser("scan", help="scan a directory remotely"))
    rs.add_argument("target", nargs="?", default=".", help="directory to send")
    rs.add_argument("--profile", default="offline",
                    help="capability budget; both profiles run with the network off")
    rs.add_argument("--only", default="", help="comma-separated probes to run exclusively")
    rs.add_argument("--skip", default="", help="comma-separated probes to skip")
    rs.add_argument("--out", default="arbiter-out", help="output directory")
    rs.add_argument("--format", default="json,console",
                    help="json,sarif,html,markdown,console")
    rs.add_argument("--limit", type=int, default=40, help="findings shown on the console")

    # No --out or --format here, unlike the local gate: /v1/gate answers with the
    # verdict and the claim ledger, not a report, so those flags would take a
    # value and do nothing with it.
    rg = remote_common(remote_sub.add_parser("gate", help="run the policy gate remotely"))
    rg.add_argument("target", nargs="?", default=".", help="directory to send")
    rg.add_argument("--profile", default="ci")
    rg.add_argument("--only", default="")
    rg.add_argument("--skip", default="")

    rq = remote_common(remote_sub.add_parser(
        "review-queue", help="draw a review queue from a report; marks nothing"))
    rq.add_argument("report", help="path to a report.json")
    rq.add_argument("--limit", type=int, default=20)
    rq.add_argument("--rule", default="", help="only findings whose rule id contains this")
    rq.add_argument("--out", help="write the queue here instead of standard output")

    rh = remote_common(remote_sub.add_parser(
        "health", help="what the server is, and what it will allow"))
    rh.set_defaults(needs_key=False)



def _resolve_reviewer(explicit: str) -> str:
    """Who is answerable for a verdict. Empty means the command must refuse.

    An explicit `--reviewer` wins. Otherwise git's configured identity, which
    is the name already attached to every other change here and to the ledger
    itself, since `.arbiter/knowledge.json` is committed. There is deliberately
    no fallback beyond that: a default like the OS username would put a name on
    a permanent record without anyone choosing it.
    """
    if (explicit or "").strip():
        return explicit.strip()
    import subprocess
    for key in ("user.email", "user.name"):
        try:
            out = subprocess.run(["git", "config", "--get", key],
                                 capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return ""
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return ""


def _terminal_or_refuse(batch: bool, command: str, batch_flag: str = "--batch") -> bool:
    """Is there plausibly a person on the other end of this adjudication?

    `stdin.isatty()` is the only signal available, and it is a weak one: a
    determined caller allocates a pty and walks straight through. It is not
    meant to stop that. It stops the accidental case — a piped script, a CI
    step, an agent shelling out — which is the one that actually happens, and
    which produces permanent marks nobody remembers making.

    The override is deliberately easy, because an override an agent cannot pass
    is an override a person cannot pass either. What it buys is not resistance
    but a record: an overridden verdict lands with an entry point that says so.
    """
    if sys.stdin.isatty():
        return True
    if batch:
        print(f"arbiter: {command} running without a terminal; recording these "
              f"verdicts as a batch import.", file=sys.stderr)
        return True
    print(f"arbiter: {command} refuses to adjudicate without a terminal. "
          f"Verdicts are permanent\n  and this ledger refuses to re-adjudicate, "
          f"so a mark made by a script is a mark nobody\n  can correct. If you "
          f"mean it, pass {batch_flag} — the verdicts are recorded as a batch "
          f"import\n  and stay findable as one.", file=sys.stderr)
    return False


def _reviewer_or_refuse(explicit: str) -> str | None:
    reviewer = _resolve_reviewer(explicit)
    if reviewer:
        return reviewer
    print("arbiter: no reviewer. This ledger refuses re-adjudication, so a mark "
          "is permanent —\n  it does not accept anonymous ones. Pass --reviewer "
          "or set `git config user.email`.", file=sys.stderr)
    return None


def _load_adapters(disabled: bool) -> None:
    if not disabled:
        register_adapters()


def _append_step_summary(report: Report) -> None:
    """Append the pull-request comment to the job summary, when there is one."""
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    from .diff import render_pr_comment
    with open(summary, "a", encoding="utf-8") as fh:
        fh.write(render_pr_comment(report) + "\n")


def cmd_scan(args, gate_mode: bool = False) -> int:
    if not args.targets and not args.system:
        print("arbiter: give a target path or --system", file=sys.stderr)
        return EXIT_ERROR
    _load_adapters(args.no_adapters)
    root = args.targets[0] if args.targets else None
    config = load_config(args.config, root if root and not root.startswith("http") else None)

    report = run_scan(
        targets=args.targets,
        config=config,
        system_path=args.system,
        profile=args.profile,
        only=[s for s in args.only.split(",") if s],
        skip=[s for s in args.skip.split(",") if s],
        baseline=args.baseline,
        use_adapters=not args.no_adapters,
        plan_paths=list(getattr(args, "tfplan", []) or []),
        knowledge_path=getattr(args, "knowledge", None),
        pin_knowledge=getattr(args, "pin_knowledge", None),
        changed_since=getattr(args, "changed", None),
        only_files=[s for s in (getattr(args, "only_files", "") or "").split(",") if s],
        out_dir=args.out,
        use_cache=not getattr(args, "no_cache", False),
        cache_path=getattr(args, "cache_path", None),
        verify_cache=bool(getattr(args, "verify_cache", False)),
    )

    formats = _formats(args.format)
    open_report = getattr(args, "open", None)
    if open_report is None:
        # No explicit --open/--no-open: default to opening only at a real
        # terminal, so scripted/CI invocations (no tty) are unaffected.
        open_report = sys.stdout.isatty()
    if open_report and "html" not in formats:
        formats = [*formats, "html"]
    github = getattr(args, "github", None)
    if github is None:
        github = os.environ.get("GITHUB_ACTIONS") == "true"
    if github and "annotations" not in formats:
        formats = [*formats, "annotations"]
    written = write_all(report, args.out, [f for f in formats if f != "console"],
                        sarif_min_severity=getattr(args, "sarif_min_severity", None))
    if "console" in formats or not formats:
        print(render_console(report, limit=getattr(args, "limit", 40)))
    for kind, path in written.items():
        print(f"  wrote {kind}: {path}")
    if not getattr(args, "no_history", False):
        print(f"  appended history: {history.append(report, args.out)}")
    if written:
        print()
    if "annotations" in formats:
        # After the console output so the log reads top-down; the runner
        # turns each line into an inline annotation wherever it appears.
        annotations = render_annotations(report)
        if annotations:
            print(annotations)
    if github:
        _append_step_summary(report)

    if open_report and "html" in written:
        try:
            webbrowser.open(Path(written["html"]).resolve().as_uri())
        except Exception as exc:  # noqa: BLE001 - a browser that will not open must never fail the scan
            print(f"  (could not open the report automatically: {exc})")

    if gate_mode and not (report.gate or {}).get("passed"):
        return EXIT_GATE_FAIL
    return EXIT_OK


def cmd_dashboard(args) -> int:
    rows = history.load(Path(args.history))
    latest = rows[-1] if rows else {}
    title = f"Arbiter \u2014 {latest.get('system') or 'run history'}"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(history.render_dashboard(rows, title), encoding="utf-8")
    if rows:
        print(f"  {len(rows)} run(s) in {args.history}")
    else:
        print(f"  no runs recorded in {args.history} yet; the page says so")
    print(f"  wrote dashboard: {out}\n")
    return EXIT_OK


def cmd_ab(args) -> int:
    _load_adapters(args.no_adapters)
    targets = list(args.target)
    arms = list(args.arm)
    name = args.name
    system_path = args.system
    config_path = args.config

    if args.spec:
        spec = load_ab_spec(args.spec)
        base = Path(args.spec).parent
        name = name or spec.get("ab", Path(args.spec).stem)
        system_path = system_path or spec.get("system")
        config_path = config_path or spec.get("config")
        for t in spec.get("targets", []):
            cand = Path(t)
            targets.append(str(cand if cand.is_absolute() else (base / cand).resolve()))
        arms = [arm_from_dict(a) for a in spec.get("arms", [])] + arms

    if len(arms) != 2:
        print(f"arbiter ab: need exactly two arms, got {len(arms)}", file=sys.stderr)
        return EXIT_ERROR
    if not targets and not system_path:
        print("arbiter ab: give --target or --system", file=sys.stderr)
        return EXIT_ERROR

    config = load_config(config_path, targets[0] if targets else None)
    result = run_ab(name or "comparison", targets, arms[0], arms[1], config, system_path)

    print(render_ab_console(result))
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "ab.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    (outdir / "ab.html").write_text(render_ab_html(result), encoding="utf-8")
    print(f"  wrote json: {outdir / 'ab.json'}")
    print(f"  wrote html: {outdir / 'ab.html'}\n")

    if result.a.error or result.b.error:
        return EXIT_ERROR
    return EXIT_OK


def cmd_probes(args) -> int:
    _load_adapters(args.no_adapters)
    import shutil as _sh

    from .engine import resolve_targets
    from .graph import build_graph
    from .inventory import build_inventory

    name, repos, tmps, manifest = resolve_targets([args.target], None)
    try:
        inv = build_inventory(repos)
        ctx = ProbeContext(repos=repos, inventory=inv, graph=build_graph(inv), config={}, system=manifest)
        print(f"\n  stacks detected: {', '.join(sorted(inv.stacks)) or 'none'}\n")
        print(f"  {'PROBE':<18} {'CHECKS':>6}  {'DIMENSIONS':<28} STATUS")
        for probe in REGISTRY:
            ok, why = probe.applicable(ctx)
            missing = [b for b in probe.binaries if _sh.which(b) is None]
            if missing:
                status = f"skip — missing binary: {', '.join(missing)}"
            elif not ok:
                status = f"skip — {why}"
            elif probe.network:
                status = "ready (needs network profile)"
            else:
                status = "ready"
            print(f"  {probe.name:<18} {probe.checks:>6}  {','.join(probe.dimensions):<28} {status}")
        print()
    finally:
        for d in tmps:
            _sh.rmtree(d, ignore_errors=True)
    return EXIT_OK


def cmd_baseline(args) -> int:
    report = Report.from_dict(json.loads(Path(args.report).read_text(encoding="utf-8")))
    n = write_baseline(report, args.out)
    print(f"  baseline written: {args.out} ({n} finding ids)")
    return EXIT_OK


def cmd_diff(args) -> int:
    from .diff import diff_reports, render_diff_console, render_pr_comment
    before = Report.from_dict(json.loads(Path(args.before).read_text(encoding="utf-8")))
    after = Report.from_dict(json.loads(Path(args.after).read_text(encoding="utf-8")))
    d = diff_reports(before, after)
    formats = _formats(args.format)

    if "console" in formats:
        print(render_diff_console(d))
    if args.out:
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        if "json" in formats:
            (outdir / "diff.json").write_text(json.dumps(d.to_dict(), indent=2), encoding="utf-8")
            print(f"  wrote json: {outdir / 'diff.json'}")
        if "pr-comment" in formats or "markdown" in formats:
            (outdir / "pr-comment.md").write_text(render_pr_comment(after, d), encoding="utf-8")
            print(f"  wrote pr-comment: {outdir / 'pr-comment.md'}")
    elif "pr-comment" in formats or "markdown" in formats:
        print(render_pr_comment(after, d))
    return EXIT_OK


def cmd_feedback(args) -> int:
    from .learn import Knowledge, record
    report = Report.from_dict(json.loads(Path(args.report).read_text(encoding="utf-8")))
    by_id = {f.id: f for f in report.findings}
    knowledge = Knowledge.load(args.knowledge)
    verdict = "false_positive" if args.false_positive else "true_positive"
    reviewer = _reviewer_or_refuse(args.reviewer)
    if reviewer is None:
        return EXIT_ERROR
    if not _terminal_or_refuse(args.batch, "feedback"):
        return EXIT_ERROR
    entry_point = "feedback" if sys.stdin.isatty() else "feedback-batch"

    recorded, repeated, unknown = 0, 0, []
    for fid in args.finding_ids:
        target = by_id.get(fid) or next((f for f in report.findings if f.id.endswith(fid)), None)
        if target is None:
            unknown.append(fid)
            continue
        if record(knowledge, target, verdict, args.note,
                  reviewer=reviewer, entry_point=entry_point):
            recorded += 1
        else:
            repeated += 1

    version = knowledge.save(args.knowledge)
    print()
    print(f"  recorded {recorded} as {verdict.replace('_', ' ')}"
          + (f", {repeated} already adjudicated" if repeated else ""))
    for fid in unknown:
        print(f"  not found in the report: {fid}")
    print(f"  knowledge version now {version}")
    print("  scans pick this up on the next run; pin it with --pin-knowledge to freeze\n")
    return EXIT_OK if not unknown else EXIT_ERROR


def cmd_learn(args) -> int:
    from .claims import observations_needed
    from .learn import MIN_OBSERVATIONS, Knowledge, calibrated_confidence
    knowledge = Knowledge.load(args.knowledge)
    print()
    print(f"  knowledge version {knowledge.version_hash()}"
          + (f"  (updated {knowledge.updated})" if knowledge.updated else "  (empty)"))
    print(f"  {len(knowledge.adjudicated)} finding(s) adjudicated across "
          f"{len(knowledge.rules)} rule(s)\n")

    if not knowledge.rules:
        print("  Nothing learned yet. Adjudicate findings with:")
        print("    arbiter feedback <finding-id> --false-positive --note 'why'\n")
        return EXIT_OK

    need = observations_needed(args.target)
    print(f"  {'RULE':<46}{'N':>5}{'TP':>5}{'FP':>5}{'PRECISION':>11}{'LOWER':>9}  STATUS")
    for rule_id, s in sorted(knowledge.rules.items(), key=lambda kv: -kv[1].observations):
        conf = calibrated_confidence(s)
        status = (f"calibrated -> {conf}" if conf
                  else f"unproven (needs {MIN_OBSERVATIONS - s.observations} more)")
        print(f"  {rule_id[:45]:<46}{s.observations:>5}{s.true_positives:>5}"
              f"{s.false_positives:>5}{(s.precision or 0):>11.3f}"
              f"{(s.precision_lower_bound or 0):>9.3f}  {status}")

    total = sum(s.observations for s in knowledge.rules.values())
    print(f"\n  Claiming an error rate below {args.target:g} with 95% confidence needs")
    print(f"  ~{need:,} clean observations per rule. Best rule so far: "
          f"{max((s.observations for s in knowledge.rules.values()), default=0):,}.")
    print(f"  Total adjudicated observations: {total:,}\n")
    return EXIT_OK


def cmd_verify(args) -> int:
    from .claims import INVARIANTS, build_claims, verify
    report = Report.from_dict(json.loads(Path(args.report).read_text(encoding="utf-8")))
    config = load_config(args.config) if args.config else {}
    claims = build_claims(report)
    violations = verify(report, config)

    print()
    if args.show_claims:
        print(f"  {'SCOPE':<10}{'CLAIM':<30}{'BASIS':>6}{'ABST':>6}  STATEMENT")
        for c in claims:
            print(f"  {c.scope:<10}{c.id:<30}{len(c.basis):>6}{len(c.abstained):>6}  {c.statement[:56]}")
        print()

    print(f"  {len(claims)} claim(s) checked against {len(INVARIANTS)} invariant(s)")
    if not violations:
        print("  integrity OK — no claim outruns its basis\n")
        return EXIT_OK
    print(f"  {len(violations)} VIOLATION(S)\n")
    for v in violations:
        print(f"    {v.invariant}  {v.claim_id}")
        print(f"           {INVARIANTS.get(v.invariant, '')}")
        print(f"           {v.detail}")
    print()
    return EXIT_GATE_FAIL


def cmd_review(args) -> int:
    from .learn import MIN_OBSERVATIONS, Knowledge
    from .review import apply as apply_marks
    from .review import newly_proven, render, select

    path = Path(args.report)
    if not path.is_file():
        print(f"arbiter: no report at {path}. Run `arbiter scan` first.", file=sys.stderr)
        return EXIT_ERROR
    report = Report.from_dict(json.loads(path.read_text(encoding="utf-8")))
    knowledge = Knowledge.load(args.knowledge)

    if args.apply:
        marked = Path(args.apply)
        if not marked.is_file():
            print(f"arbiter: no review file at {marked}", file=sys.stderr)
            return EXIT_ERROR
        reviewer = _reviewer_or_refuse(args.reviewer)
        if reviewer is None:
            return EXIT_ERROR
        before = {r: s.observations for r, s in knowledge.rules.items()}
        res = apply_marks(marked.read_text(encoding="utf-8"), report.findings,
                          knowledge, args.note, reviewer=reviewer)
        version = knowledge.save(args.knowledge)
        print()
        print(f"  {res['marked']} marked, {res['recorded']} recorded"
              + (f", {res['already_adjudicated']} already adjudicated"
                 if res["already_adjudicated"] else ""))
        for rule, counts in sorted(res["per_rule"].items()):
            print(f"    {rule:<48}{counts['true']:>3} real  {counts['false']:>3} not")
        if res["unknown"]:
            print(f"    {len(res['unknown'])} id(s) not in this report — ignored")
        proven = newly_proven(knowledge, before)
        for rule in proven:
            print(f"\n  {rule} has reached {MIN_OBSERVATIONS} adjudications "
                  "and is no longer reported as unproven.")
        if args.ledger:
            from .ledger import draft_entries
            try:
                drafted = draft_entries(report.findings,
                                        res["recorded_ids"].get("true_positive", []),
                                        Path(args.ledger))
            except ValueError as exc:
                print(f"\n  ledger not written: {exc}", file=sys.stderr)
                return EXIT_ERROR
            if drafted:
                print(f"\n  drafted {len(drafted)} open failure-ledger entr"
                      f"{'y' if len(drafted) == 1 else 'ies'} in {args.ledger}: "
                      f"{', '.join(drafted)}")
                print("  fill in the root cause, fix and regression test with "
                      "`omni failure update <id> ...`")
        if not res["recorded"]:
            print("\n  Nothing recorded. Marks go inside the brackets: [y] or [n].")
        print(f"\n  knowledge is now {version}")
        return EXIT_OK

    picked = select(report.findings, knowledge, limit=args.limit, rule=args.rule)
    if not picked:
        print("\n  Nothing left to review in this report — every finding here has "
              "already been adjudicated.")
        return EXIT_OK

    # Where to read code context from. The report records each repository's
    # path; --repo overrides it for a report produced somewhere else.
    repo_paths = {r.id: r.path for r in report.repos}
    for spec in args.repo:
        rid, _, path = spec.partition("=")
        repo_paths[rid or "root"] = path or rid

    if args.interactive:
        from .learn import record
        from .review_ui import run_terminal
        reviewer = _reviewer_or_refuse(args.reviewer)
        if reviewer is None:
            return EXIT_ERROR
        # No override here, because there is no coherent one: a keypress UI
        # driven by something that is not a keyboard is not an overridden
        # interactive review, it is a batch import wearing its name. --apply
        # is the batch path, and it records itself as one.
        if not _terminal_or_refuse(False, "review --interactive",
                                   batch_flag="--apply with a marked review file"):
            return EXIT_ERROR
        before = {r: st.observations for r, st in knowledge.rules.items()}
        marks = run_terminal(picked, knowledge, repo_paths)
        by_id = {f.id: f for f in picked}
        recorded = 0
        for fid, verdict in marks.items():
            if record(knowledge, by_id[fid], verdict, args.note,
                      reviewer=reviewer, entry_point="review-interactive"):
                recorded += 1
        version = knowledge.save(args.knowledge)
        print(f"\n  {recorded} recorded of {len(marks)} marked")
        for rule in newly_proven(knowledge, before):
            print(f"  {rule} has reached {MIN_OBSERVATIONS} adjudications "
                  "and is no longer reported as unproven.")
        print(f"  knowledge is now {version}\n")
        return EXIT_OK

    if args.html:
        from .review_ui import render_html
        out = Path(args.html)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"arbiter review {args.report} --apply review.md"
        out.write_text(render_html(picked, knowledge, repo_paths, cmd), encoding="utf-8")
        rules = {f.rule_id for f in picked}
        print()
        print(f"  wrote {out}")
        print(f"  {len(picked)} findings across {len(rules)} rules. Open it in a "
              "browser — it needs no server and no network.")
        print("  Mark them, save the text it gives you as review.md, then:")
        print(f"\n      arbiter review {args.report} --apply review.md")
        return EXIT_OK

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(picked, knowledge, str(out)), encoding="utf-8")
    rules = {f.rule_id for f in picked}
    print()
    print(f"  wrote {out}")
    print(f"  {len(picked)} findings across {len(rules)} rules, chosen to move the "
          "most rules past")
    print(f"  the {MIN_OBSERVATIONS}-observation line. Mark them, then:")
    print(f"\n      arbiter review --apply {out}")
    return EXIT_OK


def cmd_controls(args) -> int:
    """Control coverage, reported so the gap is as visible as the passes.

    The ordering is deliberate and is the whole point of the command. Violated
    and not-assessed come first, because a control nothing checked is the thing
    a reader most needs to know and the thing every other compliance report
    buries. Satisfied comes last.
    """
    from .controls import (
        NO_COVERAGE,
        NOT_ASSESSED,
        SATISFIED,
        STATE_MEANING,
        STATES,
        VIOLATED,
        evaluate_all,
        load_frameworks,
    )
    from .core import Finding

    if args.list:
        for fw in load_frameworks(args.packs):
            print(f"  {fw.id:<24}{fw.enumerated:>4} enumerated of "
                  f"{fw.declared_controls:<5} {fw.title}")
        return EXIT_OK

    path = Path(args.report)
    if not path.is_file():
        print(f"arbiter: no report at {path}. Run `arbiter scan` first.", file=sys.stderr)
        return EXIT_ERROR
    doc = json.loads(path.read_text(encoding="utf-8"))
    findings = [Finding.from_dict(f) for f in doc.get("findings", [])]
    outcomes = [_outcome_from_dict(o) for o in doc.get("probes", [])]

    results = evaluate_all(findings, outcomes, only=args.framework, extra_dirs=args.packs)
    if not results:
        print("arbiter: no control packs matched", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(results, indent=2))
        return EXIT_OK

    wanted = {s.lower() for s in args.state}
    for res in results:
        fw, counts = res["framework"], res["counts"]
        print(f"\n  {fw['title']}")
        print(f"  {fw['id']}  baseline {fw['baseline'] or '-'}")
        print(f"  {'-' * 72}")
        for state in STATES:
            print(f"    {counts[state]:>4}  {state:<17}{STATE_MEANING[state]}")
        if res["not_enumerated"]:
            print(f"    {res['not_enumerated']:>4}  not_enumerated   "
                  "not in this pack; assess by other means")
        print(f"  {'-' * 72}")
        print(f"    {res['declared_total']:>4}  controls in this baseline "
              f"({fw['declared_source'].strip()[:60]})")
        pct = res["assessed_fraction"] * 100
        print(f"\n    {pct:.1f}% of the baseline carries evidence from this scan "
              f"({counts[SATISFIED] + counts[VIOLATED]} of {res['declared_total']}).")
        print("    Every other control is unevidenced here. That is a statement "
              "about\n    this tool's reach, not about the system's security.")

        rows = [r for r in res["controls"]
                if not wanted or r["state"] in wanted]
        order = {s: i for i, s in enumerate(STATES)}
        rows.sort(key=lambda r: (order.get(r["state"], 9), r["id"]))
        shown = [r for r in rows if r["state"] in (VIOLATED, NOT_ASSESSED, NO_COVERAGE)] \
            if not wanted else rows
        if shown:
            print()
            for r in shown:
                print(f"    [{r['state'].upper()}] {r['id']}  {r['title']}")
                if r.get("reason"):
                    print(f"        {r['reason'][:100]}")
                if r["state"] in (VIOLATED, SATISFIED) and r.get("residual"):
                    print(f"        still needs a person: {r['residual'].strip()[:100]}")
    return EXIT_OK


def _outcome_from_dict(d: dict):
    from .core import ProbeOutcome
    return ProbeOutcome(
        name=d.get("name", ""), dimensions=d.get("dimensions", []),
        checks=int(d.get("checks", 0)), status=d.get("status", "skipped"),
        reason=d.get("reason", ""), version=d.get("version", ""),
        applicable=bool(d.get("applicable", True)),
    )


def cmd_explain(args) -> int:
    report = Report.from_dict(json.loads(Path(args.report).read_text(encoding="utf-8")))
    for f in report.findings:
        if f.id == args.finding_id or f.id.endswith(args.finding_id):
            print()
            print(f"  {f.severity.upper()}  {f.title}")
            print(f"  {f.id}   {f.rule_id}   [{f.provenance}, {f.confidence} confidence]")
            print(f"  repo {f.repo_id}   {f.location.short()}"
                  + (f"   {f.location.logical}" if f.location.logical else ""))
            print()
            if f.description:
                print(f"  {f.description}\n")
            if f.remediation:
                print(f"  Fix: {f.remediation}\n")
            if f.controls:
                print(f"  Controls: {', '.join(f.controls)}\n")
            if f.evidence:
                print(f"  Evidence: {f.evidence}\n")
            if f.related:
                print("  Also at: " + ", ".join(r.short() for r in f.related) + "\n")
            return EXIT_OK
    print(f"arbiter: no finding {args.finding_id} in {args.report}", file=sys.stderr)
    return EXIT_ERROR


def cmd_api(args) -> int:
    """Serve the hosted API, or issue the keys that reach it.

    A key is scoped to a user and nothing else, and one user holds one live key.
    The owner mints them one at a time; there is no sign-up, because manual
    distribution is the point.
    """
    from pathlib import Path as _Path

    from . import api
    path = _Path(args.keys).expanduser() if args.keys else None

    if args.api_cmd == "serve":
        return api.serve(host=args.host, port=args.port, key_path=path,
                         certfile=args.cert, keyfile=args.tls_key,
                         audit_path=args.audit, audit=not args.no_audit,
                         limiter=args.limiter)

    if args.key_cmd == "add":
        raw, record = api.mint_key(args.user, path,
                                   None if args.no_expiry else args.expires_days,
                                   replace=args.replace)
        print(f"key {record['id']} issued to {record['user']}")
        if record["replaced"]:
            print(f"revoked their previous key {record['replaced']}")
        print(raw)
        if record["expires"]:
            print(f"\nExpires {record['expires']}.")
        else:
            print("\nThis key never expires; revoke it by hand when it is done with.")
        print("This is the only time the key is shown; only its hash is stored.")
        print("Send it to the recipient over a channel you trust.")
        return EXIT_OK
    if args.key_cmd == "list":
        keys = api.list_keys(path)
        if not keys:
            print("no keys issued")
        for record in keys:
            state = record["state"]
            if state == "revoked":
                state = f"revoked {record['revoked']}"
            elif state == "expired":
                state = f"expired {record['expires']}"
            elif record.get("expires"):
                state = f"active until {record['expires']}"
            print(f"{record['id']}  {record['created']}  {state:34}  {record['user']}")
        return EXIT_OK
    if args.key_cmd == "revoke":
        if api.revoke_key(args.id, path):
            print(f"revoked {args.id}")
            return EXIT_OK
        print(f"arbiter: no active key {args.id}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR


def cmd_bundle(args) -> int:
    """Build or verify an air-gapped bundle.

    The bundle carries Arbiter and its runtime dependencies and nothing else:
    the analyzers stay out until the redistribution review (ARB-005) is done,
    and the manifest says so in words. See `bundle.py`.
    """
    from . import bundle

    try:
        if args.bundle_cmd == "build":
            manifest = bundle.build(args.out, include_deps=not args.no_deps_download,
                                    source=args.source)
            print(f"bundle written to {Path(args.out).expanduser().resolve()}: "
                  f"arbiter {manifest['arbiter_version']}, "
                  f"{len(manifest['files'])} file(s)")
            if not manifest["dependencies_included"]:
                print("dependency wheels were not downloaded (--no-deps-download); "
                      "the target needs PyYAML already")
            print(f"analyzers: {manifest['analyzers']}")
            return EXIT_OK
        result = bundle.verify(args.bundle_dir)
        for line in result.lines():
            print(line)
        return EXIT_OK if result.ok else EXIT_GATE_FAIL
    except bundle.BundleError as exc:
        print(f"arbiter: {exc}", file=sys.stderr)
        return EXIT_ERROR


def cmd_remote(args) -> int:
    """Run against a hosted Arbiter and render the answer as though it were local.

    Nothing is analysed here. The report the server sends back is rebuilt and
    handed to the same writers `arbiter scan` uses, so `--out` and `--format`
    mean what they have always meant and the console output is the same text.
    The only visible difference is that this machine never had the analyzers.
    """
    config = Path(args.client_config).expanduser() if args.client_config else None

    try:
        server, key = client.load_settings(
            args.server, args.key, config,
            need_key=getattr(args, "needs_key", True))

        if args.remote_cmd == "health":
            state = client.health(server, args.cacert)
            custody = ("keeps nothing after a scan" if state.get("retains_nothing")
                       else "retains what is uploaded")
            print(server)
            print(f"  arbiter {state.get('version', '?')}, {custody}"
                  + (", free to use" if state.get("free") else ""))
            for name, value in (state.get("limits") or {}).items():
                shown = f"{value:,}" if isinstance(value, int) else str(value)
                print(f"  {name.replace('_', ' '):<26} {shown}")
            return EXIT_OK

        if args.remote_cmd == "review-queue":
            report = json.loads(Path(args.report).read_text(encoding="utf-8"))
            answer = client.review_queue(server, key, report, args.limit,
                                         args.rule, args.cacert)
            if not answer.get("entry_count"):
                print(answer.get("note") or "nothing to review")
                return EXIT_OK
            queue = answer.get("queue_markdown", "")
            if args.out:
                out = Path(args.out).expanduser()
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(queue, encoding="utf-8")
                print(f"  wrote queue: {out}  "
                      f"({answer['entry_count']} to adjudicate)")
            else:
                print(queue)
            return EXIT_OK

        # Ask what the server allows before packing anything, so an oversized
        # target is refused here rather than after a long upload earns a 413.
        limits = (client.health(server, args.cacert).get("limits") or {})
        cap = limits.get("max_upload_bytes")
        send = client.gate if args.remote_cmd == "gate" else client.scan
        answer = send(server, key, Path(args.target), args.profile,
                      args.only, args.skip, args.cacert, cap)
    except client.ClientError as exc:
        print(f"arbiter: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.remote_cmd == "gate":
        passed = bool(answer.get("passed"))
        print("gate: PASSED" if passed else "gate: FAILED")
        for name, value in sorted((answer.get("gate") or {}).items()):
            if isinstance(value, (str, int, float, bool)):
                print(f"  {name.replace('_', ' '):<26} {value}")
        claims = answer.get("claims") or []
        if claims:
            print(f"  {'claims in the ledger':<26} {len(claims)}")
        return EXIT_OK if passed else EXIT_GATE_FAIL

    report = client.report_from(answer)
    formats = _formats(args.format)
    written = write_all(report, args.out, [f for f in formats if f != "console"],
                        sarif_min_severity=getattr(args, "sarif_min_severity", None))
    if "console" in formats or not formats:
        print(render_console(report, limit=args.limit))
    for kind, path in written.items():
        print(f"  wrote {kind}: {path}")
    if written:
        print()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "scan":
            return cmd_scan(args)
        if args.cmd == "gate":
            args.limit = 40
            return cmd_scan(args, gate_mode=True)
        if args.cmd == "dashboard":
            return cmd_dashboard(args)
        if args.cmd == "ab":
            return cmd_ab(args)
        if args.cmd == "probes":
            return cmd_probes(args)
        if args.cmd == "baseline":
            return cmd_baseline(args)
        if args.cmd == "diff":
            return cmd_diff(args)
        if args.cmd == "verify":
            return cmd_verify(args)
        if args.cmd == "feedback":
            return cmd_feedback(args)
        if args.cmd == "learn":
            return cmd_learn(args)
        if args.cmd == "review":
            return cmd_review(args)
        if args.cmd == "controls":
            return cmd_controls(args)
        if args.cmd == "explain":
            return cmd_explain(args)
        if args.cmd == "api":
            return cmd_api(args)
        if args.cmd == "mcp":
            from pathlib import Path as _Path

            from . import mcp as mcp_surface
            if not args.http:
                # Stdio serves one agent on this machine, which already has
                # whatever privileges this process has; there is nobody to
                # identify and nothing to confine.
                return mcp_surface.serve()
            return mcp_surface.serve_http(
                host=args.host, port=args.port,
                key_path=_Path(args.keys).expanduser() if args.keys else None,
                root=args.root, certfile=args.cert, keyfile=args.tls_key,
                audit_path=args.audit, audit=not args.no_audit,
                path=args.path, allowed_hosts=args.allowed_hosts,
                limiter=args.limiter)
        if args.cmd == "bundle":
            return cmd_bundle(args)
        if args.cmd == "remote":
            return cmd_remote(args)
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - the top-level handler: one line on stderr instead of a traceback
        print(f"arbiter: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
