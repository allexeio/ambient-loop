"""Closed-loop borehole field.

Two modes:
  fixed      the fluid leaves the field at T_ground − q·R_eff. That is a constant ground
             temperature plus a resistance term, adequate for screening.
  ils        monthly load steps superposed on the infinite line source (ILS) response:
             ΔT_b(t) = Σ_i Δq'_i / (4πk) · E1(r_b² / (4α(t − t_i))) · F_field
             The fluid temperature is T_f = T_g − ΔT_b − q'·R_b.
             F_field is a user field-interaction multiplier, typically 1.5–3 for 6 m spacing
             after 25 years. [A-027: interaction is not computed by g-function; use a design
             tool (EED, GLD) for sizing.]
References: Carslaw & Jaeger (1959) ILS; Eskilson (1987) g-functions; CIBSE TM51 (2013),
"Ground source heat pumps: design guidance", ch. 5; GSHPA Vertical Borehole Standard (2021).
Sign: q > 0 means heat extracted from the ground.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import exp1

from ambient_loop.core.series import HOURS, FloatArray, month_of_hour


@dataclass(frozen=True)
class BoreholeField:
    n_bores: int
    depth_m: float
    t_ground_C: float = 12.5  # London undisturbed ≈ 11.5–13 °C (BGS, CIBSE TM51 Fig. 2.3)
    k_W_mK: float = 2.2  # London Clay / chalk range 1.5–2.5
    alpha_m2_s: float = 0.9e-6
    rb_m: float = 0.075
    Rb_mK_W: float = 0.10
    field_factor: float = 2.0
    max_W_per_m: float = 50.0  # extraction or rejection limit per metre
    mode: str = "ils"

    @property
    def length_m(self) -> float:
        return self.n_bores * self.depth_m

    def capacity_kW(self) -> float:
        return self.max_W_per_m * self.length_m / 1e3

    def fluid_temperature(self, q_extract_kW: FloatArray) -> FloatArray:
        """Mean fluid temperature for an hourly extraction profile (negative = rejection)."""
        q = np.asarray(q_extract_kW, float) * 1e3 / self.length_m  # W/m
        inst = q * self.Rb_mK_W
        if self.mode == "fixed":
            return self.t_ground_C - inst
        m = month_of_hour()
        qm = np.array([q[m == i].mean() for i in range(12)])
        # 3-year spin-up repeating the year, so the response is closer to steady periodic
        years = 3
        steps = np.tile(qm, years)
        dq = np.diff(np.r_[0.0, steps])
        t_mid = np.arange(12 * years) * 730.0 * 3600  # step start times (s)
        dT_month = np.zeros(12)
        for k in range(12):
            tk = (12 * (years - 1) + k + 0.5) * 730.0 * 3600
            dt = tk - t_mid[: 12 * (years - 1) + k + 1]
            u = self.rb_m ** 2 / (4 * self.alpha_m2_s * dt)
            dT_month[k] = np.sum(dq[: dt.size] / (4 * np.pi * self.k_W_mK) * exp1(u)) * self.field_factor
        return self.t_ground_C - dT_month[m] - inst

    def hourly_zero(self) -> FloatArray:
        return np.zeros(HOURS)
