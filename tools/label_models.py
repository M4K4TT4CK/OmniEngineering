#!/usr/bin/env python3
"""Independent model labels for Arbiter findings: two labellers, one adjudicator (ARB-067).

    # Label a stratified sample of a gold run (kept with --raw).
    python tools/label_models.py label --report gold-report.json --corpus ~/wt/corpus-gold \\
        --n 600 --budget-usd 25 --out labels/gold-model.jsonl --cache cache.jsonl

    # The AI-written corpus, every finding.
    python tools/label_models.py label --ai-corpus --out labels/ai-corpus-model.jsonl

    # Calibration: the same labellers, blind, on items whose truth is known.
    python tools/label_models.py label --ground-truth tools/gold/labels/owasp-java.jsonl \\
        --n 120 --out labels/calibration.jsonl

    # Tables: per rule and category, Wilson bounds, kappa, labeller vs truth.
    python tools/label_models.py summarize --labels tools/gold/labels/*.jsonl

Why it is built this way. The precision claim is meant to stand without a
human reading the findings, and the Phase 2 verifier is also a model. Labels
are only evidence if they are independent of Arbiter and of the verifier:

* A labeller sees the code excerpt (`arbiter.context`, shared with the
  verifier: the enclosing function with its decorators, imports, callees,
  same-file callers when the flagged value is a parameter, and the
  repository definitions of calls on the taint path, bounded, secrets
  masked by `arbiter.judgement._mask_secrets`), the file path and a
  neutral claim from `CLAIMS` -- never Arbiter's title, description,
  remediation, confidence or the verifier's verdict.
* Two labellers with deliberately different prompts: the prosecutor looks
  for how the claim holds, the defender for a concrete reason it does not.
  Neither sees the other. When they differ, an adjudicator sees both reasons
  and decides. Cohen's kappa between the two is reported per rule.
* For ground-truth items (OWASP Benchmark, Juliet, CVE fixes) the labellers
  are calibrated blind: comments and test-case names are scrubbed
  (`gt_common.blind`) and the truth is attached only after they answer.
* Calls go through the logged-in Claude CLI headless (`claude -p`), with no
  tools, no settings, skills or MCP servers and a replaced system prompt; the
  cache (key: finding id, excerpt hash, prompt version, role, model) is this
  tool's own file, separate from the verifier's.
* `--budget-usd` caps what the run may spend (the CLI's `total_cost_usd`);
  the run stops before a call that could pass it. Cached answers cost nothing.

Nothing here runs the code it reads, and code leaves the machine only as the
bounded excerpt in the prompt.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import fnmatch
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools" / "ground_truth"))
import gt_common as g  # noqa: E402

_spec = importlib.util.spec_from_file_location("label_sample", ROOT / "tools" / "label_sample.py")
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)

# v2 (2026-10-23): the excerpt comes from `arbiter.context` -- decorators,
# class header, route registration and the repository definitions of calls
# on the taint path -- and the system prompt says so.
# v3 (2026-10-23): when the value at the marked line arrives as a parameter,
# the excerpt also shows up to two same-file callers of the function.
PROMPT_VERSION = "v3"
MODEL = "opus"
HELDOUT = ROOT / "tools" / "gold" / "heldout.json"
EXCLUDE = ls.DEFAULT_EXCLUDE
GT_ROOTS = {"owasp-java": "~/wt/corpus-gold/owasp-benchmark-java",
            "owasp-python": "~/wt/corpus-gold/owasp-benchmark-python",
            "juliet-java": "~/wt/corpus-gt/juliet-java",
            "cve-fix": "~/wt/corpus-gt/cve"}
BLIND_KIND = {"owasp-java": "owasp", "owasp-python": "owasp", "juliet-java": "juliet"}

# ---------------------------------------------------------------------------
# Neutral claims. Written for this tool, not copied from the rule packs: a
# labeller is told what is being claimed about the marked line, in plain
# terms, and nothing about why Arbiter thinks so. First match wins.
# ---------------------------------------------------------------------------

_OUTSIDE = "data that may come from outside the program (a request, user input, a file, the network or the environment)"
CLAIMS: list[tuple[str, str, str]] = [
    # (rule glob, category, claim)
    ("*.sql-injection", "injection", f"{_OUTSIDE} is put into an SQL statement by string building rather than as a bound parameter, so it could change the statement's meaning."),
    ("*.sql-string-built", "injection", f"{_OUTSIDE} is put into an SQL statement by string building rather than as a bound parameter, so it could change the statement's meaning."),
    ("*.argument-injection", "injection", f"{_OUTSIDE} is passed as an argument to an external program where a value starting with '-' could be read as an option."),
    ("*.command-injection", "injection", f"{_OUTSIDE} reaches an operating-system command or shell in a way that lets it change which command runs or what its arguments are."),
    ("*.shell-*", "injection", f"{_OUTSIDE} reaches an operating-system command or shell in a way that lets it change which command runs or what its arguments are."),
    ("*.path-traversal*", "injection", f"a file path built from {_OUTSIDE} could reach files outside the intended directory (for example with '../' or an absolute path)."),
    ("*.upload-client-filename", "injection", "an uploaded file is stored under the name the client supplied, so the client controls where it is written or what type it gets."),
    ("*.xss-*", "injection", f"{_OUTSIDE} is written into an HTML response or page without escaping, so it could inject script."),
    ("*.html-from-request", "injection", f"{_OUTSIDE} is written into an HTML response or page without escaping, so it could inject script."),
    ("*inner-html*", "injection", f"{_OUTSIDE} is assigned as HTML into the page without sanitising, so it could inject script."),
    ("*.ssrf-*", "injection", f"the program makes a network request to a URL or host that {_OUTSIDE} controls, with nothing restricting where it may go."),
    ("*.request-keyed-assignment", "injection", f"an object property is assigned with a key taken from {_OUTSIDE}, which could set '__proto__' or 'constructor' (prototype pollution)."),
    ("*.code-injection", "injection", f"a string that {_OUTSIDE} can influence is evaluated or loaded as code."),
    ("*.dynamic-eval", "injection", f"a string that {_OUTSIDE} can influence is evaluated as code."),
    ("*.eval-call", "injection", f"a string that {_OUTSIDE} can influence is evaluated as code."),
    ("*.new-function-code", "injection", f"a string that {_OUTSIDE} can influence is compiled into a function and run."),
    ("*.timer-string-code", "injection", f"a string that {_OUTSIDE} can influence is passed to a timer to be evaluated as code."),
    ("*.vm-run-code", "injection", f"a string that {_OUTSIDE} can influence is run as code in a VM context."),
    ("*deserial*", "deserialization", f"{_OUTSIDE} is deserialized with a mechanism that can create arbitrary types or run code."),
    ("*.marshal-load", "deserialization", f"{_OUTSIDE} is deserialized with Marshal, which can create arbitrary objects."),
    ("*.yaml-unsafe-load", "deserialization", f"{_OUTSIDE} is parsed with a YAML loader that can create arbitrary objects."),
    ("*.object-input-stream", "deserialization", f"{_OUTSIDE} is deserialized with Java object serialization without a type filter."),
    ("*.xml-decoder", "deserialization", f"{_OUTSIDE} is decoded with java.beans.XMLDecoder, which can run arbitrary methods."),
    ("*.jackson-default-typing", "deserialization", "Jackson polymorphic default typing is enabled, so JSON from outside could choose which classes are instantiated."),
    ("arbiter/secrets.private-key", "secrets", "a real private key protecting a real system is committed in this file -- not a test vector, example, fixture or documentation sample."),
    ("arbiter/secrets.jwt", "secrets", "a real, usable JSON Web Token (a live credential) is committed in this file -- not an example, test or documentation token."),
    ("arbiter/secrets.*", "secrets", "a real credential (a password, token, key or connection string with a password that would grant access to a real system) is written literally in this file -- not a placeholder, example, test fixture or local-development default."),
    ("*hardcoded-signing-key", "secrets", "a key used to sign or verify tokens is a literal in source code that runs in production, rather than loaded from configuration or a secret store."),
    ("*jwt-literal-signing-key", "secrets", "a key used to sign or verify tokens is a literal in source code that runs in production, rather than loaded from configuration or a secret store."),
    ("*jwt-hardcoded-secret", "secrets", "a key used to sign or verify tokens is a literal in source code that runs in production, rather than loaded from configuration or a secret store."),
    ("*hardcoded-*password", "secrets", "a database password is written literally in source code that runs in production."),
    ("*.jwt-claims-validation-disabled", "auth-crypto", "token verification here skips checking the expiry or other claims."),
    ("*.jwt-*", "auth-crypto", "a JSON Web Token is decoded and trusted without verifying its signature, or verification accepts unsigned or weakly signed tokens."),
    ("*.trust-all-certificates", "auth-crypto", "TLS certificate verification is turned off for connections made by code that runs in production (not only in a test or an explicit opt-in development setting)."),
    ("*.hostname-verifier-disabled", "auth-crypto", "TLS hostname verification is turned off for connections made by code that runs in production (not only in a test or an explicit opt-in development setting)."),
    ("*.tls-verify-off", "auth-crypto", "TLS certificate verification is turned off for connections made by code that runs in production (not only in a test or an explicit opt-in development setting)."),
    ("arbiter/authored.security-check-disabled", "auth-crypto", "a security check (TLS or certificate verification, signature or authentication checking) is switched off in code that runs in production, not only in a test or an explicit opt-in setting."),
    ("*csrf*", "auth-crypto", "protection against cross-site request forgery is disabled or bypassed for state-changing requests in a browser-facing application that authenticates with cookies or a session."),
    ("*.session-cookie-cross-site", "auth-crypto", "a session cookie is set so that browsers send it on cross-site requests (SameSite=None or unset) with no other protection against cross-site request forgery."),
    ("*random*", "auth-crypto", "a non-cryptographic random number generator produces a value that is used as a secret, token, password, nonce or key."),
    ("*.weak-password-hash", "auth-crypto", "passwords are hashed with an algorithm or work factor too weak for password storage."),
    ("*error*-to-client", "info-exposure", "internal error details (exception messages or stack traces) are sent to the client of a network service in a response."),
    ("*stack-trace*", "info-exposure", "internal error details (exception messages or stack traces) are sent to the client of a network service in a response."),
    ("*exception-message-to-client", "info-exposure", "internal error details (exception messages or stack traces) are sent to the client of a network service in a response."),
    ("*error-text-in-response", "info-exposure", "internal error details (error messages) are sent to the client of a network service in a response."),
    ("*error-object-in-response", "info-exposure", "internal error details (an error object or its stack) are sent to the client of a network service in a response."),
    ("*error-stack-in-response", "info-exposure", "internal error details (an error stack) are sent to the client of a network service in a response."),
    ("*.debug-mode-enabled", "info-exposure", "a debug mode that exposes internals or allows code execution is enabled for a server that may run in production."),
    ("*.pprof-on-default-mux", "info-exposure", "Go's profiling endpoints are registered on the default HTTP mux, so any server using that mux exposes them."),
    ("*timeout*", "resilience", "a network or database call is made with no explicit timeout or deadline set by this code, so how long it waits when the other side stalls is left to the library's default (which may be long or unlimited)."),
    ("*.http-default-client", "resilience", "an HTTP request uses Go's default client, which has no timeout, so it can wait without limit if the server stalls."),
    ("*.db-call-no-deadline", "resilience", "a database call is made with no explicit deadline or timeout set by this code, so how long it waits when the database stalls is left to the driver's default (which may be long or unlimited)."),
    ("*retry*", "resilience", "a failed operation is retried in a loop with no growing delay between attempts, or with no limit on the number of attempts."),
    ("*worker-loop*", "resilience", "a long-running loop has no way to be stopped (no stop signal, cancellation or shutdown check), so the process cannot shut down cleanly."),
    ("*.endless-worker-loop", "resilience", "a long-running loop has no way to be stopped (no stop signal, cancellation or shutdown check), so the process cannot shut down cleanly."),
    ("*.thread-not-daemon-never-joined", "resilience", "a non-daemon thread is started and never joined or stopped, so it can keep the process from exiting."),
    ("*.rescue-exception", "error-handling", "this code rescues Exception (every error class, including interrupts and exit requests) rather than StandardError or specific errors."),
    ("*swallowed*", "error-handling", "an error is caught and discarded -- not handled, logged, returned or re-raised -- so a failure goes unnoticed."),
    ("*empty-catch*", "error-handling", "an error is caught and discarded -- not handled, logged, returned or re-raised -- so a failure goes unnoticed."),
    ("*.promise-catch-noop", "error-handling", "a promise rejection is caught and discarded, so a failure goes unnoticed."),
    ("arbiter/concurrency.*", "concurrency", "shared mutable state is read and updated from concurrent threads, goroutines or requests without synchronisation, so updates can be lost or a check can be stale by the time it is acted on."),
    ("*unsynchroni*", "concurrency", "shared mutable state is read and updated from concurrent threads, goroutines or requests without synchronisation, so updates can be lost."),
    ("*check-then-act*", "concurrency", "a check on shared state and the action that depends on it are not atomic, so another thread or request can change the state in between."),
    ("*.map-write-in-goroutine", "concurrency", "a map is written from a goroutine while other goroutines may access it, without a lock."),
    ("*parsed-*", "numeric", f"a number parsed from {_OUTSIDE} is narrowed to a smaller type or used in arithmetic that can overflow, without a range check."),
    ("*test-without-assertion", "tests", "this test checks nothing: it has no assertion or expectation, directly or through a helper or a framework mechanism (expected exceptions, mock verification, snapshot), so it would pass even if the code under test were broken."),
    ("*test-asserts-nothing", "tests", "this test checks nothing: it has no assertion or expectation, directly or through a helper or a framework mechanism (expected exceptions, mock verification, snapshot), so it would pass even if the code under test were broken."),
    ("*.vacuous-spec", "tests", "this spec checks nothing: it has no expectation, so it would pass even if the code under test were broken."),
    ("*skipped*", "tests", "this test is skipped unconditionally -- not only on some platforms, environments or when an optional dependency is missing -- so it never runs."),
    ("*stub*", "stubs", "this function on a production code path is an unimplemented placeholder (it raises 'not implemented', returns a fixed value, or does nothing) where callers expect real behaviour -- not an abstract method, interface default, test double or deliberate no-op."),
    ("arbiter/assurance.blanket-suppression", "suppression", "a lint, type-check or analyser suppression on this line disables every check (it names no specific rule)."),
    ("arbiter/assurance.analysis-excluded", "suppression", "production code is excluded from static analysis or coverage here."),
    ("arbiter/supply.unpinned-action", "supply-chain", "a GitHub Actions workflow uses an action (any publisher, GitHub's own included) by tag or branch rather than by a full commit SHA, so the code it runs can change without a change to this file."),
    ("arbiter/supply.pull-request-target", "supply-chain", "a workflow triggered by pull_request_target checks out or runs code from the pull request while it has the base repository's secrets or a write token."),
    ("arbiter/supply.unpinned-*", "supply-chain", "a dependency is declared with a version range or no version, and nothing else pins it (no lockfile is used for this manifest), so a build can pick up an unreviewed new release. A library declaring a range for its own consumers does not count."),
    ("arbiter/authored.undeclared-import", "supply-chain", "a module is imported that the project does not declare as a dependency."),
    ("arbiter/authored.import-of-nonexistent-package", "supply-chain", "a module is imported that does not exist as a published package."),
    ("arbiter/drift.broken-doc-link", "docs-drift", "a link on this documentation line points to a file or anchor that does not exist in the repository."),
    ("arbiter/drift.doc-references-missing-file", "docs-drift", "this documentation line refers to a file path that does not exist in the repository."),
    ("arbiter/drift.documented-env-var-absent", "docs-drift", "an environment variable named on this documentation line is not read anywhere in the code."),
    ("arbiter/authored.docstring-promises-absent-behaviour", "docs-drift", "the function's documentation promises behaviour that its body does not implement."),
    ("arbiter/contract.*", "docs-drift", "something the documentation or schema declares (a route, a column) does not match the code."),
    ("arbiter/resource.k8s-no-resource-limits", "iac", "a container in this Kubernetes workload has no CPU or memory limits."),
    ("arbiter/resource.k8s-no-security-context", "iac", "a container in this Kubernetes workload has no security context."),
    ("arbiter/resource.k8s-privilege-escalation-not-disabled", "iac", "a container in this Kubernetes workload does not set allowPrivilegeEscalation: false."),
    ("arbiter/resource.k8s-not-run-as-non-root", "iac", "a container in this Kubernetes workload does not set runAsNonRoot: true."),
    ("arbiter/resource.k8s-writable-root-filesystem", "iac", "a container in this Kubernetes workload does not set readOnlyRootFilesystem: true."),
    ("arbiter/resource.k8s-capabilities-not-dropped", "iac", "a container in this Kubernetes workload does not drop Linux capabilities (drop: [ALL])."),
    ("arbiter/resource.k8s-mutable-image-tag", "iac", "a container image is referenced by a mutable tag (such as latest) rather than a digest."),
    # Rules for defect classes found in AI-written code (ARB-083), 2026-10-23.
    ("*.ignored-encode-error", "resilience", "an error returned while writing a response body is discarded, so a failed or partial write goes unnoticed."),
    ("*.ignored-exec-error", "resilience", "the error from a database statement is discarded, so a failed write or delete is treated as if it succeeded."),
    ("*.mail-*", "auth-crypto", "a mail connection is made over TLS without verifying the server's certificate, or allows falling back to an unencrypted connection, so credentials or messages can be intercepted."),
    ("*.image-*", "resilience", f"an image from {_OUTSIDE} is decoded with no limit on its pixel dimensions, so a small file can make the process allocate a very large amount of memory."),
    ("*.decompress-*", "resilience", f"compressed data from {_OUTSIDE} is decompressed with no limit on the output size, so a small input can expand to exhaust memory or disk."),
    ("*.request-body-*", "resilience", "a request body is read into memory with no maximum size, so one large request can exhaust the process's memory."),
    ("*.upload-memory-storage-unbounded", "resilience", "uploaded files are held in memory with no size limit, so one large upload can exhaust the process's memory."),
    ("*.requeue-without-delay", "resilience", "a failed job is put back on the queue immediately with no delay or attempt limit, so a persistent failure loops as fast as the worker can run."),
]
_CWE_CLAIMS = {"CWE-400": "resilience", "CWE-1164": "stubs"}


def claim_for(rule: str) -> tuple[str, str] | None:
    """(category, neutral claim) for a rule, or None when no claim is written."""
    for pat, cat, text in CLAIMS:
        if fnmatch.fnmatch(rule, pat):
            return cat, text
    return None


def category(rule: str) -> str:
    c = claim_for(rule)
    return c[0] if c else "other"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM = (
    "You label findings for a precision study of a static checker. You are shown one code "
    "excerpt, its file path, and one claim about the line marked >>. Decide whether the claim "
    "is true of that line, as written, in this code.\n"
    "The excerpt shows the enclosing function with its decorators or annotations, the file's "
    "imports, functions it calls and, when the value at the marked line is one of the function's "
    "parameters, the functions in the same file that call it; a section headed '--- <path>: definition of ...' is the "
    "definition, in another file of the same repository, of a function called on the way to the "
    "marked line. Line numbers are each file's own.\n"
    "- tp: the claim holds for the marked line.\n"
    "- fp: the claim does not hold for the marked line, or a condition stated in the claim rules "
    "it out (for example the value is a placeholder, the data cannot come from outside, or it is "
    "test or example code where the claim says it does not count).\n"
    "- unsure: the answer depends on code that is not shown and cannot reasonably be inferred.\n"
    "Judge from the code shown and ordinary knowledge of the language and libraries it uses. "
    "Values written as [redacted ...] were masked before you saw them. "
    'Reply with only a JSON object: {"label": "tp" | "fp" | "unsure", "reason": "<one or two '
    'sentences that cite the code>"}.'
)

ROLES = {
    "prosecutor": (
        "Your role: prosecutor. Try to establish that the claim holds: find the concrete path by "
        "which the marked line does what the claim says (where the data comes from, what reaches "
        "the call, what is missing). If you cannot build that case from the code shown, answer fp "
        "or unsure instead."),
    "defender": (
        "Your role: defender. Look for a concrete reason the claim does not hold here: the value "
        "is constant or validated, escaping or parameter binding is used, the setting is applied "
        "elsewhere in the excerpt, it is a test, example or fixture where the claim says it does "
        "not count, the code is unreachable. If there is no such reason and the claim holds, "
        "answer tp."),
}

ADJUDICATOR = (
    "Your role: adjudicator. Two reviewers labelled this claim independently and disagree. Their "
    "answers are below. Weigh each reason against the code itself -- not against the other "
    "reviewer -- and give your own label.")


def item_prompt(it: dict) -> str:
    facts = f"\nFacts checked against the repository:\n{it['facts']}\n" if it.get("facts") else ""
    return (f"File: {it['shown_path']}\nClaim about the line marked >>: {it['claim']}\n{facts}\n"
            f"Excerpt:\n```\n{it['excerpt']}\n```\n")


def role_prompt(it: dict, role: str, votes: dict | None = None) -> str:
    if role == "adjudicator":
        a, b = votes["prosecutor"], votes["defender"]
        return (f"{ADJUDICATOR}\n\n{item_prompt(it)}\nReviewer A: {a['label']} -- {a['reason']}\n"
                f"Reviewer B: {b['label']} -- {b['reason']}\n")
    return f"{ROLES[role]}\n\n{item_prompt(it)}"


def parse_answer(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    lab = str(d.get("label", "")).strip().lower()
    if lab not in g.LABELS:
        return None
    # A reason may quote the excerpt; it is masked like the excerpt was, so a
    # label file never carries a credential-shaped string from the corpus.
    return {"label": lab, "reason": mask_reason(" ".join(str(d.get("reason", "")).split()))[:600]}


_DB_URL_CRED = re.compile(r"\b((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://)[^\s:@/]+:[^\s@/]+@")


def mask_reason(text: str) -> str:
    """`_mask_secrets`, plus inline database-URL credentials (the shape
    `secrets.pg-url` flags), which a labeller may quote from the code."""
    return _DB_URL_CRED.sub(r"\1[redacted]@", g.mask(text))


# ---------------------------------------------------------------------------
# Model calls, cache and budget
# ---------------------------------------------------------------------------

class Budget:
    def __init__(self, usd: float):
        self.cap, self.spent, self.calls, self.max_seen = usd, 0.0, 0, 0.0
        self.lock = threading.Lock()
        self.stopped = False
        self.limit_hit = ""

    def reserve(self) -> float | None:
        """The amount held for one call, or None when it could pass the cap."""
        with self.lock:
            guess = max(self.max_seen * 1.5, 0.05)
            if self.stopped or self.spent + guess > self.cap:
                self.stopped = True
                return None
            self.spent += guess
            return guess

    def settle(self, held: float, cost: float) -> None:
        with self.lock:
            self.spent += cost - held
            self.calls += 1
            self.max_seen = max(self.max_seen, cost)


class Cache:
    """Append-only JSONL: one line per answered call."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        for r in g.read_jsonl(path):
            self.data[r["key"]] = r

    @staticmethod
    def key(it: dict, role: str, model: str, extra: str = "") -> str:
        return g.sha256("|".join([it["finding_id"], it["excerpt_sha256"], PROMPT_VERSION, role, model, extra]))

    def get(self, k: str) -> dict | None:
        return self.data.get(k)

    def put(self, rec: dict) -> None:
        with self.lock:
            self.data[rec["key"]] = rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")


