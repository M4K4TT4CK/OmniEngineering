"""Shared pieces of the ground-truth and model-labelling tools (ARB-067).

* `excerpt()`: what a labeller is shown, built by `arbiter.context` (the
  same builder verify.py uses): the enclosing function with its decorators,
  imports, same-file callees, same-file callers when the flagged value is a
  parameter, and the repository definitions of calls on the taint path, bounded, line-numbered, the flagged line marked, secrets
  masked with `arbiter.judgement._mask_secrets`.
* `blind()`: for items whose truth is known (OWASP Benchmark, Juliet), the
  text with comments blanked and test-case names replaced, so a model being
  calibrated against the truth cannot read it off a `/* FLAW */` comment, a
  `goodG2B` method name or a `BenchmarkTest01234` class. Line numbers are kept.
* `label()`: one JSON-lines label record (docs/precision.md, "Label format").
* `scan_tree()`: Arbiter's active findings for a directory, with ids.

Nothing here runs the code it reads.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

LABELS = ("tp", "fp", "unsure")
# The excerpt itself is built by `arbiter.context` (shared with verify.py);
# these are the labellers' bounds.
MAX_FUNCTION_LINES = 80
MAX_IMPORT_LINES = 25
MAX_HELPERS = 3          # same-file functions the enclosing one calls
MAX_HELPER_LINES = 40    # each
MAX_DEFS = 3             # definitions from other files of the repository
MAX_DEF_LINES = 30       # each
MAX_CALLERS = 2          # same-file callers, when the flagged value is a parameter
MAX_CALLER_LINES = 25    # each
MAX_EXCERPT_LINES = 220  # everything shown
WINDOW = 30  # lines each side when no enclosing function is found

_LANG_BY_EXT = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
                ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".go": "go",
                ".java": "java", ".rb": "ruby"}
_COMMENT_TYPES = {"comment", "line_comment", "block_comment"}


def language_of(path: str) -> str:
    return _LANG_BY_EXT.get(Path(path).suffix, "")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mask(text: str) -> str:
    from arbiter.judgement import _mask_secrets
    return _mask_secrets(text)


# ---------------------------------------------------------------------------
# Excerpts
# ---------------------------------------------------------------------------

def _tree(text: str, path: str, language: str):
    try:
        from arbiter import ast as ts
        grammar = ts.grammar_for(path, language)
        if not grammar or not ts.available():
            return None
        return ts._parser(grammar).parse(text.encode("utf-8"))
    except Exception:  # noqa: BLE001 - no tree means "fall back to a window", never a crash
        return None


def _function_span(text: str, path: str, language: str, line: int) -> tuple[int, int] | None:
    """1-based inclusive line span of the innermost named function around
    `line`, its decorators/annotations included."""
    from arbiter import context
    return context.function_span(text, path, language, line)


def _named_functions(text: str, path: str, language: str) -> dict[str, tuple[int, int]]:
    """Name -> 1-based line span of every named function in the file (first wins)."""
    from arbiter import context
    return context.named_functions(text, path, language)


def limits():
    from arbiter import context
    return context.Limits(function_lines=MAX_FUNCTION_LINES, import_lines=MAX_IMPORT_LINES,
                          helpers=MAX_HELPERS, helper_lines=MAX_HELPER_LINES, defs=MAX_DEFS,
                          def_lines=MAX_DEF_LINES, total=MAX_EXCERPT_LINES, window=WINDOW,
                          callers=MAX_CALLERS, caller_lines=MAX_CALLER_LINES)


def repo_view(root: Path, kind: str | None = None):
    """The repository around a finding, for cross-file definitions. With a
    blinding `kind`, every other file is blinded the same way as the
    finding's own file, and its path too."""
    from arbiter import context
    if not kind:
        return context.RepoView.from_root(root)
    return context.RepoView.from_root(root, transform=lambda text, rel: blind(text, rel, kind),
                                      shown=lambda rel: blind_other_path(rel, kind))


def excerpt(text: str, path: str, line: int, language: str | None = None, repo=None) -> str:
    """What a labeller is shown (`arbiter.context.build`): the enclosing
    function with its decorators, class header, imports, route registration,
    same-file callees and, with `repo`, the repository definitions of calls on
    the taint path. Numbered, `>>` on `line`, secrets masked. `path` is
    relative to the repository root when `repo` is given."""
    from arbiter import context
    return context.build(text, path, line, language or language_of(path), repo=repo,
                         limits=limits()).text


# ---------------------------------------------------------------------------
# Blinding (ground-truth items only)
# ---------------------------------------------------------------------------

def blank_comments(text: str, path: str, language: str | None = None) -> str:
    """Comments replaced by spaces (newlines kept, so line numbers hold)."""
    language = language or language_of(path)
    tree = _tree(text, path, language)
    if tree is None:
        return text
    data = bytearray(text.encode("utf-8"))
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in _COMMENT_TYPES:
            for b in range(n.start_byte, n.end_byte):
                if data[b] not in (0x0A, 0x0D):
                    data[b] = 0x20
            continue
        stack.extend(n.children)
    return data.decode("utf-8", errors="replace")


_JULIET_NAME = re.compile(r"\b(bad|good)(\w*)\b")
_JULIET_CLASS = re.compile(r"\bCWE\d+_\w+\b")
_OWASP_CLASS = re.compile(r"BenchmarkTest\d+")
_PLANTED_CWE = re.compile(r"(?i)\bcwe[-_ ]?\d+[-_ :]*")
_PLANTED_PKG = re.compile(r"\bplanted\b")


