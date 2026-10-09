"""Run history and the trend dashboard.

Every report is a complete record of one run. What it cannot show is the
direction: whether coverage has been sliding for a month, whether the high
count that failed today's gate was two yesterday or twenty. So each scan and
gate appends one line to `<out>/history.jsonl`, and `arbiter dashboard`
renders those lines as a page.

A history line is a summary and nothing more: the time, the commit, the grade,
the coverage, the counts by severity and the gate result. It carries no
evidence, no file paths and no finding titles, so the file can sit in a shared
build artifact or a chat message without leaking what the scan found. The
detail stays in the report that produced it.

The page is self-contained -- inline CSS and inline SVG, no script, no font,
no link to anywhere -- for the same reason the HTML report is: it has to open
on a machine with no network and say exactly what the file says.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .core import SEV_RANK, Report
from .report import _HTML_CSS, SEV_ORDER, counts_by_severity

HISTORY_FILE = "history.jsonl"
CHART_WINDOW = 60  # runs drawn in the charts; the table lists every run

# The dashboard shares the report's tokens and adds its own series colours:
# two categorical slots for the two lines, and one red ramp for the severity
# stack, which is an ordered scale rather than five unrelated categories.
# Each set was validated for both surfaces before being written down here.
_DASHBOARD_CSS = _HTML_CSS + """
:root{--s1:#2a78d6;--s2:#eb6834;--sev1:#7a1c1c;--sev2:#a52e2e;--sev3:#c84848;--sev4:#dd6f6f;--sev5:#e99c9c}
@media(prefers-color-scheme:dark){:root{--s1:#3987e5;--s2:#d95926;--sev1:#f3a9a9;--sev2:#ea7f7f;
--sev3:#d95a5a;--sev4:#b94040;--sev5:#8f2d2d}}
.chart{background:var(--sf);border:1px solid var(--rule);border-radius:5px;padding:12px 12px 8px}
svg{display:block;width:100%;height:auto}
.grid{stroke:var(--rule);stroke-width:1}
.axis{fill:var(--mut);font-size:11px;font-variant-numeric:tabular-nums}
.lbl{fill:var(--ink2);font-size:12px}
.line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.dot{stroke:var(--sf);stroke-width:2}
.seg:hover,.dot:hover{opacity:.7}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--ink2);margin:8px 0 2px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.sw.k{width:14px;height:3px;vertical-align:3px}
.ok{color:var(--low)}.bad{color:var(--crit)}
"""

_SEV_VAR = {s: f"var(--sev{i + 1})" for i, s in enumerate(SEV_ORDER)}
_DASH = "\u2014"


# ---------------------------------------------------------------------------
# Writing and reading
# ---------------------------------------------------------------------------

def row_from(report: Report) -> dict:
    """The one line a run leaves behind. Summary fields only, by design."""
    sc = report.scorecard
    active = report.active()
    withheld = sc.withheld or sc.overall is None
    high = SEV_RANK["high"]
    gate = report.gate or {}
    return {
        "time": report.started_at,
        "commit": report.repos[0].commit if report.repos else "",
        "system": report.system,
        "profile": report.profile,
        "mode": (report.scan_scope or {}).get("mode", "full"),
        # `grade` is how the report presents the result: the rounded overall,
        # or "withheld" when coverage fell below the threshold. `score` is the
        # number behind it, and null whenever the grade is withheld, so a chart
        # cannot draw a point the report refused to state.
        "grade": "withheld" if withheld else str(round(sc.overall)),
        "score": None if withheld else round(float(sc.overall), 1),
        "coverage": round(float(sc.coverage), 3),
        "counts": counts_by_severity(active),
        "new_high_or_above": sum(
            1 for f in active
            if f.status == "new" and SEV_RANK.get(f.severity, 99) <= high),
        "gate_passed": bool(gate.get("passed")) if gate else None,
        "duration_s": round(float(report.duration_s), 3),
        "findings_total": len(active),
    }


def append(report: Report, out_dir: str) -> Path:
    """Append one line for `report` to `<out_dir>/history.jsonl`."""
    path = Path(out_dir) / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row_from(report), sort_keys=True) + "\n")
    return path


def load(path: Path) -> list[dict]:
    """Read a history file. A missing file is an empty history; a corrupt
    line is skipped rather than taking the rest of the file down with it."""
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "time" in row:
                rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _when(row: dict) -> str:
    return str(row.get("time") or "")[:16].replace("T", " ")


def _counts(row: dict) -> dict[str, int]:
    raw = row.get("counts") or {}
    return {s: int(raw.get(s) or 0) for s in SEV_ORDER}


def _score(row: dict) -> float | None:
    s = row.get("score")
    return float(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else None


def _coverage_pct(row: dict) -> float:
    c = row.get("coverage")
    return float(c) * 100 if isinstance(c, (int, float)) and not isinstance(c, bool) else 0.0


def _gate_text(row: dict) -> tuple[str, str]:
    """(label, css class) for a gate result, with a glyph so it never rests
    on colour alone."""
    g = row.get("gate_passed")
    if g is True:
        return "✓ passed", "ok"
    if g is False:
        return "✗ failed", "bad"
    return "not evaluated", "muted"


def _nice_step(top: float) -> int:
    """A tick spacing that gives three to six clean ticks up to `top`."""
    for step in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000):
        if top / step <= 6:
            return step
    return 10000 * max(1, int(top // 60000) + 1)


def _tiles(latest: dict) -> str:
    e = html.escape
    grade = latest.get("grade", "")
    grade_txt = "withheld" if grade in ("withheld", "", None) else f"{e(str(grade))}/100"
    gate_txt, gate_cls = _gate_text(latest)
    return (
        "<div class='cards'>"
        f"<div class='card'><div class='n'>{grade_txt}</div><div class='l'>latest grade</div></div>"
        f"<div class='card'><div class='n'>{_coverage_pct(latest):.0f}%</div><div class='l'>coverage</div></div>"
        f"<div class='card'><div class='n'>{int(latest.get('new_high_or_above') or 0)}</div>"
        "<div class='l'>new high or above</div></div>"
        f"<div class='card'><div class='n {gate_cls}'>{gate_txt}</div><div class='l'>gate</div></div>"
        "</div>"
    )


def _x_positions(n: int, left: float, right: float) -> list[float]:
    if n == 1:
        return [(left + right) / 2]
    span = (right - left) / (n - 1)
    return [left + i * span for i in range(n)]


def _x_labels(rows: list[dict], xs: list[float], y: float) -> str:
    """At most six time labels, evenly spread, so they never overlap. The
    last run is always labelled; when it would land on top of the previous
    tick, it takes that tick's place rather than crowding it."""
    n = len(rows)
    step = max(1, -(-(n - 1) // 5)) if n > 1 else 1
    picked = list(range(0, n, step))
    if picked[-1] != n - 1:
        if (n - 1) - picked[-1] >= step / 2:
            picked.append(n - 1)
        else:
            picked[-1] = n - 1
    return "".join(
        f"<text class='axis' x='{xs[i]:.1f}' y='{y:.0f}' text-anchor='middle'>{html.escape(_when(rows[i]))}</text>"
        for i in picked
    )


def _line_chart(rows: list[dict]) -> str:
    """Score and coverage over time. Both are 0-100, so they share one axis;
    a withheld grade leaves a gap in the score line rather than a point."""
    e = html.escape
    W, H, L, R, T, B = 960, 300, 44, 110, 16, 36
    xs = _x_positions(len(rows), L, W - R)

    def y(v: float) -> float:
        return T + (100 - v) * (H - T - B) / 100

    grid = "".join(
        f"<line class='grid' x1='{L}' x2='{W - R}' y1='{y(v):.1f}' y2='{y(v):.1f}'/>"
        f"<text class='axis' x='{L - 8}' y='{y(v) + 4:.1f}' text-anchor='end'>{v}</text>"
        for v in (0, 25, 50, 75, 100)
    )

    def series(values: list[float | None], var: str, name: str, fmt: str) -> str:
        out: list[str] = []
        run: list[str] = []
        for i, v in enumerate(values):
            if v is None:
                if len(run) > 1:
                    out.append(f"<polyline class='line' stroke='{var}' points='{' '.join(run)}'/>")
                run = []
                continue
            run.append(f"{xs[i]:.1f},{y(v):.1f}")
        if len(run) > 1:
            out.append(f"<polyline class='line' stroke='{var}' points='{' '.join(run)}'/>")
        for i, v in enumerate(values):
            if v is None:
                continue
            tip = f"{_when(rows[i])} · {name} {fmt % v}"
            out.append(f"<circle class='dot' fill='{var}' cx='{xs[i]:.1f}' cy='{y(v):.1f}' r='4'>"
                       f"<title>{e(tip)}</title></circle>")
        return "".join(out)

    scores = [_score(r) for r in rows]
    covers = [_coverage_pct(r) for r in rows]
    body = series(scores, "var(--s1)", "score", "%.1f") + series(covers, "var(--s2)", "coverage", "%.0f%%")

    # Direct end labels, unless the two lines end close enough to collide; the
    # legend below carries identity either way.
    ends: list[str] = []
    last_s, last_c = scores[-1], covers[-1]
    if last_s is None or abs(y(last_s) - y(last_c)) >= 14:
        x = xs[-1] + 10
        if last_s is not None:
            ends.append(f"<text class='lbl' x='{x:.1f}' y='{y(last_s) + 4:.1f}'>score {last_s:.0f}</text>")
        ends.append(f"<text class='lbl' x='{x:.1f}' y='{y(last_c) + 4:.1f}'>coverage {last_c:.0f}%</text>")

    return (
        f"<div class='chart'><svg viewBox='0 0 {W} {H}' role='img' aria-label='score and coverage per run'>"
        f"{grid}{_x_labels(rows, xs, H - 10)}{body}{''.join(ends)}</svg>"
        "<div class='legend'><span><i class='sw k' style='background:var(--s1)'></i>score (0–100)</span>"
        "<span><i class='sw k' style='background:var(--s2)'></i>coverage (%)</span></div></div>"
    )


def _bar_chart(rows: list[dict]) -> str:
    """Findings per run, stacked by severity, a 2px surface gap between
    segments and a rounded cap on the top one."""
    e = html.escape
    W, H, L, R, T, B, GAP = 960, 240, 44, 16, 16, 36, 2
    n = len(rows)
    counts = [_counts(r) for r in rows]
    totals = [sum(c.values()) for c in counts]
    top = max(totals) if totals else 0
    step = _nice_step(max(top, 1))
    ymax = max(step, ((top + step - 1) // step) * step)
    plot_h = H - T - B
    band = (W - L - R) / n
    bw = min(24.0, band * 0.7)
    xs = _x_positions(n, L + band / 2, W - R - band / 2)

    def y(v: float) -> float:
        return T + plot_h - v * plot_h / ymax

    grid = "".join(
        f"<line class='grid' x1='{L}' x2='{W - R}' y1='{y(v):.1f}' y2='{y(v):.1f}'/>"
        f"<text class='axis' x='{L - 8}' y='{y(v) + 4:.1f}' text-anchor='end'>{v:,}</text>"
        for v in range(0, ymax + 1, step)
    )
    bars: list[str] = []
    for i, c in enumerate(counts):
        base = 0
        x0 = xs[i] - bw / 2
        nonzero = [s for s in SEV_ORDER if c[s] > 0]
        for s in SEV_ORDER:
            if c[s] == 0:
                continue
            y_top, y_bot = y(base + c[s]), y(base)
            h = max(0.0, y_bot - y_top - (GAP if base > 0 else 0))
            y_draw = y_bot - h
            tip = e(f"{_when(rows[i])} · {s} {c[s]}")
            if s == nonzero[-1]:
                # Critical sits on the baseline so the severities that gate a
                # build read from a common base; the last non-zero severity is
                # the top of the stack and gets the 4px rounded data-end.
                r = min(4.0, h / 2, bw / 2)
                d = (f"M{x0:.1f},{y_draw + h:.1f} V{y_draw + r:.1f} Q{x0:.1f},{y_draw:.1f} {x0 + r:.1f},{y_draw:.1f} "
                     f"H{x0 + bw - r:.1f} Q{x0 + bw:.1f},{y_draw:.1f} {x0 + bw:.1f},{y_draw + r:.1f} "
                     f"V{y_draw + h:.1f} Z")
                bars.append(f"<path class='seg' fill='{_SEV_VAR[s]}' d='{d}'><title>{tip}</title></path>")
            else:
                bars.append(f"<rect class='seg' fill='{_SEV_VAR[s]}' x='{x0:.1f}' y='{y_draw:.1f}' "
                            f"width='{bw:.1f}' height='{h:.1f}'><title>{tip}</title></rect>")
            base += c[s]
    note = "" if top else "<text class='lbl' x='50%' y='50%' text-anchor='middle'>no findings in any run</text>"
    legend = "".join(
        f"<span><i class='sw' style='background:{_SEV_VAR[s]}'></i>{s}</span>" for s in SEV_ORDER
    )
    return (
        f"<div class='chart'><svg viewBox='0 0 {W} {H}' role='img' aria-label='findings per run by severity'>"
        f"{grid}{_x_labels(rows, xs, H - 10)}{''.join(bars)}{note}</svg>"
        f"<div class='legend'>{legend}</div></div>"
    )


def _table(rows: list[dict]) -> str:
    e = html.escape
    body: list[str] = []
    for r in reversed(rows):
        c = _counts(r)
        gate_txt, gate_cls = _gate_text(r)
        grade = r.get("grade", "")
        body.append(
            f"<tr><td class='mono'>{e(_when(r))}</td><td class='mono'>{e(str(r.get('commit') or '—'))}</td>"
            f"<td>{e(str(r.get('mode') or ''))}</td><td>{e(str(r.get('profile') or ''))}</td>"
            f"<td>{e(str(grade) if grade not in ('', None) else '—')}</td><td>{_coverage_pct(r):.0f}%</td>"
            + "".join(f"<td>{c[s]}</td>" for s in SEV_ORDER)
            + f"<td class='{gate_cls}'>{gate_txt}</td></tr>"
        )
    head = "".join(f"<th>{s[:4]}</th>" for s in SEV_ORDER)
    return (
        "<div class='tw'><table><thead><tr><th>Time</th><th>Commit</th><th>Mode</th><th>Profile</th>"
        f"<th>Grade</th><th>Coverage</th>{head}<th>Gate</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div>"
    )


def render_dashboard(rows: list[dict], title: str) -> str:
    """A self-contained page: tiles, two charts and the run table, newest
    first. With no rows it says so instead of drawing an empty axis."""
    e = html.escape
    head = (f"<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{e(title)}</title><style>{_DASHBOARD_CSS}</style></head><body><div class=\"wrap\">"
            f"<h1>{e(title)}</h1>")
    foot = "</div></body></html>"
    if not rows:
        return (head + "<p class='sub'>run history</p>"
                "<div class='banner'><b>No runs recorded yet.</b> Every <code>arbiter scan</code> and "
                "<code>arbiter gate</code> appends one line to <code>history.jsonl</code> in its output "
                "directory. Run one, then render this page again.</div>" + foot)

    latest = rows[-1]
    window = rows[-CHART_WINDOW:]
    first, last = _when(rows[0]), _when(latest)
    scope = (f" · charts show the last {len(window)} of {len(rows)} runs"
             if len(rows) > len(window) else "")
    sub = (f"{len(rows)} run(s) · {e(first)} to {e(last)} · system "
           f"<code>{e(str(latest.get('system') or ''))}</code>{scope}")
    return (
        head + f"<p class='sub'>{sub}</p>"
        + _tiles(latest)
        + "<h2>Score and coverage</h2>" + _line_chart(window)
        + "<h2>Findings by severity</h2>" + _bar_chart(window)
        + "<h2>Runs</h2>" + _table(rows)
        + "<p class='sub' style='margin-top:28px'>A withheld grade is a gap in the score line, not a low "
          "point. Each line of history is a summary; the report that produced it holds the evidence.</p>"
        + foot
    )
