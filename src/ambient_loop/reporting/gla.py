"""Concise options report and GLA calculation summary.

1. Concise report (2–3 pages): loads, the arrangement ladder from basic to complex, the
   recommendation with reasons, drivers and risks, and the schematic.
2. Calculation summary (for an Energy Statement appendix). It substantiates the heat pump
   seasonal efficiencies entered in the Part L 2021 / SBEM model (BRUKL) for the proposed
   system. Sections: values to enter; system and plant data with sources; method (8,760 h,
   weather, loads, standards); heat-source and distribution temperatures; annual and monthly
   results; sensitivity; assumptions; metering for post-construction monitoring; sign-off.

Boundary for SBEM inputs [A-057]. SBEM calculates auxiliary energy for distribution pumps and
fans from the system type. The recommended SBEM inputs therefore EXCLUDE the LTHW/CHW
distribution pumps and INCLUDE everything on the generation side: compressors, shared heat
sources and rejection plant, the ambient-loop pump, heat-recovery and DHW primary pumps. The
system-boundary figures (all pumps) are also given for the GLA SCOP reporting. Confirm the
boundary with the SBEM modeller before submission.

Allocation [A-058]: incremental by default. Cooling carries its stand-alone electricity and
heat recovery is credited to the heat it serves. Pro-rata is shown alongside. The NCM demand
profiles differ from the project's hourly loads, so the seasonal efficiencies here are applied
to SBEM's own demands; the electricity totals will not reconcile one to one.
GLA requirements, from the June 2022 Energy Assessment Guidance as summarised in search results
on 17 Nov 2026: SCOP details, estimated heat and electricity, a SCOP calculation method that
reflects heat-source and distribution temperatures, and a commitment to post-construction
monitoring. Check the edition in force and the exact paragraph references before submission
(the guidance PDF could not be fetched from this environment).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ambient_loop.arrangements.archetypes import ARCHETYPES, LADDER
from ambient_loop.core.series import monthly_sum
from ambient_loop.reporting import html as H
from ambient_loop.reporting.full_summary import CaseResult, run_case
from ambient_loop.reporting.schematic import schematic
from ambient_loop.scenarios.economics import Economics, capex
from ambient_loop.scenarios.run import ProjectContext
from ambient_loop.scenarios.sensitivity import DRIVERS

SERVICE_LABEL = {"LTHW": ("Space heating", "SCOP"), "DHW": ("Domestic hot water", "SCOP (SPF)"),
                 "CHW": ("Space cooling", "SEER")}


def conservative_allocation(c: CaseResult) -> dict[str, np.ndarray]:
    """'No double benefit' convention for SBEM inputs [A-058].

    Start from pro-rata, where all cooling-machine electricity, heat-recovery mode included,
    stays with cooling, so SEER is the cooling machines' own seasonal EER. Then ALSO charge the
    heat-recovery-mode compressor electricity to the heat services it fed: LTHW by recovered
    heat to space heating, DHW by preheat and DHW heat-pump source. Heating SCOPs then reflect
    the machine's heating COP in heat-recovery mode, not near-free recovered heat. The HR-mode
    electricity is counted twice, so the implied total is above the simulated total.
    That is deliberately conservative for compliance.
    """
    pr = c.allocations["pro_rata"].by_service_kWh
    out = {k: v.copy() for k, v in pr.items()}
    d = c.result.ledger.derived
    w_hr = d.get("o:W_cool_hr")
    if w_hr is not None and w_hr.sum() > 0:
        share_dhw = d["o:hr_to_dhw_share"]
        for s in out:
            if s == "LTHW":
                out[s] = out[s] + w_hr * (1 - share_dhw)
            elif s == "DHW":
                out[s] = out[s] + w_hr * share_dhw
    return out


def service_ratios(c: CaseResult, convention: str = "incremental", exclude_distribution: bool = True) -> dict[str, dict[str, float]]:
    """Per-service heat, electricity and ratio, optionally excluding distribution pumps (SBEM boundary).

    convention: 'incremental', 'pro_rata', 'isolated' or 'conservative' (SBEM default, A-058).
    """
    from ambient_loop.core.metric import Allocation
    from ambient_loop.reporting.allocation import Allocated
    a = (Allocated(Allocation.PRO_RATA, conservative_allocation(c)) if convention == "conservative"
         else c.allocations[convention])
    led = c.result.ledger
    out: dict[str, dict[str, float]] = {}
    dist = led.elec.get("pump:distribution")
    info = led.components.get("pump:distribution")
    sub: dict[str, float] = {}
    if exclude_distribution and dist is not None and info is not None and info.service_weights:
        tot = np.sum(list(info.service_weights.values()), axis=0)
        for s, w in info.service_weights.items():
            sub[s] = float(np.sum(dist * np.divide(w, tot, out=np.zeros_like(tot), where=tot > 0))) / 1e3
    for s, m in c.per_service["pro_rata" if convention == "conservative" else convention].items():
        e = a.annual()[s] / 1e3 - sub.get(s, 0.0)
        q = m.numerator_kWh / 1e3
        out[s] = {"heat_MWh": q, "elec_MWh": e, "ratio": q / e if e > 0 else float("nan")}
    return out


def monthly_ratios(c: CaseResult, ctx: ProjectContext, convention: str = "conservative") -> dict[str, list[tuple[float, float]]]:
    svc = {s.id: s.service_name for s in ctx.project.streams}
    out: dict[str, list[tuple[float, float]]] = {}
    alloc = conservative_allocation(c) if convention == "conservative" else c.allocations[convention].by_service_kWh
    for s, e in alloc.items():
        q = np.sum([v for k, v in ctx.inputs.demand_kW.items() if svc[k] == s], axis=0)
        out[s] = list(zip(monthly_sum(q) / 1e3, monthly_sum(e) / 1e3, strict=True))
    return out


@dataclass
class Sens:
    name: str
    low: dict[str, float]
    high: dict[str, float]


def scop_sensitivity(ctx: ProjectContext, c: CaseResult) -> list[Sens]:
    aid = c.arrangement.id
    rebuild = aid in ARCHETYPES
    svc = {st.id: st.service_name for st in ctx.project.streams}
    out = []
    for d in DRIVERS:
        if d.name.startswith(("Loop pump", "Cooling demand", "Space heating demand")):
            continue
        vals = []
        for x in (d.low, d.high):
            s, i = d.apply(ctx.site, ctx.inputs, x, svc)
            arr = ARCHETYPES[aid](s) if rebuild else c.arrangement
            cc = run_case(ctx.project, arr, i, ctx.profile)
            vals.append({k: v["ratio"] for k, v in service_ratios(cc, "conservative").items()})
        out.append(Sens(d.name, vals[0], vals[1]))
    return out


def recommend(cases: list[CaseResult], ctx: ProjectContext, econ: Economics) -> tuple[CaseResult, list[str]]:
    ok = [c for c in cases if not any(f.severity == "fail" for f in c.findings) and c.flags.get("Unmet cooling hours", 0) == 0]
    ok = ok or cases
    reasons = []
    tg = ctx.project.targets

    def meets(c: CaseResult) -> bool:
        for t in tg:
            m = c.per_service.get(t.allocation, {}).get(t.service)
            if m is None or m.value < t.seasonal_ratio:
                return False
        return True

    meeting = [c for c in ok if meets(c)] if tg else ok
    pool = meeting or ok
    best = min(pool, key=lambda c: econ.carbon_t(c.result.ledger.total_elec()))
    if tg and meeting:
        reasons.append(f"Lowest carbon of the {len(meeting)} arrangement(s) that meet every target.")
    elif tg:
        reasons.append("No arrangement meets every target; this is the lowest-carbon option without a compliance failure.")
    else:
        reasons.append("Lowest carbon of the arrangements without a compliance failure.")
    return best, reasons


def _ladder_sort(cases: list[CaseResult]) -> list[CaseResult]:
    order = {k: i for i, k in enumerate(LADDER)}
    return sorted(cases, key=lambda c: (order.get(c.arrangement.id, 99), c.arrangement.id))


def concise_report(ctx: ProjectContext, cases: list[CaseResult], best: CaseResult, reasons: list[str],
                   econ: Economics, tornado_rows: list[tuple[str, float, float]], meta: list[str]) -> str:
    p = ctx.project
    b = best.system.boundary
    basic = next((c for c in cases if c.arrangement.id == "S1"), None)
    r = H.Report(title=f"{p.name}: heating, cooling and hot water options",
                 eyebrow="Plant options appraisal · concise report",
                 lead=(f"{len(cases)} arrangements, from stand-alone heat pumps and chillers to heat-recovery ambient loops, "
                       "were simulated hour by hour on the project's loads."),
                 boundary=(f"{b.mode.value} mode; pumps included; DHW profile {b.dhw_profile.value}; per-service SCOP/SEER on the "
                           "conservative convention used for SBEM inputs (heat-recovery compressor energy charged to heat and "
                           "kept in cooling). Target traffic lights use each target's own stated basis. Capital costs are an "
                           "indicative proxy, not a cost plan."), meta=meta)
    e_best = best.electricity_MWh
    lines = [f"Recommended: {best.arrangement.id}, {best.arrangement.title}. " + " ".join(reasons)]
    if basic is not None and basic is not best:
        lines.append(f"Against basic stand-alone plant (S1) it saves {basic.electricity_MWh - e_best:,.0f} MWh/yr "
                     f"({(1 - e_best / basic.electricity_MWh):.0%}) and "
                     f"{econ.carbon_t(basic.result.ledger.total_elec()) - econ.carbon_t(best.result.ledger.total_elec()):,.0f} tCO₂e/yr.")
    r.section("Summary", "".join(H.p(x) for x in lines))
    svc = {s.id: s.service_name for s in p.streams}
    rows = []
    for s in sorted(set(svc.values())):
        q = np.sum([v for k, v in ctx.inputs.demand_kW.items() if svc[k] == s], axis=0)
        tags = {st.tag.label for st in p.streams if st.service_name == s}
        rows.append([s, H.fmt(q.sum() / 1e3, 1), H.fmt(q.max(), 0), ", ".join(sorted(tags))])
    r.section("Loads", H.table(["Service", "Annual MWh", "Peak kW", "Source"], rows, numeric=[1, 2]),
              H.p(f"DHW profile: {b.dhw_profile.value} (peak hour {ctx.dhw_peak:.1%} of daily energy)."
                  + (" A flat profile flatters heat recovery (rule 6)." if ctx.dhw_peak < 0.06 else ""), "note"))
    lad = _ladder_sort(cases)
    head = ["Option", "MWh/yr", "System", "Heat SCOP", "DHW SCOP", "SEER", "tCO₂e", "Capex £k", "Targets"]
    trows = []
    for c in lad:
        sr = service_ratios(c, "conservative", exclude_distribution=False)
        cap, _ = capex(c.arrangement, econ.rates)
        met = []
        for t in p.targets:
            m = c.per_service.get(t.allocation, {}).get(t.service)
            met.append(m is not None and m.value >= t.seasonal_ratio)
        tchip = (H.chip("all met", "good") if met and all(met) else H.chip(f"{sum(met)}/{len(met)}", "warn") if met else "")
        name = f"<b>{H.esc(c.arrangement.id)}</b>" + (" ★" if c is best else "")
        trows.append([name, H.fmt(c.electricity_MWh, 0), f"{c.system.value:.2f}",
                      f"{sr.get('LTHW', {}).get('ratio', float('nan')):.2f}", f"{sr.get('DHW', {}).get('ratio', float('nan')):.2f}",
                      f"{sr.get('CHW', {}).get('ratio', float('nan')):.2f}",
                      H.fmt(econ.carbon_t(c.result.ledger.total_elec()), 0), H.fmt(cap / 1e3, 0), tchip])
    key = H.table(["Option", "Arrangement"], [[c.arrangement.id, c.arrangement.title] for c in lad])
    r.section("Options: basic to complex", key, H.table(head, trows, numeric=[1, 2, 3, 4, 5, 6, 7], raw=True),
              H.p("Ratios exclude nothing (all pumps), on the conservative SBEM convention. ★ = recommended.", "note"),
              H.bar_chart([c.arrangement.id for c in lad], {"Electricity": [c.electricity_MWh for c in lad]}, "MWh/yr"))
    warn = [f for f in best.findings if f.severity in ("warn", "fail")][:5]
    blocks = [H.p("Why: " + " ".join(best.arrangement.why))]
    if best.arrangement.relies_on:
        blocks.append(H.p("Relies on: " + "; ".join(best.arrangement.relies_on)))
    if tornado_rows:
        blocks.append(H.p("Biggest sensitivities (annual electricity): " + "; ".join(
            f"{n} {lo:+,.0f} / {hi:+,.0f} MWh" for n, lo, hi in tornado_rows[:3]) + "."))
    for f in warn:
        blocks.append(H.p(f"Risk ({f.rule}): {f.message}"))
    r.section(f"Recommendation: {best.arrangement.id}", *blocks)
    svg, notes = schematic(best.arrangement)
    r.section("Principle schematic", f'<div class="chart">{svg}</div>',
              "<ol class='note'>" + "".join(f"<li>{H.esc(n)}</li>" for n in notes) + "</ol>")
    return r.render()


def calc_summary(ctx: ProjectContext, c: CaseResult, sens: list[Sens], meta: list[str], manifest: dict[str, str],
                 weather_label: str = "") -> str:
    p = ctx.project
    a = c.arrangement
    sb = service_ratios(c, "conservative", exclude_distribution=True)
    sys_ = service_ratios(c, "conservative", exclude_distribution=False)
    pr = service_ratios(c, "pro_rata", exclude_distribution=True)
    inc = service_ratios(c, "incremental", exclude_distribution=True)
    r = H.Report(title=f"{p.name}: heat pump seasonal performance",
                 eyebrow="Energy Statement appendix · calculation summary",
                 lead=(f"This summary substantiates the seasonal efficiencies entered in the Part L 2021 (SBEM/BRUKL) model for "
                       f"the proposed system, {a.id}: {a.title}. The values come from an hour-by-hour (8,760 h) simulation "
                       "with temperature- and part-load-dependent plant performance."),
                 boundary=("Recommended SBEM inputs: generation-side boundary (compressors, shared heat sources and rejection, "
                           "ambient-loop, heat-recovery and DHW primary pumps included; LTHW/CHW distribution pumps excluded "
                           "because SBEM counts them as auxiliary energy). Conservative 'no double benefit' allocation: "
                           "heat-recovery compressor energy is charged in full to the heat it serves and also kept in the "
                           "cooling SEER. See §3."), meta=meta)
    rows = []
    for s, (label, metric) in SERVICE_LABEL.items():
        if s not in sb:
            continue
        lo = min([x.low.get(s, np.nan) for x in sens] + [x.high.get(s, np.nan) for x in sens]) if sens else np.nan
        hi = max([x.low.get(s, np.nan) for x in sens] + [x.high.get(s, np.nan) for x in sens]) if sens else np.nan
        rec = np.floor(sb[s]["ratio"] * 20) / 20
        rows.append([label, metric, f"{rec:.2f}", f"{sb[s]['ratio']:.2f}", f"{lo:.2f}–{hi:.2f}", f"{sys_[s]['ratio']:.2f}"])
    r.section("Values for the SBEM model", '<div class="box big">' + H.table(
        ["Service", "Metric", "Enter in SBEM", "Calculated", "Range", "All pumps"], rows,
        numeric=[2, 3, 4, 5]) + H.p("'Enter in SBEM' is the calculated value rounded down to the nearest 0.05. The sensitivity "
                                    "range covers the drivers in §6. Where the proposed plant has no heat pump for a service, "
                                    "that service is not listed.", "note") + "</div>")
    plant = []
    def mdl(spec: Any) -> str:
        return spec.model.library or spec.model.type or ""
    if a.cooling:
        plant.append(["Cooling / heat recovery", f"{a.cooling.count} × {a.cooling.unit_kW:,.0f} kW", mdl(a.cooling),
                      "air-cooled" if a.cooling.sink == "air" else "rejects to ambient loop",
                      f"HR target: {a.cooling.hr_target}"])
    if a.heating:
        plant.append(["Space heating", f"{a.heating.count} × {a.heating.unit_kW:,.0f} kW", mdl(a.heating), a.heating.source, ""])
    elif a.cooling and a.cooling.shared_with_heating:
        plant.append(["Space heating", "shared reversible units", mdl(a.cooling), "ambient loop", ""])
    if a.dhw:
        g = a.dhw.generator
        plant.append(["DHW generation", f"{g.count} × {g.unit_kW:,.0f} kW", mdl(g), g.source, f"store {a.dhw.store_C:g} °C"])
        if a.dhw.reheat:
            plant.append(["DHW circulation reheat", f"{a.dhw.reheat.count} × {a.dhw.reheat.unit_kW:,.0f} kW", mdl(a.dhw.reheat), a.dhw.reheat.source, ""])
        if a.dhw.preheat:
            plant.append(["DHW preheat", "plate HX", "", "heat-recovery circuit", f"cold feed → {a.dhw.preheat.max_C:g} °C"])
    for bb in a.deficit + a.surplus:
        if bb.fleet:
            plant.append([bb.type.replace("_", " "), f"{bb.fleet.count} × {bb.fleet.unit_kW:,.0f} kW", mdl(bb.fleet), "air", ""])
        elif bb.rated_kW:
            plant.append([bb.type.replace("_", " "), f"{bb.rated_kW:,.0f} kW", "", "", ""])
    tags = []
    for cid, info in c.result.ledger.components.items():
        if info.tags:
            tags.append(f"{cid}: {', '.join(sorted(t.label for t in info.tags))}")
    svg, notes = schematic(a)
    r.section("System and plant data", f'<div class="chart">{svg}</div>',
              H.table(["Duty", "Plant", "Performance data", "Source / sink", "Note"], plant),
              H.p("Data provenance by plant item: " + "; ".join(tags) + ".", "note"))
    lf = c.result.ledger.derived.get("T_loop_flow")
    lr = c.result.ledger.derived.get("T_loop_return")
    tdb = ctx.inputs.dry_bulb_C
    trows = []
    svc = {s.id: s.service_name for s in p.streams}

    def wmean(x: np.ndarray, w: np.ndarray) -> float:
        return float(np.sum(x * w) / np.sum(w)) if np.sum(w) > 0 else float("nan")

    for s, (label, _) in SERVICE_LABEL.items():
        q = np.sum([v for k, v in ctx.inputs.demand_kW.items() if svc[k] == s], axis=0) if s in svc.values() else None
        if q is None or q.sum() == 0:
            continue
        if s == "LTHW":
            src = "outdoor air" if (a.heating and a.heating.source == "air") else "ambient loop return"
            if a.cooling and a.cooling.hr_target != "none":
                src += f" + recovered heat at {(a.hr.flow_C if a.hr else a.lthw.flow_C if a.lthw else 45):g} °C"
            ts = wmean(tdb, q) if "air" in src else wmean(lr, q) if lr is not None else float("nan")
            sink = f"{a.lthw.flow_C:g}/{a.lthw.return_C:g} °C" if a.lthw else ""
        elif s == "DHW" and a.dhw:
            g = a.dhw.generator
            src = {"air": "outdoor air", "loop": "ambient loop return", "hr_changeover": "heat-recovery circuit / loop",
                   "hr_dedicated": "heat-recovery circuit"}.get(g.source, g.source)
            ts = wmean(tdb, q) if g.source == "air" else wmean(lr, q) if lr is not None else float("nan")
            sink = f"store {a.dhw.store_C:g} °C, primary {a.dhw.generator_flow_C or a.dhw.store_C:g} °C" + (
                f"; preheat to {a.dhw.preheat.max_C:g} °C" if a.dhw.preheat else "")
        elif s == "CHW":
            src = f"CHW {a.chw.flow_C:g}/{a.chw.return_C:g} °C" if a.chw else ""
            ts = float(a.chw.flow_C) if a.chw else float("nan")
            sink = "outdoor air" if (a.cooling and a.cooling.sink == "air") else (
                f"ambient loop flow (load-weighted {wmean(lf, q):.1f} °C)" if lf is not None else "")
        else:
            continue
        trows.append([label, src, f"{ts:.1f} °C", sink])
    r.section("Method", H.p(
        "Hour-by-hour simulation over a full year (8,760 h). Each hour, every machine's COP is taken from its performance model "
        "at that hour's leaving temperatures: manufacturer maps where supplied, otherwise a Carnot-referenced model calibrated "
        "to project selection data. Part load and cycling follow EN 14825:2022 with fleet sequencing. Shared heat sources, heat "
        "recovery, storage, rejection plant and pumps are modelled explicitly, and the energy balance closes every hour to "
        "1×10⁻⁶ kWh. Equations and references: docs/EQUATIONS.md (CIBSE AM17, CIBSE Guide C, HSG274, EN 14511/14825)."),
        H.table(["Input", "Basis"], [
            ["Weather", weather_label or f"{p.weather.file} (dry bulb, hourly)"],
            ["Loads", "; ".join(f"{s.id}: {s.kind.value}, {s.tag.label}" for s in p.streams)],
            ["DHW profile", f"{c.system.boundary.dhw_profile.value} (peak hour {ctx.dhw_peak:.1%} of daily)"],
            ["Mains water", f"{p.mains_water_C.value if not isinstance(p.mains_water_C, list) else 'monthly profile'} °C"],
            ["Allocation", "Conservative (no double benefit): heating and DHW carry the full compressor energy of heat-recovery "
                           "mode that served them; cooling keeps its machines' full seasonal EER. Heat-recovery energy is "
                           "therefore counted twice, overstating total electricity by "
                           f"{c.result.ledger.derived.get('o:W_cool_hr', np.zeros(1)).sum() / 1e3:,.0f} MWh/yr. "
                           "Incremental and pro-rata are shown in §5 for information."]]),
        "<h3>Heat-source and distribution temperatures</h3>",
        H.table(["Service", "Heat source", "Load-weighted source temperature", "Distribution / sink"], trows))
    res = []
    for s, (label, _metric) in SERVICE_LABEL.items():
        if s in sb:
            res.append([label, H.fmt(sb[s]["heat_MWh"], 1), H.fmt(sb[s]["elec_MWh"], 1), f"{sb[s]['ratio']:.2f}",
                        f"{inc[s]['ratio']:.2f}", f"{pr[s]['ratio']:.2f}"])
    res.append(["Whole system (simulated, all pumps)", H.fmt(sum(v["heat_MWh"] for v in sb.values()), 1),
                H.fmt(c.electricity_MWh, 1), f"{c.system.value:.2f}", "", ""])
    mon = monthly_ratios(c, ctx)
    mrows = []
    for i, m in enumerate(H.MONTHS):
        row = [m]
        for s in ("LTHW", "DHW", "CHW"):
            if s in mon:
                q, e = mon[s][i]
                row.append(f"{q:,.1f} / {q / e:.2f}" if e > 0 else f"{q:,.1f} / –")
        mrows.append(row)
    r.section("Results", H.table(["Service", "Delivered MWh/yr", "Electricity MWh/yr (SBEM boundary, conservative)",
                                  "Ratio (conservative)", "Ratio (incremental)", "Ratio (pro-rata)"], res, numeric=[1, 2, 3, 4, 5]),
              H.p("Incremental and pro-rata ratios are for information. Incremental credits near-free recovered heat to the heat "
                  "services, which can give heating ratios well above any machine's COP; they are not recommended as SBEM inputs.", "note"),
              "<h3>Monthly (MWh delivered / ratio, conservative, all pumps)</h3>",
              H.table(["Month"] + [s for s in ("LTHW", "DHW", "CHW") if s in mon], mrows, numeric=[1, 2, 3]),
              H.p("Backup and unmet: " + (", ".join(f"{k} {v:,} h" for k, v in c.flags.items() if v and ("backup" in k or "immersion" in k or "Unmet" in k)) or "none")
                  + ". Hours outside performance maps while running: " + (", ".join(f"{k} {v:,} h" for k, v in c.envelope_hours.items() if v) or "none")
                  + " (values held at the map edge and flagged).", "note"))
    srows = []
    for x in sens:
        row = [x.name]
        for s in ("LTHW", "DHW", "CHW"):
            if s in sb:
                row.append(f"{x.low.get(s, float('nan')):.2f} / {x.high.get(s, float('nan')):.2f}")
        srows.append(row)
    r.section("Sensitivity", H.table(["Driver (low / high)"] + [s for s in ("LTHW", "DHW", "CHW") if s in sb], srows,
                                     numeric=[1, 2, 3]),
              H.p("Each driver is varied alone; values are SBEM-boundary ratios on the conservative convention. Ranges are "
                  "engineering judgement (A-050).", "note"))
    from ambient_loop.reporting.scenario_report import assumptions_rows
    pri = {"A-040", "A-014", "A-019", "A-020", "A-023", "A-033", "A-052", "A-055", "A-057", "A-058"}
    arows = [r_ for r_ in assumptions_rows() if r_[0] in pri]
    r.section("Key assumptions and limitations", H.table(["ID", "Assumption", "Effect", "Status"], arows),
              H.p("The full register is in the project file set. NCM demand profiles differ from the project's hourly loads: "
                  "these seasonal efficiencies are inputs to SBEM, and SBEM applies them to its own demands.", "note"))
    r.section("Post-construction monitoring", H.p(
        "Proposed metering to verify these figures in operation (GLA 'be seen'): heat meters on LTHW, DHW (primary and "
        "preheat) and CHW outputs; electricity sub-meters on each heat-pump and chiller group, the roof heat-source and "
        "rejection plant, and the loop and circuit pumps; temperature logging of loop flow/return, buffer top/bottom and "
        "DHW store."))
    r.section("Record", H.table(["Item", "Value"], [["Prepared by", ""], ["Checked by", ""], ["Revision / date", ""],
                                                   ["Run reference", manifest.get("config_hash", "")],
                                                   ["Software", f"Milieu ambient-loop toolkit {manifest.get('code_version', '')} "
                                                                f"({manifest.get('git_sha', '')})"]]))
    return r.render()
