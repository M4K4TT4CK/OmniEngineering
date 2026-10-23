#!/usr/bin/env python3
"""Generate the AI-written-code corpus with a separate model run.

    python tools/ai_corpus_generate.py --out /scratch/gen            # all 25 apps
    python tools/ai_corpus_generate.py --out /scratch/gen --only go/job-queue
    python tools/ai_corpus_generate.py --out /scratch/gen --manifest-only

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures" / "ai-corpus"

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

PROMPT = (
    "Build {task} Write it in {language}, using whatever libraries or framework you "
    "think are appropriate. Include automated tests and a short README. Keep it to "
    "a small number of files. Put all files in the current directory. Only write "
    "the files; do not run, build or install anything."
)

MODEL = "sonnet"
TOOLS = ["Read", "Write", "Edit", "Glob"]


def prompt_for(lang: str, task: str) -> str:
    return PROMPT.format(task=TASKS[task], language=LANGUAGES[lang])


def command(prompt: str, model: str) -> list[str]:
    return ["claude", "-p", prompt, "--model", model,
            "--tools", *TOOLS, "--allowedTools", *TOOLS,
            "--setting-sources", "", "--strict-mcp-config",
            "--output-format", "json"]


def generate(out: Path, lang: str, task: str, model: str, timeout: int) -> dict:
    target = out / lang / task
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    prompt = prompt_for(lang, task)
    started = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(command(prompt, model), cwd=target, capture_output=True,
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True, help="scratch directory for the raw output")
    ap.add_argument("--only", action="append", default=[], help="lang/task, repeatable")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--manifest-only", action="store_true", help="print the prompts and exit")
    args = ap.parse_args()
    pairs = [(lang, task) for lang in LANGUAGES for task in TASKS]
    if args.only:
        pairs = [p for p in pairs if f"{p[0]}/{p[1]}" in args.only]
    if args.manifest_only:
        print(json.dumps([{"language": l, "task": t, "prompt": prompt_for(l, t)} for l, t in pairs], indent=2))
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(generate, args.out, l, t, args.model, args.timeout): (l, t) for l, t in pairs}
        for fut in cf.as_completed(futs):
            l, t = futs[fut]
            try:
                r = fut.result()
            except subprocess.TimeoutExpired:
                r = {"language": l, "task": t, "error": "timeout"}
            results.append(r)
            print(f"{l}/{t}: exit={r.get('exit_code')} files={len(r.get('files', []))}", flush=True)
    log = args.out / "generation.json"
    previous = json.loads(log.read_text()) if log.exists() else []
    keep = {(r["language"], r["task"]) for r in results}
    merged = [r for r in previous if (r["language"], r["task"]) not in keep] + results
    log.write_text(json.dumps(sorted(merged, key=lambda r: (r["language"], r["task"])), indent=2) + "\n")
    return 0 if all(r.get("exit_code") == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
