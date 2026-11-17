"""Results of a full-mode run: allocation under each convention, KPIs, flags."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ambient_loop.core.metric import Allocation, Boundary, DhwProfile, Metric, Mode, ratio
from ambient_loop.core.provenance import Tag
from ambient_loop.reporting.allocation import Allocated, allocate_incremental, allocate_pro_rata
from ambient_loop.schema.arrangement import Arrangement
from ambient_loop.schema.project import Project
from ambient_loop.sim.full_mode import FullInputs, FullResult, run_arrangement


@dataclass
class CaseResult:
    arrangement: Arrangement
    result: FullResult
    demand_MWh: dict[str, float]
    electricity_MWh: float
    elec_by_component_MWh: dict[str, float]
    system: Metric
    per_service: dict[str, dict[str, Metric]]
    allocations: dict[str, Allocated]
    flags: dict[str, int]
    envelope_hours: dict[str, int]
    pumps_MWh: float
    monthly_elec_MWh: list[float] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)  # rules engine output

    def row(self) -> dict[str, Any]:
        r: dict[str, Any] = {"id": self.arrangement.id, "title": self.arrangement.title,
                             "electricity_MWh": self.electricity_MWh, "system_ratio": self.system.value,
                             "pumps_MWh": self.pumps_MWh}
        for conv, ms in self.per_service.items():
            for s, m in ms.items():
                r[f"{s} {conv}"] = m.value
        return r


def run_case(project: Project, arr: Arrangement, inp: FullInputs, dhw_profile: DhwProfile) -> CaseResult:
    res = run_arrangement(project, arr, inp)
    iso = run_arrangement(project, arr, inp, isolated=True)
    led = res.ledger
    demand: dict[str, float] = {}
    for s in project.streams:
        demand[s.service_name] = demand.get(s.service_name, 0.0) + float(inp.demand_kW[s.id].sum())
    pr = allocate_pro_rata(project, led)
    iso_a = allocate_pro_rata(project, iso.ledger)
    inc = allocate_incremental(project, led, pr, iso_a)
    tags: set[Tag] = set()
    for info in led.components.values():
        tags |= info.tags
    ft = frozenset(tags)

    def b(a: Allocation) -> Boundary:
        return Boundary(pumps_included=arr.pumps_included, allocation=a, dhw_profile=dhw_profile, mode=Mode.FULL)

    tot = float(led.total_elec().sum())
    system = ratio("Combined (heat + coolth) / electricity", sum(demand.values()), tot, b(Allocation.SYSTEM), ft)
    per: dict[str, dict[str, Metric]] = {}
    for label, a, conv in (("pro_rata", pr, Allocation.PRO_RATA), ("incremental", inc, Allocation.INCREMENTAL),
                           ("isolated", iso_a, Allocation.ISOLATED)):
        per[label] = {s: ratio(f"{s} ({label})", demand.get(s, 0.0), e, b(conv), ft) for s, e in a.annual().items()}
    from ambient_loop.core.series import monthly_sum
    return CaseResult(arr, res, {k: v / 1e3 for k, v in demand.items()}, tot / 1e3,
                      {c: float(w.sum()) / 1e3 for c, w in led.elec.items() if w.sum() > 0},
                      system, per, {"pro_rata": pr, "incremental": inc, "isolated": iso_a}, res.flags,
                      res.envelope_hours, sum(res.pumps_kWh.values()) / 1e3,
                      list(monthly_sum(led.total_elec()) / 1e3))


def summary_table(cases: list[CaseResult]) -> list[dict[str, Any]]:
    return [c.row() for c in sorted(cases, key=lambda c: c.electricity_MWh)]


def _unused(x: np.ndarray) -> None:  # keep numpy import for type users
    del x
