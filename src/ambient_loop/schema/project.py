"""Project file schema (Phase 1 subset: streams, simple-mode plant, targets).

The project YAML is the contract. Units are fixed by field-name suffix: _kW, _kWh, _C, _K.
Full-physics components, graphs and scenario sets are added in later phases, without breaking
this subset.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ambient_loop.core.curves import Curve1D
from ambient_loop.core.provenance import Tag, TaggedFloat


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StreamKind(StrEnum):
    SPACE_HEATING = "space_heating"
    SPACE_COOLING = "space_cooling"
    DHW = "dhw"
    DHW_CIRCULATION = "dhw_circulation"
    PROCESS_HEATING = "process_heating"
    PROCESS_COOLING = "process_cooling"
    EXHAUST_AIR = "exhaust_air"

    @property
    def is_cooling(self) -> bool:
        return self in (StreamKind.SPACE_COOLING, StreamKind.PROCESS_COOLING)

    @property
    def default_service(self) -> str:
        return {
            StreamKind.SPACE_HEATING: "LTHW",
            StreamKind.SPACE_COOLING: "CHW",
            StreamKind.DHW: "DHW",
            StreamKind.DHW_CIRCULATION: "DHW",
            StreamKind.PROCESS_HEATING: "PROCESS_HEAT",
            StreamKind.PROCESS_COOLING: "PROCESS_COOL",
            StreamKind.EXHAUST_AIR: "EXHAUST",
        }[self]


class SeriesRef(_Strict):
    """Pointer to a column in the project's loads table."""

    column: str
    scale: float = 1.0


class Stream(_Strict):
    """A load. Temperatures are where the stream enters and leaves the exchange.

    A hot stream (T_in > T_out) gives heat, e.g. CHW return 12 °C → supply 6 °C. A cold stream
    takes heat, e.g. DHW make-up 10 → 60 °C. Temperatures are carried from Phase 1 so the
    recovery analysis (Phase 2) can use the same file. Simple mode does not use them.
    """

    id: str
    kind: StreamKind
    owner: str = "building"
    service: str | None = None  # reporting group; defaults from kind (LTHW / CHW / DHW ...)
    duty_kW: SeriesRef
    T_in_C: float | Literal["mains"] | None = None
    T_out_C: float | None = None
    exchange_class: Literal["liquid_liquid", "air_liquid"] = "liquid_liquid"
    tag: Tag = Tag.PROJECT_DATA

    @property
    def service_name(self) -> str:
        return self.service or self.kind.default_service


class WeatherSource(_Strict):
    file: Path
    format: Literal["epw", "table"] = "epw"
    # for format == "table": column holding dry bulb (°C), optional sheet for xlsx
    dry_bulb_column: str | None = None
    sheet: str | None = None
    header_row: int = 0
    tag: Tag = Tag.PROJECT_DATA


class LoadsSource(_Strict):
    file: Path
    sheet: str | None = None
    header_row: int = 0
    drop_feb29: bool = True


class UnitKind(StrEnum):
    HEAT_PUMP = "heat_pump"  # takes heat from the loop, delivers to its streams
    CHILLER = "chiller"  # removes heat from its streams, rejects to the loop
    DIRECT_ELECTRIC = "direct_electric"


class SimpleUnit(_Strict):
    """A terminal unit or plant item in simple mode, at a fixed efficiency.

    `efficiency` is the heating COP for heat pumps, the EER for chillers and 1.0 for direct
    electric. It is fixed in simple mode, by definition.
    """

    id: str
    kind: UnitKind
    serves: list[str] = Field(min_length=1)
    efficiency: TaggedFloat

    @model_validator(mode="after")
    def _check(self) -> SimpleUnit:
        if self.efficiency.value <= 0:
            raise ValueError(f"{self.id}: efficiency must be positive")
        return self


class BalancingKind(StrEnum):
    ASHP_HEATING = "ashp_heating"  # adds heat to the loop from outdoor air
    DRY_COOLER = "dry_cooler"  # removes heat from the loop to outdoor air
    ASHP_COOLING = "ashp_cooling"  # removes heat from the loop, compressor-driven


