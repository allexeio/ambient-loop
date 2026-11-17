"""Heat pump and chiller performance models.

Every model returns a heating COP (condenser heat ÷ compressor electricity) at given leaving
temperatures. For a vapour-compression machine at the same operating point,
EER = COP_h − 1 (Q_evap = Q_cond − W). Chillers and heat pumps therefore share one model.

Models:
  FixedModel    constant COP ("simple mode" only: brief §2.3)
  CarnotModel   COP = η · T_c / (T_c − T_e) with T_c = T_snk,lvg + approach_c and
                T_e = T_src,lvg − approach_e (K). Driven by leaving temperatures (rules 3, 4).
  MapModel      manufacturer table over (T_src,lvg, T_snk,lvg): bilinear interpolation,
                end values held outside the table and those hours flagged [Extrapolated]
  CO2Model      transcritical CO₂: COP depends on gas-cooler ENTERING water temperature,
                outlet temperature and source temperature (rule 4b)

Part load (PartLoad): a COP multiplier f(PLR) between PLR_min and 1, plus EN 14825:2022
§8.5 cycling degradation below PLR_min: COP_cyc = COP(PLR_min)·CR / (C_c·CR + 1 − C_c),
where CR = PLR / PLR_min and the default C_c = 0.9 is the standard's value for water units.

Fleet sequencing (rule 10):
  lead_lag      units run at full load in turn; the last unit modulates or cycles
  equal_share   all available units share the load equally
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

from ambient_loop.core.provenance import Tag
from ambient_loop.core.series import FloatArray

K0 = 273.15


@dataclass(frozen=True)
class PartLoad:
    plr: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
    mult: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0)
    plr_min: float = 0.25
    cd: float = 0.9  # EN 14825 C_c
    tag: Tag = Tag.GENERIC
    ref: str = ""

    def factor(self, plr: float) -> float:
        """COP multiplier at a unit part-load ratio (0 < plr ≤ 1)."""
        if plr <= 0:
            return 1.0
        p = max(plr, self.plr_min)
        xs, ys = self.plr, self.mult
        if p <= xs[0]:
            m = ys[0]
        elif p >= xs[-1]:
            m = ys[-1]
        else:
            i = bisect_right(xs, p)
            m = ys[i - 1] + (ys[i] - ys[i - 1]) * (p - xs[i - 1]) / (xs[i] - xs[i - 1])
        if plr < self.plr_min:
            cr = plr / self.plr_min
            m *= cr / (self.cd * cr + 1.0 - self.cd)
        return m


@dataclass
class Envelope:
    src_lvg_C: tuple[float, float] = (-50.0, 60.0)
    snk_lvg_C: tuple[float, float] = (0.0, 95.0)

    def violations(self, t_src: FloatArray, t_snk: FloatArray) -> np.ndarray:
        return ((t_src < self.src_lvg_C[0]) | (t_src > self.src_lvg_C[1])
                | (t_snk < self.snk_lvg_C[0]) | (t_snk > self.snk_lvg_C[1]))


@dataclass
class PerfModel:
    name: str
    tag: Tag
    ref: str
    part_load: PartLoad = field(default_factory=PartLoad)
    envelope: Envelope = field(default_factory=Envelope)

    def cop_h(self, t_src: FloatArray, t_snk: FloatArray,
              t_snk_in: FloatArray | None = None) -> tuple[FloatArray, np.ndarray]:
        """Full-load heating COP and extrapolation mask."""
        raise NotImplementedError

    def capacity_factor(self, t_src: FloatArray, t_snk: FloatArray) -> FloatArray:
        """Heating capacity relative to the rated capacity."""
        return np.ones_like(np.asarray(t_src, dtype=float))


@dataclass
class FixedModel(PerfModel):
    cop: float = 3.0

    def cop_h(self, t_src, t_snk, t_snk_in=None):  # type: ignore[no-untyped-def]
        t = np.asarray(t_src, dtype=float)
        return np.full_like(t, self.cop), np.zeros(t.shape, bool)


@dataclass
class CarnotModel(PerfModel):
    eta: float = 0.5
    approach_evap_K: float = 2.0
    approach_cond_K: float = 2.0
    min_lift_K: float = 10.0  # floor on T_c − T_e so a tiny lift cannot report an absurd COP
    cap_per_K_src: float = 0.025  # +2.5 % heating capacity per K warmer source [Generic]
    rated_src_C: float = 10.0

    def cop_h(self, t_src, t_snk, t_snk_in=None):  # type: ignore[no-untyped-def]
        tc = np.asarray(t_snk, dtype=float) + self.approach_cond_K + K0
        te = np.asarray(t_src, dtype=float) - self.approach_evap_K + K0
        cop = self.eta * tc / np.maximum(tc - te, self.min_lift_K)
        return cop, self.envelope.violations(np.asarray(t_src, float), np.asarray(t_snk, float))

    def capacity_factor(self, t_src, t_snk):  # type: ignore[no-untyped-def]
        return np.clip(1 + self.cap_per_K_src * (np.asarray(t_src, float) - self.rated_src_C), 0.3, 1.6)


@dataclass
class AirSourceModel(CarnotModel):
    """Air-to-water: T_e = T_db − air approach; defrost derate in the frost band.

    Defaults: 8 K air approach and a 10 % derate between −3 and +5 °C dry bulb. They are
    calibrated so η ≈ 0.45 reproduces the fixture ASHP map within ±8 % (see tests). EN 14511
    rating points already include defrost; the derate stands in for that. [A-021]
    """

    defrost_band_C: tuple[float, float] = (-3.0, 5.0)
    defrost_derate: float = 0.10

    def cop_h(self, t_src, t_snk, t_snk_in=None):  # type: ignore[no-untyped-def]
        cop, ex = super().cop_h(t_src, t_snk)
        t = np.asarray(t_src, float)
        lo, hi = self.defrost_band_C
        return np.where((t >= lo) & (t <= hi), cop * (1 - self.defrost_derate), cop), ex


@dataclass
class MapModel(PerfModel):
    """Gridded manufacturer data: COP_h over (T_src,lvg, T_snk,lvg); optional capacity grid."""

    src_axis: tuple[float, ...] = ()
    snk_axis: tuple[float, ...] = ()
    cop_grid: tuple[tuple[float, ...], ...] = ()  # [src][snk] heating COP
    cap_grid: tuple[tuple[float, ...], ...] | None = None  # relative heating capacity

    def _interp(self, grid: np.ndarray, t_src: FloatArray, t_snk: FloatArray) -> tuple[FloatArray, np.ndarray]:
        xs, ys = np.asarray(self.src_axis), np.asarray(self.snk_axis)
        a = np.asarray(t_src, float)
        b = np.asarray(t_snk, float)
        ex = (a < xs[0]) | (a > xs[-1]) | (b < ys[0]) | (b > ys[-1])
        if xs.size == 1:
            col = np.array([np.interp(b, ys, grid[0])])
            return np.broadcast_to(col[0], a.shape).copy(), ex
        ac = np.clip(a, xs[0], xs[-1])
        bc = np.clip(b, ys[0], ys[-1])
        i = np.clip(np.searchsorted(xs, ac) - 1, 0, xs.size - 2)
        j = np.clip(np.searchsorted(ys, bc) - 1, 0, max(ys.size - 2, 0))
        if ys.size == 1:
            wa = (ac - xs[i]) / (xs[i + 1] - xs[i])
            return grid[i, 0] * (1 - wa) + grid[i + 1, 0] * wa, ex
        wa = (ac - xs[i]) / (xs[i + 1] - xs[i])
        wb = (bc - ys[j]) / (ys[j + 1] - ys[j])
        v = (grid[i, j] * (1 - wa) * (1 - wb) + grid[i + 1, j] * wa * (1 - wb)
             + grid[i, j + 1] * (1 - wa) * wb + grid[i + 1, j + 1] * wa * wb)
        return v, ex

    def cop_h(self, t_src, t_snk, t_snk_in=None):  # type: ignore[no-untyped-def]
        return self._interp(np.asarray(self.cop_grid, float), t_src, t_snk)

    def capacity_factor(self, t_src, t_snk):  # type: ignore[no-untyped-def]
        if self.cap_grid is None:
            return np.ones_like(np.asarray(t_src, float))
        return self._interp(np.asarray(self.cap_grid, float), t_src, t_snk)[0]


@dataclass
class CO2Model(PerfModel):
    """Transcritical CO₂ water heater.

    COP = COP_ref · (1 − k_in·(T_in − T_in,ref) − k_in2·max(0, T_in − T_knee))
                 · (1 + k_src·(T_src − T_src,ref)) · (1 − k_out·(T_out − T_out,ref))

    Calibrated to published rating points for a 40 kW air-source unit (Mitsubishi Electric
    QAHV-N560YA-HPB flyer and UK data sheet, 65 °C outlet):
      16 °C air, 17 °C inlet: COP 3.88
       7 °C air,  9 °C inlet: COP 3.65
       7 °C air, 15 °C inlet: COP 3.44
    These three points fix k_in = 0.96 %/K and k_src = 1.68 %/K exactly. The manufacturer's FAQ says capacity and
    COP "start to reduce" above a 29 °C inlet, so the knee is placed at 29 °C with a steeper
    slope above it [A-022, Assumption]. k_out = 1 %/K. [A-023, Generic]
    """

    cop_ref: float = 3.65
    t_in_ref: float = 9.0
    t_src_ref: float = 7.0
    t_out_ref: float = 65.0
    k_in: float = 0.0096
    k_in2: float = 0.0300
    t_knee: float = 29.0
    k_src: float = 0.0168
    k_out: float = 0.010

    def cop_h(self, t_src, t_snk, t_snk_in=None):  # type: ignore[no-untyped-def]
        a = np.asarray(t_src, float)
        b = np.asarray(t_snk, float)
        tin = np.full_like(a, self.t_in_ref) if t_snk_in is None else np.asarray(t_snk_in, float)
        f_in = 1 - self.k_in * (tin - self.t_in_ref) - self.k_in2 * np.maximum(0, tin - self.t_knee)
        cop = (self.cop_ref * np.clip(f_in, 0.2, None) * (1 + self.k_src * (a - self.t_src_ref))
               * (1 - self.k_out * (b - self.t_out_ref)))
        ex = self.envelope.violations(a, b) | (tin > 45.0) | (a > 30.0)
        return np.maximum(cop, 1.0), ex


@dataclass
class Fleet:
    """N identical units with a performance model and a sequencing strategy."""

    name: str
    model: PerfModel
    count: int
    unit_capacity_kW: float  # rated heating (condenser) output per unit
    sequencing: str = "lead_lag"  # or "equal_share"

    def electricity(self, load_cond_kW: float, cop_full: float, cap_factor: float = 1.0,
                    units_available: float | None = None) -> tuple[float, float, float]:
        """Electricity for a condenser-side load. Returns (W, unmet_cond_kW, lead-unit PLR)."""
        if load_cond_kW <= 1e-9:
            return 0.0, 0.0, 0.0
        n = self.count if units_available is None else max(units_available, 0.0)
        cu = self.unit_capacity_kW * cap_factor
        if n <= 0 or cu <= 0:
            return 0.0, load_cond_kW, 0.0
        served = min(load_cond_kW, n * cu)
        unmet = load_cond_kW - served
        pl = self.model.part_load
        if self.sequencing == "equal_share":
            plr = served / (n * cu)
            return served / (cop_full * pl.factor(plr)), unmet, plr
        k = int(served // cu)
        r = served / cu - k
        w = k * cu / (cop_full * pl.factor(1.0))
        if r > 1e-9:
            w += r * cu / (cop_full * pl.factor(r))
        return w, unmet, (r if r > 1e-9 else 1.0)
