"""Building model for the walkthrough: zones, annual demands, fresh air, and hourly synthesis.

Steps 1–2 capture zones (space type, area, occupancy) and annual demands per zone. Hourly profiles
are synthesised per zone from the space type's profile and the project weather, scaled to the
annual figures, unless the engineer uploads hourly data (e.g. TAS/IES output) [A-055].

Fresh air (step 2):
  included  the heating and cooling demands already include ventilation load
  separate  AHU heating and cooling annual demands are entered separately
  estimate  not included: estimated hourly as Q = ρ c_p V (ΔT)(1 − ε), with V = occupants × L/s
            per person (space-type default), ε = heat-recovery effectiveness, and supply 18 °C
            heating / 16 °C cooling set-points, during occupied hours; sensible only [A-066]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ambient_loop.core.series import HOURS, FloatArray
from ambient_loop.loads.synth import SynthSpec, synthesise
from ambient_loop.studio.terminals import SPACE_TYPES


@dataclass
class Zone:
    name: str
    space_type: str
    area_m2: float
    occupants: float = 0.0
    heating_kWh: float = 0.0
    cooling_kWh: float = 0.0
    dhw_kWh: float = 0.0
    notes: str = ""

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Zone:
        return Zone(str(d.get("name", "Zone")), str(d.get("space_type", "other")), float(d.get("area_m2") or 0),
                    float(d.get("occupants") or 0), float(d.get("heating_kWh") or 0), float(d.get("cooling_kWh") or 0),
                    float(d.get("dhw_kWh") or 0), str(d.get("notes", "")))


@dataclass
class FreshAir:
    mode: str = "included"  # included | separate | estimate
    ahu_heating_kWh: float = 0.0
    ahu_cooling_kWh: float = 0.0
    heat_recovery: float = 0.75


@dataclass
class Building:
    name: str
    location: str
    zones: list[Zone]
    fresh_air: FreshAir = field(default_factory=FreshAir)
    dhw_store_C: float = 60.0
    circulation_fraction: float = 0.25

    @property
    def gia_m2(self) -> float:
        return sum(z.area_m2 for z in self.zones)


def _occ(profile: str) -> FloatArray:
    from ambient_loop.loads.synth import _tile, building_types
    p = building_types()[profile]
    return _tile(p["occupancy_shape"], float(p["weekend_factor"]), 0)  # type: ignore[arg-type]


def fresh_air_hourly(b: Building, dry_bulb: FloatArray) -> tuple[FloatArray, FloatArray]:
    heat = np.zeros(HOURS)
    cool = np.zeros(HOURS)
    db = np.asarray(dry_bulb, float)
    for z in b.zones:
        st = SPACE_TYPES.get(z.space_type, SPACE_TYPES["other"])
        V = z.occupants * st.fresh_air_ls_per_person / 1000.0  # m³/s
        occ = _occ(st.profile)
        on = occ > 0.3
        rc = 1.2 * 1005.0 * V * (1 - b.fresh_air.heat_recovery) / 1000.0  # kW/K
        heat += np.where(on, rc * np.maximum(0.0, 18.0 - db), 0.0)
        cool += np.where(on, rc * np.maximum(0.0, db - 16.0), 0.0)
    return heat, cool


def synthesise_loads(b: Building, dry_bulb: FloatArray, mains_C: float = 10.0) -> dict[str, FloatArray]:
    """Hourly LTHW / CHW / DHW (kW) for the whole building, scaled to each zone's annual demands."""
    tot = {"LTHW": np.zeros(HOURS), "CHW": np.zeros(HOURS), "DHW": np.zeros(HOURS)}
    for z in b.zones:
        if z.area_m2 <= 0:
            continue
        st = SPACE_TYPES.get(z.space_type, SPACE_TYPES["other"])
        sp = SynthSpec(st.profile, z.area_m2, max(z.occupants, 1.0), heating_kWh_m2=z.heating_kWh / z.area_m2,
                       cooling_kWh_m2=z.cooling_kWh / z.area_m2, dhw_litres_per_occupant_day=0.0,
                       dhw_store_C=b.dhw_store_C, mains_C=mains_C, circulation_fraction=0.0)
        raw = synthesise(sp, dry_bulb)
        tot["LTHW"] += raw["LTHW_kW"]
        tot["CHW"] += raw["CHW_kW"]
        if z.dhw_kWh > 0:  # DHW shape from the profile, scaled to the annual figure (incl. circulation)
            sp2 = SynthSpec(st.profile, z.area_m2, max(z.occupants, 1.0), 0, 0, 50.0, b.dhw_store_C, mains_C, 0.0)
            shape = synthesise(sp2, dry_bulb)["DHW_kW"]
            tot["DHW"] += z.dhw_kWh * shape / shape.sum() if shape.sum() > 0 else 0.0
    fa = b.fresh_air
    if fa.mode in ("separate", "estimate"):
        h, c = fresh_air_hourly(b, dry_bulb)
        if fa.mode == "separate":
            h = h * (fa.ahu_heating_kWh / h.sum()) if h.sum() > 0 else h
            c = c * (fa.ahu_cooling_kWh / c.sum()) if c.sum() > 0 else c
        tot["LTHW"] += h
        tot["CHW"] += c
    return {k: v for k, v in tot.items() if v.sum() > 0}