def call_cli(system: str, prompt: str, model: str, timeout: int = 240) -> tuple[str, float, str]:
    """(reply text, cost in USD, resolved model) from one headless CLI run."""
    cmd = ["claude", "-p", prompt, "--model", model, "--tools", "", "--setting-sources", "",
           "--strict-mcp-config", "--system-prompt", system, "--output-format", "json",
           "--no-session-persistence"]
    with tempfile.TemporaryDirectory(prefix="label-") as cwd:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env)
    try:
        meta = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"cli exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
    if meta.get("is_error"):
        raise RuntimeError(f"cli error: {str(meta.get('result'))[:300]}")
    resolved = next(iter(meta.get("modelUsage") or {}), model)
    return meta.get("result", ""), float(meta.get("total_cost_usd") or 0.0), resolved


# A usage limit (session, weekly, credit) stops the run: every later call
# would fail the same way. A server-side throttle says it is not the usage
# limit and asks for a retry; round 2 stopped on one after 196 of 450 items
# because the old pattern matched "Rate limited".
_USAGE_LIMIT = re.compile(r"(?i)(session|usage|rate|weekly) limit|credit balance")
_TRANSIENT = re.compile(r"(?i)not your usage limit|temporarily limiting|retry after|overloaded")
RETRY_DELAYS = (2, 5, 15, 40)