class BalancingPlant(_Strict):
    id: str
    kind: BalancingKind
    capacity_kW: float | None = None  # None = unlimited (workbook simple mode)
    # ASHP heating: COP vs dry bulb. ASHP cooling: EER vs dry bulb (or fixed).
    efficiency_curve: Curve1D | None = None
    efficiency: TaggedFloat | None = None
    # Dry cooler: available only when dry bulb <= cutoff (inclusive; simple mode uses 27 °C).
    cutoff_dry_bulb_C: float | None = None
    # Dry cooler: fan (+ coil pump) electricity per kW rejected, always counted
    fan_kW_per_kW: TaggedFloat = TaggedFloat(value=0.0)
    # Separately listed circulation pumps, per kW of heat moved. Counted only when
    # plant.pumps_included is true. Pump heat is not returned to the loop in simple mode.
    pump_kW_per_kW: TaggedFloat = TaggedFloat(value=0.0)

    @model_validator(mode="after")
    def _check(self) -> BalancingPlant:
        needs_eff = self.kind in (BalancingKind.ASHP_HEATING, BalancingKind.ASHP_COOLING)
        if needs_eff and (self.efficiency_curve is None) == (self.efficiency is None):
            raise ValueError(f"{self.id}: give exactly one of efficiency or efficiency_curve")
        if self.kind is BalancingKind.DRY_COOLER and self.cutoff_dry_bulb_C is None:
            raise ValueError(f"{self.id}: dry cooler needs cutoff_dry_bulb_C")
        return self


class SimplePlant(_Strict):
    """Shared ambient loop in simple mode.

    Simplifications: fixed unit efficiencies; same-hour reuse on the loop, scaled by
    reuse_fraction (1 = ideal matching, 0 = none); no storage.
    """

    units: list[SimpleUnit]
    deficit_plant: list[BalancingPlant] = []  # dispatched in order when the loop is short
    surplus_plant: list[BalancingPlant] = []  # dispatched in order when the loop is long
    reuse_fraction: TaggedFloat = TaggedFloat(value=1.0)
    pumps_included: bool = False

    @model_validator(mode="after")
    def _check(self) -> SimplePlant:
        if not 0.0 <= self.reuse_fraction.value <= 1.0:
            raise ValueError("reuse_fraction must be between 0 and 1")
        return self


class StorageSweep(_Strict):
    capacities_kWh: list[float] = [0, 250, 500, 1000, 2000, 4000]
    windows_h: list[int] = [2, 6, 12, 24]
    loss_fraction_per_h: float = 0.005  # [Assumption A-016] standing loss of stored energy


class RecoverySettings(_Strict):
    """Settings for the hourly pinch / recovery analysis (Phase 2)."""

    dT_min_K: dict[str, float] = {"liquid_liquid": 2.0, "air_liquid": 5.0}  # [A-001]
    grid_K: float = 0.5  # problem-table resolution
    band_K: float = 2.5  # heat-pump matching band width [A-002]
    eta_carnot: float = 0.50  # generic second-law efficiency for screening [A-014]
    hp_approach_K: float = 3.0  # refrigerant-to-fluid approach each side [A-015]
    storage: StorageSweep = StorageSweep()


class Target(_Strict):
    service: str  # e.g. LTHW, DHW, CHW, SYSTEM
    seasonal_ratio: float
    allocation: Literal["pro_rata", "incremental", "system"] = "incremental"


class Project(_Strict):
    name: str
    description: str = ""
    mode: Literal["simple"] = "simple"
    loads: LoadsSource
    weather: WeatherSource
    mains_water_C: TaggedFloat | list[float] = TaggedFloat(value=10.0)
    streams: list[Stream]
    plant: SimplePlant
    targets: list[Target] = []
    recovery: RecoverySettings = RecoverySettings()

    @model_validator(mode="after")
    def _check_refs(self) -> Project:
        ids = [s.id for s in self.streams]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate stream ids")
        served: dict[str, str] = {}
        for u in self.plant.units:
            for sid in u.serves:
                if sid not in ids:
                    raise ValueError(f"unit {u.id} serves unknown stream {sid}")
                if sid in served:
                    raise ValueError(f"stream {sid} served by both {served[sid]} and {u.id}")
                served[sid] = u.id
                kind = next(s.kind for s in self.streams if s.id == sid)
                if kind.is_cooling != (u.kind is UnitKind.CHILLER):
                    raise ValueError(f"unit {u.id} ({u.kind.value}) cannot serve {sid}")
        unserved = set(ids) - set(served)
        if unserved:
            raise ValueError(f"streams with no unit: {sorted(unserved)}")
        plant_ids = (
            [u.id for u in self.plant.units]
            + [b.id for b in self.plant.deficit_plant]
            + [b.id for b in self.plant.surplus_plant]
        )
        if len(plant_ids) != len(set(plant_ids)):
            raise ValueError("duplicate plant ids")
        for b in self.plant.deficit_plant:
            if b.kind is not BalancingKind.ASHP_HEATING:
                raise ValueError(f"{b.id}: only ashp_heating can serve a loop deficit")
        for b in self.plant.surplus_plant:
            if b.kind is BalancingKind.ASHP_HEATING:
                raise ValueError(f"{b.id}: ashp_heating cannot reject loop surplus")
        return self


def load_project(path: Path) -> tuple[Project, Path]:
    """Load and validate a project file. Returns (project, project directory)."""
    with path.open() as f:
        raw = yaml.safe_load(f)
    return Project.model_validate(raw), path.parent
