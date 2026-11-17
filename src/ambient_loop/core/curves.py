"""One-dimensional performance curves with explicit hold and extrapolation flags."""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict, model_validator

from ambient_loop.core.provenance import DEFAULT_TAG, Tag
from ambient_loop.core.series import FloatArray


class Curve1D(BaseModel):
    """Piecewise-linear y(x).

    Outside [x_min, x_max] the curve holds its end value. Points outside the range are flagged
    as extrapolated, unless the curve's definition holds that end on purpose (`hold_above` /
    `hold_below`). Example: the workbook ASHP COP is held constant above 10 °C by definition.
    That is a modelling choice, not an extrapolation.
    """

    model_config = ConfigDict(frozen=True)

    x: list[float]
    y: list[float]
    hold_above: bool = False
    hold_below: bool = False
    tag: Tag = DEFAULT_TAG
    ref: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Curve1D:
        if len(self.x) != len(self.y) or len(self.x) < 1:
            raise ValueError("curve x and y must be the same non-zero length")
        if any(b <= a for a, b in zip(self.x, self.x[1:], strict=False)):
            raise ValueError("curve x must be strictly increasing")
        return self

    def __call__(self, xq: FloatArray) -> tuple[FloatArray, np.ndarray]:
        """Return (y, extrapolated_mask)."""
        xq = np.asarray(xq, dtype=np.float64)
        y = np.interp(xq, self.x, self.y)
        above = (xq > self.x[-1]) & (not self.hold_above)
        below = (xq < self.x[0]) & (not self.hold_below)
        return y, above | below