class Labeller:
    def __init__(self, cache: Cache, budget: Budget, model: str = MODEL, caller=call_cli,
                 sleep=time.sleep):
        self.cache, self.budget, self.model, self.caller, self.sleep = cache, budget, model, caller, sleep

    def _call(self, prompt: str) -> tuple[str, float, str]:
        """One call, retried with growing delays while the server throttles."""
        for delay in RETRY_DELAYS:
            try:
                return self.caller(SYSTEM, prompt, self.model)
            except RuntimeError as exc:
                if not _TRANSIENT.search(str(exc)):
                    raise
                self.sleep(delay)
        return self.caller(SYSTEM, prompt, self.model)

    def ask(self, it: dict, role: str, votes: dict | None = None) -> dict | None:
        extra = ""
        if votes:
            extra = g.sha256(json.dumps(votes, sort_keys=True))
        k = Cache.key(it, role, self.model, extra)
        hit = self.cache.get(k)
        if hit:
            return {**hit, "cached": True}
        held = self.budget.reserve()
        if held is None:
            return None
        cost = 0.0
        try:
            for attempt in range(2):
                text, c, resolved = self._call(role_prompt(it, role, votes))
                cost += c
                ans = parse_answer(text)
                if ans:
                    break
            else:
                ans = {"label": "unsure", "reason": "unparseable reply"}
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            self.budget.settle(held, cost)
            if _USAGE_LIMIT.search(str(exc)) and not _TRANSIENT.search(str(exc)):
                self.budget.stopped = True  # every later call would fail the same way
                self.budget.limit_hit = str(exc)[:200]
            print(f"  call failed ({role} {it['finding_id']}): {exc}", file=sys.stderr)
            return None
        self.budget.settle(held, cost)
        rec = {"key": k, "finding_id": it["finding_id"], "role": role, "model": resolved,
               "prompt_version": PROMPT_VERSION, "excerpt_sha256": it["excerpt_sha256"],
               "cost_usd": round(cost, 6), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **ans}
        self.cache.put(rec)
        return rec

    def label(self, it: dict) -> dict | None:
        votes = {}
        for role in ("prosecutor", "defender"):
            v = self.ask(it, role)
            if v is None:
                return None
            votes[role] = v
        p, d = votes["prosecutor"], votes["defender"]
        adj = None
        if p["label"] == d["label"]:
            final, reason = p["label"], p["reason"]
        else:
            adj = self.ask(it, "adjudicator", {r: {"label": v["label"], "reason": v["reason"]}
                                               for r, v in votes.items()})
            if adj is None:
                return None
            final, reason = adj["label"], adj["reason"]
        cost = sum(v.get("cost_usd", 0) for v in [p, d, adj] if v and not v.get("cached"))
        model = p.get("model", self.model)
        rec = g.label(it["finding"], final, labeller=f"{model}:prosecutor+defender"
                      f"{'+adjudicator' if adj else ''}/{PROMPT_VERSION}",
                      source=it["source"], held_out=it["held_out"], reason=reason,
                      excerpt_sha256=it["excerpt_sha256"], category=it["category"],
                      votes={r: {"label": v["label"], "reason": v["reason"]}
                             for r, v in [("prosecutor", p), ("defender", d), ("adjudicator", adj)] if v},
                      cost_usd=round(cost, 6),
                      # The population the item was drawn from, for the ledger's weights.
                      **{k: it[k] for k in ls.POPULATION_KEYS if k in it})
        if "truth" in it:
            rec["truth"] = it["truth"]
        return rec


