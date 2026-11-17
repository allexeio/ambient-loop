"""Allocation of electricity to services, plus system-level metrics.

The allocation conventions change the per-service ratios but not the total (brief, rule 13).
Each convention is checked to sum to the system total, hour by hour.

PRO_RATA (the workbook's convention):
  - unit electricity goes to the streams it serves, in proportion to delivered duty
  - deficit-plant electricity (ASHP heating and its listed pumps) is split among heat
    services in proportion to the heat each extracts from the loop in that hour
  - surplus-plant electricity (dry cooler, ASHP cooling and their pumps) is split among
    cooling services in proportion to the heat each rejects to the loop in that hour

INCREMENTAL:
  - cooling services keep their stand-alone ("isolated") electricity: same units, no
    heat reuse, surplus rejected by compressor plant only (no dry cooler)
  - heat services carry the rest: total minus cooling stand-alone, split in proportion to
    their pro-rata electricity
    [Assumption A-010: the brief does not say how to split the remainder between heat services]
  - in an hour with no heat service, cooling carries the actual electricity (there is no heat
    to charge the difference to)

ISOLATED (a reference, not an allocation of this system's electricity): each service run on
its own with no reuse and no dry cooler, i.e. the pro-rata allocation of the
reuse_fraction = 0 run.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ambient_loop.core.metric import Allocation, Boundary, DhwProfile, Metric, Mode, ratio
from ambient_loop.core.provenance import Tag
from ambient_loop.core.series import HOURS, FloatArray
from ambient_loop.schema.project import BalancingKind, Project
from ambient_loop.sim.ledger import Ledger, Role
from ambient_loop.sim.simple_mode import SimpleInputs, run_simple

ALLOC_TOL_KWH = 1e-6


@dataclass
class Allocated:
    convention: Allocation
    by_service_kWh: dict[str, FloatArray]  # hourly electricity per service

    def annual(self) -> dict[str, float]:
        return {k: float(v.sum()) for k, v in self.by_service_kWh.items()}


def _safe_share(part: FloatArray, whole: FloatArray) -> FloatArray:
    return np.divide(part, whole, out=np.zeros(HOURS), where=whole > 0)


def allocate_pro_rata(project: Project, ledger: Ledger) -> Allocated:
    streams = {s.id: s for s in project.streams}
    services = sorted({s.service_name for s in project.streams})
    out = {svc: np.zeros(HOURS) for svc in services}

    heat_ex = {k.split(":", 1)[1]: v for k, v in ledger.derived.items()
               if k.startswith("extraction:")}
    cool_rej = {k.split(":", 1)[1]: v for k, v in ledger.derived.items()
                if k.startswith("rejection:")}
    tot_ex = np.sum(list(heat_ex.values()), axis=0) if heat_ex else np.zeros(HOURS)
    tot_rej = np.sum(list(cool_rej.values()), axis=0) if cool_rej else np.zeros(HOURS)

    for cid, info in ledger.components.items():
        w = ledger.elec[cid]
        if info.service_weights is not None:
            tot = np.sum(list(info.service_weights.values()), axis=0)
            for svc, wt in info.service_weights.items():
                share = _safe_share(wt, tot)
                orphan = np.where(tot > 0, 0.0, 1.0 / len(info.service_weights))
                out.setdefault(svc, np.zeros(HOURS))
                out[svc] += w * (share + orphan)
        elif info.role is Role.UNIT:
            demand = {sid: np.abs(ledger.flow(cid, f"svc:{sid}")) for sid in info.serves}
            total = np.sum(list(demand.values()), axis=0)
            for sid, q in demand.items():
                out[streams[sid].service_name] += w * _safe_share(q, total)
        elif info.role in (Role.DEFICIT, Role.SURPLUS):
            basis, tot = (heat_ex, tot_ex) if info.role is Role.DEFICIT else (cool_rej, tot_rej)
            for sid, e in basis.items():
                out[streams[sid].service_name] += w * _safe_share(e, tot)
            # hours with no basis (e.g. rejecting only pump heat): share equally, stays visible
            orphan = np.where(tot > 0, 0.0, w)
            if np.abs(orphan).max() > 0:
                for svc in services:
                    out[svc] += orphan / len(services)
    _check_sum(out, ledger, "pro_rata")
    return Allocated(Allocation.PRO_RATA, out)


def isolated(project: Project, inputs: SimpleInputs) -> Allocated:
    ref = run_simple(
        project, inputs, reuse_fraction=0.0, exclude_kinds=frozenset({BalancingKind.DRY_COOLER})
    )
    a = allocate_pro_rata(project, ref)
    return Allocated(Allocation.ISOLATED, a.by_service_kWh)


def allocate_incremental(
    project: Project, ledger: Ledger, pro_rata: Allocated, iso: Allocated
) -> Allocated:
    cooling = {s.service_name for s in project.streams if s.kind.is_cooling}
    heating = {s.service_name for s in project.streams} - cooling
    out: dict[str, FloatArray] = {}
    total = ledger.total_elec()
    cool_sum = np.zeros(HOURS)
    for svc in cooling:
        out[svc] = iso.by_service_kWh[svc].copy()
        cool_sum += out[svc]
    remainder = total - cool_sum
    heat_pr = {svc: pro_rata.by_service_kWh[svc] for svc in heating}
    heat_pr_total = np.sum(list(heat_pr.values()), axis=0) if heat_pr else np.zeros(HOURS)
    for svc, w in heat_pr.items():
        out[svc] = remainder * _safe_share(w, heat_pr_total)
    if heating:
        # hours where heat services used nothing: remainder (if any) to cooling, stays visible
        orphan = np.where(heat_pr_total > 0, 0.0, remainder)
        if cooling and np.abs(orphan).max() > 0:
            first = sorted(cooling)[0]
            out[first] = out[first] + orphan
    _check_sum(out, ledger, "incremental")
    return Allocated(Allocation.INCREMENTAL, out)


def _check_sum(out: dict[str, FloatArray], ledger: Ledger, name: str) -> None:
    resid = np.sum(list(out.values()), axis=0) - ledger.total_elec()
    if np.abs(resid).max() > ALLOC_TOL_KWH:
        raise AssertionError(f"{name} allocation does not sum to total: {np.abs(resid).max()}")


# ---- metrics ------------------------------------------------------------------------------
@dataclass
class Summary:
    demand_MWh: dict[str, float]
    electricity_MWh: float
    elec_by_component_MWh: dict[str, float]
    system: Metric
    per_service: dict[str, dict[str, Metric]]  # convention -> service -> metric
    reused_MWh: float
    residual_deficit_MWh: float
    residual_surplus_MWh: float
    unmet_heat_MWh: float
    unmet_reject_MWh: float
    flags_hours: dict[str, int]
    residuals_kWh: dict[str, float]


def summarise(
    project: Project, inputs: SimpleInputs, ledger: Ledger, dhw_profile: DhwProfile
) -> Summary:
    streams = project.streams
    demand: dict[str, float] = {}
    for s in streams:
        demand[s.service_name] = demand.get(s.service_name, 0.0) + float(
            inputs.demand_kW[s.id].sum()
        )
    tags: set[Tag] = set()
    for info in ledger.components.values():
        tags |= info.tags
    ftags = frozenset(tags)
    pumps = project.plant.pumps_included

    def b(alloc: Allocation) -> Boundary:
        return Boundary(pumps_included=pumps, allocation=alloc, dhw_profile=dhw_profile,
                        mode=Mode.SIMPLE)

    total_e = float(ledger.total_elec().sum())
    useful = sum(demand.values())
    system = ratio("Combined (heat + coolth) / electricity", useful, total_e,
                   b(Allocation.SYSTEM), ftags)

    pr = allocate_pro_rata(project, ledger)
    iso = isolated(project, inputs)
    inc = allocate_incremental(project, ledger, pr, iso)
    per: dict[str, dict[str, Metric]] = {}
    for label, alloc, conv in (
        ("pro_rata", pr, Allocation.PRO_RATA),
        ("incremental", inc, Allocation.INCREMENTAL),
        ("isolated", iso, Allocation.ISOLATED),
    ):
        per[label] = {
            svc: ratio(f"{svc} ({label})", demand[svc], e, b(conv), ftags)
            for svc, e in alloc.annual().items()
        }

    return Summary(
        demand_MWh={k: v / 1e3 for k, v in demand.items()},
        electricity_MWh=total_e / 1e3,
        elec_by_component_MWh={c: float(w.sum()) / 1e3 for c, w in ledger.elec.items()},
        system=system,
        per_service=per,
        reused_MWh=float(ledger.derived["loop:reused"].sum()) / 1e3,
        residual_deficit_MWh=float(ledger.derived["loop:residual_deficit"].sum()) / 1e3,
        residual_surplus_MWh=float(ledger.derived["loop:residual_surplus"].sum()) / 1e3,
        unmet_heat_MWh=float(ledger.derived["unmet:heat"].sum()) / 1e3,
        unmet_reject_MWh=float(ledger.derived["unmet:reject"].sum()) / 1e3,
        flags_hours={k: int(v.sum()) for k, v in ledger.flags.items()},
        residuals_kWh=ledger.residuals(),
    )
