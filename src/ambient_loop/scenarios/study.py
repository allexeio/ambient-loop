"""End-to-end study: recovery → generator → scenarios → economics → sensitivity → variants → report."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from ambient_loop.arrangements.generator import propose
from ambient_loop.core.run import manifest
from ambient_loop.reporting import html as H
from ambient_loop.reporting.full_summary import CaseResult
from ambient_loop.reporting.scenario_report import scenario_report
from ambient_loop.scenarios.economics import Economics, capex
from ambient_loop.scenarios.run import arrangements_for, load_context, run_many
from ambient_loop.scenarios.sensitivity import tornado
from ambient_loop.scenarios.sets import variants
from ambient_loop.scenarios.sweep import Point, pareto, sweep
from ambient_loop.sim.full_mode import run_arrangement


def run_study(project_file: Path, out: Path | None = None, workers: int | None = None, quick: bool = False,
              carbon: float = 0.136, tariff: float = 0.25) -> dict[str, Any]:
    t0 = time.perf_counter()
    ctx = load_context(project_file)
    p = ctx.project
    svc = {s.id: s.service_name for s in p.streams}

    def tot(service: str) -> np.ndarray:
        arrs = [v for k, v in ctx.inputs.demand_kW.items() if svc[k] == service]
        return np.sum(arrs, axis=0) if arrs else np.zeros(8760)

    if p.archetypes or p.arrangements:
        arrs = arrangements_for(ctx)
        reasons = [(a.id, " ".join(a.why)) for a in arrs]
    else:
        has_ground = bool(p.site.get("ground"))
        has_ext = bool(p.site.get("greywater_column"))
        prop = propose(ctx.site, ctx.recovery, tot("LTHW"), tot("CHW"), tot("DHW"), has_ground, has_ext)
        arrs = [a for a, _ in prop]
        reasons = [(a.id, r) for a, r in prop]
    cases = run_many(project_file, arrs, workers, ctx)
    econ = Economics.default(p.capex, carbon, tariff)
    eco: dict[str, dict[str, Any]] = {}
    for c in cases:
        e = c.result.ledger.total_elec()
        cap, items = capex(c.arrangement, econ.rates)
        eco[c.arrangement.id] = {"carbon_t": econ.carbon_t(e), "cost_gbp": econ.cost_gbp(e), "capex_gbp": cap,
                                 "capex_items": items}
    pts = [(v["capex_gbp"] / 1e3, v["carbon_t"], k) for k, v in eco.items()]
    front = [x.arrangement_id for x in pareto([Point({}, k, 0, v["carbon_t"], 0, v["capex_gbp"], 0, 0)
                                                for k, v in eco.items()])]
    best = min(cases, key=lambda c: c.electricity_MWh)
    base = next((c for c in cases if c.arrangement.id == "A1"), best)
    sections: list[tuple[str, str]] = []
    sections.append(("Why each arrangement was proposed",
                     H.table(["Arrangement", "Reason (from the recovery analysis)"], reasons)))
    eco_rows = [[k, H.fmt(v["carbon_t"], 0), H.fmt(v["cost_gbp"] / 1e3, 0), H.fmt(v["capex_gbp"] / 1e3, 0),
                 "yes" if k in front else ""] for k, v in sorted(eco.items(), key=lambda kv: kv[1]["carbon_t"])]
    sections.append(("Carbon, running cost and capital proxy",
                     H.table(["Arrangement", "tCO₂e/yr", "£k/yr", "Capex proxy £k", "Pareto"], eco_rows, numeric=[1, 2, 3])
                     + H.scatter(pts, "Capital proxy (£k, indicative)", "tCO₂e/yr", front)
                     + H.p(f"Carbon at {carbon} kgCO₂e/kWh (SAP 10.2, A-047); tariff £{tariff}/kWh flat (A-048); capital "
                           "proxy from library/costs/default.yaml, INDICATIVE ONLY (A-046).", "note")))
    if not quick:
        b0, rows = tornado(ctx, "", custom=best.arrangement)
        sections.append((f"Sensitivity: {best.arrangement.id}", H.tornado_chart(b0, rows, "MWh/yr")))
        var = variants(p, ctx.base, ctx.inputs, {"dhw_scale": [0.8, 1.0, 1.2]})
        vrows = []
        for label, inp in var:
            r1 = []
            for c in (base, best):
                led = run_arrangement(p, c.arrangement, inp).ledger
                dem = sum(float(v.sum()) for v in inp.demand_kW.values())
                r1 += [H.fmt(float(led.total_elec().sum()) / 1e3, 0), f"{dem / float(led.total_elec().sum()):.2f}"]
            vrows.append([label] + r1)
        sections.append(("Demand variants (DHW low / central / high)",
                         H.table(["Variant", f"{base.arrangement.id} MWh", "ratio", f"{best.arrangement.id} MWh", "ratio"],
                                 vrows, numeric=[1, 2, 3, 4])))
        hr_case = next((c for c in sorted(cases, key=lambda c: c.electricity_MWh) if c.arrangement.buffer), None)
        if hr_case is not None:
            sw = sweep(project_file, hr_case.arrangement, {"buffer.volume_m3": [0.5, 5.0, 10.0, 20.0, 30.0]},
                       (p.capex, carbon, tariff), workers)
            sections.append((f"Storage sweep: {hr_case.arrangement.id}", H.bar_chart(
                [f"{x.params['buffer.volume_m3']:g} m³" for x in sw], {"Electricity": [x.electricity_MWh for x in sw]},
                "MWh/yr") + H.table(["Buffer m³", "MWh/yr", "Capex proxy £k"],
                                    [[f"{x.params['buffer.volume_m3']:g}", H.fmt(x.electricity_MWh, 1),
                                      H.fmt(x.capex_gbp / 1e3, 0)] for x in sw], numeric=[0, 1, 2])))
    out = out or ctx.base / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    man = manifest(p.model_dump(mode="json"), [project_file, *ctx.files])
    rec = ctx.recovery
    line = (f"Recovery screening: {rec.annual_MWh['Direct (HX only)']:,.0f} MWh/yr direct, "
            f"{rec.annual_MWh['Heat-pump assisted (delivered)']:,.0f} MWh/yr heat-pump assisted, coincidence "
            f"{rec.coincidence:.2f}. Loads: " + ", ".join(f"{k} {v:,.0f} MWh" for k, v in best.demand_MWh.items()) + ".")
    page = scenario_report(p, cases, [f"run {man['config_hash']}", man["timestamp_utc"],
                                      f"code {man['code_version']} ({man['git_sha']})"], line, sections)
    (out / "study.html").write_text(page)
    summary = {"manifest": man, "cases": [c.row() | eco[c.arrangement.id] for c in cases],
               "findings": {c.arrangement.id: [asdict(f) for f in c.findings] for c in cases},
               "pareto": front, "seconds": time.perf_counter() - t0}
    (out / "study.json").write_text(json.dumps(summary, indent=2, default=str))
    return {"cases": cases, "eco": eco, "out": out, "summary": summary, "ctx": ctx}


def _unused(c: CaseResult) -> None:
    del c