# ---------------------------------------------------------------------------
# Items: what each labeller is shown
# ---------------------------------------------------------------------------

def heldout_repos() -> set[str]:
    try:
        return set(json.loads(HELDOUT.read_text(encoding="utf-8"))["held_out"]["gold_repos"])
    except (OSError, KeyError, ValueError):
        return set()


_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)|`([\w./-]+\.\w{1,5})`|\b([\w-]+(?:/[\w.-]+)+\.\w{1,5})\b")
_ENV = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")


def repo_facts(root: Path, path: str, line_text: str, rule: str) -> str:
    """Mechanical facts for the claims an excerpt cannot settle (does a linked
    file exist, is an env var read). Computed here from the tree, never taken
    from Arbiter."""
    out = []
    if rule.startswith(("arbiter/drift.broken-doc-link", "arbiter/drift.doc-references-missing-file")):
        for m in _LINK.finditer(line_text):
            target = next(t for t in m.groups() if t)
            if re.match(r"^[a-z]+:", target):
                continue
            rel = (Path(path).parent / target) if not target.startswith("/") else Path(target.lstrip("/"))
            exists = (root / rel).exists()
            by_name = sum(1 for _ in _limited_glob(root, Path(target).name))
            out.append(f"- `{target}` relative to this file ({os.path.normpath(rel)}): "
                       f"{'exists' if exists else 'does not exist'}; files in the repository named "
                       f"`{Path(target).name}`: {by_name}")
    elif rule.startswith("arbiter/supply.unpinned-") and rule != "arbiter/supply.unpinned-action":
        here = (root / path).parent
        locks = [n for n in _LOCKS if (here / n).exists() or (root / n).exists()]
        out.append(f"- lockfiles beside this manifest or at the repository root: {', '.join(locks) or 'none'}")
    elif rule.startswith("arbiter/drift.documented-env-var-absent"):
        for name in sorted(set(_ENV.findall(line_text)))[:5]:
            n = _count_in_code(root, name)
            out.append(f"- `{name}` appears in {n} non-documentation source file(s) of the repository")
    return "\n".join(out[:6])


_PLACEHOLDER = re.compile(r"(?i)(example|sample|changeme|change[-_]?me|dummy|fake|test|placeholder|"
                          r"your[-_]|xxx|\*\*\*|insecure|secret|password|redacted|todo|demo|default|foo|bar|"
                          r"local|dev)")


def secret_shape(line_text: str) -> str:
    """What a masked value looks like, without the value: enough for a
    labeller to tell `"changeme"` from a 40-character random string."""
    vals = re.findall(r"""["']([^"']{4,})["']""", line_text)
    if not vals:
        m = re.search(r"[:=]\s*(\S{4,})", line_text)
        vals = [m.group(1)] if m else []
    out = []
    for v in vals[:3]:
        classes = [n for n, pat in (("lower", r"[a-z]"), ("upper", r"[A-Z]"), ("digits", r"[0-9]"),
                                    ("symbols", r"[^A-Za-z0-9]")) if re.search(pat, v)]
        counts = Counter(v)
        ent = -sum(c / len(v) * math.log2(c / len(v)) for c in counts.values())
        words = sorted({w.lower() for w in _PLACEHOLDER.findall(v)})
        env = bool(re.search(r"(os\.environ|getenv|ENV\[|process\.env|\$\{)", v))
        out.append(f"- a literal value on the marked line: {len(v)} characters, "
                   f"{'+'.join(classes)}, entropy {ent:.1f} bits/char"
                   f"{', contains the word(s) ' + ', '.join(words) if words else ', no placeholder-like words'}"
                   f"{', looks like an environment lookup' if env else ''}")
    return "\n".join(out)


