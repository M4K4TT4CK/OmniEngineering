"""Two front ends for adjudication, over one sampler and one ledger.

## Why the interface is the whole problem

Calibration reads exactly one ledger: findings a person looked at and judged
right or wrong. Not the injection trials, which measure whether a rule works
mechanically against faults the tool generated for itself. Not the corpus
discrimination, which measures whether a rule separates broken code from
working code. Both of those are worth having and neither answers the question
calibration asks, which is whether the things a rule flags are things you
would act on.

That ledger sat at zero entries for the tool's entire life, in a tool built
around calibration. Not because nobody was willing, but because adjudicating
meant reading a JSON report, copying a fingerprint, and typing a command, once
per finding. Twenty of those is an afternoon of clerical work, so it never
happened.

So the interface is not a nicety here. It is the difference between the
calibration machinery working and not working.

## The HTML front end

One self-contained file. No server, no network, no build step: the findings are
serialized into the page, and the marks are held in memory and written out at
the end as the same markdown format `arbiter review --apply` already reads. It
works from a phone, on a plane, and on a laptop with the wifi off, which
matters because the reports worth adjudicating are often the ones you cannot
send anywhere.

Deliberately one finding at a time, with its code around it. Adjudicating from
a list encourages skimming, and a skimmed verdict is worse than no verdict —
this ledger is the only thing calibration reads, so a careless mark is not
noise, it is wrong data with nothing to average it out.

Browser storage is used for exactly one thing: surviving an accidental tab
close mid-review. It is wrapped in try/catch and the page works without it.

## The terminal front end

The same sampler, the same output, single keypress per finding, for when the
scan and the review happen in the same sitting.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import Finding
from .learn import MIN_OBSERVATIONS, Knowledge
from .review import _rule_gap, where

# How much code to show around a finding. Enough to judge, short enough that a
# phone screen holds it without scrolling past the point.
CONTEXT_BEFORE = 6
CONTEXT_AFTER = 6


def _context(repo_path: str, finding: Finding) -> tuple[list[tuple[int, str]], str]:
    """The lines around a finding, and a note when they could not be read."""
    if not finding.location.path:
        return [], "this finding is about the repository as a whole"
    p = Path(repo_path) / finding.location.path
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError as exc:
        return [], f"could not read the file ({type(exc).__name__})"
    line = finding.location.start_line or 1
    lo = max(0, line - 1 - CONTEXT_BEFORE)
    hi = min(len(lines), line + CONTEXT_AFTER)
    return [(i + 1, lines[i]) for i in range(lo, hi)], ""


def build_payload(findings: list[Finding], knowledge: Knowledge,
                  repo_paths: dict[str, str]) -> list[dict]:
    out = []
    for f in findings:
        ctx, note = _context(repo_paths.get(f.repo_id, repo_paths.get("root", ".")), f)
        stats = knowledge.rules.get(f.rule_id)
        out.append({
            "id": f.id,
            "rule": f.rule_id,
            "title": f.title,
            "severity": f.severity,
            "confidence": f.confidence,
            "dimension": f.dimension,
            "where": where(f),
            "line": f.location.start_line or 0,
            "evidence": f.evidence[:200],
            "description": f.description[:600],
            "remediation": f.remediation[:300],
            "context": ctx,
            "context_note": note,
            "reviewed": stats.observations if stats else 0,
            "gap": _rule_gap(knowledge, f.rule_id),
        })
    return out


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_CSS = """
:root{--bg:#fbfaf8;--fg:#22201d;--muted:#6b6660;--line:#e3ded7;--card:#fff;
--yes:#1f7a4d;--no:#a3341f;--skip:#6b6660;--accent:#2f5fa8;}
@media(prefers-color-scheme:dark){:root{--bg:#16150f;--fg:#e9e5dd;--muted:#98918a;
--line:#312e28;--card:#1e1c16;--yes:#4fb884;--no:#e08268;--skip:#98918a;--accent:#7fa6e0;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;}
.wrap{max-width:760px;margin:0 auto;padding:16px 16px 120px;}
header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
flex-wrap:wrap;padding-bottom:10px;border-bottom:1px solid var(--line);margin-bottom:18px;}
h1{font-size:17px;margin:0;font-weight:650;letter-spacing:-.01em}
.count{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin-bottom:20px}
.bar i{display:block;height:100%;background:var(--accent);transition:width .2s}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px;margin-bottom:16px}
.rule{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);
word-break:break-all}
h2{font-size:17px;margin:6px 0 10px;font-weight:620;line-height:1.35}
.meta{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.tag{font-size:11px;padding:2px 8px;border:1px solid var(--line);border-radius:99px;
color:var(--muted);white-space:nowrap}
.sev-critical,.sev-high{color:var(--no);border-color:currentColor}
p.desc{margin:0 0 12px;color:var(--fg)}
p.fix{margin:0 0 12px;color:var(--muted);font-size:14px}
pre{margin:0 0 12px;padding:12px;background:var(--bg);border:1px solid var(--line);
border-radius:8px;overflow-x:auto;font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
pre .ln{color:var(--muted);user-select:none;display:inline-block;
min-width:3.5em;text-align:right;padding-right:1em}
pre .hit{background:color-mix(in srgb,var(--accent) 16%,transparent);
display:block;margin:0 -12px;padding:0 12px}
.note{color:var(--muted);font-size:13px;font-style:italic;margin-bottom:12px}
.actions{position:fixed;left:0;right:0;bottom:0;background:var(--card);
border-top:1px solid var(--line);padding:12px 16px calc(12px + env(safe-area-inset-bottom));}
.actions .inner{max-width:760px;margin:0 auto;display:flex;gap:10px}
button{flex:1;padding:14px 8px;font-size:15px;font-weight:600;border-radius:9px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);cursor:pointer;
font-family:inherit;min-height:48px}
button:hover{border-color:var(--muted)}
button.yes{color:var(--yes)} button.no{color:var(--no)} button.skip{color:var(--skip)}
button kbd{font:inherit;opacity:.5;font-weight:400}
.done{text-align:center;padding:40px 0}
.done h2{font-size:19px}
textarea{width:100%;height:240px;font:12px/1.5 ui-monospace,Menlo,monospace;
padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--card);
color:var(--fg)}
.hint{color:var(--muted);font-size:13px;margin:14px 0}
.tally{display:flex;gap:16px;justify-content:center;margin:16px 0;font-size:14px}
.tally b{font-variant-numeric:tabular-nums}
"""

_JS = r"""
const F = window.__FINDINGS__, CMD = window.__APPLY_CMD__;
let i = 0; const marks = {};
try { const s = localStorage.getItem('arbiter-review');
      if (s) Object.assign(marks, JSON.parse(s)); } catch (e) {}
