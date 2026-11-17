"""Hourly series helpers. A model year is 8,760 hours from 1 January 00:00, non-leap."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

HOURS = 8760
DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

FloatArray = npt.NDArray[np.float64]


def month_of_hour() -> npt.NDArray[np.int64]:
    """Month index 0–11 for each hour of the model year."""
    return np.repeat(np.arange(12), np.array(DAYS_IN_MONTH) * 24)


def day_of_hour() -> npt.NDArray[np.int64]:
    """Day index 0–364 for each hour of the model year."""
    return np.repeat(np.arange(365), 24)


def monthly_to_hourly(values: list[float] | tuple[float, ...]) -> FloatArray:
    if len(values) != 12:
        raise ValueError(f"monthly series needs 12 values, got {len(values)}")
    return np.asarray(values, dtype=np.float64)[month_of_hour()]


def monthly_sum(x: FloatArray) -> FloatArray:
    """Sum an hourly series into 12 monthly totals."""
    return np.bincount(month_of_hour(), weights=x, minlength=12)


def as_hourly(x: float | list[float] | FloatArray) -> FloatArray:
    """Broadcast a scalar, a 12-value monthly list or an 8,760 array to an hourly array."""
    if isinstance(x, (int, float)):
        return np.full(HOURS, float(x))
    arr = np.asarray(x, dtype=np.float64)
    if arr.shape == (12,):
        return monthly_to_hourly(list(arr))
    if arr.shape == (HOURS,):
        return arr
    raise ValueError(f"cannot broadcast shape {arr.shape} to {HOURS} hours")
