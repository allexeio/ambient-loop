"""Arrangement schema: a hydraulic arrangement as composable blocks.

An arrangement is a set of circuits (ambient loop, CHW, LTHW, heat-recovery circuit, DHW
primary) and the plant that links them. The same object drives four things: the simulation,
the rules engine, the auto-generated schematic and the generator's record of why it was
proposed. Any block can be omitted. The archetypes in arrangements/archetypes.py are presets
of these blocks.

Temperatures: an ambient loop is described by its flow (warm) and return (cool) temperatures.
Heat pumps in heating evaporate against the loop RETURN (leaving temperature). Chillers
rejecting to the loop condense against the loop FLOW (leaving temperature). This matches the
fixture selections: heating source 25/20, cooling sink 20/25. [A-032]
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSpec(_S):
    """Performance model: a library reference or inline parameters."""

    library: str | None = None
    type: Literal["fixed", "carnot", "air_source", "map", "co2"] | None = None
    params: dict[str, Any] = {}
    tag: str | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def _c(self) -> ModelSpec:
        if (self.library is None) == (self.type is None):
            raise ValueError("model: give exactly one of library or type")
        return self


class PumpSpec(_S):
    head_kPa: float = 150.0
    dT_K: float | None = None  # circuit design ΔT; defaults from the circuit temperatures
    design_flow_m3s: float | None = None  # defaults from peak duty
    control: Literal["variable", "constant"] = "variable"
    min_speed: float = 0.3
    eta_pump: float = 0.70
    eta_motor_drive: float = 0.88


class LoopSpec(_S):
    mode: Literal["fixed", "outdoor_reset", "monthly"] = "fixed"
    flow_C: float = 25.0
    return_C: float = 20.0
    # outdoor_reset: pairs [T_db, flow_C]; the return keeps the same ΔT
    reset: list[tuple[float, float]] = []
    monthly_flow_C: list[float] = []
    fluid: str = "water"
    pump: PumpSpec = PumpSpec(head_kPa=250.0)
    unit_pumps_included: bool = False  # in-unit pumps already in machine ratings


class CircuitSpec(_S):
    flow_C: float
    return_C: float
    fluid: str = "water"
    pump: PumpSpec = PumpSpec()


class FleetSpec(_S):
    model: ModelSpec
    count: int = 1
    unit_kW: float = 100.0  # rated heating (condenser) output per unit
    sequencing: Literal["lead_lag", "equal_share"] = "lead_lag"
    min_flow_m3s: float | None = None  # per unit, condenser side (rule 4 check)
    source: Literal["loop", "air", "hr_changeover", "hr_dedicated"] = "loop"  # heating / reheat source


class CoolingSpec(FleetSpec):
    """Reversible water-to-water units serving CHW. In heat recovery they condense at hr_flow_C."""

    hr_model: ModelSpec | None = None  # simplified mode only: a separate fixed EER for HR mode
    sink: Literal["loop", "air"] = "loop"  # reject to the ambient loop, or air-cooled (stand-alone chiller)
    hr_target: Literal["none", "lthw", "hr_circuit"] = "none"
    shared_with_heating: bool = False  # the same units also serve LTHW (they compete)


class HRCircuitSpec(_S):
    """Heat-recovery circuit: condensers in HR mode deliver at flow_C."""

    flow_C: float = 45.0
    return_C: float = 40.0
    priority: list[Literal["space_heating", "dhw_preheat", "store", "dhw_hp_source", "reject"]] = [
        "space_heating", "dhw_preheat", "store", "dhw_hp_source", "reject"]
    pump: PumpSpec = PumpSpec(head_kPa=120.0)


class StoreSpec(_S):
    volume_m3: float = 10.0
    nodes: int = Field(6, ge=1)
    t_return_C: float = 15.0  # reference (design return) temperature
    ua_W_K: float = 15.0
    t_amb_C: float = 18.0
    substeps: int = 4
    potable: bool = False


class PreheatSpec(_S):
    enabled: bool = True
    max_C: float = 40.0  # cold-feed outlet set-point
    approach_K: float = 5.0
    stores_potable: bool = False  # True = a warm potable preheat vessel (HSG274 flag)


class DHWGenSpec(FleetSpec):
    type: Literal["wshp", "co2", "cascade", "electric"] = "wshp"
    hr_units: int = 1  # for hr_dedicated
    hr_model: ModelSpec | None = None  # simplified mode only: a fixed COP on the HR source
    intermediate_C: float = 45.0  # cascade
    stage1: FleetSpec | None = None  # cascade first stage (loop → intermediate)


class DHWSpec(_S):
    store_C: float = 65.0
    generator_flow_C: float | None = None  # primary flow; defaults to store_C (coil calorifier)
    circulation_fraction: float = 0.0  # share of the DHW stream that is circulation loss
    circulation_return_C: float = 55.0
    preheat: PreheatSpec | None = None
    generator: DHWGenSpec
    reheat: FleetSpec | None = None  # circulation losses (CO₂ schemes); None = the generator does it
    calorifier_m3: float = 0.0
    calorifier_usable_fraction: float = 0.75
    calorifier_ua_W_K: float = 30.0
    pump: PumpSpec = PumpSpec(head_kPa=100.0)


class BalancingSpec(_S):
    type: Literal["ashp", "dry_cooler", "tower", "ashp_cooling", "ground", "external"]
    fleet: FleetSpec | None = None  # ashp / ashp_cooling
    rated_kW: float | None = None  # dry cooler / tower / external
    rated_itd_K: float = 8.0
    rated_fan_kW: float = 0.0
    approach_min_K: float = 5.0
    ground: dict[str, Any] = {}  # BoreholeField parameters
    external_T_C: float = 25.0  # e.g. greywater
    external_column: str | None = None  # hourly available kW column in the loads table
    pump_kW_per_kW: float = 0.0  # listed pump allowance per kW moved
    # simple-mode dry cooler (workbook screening): full rated capacity at or below a fixed
    # dry-bulb cutoff, electricity per kW rejected. Overrides the ITD/approach physics.
    simple_cutoff_C: float | None = None
    simple_kW_per_kW: float | None = None


class Arrangement(_S):
    id: str
    archetype: str
    title: str
    why: list[str] = []
    relies_on: list[str] = []
    loop: LoopSpec | None = LoopSpec()
    chw: CircuitSpec | None = CircuitSpec(flow_C=10.0, return_C=16.0)
    lthw: CircuitSpec | None = CircuitSpec(flow_C=45.0, return_C=40.0)
    cooling: CoolingSpec | None = None
    heating: FleetSpec | None = None
    hr: HRCircuitSpec | None = None
    buffer: StoreSpec | None = None
    dhw: DHWSpec | None = None
    deficit: list[BalancingSpec] = []
    surplus: list[BalancingSpec] = []
    reuse: bool = True
    pumps_included: bool = True

    @model_validator(mode="after")
    def _c(self) -> Arrangement:
        if self.cooling and self.cooling.hr_target == "hr_circuit" and self.hr is None:
            raise ValueError(f"{self.id}: cooling.hr_target=hr_circuit needs an hr block")
        if self.hr and "dhw_preheat" in self.hr.priority and "dhw_hp_source" in self.hr.priority:
            if self.hr.priority.index("dhw_preheat") > self.hr.priority.index("dhw_hp_source"):
                raise ValueError(f"{self.id}: dhw_preheat must come before dhw_hp_source")
        if self.dhw and self.dhw.generator.type == "cascade" and self.dhw.generator.stage1 is None:
            raise ValueError(f"{self.id}: cascade generator needs stage1")
        return self