const save = () => { try { localStorage.setItem('arbiter-review', JSON.stringify(marks)); }
                     catch (e) {} };
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function firstUnmarked() {
  for (let k = 0; k < F.length; k++) if (!(F[k].id in marks)) return k;
  return F.length;
}

function render() {
  const app = document.getElementById('app'), act = document.getElementById('act');
  const n = Object.keys(marks).length;
  document.getElementById('count').textContent = `${n} of ${F.length} marked`;
  document.getElementById('fill').style.width = (100 * n / F.length) + '%';

  if (i >= F.length) {
    act.hidden = true;
    const yes = Object.values(marks).filter(v => v === 'y').length;
    const no = Object.values(marks).filter(v => v === 'n').length;
    const skipped = Object.values(marks).filter(v => v === 's').length;
    const body = F.map(f => `[${marks[f.id] === 'y' ? 'y' : marks[f.id] === 'n' ? 'n' : ' '}] ${f.id}  ${f.title}`).join('\n');
    app.innerHTML = `<div class="done"><h2>Done</h2>
      <div class="tally"><span><b>${yes}</b> real</span><span><b>${no}</b> not real</span>
      <span><b>${skipped}</b> skipped</span></div>
      <p class="hint">Save this as <code>review.md</code>, then run:<br>
      <code>${esc(CMD)}</code></p>
      <textarea id="out" readonly>${esc(body)}</textarea>
      <p class="hint"><button id="copy" style="max-width:220px">Copy to clipboard</button></p>
      <p class="hint"><a href="#" id="again">Review the skipped ones</a></p></div>`;
    document.getElementById('copy').onclick = () => {
      const t = document.getElementById('out'); t.select();
      try { document.execCommand('copy'); } catch (e) {}
      document.getElementById('copy').textContent = 'Copied';
    };
    document.getElementById('again').onclick = ev => {
      ev.preventDefault();
      for (const k of Object.keys(marks)) if (marks[k] === 's') delete marks[k];
      save(); i = firstUnmarked(); render();
    };
    return;
  }

  act.hidden = false;
  const f = F[i];
  const ctx = (f.context || []).map(([ln, text]) =>
    `<span class="${ln === f.line ? 'hit' : ''}"><span class="ln">${ln}</span>${esc(text)}</span>`
  ).join('\n');
  const gap = f.gap > 0
    ? `${f.reviewed} reviewed, ${f.gap} more before this rule counts as proven`
    : `${f.reviewed} reviewed — already proven`;
  app.innerHTML = `<div class="card">
    <div class="rule">${esc(f.rule)}</div>
    <h2>${esc(f.title)}</h2>
    <div class="meta">
      <span class="tag sev-${esc(f.severity)}">${esc(f.severity)}</span>
      <span class="tag">${esc(f.confidence)} confidence</span>
      <span class="tag">${esc(f.dimension)}</span>
      <span class="tag">${esc(f.where)}</span>
      <span class="tag">${esc(gap)}</span>
    </div>
    <p class="desc">${esc(f.description)}</p>
    ${ctx ? `<pre>${ctx}</pre>` : ''}
    ${f.context_note ? `<p class="note">${esc(f.context_note)}</p>` : ''}
    ${f.remediation ? `<p class="fix">Suggested fix: ${esc(f.remediation)}</p>` : ''}
  </div>`;
  window.scrollTo(0, 0);
}

