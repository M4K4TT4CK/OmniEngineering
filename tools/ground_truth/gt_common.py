"""Shared pieces of the ground-truth and model-labelling tools (ARB-067).

* `excerpt()`: what a labeller is shown -- the enclosing function plus the
  file's imports, bounded, line-numbered, the flagged line marked, secrets
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
MAX_FUNCTION_LINES = 80
MAX_IMPORT_LINES = 25
MAX_HELPERS = 3          # same-file functions the enclosing one calls
MAX_HELPER_LINES = 40    # each
MAX_EXCERPT_LINES = 180  # everything shown, imports included
WINDOW = 30  # lines each side when no enclosing function is found

_IMPORT = {
    "python": re.compile(r"^\s*(import\s|from\s+\S+\s+import\s)"),
    "typescript": re.compile(r"^\s*(import\s|export\s.*\sfrom\s|(const|let|var)\s.*=\s*require\()"),
    "javascript": re.compile(r"^\s*(import\s|export\s.*\sfrom\s|(const|let|var)\s.*=\s*require\()"),
    "go": re.compile(r'^\s*(import\b|"[\w./-]+"\s*$|\w+\s+"[\w./-]+"\s*$)'),
    "java": re.compile(r"^\s*import\s"),
    "ruby": re.compile(r"^\s*(require|require_relative|load)\b"),
}
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
    """1-based inclusive line span of the innermost named function around `line`."""
    from arbiter.lang_rules import FUNCTION_TYPES
    tree = _tree(text, path, language)
    if tree is None:
        return None
    types = FUNCTION_TYPES.get("typescript" if language == "javascript" else language, set())
    named = types - {"lambda", "lambda_expression", "arrow_function", "func_literal"}
    row = max(0, line - 1)
    lines = text.splitlines()
    col = len(lines[row]) - len(lines[row].lstrip()) if row < len(lines) else 0
    node = tree.root_node.descendant_for_point_range((row, col), (row, col))
    best = None
    while node is not None:
        if node.type in named:
            best = node
            break
        if node.type in types and best is None:
            best = node  # an anonymous function, kept unless a named one encloses it
        node = node.parent
    if best is None:
        return None
    return best.start_point[0] + 1, best.end_point[0] + 1


def _named_functions(text: str, path: str, language: str) -> dict[str, tuple[int, int]]:
    """Name -> 1-based line span of every named function in the file (first wins)."""
    from arbiter.lang_rules import FUNCTION_TYPES
    tree = _tree(text, path, language)
    if tree is None:
        return {}
    types = FUNCTION_TYPES.get("typescript" if language == "javascript" else language, set())
    out: dict[str, tuple[int, int]] = {}
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in types:
            name = n.child_by_field_name("name")
            if name is not None:
                key = text.encode("utf-8")[name.start_byte:name.end_byte].decode("utf-8", "replace")
                out.setdefault(key, (n.start_point[0] + 1, n.end_point[0] + 1))
        stack.extend(reversed(n.children))
    return out


def _helpers(text: str, path: str, language: str, span: tuple[int, int]) -> list[int]:
    """Lines of up to MAX_HELPERS same-file functions called from `span`, so
    a labeller sees the source or sanitiser a sink's data went through."""
    funcs = _named_functions(text, path, language)
    if not funcs:
        return []
    lines = text.splitlines()
    body = "\n".join(lines[span[0] - 1: span[1]])
    called = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", body):
        name = m.group(1)
        if name in funcs and name not in called and not (funcs[name][0] <= span[0] <= funcs[name][1]):
            called.append(name)
    out: list[int] = []
    for name in called[:MAX_HELPERS]:
        lo, hi = funcs[name]
        out += list(range(lo, min(hi, lo + MAX_HELPER_LINES - 1) + 1))
    return out


def _imports(lines: list[str], language: str, stop: int) -> list[int]:
    pat = _IMPORT.get(language)
    if pat is None:
        return []
    out = []
    in_go_block = False
    for i, ln in enumerate(lines[: min(stop, 300)], 1):
        if language == "go":
            if re.match(r"^\s*import\s*\($", ln):
                in_go_block = True
                out.append(i)
                continue
            if in_go_block:
                out.append(i)
                if ln.strip() == ")":
                    in_go_block = False
                continue
            if re.match(r"^\s*import\s", ln):
                out.append(i)
            continue
        if pat.match(ln):
            out.append(i)
    return out[:MAX_IMPORT_LINES]


def excerpt(text: str, path: str, line: int, language: str | None = None) -> str:
    """Imports, then the enclosing function (or a window), numbered, `>>` on `line`."""
    language = language or language_of(path)
    lines = text.splitlines()
    if not lines:
        return ""
    line = min(max(1, line), len(lines))
    span = _function_span(text, path, language, line)
    if span is None or not span[0] <= line <= span[1]:
        keep = list(range(max(1, line - WINDOW), min(len(lines), line + WINDOW) + 1))
    else:
        lo, hi = span[0], min(span[1], len(lines))
        if hi - lo + 1 > MAX_FUNCTION_LINES:
            # Keep the signature and a window around the flagged line.
            half = (MAX_FUNCTION_LINES - 4) // 2
            body_lo, body_hi = max(lo, line - half), min(hi, line + half)
            keep = list(range(lo, min(lo + 3, body_lo))) + list(range(body_lo, body_hi + 1))
        else:
            keep = list(range(lo, hi + 1))
    helpers = _helpers(text, path, language, span) if span and span[0] <= line <= span[1] else []
    imports = [i for i in _imports(lines, language, keep[0]) if i < keep[0]]
    room = MAX_EXCERPT_LINES - len(keep) - len(imports)
    shown = sorted(set(imports) | set(keep) | set(helpers[: max(0, room)]))
    out, prev = [], None
    for i in shown:
        if prev is not None and i != prev + 1:
            out.append("      ...")
        mark = ">>" if i == line else "  "
        out.append(f"{mark}{i:>5} {lines[i - 1][:240]}".rstrip())
        prev = i
    return mask("\n".join(out))


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
