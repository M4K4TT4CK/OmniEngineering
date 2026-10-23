#!/usr/bin/env python3
"""Generate the AI-written-code corpus with a separate model run.

    python tools/ai_corpus_generate.py --out /scratch/gen            # all 25 apps
    python tools/ai_corpus_generate.py --out /scratch/gen --only go/job-queue
    python tools/ai_corpus_generate.py --out /scratch/gen --manifest-only
    python tools/ai_corpus_generate.py --set heldout --models opus,haiku \
        --budget-usd 25 --out scratchpad/heldout/gen        # the held-out batch
    python tools/ai_corpus_generate.py --set heldout --out scratchpad/heldout/gen --install

`--set heldout` is the held-out batch (fixtures/ai-corpus-heldout/): eight
other tasks, and two generator models assigned alternately over the
language x task grid so each language and each task gets both. Its findings
are never inspected by rule authors; see docs/testing-harness.md.

Each app is one headless Claude Code run (`claude -p`) in an empty directory,
given a neutral "build this" prompt: no mention of security, error handling,
review or the defects the corpus is meant to measure. The run is limited to
the file tools (Read, Write, Edit, Glob) so it cannot execute, build or
install anything, and it loads no project settings, skills or MCP servers
that could hint at what is being measured.

The output is data. It is copied into fixtures/ai-corpus/<lang>/<task>/ by
hand (or with --install), never built, installed or run; the labels in
fixtures/ai-corpus/<lang>/<task>/labels.json are written by reading it.
The exact prompt, model and date land in fixtures/ai-corpus/manifest.json.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures" / "ai-corpus"
HELDOUT = ROOT / "fixtures" / "ai-corpus-heldout"

LANGUAGES = {
    "python": "Python",
    "typescript": "TypeScript (Node.js)",
    "go": "Go",
    "java": "Java",
    "ruby": "Ruby",
}

# Interview-style tasks. Described by what the service does, never by how it
# should be hardened.
TASKS = {
    "url-shortener": (
        "a URL shortener web service. It should have an HTTP API to create a short "
        "code for a long URL (optionally with a custom alias), redirect a short code "
        "to its URL, and return stats for a code: click count, creation time and the "
        "title of the target page (fetched from the long URL when the link is created). "
        "Persist links in SQLite."
    ),
    "notes-api": (
        "a REST API for personal notes with user accounts. Users can sign up and log "
        "in with a username and password and receive a token; with the token they can "
        "create, list, search, update and delete their own notes. Each user is limited "
        "to 60 requests per minute. Persist data in SQLite."
    ),
    "file-upload": (
        "a file upload service. It should have HTTP endpoints to upload a file, list "
        "uploaded files with their size and upload time, download a file by name, "
        "delete a file, and generate a thumbnail for uploaded images. Files are stored "
        "on local disk in a configurable directory."
    ),
    "job-queue": (
        "a background job queue. An HTTP API accepts jobs (a type and a JSON payload), "
        "returns a job id, and reports job status and result. A pool of worker threads "
        "processes jobs concurrently, retries failed jobs up to three times, and "
        "supports two job types: 'resize-image' (given a file path) and 'send-email' "
        "(given recipient, subject, body; use SMTP settings from the environment). "
        "Jobs are kept in memory."
    ),
    "webhook-receiver": (
        "a webhook receiver for payment events. It exposes an HTTP endpoint that "
        "receives JSON events from a payment provider, verifies the HMAC-SHA256 "
        "signature in the X-Signature header using a shared secret, stores each event "
        "in SQLite, and forwards successful-payment events to a callback URL "
        "configured per merchant. It also has an endpoint to list a merchant's "
        "recent events."
    ),
}

# The held-out batch: tasks that differ from the development set above.
HELDOUT_TASKS = {
    "tenant-notes": (
        "a multi-tenant notes service. Each organisation has users with one of three "
        "roles: admin, editor or viewer. Users log in with an email and password and "
        "receive a token. Editors and admins can create, update and delete notes in "
        "their organisation; viewers can only read them; admins can invite users to "
        "their organisation and change their roles. Notes can be listed, fetched by id "
        "and searched by text. Persist data in SQLite."
    ),
    "payment-webhook": (
        "a service that receives payment webhooks from a payment provider. Each event "
        "is a JSON body with an event id, a type (payment.succeeded, payment.failed, "
        "refund.created), an order id and an amount, signed with HMAC-SHA256 in a "
        "header using a shared secret. The provider retries deliveries, so the same "
        "event may arrive more than once; each event must be applied to its order "
        "exactly once. Keep orders and their payment status in SQLite and expose an "
        "endpoint to look up an order's status."
    ),
    "thumbnail-service": (
        "an image thumbnail service. Clients POST either an image file or the URL of "
        "an image, plus a list of widths; the service returns URLs of thumbnails at "
        "those widths, generating them on first request and caching them on local "
        "disk. Thumbnails are served over HTTP from the cache."
    ),
    "csv-import": (
        "a CSV import job for a customer database. An HTTP endpoint accepts an "
        "uploaded CSV file of customers (name, email, phone, country, signup date), "
        "starts an import that runs in the background, and returns an import id. The "
        "import validates each row, inserts or updates customers by email in SQLite, "
        "and records per-row errors. Another endpoint reports an import's progress and "
        "its errors."
    ),
    "oauth-login": (
        "a web app that lets users log in with GitHub using OAuth 2.0. It has a login "
        "route that redirects to GitHub, a callback route that exchanges the code for "
        "an access token, fetches the user's profile and creates or updates a local "
        "user record, then starts a session and redirects the user back to the page "
        "they came from. A profile page shows the logged-in user's details and a "
        "logout route ends the session. Persist users in SQLite."
    ),
    "admin-export": (
        "an admin reporting service for an online shop. Orders, customers and products "
        "are in SQLite. Admin users authenticate with an API key. Endpoints return a "
        "sales report for a date range grouped by day, week or month, the top products "
        "by revenue, and an export of orders in CSV or JSON with filters for status, "
        "customer and date range, sortable by a column the caller chooses."
    ),
    "chat-relay": (
        "a chat relay server using WebSockets. Clients connect, give a display name, "
        "and join named rooms; a message sent to a room is relayed to everyone else in "
        "it. The server keeps the last 50 messages of each room and sends them to a "
        "client when it joins. It also has an HTTP endpoint listing the rooms and how "
        "many people are in each."
    ),
    "backup-uploader": (
        "a scheduled backup uploader. It runs as a long-lived process that, on a "
        "schedule from a config file, archives a set of configured directories into a "
        "compressed tarball, uploads it to an S3-compatible object store, and deletes "
        "backups older than a configured retention period from the bucket. It writes a "
        "log line for each run and exposes a small HTTP status endpoint with the time "
        "and result of the last run. A command-line flag runs one backup immediately."
    ),
}

TASK_SETS = {"dev": (TASKS, CORPUS), "heldout": (HELDOUT_TASKS, HELDOUT)}

PROMPT = (
    "Build {task} Write it in {language}, using whatever libraries or framework you "
    "think are appropriate. Include automated tests and a short README. Keep it to "
    "a small number of files. Put all files in the current directory. Only write "
    "the files; do not run, build or install anything."
)

MODEL = "sonnet"
TOOLS = ["Read", "Write", "Edit", "Glob"]


def prompt_for(lang: str, task: str, tasks: dict | None = None) -> str:
    return PROMPT.format(task=(tasks or {**TASKS, **HELDOUT_TASKS})[task], language=LANGUAGES[lang])


def model_for(lang: str, task: str, tasks: dict, models: list[str]) -> str:
    """Alternate the generator models over the language x task grid, so every
    language and every task is written by each model (as near as the counts
    allow) and no model owns a whole language."""
    i = list(LANGUAGES).index(lang) + list(tasks).index(task)
    return models[i % len(models)]


def command(prompt: str, model: str, max_budget: float | None = None) -> list[str]:
    cmd = ["claude", "-p", prompt, "--model", model,
           "--tools", *TOOLS, "--allowedTools", *TOOLS,
           "--setting-sources", "", "--strict-mcp-config",
           "--output-format", "json"]
    if max_budget:
        cmd += ["--max-budget-usd", str(max_budget)]
    return cmd


class Budget:
    """A spend cap across parallel runs. A run starts only when what has been
    spent plus the worst case of every run in flight stays under the cap, so
    the cap holds even if each run spends its own per-run maximum. A run that
    does not fit waits for runs in flight to finish; it is refused only when
    nothing is in flight and it still does not fit."""

    def __init__(self, cap: float | None, per_run: float | None):
        self.cap, self.per_run = cap, per_run or 0.0
        self.spent, self.in_flight = 0.0, 0
        self._cond = threading.Condition()

    def _fits(self) -> bool:
        return self.cap is None or self.spent + (self.in_flight + 1) * self.per_run <= self.cap

    def start(self) -> bool:
        with self._cond:
            while not self._fits():
                if self.in_flight == 0:
                    return False
                self._cond.wait()
            self.in_flight += 1
            return True

    def finish(self, cost: float | None) -> None:
        with self._cond:
            self.in_flight -= 1
            self.spent += cost or 0.0
            self._cond.notify_all()


def generate(out: Path, lang: str, task: str, model: str, timeout: int,
             tasks: dict | None = None, max_budget: float | None = None) -> dict:
    target = out / lang / task
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    prompt = prompt_for(lang, task, tasks)
    started = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(command(prompt, model, max_budget), cwd=target, capture_output=True,
                          text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    resolved = model
    try:
        meta = json.loads(proc.stdout)
        resolved = next(iter(meta.get("modelUsage", {})), model)
        cost = meta.get("total_cost_usd")
    except (json.JSONDecodeError, StopIteration):
        cost = None
    files = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    return {"language": lang, "task": task, "prompt": prompt, "model_alias": model,
            "model": resolved, "date": started.date().isoformat(),
            "generated_at": started.isoformat(timespec="seconds"),
            "cli": "claude -p (Claude Code " + _cli_version() + ")",
            "tools": TOOLS, "exit_code": proc.returncode, "cost_usd": cost,
            "files": files}


def _cli_version() -> str:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True,
                              timeout=30).stdout.split()[0]
    except (OSError, IndexError, subprocess.SubprocessError):
        return "unknown"


MANIFEST_ABOUT = (
    "How each app under {tree}/<language>/<task>/app/ was produced. One headless "
    "Claude Code run per app (tools/ai_corpus_generate.py) in an empty directory, file "
    "tools only, no project settings, skills or MCP servers. The prompt is neutral: it "
    "describes the service and never mentions security, error handling or review. "
    "Output was copied in unchanged and is never built, installed or run. Dates are UTC."
)


def install(out: Path, corpus: Path, log: list[dict]) -> list[str]:
    """Copy each generated app into <corpus>/<lang>/<task>/app/ unchanged and
    write <corpus>/manifest.json from the generation log. Existing labels are
    left alone. Returns the apps installed."""
    done = []
    apps = []
    for r in sorted(log, key=lambda r: (r["language"], r["task"])):
        if r.get("exit_code") != 0 or not r.get("files"):
            continue
        src = out / r["language"] / r["task"]
        dst = corpus / r["language"] / r["task"] / "app"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        apps.append({k: v for k, v in r.items() if k != "exit_code"})
        done.append(f"{r['language']}/{r['task']}")
    corpus.mkdir(parents=True, exist_ok=True)
    tree = corpus.relative_to(ROOT).as_posix() if corpus.is_relative_to(ROOT) else corpus.name
    manifest = {"_about": MANIFEST_ABOUT.format(tree=tree), "generator": "tools/ai_corpus_generate.py",
                "apps": apps}
    (corpus / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="scratch directory for the raw output")
    ap.add_argument("--set", choices=sorted(TASK_SETS), default="dev", dest="task_set",
                    help="which task list: the development corpus or the held-out batch")
    ap.add_argument("--only", action="append", default=[], help="lang/task, repeatable")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--models", help="comma-separated models assigned alternately over the grid "
                                     "(overrides --model)")
    ap.add_argument("--budget-usd", type=float, help="stop starting runs before total spend could pass this")
    ap.add_argument("--per-run-usd", type=float, default=2.0,
                    help="per-run cap passed to claude --max-budget-usd (with --budget-usd)")
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--manifest-only", action="store_true", help="print the prompts and exit")
    ap.add_argument("--install", action="store_true",
                    help="copy --out into the corpus tree and write its manifest; no generation")
    args = ap.parse_args(argv)
    tasks, corpus = TASK_SETS[args.task_set]
    models = args.models.split(",") if args.models else [args.model]
    pairs = [(lang, task) for lang in LANGUAGES for task in tasks]
    if args.only:
        pairs = [p for p in pairs if f"{p[0]}/{p[1]}" in args.only]
    if args.install:
        log = json.loads((args.out / "generation.json").read_text())
        for app in install(args.out, corpus, log):
            print(f"installed {app}")
        return 0
    if args.manifest_only:
        print(json.dumps([{"language": l, "task": t, "model": model_for(l, t, tasks, models),
                           "prompt": prompt_for(l, t, tasks)} for l, t in pairs], indent=2))
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    budget = Budget(args.budget_usd, args.per_run_usd if args.budget_usd else None)

    def run(l: str, t: str) -> dict:
        if not budget.start():
            return {"language": l, "task": t, "error": "budget", "model_alias": model_for(l, t, tasks, models)}
        cost = None
        try:
            r = generate(args.out, l, t, model_for(l, t, tasks, models), args.timeout, tasks,
                         budget.per_run or None)
            cost = r.get("cost_usd")
            return r
        finally:
            budget.finish(cost)

    results = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(run, l, t): (l, t) for l, t in pairs}
        for fut in cf.as_completed(futs):
            l, t = futs[fut]
            try:
                r = fut.result()
            except subprocess.TimeoutExpired:
                r = {"language": l, "task": t, "error": "timeout"}
            results.append(r)
            print(f"{l}/{t}: model={r.get('model_alias')} exit={r.get('exit_code')} "
                  f"files={len(r.get('files', []))} cost={r.get('cost_usd')} "
                  f"spent={budget.spent:.2f} {r.get('error', '')}", flush=True)
    log = args.out / "generation.json"
    previous = json.loads(log.read_text()) if log.exists() else []
    keep = {(r["language"], r["task"]) for r in results}
    merged = [r for r in previous if (r["language"], r["task"]) not in keep] + results
    log.write_text(json.dumps(sorted(merged, key=lambda r: (r["language"], r["task"])), indent=2) + "\n")
    print(f"total spend this run: ${budget.spent:.2f}")
    return 0 if all(r.get("exit_code") == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