def _limited_glob(root: Path, name: str, cap: int = 50):
    n = 0
    for p in root.rglob(name):
        if ".git" in p.parts:
            continue
        yield p
        n += 1
        if n >= cap:
            return


_CODE_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".go", ".java", ".rb", ".sh", ".yaml",
             ".yml", ".toml", ".json", ".kt", ".rs", ".env", ".cfg", ".ini", ".properties"}


def _count_in_code(root: Path, name: str) -> int:
    n = 0
    for p in root.rglob("*"):
        if p.suffix not in _CODE_EXT or ".git" in p.parts or not p.is_file():
            continue
        try:
            if name in p.read_text(encoding="utf-8", errors="ignore"):
                n += 1
        except OSError:
            continue
    return n


def locate_line(f: dict, text: str) -> int:
    """A line for a file-level finding (a manifest entry, a Kubernetes
    workload). The backticked name in the title is used only to find the
    line; the title itself is never shown to a labeller."""
    lines = text.splitlines()
    m = re.search(r"`([^`]+)`", f.get("title") or "")
    if m:
        name = m.group(1)
        for i, ln in enumerate(lines, 1):
            if f'"{name}"' in ln or f"'{name}'" in ln:
                return i
        for i, ln in enumerate(lines, 1):
            if re.search(rf"(^|[\s\[,]){re.escape(name)}\b", ln):
                return i
    if f["rule"].startswith("arbiter/resource.k8s-"):
        for i, ln in enumerate(lines, 1):
            if re.match(r"^\s*-?\s*image:", ln):
                return i
    return 1 if lines else 0


_LOCKS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json", "bun.lockb",
          "poetry.lock", "uv.lock", "Pipfile.lock", "pdm.lock", "Gemfile.lock", "go.sum")


def make_item(f: dict, root: Path, source: str, held_out: bool, blind_kind: str | None = None,
              truth: str | None = None) -> dict | None:
    c = claim_for(f["rule"])
    if c is None:
        return None
    p = root / f["path"]
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not f.get("line"):
        f = {**f, "line": locate_line(f, text)}
        if not f["line"]:
            return None
    shown = f["path"]
    if blind_kind:
        text = g.blind(text, str(p), blind_kind)
        shown = g.blind_path(f["path"], blind_kind)
    ex = g.excerpt(text, f["path"], int(f["line"]), f.get("language") or g.language_of(f["path"]),
                   repo=g.repo_view(root, blind_kind))
    lines = text.splitlines()
    line_text = lines[int(f["line"]) - 1] if int(f["line"]) <= len(lines) else ""
    facts = repo_facts(root, f["path"], line_text, f["rule"]) if not blind_kind else ""
    if c[0] == "secrets" and "[redacted" in ex:
        facts = "\n".join(x for x in (facts, secret_shape(line_text)) if x)
    it = {"finding": f, "finding_id": f.get("id") or g.sha256(f"{f.get('repo')}|{f['path']}|{f['line']}|{f['rule']}")[:14],
          "rule": f["rule"], "category": c[0], "claim": c[1], "shown_path": shown, "excerpt": ex,
          "facts": facts, "source": source, "held_out": held_out}
    it["excerpt_sha256"] = g.sha256(item_prompt(it))
    if truth is not None:
        it["truth"] = truth
    return it


def per_rule_sample(findings: list[dict], want: dict[str, int], seed: int) -> list[dict]:
    """`want[rule]` findings of each rule (all of them when it has fewer),
    spread over the rule's languages by `ls.allocate`; a rule not named gets
    `want.get("*", 0)`. Each row keeps `stratum_size`, the size of its
    (rule, language) stratum, for population weighting. Deterministic, and
    each stratum is a prefix of its own seeded shuffle, so raising a rule's
    size later only adds findings (a resumed run keeps what it labelled)."""
    import random
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for f in findings:
        strata[(f["rule"], f["language"])].append(f)
    by_rule: dict[str, dict] = defaultdict(dict)
    for (rule, lang), v in strata.items():
        by_rule[rule][(rule, lang)] = len(v)
    out = []
    for rule in sorted(by_rule):
        sizes = by_rule[rule]
        n = min(sum(sizes.values()), int(want.get(rule, want.get("*", 0))))
        if n <= 0:
            continue
        alloc = ls.allocate(sizes, n, floor=0) if n < sum(sizes.values()) else dict(sizes)
        for k in sorted(sizes):
            pool = sorted(strata[k], key=lambda f: (f["repo"], f["path"], f["line"], f["rule"]))
            random.Random(f"{seed}|{k[0]}|{k[1]}").shuffle(pool)
            out += [{**f, "stratum_size": sizes[k]} for f in pool[: alloc.get(k, 0)]]
    random.Random(seed).shuffle(out)
    return out


def items_from_report(report: Path, corpus: Path, n: int, seed: int,
                      per_rule: dict[str, int] | None = None) -> tuple[list[dict], dict]:
    data = json.loads(report.read_text(encoding="utf-8"))
    findings = [f for f in ls.findings_from_report(data)
                if not f["rule"].startswith(EXCLUDE) and claim_for(f["rule"])]
    held = heldout_repos()
    if per_rule is not None:
        rows = per_rule_sample(findings, per_rule, seed)
    else:
        rows = ls.stratified_sample(findings, n, seed,
                                    key=lambda f: (f["rule"], f["language"], category(f["rule"])))
    pops = ls.stratum_populations(findings, lambda f: f["repo"] in held)
    items = []
    for f in rows:
        it = make_item(f, corpus / f["repo"], "gold-model", f["repo"] in held)
        if it:
            it.update(pops[(f["rule"], f["language"])])
            assert it["stratum_size"] == f["stratum_size"]
            items.append(it)
    return items, {"population": len(findings), "strata": len(pops)}


def census(items: list[dict]) -> list[dict]:
    """Every finding was labelled (no sampling): each stratum's population
    is the items in it."""
    rows = [{"rule": it["rule"], "language": it["finding"].get("language") or "", "held_out": it["held_out"]}
            for it in items]
    pops = ls.stratum_populations(rows, lambda r: r["held_out"], "census")
    for it, r in zip(items, rows):
        it.update(pops[(r["rule"], r["language"])])
    return items


def items_from_ai_corpus() -> list[dict]:
    items = []
    for app in sorted((ROOT / "fixtures" / "ai-corpus").glob("*/*/app")):
        name = "ai-corpus/" + app.parent.relative_to(ROOT / "fixtures" / "ai-corpus").as_posix()
        for f in g.scan_tree(app):
            if f["rule"].startswith(EXCLUDE):
                continue
            it = make_item({**f, "repo": name}, app, "ai-corpus-model", False)
            if it:
                items.append(it)
    return census(items)


def items_from_ground_truth(paths: list[Path], n: int, seed: int,
                            exclude: set[str] | None = None) -> list[dict]:
    """A stratified sample of known-truth labels, shown blind. Findings in
    `exclude` (ids already used by an earlier calibration round) are skipped,
    so a later round is drawn only from items no labeller has seen."""
    import random
    exclude = exclude or set()
    recs = [r for p in paths for r in g.read_jsonl(p) if r.get("labeller") == "ground-truth"
            and not _cross_file(r) and r["finding_id"] not in exclude]
    strata = defaultdict(list)
    for r in recs:
        strata[(r["source"], r["rule_id"], r["label"])].append(r)
    alloc = ls.allocate({k: len(v) for k, v in strata.items()}, n) if n else {k: len(v) for k, v in strata.items()}
    rng = random.Random(seed)
    items = []
    for k in sorted(strata):
        pool = sorted(strata[k], key=lambda r: (r["path"], r["line"]))
        for r in rng.sample(pool, alloc[k]):
            src = r["source"]
            root = Path(GT_ROOTS[src]).expanduser()
            if src == "cve-fix":
                root = root / r["ghsa"] / "vuln"
            f = {"id": r["finding_id"], "rule": r["rule_id"], "path": r["path"], "line": r["line"],
                 "cwe": r.get("cwe", []), "language": r.get("language", ""), "repo": r.get("repo", src),
                 "severity": r.get("severity", "")}
            it = make_item(f, root, f"calibration:{src}", True, BLIND_KIND.get(src), truth=r["label"])
            if it:
                it["stratum_size"] = len(strata[k])
                items.append(it)
    rng.shuffle(items)
    return items


