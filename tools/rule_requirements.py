#!/usr/bin/env python3
"""Propose `requirements: [ARB-xxx]` for every shipped rule, from evidence.

    python tools/rule_requirements.py propose --json arbiter-out/rule-reqs.json
    python tools/rule_requirements.py propose --missing     # rules with no evidence
    python tools/rule_requirements.py write                 # write the keys (after review)

A one-off for ARB-064: a finding should trace to the requirement its rule
was built or fixed under, and on real data only 8 of 506 did. The evidence,
strongest first:

  changelog   a `CHANGELOG.md` bullet `- ARB-062 | ... \\`go.ssrf-request-url\\` ...`:
              the ids before the `|` (several are allowed, and `FAIL-050
              (ARB-048) |` counts), and every rule id in backticks in the bullet,
              with or without the `arbiter/` prefix. A bare id (`\\`ssrf-request-url\\``)
              counts when exactly one rule has it, or when the bullet names the
              language of one of the rules that do.
  pack        an ARB id in a pack comment: the file header applies to every rule
              in the pack, a `# ---` section header to the rules up to the next
              section, a comment inside a rule (or a `;` comment in its query) to
              that rule only.
  registry    `.ai/requirements/requirements.json` (and the archive) text that
              names the rule by its full or `pack.id` form.
  git         for a rule with none of the above: the ARB ids in the subjects of
              the commits that added its id string (`git log -S`).

`write` puts the proposal into the packs (`requirements:` after `- id:`) and
into `packs/rules/native.yaml` for the rules coded in Python. Review the
proposal before writing; the mapping is a claim about history, and a wrong
id is worse than none.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PACKS = ROOT / "src" / "arbiter" / "packs" / "rules"
CHANGELOG = ROOT / "CHANGELOG.md"
REGISTRIES = [ROOT / ".ai" / "requirements" / "requirements.json",
              ROOT / ".ai" / "requirements" / "requirements-archive.json"]
ARB = re.compile(r"\b(ARB|REQ)-(\d{3})\b")
BACKTICK = re.compile(r"`([^`\s]{3,120})`")
LANG_WORDS = {"python": ("python", "flask", "django"), "typescript": ("typescript", "javascript", "node", "ts "),
              "go": (" go ", "go's", "golang", "go and", "go,"), "java": ("java ", "java,", "java's", "spring"),
              "ruby": ("ruby", "rails")}


# ---------------------------------------------------------------------------
# What exists
# ---------------------------------------------------------------------------

def registry() -> tuple[set[str], dict[str, str]]:
    ids: set[str] = set()
    aliases: dict[str, str] = {}
    for p in REGISTRIES:
        if not p.is_file():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        ids |= {r["id"] for r in d.get("requirements", [])}
        aliases.update(d.get("id_aliases") or {})
    return ids, aliases


def canonical(token: str, known: set[str], aliases: dict[str, str]) -> str | None:
    token = aliases.get(token, token)
    if token.startswith("REQ-"):
        token = "ARB-" + token[4:]
    return token if token in known else None


def all_rules() -> dict[str, dict]:
    """rule id -> {pack, short, language} for every shipped rule."""
    from arbiter.rule_meta import native_rule_ids_from_source, shipped_rules
    every = {**{r: {"_source": "native"} for r in native_rule_ids_from_source()}, **shipped_rules()}
    out = {}
    for rid, meta in every.items():
        short = rid.split("/", 1)[1]                # go.ssrf-request-url
        pack, _, bare = short.partition(".")
        out[rid] = {"pack": pack, "short": short, "bare": bare, "source": meta.get("_source", "")}
    return out


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def changelog_bullets(text: str) -> list[str]:
    bullets, cur = [], None
    for line in text.splitlines():
        if line.startswith("- ") or line.startswith("#"):
            if cur is not None:
                bullets.append(cur)
            cur = line[2:] if line.startswith("- ") else None
        elif cur is not None:
            cur += "\n" + line
    if cur is not None:
        bullets.append(cur)
    return bullets


def resolve_token(tok: str, rules: dict, by_short: dict, by_bare: dict, context: str) -> list[str]:
    tok = tok.strip().rstrip(".,:;")
    if tok.startswith("arbiter/"):
        return [tok] if tok in rules else []
    if tok in by_short:
        return [by_short[tok]]
    cands = by_bare.get(tok, [])
    if len(cands) == 1:
        return cands
    if len(cands) > 1:
        low = f" {context.lower()} "
        named = [c for c in cands if any(w in low for w in LANG_WORDS.get(rules[c]["pack"], (rules[c]["pack"],)))]
        return named
    return []


def from_changelog(rules: dict, known: set[str], aliases: dict) -> dict[str, list[dict]]:
    by_short = {m["short"]: r for r, m in rules.items()}
    by_bare: dict[str, list[str]] = defaultdict(list)
    for r, m in rules.items():
        by_bare[m["bare"]].append(r)
    ev: dict[str, list[dict]] = defaultdict(list)
    for bullet in changelog_bullets(CHANGELOG.read_text(encoding="utf-8")):
        head, sep, _ = bullet.partition(" | ")
        if not sep:
            continue
        ids = sorted({c for m in ARB.finditer(head)
                      if (c := canonical(m.group(0), known, aliases))})
        if not ids:
            continue
        hit: set[str] = set()
        for tok in BACKTICK.findall(bullet):
            hit.update(resolve_token(tok, rules, by_short, by_bare, bullet))
        for r in hit:
            ev[r].append({"source": "changelog", "ids": ids, "text": bullet[:140].replace("\n", " ")})
    return ev


def from_packs(rules: dict, known: set[str], aliases: dict) -> dict[str, list[dict]]:
    ev: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(PACKS.rglob("*.yaml")):
        if path.name == "native.yaml":   # written from this tool's own output
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        pack = path.stem if path.parent.name == "ast" else "resource"
        header_ids: list[str] = []
        section_ids: list[str] = []
        pending: list[str] = []          # a plain comment block's ids, for the next rule
        block: list[str] = []
        current: str | None = None
        in_rules = False

        def ids_in(text: str) -> list[str]:
            return sorted({c for m in ARB.finditer(text) if (c := canonical(m.group(0), known, aliases))})

        def flush_block():
            nonlocal section_ids, pending, block
            if not block:
                return
            text = "\n".join(block)
            if "# ---" in text or "# ===" in text:
                section_ids = ids_in(text)
                pending = []
            else:
                pending = ids_in(text)
            block = []

        for line in lines:
            if line.startswith("rules:"):
                in_rules = True
                continue
            if not in_rules:
                if line.startswith("#"):
                    header_ids += ids_in(line)
                continue
            m = re.match(r"^  - id:\s*([A-Za-z0-9_.-]+)", line)
            if m:
                flush_block()
                current = f"arbiter/{pack}.{m.group(1)}"
                if current not in rules:
                    current = None
                    continue
                for ids, src in ((header_ids, "pack-header"), (section_ids, "pack-section"),
                                 (pending, "pack-comment")):
                    if ids:
                        ev[current].append({"source": src, "ids": sorted(set(ids)),
                                            "text": f"{path.relative_to(ROOT)}"})
                pending = []
                continue
            if re.match(r"^  #", line) or (not line.strip() and block):
                block.append(line)
                continue
            if current and ("#" in line or ";" in line):
                ids = ids_in(line)
                if ids:
                    ev[current].append({"source": "pack-rule-comment", "ids": ids,
                                        "text": line.strip()[:140]})
    return ev


def from_registry(rules: dict, known: set[str], aliases: dict) -> dict[str, list[dict]]:
    ev: dict[str, list[dict]] = defaultdict(list)
    for p in REGISTRIES:
        if not p.is_file():
            continue
        for req in json.loads(p.read_text(encoding="utf-8")).get("requirements", []):
            text = json.dumps({k: req.get(k) for k in ("title", "description", "acceptance_criteria",
                                                       "risk_notes")})
            rid = canonical(req["id"], known, aliases)
            if not rid:
                continue
            for r, m in rules.items():
                if re.search(rf"(?<![\w/.-])(?:arbiter/)?{re.escape(m['short'])}(?![\w-])", text):
                    ev[r].append({"source": "registry", "ids": [rid], "text": req["title"][:100]})
    return ev


def from_git(rule: str, meta: dict, known: set[str], aliases: dict) -> list[dict]:
    """The ids in the subject of the commit that first added the rule's id.
    Only the introducing commit: later commits that move the string around
    (a rename, a merge) say nothing about why the rule exists. A subject that
    cites a range (`REQ-032..REQ-037`) is ambiguous and skipped."""
    needle = f"id: {meta['bare']}" if meta["source"] == "pack" else rule
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "log", "--reverse", "--format=%h %s", "-S", needle,
                              "--", "src"], capture_output=True, text=True, timeout=120, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    first = out.strip().splitlines()[0] if out.strip() else ""
    if not first or ".." in first:
        return []
    ids = sorted({c for m in ARB.finditer(first) if (c := canonical(m.group(0), known, aliases))})
    return [{"source": "git", "ids": ids, "text": first[:120]}] if ids else []


# Reviewed by hand (2026-10-22). Each entry removes evidence that names a
# rule without the requirement having built or changed it.
OVERRIDES: dict[str, dict] = {
    "arbiter/resource.k8s-no-security-context": {
        "drop": ["ARB-017"], "why": "ARB-017 (review sampler) names it as an example finding"},
    "arbiter/resource.unencrypted-database": {
        "drop": ["ARB-017"], "why": "ARB-017 (review sampler) names it as an example finding"},
    "arbiter/secrets.private-key": {
        "drop": ["ARB-017"], "why": "ARB-017 (review sampler) names it as an example finding"},
    "arbiter/assurance.blanket-suppression": {
        "drop": ["ARB-011"], "why": "ARB-011 (output directory) mentions it in passing"},
    "arbiter/concurrency.check-then-act-unlocked": {
        "drop": ["ARB-054"], "why": "one bullet covers both concurrency rules; ARB-054 is the counter rule"},
    "arbiter/concurrency.unsynchronised-counter": {
        "drop": ["ARB-053"], "why": "one bullet covers both concurrency rules; ARB-053 is check-then-act"},
    "arbiter/secrets.pg-url": {
        "drop": ["ARB-062"], "why": "an ARB-062 bullet cites it as the model for a Go rule; pg-url itself was not changed"},
    "arbiter/drift.documented-env-var-absent": {
        "drop": ["ARB-065"], "why": "the ARB-065 corpus bullet reports a defect in it; nothing was changed"},
}


def propose(use_git: bool = True) -> dict[str, dict]:
    known, aliases = registry()
    rules = all_rules()
    evidence: dict[str, list[dict]] = defaultdict(list)
    for fn in (from_changelog, from_packs, from_registry):
        for r, items in fn(rules, known, aliases).items():
            evidence[r].extend(items)
    out = {}
    for r in sorted(rules):
        items = evidence.get(r, [])
        if not items and use_git:
            items = from_git(r, rules[r], known, aliases)
        ids = {i for e in items for i in e["ids"]}
        override = OVERRIDES.get(r)
        if override:
            ids -= set(override.get("drop", []))
            ids |= set(override.get("add", []))
            items = [*items, {"source": "override", "ids": sorted(override.get("add", [])),
                              "text": override["why"]}]
        out[r] = {"proposed": sorted(ids), "evidence": items}
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def write_pack_keys(mapping: dict[str, list[str]]) -> int:
    """Insert or replace `requirements:` directly after each rule's `- id:`."""
    changed = 0
    for path in sorted(PACKS.rglob("*.yaml")):
        if path.name == "native.yaml":
            continue
        pack = path.stem if path.parent.name == "ast" else "resource"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        out: list[str] = []
        skip_existing = False
        for line in lines:
            if skip_existing:
                skip_existing = False
                if re.match(r"^    requirements:", line):
                    continue
            out.append(line)
            m = re.match(r"^  - id:\s*([A-Za-z0-9_.-]+)\s*$", line)
            if m:
                ids = mapping.get(f"arbiter/{pack}.{m.group(1)}")
                if ids:
                    out.append(f"    requirements: [{', '.join(ids)}]\n")
                    changed += 1
                    skip_existing = True
        path.write_text("".join(out), encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["propose", "write"])
    ap.add_argument("--json", type=Path, help="propose: write the full proposal with evidence")
    ap.add_argument("--missing", action="store_true", help="propose: list only rules with no evidence")
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--from", dest="source", type=Path,
                    help="write: a reviewed proposal JSON (default: propose afresh)")
    args = ap.parse_args(argv)
    proposal = (json.loads(args.source.read_text(encoding="utf-8")) if args.source
                else propose(use_git=not args.no_git))
    if args.cmd == "propose":
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(json.dumps(proposal, indent=1) + "\n", encoding="utf-8")
        with_ids = sum(1 for v in proposal.values() if v["proposed"])
        for r, v in proposal.items():
            if args.missing and v["proposed"]:
                continue
            srcs = ",".join(sorted({e["source"] for e in v["evidence"]})) or "-"
            print(f"{r:<58} {' '.join(v['proposed']) or '(none)':<30} {srcs}")
        print(f"{with_ids} of {len(proposal)} rules have evidence", file=sys.stderr)
        return 0
    mapping = {r: v["proposed"] for r, v in proposal.items() if v["proposed"]}
    n = write_pack_keys(mapping)
    print(f"wrote requirements on {n} pack rules; native rules go in packs/rules/native.yaml "
          "(edit it by hand from the proposal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
