"""Hourly pinch and heat-cascade analysis (Phase 2).

Method (Linnhoff & Hindmarsh 1983; Kemp, "Pinch Analysis and Process Integration", 2nd ed.,
2007, ch. 2–4; adapted to 8,760 hourly problems):

1. Shifted temperatures. Hot streams (those that give heat) move down by ΔT_min/2 and cold
   streams move up by ΔT_min/2. ΔT_min is set per exchange class.
2. The problem table is built on a fixed grid of temperature intervals (default 0.5 K), so the
   table can be vectorised across all hours. Each stream contributes CP·overlap to every
   interval it spans, where CP = Q / |T_in − T_out| (kW/K).
3. Cascade from the top. Q_H,min = max(0, −min cumulative surplus). The pinch is the boundary
   where the corrected cascade is zero.
4. Direct recovery = Σ cold duty − Q_H,min. This is heat exchange alone, with no compressor.
5. Heat-pump-assisted potential uses the grand composite curve (GCC):
   - Below the pinch, source heat extractable at or above T is
     E(T) = min over T'' ≤ T of Q(T''). This removes pockets, which are already
     self-satisfied.
   - Above the pinch, sink heat deliverable at or below T is
     D(T) = min over T'' ≥ T of Q(T'').
   - Both are binned into matching bands (default 2.5 K).
   - Sinks are taken from the coldest band up and sources from the warmest band down
     (minimum lift first).
   - Each match uses Q_src = Q_snk (1 − 1/COP), with
     COP = η · T_c / (T_c − T_e), T_c = T_snk + approach and T_e = T_src − approach.
   [Assumption A-003: greedy minimum-lift matching is a screening heuristic. It is not a
   proven optimum for total compressor work.]
6. Coincidence factor (rule 5) = Σ_t source heat matched in the same hour ÷ min(Σ surplus,
   Σ source heat the deficit would need). 1.0 means timing costs nothing.

All quantities are kWh in each hour (numerically equal to mean kW).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ambient_loop.core.series import HOURS, FloatArray, as_hourly
from ambient_loop.schema.project import Project, RecoverySettings, Stream

K0 = 273.15


@dataclass
class StreamT:
    """A stream reduced to hourly arrays: duty and shifted inlet/outlet temperatures."""

    id: str
    hot: bool
    duty: FloatArray
    t_in: FloatArray  # actual
    t_out: FloatArray
    shift: float  # ±ΔT_min/2 applied


def stream_temperatures(
    stream: Stream, duty: FloatArray, mains_C: FloatArray, settings: RecoverySettings
) -> StreamT | None:
    if stream.T_in_C is None or stream.T_out_C is None:
        return None
    t_in = mains_C if stream.T_in_C == "mains" else as_hourly(float(stream.T_in_C))
    t_out = as_hourly(float(stream.T_out_C))
    hot = bool(np.mean(t_in) > np.mean(t_out))
    shift = settings.dT_min_K[stream.exchange_class] / 2.0
    return StreamT(stream.id, hot, duty, t_in, t_out, shift)


@dataclass
class PinchResult:
    grid_C: FloatArray  # interval boundaries (shifted temperature), ascending
    hot_total: FloatArray  # hourly kWh
    cold_total: FloatArray
    q_hmin: FloatArray  # minimum heating after direct recovery
    q_cmin: FloatArray  # minimum cooling (heat to reject) after direct recovery
    direct: FloatArray  # directly recoverable (HX only)
    pinch_C: FloatArray  # shifted pinch temperature (nan when there is no pinch)
    source_by_band: FloatArray  # [hours, bands] kWh available below pinch
    sink_by_band: FloatArray  # [hours, bands] kWh needed above pinch
    band_lo_C: FloatArray  # lower edge of matching bands (shifted)
    hp_sink: FloatArray = field(default_factory=lambda: np.zeros(HOURS))  # HP heat delivered
    hp_source: FloatArray = field(default_factory=lambda: np.zeros(HOURS))  # source heat used
    hp_work: FloatArray = field(default_factory=lambda: np.zeros(HOURS))
    hp_lift_kWh: dict[float, float] = field(default_factory=dict)  # lift band (K) -> sink kWh
    unmatched_surplus: FloatArray = field(default_factory=lambda: np.zeros(HOURS))
    unmatched_deficit: FloatArray = field(default_factory=lambda: np.zeros(HOURS))
    source_need_unmet: FloatArray = field(default_factory=lambda: np.zeros(HOURS))
    coincidence: float = float("nan")


def _interval_heat(st: StreamT, lo: FloatArray, hi: FloatArray) -> FloatArray:
    """Heat (kWh) the stream puts into each interval for each hour: [hours, intervals]."""
    a = np.minimum(st.t_in, st.t_out) + (-st.shift if st.hot else st.shift)
    b = np.maximum(st.t_in, st.t_out) + (-st.shift if st.hot else st.shift)
    span = np.maximum(b - a, 1e-9)
    cp = st.duty / span  # kW/K
    overlap = np.clip(np.minimum(b[:, None], hi[None, :]) - np.maximum(a[:, None], lo[None, :]),
                      0.0, None)
    return cp[:, None] * overlap


def problem_table(streams: list[StreamT], settings: RecoverySettings) -> PinchResult:
    if not streams:
        raise ValueError("recovery analysis needs streams with temperatures")
    tmin = min(float(np.min(np.minimum(s.t_in, s.t_out))) for s in streams) - 5
    tmax = max(float(np.max(np.maximum(s.t_in, s.t_out))) for s in streams) + 5
    g = settings.grid_K
    edges = np.arange(np.floor(tmin / g) * g, np.ceil(tmax / g) * g + g / 2, g)
    lo, hi = edges[:-1], edges[1:]
    net = np.zeros((HOURS, lo.size))  # surplus (+) per interval
    hot_total = np.zeros(HOURS)
    cold_total = np.zeros(HOURS)
    for st in streams:
        q = _interval_heat(st, lo, hi)
        if st.hot:
            net += q
            hot_total += st.duty
        else:
            net -= q
            cold_total += st.duty
    # cascade from the top: boundary k (descending) carries cumulative surplus of intervals above
    cum = np.concatenate([np.zeros((HOURS, 1)), np.cumsum(net[:, ::-1], axis=1)], axis=1)
    q_hmin = np.maximum(0.0, -cum.min(axis=1))
    flow = cum + q_hmin[:, None]  # GCC: heat flowing down through each boundary (descending)
    q_cmin = flow[:, -1]
    direct = cold_total - q_hmin
    # pinch: highest boundary with zero flow, when both utilities are needed or heat is exchanged
    bnd_desc = edges[::-1]
    zero = np.isclose(flow, 0.0, atol=1e-9)
    has = zero.any(axis=1) & (hot_total > 0) & (cold_total > 0)
    first = np.argmax(zero, axis=1)
    pinch = np.where(has, bnd_desc[first], np.nan)

    # GCC envelopes on ascending boundaries
    flow_asc = flow[:, ::-1]  # boundary i ↔ edges[i]
    pidx = np.where(has, edges.size - 1 - first, np.where(hot_total > cold_total, edges.size - 1, 0))
    idx = np.arange(edges.size)[None, :]
    below = idx <= pidx[:, None]
    big = 1e30
    # source envelope: E(T) = cummin from bottom up, only below pinch
    e = np.minimum.accumulate(np.where(below, flow_asc, big), axis=1)
    e = np.where(below, e, np.take_along_axis(e, pidx[:, None], axis=1))
    src_interval = (-np.diff(e, axis=1)).clip(min=0.0)
    # sink envelope: D(T) = cummin from top down, only above pinch
    above = idx >= pidx[:, None]
    d = np.minimum.accumulate(np.where(above, flow_asc, big)[:, ::-1], axis=1)[:, ::-1]
    d = np.where(above, d, np.take_along_axis(d, pidx[:, None], axis=1))
    snk_interval = np.diff(d, axis=1).clip(min=0.0)

    # aggregate to matching bands
    per = max(1, int(round(settings.band_K / g)))
    nb = int(np.ceil(lo.size / per))
    pad = nb * per - lo.size
    src_b = np.pad(src_interval, ((0, 0), (0, pad))).reshape(HOURS, nb, per).sum(axis=2)
    snk_b = np.pad(snk_interval, ((0, 0), (0, pad))).reshape(HOURS, nb, per).sum(axis=2)
    band_lo = lo[0] + np.arange(nb) * per * g
    return PinchResult(edges, hot_total, cold_total, q_hmin, q_cmin, direct, pinch, src_b, snk_b,
                       band_lo)


def generic_cop(t_src_C: float, t_snk_C: float, eta: float, approach: float) -> float:
    """Generic heating COP: η · T_c/(T_c − T_e), with approaches applied (temperatures in K).

    The temperature difference is floored at 5 K, so a near-zero lift cannot report an
    unbounded COP.
    """
    tc = t_snk_C + approach + K0
    te = t_src_C - approach + K0
    return eta * tc / max(tc - te, 5.0)


def match_heat_pumps(pr: PinchResult, settings: RecoverySettings) -> PinchResult:
    """Greedy minimum-lift matching of GCC sources to sinks, hour by hour."""
    band_mid = pr.band_lo_C + settings.band_K / 2
    eta, app = settings.eta_carnot, settings.hp_approach_K
    nb = band_mid.size
    cop = np.array([[generic_cop(band_mid[i], band_mid[j], eta, app) for j in range(nb)]
                    for i in range(nb)])  # [src, snk]
    hp_sink = np.zeros(HOURS)
    hp_src = np.zeros(HOURS)
    lift: dict[float, float] = {}
    src_all = pr.source_by_band.sum(axis=1)
    snk_all = pr.sink_by_band.sum(axis=1)
    for t in range(HOURS):
        if src_all[t] <= 1e-9 or snk_all[t] <= 1e-9:
            continue
        src = pr.source_by_band[t].copy()
        snk = pr.sink_by_band[t].copy()
        si = [i for i in range(nb) if src[i] > 1e-12][::-1]  # warmest first
        kj = [j for j in range(nb) if snk[j] > 1e-12]  # coldest first
        a = b = 0
        while a < len(si) and b < len(kj):
            i, j = si[a], kj[b]
            c = cop[i, j]
            frac = 1.0 - 1.0 / c if c > 1.0 else 0.0
            if frac <= 0:
                b += 1
                continue
            q_snk = min(snk[j], src[i] / frac)
            q_src = q_snk * frac
            snk[j] -= q_snk
            src[i] -= q_src
            hp_sink[t] += q_snk
            hp_src[t] += q_src
            lb = float(np.floor(max(band_mid[j] - band_mid[i], 0) / 5.0) * 5.0)
            lift[lb] = lift.get(lb, 0.0) + float(q_snk)
            if snk[j] <= 1e-12:
                b += 1
            if src[i] <= 1e-12:
                a += 1
    pr.hp_sink = hp_sink
    pr.hp_source = hp_src
    pr.hp_work = hp_sink - hp_src
    pr.hp_lift_kWh = dict(sorted(lift.items()))
    pr.unmatched_surplus = src_all - hp_src
    pr.unmatched_deficit = snk_all - hp_sink
    # coincidence: same-hour matched source / what could be matched if timing were free
    frac_year = hp_src.sum() / hp_sink.sum() if hp_sink.sum() > 0 else 0.6
    need = snk_all * frac_year
    # source heat the unmet deficit would need from elsewhere (annual mean source fraction)
    pr.source_need_unmet = pr.unmatched_deficit * frac_year
    denom = min(src_all.sum(), need.sum())
    pr.coincidence = float(hp_src.sum() / denom) if denom > 0 else float("nan")
    return pr


def analyse(project: Project, demand_kW: dict[str, FloatArray]) -> tuple[PinchResult, list[str]]:
    """Run the hourly pinch analysis on a project's streams. Returns (result, skipped ids)."""
    s = project.recovery
    mw = project.mains_water_C
    mains = as_hourly(mw if isinstance(mw, list) else mw.value)
    streams: list[StreamT] = []
    skipped: list[str] = []
    for st in project.streams:
        r = stream_temperatures(st, demand_kW[st.id], mains, s)
        if r is None:
            skipped.append(st.id)
        else:
            streams.append(r)
    pr = match_heat_pumps(problem_table(streams, s), s)
    return pr, skipped