PLANTED = ROOT / "fixtures" / "planted"


def _anchor(lines: list[str], line: int) -> str | None:
    """The first quoted test title or assigned name on or just above `line`."""
    for i in range(line - 1, max(-1, line - 4), -1):
        m = re.search(r"""\b(?:it|test|describe|context|specify|scenario)(?:\.\w+)?\s*\(?\s*(["'])(.{4,}?)\1""",
                      lines[i])
        if m:
            return m.group(1) + m.group(2) + m.group(1)
    m = re.match(r"\s*(?:const |let |var |final |static |private |public )*([A-Za-z_]\w*)\s*[:=]", lines[line - 1])
    return f"{m.group(1)}" if m else None


def twin_line(bad: str, good: str, path: str, line: int, language: str) -> int | None:
    """The line of the good twin that corresponds to `line` of the bad file:
    the same test title or assigned name, else the closest line of the
    same-named function. The good twin is the fixed version, so the claim
    made about the bad line is false of this one. None when nothing matches."""
    import difflib
    bl, gl = bad.splitlines(), good.splitlines()
    if not 0 < line <= len(bl):
        return None
    target = bl[line - 1].strip()
    anchor = _anchor(bl, line)
    if anchor:
        if anchor[0] in "\"'":
            hits = [i for i, ln in enumerate(gl, 1) if anchor in ln]
        else:
            hits = [i for i, ln in enumerate(gl, 1) if re.match(rf"\s*(?:\w+\s+)*{re.escape(anchor)}\s*[:=]", ln)]
        if len(hits) == 1:
            return hits[0]
    span = g._function_span(bad, path, language, line)
    funcs_b = g._named_functions(bad, path, language)
    name = next((n for n, s in sorted(funcs_b.items(), key=lambda kv: kv[1][1] - kv[1][0])
                 if span and s == span), None)
    if name:
        s = g._named_functions(good, path, language).get(name)
        if s:
            cands = range(s[0], min(s[1], len(gl)) + 1)
            return max(cands, key=lambda i: difflib.SequenceMatcher(None, target, gl[i - 1].strip()).ratio())
    return None


def items_from_planted(categories: set[str], cap: int | None = None) -> list[dict]:
    """Calibration items with truth by construction, for categories no
    external benchmark covers: each finding Arbiter reports on a planted
    `bad` file (truth tp -- the defect was planted there and marked
    `expect:`), and the corresponding line of its `good` twin, the fixed
    version (truth fp). Shown blind: comments (the `expect:` markers),
    Python docstrings, `cwe`/`planted` names in paths and packages removed.
    These are files we wrote, small and clear-cut, so this measures the
    labellers on easy cases; it does not stand in for real-code truth."""
    import random
    items = []
    for lang_dir in sorted(p for p in PLANTED.iterdir() if p.is_dir()):
        bad_root, good_root = lang_dir / "bad", lang_dir / "good"
        per_cat = Counter()
        for f in sorted(g.scan_tree(bad_root), key=lambda f: (f["path"], f["line"], f["rule"])):
            cat = category(f["rule"])
            if cat not in categories or f["rule"].startswith(EXCLUDE):
                continue
            per_cat[cat] += 1
            if cap and per_cat[cat] > cap:
                continue
            repo = f"planted/{lang_dir.name}"
            it = make_item({**f, "repo": repo + "/bad"}, bad_root, f"calibration:planted-{cat}", True,
                           "planted", truth="tp")
            if it:
                items.append(it)
            gp = good_root / f["path"]
            if not gp.exists():
                continue
            bad = (bad_root / f["path"]).read_text(encoding="utf-8", errors="replace")
            good = gp.read_text(encoding="utf-8", errors="replace")
            tl = twin_line(bad, good, f["path"], int(f["line"]), f["language"])
            if tl is None:
                continue
            gf = {**f, "id": g.sha256(f"{repo}/good|{f['path']}|{tl}|{f['rule']}")[:14], "line": tl,
                  "repo": repo + "/good"}
            it = make_item(gf, good_root, f"calibration:planted-{cat}", True, "planted", truth="fp")
            if it:
                items.append(it)
    random.Random(20261023).shuffle(items)
    return items


# ---------------------------------------------------------------------------
# Mechanical facts as truth: where a claim turns on something a script can
# check (a SHA pin, a lockfile, a file that exists, a test path), compare the
# model label with it. The labellers saw the repo facts in their prompt, so
# this measures whether they follow facts, not an independent truth -- except
# the SHA-pin, test-path and suppression-names-a-rule checks, which they were
# never shown.
# ---------------------------------------------------------------------------

_SHA_PIN = re.compile(r"uses:\s*['\"]?[^@\s'\"]+@[0-9a-f]{40}\b")
_TEST_PATH = re.compile(r"(?i)(^|/)(tests?|spec|__tests__|testdata|test-?fixtures?|fixtures?|examples?|samples?|"
                        r"docs?|cookbook|itest|testsuite|integration-test|e2e|dummyserver|caddytest)(/|$)|"
                        r"(^|/)[^/]*(_test|\.test|\.spec|_spec|Test|Tests)\.\w+$|(^|/)test_[^/]*$")


# A suppression directive and, when it names rules, the names (group
# `named`). Written here from each tool's syntax, not taken from Arbiter's
# SUPPRESSION_PATTERNS, so the check is independent of the rule it checks.
# `@ts-ignore` / `@ts-expect-error` and `NOSONAR` cannot name a rule at all.
_SUPPRESSION_DIRECTIVES = [
    re.compile(r"#\s*noqa\b(?P<named>\s*:\s*[A-Z]+[0-9]+)?", re.I),
    re.compile(r"#\s*type:\s*ignore\b(?P<named>\[[^\]]+\])?"),
    re.compile(r"#\s*pyright:\s*ignore\b(?P<named>\[[^\]]+\])?"),
    re.compile(r"#\s*nosec\b(?P<named>[ \t]+B\d{3}|[ \t]+G\d{3})?"),
    re.compile(r"#\s*pylint:\s*disable\s*=\s*(?P<named>(?!all\b)[\w-]+)?"),
    re.compile(r"#\s*rubocop:\s*disable\b(?P<named>[ \t]+(?!all\b)[A-Z]\w*/\w+)?"),
    re.compile(r"//\s*@ts-(?:ignore|expect-error|nocheck)\b()?"),
    re.compile(r"(?://|/\*)\s*eslint-disable(?:-next-line|-line)?\b(?P<named>[ \t]+(?!\*/|--)[@\w/-]+)?"),
    re.compile(r"//\s*nolint\b(?P<named>:\w+)?"),
    re.compile(r"(?://|#)\s*NOSONAR\b()?"),
    re.compile(r"@SuppressWarnings\(\s*(?P<all>\"all\")?"),
]


def suppression_fact(line: str) -> tuple[str, str] | None:
    """Whether the suppression on `line` names a rule: ("tp", ...) when it
    names none, ("fp", ...) when it does, None when the line holds no
    directive outside a string literal."""
    for rx in _SUPPRESSION_DIRECTIVES:
        m = rx.search(line)
        if not m:
            continue
        before = line[:m.start()]
        if before.count('"') % 2 or before.count("'") % 2 or before.count("`") % 2:
            continue        # inside a string literal: not a directive
        groups = m.groupdict()
        if "all" in groups:
            return ("tp", "suppresses \"all\"") if groups["all"] else ("fp", "names the warnings it suppresses")
        if groups.get("named"):
            return "fp", f"names a rule ({groups['named'].strip(' :')})"
        return "tp", "the suppression names no rule"
    return None


