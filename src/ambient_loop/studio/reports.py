"""Walkthrough reports (Milieu Labs style, light theme for print).

Step 5  comparison report for up to three strategies: schematic, energy flows, heat recovery,
        per-service efficiencies for BRUKL, energy, carbon, cost, plant space, temperatures
Step 6  detailed engineer report, concise file note, and the GLA calculation summary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ambient_loop.reporting import html as H
from ambient_loop.reporting.full_summary import CaseResult
from ambient_loop.reporting.gla import service_ratios
from ambient_loop.reporting.sankey import sankey_svg
from ambient_loop.reporting.schematic import schematic
from ambient_loop.studio.data import rates, rates_are_placeholder
from ambient_loop.studio.pipeline import Screen, SweepPoint

LABS_FONT = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500'
             '&family=Raleway:wght@300;400;600;700&display=swap">')
LABS_CSS = """<style>
:root{--mono:"JetBrains Mono",ui-monospace,Menlo,Consolas,monospace;--accent:#009ab9;--bar:#231f20}
td.n,th.n,.kpi .v,code{font-family:var(--mono);font-variant-numeric:tabular-nums}
.kpi .v{font-weight:300}.label{font-family:var(--mono);letter-spacing:.08em}
.brand{display:flex;align-items:center;gap:12px;margin-bottom:8px}.brand img,.brand svg{height:28px;width:auto;max-width:220px}
td.n{white-space:normal}
.banner{border:1px solid #f0a33a;background:#fdf1df;padding:8px 12px;font-size:12.5px;color:#231f20}
footer span:last-child::after{content:" · Milieu Labs"}
</style>"""


def _labs(page: str, logo_svg: str = "") -> str:
    page = page.replace("</head>", LABS_FONT + LABS_CSS + "</head>")
    if logo_svg:
        page = page.replace('<div class="page"><header>', f'<div class="page"><header><div class="brand">{logo_svg}</div>', 1)
    return page


@dataclass
class Detail:
    screen: Screen
    case: CaseResult
    sweep: list[SweepPoint] = field(default_factory=list)


def _banner() -> str:
    if rates_are_placeholder():
        return ('<div class="banner">Cost figures use placeholder rates: load the Milieu rate table (MILIEU_DATA/rates.yaml) '
                'to replace them.</div>')
    r = rates()
    return f'<p class="note">Rates: {H.esc(r.get("source", ""))}, {H.esc(r.get("date", ""))}.</p>'


def heat_recovery_block(c: CaseResult) -> tuple[str, dict[str, float]]:
    d = c.result.ledger.derived
    q = lambda k: float(np.sum(d.get(f"o:{k}", 0))) / 1e3  # noqa: E731
    heat_dem = c.demand_MWh.get("LTHW", 0) + c.demand_MWh.get("DHW", 0)
    rec = q("hr_L") + q("hr_P") + q("evap_gen_hr")
    loop_reuse = float(np.sum(np.minimum(d.get("loop:extraction", np.zeros(1)), d.get("o:cond_rej", np.zeros(1))))) / 1e3
    rej = q("dc_r") + q("ac_r") + q("tw_r") + q("cond_air")
    nums = {"recovered_MWh": rec, "loop_reuse_MWh": loop_reuse, "rejected_MWh": rej,
            "share": (rec + loop_reuse) / heat_dem if heat_dem else 0.0}
    text = (f"Heat recovered directly from cooling into heating or hot water: {rec:,.0f} MWh/yr. Heat rejected by cooling and "
            f"reused through the ambient loop in the same hour: {loop_reuse:,.0f} MWh/yr. Together these cover "
            f"{nums['share']:.0%} of the heating and hot-water demand. Heat still rejected outdoors: {rej:,.0f} MWh/yr.")
    return text, nums


def comparison_report(project_name: str, details: list[Detail], meta: list[str], narrative: Any | None = None,
                      logo: str = "") -> str:
    r = H.Report(title=f"{project_name}: plant strategy comparison", eyebrow="Milieu Labs / Energy strategy · step 5",
                 lead=f"Comparison of {len(details)} central-plant strategies, simulated hour by hour on the project loads.",
                 boundary=("Whole system, all pumps; per-service efficiencies on the conservative convention used for BRUKL "
                           "inputs (heat-recovery compressor energy charged to heat and kept in cooling); gas counted as energy "
                           "input; carbon SAP 10.2 (electricity 0.136, gas 0.210 kgCO₂e/kWh)."), meta=meta)
    if narrative is not None:
        r.section("Summary", H.p(narrative.summary), "<ul>" + "".join(f"<li>{H.esc(x)}</li>" for x in narrative.key_points) + "</ul>")
    head = ["Metric"] + [d.screen.id for d in details]
    rows = [["Strategy"] + [d.screen.title for d in details],
            ["Electricity MWh/yr"] + [H.fmt(d.screen.electricity_MWh, 0) for d in details],
            ["Gas MWh/yr"] + [H.fmt(d.screen.gas_MWh, 0) for d in details],
            ["Carbon tCO₂e/yr"] + [H.fmt(d.screen.carbon_t, 0) for d in details],
            ["Running cost £k/yr (energy + maintenance)"] + [H.fmt(d.screen.opex_gbp / 1e3, 0) for d in details],
            ["Capital cost £k"] + [H.fmt(d.screen.capex_gbp / 1e3, 0) for d in details],
            ["Roof plant area m²"] + [H.fmt(d.screen.roof_m2, 0) for d in details],
            ["Plant room area m²"] + [H.fmt(d.screen.plantroom_m2, 0) for d in details],
            ["Pumps MWh/yr"] + [H.fmt(d.screen.pumps_MWh, 0) for d in details]]
    for svc, label in (("LTHW", "Space heating SCOP / efficiency"), ("DHW", "Hot water SPF / efficiency"), ("CHW", "Cooling SEER")):
        rows.append([label] + [f"{service_ratios(d.case, 'conservative', False).get(svc, {}).get('ratio', float('nan')):.2f}" for d in details])
    r.section("Side by side", H.table(head, rows, numeric=list(range(1, len(details) + 1))), _banner())
    r.section("Carbon and capital", H.bar_chart([d.screen.id for d in details],
              {"tCO₂e/yr": [d.screen.carbon_t for d in details]}, "tCO₂e/yr"),
              H.bar_chart([d.screen.id for d in details], {"Capital £k": [d.screen.capex_gbp / 1e3 for d in details]}, "£k"))
    for d in details:
        svg, notes = schematic(d.case.arrangement)
        hr_text, _ = heat_recovery_block(d.case)
        sr = service_ratios(d.case, "conservative", False)
        brukl = H.table(["Service", "Delivered MWh/yr", "Energy in MWh/yr", "Seasonal ratio for BRUKL"],
                        [[k, H.fmt(v["heat_MWh"], 0), H.fmt(v["elec_MWh"], 0), f"{v['ratio']:.2f}"] for k, v in sr.items()],
                        numeric=[1, 2, 3])
        sweep = ""
        if d.sweep:
            sweep = ("<h3>Flow temperatures and ΔT: compressor against pumping energy</h3>" + H.table(
                ["LTHW °C", "CHW °C", "Total MWh/yr", "Compressors etc.", "Pumps"],
                [[f"{p.lthw[0]:g}/{p.lthw[1]:g}", f"{p.chw[0]:g}/{p.chw[1]:g}" if p.chw else "–", H.fmt(p.electricity_MWh, 0),
                  H.fmt(p.compressor_MWh, 0), H.fmt(p.pumps_MWh, 1)] for p in sorted(d.sweep, key=lambda x: x.electricity_MWh)],
                numeric=[2, 3, 4]) + H.p(_sweep_advice(d.sweep), "note"))
        warn = [f for f in d.case.findings if f.severity in ("warn", "fail")][:6]
        r.section(f"{d.screen.id}: {d.screen.title}", f'<div class="chart">{svg}</div>',
                  "<ol class='note'>" + "".join(f"<li>{H.esc(n)}</li>" for n in notes) + "</ol>",
                  "<h3>Energy flows</h3>" + sankey_svg(d.case.result.ledger),
                  "<h3>Role of heat recovery</h3>" + H.p(hr_text),
                  "<h3>Efficiencies for BRUKL (whole system, conservative)</h3>" + brukl,
                  "<h3>Plant space</h3>" + H.p(f"Roof: about {d.screen.roof_m2:,.0f} m² of air-side plant including access. Plant room: "
                                                f"about {d.screen.plantroom_m2:,.0f} m² for water-side plant, vessels and access "
                                                "(space factors A-067; check against manufacturer footprints)."),
                  sweep,
                  "<h3>Engineering checks</h3>" + "".join(H.p(f"{f.rule} · {f.message}") for f in warn) if warn else "",
                  H.p("Compatibility: " + "; ".join(d.screen.notes), "note") if d.screen.notes else "")
    return _labs(r.render(), logo)


def _sweep_advice(pts: list[SweepPoint]) -> str:
    best = min(pts, key=lambda x: x.electricity_MWh)
    worst = max(pts, key=lambda x: x.electricity_MWh)
    pump_range = max(p.pumps_MWh for p in pts) - min(p.pumps_MWh for p in pts)
    return (f"Lowest total at LTHW {best.lthw[0]:g}/{best.lthw[1]:g} °C" + (f", CHW {best.chw[0]:g}/{best.chw[1]:g} °C" if best.chw else "")
            + f" ({best.electricity_MWh:,.0f} MWh/yr), {worst.electricity_MWh - best.electricity_MWh:,.0f} MWh/yr below the worst "
            f"combination tested. Pump energy varies by {pump_range:,.1f} MWh/yr across the range: wider ΔT cuts pumping, but the "
            "compressor effect of flow temperature dominates. Check emitter output at the chosen temperatures before fixing them.")


def detailed_report(project_name: str, d: Detail, others: list[Screen], meta: list[str], narrative: Any | None = None,
                    logo: str = "") -> str:
    from ambient_loop.reporting.case_report import case_page
    page = case_page(type("P", (), {"name": project_name})(), d.case, meta)  # type: ignore[arg-type]
    extra = H.Report(title="", eyebrow="")
    if narrative is not None:
        extra.section("Summary", H.p(narrative.summary), "<ul>" + "".join(f"<li>{H.esc(x)}</li>" for x in narrative.key_points) + "</ul>",
                      "<h3>Risks</h3><ul>" + "".join(f"<li>{H.esc(x)}</li>" for x in narrative.risks) + "</ul>")
    hr_text, _ = heat_recovery_block(d.case)
    extra.section("Selected strategy in context", H.table(
        ["Strategy", "MWh elec", "MWh gas", "tCO₂e", "Capex £k", "Roof m²", "Plant room m²"],
        [[("★ " if s.id == d.screen.id else "") + s.title, H.fmt(s.electricity_MWh, 0), H.fmt(s.gas_MWh, 0), H.fmt(s.carbon_t, 0),
          H.fmt(s.capex_gbp / 1e3, 0), H.fmt(s.roof_m2, 0), H.fmt(s.plantroom_m2, 0)] for s in others[:8]],
        numeric=[1, 2, 3, 4, 5, 6]), H.p(hr_text), _banner())
    if d.sweep:
        extra.section("Temperature and ΔT optimisation", H.p(_sweep_advice(d.sweep)))
    body = "".join(extra.parts)
    page = page.replace("<footer>", body + "<footer>", 1)
    return _labs(page, logo)


def file_note(project_name: str, d: Detail, meta: list[str], narrative: Any | None = None, logo: str = "") -> str:
    sr = service_ratios(d.case, "conservative", False)
    r = H.Report(title=f"File note: {project_name} energy strategy", eyebrow="Milieu Labs / Energy strategy · file note",
                 lead=f"Selected central plant: {d.screen.title}.", meta=meta)
    lines = [narrative.summary] if narrative is not None else []
    lines.append(f"Annual energy: {d.screen.electricity_MWh:,.0f} MWh electricity" + (f" and {d.screen.gas_MWh:,.0f} MWh gas" if d.screen.gas_MWh else "")
                 + f"; carbon {d.screen.carbon_t:,.0f} tCO₂e/yr (SAP 10.2); capital cost £{d.screen.capex_gbp / 1e3:,.0f}k"
                 + (" (placeholder rates)" if rates_are_placeholder() else "") + ".")
    lines.append("Seasonal efficiencies for BRUKL (whole system, conservative): "
                 + "; ".join(f"{k} {v['ratio']:.2f}" for k, v in sr.items()) + ".")
    lines.append(f"Plant space: roof about {d.screen.roof_m2:,.0f} m², plant room about {d.screen.plantroom_m2:,.0f} m².")
    hr_text, _ = heat_recovery_block(d.case)
    lines.append(hr_text)
    r.section("Decision", "".join(H.p(x) for x in lines))
    warn = [f for f in d.case.findings if f.severity in ("warn", "fail")][:5]
    if warn:
        r.section("Actions and risks", "<ul>" + "".join(f"<li>{H.esc(f.message)}</li>" for f in warn) + "</ul>")
    r.section("Record", H.table(["Item", "Value"], [["Prepared by", ""], ["Checked by", ""], ["Date", ""], ["Run", meta[0] if meta else ""]]))
    return _labs(r.render(), logo)

