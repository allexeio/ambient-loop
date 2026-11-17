"""Parameter sweeps and optimisation over arrangement fields.

Fields are addressed by dotted path on the Arrangement (e.g. "buffer.volume_m3",
"dhw.store_C", "dhw.preheat.max_C", "loop.flow_C", "cooling.count", "loop.fluid"). A sweep
evaluates the Cartesian grid in parallel. The optimiser uses scipy's differential evolution
(Storn & Price 1997) over continuous fields, with the chosen objective plus a penalty for
missed targets.
"""

from __future__ import annotations

import itertools
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import differential_evolution

from ambient_loop.scenarios.economics import Economics, capex
from ambient_loop.scenarios.run import ProjectContext, load_context
from ambient_loop.schema.arrangement import Arrangement
from ambient_loop.sim.full_mode import run_arrangement


def set_path(arr: Arrangement, path: str, value: Any) -> Arrangement:
    d = arr.model_dump()
    node = d
    keys = path.split(".")
    for k in keys[:-1]:
        if node.get(k) is None:
            raise KeyError(f"{arr.id}: '{path}' — '{k}' is not set on this arrangement")
        node = node[k]
    node[keys[-1]] = value
    if path == "loop.flow_C" and d.get("loop"):  # keep the loop ΔT
        d["loop"]["return_C"] = value - (arr.loop.flow_C - arr.loop.return_C)  # type: ignore[union-attr]
    if path == "lthw.flow_C" and d.get("lthw"):
        d["lthw"]["return_C"] = value - (arr.lthw.flow_C - arr.lthw.return_C)  # type: ignore[union-attr]
    d["id"] = f"{arr.id}[{path}={value}]"
    return Arrangement.model_validate(d)


@dataclass
class Point:
    params: dict[str, Any]
    arrangement_id: str
    electricity_MWh: float
    carbon_t: float
    cost_gbp: float
    capex_gbp: float
    system_ratio: float
    backup_hours: int


def quick_eval(ctx: ProjectContext, arr: Arrangement, econ: Economics, params: dict[str, Any]) -> Point:
    res = run_arrangement(ctx.project, arr, ctx.inputs)
    e = res.ledger.total_elec()
    demand = sum(float(v.sum()) for v in ctx.inputs.demand_kW.values())
    cap, _ = capex(arr, econ.rates)
    backup = sum(v for k, v in res.flags.items() if "backup" in k or "immersion" in k or "Unmet" in k)
    return Point(params, arr.id, float(e.sum()) / 1e3, econ.carbon_t(e), econ.cost_gbp(e), cap,
                 demand / float(e.sum()), backup)


_C: ProjectContext | None = None
_E: Economics | None = None


def _init(pf: str, rates: dict[str, float], carbon: float, tariff: float) -> None:
    global _C, _E
    _C = load_context(Path(pf))
    _E = Economics.default(rates, carbon, tariff)


def _job(args: tuple[dict[str, Any], dict[str, Any]]) -> Point:
    arr_d, params = args
    assert _C is not None and _E is not None
    return quick_eval(_C, Arrangement.model_validate(arr_d), _E, params)


def sweep(project_file: Path, base: Arrangement, grid: dict[str, list[Any]], econ_args: tuple[dict[str, float], float, float],
          workers: int | None = None) -> list[Point]:
    keys = list(grid)
    jobs = []
    for combo in itertools.product(*[grid[k] for k in keys]):
        a = base
        for k, v in zip(keys, combo, strict=True):
            a = set_path(a, k, v)
        jobs.append((a.model_dump(), dict(zip(keys, combo, strict=True))))
    workers = workers or min(len(jobs), os.cpu_count() or 1, 8)
    if workers <= 1:
        _init(str(project_file), *econ_args)
        return [_job(j) for j in jobs]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(str(project_file), *econ_args)) as ex:
        return list(ex.map(_job, jobs))


def pareto(points: list[Point], x: str = "capex_gbp", y: str = "carbon_t") -> list[Point]:
    """Non-dominated points, minimising both x and y."""
    pts = sorted(points, key=lambda p: (getattr(p, x), getattr(p, y)))
    front: list[Point] = []
    best = np.inf
    for p in pts:
        if getattr(p, y) < best - 1e-9:
            front.append(p)
            best = getattr(p, y)
    return front


def optimise(ctx: ProjectContext, base: Arrangement, bounds: dict[str, tuple[float, float]], econ: Economics,
             objective: str = "electricity_MWh", maxiter: int = 12, popsize: int = 6, seed: int = 1,
             integer: set[str] | None = None) -> tuple[dict[str, float], Point, list[Point]]:
    """Differential evolution over continuous arrangement fields. Returns (best params, point, history)."""
    keys = list(bounds)
    integer = integer or set()
    hist: list[Point] = []

    def build(x: np.ndarray) -> tuple[Arrangement, dict[str, float]]:
        a = base
        pr: dict[str, float] = {}
        for k, v in zip(keys, x, strict=True):
            val: float = float(round(v)) if k in integer else float(v)
            if k in integer:
                val = int(val)  # type: ignore[assignment]
            a = set_path(a, k, val)
            pr[k] = val
        return a, pr

    def f(x: np.ndarray) -> float:
        a, pr = build(x)
        p = quick_eval(ctx, a, econ, pr)
        hist.append(p)
        pen = 1e3 * p.backup_hours / 8760  # discourage solutions that lean on backup
        return float(getattr(p, objective)) * (1 + pen)

    r = differential_evolution(f, [bounds[k] for k in keys], maxiter=maxiter, popsize=popsize, seed=seed,
                               polish=False, tol=1e-3)
    a, pr = build(r.x)
    return pr, quick_eval(ctx, a, econ, pr), hist
