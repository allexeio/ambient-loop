"""Walkthrough analysis pipeline: model build, step 4 screening, temperature sweep, step 5 comparison."""

from __future__ import annotations

import math
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ambient_loop.arrangements.archetypes import SiteBasis
from ambient_loop.io.new_project import create_project
from ambient_loop.reporting.allocation import allocate_pro_rata
from ambient_loop.scenarios.run import ProjectContext, load_context
from ambient_loop.schema.arrangement import Arrangement
from ambient_loop.sim.full_mode import run_arrangement
from ambient_loop.studio import catalogue as C
from ambient_loop.studio.building import Building, synthesise_loads
from ambient_loop.studio.data import load_weather, rates
from ambient_loop.studio.terminals import TerminalChoice, all_refrigerant, circuit_temperatures, needs_simultaneous


def build_model(b: Building, choices: list[TerminalChoice], weather_id: str, out: Path,
                hourly: dict[str, np.ndarray] | None = None) -> Path:
    db, wb, label = load_weather(weather_id)
    loads = hourly or synthesise_loads(b, db)
    temps = circuit_temperatures(choices)
    p = create_project(out, b.name, loads, db, description=f"{b.location}; weather {label}", dhw_store_C=b.dhw_store_C,
                       load_tag="project_data" if hourly else "assumption",
                       site={**temps, "circulation_fraction": b.circulation_fraction},
                       temps={"LTHW": {"T_in_C": temps.get("lthw_return_C", 40.0), "T_out_C": temps.get("lthw_flow_C", 45.0)},
                              "CHW": {"T_in_C": temps.get("chw_return_C", 16.0), "T_out_C": temps.get("chw_flow_C", 10.0)}})
    if wb is not None:
        w = pd.read_csv(out / "weather.csv")
        w["wet_bulb_C"] = wb
        w.to_csv(out / "weather.csv", index=False)
    return p


@dataclass
class Screen:
    id: str
    title: str
    compatible: bool
    notes: list[str]
    electricity_MWh: float
    gas_MWh: float
    carbon_t: float
    opex_gbp: float
    capex_gbp: float
    roof_m2: float
    plantroom_m2: float
    system_eff: float
    service_eff: dict[str, float]
    pumps_MWh: float
    flags: dict[str, int]
    strategy: dict[str, str] = field(default_factory=dict)


def gas_kWh(led: Any) -> np.ndarray:
    d = led.derived
    return d.get("o:gas_L", 0) + d.get("o:gas_D", 0)


def plant_space(arr: Arrangement, r: dict[str, Any]) -> tuple[float, float]:
    """(roof m², plant room m²) from the space factors [A-067]."""
    sp = r["space"]
    roof = room = 0.0
    if arr.cooling:
        cap = arr.cooling.count * arr.cooling.unit_kW * 0.78
        if arr.strategy.get("cool") == "vrf":
            roof += cap * sp["vrf_condenser_m2_per_kW"]
        elif arr.cooling.sink == "air":
            roof += cap * sp["roof_air_source_m2_per_kW"]
        else:
            room += cap * (sp["plantroom_chiller_m2_per_kW"] if arr.loop and arr.strategy.get("cool", "").startswith("wcc")
                           else sp["plantroom_hp_m2_per_kW"])
    if arr.heating:
        cap = arr.heating.count * arr.heating.unit_kW
        if arr.heating.source == "gas":
            room += cap * sp["plantroom_boiler_m2_per_kW"]
        elif arr.heating.source == "air" and arr.strategy.get("heat") != "vrf":
            roof += cap * sp["roof_air_source_m2_per_kW"]
        elif arr.heating.source != "air":
            room += cap * sp["plantroom_hp_m2_per_kW"]
    if arr.dhw:
        g = arr.dhw.generator
        if g.type == "co2":
            roof += g.count * sp["co2_unit_m2_each"]
        elif g.type == "gas":
            room += g.count * g.unit_kW * sp["plantroom_boiler_m2_per_kW"]
        elif g.type == "wshp" and g.source == "air":
            roof += g.count * g.unit_kW * sp["roof_air_source_m2_per_kW"]
        elif g.type != "electric":
            room += g.count * g.unit_kW * sp["plantroom_hp_m2_per_kW"]
        room += _tank_m2(arr.dhw.calorifier_m3, sp)
    if arr.buffer:
        room += _tank_m2(arr.buffer.volume_m3, sp)
    for b in arr.surplus + arr.deficit:
        if b.type == "dry_cooler" and b.rated_kW:
            roof += b.rated_kW * sp["roof_dry_cooler_m2_per_kW"]
        elif b.type == "tower" and b.rated_kW:
            roof += b.rated_kW * sp["roof_tower_m2_per_kW"]
        elif b.fleet:
            roof += b.fleet.count * b.fleet.unit_kW * sp["roof_air_source_m2_per_kW"] * (0.78 if b.type == "ashp_cooling" else 1.0) * 0.5
    return roof, room


