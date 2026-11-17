"""Arrangement generator: propose candidates from the loads and the recovery analysis.

Every candidate records why it was proposed (the opportunity it targets, with numbers) and the
assumptions it relies on. Thresholds are deliberately simple and listed here, so an engineer
can see why something was or wasn't proposed [A-049].
"""

from __future__ import annotations

import numpy as np

from ambient_loop.arrangements import archetypes as A
from ambient_loop.recovery.report import RecoverySummary
from ambient_loop.schema.arrangement import Arrangement

MATERIAL = 0.02  # an opportunity is material above 2 % of annual heat demand


def _overlap_hours(a: np.ndarray, b: np.ndarray, frac: float = 0.1) -> int:
    return int(((a > frac * max(a.max(), 1e-9)) & (b > frac * max(b.max(), 1e-9))).sum())


def propose(site: A.SiteBasis, rec: RecoverySummary, lthw: np.ndarray, chw: np.ndarray, dhw: np.ndarray,
            has_ground: bool = False, has_external: bool = False) -> list[tuple[Arrangement, str]]:
    an = rec.annual_MWh
    heat = an["Total heat demand"]
    hp = an["Heat-pump assisted (delivered)"]
    direct = an["Direct (HX only)"]
    deficit, surplus = an["Unmatched deficit (generate)"], an["Unmatched surplus (reject)"]
    out: list[tuple[Arrangement, str]] = []

    def add(arr: Arrangement, reason: str) -> None:
        arr.why = [reason] + arr.why
        out.append((arr, reason))

    add(A.s0_electric_dhw(site), "Lowest-capital reference at the basic end of the ladder.")
    add(A.s1_ashp_chiller(site), "Basic all-electric plant: separate air-source heating, cooling and DHW, no heat sharing.")
    if dhw.sum() > 0:
        add(A.s2_ashp_chiller_co2(site), "CO₂ for DHW without a loop: single lift from cold mains (rule 4b).")
    if chw.sum() > 0 and lthw.sum() > 0 and _overlap_hours(lthw, chw) > 200:
        add(A.s3_four_pipe_air(site), f"{_overlap_hours(lthw, chw):,} h/yr of simultaneous LTHW and CHW suit 4-pipe air-source units.")
    if chw.sum() > 0 and dhw.sum() > 0:
        add(A.s4_chiller_hr_preheat(site), "Chiller heat recovery to LTHW and DHW preheat, without a loop.")
    add(A.a1_shared_loop(site), "Shared ambient loop: the reference loop scheme.")
    add(A.a5_cascade(site), "Known-poor cascade kept so the double-lift penalty is explicit (rule 1).")
    cool = float(chw.sum()) / 1e3
    if cool > MATERIAL * heat and (hp + direct) > MATERIAL * heat:
        ov_l = _overlap_hours(lthw, chw)
        if lthw.sum() / 1e3 > MATERIAL * heat:
            add(A.a2_hr_lthw_buffer(site), f"LTHW and CHW overlap in {ov_l:,} h/yr; recovered heat can serve space heating.")
            if ov_l > 500:
                add(A.a6_four_pipe_hrc(site), f"{ov_l:,} h/yr of simultaneous LTHW and CHW suit a 4-pipe HR chiller.")
        if dhw.sum() > 0:
            add(A.a3_hr_preheat(site), f"{direct:,.0f} MWh/yr is directly recoverable and DHW needs a cold-feed rise: "
                "preheat uses recovered heat without a compressor (rule 2).")
            add(A.a23_buffer_preheat(site), "Primary-side storage lets recovered heat be used across hours (rule 7); "
                f"screening storage gain up to {max(rec.storage_MWh.values()):,.0f} MWh/yr.")
            add(A.a4_hr_dhw_source(site, True), "A warmer HR source can lift DHW COP; changeover keeps the loop as backup.")
    dhw_share = dhw.sum() / max(lthw.sum() + dhw.sum(), 1e-9)
    if dhw_share > 0.4:
        add(A.a7_co2_dhw(site), f"DHW is {dhw_share:.0%} of heat demand: a single-lift CO₂ generator avoids the "
            "ASHP → loop → WSHP two-stage path (rules 1, 4b).")
        add(A.a9_dhw60(site), "DHW storage at 60 °C (HSG274 minimum) lowers the DHW condensing temperature.")
    if deficit > 3 * max(surplus, 1e-9):
        add(A.a10_no_dry_cooler(site), f"The loop is short of heat ({deficit:,.0f} vs {surplus:,.0f} MWh/yr): test "
            "whether the dry cooler earns its place (rule 9).")
    elif surplus > deficit:
        add(A.a10_ground_reject(site), "The loop is long on heat: compare air and ground rejection.")
    if has_ground or has_external:
        add(A.a8_external_sources(site), "External sources supplied: they need a loop running below their temperature.")
    if lthw.sum() > 0:
        add(A.a9_low_lthw(site), "Lower LTHW flow raises COP if emitters allow (rule 4).")
    add(A.a9_floating_loop(site), "Seasonal loop float as a temperature-strategy variant.")
    return out
