"""Synthetic fixtures: small, fully specified cases with hand-checkable answers."""

from __future__ import annotations

from typing import Any

import numpy as np

from ambient_loop.core.series import HOURS
from ambient_loop.schema.project import Project
from ambient_loop.sim.simple_mode import SimpleInputs


def project(
    *,
    reuse: float = 1.0,
    pumps: bool = False,
    dc_capacity: float | None = 800.0,
    ashp_capacity: float | None = None,
    with_dry_cooler: bool = True,
) -> Project:
    surplus: list[dict[str, Any]] = []
    if with_dry_cooler:
        surplus.append({"id": "dc", "kind": "dry_cooler", "capacity_kW": dc_capacity,
                        "cutoff_dry_bulb_C": 27, "fan_kW_per_kW": 0.1})
    surplus.append({"id": "ashp_c", "kind": "ashp_cooling", "efficiency": 4.0,
                    "pump_kW_per_kW": 0.02})
    raw: dict[str, Any] = {
        "name": "synthetic",
        "loads": {"file": "unused.csv"},
        "weather": {"file": "unused.csv", "format": "table", "dry_bulb_column": "db"},
        "streams": [
            {"id": "lthw", "kind": "space_heating", "duty_kW": {"column": "l"}},
            {"id": "chw", "kind": "space_cooling", "duty_kW": {"column": "c"}},
            {"id": "dhw", "kind": "dhw", "duty_kW": {"column": "d"}},
        ],
        "plant": {
            "reuse_fraction": reuse,
            "pumps_included": pumps,
            "units": [
                {"id": "hp_l", "kind": "heat_pump", "serves": ["lthw"], "efficiency": 5.0},
                {"id": "ch", "kind": "chiller", "serves": ["chw"], "efficiency": 6.0},
                {"id": "hp_d", "kind": "heat_pump", "serves": ["dhw"], "efficiency": 3.0},
            ],
            "deficit_plant": [
                {"id": "ashp_h", "kind": "ashp_heating", "capacity_kW": ashp_capacity,
                 "efficiency_curve": {"x": [-5, 10], "y": [3.0, 4.5]},
                 "pump_kW_per_kW": 0.015},
            ],
            "surplus_plant": surplus,
        },
    }
    return Project.model_validate(raw)


def inputs(lthw: Any = 0.0, chw: Any = 0.0, dhw: Any = 0.0, db: Any = 10.0) -> SimpleInputs:
    def h(x: Any) -> np.ndarray:
        return np.broadcast_to(np.asarray(x, dtype=float), (HOURS,)).copy()

    return SimpleInputs({"lthw": h(lthw), "chw": h(chw), "dhw": h(dhw)}, h(db))


def out_of_phase() -> SimpleInputs:
    """Cooling by day, DHW by night: same daily totals, no same-hour overlap."""
    day = np.tile((np.arange(24) >= 8) & (np.arange(24) < 20), 365)
    return inputs(chw=np.where(day, 100.0, 0.0), dhw=np.where(day, 0.0, 100.0), db=15.0)
