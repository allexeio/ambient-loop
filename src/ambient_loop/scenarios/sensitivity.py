"""Sensitivity: tornado on key assumptions, and optional Monte Carlo.

Each driver perturbs the site basis, the arrangement or the inputs, low and high. The tornado
shows the change in annual electricity against the central case. Monte Carlo samples drivers
independently (uniform within ± range) and reports P10/P50/P90. [A-050: ranges are engineering
judgement, listed in DRIVERS; independent sampling ignores correlations.]
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np

from ambient_loop.arrangements.archetypes import ARCHETYPES, SiteBasis
from ambient_loop.physics.library import library_index
from ambient_loop.schema.arrangement import Arrangement, ModelSpec
from ambient_loop.scenarios.run import ProjectContext
from ambient_loop.sim.full_mode import FullInputs, run_arrangement


@dataclass(frozen=True)
class Driver:
    name: str
    low: float
    high: float
    apply: Callable[[SiteBasis, FullInputs, float, dict[str, str]], tuple[SiteBasis, FullInputs]]
    unit: str = ""


def _scale_eta(field: str) -> Callable[[SiteBasis, FullInputs, float, dict[str, str]], tuple[SiteBasis, FullInputs]]:
    def f(s: SiteBasis, i: FullInputs, x: float, svc: dict[str, str]) -> tuple[SiteBasis, FullInputs]:
        spec: ModelSpec = getattr(s, field)
        if spec.library and library_index()[spec.library].get("eta"):
            eta = library_index()[spec.library]["eta"] * x
            spec = spec.model_copy(update={"params": {**spec.params, "eta": eta}})
            return s.model_copy(update={field: spec}), i
        return s, i
    return f


def _scale_demand(service: str) -> Callable[[SiteBasis, FullInputs, float, dict[str, str]], tuple[SiteBasis, FullInputs]]:
    def f(s: SiteBasis, i: FullInputs, x: float, svc: dict[str, str]) -> tuple[SiteBasis, FullInputs]:
        d = {k: (v * x if svc.get(k) == service else v) for k, v in i.demand_kW.items()}
        return s, replace(i, demand_kW=d)
    return f


def _mains(s: SiteBasis, i: FullInputs, x: float, svc: dict[str, str]) -> tuple[SiteBasis, FullInputs]:
    return s, replace(i, mains_C=i.mains_C + x)


def _loop_T(s: SiteBasis, i: FullInputs, x: float, svc: dict[str, str]) -> tuple[SiteBasis, FullInputs]:
    return s.model_copy(update={"loop_flow_C": s.loop_flow_C + x, "loop_return_C": s.loop_return_C + x}), i


def _pump_head(s: SiteBasis, i: FullInputs, x: float, svc: dict[str, str]) -> tuple[SiteBasis, FullInputs]:
    return s.model_copy(update={"loop_pump_kPa": s.loop_pump_kPa * x}), i


def _weather(s: SiteBasis, i: FullInputs, x: float, svc: dict[str, str]) -> tuple[SiteBasis, FullInputs]:
    return s, replace(i, dry_bulb_C=i.dry_bulb_C + x)


DRIVERS = [
    Driver("WSHP / chiller η (±10 %)", 0.9, 1.1, _scale_eta("cooling_model")),
    Driver("DHW heat pump η (±10 %)", 0.9, 1.1, _scale_eta("dhw_model")),
    Driver("DHW demand (±20 %)", 0.8, 1.2, _scale_demand("DHW")),
    Driver("Cooling demand (±20 %)", 0.8, 1.2, _scale_demand("CHW")),
    Driver("Space heating demand (±20 %)", 0.8, 1.2, _scale_demand("LTHW")),
    Driver("Mains water temperature (±3 K)", -3.0, 3.0, _mains, "K"),
    Driver("Loop temperature (±2 K)", -2.0, 2.0, _loop_T, "K"),
    Driver("Loop pump head (±30 %)", 0.7, 1.3, _pump_head),
    Driver("Outdoor temperature (±1.5 K, climate)", -1.5, 1.5, _weather, "K"),
]


def _arr(aid: str, site: SiteBasis, custom: Arrangement | None) -> Arrangement:
    return custom if custom is not None else ARCHETYPES[aid](site)


def tornado(ctx: ProjectContext, aid: str, custom: Arrangement | None = None,
            drivers: list[Driver] | None = None) -> tuple[float, list[tuple[str, float, float]]]:
    drivers = drivers or DRIVERS
    svc = {st.id: st.service_name for st in ctx.project.streams}
    base = float(run_arrangement(ctx.project, _arr(aid, ctx.site, custom), ctx.inputs).ledger.total_elec().sum()) / 1e3
    out = []
    for d in drivers:
        vals = []
        for x in (d.low, d.high):
            s, i = d.apply(ctx.site, ctx.inputs, x, svc)
            a = _arr(aid, s, custom)
            vals.append(float(run_arrangement(ctx.project, a, i).ledger.total_elec().sum()) / 1e3 - base)
        out.append((d.name, vals[0], vals[1]))
    out.sort(key=lambda r: -max(abs(r[1]), abs(r[2])))
    return base, out


def monte_carlo(ctx: ProjectContext, aid: str, n: int = 40, seed: int = 7,
                drivers: list[Driver] | None = None) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    drivers = drivers or DRIVERS
    svc = {st.id: st.service_name for st in ctx.project.streams}
    res = []
    for _ in range(n):
        s, i = ctx.site, ctx.inputs
        for d in drivers:
            s, i = d.apply(s, i, float(rng.uniform(d.low, d.high)), svc)
        res.append(float(run_arrangement(ctx.project, ARCHETYPES[aid](s), i).ledger.total_elec().sum()) / 1e3)
    a = np.array(res)
    return {"P10": float(np.percentile(a, 10)), "P50": float(np.percentile(a, 50)), "P90": float(np.percentile(a, 90)),
            "mean": float(a.mean()), "n": n}
