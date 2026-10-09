"""The model-backed judgement pass.

## What a model is for here, and what it is not for

Everything else in Arbiter answers questions with a definite shape: is this
property set, does this value match this pattern, does this address appear in
two repositories with different values. A model is worth adding only for the
questions that have no such shape -- whether the README describes behaviour the
code no longer has, whether a comment and the function beneath it disagree,
whether a variable named `temp_fix_DO_NOT_SHIP` is still shipping.

It is emphatically not here to re-find secrets or misconfigurations. Those have
deterministic answers, and swapping a decidable check for a probabilistic one
is a downgrade dressed as an upgrade.

## Three rules this pass obeys

**1. Inferred findings never blend with deterministic ones.** Every finding
from this pass carries `provenance="inferred"`, and the gate ignores inferred
findings unless `gate.gate_inferred` is explicitly set. A model's opinion can
inform a person; it should not turn somebody's build red on its own.

**2. No provider configured is NOT-ASSESSED, never a pass.** This is the rule
the whole file exists to protect. The easy implementation returns an empty list
when there is no API key, and an empty list is indistinguishable from "the
model looked and found nothing". Every scan without a provider would then
quietly report clean documentation drift. So the probe raises `Unavailable`,
the engine records `skipped` with the reason, and the coverage figure drops by
exactly the amount that was not checked.

**3. The model never sees more than it needs, and never runs the code.** Only
file excerpts are sent, secrets are masked before sending, and the response is
parsed as data. A model cannot be allowed to name a file path that was never
in the request -- see `_reconcile`.

## Determinism

A model is not reproducible in the way the rest of the tool is, and pretending
otherwise would be worse than admitting it. Two mitigations, both partial and
both stated in the report rather than hidden:

  * temperature 0 and a pinned model id, recorded in the outcome, so a run is
    as repeatable as the provider allows;
  * inferred findings excluded from the gate by default, so irreproducibility
    cannot change a pass/fail outcome without somebody opting in.

The `offline` and `ci` profiles forbid model calls outright. That is the
setting to use when reproducibility must be absolute.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .core import Finding, Location
from .probes import ProbeContext

# Kept small on purpose. A judgement pass that reads an entire repository is
# slow, expensive, and mostly reading code the question does not concern.
MAX_FILES = 24
MAX_BYTES_PER_FILE = 6_000
MAX_TOTAL_BYTES = 90_000

DEFAULT_MODEL = "claude-sonnet-4-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class Unavailable(RuntimeError):
    """No usable provider. Raised so the probe records not-assessed.

    Deliberately an exception rather than an empty result. An empty result is
    the same shape as "checked, found nothing", and that is the confusion this
    whole module is written to avoid.
    """


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

@dataclass
class Provider:
    """One way to ask a model a question. Adding a vendor is a subclass."""

    name: str = "none"
    model: str = ""
    timeout: int = 120

    def available(self) -> tuple[bool, str]:
        return False, "no provider configured"

    def complete(self, system: str, user: str) -> str:
        raise Unavailable("no provider configured")


@dataclass
class AnthropicProvider(Provider):
    name: str = "anthropic"
    model: str = DEFAULT_MODEL
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens: int = 4000

    def available(self) -> tuple[bool, str]:
        if not os.environ.get(self.api_key_env):
            return False, f"{self.api_key_env} is not set"
        return True, ""

    def complete(self, system: str, user: str) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise Unavailable(f"{self.api_key_env} is not set")
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            # Zero temperature is the most determinism a hosted model offers.
            # It is not a guarantee, which is why inferred findings do not gate.
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            ANTHROPIC_URL, data=body, method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                doc = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200] if hasattr(e, "read") else ""
            raise Unavailable(f"provider returned HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise Unavailable(f"{type(e).__name__}: {e}"[:200]) from e
        parts = [b.get("text", "") for b in doc.get("content", [])
                 if b.get("type") == "text"]
        return "".join(parts)


PROVIDERS = {"anthropic": AnthropicProvider, "none": Provider}


def build_provider(config: dict) -> Provider:
    jm = (config.get("judgement") or {})
    name = jm.get("provider", "anthropic")
    cls = PROVIDERS.get(name)
    if cls is None:
        raise Unavailable(f"unknown provider '{name}'; known: {', '.join(PROVIDERS)}")
    kwargs: dict[str, Any] = {}
    if jm.get("model"):
        kwargs["model"] = jm["model"]
    if jm.get("timeout"):
        kwargs["timeout"] = int(jm["timeout"])
    if jm.get("api_key_env"):
        kwargs["api_key_env"] = jm["api_key_env"]
    try:
        return cls(**kwargs)
    except TypeError:
        return cls()


# ---------------------------------------------------------------------------
# The question
# ---------------------------------------------------------------------------

SYSTEM = """\
You are reviewing a source repository for claims that contradict the code.

