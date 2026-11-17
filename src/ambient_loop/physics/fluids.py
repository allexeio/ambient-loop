"""Secondary-fluid properties: water, propylene glycol and ethylene glycol.

Tables come from library/fluids/properties.yaml, generated with CoolProp 6.6 (Bell et al.
2014): IAPWS-95 for water, and Melinder (2010) incompressible correlations for MPG/MEG,
which ASHRAE Handbook Fundamentals (2021) ch. 31 also tabulates. Linear interpolation in
temperature. [A-017]
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
import yaml

LIB = Path(__file__).resolve().parents[3] / "library" / "fluids" / "properties.yaml"


@dataclass(frozen=True)
class Fluid:
    name: str
    label: str
    freeze_C: float | None
    T: tuple[float, ...]
    cp_: tuple[float, ...]
    rho_: tuple[float, ...]
    mu_: tuple[float, ...]
    k_: tuple[float, ...]

    def _i(self, arr: tuple[float, ...], t: float) -> float:
        return float(np.interp(t, self.T, arr))

    def cp(self, t: float) -> float:
        """Specific heat, J/(kg·K)."""
        return self._i(self.cp_, t)

    def rho(self, t: float) -> float:
        """Density, kg/m³."""
        return self._i(self.rho_, t)

    def mu(self, t: float) -> float:
        """Dynamic viscosity, Pa·s."""
        return self._i(self.mu_, t)

    def k(self, t: float) -> float:
        """Thermal conductivity, W/(m·K)."""
        return self._i(self.k_, t)

    def vol_heat_capacity_kWh_m3K(self, t: float) -> float:
        return self.rho(t) * self.cp(t) / 3.6e6


@cache
def _table() -> dict[str, dict[str, object]]:
    return yaml.safe_load(LIB.read_text())["fluids"]  # type: ignore[no-any-return]


def get_fluid(name: str) -> Fluid:
    """name: 'water', 'propylene_glycol_30', 'ethylene_glycol_25', ..."""
    t = _table()
    if name not in t:
        raise KeyError(f"unknown fluid {name!r}; have {sorted(t)}")
    d = t[name]
    return Fluid(name, str(d["label"]), d["freeze_C"],  # type: ignore[arg-type]
                 tuple(d["T_C"]), tuple(d["cp_J_kgK"]), tuple(d["rho_kg_m3"]),  # type: ignore[arg-type]
                 tuple(d["mu_Pa_s"]), tuple(d["k_W_mK"]))  # type: ignore[arg-type]


def pressure_drop_factor(fluid: Fluid, water: Fluid, t: float, same_duty: bool = True,
                         dT_ratio: float = 1.0) -> float:
    """Ratio of pump head × flow (hydraulic power) for `fluid` versus water at the same duty.

    Turbulent pipe flow with Blasius friction (f = 0.316 Re^-0.25) gives Δp ∝ ρ^0.75 μ^0.25 V̇^1.75.
    For the same duty and ΔT, V̇ ∝ 1/(ρ cp). Hydraulic power is Δp · V̇.
    Reference: CIBSE Guide C (2007) §4.2. Valid for fully turbulent flow (Re > 4,000).
    """
    vr = (water.rho(t) * water.cp(t)) / (fluid.rho(t) * fluid.cp(t)) / dT_ratio if same_duty else 1.0
    dp = (fluid.rho(t) / water.rho(t)) ** 0.75 * (fluid.mu(t) / water.mu(t)) ** 0.25 * vr ** 1.75
    return float(dp * vr)


def htc_factor(fluid: Fluid, water: Fluid, t: float) -> float:
    """Ratio of the convective heat-transfer coefficient for `fluid` versus water.

    Dittus–Boelter, h ∝ k^0.6 ρ^0.8 cp^0.4 μ^-0.4, at the same velocity. A plate HX at fixed
    area then needs a larger approach roughly in proportion to 1/U.
    Reference: Incropera & DeWitt, Fundamentals of Heat and Mass Transfer, 7th ed., eq. 8.60.
    """
    return float((fluid.k(t) / water.k(t)) ** 0.6 * (fluid.rho(t) / water.rho(t)) ** 0.8
                 * (fluid.cp(t) / water.cp(t)) ** 0.4 * (fluid.mu(t) / water.mu(t)) ** -0.4)