def _tank_m2(v: float, sp: dict[str, float]) -> float:
    if v <= 0:
        return 0.0
    d = (4 * v / (math.pi * sp["tank_height_to_diameter"])) ** (1 / 3)
    return (d + 2 * sp["tank_access_m"]) ** 2


def capex_gbp(arr: Arrangement, r: dict[str, Any], gia: float) -> float:
    c = r["capex"]
    tot = 0.0
    st = arr.strategy
    if arr.cooling:
        cap_c = arr.cooling.count * arr.cooling.unit_kW * 0.78
        key = {"vrf": "vrf_per_kW", "ashp4": "ashp_4pipe_per_kW", "ashp2": "ashp_2pipe_per_kW", "loop": "wshp_per_kW"}.get(
            st.get("cool", ""), "chiller_water_per_kW" if st.get("cool", "").startswith("wcc") else "chiller_air_per_kW")
        tot += cap_c * c[key] if key != "wshp_per_kW" else arr.cooling.count * arr.cooling.unit_kW * c[key]
    if arr.heating and st.get("heat") not in ("vrf", "ashp4", "ashp2"):
        cap_h = arr.heating.count * arr.heating.unit_kW
        tot += cap_h * (c["gas_boiler_per_kW"] if arr.heating.source == "gas" else c["ashp_per_kW"] if arr.heating.source == "air" else c["wshp_per_kW"])
    if arr.dhw:
        g = arr.dhw.generator
        tot += g.count * g.unit_kW * {"co2": c["co2_hp_per_kW"], "gas": c["gas_boiler_per_kW"], "electric": 0.0}.get(
            g.type, c["ashp_ht_per_kW"] if g.source == "air" else c["dhw_hp_per_kW"]) if g.type != "electric" else 0.0
        if arr.dhw.reheat:
            tot += arr.dhw.reheat.count * arr.dhw.reheat.unit_kW * c["ashp_ht_per_kW"]
        if arr.dhw.preheat:
            tot += c["phx_preheat_item"]
        tot += arr.dhw.calorifier_m3 * c["calorifier_per_m3"]
    if arr.buffer:
        tot += arr.buffer.volume_m3 * c["buffer_per_m3"]
    if arr.cooling and arr.cooling.hr_target == "hr_circuit":
        tot += c["hr_circuit_item"]
    if arr.loop and st.get("heat") == "loop":
        tot += gia * c["ambient_loop_per_m2_gia"]
    for b in arr.deficit + arr.surplus:
        if b.type == "ashp" and b.fleet:
            tot += b.fleet.count * b.fleet.unit_kW * c["ashp_per_kW"]
        elif b.type == "ashp_cooling" and b.fleet:
            tot += b.fleet.count * b.fleet.unit_kW * 0.78 * c["chiller_air_per_kW"] * 0.5  # same reversible units as ashp
        elif b.type == "dry_cooler" and b.rated_kW:
            tot += b.rated_kW * c["dry_cooler_per_kW"]
        elif b.type == "tower" and b.rated_kW:
            tot += b.rated_kW * c["tower_per_kW"]
    return tot


