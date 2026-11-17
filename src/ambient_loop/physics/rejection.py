"""Heat rejection and air-side plant: dry cooler, cooling tower, air-source heat pump.

Dry cooler (rule 9): available only when T_db < T_fluid,in − approach_min.
  Capacity Q_av = Q_rated · (T_fluid,in − T_db) / ITD_rated, linear in the inlet temperature
  difference (the usual manufacturer rating basis; Eurovent RS 7/C/005).
  Fan power: airflow fraction a = (Q / Q_av)^(1/0.6), since heat transfer goes roughly as
  airflow^0.6 at constant ITD, and P = P_rated · a³ (fan affinity), with a minimum speed of
  20 % [A-025].
Cooling tower: available when T_wb + approach < T_fluid,in. The capacity scales the same way
  with (T_in − T_wb) / (range + approach)_rated. Fan power is a constant 0.01 kW per kW at full
  duty, scaled by a³ [A-026]. Water treatment and bleed are not modelled.
Wet bulb: Stull (2011), "Wet-bulb temperature from relative humidity and air temperature",
  J. Appl. Meteor. Climatol. 50, 2267–2269 (±0.3 K for RH 5–99 %, T −20 to 50 °C).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ambient_loop.core.series import FloatArray


@dataclass(frozen=True)
class DryCooler:
    rated_kW: float
    rated_itd_K: float  # fluid inlet − air at the rating point
    rated_fan_kW: float
    approach_min_K: float = 5.0
    min_fan_speed: float = 0.2
    simple_cutoff_C: float | None = None
    simple_kW_per_kW: float | None = None

    def available_kW(self, t_fluid_in: float, t_db: float) -> float:
        if self.simple_cutoff_C is not None:
            return self.rated_kW if t_db <= self.simple_cutoff_C else 0.0
        d = t_fluid_in - t_db
        if d < self.approach_min_K:
            return 0.0
        return self.rated_kW * d / self.rated_itd_K

    def fan_kW(self, q_kW: float, q_avail_kW: float) -> float:
        if q_kW <= 1e-9 or q_avail_kW <= 1e-9:
            return 0.0
        if self.simple_kW_per_kW is not None:
            return q_kW * self.simple_kW_per_kW
        a = max(min(q_kW / q_avail_kW, 1.0) ** (1 / 0.6), self.min_fan_speed)
        return self.rated_fan_kW * a ** 3


@dataclass(frozen=True)
class CoolingTower:
    rated_kW: float
    rated_range_K: float = 5.0
    rated_approach_K: float = 4.0
    fan_kW_per_kW: float = 0.01

    def available_kW(self, t_fluid_in: float, t_wb: float) -> float:
        d = t_fluid_in - t_wb
        if d < self.rated_approach_K:
            return 0.0
        return self.rated_kW * d / (self.rated_range_K + self.rated_approach_K)

    def fan_kW(self, q_kW: float, q_avail_kW: float) -> float:
        if q_kW <= 1e-9 or q_avail_kW <= 1e-9:
            return 0.0
        a = max(min(q_kW / q_avail_kW, 1.0) ** (1 / 0.6), 0.2)
        return self.fan_kW_per_kW * self.rated_kW * a ** 3


def wet_bulb_stull(t_C: FloatArray, rh_pct: FloatArray) -> FloatArray:
    t = np.asarray(t_C, float)
    rh = np.clip(np.asarray(rh_pct, float), 5, 99)
    return (t * np.arctan(0.151977 * np.sqrt(rh + 8.313659)) + np.arctan(t + rh)
            - np.arctan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * np.arctan(0.023101 * rh)
            - 4.686035)
