"""Archetype builders (brief §4.2) from a site plant basis.

A SiteBasis holds the project's temperatures and plant selections (or generic defaults).
Each builder returns an Arrangement with its reasons (`why`) and the assumptions it relies on
(`relies_on`). The generator (Phase 5) picks among these using the recovery analysis.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from ambient_loop.schema.arrangement import (
    Arrangement,
    BalancingSpec,
    CircuitSpec,
    CoolingSpec,
    DHWGenSpec,
    DHWSpec,
    FleetSpec,
    HRCircuitSpec,
    LoopSpec,
    ModelSpec,
    PreheatSpec,
    PumpSpec,
    StoreSpec,
)


def lib(name: str) -> ModelSpec:
    return ModelSpec(library=name)


class SiteBasis(BaseModel):
    """Temperatures and plant selections that every archetype for a site shares."""

    model_config = ConfigDict(extra="forbid")
    loop_flow_C: float = 25.0
    loop_return_C: float = 20.0
    loop_fluid: str = "water"
    loop_pump_kPa: float = 250.0
    chw_flow_C: float = 10.0
    chw_return_C: float = 16.0
    lthw_flow_C: float = 45.0
    lthw_return_C: float = 40.0
    hr_flow_C: float = 45.0
    hr_return_C: float = 40.0
    dhw_store_C: float = 65.0
    dhw_preheat_C: float = 40.0
    circulation_fraction: float = 0.0
    cooling_model: ModelSpec = lib("wshp_scroll_inverter")
    cooling_hr_model: ModelSpec | None = None
    cooling_count: int = 3
    cooling_unit_kW: float = 326.0
    heating_model: ModelSpec | None = None  # set = separate LTHW fleet (not shared reversible units)
    dry_cooler_simple_cutoff_C: float | None = None
    dry_cooler_simple_kW_per_kW: float | None = None
    dhw_model: ModelSpec = lib("wshp_screw_ht")
    dhw_hr_model: ModelSpec | None = None
    dhw_count: int = 3
    dhw_unit_kW: float = 220.5
    stage1_model: ModelSpec = lib("wshp_scroll_inverter")
    co2_model: ModelSpec = lib("mitsubishi_qahv_n560_co2")
    co2_unit_kW: float = 40.0
    co2_count: int = 20
    reheat_model: ModelSpec = lib("wshp_scroll_inverter")
    ashp_model: ModelSpec = lib("ashp_air_to_water")
    ashp_count: int = 4
    ashp_unit_kW: float = 622.5
    ashp_cooling_model: ModelSpec = lib("chiller_air_cooled")
    ashp_cooling_count: int = 4
    ashp_cooling_unit_kW: float = 850.0
    dry_cooler_kW: float = 800.0
    dry_cooler_itd_K: float = 8.0
    dry_cooler_fan_kW: float = 80.75
    dry_cooler_approach_K: float = 5.0
    buffer_m3: float = 10.0
    calorifier_m3: float = 15.0
    greywater_T_C: float = 25.0
    greywater_column: str | None = None
    greywater_kW: float = 50.0
    ground: dict[str, float | int | str] = {"n_bores": 40, "depth_m": 200.0}
    pumps_included: bool = True


# ---- blocks ---------------------------------------------------------------------------------
def _loop(b: SiteBasis, **kw: object) -> LoopSpec:
    d: dict[str, object] = {"flow_C": b.loop_flow_C, "return_C": b.loop_return_C, "fluid": b.loop_fluid,
                            "pump": PumpSpec(head_kPa=b.loop_pump_kPa)}
    d.update(kw)
    return LoopSpec(**d)  # type: ignore[arg-type]


def _cooling(b: SiteBasis, hr_target: str = "none") -> CoolingSpec:
    return CoolingSpec(model=b.cooling_model, hr_model=b.cooling_hr_model, count=b.cooling_count,
                       unit_kW=b.cooling_unit_kW, hr_target=hr_target,  # type: ignore[arg-type]
                       shared_with_heating=b.heating_model is None)


def _heating(b: SiteBasis) -> FleetSpec | None:
    if b.heating_model is None:
        return None
    return FleetSpec(model=b.heating_model, count=b.cooling_count, unit_kW=b.cooling_unit_kW)


def _dhw(b: SiteBasis, gen: DHWGenSpec | None = None, **kw: object) -> DHWSpec:
    gen = gen or DHWGenSpec(model=b.dhw_model, count=b.dhw_count, unit_kW=b.dhw_unit_kW)
    d: dict[str, object] = {"store_C": b.dhw_store_C, "generator": gen, "calorifier_m3": b.calorifier_m3,
                            "circulation_fraction": b.circulation_fraction}
    d.update(kw)
    return DHWSpec(**d)  # type: ignore[arg-type]


def _ashp(b: SiteBasis) -> BalancingSpec:
    return BalancingSpec(type="ashp", fleet=FleetSpec(model=b.ashp_model, count=b.ashp_count, unit_kW=b.ashp_unit_kW))


def _dry(b: SiteBasis) -> BalancingSpec:
    return BalancingSpec(type="dry_cooler", rated_kW=b.dry_cooler_kW, rated_itd_K=b.dry_cooler_itd_K,
                         rated_fan_kW=b.dry_cooler_fan_kW, approach_min_K=b.dry_cooler_approach_K,
                         simple_cutoff_C=b.dry_cooler_simple_cutoff_C, simple_kW_per_kW=b.dry_cooler_simple_kW_per_kW)


def _ashp_cool(b: SiteBasis) -> BalancingSpec:
    return BalancingSpec(type="ashp_cooling", fleet=FleetSpec(model=b.ashp_cooling_model, count=b.ashp_cooling_count,
                                                             unit_kW=b.ashp_cooling_unit_kW))


def _base(b: SiteBasis, **kw: object) -> dict[str, object]:
    d: dict[str, object] = {
        "loop": _loop(b),
        "chw": CircuitSpec(flow_C=b.chw_flow_C, return_C=b.chw_return_C),
        "lthw": CircuitSpec(flow_C=b.lthw_flow_C, return_C=b.lthw_return_C),
        "cooling": _cooling(b),
        "heating": _heating(b),
        "dhw": _dhw(b),
        "deficit": [_ashp(b)],
        "surplus": [_dry(b), _ashp_cool(b)],
        "pumps_included": b.pumps_included,
    }
    d.update(kw)
    return d


def _hr(b: SiteBasis, priority: list[str]) -> HRCircuitSpec:
    return HRCircuitSpec(flow_C=b.hr_flow_C, return_C=b.hr_return_C, priority=priority)  # type: ignore[arg-type]


# ---- archetypes -------------------------------------------------------------------------------
def a1_shared_loop(b: SiteBasis) -> Arrangement:
    return Arrangement(id="A1", archetype="1", title="Shared ambient loop",
                       why=["Baseline: every heat pump on the loop; reuse is limited to same-hour net balance."],
                       relies_on=["Loop at fixed flow/return temperatures", "ASHP balances the loop deficit"],
                       **_base(b))  # type: ignore[arg-type]


def a2_hr_lthw_buffer(b: SiteBasis, volume_m3: float | None = None) -> Arrangement:
    return Arrangement(id="A2", archetype="2", title="Loop + heat recovery to stratified LTHW buffer",
                       why=["Cooling condensers charge a stratified buffer that serves space heating first."],
                       relies_on=["Units can condense at 45 °C on the source side", "Buffer stays stratified"],
                       hr=_hr(b, ["space_heating", "store", "reject"]),
                       buffer=StoreSpec(volume_m3=volume_m3 if volume_m3 is not None else b.buffer_m3),
                       **_base(b, cooling=_cooling(b, "hr_circuit")))  # type: ignore[arg-type]


def a3_hr_preheat(b: SiteBasis) -> Arrangement:
    return Arrangement(id="A3", archetype="3", title="Heat recovery to DHW preheat + DHW HP top-up from loop",
                       why=["Recovered 45 °C heat preheats the cold feed by heat exchange, with no compressor (rule 2)."],
                       relies_on=["Preheat plate HX on the cold feed (no warm potable storage)",
                                  "DHW secondary return bypasses preheat (rule 8)"],
                       hr=_hr(b, ["space_heating", "dhw_preheat", "reject"]),
                       **_base(b, cooling=_cooling(b, "hr_circuit"),
                               dhw=_dhw(b, preheat=PreheatSpec(max_C=b.dhw_preheat_C))))  # type: ignore[arg-type]


def a23_buffer_preheat(b: SiteBasis, volume_m3: float | None = None, store_C: float | None = None) -> Arrangement:
    """The Option B principle schematic: HR → buffer → LTHW first → preheat → DHW HP top-up from loop."""
    bb = b.model_copy(update={"dhw_store_C": store_C}) if store_C else b
    v = volume_m3 if volume_m3 is not None else b.buffer_m3
    return Arrangement(id="A2+3" + (f"-{v:g}m3" if volume_m3 is not None else "") + (f"-{store_C:g}C" if store_C else ""),
                       archetype="2+3", title="HR → stratified buffer → LTHW first → DHW preheat → DHW HP top-up",
                       why=["Captures recovered heat directly where its temperature allows (rule 2) and stores it on "
                            "the primary side (rule 7)."],
                       relies_on=["Units condense at 45 °C to buffer top", "Buffer return about 15 °C from the PHX",
                                  "Stratification maintained (≥ 5 sensors)"],
                       hr=_hr(bb, ["space_heating", "dhw_preheat", "store", "reject"]),
                       buffer=StoreSpec(volume_m3=v),
                       **_base(bb, cooling=_cooling(bb, "hr_circuit"),
                               dhw=_dhw(bb, preheat=PreheatSpec(max_C=bb.dhw_preheat_C))))  # type: ignore[arg-type]


def a4_hr_dhw_source(b: SiteBasis, changeover: bool = True) -> Arrangement:
    gen = DHWGenSpec(model=b.dhw_model, hr_model=b.dhw_hr_model, count=b.dhw_count, unit_kW=b.dhw_unit_kW,
                     source="hr_changeover" if changeover else "hr_dedicated", hr_units=1)
    return Arrangement(id="A4" if changeover else "A4-nc", archetype="4",
                       title="Heat recovery as DHW HP source" + (" with loop changeover" if changeover else ", dedicated unit (no changeover)"),
                       why=["A warmer source (45/40 °C) raises DHW heat-pump COP; changeover keeps the loop as backup."],
                       relies_on=["Units condense at 45 °C", "DHW HP envelope allows a 40 °C leaving source"],
                       hr=_hr(b, ["space_heating", "dhw_hp_source", "reject"]),
                       **_base(b, cooling=_cooling(b, "hr_circuit"), dhw=_dhw(b, gen)))  # type: ignore[arg-type]


def a5_cascade(b: SiteBasis) -> Arrangement:
    gen = DHWGenSpec(type="cascade", model=b.dhw_model, hr_model=b.dhw_hr_model, count=b.dhw_count, unit_kW=b.dhw_unit_kW,
                     intermediate_C=b.hr_flow_C,
                     stage1=FleetSpec(model=b.stage1_model, count=b.dhw_count, unit_kW=b.dhw_unit_kW))
    return Arrangement(id="A5", archetype="5", title="Cascade: loop → 45 °C → 65 °C (known-poor baseline)",
                       why=["Included so the double-lift penalty is explicit (rule 1)."],
                       relies_on=["Intermediate heat is generated, not recovered"],
                       **_base(b, dhw=_dhw(b, gen)))  # type: ignore[arg-type]


def a6_four_pipe_hrc(b: SiteBasis) -> Arrangement:
    return Arrangement(id="A6", archetype="6", title="4-pipe heat-recovery chiller linking CHW and LTHW",
                       why=["Simultaneous heating and cooling served by one machine with no loop transfer."],
                       relies_on=["Coincident LTHW and CHW demand"],
                       **_base(b, cooling=_cooling(b, "lthw")))  # type: ignore[arg-type]


def a7_co2_dhw(b: SiteBasis, reheat: bool = True) -> Arrangement:
    gen = DHWGenSpec(type="co2", source="air", model=b.co2_model, count=b.co2_count, unit_kW=b.co2_unit_kW)
    rh = FleetSpec(model=b.reheat_model, count=2, unit_kW=max(b.dhw_unit_kW, 100.0)) if reheat else None
    cf = b.circulation_fraction or 0.25
    return Arrangement(id="A7" if reheat else "A7-noreheat", archetype="7",
                       title="CO₂ heat pumps for DHW make-up" + (" + separate circulation reheat" if reheat else " (CO₂ also reheats circulation)"),
                       why=["CO₂ is efficient heating cold mains to 65 °C, but not reheating 55 °C circulation (rule 4b)."],
                       relies_on=[f"Circulation losses {cf:.0%} of DHW energy [Assumption A-033]", "Air-source CO₂ units on roof"],
                       **_base(b, dhw=_dhw(b, gen, reheat=rh, circulation_fraction=cf)))  # type: ignore[arg-type]


def a8_external_sources(b: SiteBasis) -> Arrangement:
    ext = BalancingSpec(type="external", rated_kW=b.greywater_kW, external_T_C=b.greywater_T_C,
                        external_column=b.greywater_column, approach_min_K=3.0, pump_kW_per_kW=0.02)
    gnd = BalancingSpec(type="ground", ground=dict(b.ground), approach_min_K=3.0, pump_kW_per_kW=0.03)
    lp = _loop(b, flow_C=16.0, return_C=12.0)
    return Arrangement(id="A8", archetype="8", title="External sources: greywater recovery + borehole field, loop floated to 16/12 °C",
                       why=["Ground and wastewater can only feed the loop if it runs below their temperature."],
                       relies_on=["Greywater available at ≥ 25 °C", "Borehole field sized by a design tool (A-027)"],
                       **_base(b, loop=lp, deficit=[ext, gnd, _ashp(b)], surplus=[gnd, _dry(b), _ashp_cool(b)]))  # type: ignore[arg-type]


def a9_floating_loop(b: SiteBasis) -> Arrangement:
    lp = _loop(b, mode="outdoor_reset", reset=[(-5.0, 18.0), (10.0, 22.0), (25.0, 30.0)])
    return Arrangement(id="A9-float", archetype="9", title="Loop floated with outdoor temperature (18–30 °C)",
                       why=["A cooler loop in winter helps the ASHP; a warmer loop in summer helps free rejection."],
                       relies_on=["Units' envelopes cover the float range"], **_base(b, loop=lp))  # type: ignore[arg-type]


def a9_low_lthw(b: SiteBasis) -> Arrangement:
    return Arrangement(id="A9-lthw40", archetype="9", title="Lower LTHW flow (40/35 °C)",
                       why=["Lower condensing temperature raises LTHW COP (rule 4)."],
                       relies_on=["Emitters sized for 40/35 °C"],
                       **_base(b, lthw=CircuitSpec(flow_C=40.0, return_C=35.0)))  # type: ignore[arg-type]


def a9_dhw60(b: SiteBasis) -> Arrangement:
    bb = b.model_copy(update={"dhw_store_C": 60.0})
    return Arrangement(id="A9-dhw60", archetype="9", title="DHW stored at 60 °C instead of 65 °C",
                       why=["Lower DHW condensing temperature; still HSG274-compliant at 60 °C storage."],
                       relies_on=["Distribution ≥ 50 °C at outlets (≥ 55 °C healthcare)"],
                       **_base(bb))  # type: ignore[arg-type]


def a10_no_dry_cooler(b: SiteBasis) -> Arrangement:
    return Arrangement(id="A10-nodc", archetype="10", title="Rejection by ASHP only (no dry cooler)",
                       why=["Tests whether the dry cooler earns its place on a loop short of heat (rule 9)."],
                       relies_on=[], **_base(b, surplus=[_ashp_cool(b)]))  # type: ignore[arg-type]


def a10_ground_reject(b: SiteBasis) -> Arrangement:
    gnd = BalancingSpec(type="ground", ground=dict(b.ground), approach_min_K=3.0, pump_kW_per_kW=0.03)
    return Arrangement(id="A10-ground", archetype="10", title="Rejection to borehole field instead of air",
                       why=["Ground rejection at 25 °C loop without fans."],
                       relies_on=["Borehole field sized by a design tool (A-027)"],
                       **_base(b, surplus=[gnd, _ashp_cool(b)]))  # type: ignore[arg-type]


ARCHETYPES: dict[str, Callable[[SiteBasis], Arrangement]] = {
    "A1": a1_shared_loop,
    "A2": a2_hr_lthw_buffer,
    "A3": a3_hr_preheat,
    "A2+3": a23_buffer_preheat,
    "A4": a4_hr_dhw_source,
    "A4-nc": lambda b: a4_hr_dhw_source(b, changeover=False),
    "A5": a5_cascade,
    "A6": a6_four_pipe_hrc,
    "A7": a7_co2_dhw,
    "A7-noreheat": lambda b: a7_co2_dhw(b, reheat=False),
    "A8": a8_external_sources,
    "A9-float": a9_floating_loop,
    "A9-lthw40": a9_low_lthw,
    "A9-dhw60": a9_dhw60,
    "A10-nodc": a10_no_dry_cooler,
    "A10-ground": a10_ground_reject,
}