def evaluate_screen(ctx: ProjectContext, s: C.Strategy, has_cooling: bool, has_dhw: bool, gia: float) -> Screen:
    r = rates()
    arr = C.build(s, ctx.site, has_cooling, has_dhw)
    res = run_arrangement(ctx.project, arr, ctx.inputs)
    led = res.ledger
    e = led.total_elec()
    g = gas_kWh(led) * np.ones(8760)
    car = r["carbon"]
    op = r["opex"]
    demand = sum(float(v.sum()) for v in ctx.inputs.demand_kW.values())
    pr = allocate_pro_rata(ctx.project, led).by_service_kWh
    svc = {st.id: st.service_name for st in ctx.project.streams}
    q = {sv: sum(float(ctx.inputs.demand_kW[k].sum()) for k, v in svc.items() if v == sv) for sv in set(svc.values())}
    gsv = {"LTHW": float(np.sum(led.derived.get("o:gas_L", 0))), "DHW": float(np.sum(led.derived.get("o:gas_D", 0)))}
    eff = {sv: q[sv] / (float(pr[sv].sum()) + gsv.get(sv, 0.0)) for sv in q if float(pr[sv].sum()) + gsv.get(sv, 0.0) > 0}
    roof, room = plant_space(arr, r)
    cx = capex_gbp(arr, r, gia)
    return Screen(s.id, s.title, s.compatible, s.notes, float(e.sum()) / 1e3, float(g.sum()) / 1e3,
                  (float(e.sum()) * car["electricity"] + float(g.sum()) * car["gas"]) / 1e3,
                  float(e.sum()) * op["electricity_gbp_per_kWh"] + float(g.sum()) * op["gas_gbp_per_kWh"] + cx * op["maintenance_pct_capex"] / 100,
                  cx, roof, room, demand / (float(e.sum()) + float(g.sum())), eff, sum(res.pumps_kWh.values()) / 1e3,
                  {k: v for k, v in res.flags.items() if v and ("backup" in k or "Unmet" in k or "immersion" in k or "Changeover" in k)},
                  {"heat": s.heat, "cool": s.cool, "dhw": s.dhw, "balance": s.bal})


_C: ProjectContext | None = None


def _init(pf: str) -> None:
    global _C
    _C = load_context(Path(pf))


def _job(a: tuple[str, str, str, str, list[str], bool, bool, bool, float]) -> Screen:
    heat, cool, dhw, bal, notes, comp, hc, hd, gia = a
    assert _C is not None
    s = C.Strategy(heat, cool, dhw, bal, notes, comp)
    return evaluate_screen(_C, s, hc, hd, gia)


def flags_for(ctx: ProjectContext) -> tuple[bool, bool]:
    svc = {s.service_name for s in ctx.project.streams}
    return "CHW" in svc, "DHW" in svc


def screen_all(project_file: Path, choices: list[TerminalChoice], gia: float, workers: int | None = None) -> list[Screen]:
    ctx = load_context(project_file)
    hc, hd = flags_for(ctx)
    strategies = C.enumerate_strategies(hc, hd, needs_simultaneous(choices), all_refrigerant(choices))
    jobs = [(s.heat, s.cool, s.dhw, s.bal, s.notes, s.compatible, hc, hd, gia) for s in strategies]
    workers = workers if workers is not None else min(len(jobs), os.cpu_count() or 1, 8)
    if workers <= 1:
        global _C
        _C = ctx
        out = [_job(j) for j in jobs]
    else:
        with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"), initializer=_init, initargs=(str(project_file),)) as ex:
            out = list(ex.map(_job, jobs))
    return sorted(out, key=lambda x: (not x.compatible, x.carbon_t))


@dataclass
class SweepPoint:
    lthw: tuple[float, float]
    chw: tuple[float, float] | None
    electricity_MWh: float
    pumps_MWh: float
    compressor_MWh: float


def temperature_sweep(ctx: ProjectContext, s: C.Strategy, lthw_opts: list[tuple[float, float]],
                      chw_opts: list[tuple[float, float]]) -> list[SweepPoint]:
    hc, hd = flags_for(ctx)
    out = []
    for lt in lthw_opts:
        for ct in (chw_opts or [None]):  # type: ignore[list-item]
            upd: dict[str, float] = {"lthw_flow_C": lt[0], "lthw_return_C": lt[1]}
            if ct:
                upd.update(chw_flow_C=ct[0], chw_return_C=ct[1])
            site: SiteBasis = ctx.site.model_copy(update=upd)
            arr = C.build(s, site, hc, hd)
            res = run_arrangement(ctx.project, arr, ctx.inputs)
            e = float(res.ledger.total_elec().sum()) / 1e3
            p = sum(res.pumps_kWh.values()) / 1e3
            out.append(SweepPoint(lt, ct, e, p, e - p))
    return out
