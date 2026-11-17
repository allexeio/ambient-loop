"""Benchmark load synthesis (brief §1, §7): hourly loads from building type, area and occupancy.

For early stages, when no dynamic model output exists. Each synthesised series is tagged
[Assumption] and reported as such.
  Space heating: annual = q_h · GIA. Hourly shape ∝ max(0, T_base,h − T_db) × (0.6 + 0.4 ·
    occupancy), with T_base,h = 15.5 °C (the UK degree-day base, CIBSE TM41).
  Space cooling: annual = q_c · GIA. A base share follows occupancy (internal and process
    gains); the rest ∝ max(0, T_db − 12 °C) × occupancy (fabric, solar and ventilation proxy).
  DHW: litres per occupant per day × occupants × ρ c_p (T_store − T_mains) / 3.6e6 kWh,
    distributed by the type's hourly shape. Circulation losses are added as a fraction.
Defaults are in library/profiles/building_types.yaml [A-055]. Weekends scale occupancy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from ambient_loop.core.series import HOURS, FloatArray, day_of_hour

LIB = Path(__file__).resolve().parents[3] / "library" / "profiles" / "building_types.yaml"


def building_types() -> dict[str, dict[str, object]]:
    return yaml.safe_load(LIB.read_text())  # type: ignore[no-any-return]


@dataclass
class SynthSpec:
    building_type: str
    gia_m2: float
    occupants: float
    heating_kWh_m2: float | None = None
    cooling_kWh_m2: float | None = None
    dhw_litres_per_occupant_day: float | None = None
    dhw_store_C: float = 60.0
    mains_C: float = 10.0
    circulation_fraction: float = 0.25
    year_start_weekday: int = 0  # 0 = Monday


def _tile(shape: list[float], weekend: float, start_wd: int) -> FloatArray:
    s = np.tile(np.asarray(shape, float), 365)
    wd = (day_of_hour() + start_wd) % 7
    return np.where(wd >= 5, s * weekend, s)


def synthesise(spec: SynthSpec, dry_bulb_C: FloatArray) -> dict[str, FloatArray]:
    bt = building_types()
    if spec.building_type not in bt:
        raise KeyError(f"unknown building type {spec.building_type}; have {sorted(bt)}")
    p = bt[spec.building_type]
    occ = _tile(p["occupancy_shape"], float(p["weekend_factor"]), spec.year_start_weekday)  # type: ignore[arg-type]
    db = np.asarray(dry_bulb_C, float)
    out: dict[str, FloatArray] = {}
    qh = (spec.heating_kWh_m2 if spec.heating_kWh_m2 is not None else float(p["heating_kWh_m2"])) * spec.gia_m2  # type: ignore[arg-type]
    hs = np.maximum(0.0, 15.5 - db) * (0.6 + 0.4 * occ)
    out["LTHW_kW"] = qh * hs / hs.sum() if hs.sum() > 0 else np.zeros(HOURS)
    qc = (spec.cooling_kWh_m2 if spec.cooling_kWh_m2 is not None else float(p["cooling_kWh_m2"])) * spec.gia_m2  # type: ignore[arg-type]
    base = float(p["cooling_base_fraction"])  # type: ignore[arg-type]
    cs_amb = np.maximum(0.0, db - 12.0) * occ
    cooling = base * qc * occ / occ.sum() + ((1 - base) * qc * cs_amb / cs_amb.sum() if cs_amb.sum() > 0 else 0.0)
    out["CHW_kW"] = np.asarray(cooling, float)
    lpd = spec.dhw_litres_per_occupant_day if spec.dhw_litres_per_occupant_day is not None else float(p["dhw_litres_per_occupant_day"])  # type: ignore[arg-type]
    day_kWh = lpd * spec.occupants * 4.186 * (spec.dhw_store_C - spec.mains_C) / 3600.0
    dshape = _tile(p["dhw_shape"], float(p["weekend_factor"]), spec.year_start_weekday)  # type: ignore[arg-type]
    daily = dshape.reshape(365, 24)
    norm = (daily / np.maximum(daily.sum(axis=1, keepdims=True), 1e-9)).ravel()
    draw = day_kWh * norm
    out["DHW_kW"] = draw * (1 + spec.circulation_fraction / (1 - spec.circulation_fraction)) if spec.circulation_fraction < 1 else draw
    return out
