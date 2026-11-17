"""Scenario comparison report: ranked table with targets, breakdowns, findings, assumptions."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ambient_loop.reporting import html as H
from ambient_loop.reporting.full_summary import CaseResult
from ambient_loop.schema.project import Project

ROOT = Path(__file__).resolve().parents[3]


def _light(value: float, target: float) -> str:
    if not np.isfinite(value):
        return H.chip("n/a", "info")
    if value >= target:
        return H.chip(f"{value:.2f}", "good")
    if value >= 0.95 * target:
        return H.chip(f"{value:.2f}", "warn")
    return H.chip(f"{value:.2f}", "bad")


def assumptions_rows() -> list[list[str]]:
    rows = []
    for line in (ROOT / "ASSUMPTIONS.md").read_text().splitlines():
        if line.startswith("| A-"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows.append([cells[0], cells[1], cells[3] if len(cells) > 3 else "", cells[-1]])
    return rows


def scenario_report(project: Project, cases: list[CaseResult], meta: list[str], recovery_line: str = "",
                    extra_sections: list[tuple[str, str]] | None = None) -> str:
    cases = sorted(cases, key=lambda c: c.electricity_MWh)
    best = cases[0]
    base = next((c for c in cases if c.arrangement.id == "A1"), cases[-1])
    b = best.system.boundary
    r = H.Report(
        title=f"{project.name}: arrangement comparison",
        eyebrow="Ambient loop toolkit · Scenario results",
        lead=("Each arrangement simulated hour by hour with temperature- and part-load-dependent plant, "
              "energy balance closed every hour."),
        boundary=(f"{b.mode.value} mode; pumps {'included' if b.pumps_included else 'excluded'}; DHW profile "
                  f"{b.dhw_profile.value}; per-service ratios under pro-rata and incremental conventions; system "
                  "ratio = (heat + coolth delivered) ÷ total electricity. Indicative design-stage results."),
        meta=meta,
    )
    saving = base.electricity_MWh - best.electricity_MWh
    r.section("Headline", H.kpis([
        (best.arrangement.id, best.arrangement.title),
        (H.fmt(best.electricity_MWh, 0), "MWh/yr electricity, best case"),
        (f"{best.system.value:.2f}", "system ratio, best case"),
        (H.fmt(saving, 0), f"MWh/yr saved against {base.arrangement.id}"),
    ]), H.p(recovery_line) if recovery_line else "")
    tg = project.targets
    head = ["Rank", "Arrangement", "Electricity MWh/yr", "System ratio", "Pumps MWh/yr"]
    head += [f"{t.service} ({t.allocation[:4]}) ≥ {t.seasonal_ratio:g}" for t in tg]
    head += ["Warnings"]
    rows = []
    for i, c in enumerate(cases, 1):
        cells: list[object] = [str(i), f"<b>{H.esc(c.arrangement.id)}</b> {H.esc(c.arrangement.title)}",
                               H.fmt(c.electricity_MWh, 0), f"{c.system.value:.2f}", H.fmt(c.pumps_MWh, 0)]
        for t in tg:
            m = c.per_service.get(t.allocation, {}).get(t.service)
            cells.append(_light(m.value if m else float("nan"), t.seasonal_ratio))
        nw = sum(1 for f in c.findings if f.severity in ("warn", "fail"))
        nf = sum(1 for f in c.findings if f.severity == "fail")
        cells.append(H.chip(f"{nf} fail", "bad") + " " if nf else "")
        cells[-1] = f"{cells[-1]}{nw - nf} warn"
        rows.append(cells)
    r.section("Ranked results", H.table(head, rows, numeric=[2, 3, 4], raw=True),
              H.p("Traffic lights: green meets the target, amber within 5 %, red below. Every figure uses the "
                  "boundary stated above.", "note"))
    comps = sorted({k for c in cases for k in c.elec_by_component_MWh})
    r.section("Where the electricity goes", H.bar_chart(
        [c.arrangement.id for c in cases], {k: [c.elec_by_component_MWh.get(k, 0.0) for c in cases] for k in comps},
        "MWh/yr"))
    top = cases[: min(4, len(cases))]
    r.section("Month by month (top cases)", H.bar_chart(H.MONTHS, {c.arrangement.id: c.monthly_elec_MWh for c in top},
                                                       "MWh electricity", stacked=False))
    srv = sorted({s for c in cases for s in c.per_service["pro_rata"]})
    rows2 = []
    for c in cases:
        rows2.append([c.arrangement.id] + [f"{c.per_service['pro_rata'][s].value:.2f} / {c.per_service['incremental'][s].value:.2f}"
                                           for s in srv])
    r.section("Per-service ratios (pro-rata / incremental)", H.table(["Arrangement"] + srv, rows2, numeric=list(range(1, len(srv) + 1))),
              H.p("Allocation changes the per-service split, never the total (rule 13).", "note"))
    for c in top:
        blocks = [H.p(" ".join(c.arrangement.why), "")]
        if c.arrangement.relies_on:
            blocks.append(H.p("Relies on: " + "; ".join(c.arrangement.relies_on), "note"))
        items = []
        for f in c.findings:
            kind = {"fail": "bad", "warn": "warn", "info": "info"}[f.severity]
            items.append(f'<div class="finding"><div>{H.chip(f.rule, kind)}</div><div><b>{H.esc(f.title)}.</b> '
                         f'{H.esc(f.message)}<div class="note">{H.esc(f.evidence)}</div></div></div>')
        blocks.append("".join(items))
        r.section(f"Insights: {c.arrangement.id}", *blocks)
    for title, body in extra_sections or []:
        r.section(title, body)
    r.section("Assumptions in force", H.table(["ID", "Assumption", "Effect", "Status"], assumptions_rows()),
              H.p("Full register in ASSUMPTIONS.md; each entry is tagged where it enters a result.", "note"))
    return r.render()