def fact_truth(rec: dict, root: Path) -> tuple[str, str] | None:
    """(truth implied by a mechanical fact, the fact) or None when no fact decides it."""
    rule, path = rec["rule_id"], rec.get("path") or ""
    try:
        lines = (root / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    ln = lines[int(rec["line"]) - 1] if rec.get("line") and int(rec["line"]) <= len(lines) else ""
    if rule == "arbiter/supply.unpinned-action":
        if "uses:" not in ln or re.search(r"uses:\s*['\"]?(\./|docker://)", ln):
            return None
        return ("fp", "pinned to a full commit SHA") if _SHA_PIN.search(ln) else ("tp", "uses a tag or branch")
    if rule == "arbiter/assurance.blanket-suppression":
        return suppression_fact(ln)
    if rule.startswith("arbiter/supply.unpinned-"):
        here = (root / path).parent
        locks = [n for n in _LOCKS if (here / n).exists() or (root / n).exists()]
        return ("fp", f"lockfile present: {', '.join(locks)}") if locks else None
    if rule.startswith(("arbiter/drift.broken-doc-link", "arbiter/drift.doc-references-missing-file")):
        facts = repo_facts(root, path, ln, rule)
        if not facts:
            return None
        rows = facts.splitlines()
        # Only one direction is decisive. A path that exists nowhere is not
        # yet a broken reference: guides link to the .html they build into,
        # prose names generic files (`structure.sql`) or other projects'
        # files (`express.js`). Round 2 measured 21 of 28 such items as
        # model fp with reasons of exactly that kind.
        if all(": exists;" in r for r in rows):
            return "fp", "every referenced path exists"
        return None
    if rule.startswith("arbiter/drift.documented-env-var-absent"):
        facts = repo_facts(root, path, ln, rule)
        if facts and all(" appears in 0 " not in r for r in facts.splitlines()):
            return "fp", "every named variable appears in source"
        return None
    if category(rule) == "secrets" and _TEST_PATH.search(path):
        return "fp", "file is under a test, example, fixture or documentation path"
    return None


def fact_root(rec: dict, corpus: Path) -> Path:
    repo = rec.get("repo", "")
    if repo.startswith("ai-corpus/"):
        return ROOT / "fixtures" / repo / "app"
    return corpus / repo


def fact_check(recs: list[dict], corpus: Path) -> dict:
    """Per rule: how often the model label agrees with the fact-implied truth."""
    out: dict = defaultdict(lambda: {"n": 0, "agree": 0, "unsure": 0, "model_tp_fact_fp": 0,
                                     "model_fp_fact_tp": 0, "fact_tp": 0, "fact_fp": 0,
                                     "model_tp_calls": 0, "model_tp_right": 0, "examples": []})
    for r in recs:
        if r.get("labeller") == "ground-truth" or r["source"].startswith("calibration:"):
            continue
        ft = fact_truth(r, fact_root(r, corpus))
        if ft is None:
            continue
        e = out[(category(r["rule_id"]), r["rule_id"])]
        e["n"] += 1
        e[f"fact_{ft[0]}"] += 1
        if r["label"] == "unsure":
            e["unsure"] += 1
            continue
        if r["label"] == "tp":
            e["model_tp_calls"] += 1
            e["model_tp_right"] += ft[0] == "tp"
        if r["label"] == ft[0]:
            e["agree"] += 1
        else:
            e[f"model_{r['label']}_fact_{ft[0]}"] += 1
            if len(e["examples"]) < 5:
                e["examples"].append({"repo": r.get("repo"), "path": r.get("path"), "line": r.get("line"),
                                      "model": r["label"], "fact": ft[1], "reason": r.get("reason", "")[:200]})
    return {f"{c}|{k}": v for (c, k), v in sorted(out.items())}


_JULIET_SPLIT = re.compile(r"_\d+(?:[a-z]|_bad|_good\w*|_base)$")


def _cross_file(r: dict) -> bool:
    """A Juliet case split over several files: `..._54a.java` to `_54e`, and
    the `_81` family (`_81a` calls `_81_bad` / `_81_goodG2B` through
    `_81_base`). Its source and sink are in different files, so the excerpt
    cannot show whether the data is tainted. Kept as ground truth, left out
    of calibration, where it would measure the excerpt rather than the
    labeller. Round 2 let one `_81_goodG2B` through and it was a false tp."""
    return r["source"] == "juliet-java" and bool(_JULIET_SPLIT.search(Path(r["path"]).stem))


def items_from_suspects(path: Path) -> list[dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for s in d["suspects"]:
        it = make_item(s, Path(s["root"]).expanduser(), s["suspect"], True)
        if it:
            out.append(it)
    return census(out)


# ---------------------------------------------------------------------------
# Scoring -- pure
# ---------------------------------------------------------------------------

def kappa_pairs(recs: list[dict]) -> float | None:
    pairs = [(r["votes"]["prosecutor"]["label"], r["votes"]["defender"]["label"])
             for r in recs if r.get("votes", {}).get("defender")]
    return ls.cohens_kappa([a for a, _ in pairs], [b for _, b in pairs])


def tally(recs: list[dict], key) -> dict:
    out = defaultdict(lambda: {"tp": 0, "fp": 0, "unsure": 0, "recs": []})
    for r in recs:
        e = out[key(r)]
        e[r["label"]] += 1
        e["recs"].append(r)
    table = {}
    for k, e in sorted(out.items()):
        n = e["tp"] + e["fp"]
        table[k] = {"tp": e["tp"], "fp": e["fp"], "unsure": e["unsure"],
                    "precision": round(e["tp"] / n, 3) if n else None,
                    "wilson_lower": ls.wilson(e["tp"], n)[0] if n else None,
                    "kappa": kappa_pairs(e["recs"]),
                    "agreement": _agree(e["recs"])}
    return table


def _agree(recs: list[dict]) -> float | None:
    v = [r for r in recs if r.get("votes", {}).get("defender")]
    if not v:
        return None
    return round(sum(r["votes"]["prosecutor"]["label"] == r["votes"]["defender"]["label"] for r in v) / len(v), 3)


def calibration(recs: list[dict]) -> dict:
    """Model labels against the truth they never saw."""
    def score(rs, pick):
        c = Counter()
        for r in rs:
            m = pick(r)
            c[(m, r["truth"])] += 1
        decided = sum(v for (m, _t), v in c.items() if m != "unsure")
        correct = c[("tp", "tp")] + c[("fp", "fp")]
        called_tp = c[("tp", "tp")] + c[("tp", "fp")]
        called_fp = c[("fp", "fp")] + c[("fp", "tp")]
        return {"n": len(rs), "decided": decided, "unsure": len(rs) - decided,
                "accuracy_decided": round(correct / decided, 3) if decided else None,
                "tp_call_precision": round(c[("tp", "tp")] / called_tp, 3) if called_tp else None,
                "tp_call_wilson_lower": ls.wilson(c[("tp", "tp")], called_tp)[0] if called_tp else None,
                "fp_call_precision": round(c[("fp", "fp")] / called_fp, 3) if called_fp else None,
                "false_tp_rate": round(c[("tp", "fp")] / (c[("tp", "fp")] + c[("fp", "fp")] + c[("unsure", "fp")]), 3)
                if any(t == "fp" for (_m, t) in c) else None,
                "truth_tp_recall": round(c[("tp", "tp")] / sum(v for (_m, t), v in c.items() if t == "tp"), 3)
                if any(t == "tp" for (_m, t) in c) else None,
                "confusion": {f"{m}|truth={t}": v for (m, t), v in sorted(c.items())}}
    out = {"final": score(recs, lambda r: r["label"]),
           "prosecutor": score(recs, lambda r: r["votes"]["prosecutor"]["label"]),
           "defender": score(recs, lambda r: r["votes"]["defender"]["label"]),
           "unanimous_only": score([r for r in recs if "adjudicator" not in r["votes"]], lambda r: r["label"]),
           "kappa_prosecutor_defender": kappa_pairs(recs),
           "kappa_final_vs_truth": ls.cohens_kappa(
               [r["label"] for r in recs if r["label"] != "unsure"],
               [r["truth"] for r in recs if r["label"] != "unsure"]),
           "by_source": {}}
    for src in sorted({r["source"] for r in recs}):
        out["by_source"][src] = score([r for r in recs if r["source"] == src], lambda r: r["label"])
    return out


# ---------------------------------------------------------------------------

def run_label(items: list[dict], labeller: Labeller, out: Path, jobs: int) -> list[dict]:
    done: dict[str, dict] = {}
    for r in g.read_jsonl(out):
        done[r["finding_id"] + "|" + r["excerpt_sha256"]] = r
    todo = [it for it in items if it["finding_id"] + "|" + it["excerpt_sha256"] not in done]
    print(f"{len(items)} items, {len(items) - len(todo)} already labelled, {len(todo)} to do", file=sys.stderr)
    results = list(done.values())
    lock = threading.Lock()

    def work(it):
        rec = labeller.label(it)
        if rec is None:
            return
        with lock:
            results.append(rec)
            with out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = [pool.submit(work, it) for it in todo]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            fut.result()
            if i % 25 == 0:
                print(f"  {i}/{len(todo)} spent ${labeller.budget.spent:.2f}", file=sys.stderr, flush=True)
    if labeller.budget.limit_hit:
        print(f"stopped: the CLI reported a limit ({labeller.budget.limit_hit}); rerun to resume",
              file=sys.stderr)
    elif labeller.budget.stopped:
        print(f"budget ${labeller.budget.cap} reached: stopped with {len(todo) - (len(results) - len(done))} "
              "items unlabelled; rerun with a higher --budget-usd to resume", file=sys.stderr)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lab = sub.add_parser("label", help="label findings with two model labellers and an adjudicator")
    lab.add_argument("--report", type=Path, help="gold-report.json written with --raw")
    lab.add_argument("--corpus", type=Path, default=Path("~/wt/corpus-gold"))
    lab.add_argument("--ai-corpus", action="store_true", help="every finding on fixtures/ai-corpus")
    lab.add_argument("--ground-truth", type=Path, nargs="*", default=[],
                     help="ground-truth JSONL: label a blind sample for calibration")
    lab.add_argument("--exclude-labels", type=Path, nargs="*", default=[],
                     help="label files whose findings a ground-truth sample must not reuse")
    lab.add_argument("--planted", nargs="*", default=[], metavar="CATEGORY",
                     help="calibration items from fixtures/planted bad/good twins for these categories")
    lab.add_argument("--planted-cap", type=int, help="at most this many bad-side findings per category and language")
    lab.add_argument("--suspects", type=Path, help="cve-suspects.json from cve_pairs.py")
    lab.add_argument("--n", type=int, default=600, help="sample size (report and ground truth)")
    lab.add_argument("--per-rule", type=Path,
                     help="report: a JSON object rule -> sample size ('*' for the rest) instead of "
                          "the square-root allocation of --n")
    lab.add_argument("--max", type=int, help="label at most this many items")
    lab.add_argument("--seed", type=int, default=20261022)
    lab.add_argument("--budget-usd", type=float, default=25.0)
    lab.add_argument("--model", default=MODEL)
    lab.add_argument("--jobs", type=int, default=6)
    lab.add_argument("--cache", type=Path, default=ROOT / "arbiter-out" / "label-cache.jsonl")
    lab.add_argument("--out", type=Path, required=True)
    lab.add_argument("--dry-run", action="store_true", help="build the items and print one prompt")
    sm = sub.add_parser("summarize", help="tables from label files")
    sm.add_argument("--labels", type=Path, nargs="+", required=True)
    sm.add_argument("--json", type=Path)
    sm.add_argument("--markdown", type=Path, help="write the tables as Markdown")
    fc = sub.add_parser("fact-check", help="model labels against mechanically checkable facts")
    fc.add_argument("--labels", type=Path, nargs="+", required=True)
    fc.add_argument("--corpus", type=Path, default=Path("~/wt/corpus-gold"))
    fc.add_argument("--json", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "fact-check":
        res = fact_check([r for p in args.labels for r in g.read_jsonl(p)], args.corpus.expanduser())
        if args.json:
            args.json.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
        for k, e in res.items():
            print(f"{k}: n={e['n']} agree={e['agree']} unsure={e['unsure']} fact tp/fp={e['fact_tp']}/{e['fact_fp']} "
                  f"model tp calls right {e['model_tp_right']}/{e['model_tp_calls']}")
        return 0

    if args.cmd == "summarize":
        recs = [r for p in args.labels for r in g.read_jsonl(p)]
        res = summarize(recs)
        if args.json:
            args.json.write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
        if args.markdown:
            args.markdown.write_text(render_tables(res), encoding="utf-8")
        print(json.dumps(res["overall"], indent=1))
        return 0

    items: list[dict] = []
    meta = {}
    if args.report:
        per_rule = json.loads(args.per_rule.read_text(encoding="utf-8")) if args.per_rule else None
        its, meta = items_from_report(args.report, args.corpus.expanduser(), args.n, args.seed, per_rule)
        items += its
    if args.ai_corpus:
        items += items_from_ai_corpus()
    if args.ground_truth:
        used = {r["finding_id"] for p in args.exclude_labels for r in g.read_jsonl(p)}
        items += items_from_ground_truth(args.ground_truth, args.n, args.seed, used)
    if args.planted:
        items += items_from_planted(set(args.planted), args.planted_cap)
    if args.suspects:
        items += items_from_suspects(args.suspects)
    if args.max:
        items = items[: args.max]
    print(f"{len(items)} items {dict(Counter(it['source'] for it in items))} {meta}", file=sys.stderr)
    if args.dry_run:
        if items:
            print(SYSTEM + "\n----\n" + role_prompt(items[0], "prosecutor"))
        return 0
    budget = Budget(args.budget_usd)
    labeller = Labeller(Cache(args.cache), budget, args.model)
    results = run_label(items, labeller, args.out, args.jobs)
    print(f"{len(results)} labelled, ${budget.spent:.2f} spent in {budget.calls} new calls -> {args.out}")
    return 3 if budget.stopped else 0


def summarize(recs: list[dict]) -> dict:
    model = [r for r in recs if r.get("labeller") != "ground-truth" and not r["source"].startswith("calibration:")]
    gt = [r for r in recs if r.get("labeller") == "ground-truth"]
    cal = [r for r in recs if r["source"].startswith("calibration:")]
    for r in gt:
        r.setdefault("category", category(r["rule_id"]))
    both = model + gt
    return {
        "overall": {"model_labels": len(model), "ground_truth_labels": len(gt), "calibration": len(cal),
                    "cost_usd": round(sum(r.get("cost_usd", 0) for r in model + cal), 2)},
        "by_rule": tally(both, lambda r: r["rule_id"]),
        "by_category": tally(both, lambda r: r.get("category") or category(r["rule_id"])),
        "by_rule_model": tally(model, lambda r: r["rule_id"]),
        "by_rule_truth": tally(gt, lambda r: r["rule_id"]),
        "calibration": calibration(cal) if cal else None,
    }


def _row(name: str, e: dict) -> str:
    p = "-" if e["precision"] is None else f"{e['precision']:.3f}"
    lo = "-" if e["wilson_lower"] is None else f"{e['wilson_lower']:.3f}"
    k = "-" if e["kappa"] is None else f"{e['kappa']:.2f}"
    a = "-" if e["agreement"] is None else f"{e['agreement']:.2f}"
    return f"| {name} | {e['tp']} | {e['fp']} | {e['unsure']} | {p} | {lo} | {a} | {k} |"


_HEAD = ["| {} | TP | FP | unsure | precision | Wilson 95% lower | agreement | kappa |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]


def render_tables(res: dict) -> str:
    out = []
    for title, key, col in (("By category: model and ground-truth labels together", "by_category", "category"),
                            ("By rule: ground truth only (OWASP, Juliet, CVE fixes)", "by_rule_truth", "rule"),
                            ("By rule: model labels only (gold sample, AI corpus, CVE suspects)", "by_rule_model", "rule")):
        out += ["", f"### {title}", "", _HEAD[0].format(col), _HEAD[1]]
        out += [_row(f"`{k}`" if col == "rule" else k, e) for k, e in res[key].items()]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
