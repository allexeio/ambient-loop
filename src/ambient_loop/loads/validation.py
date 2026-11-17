"""Load validation and warnings.

DHW profile shape (brief, rule 6): if the peak hour is below about 6 % of the daily total, the
profile is effectively flat. A flat profile flatters heat recovery, because it lines DHW demand
up with steady heat rejection that real draw-off peaks would not match. CIBSE Guide G
(Public health and plumbing engineering, 2014) and CIPHE design guide shapes put a hotel's
morning peak hour at roughly 10–15 % of the daily volume [Likely; quote the exact profile
when shaped profiles are added in a later phase].
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ambient_loop.core.metric import DhwProfile
from ambient_loop.core.series import FloatArray

FLAT_PEAK_FRACTION = 0.06


@dataclass(frozen=True)
class Warning_:
    code: str
    message: str
    evidence: str


def dhw_peak_fraction(dhw_kW: FloatArray) -> float:
    """Median over days with demand of (peak hour / daily total)."""
    days = dhw_kW.reshape(365, 24)
    totals = days.sum(axis=1)
    mask = totals > 0
    if not mask.any():
        return float("nan")
    return float(np.median(days[mask].max(axis=1) / totals[mask]))


def classify_dhw(dhw_kW: FloatArray) -> DhwProfile:
    f = dhw_peak_fraction(dhw_kW)
    if np.isnan(f):
        return DhwProfile.IMPORTED
    return DhwProfile.FLAT if f < FLAT_PEAK_FRACTION else DhwProfile.SHAPED


def validate_loads(streams: dict[str, tuple[str, FloatArray]]) -> list[Warning_]:
    """streams: id -> (service, hourly kW)."""
    out: list[Warning_] = []
    for sid, (_service, q) in streams.items():
        if (q < 0).any():
            n = int((q < 0).sum())
            out.append(
                Warning_("NEG", f"{sid}: {n} negative hours", "Loads must be non-negative")
            )
    dhw = [q for (svc, q) in streams.values() if svc == "DHW"]
    if dhw:
        total = np.sum(dhw, axis=0)
        f = dhw_peak_fraction(total)
        if f < FLAT_PEAK_FRACTION:
            out.append(
                Warning_(
                    "R6-FLAT-DHW",
                    f"DHW peak hour is {f:.1%} of the daily total (median day), below "
                    f"{FLAT_PEAK_FRACTION:.0%}. The profile is effectively flat and will "
                    "flatter heat recovery. Peak-hour draw-off is not the same as plant duty.",
                    "Brief rule 6; CIBSE Guide G (2014) DHW demand profiles",
                )
            )
    return out
