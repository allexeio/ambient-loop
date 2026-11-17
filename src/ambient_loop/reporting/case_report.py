"""Per-arrangement detail page (energy-statement appendix) and side-by-side comparison."""

from __future__ import annotations

import numpy as np

from ambient_loop.core.series import monthly_sum
from ambient_loop.reporting import html as H
from ambient_loop.reporting.full_summary import CaseResult
from ambient_loop.reporting.sankey import sankey_svg
from ambient_loop.reporting.schematic import schematic
from ambient_loop.schema.project import Project


def _hist(vals: list[float], bins: int = 10) -> list[float]:
    if not vals:
        return [0.0] * bins
    h, _ = np.histogram(np.clip(vals, 0, 1), bins=bins, range=(0, 1))
    return list(h.astype(float))


def case_page(project: Project, c: CaseResult, meta: list[str]) -> str:
    a = c.arrangement
    led = c.result.ledger
    b = c.system.boundary
    r = H.Report(title=f"{a.id}: {a.title}", eyebrow=f"{project.name} · Arrangement detail", lead=" ".join(a.why),
                 boundary=(f"{b.mode.value} mode; pumps {'included' if b.pumps_included else 'excluded'}; DHW profile "
                           f"{b.dhw_profile.value}; headline tag [{c.system.headline_tag.label}]."), meta=meta)
    r.section("Headline", H.kpis([(H.fmt(c.electricity_MWh, 0), "MWh/yr electricity"), (f"{c.system.value:.2f}", "system ratio"),
                                  (H.fmt(c.pumps_MWh, 0), "MWh/yr pumps")]))
    svg, notes = schematic(a)
    r.section("Principle schematic", f'<div class="chart">{svg}</div>',
              "<ol>" + "".join(f"<li>{H.esc(n)}</li>" for n in notes) + "</ol>")
    r.section("Energy flows", sankey_svg(led))
    rows = []
    for conv in ("pro_rata", "incremental", "isolated"):
        for s, m in sorted(c.per_service[conv].items()):
            rows.append([conv, s, H.fmt(m.numerator_kWh / 1e3, 1), H.fmt(m.denominator_kWh / 1e3, 1), f"{m.value:.2f}"])
    r.section("Per-service ratios", H.table(["Convention", "Service", "Useful MWh", "Electricity MWh", "Ratio"], rows, numeric=[2, 3, 4]))
    comps = {k: list(monthly_sum(v) / 1e3) for k, v in led.elec.items() if v.sum() > 0}
    r.section("Monthly electricity by plant", H.bar_chart(H.MONTHS, comps, "MWh"))
    hm = []
    for key, label in (("o:ashp_d", "ASHP heat to loop"), ("o:cond_hr", "Heat recovered by condensers"),
                       ("o:imm", "DHW immersion backup"), ("o:dc_r", "Dry-cooler rejection")):
        v = led.derived.get(key)
        if v is not None and v.sum() > 0:
            hm.append(f"<h3>{H.esc(label)}</h3>" + H.heatmap(v, "kW"))
    if hm:
        r.section("Hourly patterns", *hm)
    pl = c.result.plr
    if pl:
        r.section("Part-load histograms (lead unit, running hours)", H.bar_chart(
            [f"{i / 10:.1f}–{(i + 1) / 10:.1f}" for i in range(10)], {k: _hist(v) for k, v in pl.items()}, "hours",
            stacked=False))
    env = [[k, f"{h:,}"] for k, h in c.envelope_hours.items()]
    flags = [[k, f"{h:,}"] for k, h in c.flags.items()]
    r.section("Envelope and operating flags",
              '<div class="grid2"><div>' + H.table(["Machine / mode", "Hours outside map"], env, numeric=[1]) + "</div><div>"
              + H.table(["Flag", "Hours"], flags, numeric=[1]) + "</div></div>")
    items = []
    for f in c.findings:
        kind = {"fail": "bad", "warn": "warn", "info": "info"}[f.severity]
        items.append(f'<div class="finding"><div>{H.chip(f.rule, kind)}</div><div><b>{H.esc(f.title)}.</b> {H.esc(f.message)}'
                     f'<div class="note">{H.esc(f.evidence)}</div></div></div>')
    r.section("Insights (rules engine)", "".join(items))
    return r.render()


def compare_page(project: Project, cases: list[CaseResult], meta: list[str]) -> str:
    """Side-by-side comparison of 2–4 arrangements with deltas and driver explanation."""
    cases = cases[:4]
    ref = cases[0]
    r = H.Report(title=f"Compare: {' vs '.join(c.arrangement.id for c in cases)}", eyebrow=f"{project.name} · Comparison",
                 lead=f"Deltas are against {ref.arrangement.id}.", boundary=ref.system.boundary.short(), meta=meta)
    head = ["Metric"] + [c.arrangement.id for c in cases]
    rows = [["Electricity MWh/yr"] + [f"{c.electricity_MWh:,.1f}" + (f" ({c.electricity_MWh - ref.electricity_MWh:+,.1f})" if c is not ref else "") for c in cases],
            ["System ratio"] + [f"{c.system.value:.2f}" for c in cases],
            ["Pumps MWh/yr"] + [f"{c.pumps_MWh:,.1f}" for c in cases]]
    for conv in ("pro_rata", "incremental"):
        for s in sorted(ref.per_service[conv]):
            rows.append([f"{s} ({conv})"] + [f"{c.per_service[conv][s].value:.2f}" for c in cases])
    r.section("Side by side", H.table(head, rows, numeric=list(range(1, len(cases) + 1))))
    comps = sorted({k for c in cases for k in c.elec_by_component_MWh})
    drows = []
    expl = []
    for c in cases[1:]:
        deltas = {k: c.elec_by_component_MWh.get(k, 0) - ref.elec_by_component_MWh.get(k, 0) for k in comps}
        top = sorted(deltas.items(), key=lambda kv: -abs(kv[1]))[:3]
        expl.append(f"{c.arrangement.id} vs {ref.arrangement.id}: " + "; ".join(f"{k} {v:+,.0f} MWh" for k, v in top) + ".")
    for k in comps:
        drows.append([k] + [f"{c.elec_by_component_MWh.get(k, 0):,.1f}" for c in cases])
    r.section("What drives the difference", H.table(["Plant electricity MWh/yr"] + [c.arrangement.id for c in cases], drows,
                                                     numeric=list(range(1, len(cases) + 1))),
              "".join(H.p(e) for e in expl))
    r.section("Energy flows", *[f"<h3>{H.esc(c.arrangement.id)}</h3>" + sankey_svg(c.result.ledger) for c in cases])
    return r.render()
