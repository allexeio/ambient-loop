"""Self-contained HTML reports in the Milieu house style.

There are no external scripts. Charts are inline SVG, drawn from theme tokens so they read in
light and dark. The only external resource is the Raleway webfont from Google Fonts, which
falls back to Helvetica/Arial. Every page states the run's boundary and lists the assumptions
that apply.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Series colours: Milieu accents (dark-blue, eco-green, sky-blue, dark-green) plus greys.
SERIES = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)"]

CSS = """
:root{--surface:#fff;--surface-alt:#f4f4f4;--text:#231f20;--muted:#4f4c4d;--rule:#d3d2d2;
--bar:#231f20;--accent:#009ab9;--c1:#009ab9;--c2:#aab957;--c3:#3ac4e5;--c4:#70a351;--c5:#918f90;
--c6:#231f20;--good:#e2eddc;--warn:#fbe9c6;--bad:#f6d5d1;--on:#231f20;
--sans:Raleway,"Helvetica Neue",Arial,sans-serif;--mono:ui-monospace,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--surface:#231f20;
--surface-alt:#4f4c4d;--text:#fff;--muted:#d3d2d2;--rule:#4f4c4d;--bar:#4f4c4d;--accent:#3ac4e5;
--c6:#d3d2d2;--good:#8db574;--warn:#e8c46a;--bad:#e59a8f;color-scheme:dark}}
:root[data-theme=dark]{--surface:#231f20;--surface-alt:#4f4c4d;--text:#fff;--muted:#d3d2d2;
--rule:#4f4c4d;--bar:#4f4c4d;--accent:#3ac4e5;--c6:#d3d2d2;--good:#8db574;--warn:#e8c46a;
--bad:#e59a8f;color-scheme:dark}
*{box-sizing:border-box}body{background:var(--surface);color:var(--text);font-family:var(--sans);
font-size:15px;line-height:24px;margin:0}
.page{max-width:1080px;margin:0 auto;padding-block:40px 0;
padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
.label{font-size:12px;line-height:16px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
h1{font-size:34px;line-height:42px;font-weight:700;margin:8px 0 12px;text-wrap:balance}
h2{font-size:24px;line-height:30px;font-weight:700;margin:0;padding-bottom:10px;border-bottom:1px solid var(--rule)}
h2 .n{font-weight:300;color:var(--accent);margin-right:10px}
h3{font-size:17px;line-height:24px;font-weight:600;margin:0}
p{margin:0;max-width:75ch}section{padding-top:40px;display:grid;gap:16px}
.boundary{background:var(--surface-alt);padding:12px 16px;font-size:13px;line-height:20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));border-top:1px solid var(--rule);border-bottom:1px solid var(--rule)}
.kpi{padding:16px 16px 16px 0;display:grid;gap:2px}.kpi .v{font-size:34px;line-height:1.1;font-weight:300;font-variant-numeric:lining-nums tabular-nums}
.kpi .d{font-size:12.5px;line-height:18px;color:var(--muted)}
.tw{overflow-x:auto;min-width:0}table{border-collapse:collapse;width:100%;font-size:13.5px;line-height:20px}
th,td{text-align:left;padding:7px 10px 7px 0;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:11.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:lining-nums tabular-nums;white-space:nowrap;padding-left:10px}
.chip{display:inline-block;font-size:11px;line-height:16px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 7px;color:var(--on)}
.good{background:var(--good)}.warn{background:var(--warn)}.bad{background:var(--bad)}.info{box-shadow:inset 0 0 0 1px var(--text);color:var(--text)}
.chart{overflow-x:auto}.chart svg{display:block;max-width:100%;height:auto}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:12.5px;color:var(--muted)}
.legend i{display:inline-block;width:12px;height:12px;margin-right:6px;vertical-align:-1px}
.note{font-size:12.5px;line-height:19px;color:var(--muted)}
.grid2{display:grid;gap:24px}@media(min-width:820px){.grid2{grid-template-columns:1fr 1fr}}
.grid2>div{min-width:0;display:grid;gap:10px;align-content:start}
.finding{display:grid;grid-template-columns:88px 1fr;gap:12px;padding:10px 0;border-bottom:1px solid var(--rule)}
.finding div{min-width:0}code{font-family:var(--mono);font-size:12.5px}
footer{margin-top:56px;border-top:10px solid var(--bar);padding-block:10px 28px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:12px;font-size:12.5px;color:var(--muted)}
"""

FONT = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Raleway:wght@300;'
        '400;600;700&display=swap">')


def esc(x: object) -> str:
    return html.escape(str(x))


def fmt(v: float, nd: int = 1) -> str:
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "–"
    return f"{v:,.{nd}f}"


@dataclass
class Report:
    title: str
    eyebrow: str
    lead: str = ""
    boundary: str = ""
    meta: list[str] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    _n: int = 0

    def section(self, heading: str, *blocks: str) -> None:
        self._n += 1
        self.parts.append(
            f'<section><h2><span class="n">{self._n}.0</span>{esc(heading)}</h2>{"".join(blocks)}</section>'
        )

    def render(self) -> str:
        meta = " · ".join(esc(m) for m in self.meta)
        bnd = f'<div class="boundary"><b>Boundary:</b> {esc(self.boundary)}</div>' if self.boundary else ""
        return (
            f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f"<title>{esc(self.title)}</title>{FONT}<style>{CSS}</style></head><body>"
            f'<div class="page"><header><div class="label">{esc(self.eyebrow)}</div>'
            f"<h1>{esc(self.title)}</h1><p>{esc(self.lead)}</p>"
            f'<p class="note" style="margin-top:8px">{meta}</p></header>{bnd}'
            f'{"".join(self.parts)}<footer><span>{esc(self.eyebrow)}</span>'
            f"<span>Milieu Consult Limited</span></footer></div></body></html>"
        )


# ---- blocks -------------------------------------------------------------------------------
def p(text: str, cls: str = "") -> str:
    c = f' class="{cls}"' if cls else ""
    return f"<p{c}>{esc(text)}</p>"


def kpis(items: Iterable[tuple[str, str]]) -> str:
    return '<div class="kpis">' + "".join(
        f'<div class="kpi"><span class="v">{esc(v)}</span><span class="d">{esc(d)}</span></div>'
        for v, d in items
    ) + "</div>"


def table(head: Sequence[str], rows: Iterable[Sequence[object]], numeric: Sequence[int] = (),
          raw: bool = False) -> str:
    def cell(i: int, v: object, tag: str) -> str:
        cls = ' class="n"' if i in numeric else ""
        body = str(v) if raw else esc(v)
        return f"<{tag}{cls}>{body}</{tag}>"

    h = "".join(cell(i, x, "th") for i, x in enumerate(head))
    b = "".join("<tr>" + "".join(cell(i, x, "td") for i, x in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def chip(text: str, kind: str) -> str:
    return f'<span class="chip {kind}">{esc(text)}</span>'


def legend(names: Sequence[str]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:{SERIES[i % len(SERIES)]}"></i>{esc(n)}</span>'
        for i, n in enumerate(names)
    ) + "</div>"


def _nice(vmax: float) -> tuple[float, list[float]]:
    """Round tick step (1, 2 or 5 × 10^n) giving at most 5 intervals."""
    if vmax <= 0:
        return 1.0, [0, 0.5, 1.0]
    mag = 10 ** np.floor(np.log10(vmax))
    step = mag
    for m in (0.1, 0.2, 0.5, 1, 2, 5, 10):
        step = m * mag
        if vmax / step <= 5:
            break
    n = int(np.ceil(vmax / step - 1e-9))
    return step * n, [step * i for i in range(n + 1)]


def bar_chart(categories: Sequence[str], series: dict[str, Sequence[float]], unit: str,
              stacked: bool = True, width: int = 900, height: int = 260) -> str:
    """Grouped or stacked bar chart as inline SVG, on one linear scale."""
    names = list(series)
    vals = np.array([series[n] for n in names], dtype=float)
    vals = np.nan_to_num(vals)
    top_v = vals.clip(min=0).sum(axis=0).max() if stacked else vals.max()
    top, ticks = _nice(float(top_v))
    ml, mr, mt, mb = 56, 8, 24, 26
    pw, ph = width - ml - mr, height - mt - mb
    n = len(categories)
    slot = pw / max(n, 1)
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(unit)} chart">']
    for tk in ticks:
        y = mt + ph - ph * tk / top
        out.append(f'<line x1="{ml}" x2="{width - mr}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--rule)"/>')
        out.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" font-size="11" text-anchor="end" fill="var(--muted)">{tk:,.{0 if top >= 10 else 1}f}</text>')
    out.append(f'<text x="{ml}" y="12" font-size="11" fill="var(--muted)">{esc(unit)}</text>')
    for i, c in enumerate(categories):
        x0 = ml + i * slot
        if stacked:
            base = 0.0
            bw = slot * 0.62
            for k in range(len(names)):
                v = max(vals[k, i], 0)
                h = ph * v / top
                y = mt + ph - ph * (base + v) / top
                out.append(f'<rect x="{x0 + (slot - bw) / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" fill="{SERIES[k % 6]}"><title>{esc(c)} · {esc(names[k])}: {v:,.1f} {esc(unit)}</title></rect>')
                base += v
        else:
            bw = slot * 0.8 / max(len(names), 1)
            for k in range(len(names)):
                v = max(vals[k, i], 0)
                h = ph * v / top
                out.append(f'<rect x="{x0 + slot * 0.1 + k * bw:.1f}" y="{mt + ph - h:.1f}" width="{bw * 0.92:.1f}" height="{h:.1f}" fill="{SERIES[k % 6]}"><title>{esc(c)} · {esc(names[k])}: {v:,.1f} {esc(unit)}</title></rect>')
        if n <= 24 or i % max(1, n // 12) == 0:
            out.append(f'<text x="{x0 + slot / 2:.1f}" y="{height - 8}" font-size="11" text-anchor="middle" fill="var(--muted)">{esc(c)}</text>')
    out.append(f'<line x1="{ml}" x2="{width - mr}" y1="{mt + ph}" y2="{mt + ph}" stroke="var(--text)"/>')
    out.append("</svg>")
    return f'<div class="chart">{"".join(out)}</div>' + legend(names)


def heatmap(hourly: np.ndarray, unit: str, width: int = 900, height: int = 220) -> str:
    """Day-of-year × hour-of-day heatmap (365 × 24) on a sequential single-hue scale."""
    m = np.asarray(hourly, dtype=float).reshape(365, 24)
    vmax = float(np.nanmax(m)) if np.nanmax(m) > 0 else 1.0
    ml, mt, mb = 40, 6, 22
    pw, ph = width - ml - 6, height - mt - mb
    cw, chh = pw / 365, ph / 24
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="hourly heatmap">']
    for d in range(365):
        for h in range(24):
            v = m[d, h]
            if v <= 0:
                continue
            out.append(f'<rect x="{ml + d * cw:.2f}" y="{mt + h * chh:.2f}" width="{cw + 0.3:.2f}" height="{chh + 0.3:.2f}" fill="var(--c1)" fill-opacity="{0.08 + 0.92 * v / vmax:.2f}"/>')
    for h in (0, 6, 12, 18, 23):
        out.append(f'<text x="{ml - 4}" y="{mt + h * chh + chh:.1f}" font-size="10" text-anchor="end" fill="var(--muted)">{h:02d}h</text>')
    days = np.cumsum([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30])
    for i, d in enumerate(days):
        out.append(f'<text x="{ml + d * cw + 2:.1f}" y="{height - 6}" font-size="10" fill="var(--muted)">{MONTHS[i]}</text>')
    out.append("</svg>")
    return f'<div class="chart">{"".join(out)}</div><p class="note">Darkest cell = {vmax:,.0f} {esc(unit)}.</p>'


def scatter(points: Sequence[tuple[float, float, str]], xlabel: str, ylabel: str, front: Sequence[str] = (),
            width: int = 900, height: int = 340) -> str:
    """Scatter (e.g. capex vs carbon) on linear scales; points in `front` are highlighted and joined."""
    if not points:
        return ""
    xs = np.array([p[0] for p in points])
    ys = np.array([p[1] for p in points])
    x0, x1 = float(xs.min()), float(xs.max())
    y0, y1 = float(ys.min()), float(ys.max())
    padx = (x1 - x0) * 0.08 or 1.0
    pady = (y1 - y0) * 0.1 or 1.0
    x0, x1, y0, y1 = x0 - padx, x1 + padx, y0 - pady, y1 + pady
    ml, mr, mt, mb = 70, 16, 16, 40
    pw, ph = width - ml - mr, height - mt - mb
    X = lambda v: ml + pw * (v - x0) / (x1 - x0)  # noqa: E731
    Y = lambda v: mt + ph - ph * (v - y0) / (y1 - y0)  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="scatter">']
    for k in range(5):
        yv = y0 + (y1 - y0) * k / 4
        out.append(f'<line x1="{ml}" x2="{width - mr}" y1="{Y(yv):.1f}" y2="{Y(yv):.1f}" stroke="var(--rule)"/>')
        out.append(f'<text x="{ml - 6}" y="{Y(yv) + 4:.1f}" font-size="11" text-anchor="end" fill="var(--muted)">{yv:,.0f}</text>')
        xv = x0 + (x1 - x0) * k / 4
        out.append(f'<text x="{X(xv):.1f}" y="{height - 22}" font-size="11" text-anchor="middle" fill="var(--muted)">{xv:,.0f}</text>')
    out.append(f'<text x="{ml + pw / 2}" y="{height - 4}" font-size="11.5" text-anchor="middle" fill="var(--muted)">{esc(xlabel)}</text>')
    out.append(f'<text x="12" y="{mt + ph / 2}" font-size="11.5" text-anchor="middle" fill="var(--muted)" transform="rotate(-90 12 {mt + ph / 2})">{esc(ylabel)}</text>')
    fpts = sorted([p for p in points if p[2] in front], key=lambda p: p[0])
    if len(fpts) > 1:
        d = " ".join(f"{X(p[0]):.1f},{Y(p[1]):.1f}" for p in fpts)
        out.append(f'<polyline points="{d}" fill="none" stroke="var(--c2)" stroke-width="2"/>')
    for x, y, lab in points:
        on = lab in front
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="{6 if on else 4.5}" fill="{"var(--c2)" if on else "var(--c1)"}" fill-opacity="{1 if on else 0.6}"><title>{esc(lab)}: {x:,.0f}, {y:,.1f}</title></circle>')
        if on or len(points) <= 16:
            out.append(f'<text x="{X(x) + 8:.1f}" y="{Y(y) - 6:.1f}" font-size="10.5" fill="var(--text)">{esc(lab)}</text>')
    out.append("</svg>")
    return f'<div class="chart">{"".join(out)}</div>'


def tornado_chart(base: float, rows: Sequence[tuple[str, float, float]], unit: str, width: int = 900) -> str:
    """Horizontal tornado: change from base for low and high settings of each driver."""
    if not rows:
        return ""
    h = 30 * len(rows) + 50
    m = max(max(abs(a), abs(b)) for _, a, b in rows) or 1.0
    ml, mr = 300, 30
    pw = width - ml - mr
    cx = ml + pw / 2
    X = lambda v: cx + (pw / 2) * v / m  # noqa: E731
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="tornado chart">']
    out.append(f'<line x1="{cx}" x2="{cx}" y1="10" y2="{h - 30}" stroke="var(--text)"/>')
    for i, (name, lo, hi) in enumerate(rows):
        y = 16 + i * 30
        for v, col in ((lo, "var(--c1)"), (hi, "var(--c2)")):
            x = min(X(0), X(v))
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{abs(X(v) - X(0)):.1f}" height="20" fill="{col}"><title>{esc(name)}: {v:+,.1f} {esc(unit)}</title></rect>')
        out.append(f'<text x="{ml - 8}" y="{y + 14}" font-size="12" text-anchor="end" fill="var(--text)">{esc(name)}</text>')
    for v in (-m, -m / 2, 0, m / 2, m):
        out.append(f'<text x="{X(v):.1f}" y="{h - 12}" font-size="11" text-anchor="middle" fill="var(--muted)">{v:+,.0f}</text>')
    out.append("</svg>")
    return (f'<div class="chart">{"".join(out)}</div>' + legend(["Low setting", "High setting"])
            + f'<p class="note">Change in annual electricity against the central case of {base:,.0f} {esc(unit)}.</p>')
