"""Compare a simple-mode run with the workbook's own results, annual and hour by hour."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ambient_loop.core.series import FloatArray
from ambient_loop.reporting.allocation import Summary, allocate_pro_rata, isolated
from ambient_loop.schema.project import Project
from ambient_loop.sim.ledger import Ledger
from ambient_loop.sim.simple_mode import LOOP, SimpleInputs

ANNUAL_TOL = 0.005  # brief §9.1: reproduce within 0.5 %


@dataclass
class Check:
    name: str
    ours: float
    theirs: float

    @property
    def rel(self) -> float:
        return (self.ours - self.theirs) / self.theirs if self.theirs else float(
            "inf" if self.ours else 0.0
        )

    @property
    def ok(self) -> bool:
        return abs(self.rel) <= ANNUAL_TOL or abs(self.ours - self.theirs) < 1e-9


@dataclass
class HourlyCheck:
    name: str
    max_abs: float
    annual_ours: float
    annual_theirs: float


def compare(
    project: Project, inputs: SimpleInputs, ledger: Ledger, summary: Summary, ref_dir: Path
) -> tuple[list[Check], list[HourlyCheck]]:
    ref = yaml.safe_load((ref_dir / "reference_results.yaml").read_text())
    pr = summary.per_service["pro_rata"]
    iso = summary.per_service["isolated"]
    checks: list[Check] = []
    for svc, r in ref["services"].items():
        checks += [
            Check(f"{svc} useful MWh", summary.demand_MWh[svc], r["useful_MWh"]),
            Check(f"{svc} allocated electricity MWh (pro rata)",
                  pr[svc].denominator_kWh / 1e3, r["elec_MWh"]),
            Check(f"{svc} ratio (pro rata)", pr[svc].value, r["ratio"]),
            Check(f"{svc} isolated ratio", iso[svc].value, r["isolated_ratio"]),
        ]
    t = ref["totals"]
    checks += [
        Check("Total plant electricity MWh", summary.electricity_MWh, t["electricity_MWh"]),
        Check("Isolated electricity MWh",
              sum(m.denominator_kWh for m in iso.values()) / 1e3, t["isolated_electricity_MWh"]),
        Check("Loop heat reused MWh", summary.reused_MWh, t["reused_MWh"]),
        Check("Residual rooftop heating MWh", summary.residual_deficit_MWh, t["residual_heat_MWh"]),
        Check("Surplus for outdoor rejection MWh", summary.residual_surplus_MWh, t["surplus_MWh"]),
    ]

    href = pd.read_csv(ref_dir / "reference_hourly.csv")
    prh = allocate_pro_rata(project, ledger).by_service_kWh
    isoh = isolated(project, inputs).by_service_kWh
    ours: dict[str, FloatArray] = {
        "reused_kW": ledger.derived["loop:reused"],
        "residual_heat_kW": ledger.derived["loop:residual_deficit"],
        "residual_cool_kW": ledger.derived["loop:residual_surplus"],
        "total_kWe": ledger.total_elec(),
    }
    if ("dry_cooler", LOOP) in ledger.flows:
        ours["dry_cooler_kW"] = -ledger.flow("dry_cooler", LOOP)
    for svc in ("LTHW", "DHW", "CHW"):
        if svc in prh:
            ours[f"{svc}_kWe"] = prh[svc]
            ours[f"{svc}_isolated_kWe"] = isoh[svc]
    hourly = [
        HourlyCheck(k, float(np.max(np.abs(v - href[k].to_numpy()))), float(v.sum()),
                    float(href[k].sum()))
        for k, v in ours.items() if k in href.columns
    ]
    return checks, hourly
