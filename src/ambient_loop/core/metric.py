"""Efficiency metrics that cannot be shown without their boundary.

Working rule (brief, section 11): never show an efficiency without saying whether pumps are
included, which allocation convention applies, what kind of DHW profile was used, and whether
the run was simple or full mode. `Metric.__str__` always prints the boundary.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from ambient_loop.core.provenance import Tag, worst


class Allocation(StrEnum):
    SYSTEM = "system"  # whole-system combined figure, no allocation needed
    PRO_RATA = "pro_rata"
    INCREMENTAL = "incremental"
    ISOLATED = "isolated_reference"  # each service alone, no reuse: a reference, not a split


class Mode(StrEnum):
    SIMPLE = "simple"
    FULL = "full"


class DhwProfile(StrEnum):
    FLAT = "flat"
    SHAPED = "shaped"
    MEASURED = "measured"
    IMPORTED = "imported"  # taken from an imported file without being classified


class Boundary(BaseModel):
    model_config = ConfigDict(frozen=True)

    pumps_included: bool
    allocation: Allocation
    dhw_profile: DhwProfile
    mode: Mode

    def short(self) -> str:
        pumps = "pumps incl." if self.pumps_included else "pumps excl."
        return (
            f"{pumps}; {self.allocation.value} allocation; "
            f"DHW profile {self.dhw_profile.value}; {self.mode.value} mode"
        )


class Metric(BaseModel):
    """A ratio such as a seasonal COP or combined system ratio."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: float
    numerator_kWh: float
    denominator_kWh: float
    boundary: Boundary
    tags: frozenset[Tag]

    @property
    def headline_tag(self) -> Tag:
        return worst(self.tags)

    def __str__(self) -> str:
        return (
            f"{self.name} = {self.value:.2f} "
            f"({self.numerator_kWh / 1e3:,.1f} / {self.denominator_kWh / 1e3:,.1f} MWh) "
            f"[{self.headline_tag.label}; {self.boundary.short()}]"
        )


def ratio(
    name: str, num_kWh: float, den_kWh: float, boundary: Boundary, tags: frozenset[Tag]
) -> Metric:
    value = num_kWh / den_kWh if den_kWh > 0 else float("nan")
    return Metric(
        name=name,
        value=value,
        numerator_kWh=num_kWh,
        denominator_kWh=den_kWh,
        boundary=boundary,
        tags=tags,
    )
