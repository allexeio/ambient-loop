"""Concise options report and GLA calculation summary.

1. Concise report (2–3 pages): loads, the arrangement ladder from basic to complex, the
   recommendation with reasons, drivers and risks, and the schematic.
2. Calculation summary (for an Energy Statement appendix). It substantiates the heat pump
   seasonal efficiencies entered in the Part L 2021 / SBEM model (BRUKL) for the proposed
   system. Sections: values to enter; system and plant data with sources; method (8,760 h,
   weather, loads, standards); heat-source and distribution temperatures; annual and monthly
   results; sensitivity; assumptions; metering for post-construction monitoring; sign-off.

Boundary [A-057, revised after reading the guidance]. GLA para 10.8 asks for the specification
of the entire heating system: source temperature, flow temperature, distribution losses and
pumping energy. The values for modelling are therefore whole-system, all pumps included.
Generation-side values (distribution pumps excluded) are given for the SBEM modeller, because
SBEM also calculates auxiliary pump energy.

Allocation [A-058]: incremental by default. Cooling carries its stand-alone electricity and
heat recovery is credited to the heat it serves. Pro-rata is shown alongside. The NCM demand
profiles differ from the project's hourly loads, so the seasonal efficiencies here are applied
to SBEM's own demands; the electricity totals will not reconcile one to one.
GLA requirements: Energy Assessment Guidance, June 2022 (supplied 17 Nov 2026), paras 7.9 and
footnote 20 (Part L notional heat pump: SCOP 2.64 space heating, 2.86 hot water), 7.11, 7.16–7.19,
9.9–9.10, 10.6–10.9. The summary follows the para 10.9 list item by item.
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

SERVICE_LABEL = {"LTHW": ("Space heating", "SCOP"), "DHW": ("Domestic hot water", "SPF"),
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
            vals.append({k: v["ratio"] for k, v in service_ratios(cc, "conservative", exclude_distribution=False).items()})
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


GLA_REF = "GLA Energy Assessment Guidance, June 2022"
NOTIONAL = {"LTHW": 2.64, "DHW": 2.86}  # GLA guidance footnote 20 (para 7.9): Part L 2021 notional heat pump values


def _cop_at(spec_model: Any, src: float, snk: float) -> tuple[float, str]:
    from ambient_loop.physics.library import build_model
    m = build_model(spec_model)
    cop, ex = m.cop_h(np.array([src]), np.array([snk]))
    return float(cop[0]), m.tag.label + (" (extrapolated)" if bool(ex[0]) else "")


def _bins(ctx: ProjectContext, c: CaseResult, alloc: dict[str, np.ndarray]) -> list[list[str]]:
    tdb = ctx.inputs.dry_bulb_C
    svc = {s.id: s.service_name for s in ctx.project.streams}
    q = {s: np.sum([v for k, v in ctx.inputs.demand_kW.items() if svc[k] == s], axis=0) for s in set(svc.values())}
    edges = [-np.inf, -2, 2, 6, 10, 14, 18, 22, 26, np.inf]
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (tdb > lo) & (tdb <= hi)
        if not m.any():
            continue
        lab = f"≤ {hi:g}" if lo == -np.inf else (f"> {lo:g}" if hi == np.inf else f"{lo:g} to {hi:g}")
        row = [lab, f"{int(m.sum()):,}"]
        for s in ("LTHW", "DHW", "CHW"):
            if s in alloc and s in q:
                qq, ee = float(q[s][m].sum()) / 1e3, float(alloc[s][m].sum()) / 1e3
                row.append(f"{qq:,.1f} / {qq / ee:.2f}" if ee > 0 else f"{qq:,.1f} / –")
        rows.append(row)
    return rows


def calc_summary(ctx: ProjectContext, c: CaseResult, sens: list[Sens], meta: list[str], manifest: dict[str, str],
                 weather_label: str = "", carbon: float = 0.136, tariff: float = 0.25) -> str:
    """Heat pump calculation summary structured on GLA Energy Assessment Guidance (June 2022) paras 10.8-10.9."""
    p = ctx.project
    a = c.arrangement
    sysb = service_ratios(c, "conservative", exclude_distribution=False)   # whole heating system (para 10.8)
    gen = service_ratios(c, "conservative", exclude_distribution=True)     # generation side (information)
    inc = service_ratios(c, "incremental", exclude_distribution=False)
    pr = service_ratios(c, "pro_rata", exclude_distribution=False)
    labels = {"LTHW": ("Space heating", "SCOP"), "DHW": ("Domestic hot water", "SPF"), "CHW": ("Space cooling", "SEER")}
    r = H.Report(title=f"{p.name}: heat pump seasonal performance",
                 eyebrow="Energy Statement appendix · heat pump calculation summary (GLA guidance para 10.9)",
                 lead=(f"Seasonal performance of the proposed system, {a.id}: {a.title}, for use in the Part L 2021 energy "
                       "modelling and the GLA carbon emissions reporting spreadsheet. A dynamic calculation over a full year "
                       "(8,760 hours), with source temperatures varying hour by hour and the design sink temperatures for "
                       f"space heating and hot water, as required by {GLA_REF}, para 10.9."),
                 boundary=("Whole heating system (para 10.8): heat pumps and chillers, shared heat sources and rejection plant, "
                           "all pumps including distribution pumping, and thermal store standing losses. Heat-recovery "
                           "compressor energy is charged in full to the heat it serves and also kept in the cooling SEER "
                           "(conservative; no benefit claimed twice)."), meta=meta)
    # 1. values
    rows = []
    for s, (label, metric) in labels.items():
        if s not in sysb:
            continue
        lo = min([x.low.get(s, np.nan) for x in sens] + [x.high.get(s, np.nan) for x in sens]) if sens else np.nan
        hi = max([x.low.get(s, np.nan) for x in sens] + [x.high.get(s, np.nan) for x in sens]) if sens else np.nan
        rec = np.floor(sysb[s]["ratio"] * 20) / 20
        note = f"Part L notional {NOTIONAL[s]:.2f}" if s in NOTIONAL else ""
        rows.append([label, metric, f"{rec:.2f}", f"{sysb[s]['ratio']:.2f}", f"{lo:.2f}–{hi:.2f}", note])
    r.section("Seasonal performance values for the energy modelling", '<div class="box big">' + H.table(
        ["Service", "Metric", "Use in modelling", "Calculated", "Sensitivity range", "Comparison"], rows, numeric=[2, 3, 4]) +
        H.p("'Use in modelling' is the whole-system value rounded down to the nearest 0.05. The comparison column gives the "
            "Part L 2021 notional heat pump values quoted in the GLA guidance (footnote 20 to para 7.9); savings above notional "
            "are reported under 'be green' (para 10.6). Generation-side values (distribution pumps excluded) are in §5 for the "
            "SBEM modeller, because SBEM also calculates auxiliary pump energy; agree the treatment so pumping is neither "
            "omitted nor double counted.", "note") + "</div>")
    # 2. system
    plant = []
    design = []

    def mdl(spec: Any) -> str:
        return spec.model.library or spec.model.type or ""
    lthw_f = a.lthw.flow_C if a.lthw else 45.0
    lr = float(a.loop.return_C) if a.loop else None
    lf = float(a.loop.flow_C) if a.loop else None
    tdes = float(np.percentile(ctx.inputs.dry_bulb_C, 1))
    if a.cooling:
        plant.append(["Cooling / heat recovery", f"{a.cooling.count} × {a.cooling.unit_kW:,.0f} kW", mdl(a.cooling),
                      "air-cooled" if a.cooling.sink == "air" else "rejects to ambient loop"])
        snk = 30.0 if a.cooling.sink == "air" else (lf or 25.0)
        cop, tag = _cop_at(a.cooling.model, a.chw.flow_C if a.chw else 7.0, snk)
        design.append(["Cooling (reject mode)", f"CHW {a.chw.flow_C if a.chw else 7:g} °C / sink {snk:g} °C", f"EER {cop - 1:.2f}", tag])
        if a.cooling.hr_target != "none":
            hrf = a.hr.flow_C if a.hr else lthw_f
            cop, tag = _cop_at(a.cooling.hr_model or a.cooling.model, a.chw.flow_C if a.chw else 7.0, hrf)
            design.append(["Cooling (heat-recovery mode)", f"CHW {a.chw.flow_C if a.chw else 7:g} °C / condensing {hrf:g} °C",
                           f"COP_h {cop:.2f} (EER {cop - 1:.2f})", tag])
    hp = a.heating or (a.cooling if a.cooling and a.cooling.shared_with_heating else None)
    if hp is not None:
        air = getattr(hp, "source", "loop") == "air" or (a.cooling is hp and a.cooling.sink == "air")
        plant.append(["Space heating", f"{hp.count} × {hp.unit_kW:,.0f} kW", mdl(hp), "outdoor air" if air else "ambient loop"])
        src = tdes if air else (lr or 20.0)
        cop, tag = _cop_at(hp.model, src, lthw_f)
        design.append(["Space heating", f"source {src:.1f} °C / flow {lthw_f:g} °C", f"COP {cop:.2f}", tag])
    if a.dhw:
        g = a.dhw.generator
        plant.append(["DHW generation", f"{g.count} × {g.unit_kW:,.0f} kW", mdl(g), g.source])
        gf = a.dhw.generator_flow_C or a.dhw.store_C
        if g.type in ("wshp", "co2"):
            src = tdes if g.source == "air" else (lr or 20.0)
            from ambient_loop.physics.library import build_model
            m = build_model(g.model)
            ccop, ex = m.cop_h(np.array([src]), np.array([gf]), np.array([10.0]))
            design.append(["DHW", f"source {src:.1f} °C / flow {gf:g} °C" + (" / inlet 10 °C" if g.type == "co2" else ""),
                           f"COP {float(ccop[0]):.2f}", m.tag.label + (" (extrapolated)" if bool(ex[0]) else "")])
        if a.dhw.reheat:
            plant.append(["DHW circulation reheat", f"{a.dhw.reheat.count} × {a.dhw.reheat.unit_kW:,.0f} kW", mdl(a.dhw.reheat), a.dhw.reheat.source])
    for bb in a.deficit + a.surplus:
        if bb.fleet:
            plant.append([bb.type.replace("_", " "), f"{bb.fleet.count} × {bb.fleet.unit_kW:,.0f} kW", mdl(bb.fleet), "outdoor air"])
            if bb.type == "ashp":
                cop, tag = _cop_at(bb.fleet.model, tdes, lf or 25.0)
                design.append(["Loop heat source (ASHP)", f"outdoor {tdes:.1f} °C (1 % design) / loop flow {lf or 25:g} °C", f"COP {cop:.2f}", tag])
        elif bb.rated_kW:
            plant.append([bb.type.replace("_", " "), f"{bb.rated_kW:,.0f} kW", "", ""])
    svg, notes = schematic(a)
    need_ds = any("Generic" in d[3] or "Assumption" in d[3] for d in design)
    r.section("Proposed system", f'<div class="chart">{svg}</div>',
              H.table(["Duty", "Plant", "Performance data", "Source / sink"], plant),
              "<h3>Performance at the project's source and sink temperatures</h3>",
              H.table(["Machine / mode", "Conditions", "Full-load performance", "Basis"], design),
              H.p("Para 10.9 asks for manufacturer datasheets showing performance under test conditions at the project's "
                  "specific source and sink temperatures." + (" Rows tagged Generic use a model calibrated to project selection "
                                                               "data; attach the manufacturer datasheets at these conditions before "
                                                               "submission." if need_ds else ""), "note"))
    # 3. method & temperatures
    trows = []
    svc = {s.id: s.service_name for s in p.streams}
    tdb = ctx.inputs.dry_bulb_C
    lrr = c.result.ledger.derived.get("T_loop_return")
    for s, (label, _) in labels.items():
        qs = [v for k, v in ctx.inputs.demand_kW.items() if svc[k] == s]
        if not qs:
            continue
        q = np.sum(qs, axis=0)
        w = q / q.sum() if q.sum() > 0 else q
        if s == "CHW":
            trows.append([label, f"CHW {a.chw.flow_C:g}/{a.chw.return_C:g} °C" if a.chw else "", "",
                          "outdoor air" if (a.cooling and a.cooling.sink == "air") else "ambient loop"])
            continue
        src_air = (s == "LTHW" and hp is not None and (getattr(hp, "source", "") == "air" or (a.cooling is hp and a.cooling.sink == "air"))) or \
                  (s == "DHW" and a.dhw and a.dhw.generator.source == "air")
        ts = float(np.sum(tdb * w)) if src_air else (float(np.sum(lrr * w)) if lrr is not None else float("nan"))
        src = "outdoor air" if src_air else "ambient loop return"
        if a.cooling and a.cooling.hr_target != "none":
            src += f" + recovered heat at {(a.hr.flow_C if a.hr else lthw_f):g} °C"
        sink = (f"{a.lthw.flow_C:g}/{a.lthw.return_C:g} °C" if s == "LTHW" and a.lthw else
                f"store {a.dhw.store_C:g} °C, primary {a.dhw.generator_flow_C or a.dhw.store_C:g} °C" if s == "DHW" and a.dhw else "")
        trows.append([label, src, f"{ts:.1f} °C", sink])
    r.section("Method and temperatures", H.p(
        "Every hour, each machine's COP comes from its performance model at that hour's leaving temperatures. Manufacturer "
        "maps are used where supplied; otherwise a Carnot-referenced model calibrated to project selection data. Part load "
        "and cycling follow EN 14825:2022, with fleet sequencing. Shared heat sources, heat recovery, storage, rejection plant "
        "and pumps are modelled explicitly, and the energy balance closes every hour to 1×10⁻⁶ kWh. Para 10.8 lists the "
        "factors that govern efficiency: building use, source temperature, output (flow) temperature, distribution losses "
        "and pumping energy. All five are represented. Distribution heat losses are included for thermal stores and for "
        "DHW circulation where it is in the load; LTHW pipework losses are not modelled (A-061)."),
        H.table(["Input", "Basis"], [
            ["Weather", weather_label or f"{p.weather.file} (hourly dry bulb)"],
            ["Loads", "; ".join(f"{s.id}: {s.kind.value} [{s.tag.label}]" for s in p.streams)],
            ["DHW profile", f"{c.system.boundary.dhw_profile.value} (peak hour {ctx.dhw_peak:.1%} of daily)"],
            ["Mains water", f"{p.mains_water_C.value if not isinstance(p.mains_water_C, list) else 'monthly profile'} °C"]]),
        "<h3>Source and design sink temperatures</h3>",
        H.table(["Service", "Heat source", "Load-weighted source temperature", "Design sink"], trows),
        "<h3>Hours at each outdoor temperature (para 10.9: 'hours spent under changing source temperatures')</h3>",
        H.table(["Outdoor dry bulb °C", "Hours"] + [lab for s, (lab, _) in labels.items() if s in sysb],
                _bins(ctx, c, conservative_allocation(c)), numeric=[1, 2, 3, 4]),
        H.p("Each cell shows MWh delivered / seasonal ratio within the bin, whole-system conservative basis.", "note"))
    # 4. operation, top-up, DHW, other technologies
    ops = []
    if a.loop:
        ops.append(f"Heat pumps share an ambient loop at {a.loop.flow_C:g}/{a.loop.return_C:g} °C. Heat rejected by cooling is reused in "
                   "the same hour; the balance is supplied by " + (", ".join(b.type.replace('_', ' ') for b in a.deficit) or "nothing")
                   + " and surplus rejected by " + (", ".join(b.type.replace('_', ' ') for b in a.surplus) or "nothing") + ".")
    if a.cooling and a.cooling.hr_target != "none" and a.hr:
        ops.append("Recovered heat priority: " + " → ".join(x.replace("_", " ") for x in a.hr.priority) + ".")
    top = {k: v for k, v in c.flags.items() if ("backup" in k or "immersion" in k) and v}
    led = c.result.ledger
    top_e = sum(float(led.elec[k].sum()) for k in ("lthw_backup", "dhw_immersion", "loop_backup") if k in led.elec) / 1e3
    ops.append("Top-up: electric backup is modelled explicitly and only runs when heat-pump capacity is exceeded"
               + (f": {', '.join(f'{k} {v:,} h' for k, v in top.items())}, {top_e:,.1f} MWh/yr." if top else "; no top-up hours in the year."))
    if a.dhw:
        d = a.dhw
        ops.append(f"Hot water: generated to {d.store_C:g} °C (HSG274 ≥ 60 °C) into {d.calorifier_m3 * 1000:,.0f} L of calorifier "
                   f"storage, which buffers peak draw-off against generator capacity (para 10.9 expects a thermal store)."
                   + (f" The cold feed is preheated to {d.preheat.max_C:g} °C by recovered heat through a plate HX, with no warm "
                      "potable storage." if d.preheat else "")
                   + (" Circulation losses are reheated by a separate unit." if d.reheat else ""))
    if a.buffer:
        ops.append(f"A {a.buffer.volume_m3:g} m³ stratified primary buffer stores recovered heat between hours.")
    r.section("Operation, top-up and hot water", "".join(H.p(x) for x in ops))
    # 5. energy, carbon, cost
    res = []
    heat_e = 0.0
    notional_e = 0.0
    for s, (label, _metric) in labels.items():
        if s in sysb:
            res.append([label, H.fmt(sysb[s]["heat_MWh"], 1), H.fmt(sysb[s]["elec_MWh"], 1), f"{sysb[s]['ratio']:.2f}",
                        f"{gen[s]['ratio']:.2f}", f"{inc[s]['ratio']:.2f}"])
            if s in NOTIONAL:
                heat_e += sysb[s]["elec_MWh"]
                notional_e += sysb[s]["heat_MWh"] / NOTIONAL[s]
    res.append(["Whole system (simulated, all plant and pumps)", H.fmt(sum(v["heat_MWh"] for v in sysb.values()), 1),
                H.fmt(c.electricity_MWh, 1), f"{c.system.value:.2f}", "", ""])
    saving_t = (notional_e - heat_e) * carbon
    heat_q = sum(sysb[s]["heat_MWh"] for s in NOTIONAL if s in sysb)
    cost = heat_e * 1e3 * tariff
    below = [labels[s][0] for s in NOTIONAL if s in sysb and sysb[s]["ratio"] < NOTIONAL[s]]
    warn_html = ""
    if below:
        warn_html = ('<div class="box"><b>Below the Part L notional heat pump: ' + ", ".join(below) + ".</b> "
                     + H.esc("On the whole-system basis these services perform worse than the notional values in footnote 20, "
                             "so the 'be green' saving for them is negative. Review the heat source and flow temperatures, or "
                             "the arrangement, before submission.") + "</div>")
    r.section("Heat, electricity, carbon and cost", H.table(
        ["Service", "Delivered MWh/yr", "Electricity MWh/yr", "Ratio (whole system)", "Generation side",
         "Incremental"], res, numeric=[1, 2, 3, 4, 5]), warn_html,
        H.p("Generation-side and incremental ratios are for information (pro-rata is in the run outputs). Incremental credits near-free recovered heat to "
            "the heat services and can give heating ratios well above any machine's COP; do not use it as a modelling input.", "note"),
        H.kpis([(f"{saving_t:+,.1f}", "tCO₂e/yr for heating and hot water against the Part L notional heat pump "
                 "(2.64 / 2.86); positive = saving, negative = worse than notional"),
                (f"{notional_e - heat_e:+,.0f}", "MWh/yr electricity against notional (positive = saving)"),
                (f"£{cost / 1e3:,.0f}k", f"annual electricity for heating and hot water at £{tariff:.2f}/kWh"),
                (f"{cost / max(heat_q, 1e-9) / 10:,.1f} p", "per kWh of heat delivered")]),
        H.p(f"Carbon at {carbon} kgCO₂e/kWh (SAP 10.2, as used in the GLA carbon emissions reporting spreadsheet; confirm the "
            "factor set required at submission). The cost figure covers fuel only: para 7.19 also asks for taxes and CCL, "
            "incentives, plant replacement, overheads and maintenance, which the applicant should add.", "note"))
    # 6. sensitivity
    srows = []
    for x in sens:
        row = [x.name]
        for s in ("LTHW", "DHW", "CHW"):
            if s in sysb:
                row.append(f"{x.low.get(s, float('nan')):.2f} / {x.high.get(s, float('nan')):.2f}")
        srows.append(row)
    mon = monthly_ratios(c, ctx)
    mrows = []
    for i, mname in enumerate(H.MONTHS):
        row = [mname]
        for s in ("LTHW", "DHW", "CHW"):
            if s in mon:
                q_, e_ = mon[s][i]
                row.append(f"{q_:,.1f} / {q_ / e_:.2f}" if e_ > 0 else f"{q_:,.1f} / –")
        mrows.append(row)
    r.section("Sensitivity and monthly profile",
              H.table(["Driver (low / high)"] + [s for s in ("LTHW", "DHW", "CHW") if s in sysb], srows, numeric=[1, 2, 3]),
              "<h3>Monthly (MWh delivered / ratio)</h3>",
              H.table(["Month"] + [s for s in ("LTHW", "DHW", "CHW") if s in mon], mrows, numeric=[1, 2, 3]))
    # 7. para 10.9 checklist
    has_g = any(b.type == "ground" for b in a.deficit + a.surplus)
    has_air = any(b.type == "ashp" for b in a.deficit) or (hp is not None and getattr(hp, "source", "") == "air") or \
        (a.dhw is not None and a.dhw.generator.source == "air")
    chk = [
        ["SCOP, SPF and SEER from a dynamic annual calculation, varying source temperatures and design sink temperatures", "§1, §3", H.chip("Provided", "good")],
        ["Assumptions, including hours spent under changing source temperatures", "§3, §6, §8", H.chip("Provided", "good")],
        ["Manufacturer datasheets at the project's source and sink temperatures", "§2", H.chip("Applicant to attach", "warn") if need_ds else H.chip("Provided", "good")],
        ["How the heat pump operates alongside other heating/cooling technologies", "§4", H.chip("Provided", "good")],
        ["Top-up technology at peak, included in the modelling", "§4", H.chip("Provided", "good")],
        ["Approach to DHW generation, with an integrated thermal store and its operation", "§4", H.chip("Provided", "good")],
        ["CO₂ savings expected from the technology", "§5", H.chip("Provided", "good")],
        ["Estimate of heating costs to occupants", "§5 (fuel only)", H.chip("Applicant to complete (para 7.19)", "warn")],
        ["Heating/cooling energy provided and electricity required", "§5", H.chip("Provided", "good")],
        ["GSHP: land area for boreholes, ground conditions; open loop flow rate", "§2" if has_g else "n/a",
         H.chip("Applicant to provide", "warn") if has_g else H.chip("Not applicable", "info")],
        ["Diagram of heat pump and condenser locations (and future DHN pipework in HNPAs)", "§2 schematic is a principle only",
         H.chip("Applicant to provide", "warn")],
        ["ASHP: ECA product criteria and MCS product certification evidence", "", H.chip("Applicant to provide", "warn") if has_air else H.chip("Not applicable", "info")],
        ["GSHP: geology suitability and Environment Agency permit likelihood", "", H.chip("Applicant to provide", "warn") if has_g else H.chip("Not applicable", "info")],
        ["End-user information to control and operate the system", "", H.chip("Applicant to confirm", "warn")],
        ["Commitment to post-construction monitoring ('be seen')", "§9", H.chip("Metering proposed; applicant to commit", "warn")],
    ]
    r.section("GLA guidance para 10.9: information checklist", H.table(["Requirement (para 10.9)", "Where", "Status"], chk, raw=True),
              H.p("Also relevant: para 10.6 (heat pump savings reported under 'be green' unless serving a DHN); para 9.9 (ambient "
                  "loops with heat pumps in individual units are treated as individual systems in HNPAs); para 9.10 (direct "
                  "electric heating not accepted in most cases); para 7.11 (consider wastewater heat recovery where DHW demand "
                  "is high).", "note"))
    from ambient_loop.reporting.scenario_report import assumptions_rows
    pri = {"A-040", "A-014", "A-019", "A-020", "A-023", "A-033", "A-052", "A-055", "A-057", "A-058", "A-061"}
    r.section("Key assumptions and limitations", H.table(["ID", "Assumption", "Effect", "Status"],
                                                         [x for x in assumptions_rows() if x[0] in pri]),
              H.p("The modelling software applies these seasonal values to its own NCM demands, which differ from the hourly loads "
                  "used here. The electricity totals therefore will not reconcile one to one.", "note"))
    r.section("Post-construction monitoring ('be seen')", H.p(
        "Proposed metering to verify these figures in operation: heat meters on the LTHW, DHW (primary and preheat) and CHW "
        "outputs; electricity sub-meters on each heat-pump and chiller group, the heat-source and rejection plant, and the loop "
        "and circuit pumps; temperature logging of loop flow/return, buffer top/bottom and DHW store. Report through the "
        "Mayor's post-construction monitoring platform."))
    r.section("Record", H.table(["Item", "Value"], [["Prepared by", ""], ["Checked by", ""], ["Revision / date", ""],
                                                   ["Run reference", manifest.get("config_hash", "")],
                                                   ["Software", f"Milieu ambient-loop toolkit {manifest.get('code_version', '')} "
                                                                f"({manifest.get('git_sha', '')})"],
                                                   ["Guidance", f"{GLA_REF}, paras 7.9 (fn 20), 7.11, 7.16–7.19, 9.9–9.10, 10.6–10.9"]]))
    return r.render()