function mark(v) { marks[F[i].id] = v; save(); i++; render(); }
function back() { if (i > 0) { i--; delete marks[F[i].id]; save(); render(); } }

document.addEventListener('keydown', e => {
  if (i >= F.length) return;
  const k = e.key.toLowerCase();
  if (k === 'y' || k === '1') mark('y');
  else if (k === 'n' || k === '2') mark('n');
  else if (k === 's' || k === ' ' || k === 'arrowright') { e.preventDefault(); mark('s'); }
  else if (k === 'arrowleft' || k === 'backspace') { e.preventDefault(); back(); }
});

window.addEventListener('DOMContentLoaded', () => {
  document.getElementById('y').onclick = () => mark('y');
  document.getElementById('n').onclick = () => mark('n');
  document.getElementById('s').onclick = () => mark('s');
  i = firstUnmarked();
  render();
});
"""


def render_html(findings: list[Finding], knowledge: Knowledge,
                repo_paths: dict[str, str], apply_cmd: str) -> str:
    payload = build_payload(findings, knowledge, repo_paths)
    rules = len({f["rule"] for f in payload})
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arbiter — review {len(payload)} findings</title>
<style>{_CSS}</style></head><body>
<div class="wrap">
  <header>
    <h1>Is this a real problem?</h1>
    <span class="count" id="count"></span>
  </header>
  <div class="bar"><i id="fill"></i></div>
  <div id="app"></div>
  <p class="hint">{len(payload)} findings across {rules} rules, chosen to move the
  most rules past {MIN_OBSERVATIONS} adjudications. A skipped finding costs nothing;
  a careless mark is worse than none, because this ledger is the only thing
  calibration reads.</p>
</div>
<div class="actions" id="act"><div class="inner">
  <button class="yes" id="y">Real <kbd>y</kbd></button>
  <button class="no" id="n">Not real <kbd>n</kbd></button>
  <button class="skip" id="s">Skip <kbd>space</kbd></button>
</div></div>
<script>
window.__FINDINGS__ = {json.dumps(payload)};
window.__APPLY_CMD__ = {json.dumps(apply_cmd)};
</script>
<script>{_JS}</script>
</body></html>
"""


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

def _getch() -> str:
    """One keypress, without waiting for enter. Falls back to a line read."""
    try:
        import sys as _sys
        import termios
        import tty
        fd = _sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return _sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:  # noqa: BLE001 - no tty, no termios, or a redirected stdin: fall back to a line read
        return (input().strip()[:1] or "s")


_DIM, _B, _R = "\033[2m", "\033[1m", "\033[0m"
_GREEN, _RED, _CYAN = "\033[32m", "\033[31m", "\033[36m"


def run_terminal(findings: list[Finding], knowledge: Knowledge,
                 repo_paths: dict[str, str]) -> dict[str, str]:
    """Walk the findings, one keypress each. Returns id -> verdict."""
    import sys as _sys
    payload = build_payload(findings, knowledge, repo_paths)
    marks: dict[str, str] = {}
    i = 0
    while i < len(payload):
        f = payload[i]
        print("\033[2J\033[H", end="")
        print(f"  {_DIM}{i + 1} of {len(payload)}   {f['rule']}{_R}")
        print(f"  {_B}{f['title']}{_R}")
        gap = (f"{f['reviewed']} reviewed, {f['gap']} more to prove this rule"
               if f["gap"] else f"{f['reviewed']} reviewed — proven")
        print(f"  {_DIM}{f['severity']}/{f['confidence']} · {f['where']} · {gap}{_R}\n")
        for line in (f["description"] or "").split(". "):
            if line.strip():
                print(f"  {line.strip().rstrip('.')}.")
        print()
        for ln, text in f["context"]:
            marker = f"{_CYAN}>{_R}" if ln == f["line"] else " "
            print(f"  {marker} {_DIM}{ln:>5}{_R}  {text[:100]}")
        if f["context_note"]:
            print(f"  {_DIM}({f['context_note']}){_R}")
        print(f"\n  {_GREEN}y{_R} real   {_RED}n{_R} not real   "
              f"space skip   b back   q stop\n")
        _sys.stdout.flush()
        key = _getch().lower()
        if key in ("q", "\x03", "\x04"):
            break
        if key == "b":
            if i:
                i -= 1
                marks.pop(payload[i]["id"], None)
            continue
        if key in ("y", "1"):
            marks[f["id"]] = "true_positive"
        elif key in ("n", "2"):
            marks[f["id"]] = "false_positive"
        i += 1
    print("\033[2J\033[H", end="")
    return marks