def _blank_docstrings(text: str, path: str) -> str:
    """Python bare string statements (docstrings) replaced by spaces: in the
    planted fixtures they say what the file plants."""
    tree = _tree(text, path, "python")
    if tree is None:
        return text
    data = bytearray(text.encode("utf-8"))
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        # The grammar in use puts a bare string straight under the block
        # (older ones wrap it in an expression_statement).
        bare = n.type == "string" and n.parent is not None and n.parent.type in ("module", "block")
        wrapped = (n.type == "expression_statement" and n.named_child_count == 1
                   and n.named_children[0].type == "string")
        if bare or wrapped:
            for b in range(n.start_byte, n.end_byte):
                if data[b] not in (0x0A, 0x0D):
                    data[b] = 0x20
            continue
        stack.extend(n.children)
    return data.decode("utf-8", errors="replace")


def blind(text: str, path: str, kind: str) -> str:
    """Remove what would let a reader see the answer instead of judging the code."""
    text = blank_comments(text, path)
    if kind == "juliet":
        names: dict[str, str] = {}

        def rename(m: re.Match) -> str:
            key = m.group(0)
            names.setdefault(key, f"handler{len(names) + 1}")
            return names[key]
        text = _JULIET_NAME.sub(rename, text)
        text = _JULIET_CLASS.sub("Case", text)
    elif kind == "owasp":
        text = _OWASP_CLASS.sub("Handler", text)
    elif kind == "planted":
        if language_of(path) == "python":
            text = _blank_docstrings(text, path)
        text = _PLANTED_PKG.sub("app", _PLANTED_CWE.sub("", text))
    return text


def blind_other_path(path: str, kind: str) -> str:
    """The shown path of a file other than the finding's own (a cross-file
    definition). Juliet support files keep their names (`IO.java`); another
    test-case file would give the case away and is called `Case2`."""
    if kind == "juliet":
        name = Path(path).name
        return "src/main/java/app/" + ("Case2" + Path(path).suffix if re.search(r"CWE\d", name) else name)
    return blind_path(path, kind)


def blind_path(path: str, kind: str) -> str:
    if kind == "juliet":
        return "src/main/java/app/Case" + Path(path).suffix
    if kind == "owasp":
        return re.sub(r"BenchmarkTest\d+", "Handler", path)
    if kind == "planted":
        return _PLANTED_PKG.sub("app", _PLANTED_CWE.sub("", re.sub(r"(?i)\bcwe25/", "", path)))
    return path


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def label(finding: dict, verdict: str, *, labeller: str, source: str, held_out: bool,
          reason: str, excerpt_sha256: str = "", **extra) -> dict:
    if verdict not in LABELS:
        raise ValueError(f"label must be one of {LABELS}, not {verdict!r}")
    rec = {"finding_id": finding.get("id", ""), "rule_id": finding["rule"], "label": verdict,
           "labeller": labeller, "source": source, "held_out": held_out, "reason": reason,
           "excerpt_sha256": excerpt_sha256}
    for k in ("repo", "path", "line", "language", "cwe", "severity"):
        if k in finding and k not in extra:
            rec[k] = finding[k]
    rec.update(extra)
    return rec


def census_population(records: list[dict], how: str = "census") -> list[dict]:
    """Label records that cover every finding of their population (a
    ground-truth benchmark labels each finding it can judge; the AI corpus
    and the CVE suspects are labelled whole), with the population fields of
    docs/precision.md ("Population weights"): each (source, rule, language)
    stratum's population is the records in it. Returns new records; the
    labels are untouched."""
    from collections import Counter
    size: Counter = Counter()
    held: Counter = Counter()
    rule_size: Counter = Counter()
    rule_held: Counter = Counter()
    for r in records:
        k = (r["source"], r["rule_id"], r.get("language") or "")
        size[k] += 1
        held[k] += bool(r["held_out"])
        rule_size[k[:2]] += 1
        rule_held[k[:2]] += bool(r["held_out"])
    out = []
    for r in records:
        k = (r["source"], r["rule_id"], r.get("language") or "")
        out.append({**r, "stratum_size": size[k], "stratum_held_out_size": held[k],
                    "rule_population": rule_size[k[:2]], "rule_held_out_population": rule_held[k[:2]],
                    "sampling": how})
    return out


def _open(path: Path, mode: str):
    import gzip
    if path.suffix == ".gz":
        # mtime=0 keeps the bytes identical for identical labels
        return gzip.open(path, mode + "t", encoding="utf-8") if "r" in mode else \
            gzip.GzipFile(path, mode + "b", mtime=0)
    return path.open(mode, encoding="utf-8")


def write_jsonl(path: Path, records: list[dict]) -> None:
    """One JSON object per line; `.jsonl.gz` is written gzipped (large
    mechanical label sets such as Juliet's)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
    with _open(path, "w") as fh:
        fh.write(text.encode("utf-8") if path.suffix == ".gz" else text)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with _open(path, "r") as fh:
        return [json.loads(ln) for ln in fh.read().splitlines() if ln.strip()]


def scan_tree(root: Path) -> list[dict]:
    """Arbiter's active findings under `root`, paths relative to it."""
    from arbiter.engine import run_scan
    from arbiter.policy import load_config
    report = run_scan([str(root)], load_config(None), use_adapters=False, use_cache=False)
    return [{"id": f.id, "rule": f.rule_id, "path": f.location.path, "line": f.location.start_line,
             "cwe": list(f.cwe), "severity": f.severity, "confidence": f.confidence,
             "language": language_of(f.location.path)}
            for f in report.active()]


def cwe_match(finding_cwes: list[str], truth_cwes: list[str], aliases: dict[str, list[str]]) -> bool:
    want = set(truth_cwes)
    for c in truth_cwes:
        want.update(aliases.get(c, []))
    return bool(set(finding_cwes) & want)
