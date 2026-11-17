"""Provenance tags for inputs and outputs.

Every input value carries a tag. Every output carries the set of tags of the inputs that fed
it. Severity order (worst last) decides the headline tag shown next to a figure:

    manufacturer_data < project_data < generic < assumption < extrapolated
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class Tag(StrEnum):
    MANUFACTURER_DATA = "manufacturer_data"
    PROJECT_DATA = "project_data"
    GENERIC = "generic"
    ASSUMPTION = "assumption"
    EXTRAPOLATED = "extrapolated"

    @property
    def severity(self) -> int:
        return _ORDER.index(self)

    @property
    def label(self) -> str:
        return {
            Tag.MANUFACTURER_DATA: "Manufacturer data",
            Tag.PROJECT_DATA: "Project data",
            Tag.GENERIC: "Generic",
            Tag.ASSUMPTION: "Assumption",
            Tag.EXTRAPOLATED: "Extrapolated",
        }[self]


_ORDER = [
    Tag.MANUFACTURER_DATA,
    Tag.PROJECT_DATA,
    Tag.GENERIC,
    Tag.ASSUMPTION,
    Tag.EXTRAPOLATED,
]

# A bare number in a project file with no tag is treated as an assumption. This is the
# conservative choice: a value gets a better tag only when someone states its source.
# (ASSUMPTIONS.md A-008)
DEFAULT_TAG = Tag.ASSUMPTION


def worst(tags: Iterable[Tag]) -> Tag:
    """Return the most severe tag; PROJECT_DATA if the iterable is empty."""
    tags = list(tags)
    if not tags:
        return Tag.PROJECT_DATA
    return max(tags, key=lambda t: t.severity)


class TaggedFloat(BaseModel):
    """A scalar with provenance. Accepts a bare number in YAML (tagged DEFAULT_TAG)."""

    model_config = ConfigDict(frozen=True)

    value: float
    tag: Tag = DEFAULT_TAG
    ref: str | None = None  # citation, document reference or cell address

    @model_validator(mode="before")
    @classmethod
    def _accept_bare(cls, data: Any) -> Any:
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            return {"value": float(data)}
        return data

    def __float__(self) -> float:
        return self.value

    def __str__(self) -> str:
        return f"{self.value:g} [{self.tag.label}]"