Report only DISAGREEMENTS you can point at: documentation or a comment that \
says something the code does not do, a stated default that differs from the \
actual default, an example that would not run, a named parameter or endpoint \
that no longer exists.

Do NOT report:
  - missing documentation, style, naming, formatting, or test coverage
  - security or configuration problems (other checks own those, and they can
    answer definitively where you can only guess)
  - anything you are inferring from a file you were not shown

Reply with JSON only, no prose around it:

{"findings":[{"path":"<exact path as given>","line":<int or 0>,
  "title":"<one line>","detail":"<what disagrees with what>",
  "confidence":"high|medium|low"}]}

An empty list is a good answer. Report nothing you cannot quote."""


def _mask_secrets(text: str) -> str:
    """Redact credential-shaped material before it leaves the machine."""
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                  "[redacted private key]", text, flags=re.S)
    text = re.sub(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[redacted aws key]", text)
    text = re.sub(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", "[redacted token]", text)
    text = re.sub(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "[redacted token]", text)
    text = re.sub(r"(?i)\b([a-z0-9_.-]*(?:password|secret|token|api[_-]?key)"
                  r"[a-z0-9_.-]*)\s*[:=]\s*(\S{6,})", r"\1=[redacted]", text)
    return text


def _select_files(ctx: ProbeContext) -> list[tuple[str, str]]:
    """Documentation plus the code it most plausibly describes."""
    from .probes import _read
    docs, code = [], []
    for f in ctx.inventory.text_files():
        if f.binary or f.role in ("generated", "data"):
            continue
        if f.language in ("markdown", "rst") or f.path.lower().startswith("readme"):
            docs.append(f)
        elif f.role in ("source", "config", "iac") and f.lines > 4:
            code.append(f)
    docs.sort(key=lambda f: (f.path.count("/"), -f.lines))
    code.sort(key=lambda f: -f.lines)

    picked, total = [], 0
    for f in docs[: MAX_FILES // 2] + code[: MAX_FILES // 2]:
        text = _read(f)
        if not text:
            continue
        text = _mask_secrets(text[:MAX_BYTES_PER_FILE])
        if total + len(text) > MAX_TOTAL_BYTES:
            break
        total += len(text)
        picked.append((f.path, text))
    return picked


def _reconcile(raw: str, sent_paths: set[str], repo_id: str) -> list[Finding]:
    """Turn the reply into findings, discarding anything unverifiable.

    A model will occasionally name a file it was never shown, or one it half
    remembers from training. Such a finding cites evidence that does not exist
    in this repository, which is precisely the thing this tool is built not to
    do, so it is dropped rather than reported with a caveat.
    """
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return []
    try:
        doc = json.loads(m.group(0))
    except ValueError:
        return []

    out: list[Finding] = []
    for row in (doc.get("findings") or [])[:50]:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path", "")).strip().lstrip("./")
        if path not in sent_paths:
            continue  # not a file we showed it; it cannot support the claim
        title = str(row.get("title", "")).strip()[:160]
        if not title:
            continue
        detail = str(row.get("detail", "")).strip()[:800]
        conf = str(row.get("confidence", "medium")).lower()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        try:
            line = int(row.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        out.append(Finding(
            rule_id="arbiter/judgement.claim-contradicts-code",
            title=title,
            dimension="drift",
            # Capped at medium regardless of how certain the model sounds.
            # Confidence expressed by a model is not the same quantity as
            # confidence measured from adjudicated outcomes, and letting the
            # two share a scale would be a category error.
            severity="medium" if conf == "high" else "low",
            confidence="medium" if conf == "high" else "low",
            provenance="inferred",
            repo_id=repo_id,
            probe="judgement",
            location=Location(path=path, start_line=line, repo_id=repo_id),
            description=detail or title,
            remediation="Correct whichever of the two is wrong, then re-run.",
            evidence=f"judgement:{path}:{title[:60]}",
            tags=["inferred", "model"],
        ))
    return out


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------

def probe_judgement(ctx: ProbeContext) -> list[Finding]:
    provider = build_provider(ctx.config)
    ok, why = provider.available()
    if not ok:
        # The load-bearing line in this file. Not an empty list.
        raise Unavailable(why)

    out: list[Finding] = []
    for repo in ctx.repos:
        files = [(p, t) for p, t in _select_files(ctx)]
        if not files:
            continue
        sent = {p for p, _ in files}
        body = "\n\n".join(f"=== {p} ===\n{t}" for p, t in files)
        reply = provider.complete(SYSTEM, body)
        out.extend(_reconcile(reply, sent, repo.id))
    return out


def register_judgement() -> None:
    """Registered unconditionally, so an unconfigured provider shows as
    not-assessed rather than vanishing from the coverage denominator."""
    from .probes import Probe, register
    register(Probe(
        name="judgement",
        dimensions=["drift"],
        checks=4,
        run=probe_judgement,
        network=True,
        model=True,
    ))
