"""Central plant strategies for step 4: every combination of heating, cooling and DHW plant on the
engineer's list, screened for compatibility with the chosen terminals.

Options
  Heating: gas boilers | ASHP (heating only) | 2-pipe reversible ASHP | 4-pipe ASHP with heat
           recovery | VRF heat recovery | WSHPs on an ambient loop
  Cooling: air-cooled chillers | 2-pipe ASHP | 4-pipe ASHP | VRF | water-cooled chillers + dry
           air coolers | water-cooled chillers + cooling towers | WSHPs on an ambient loop
  DHW:     gas | HT air-source heat pump ("ASHP for heating and hot water" / "ASHP for DHW only")
           | CO₂ air-source heat pump | WSHP on the ambient loop | direct electric (reference only)
  Ambient-loop balancing: 2-pipe or 4-pipe ASHP (heat source and rejection) | dry air coolers
           for rejection, with ASHP heat source | loop + heat recovery to buffer and DHW preheat

Coupling rules: 2-pipe, 4-pipe and VRF units do both heating and cooling; WSHP-on-loop heating
goes with WSHP-on-loop cooling; loop DHW needs a loop. Compatibility notes tell the engineer when
a strategy conflicts with the terminal choice (e.g. VRF needs refrigerant terminals; a 2-pipe
changeover cannot serve 4-pipe terminals that need simultaneous heating and cooling).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ambient_loop.arrangements import archetypes as A
from ambient_loop.arrangements.archetypes import SiteBasis, lib
from ambient_loop.schema.arrangement import (
    Arrangement,
    BalancingSpec,
    CircuitSpec,
    CoolingSpec,
    DHWGenSpec,
    FleetSpec,
    LoopSpec,
    PumpSpec,
)

HEAT = {"gas": "Gas boilers", "ashp": "ASHP heating only", "ashp2": "2-pipe reversible ASHP",
        "ashp4": "4-pipe ASHP with heat recovery", "vrf": "VRF heat recovery", "loop": "WSHPs on ambient loop"}
COOL = {"none": "No cooling", "acc": "Air-cooled chillers", "ashp2": "2-pipe reversible ASHP",
        "ashp4": "4-pipe ASHP with heat recovery", "vrf": "VRF heat recovery",
        "wcc_dc": "Water-cooled chillers + dry air coolers", "wcc_ct": "Water-cooled chillers + cooling towers",
        "loop": "WSHPs on ambient loop"}
DHW = {"gas": "Gas", "ashp_ht": "HT air-source heat pumps", "co2": "CO₂ air-source heat pumps",
       "loop": "WSHPs on ambient loop", "electric": "Direct electric (reference)"}
BAL = {"": "", "ashp": "reversible ASHPs balance the loop", "dc": "dry air coolers reject, ASHPs supply heat",
       "hr": "heat recovery to buffer and DHW preheat, ASHPs balance"}


@dataclass
class Strategy:
    heat: str
    cool: str
    dhw: str
    bal: str = ""
    notes: list[str] = field(default_factory=list)  # compatibility notes
    compatible: bool = True

    @property
    def id(self) -> str:
        return "/".join(x for x in (self.heat, self.cool, self.dhw, self.bal) if x)

    @property
    def title(self) -> str:
        if self.heat == self.cool and self.heat in ("ashp2", "ashp4", "vrf", "loop"):
            t = HEAT[self.heat] + (" (" + BAL[self.bal] + ")" if self.bal else "")
        else:
            t = HEAT[self.heat] + (" + " + COOL[self.cool] if self.cool != "none" else "")
        return f"{t}; DHW: {DHW[self.dhw]}"


def enumerate_strategies(has_cooling: bool, has_dhw: bool, simultaneous: bool, refrigerant_terminals: bool) -> list[Strategy]:
    out: list[Strategy] = []
    dhw_opts_gas = ["gas", "ashp_ht", "co2"] if has_dhw else ["gas"]
    dhw_opts_hp = ["ashp_ht", "co2"] if has_dhw else ["ashp_ht"]
    cools = ["acc", "wcc_dc", "wcc_ct"] if has_cooling else ["none"]
    for c in cools:
        for d in dhw_opts_gas:
            out.append(Strategy("gas", c, d))
        for d in dhw_opts_hp:
            out.append(Strategy("ashp", c, d))
    if has_cooling:
        for u in ("ashp2", "ashp4", "vrf"):
            for d in dhw_opts_hp:
                out.append(Strategy(u, u, d))
        for bal in ("ashp", "dc"):
            for d in (["loop", "co2"] if has_dhw else ["loop"]):
                out.append(Strategy("loop", "loop", d, bal))
        if has_dhw:
            out.append(Strategy("loop", "loop", "loop", "hr"))
    if has_dhw:
        out.append(Strategy("ashp", "acc" if has_cooling else "none", "electric"))
    for s in out:
        if s.heat == "vrf" and not refrigerant_terminals:
            s.notes.append("VRF needs refrigerant (DX) terminals; the selected terminals are water-based.")
            s.compatible = False
        if s.heat != "vrf" and refrigerant_terminals:
            s.notes.append("Refrigerant terminals selected: only VRF plant can serve them directly.")
            s.compatible = False
        if s.heat == "ashp2" and simultaneous:
            s.notes.append("2-pipe changeover cannot give heating and cooling at the same time, which the 4-pipe terminals expect.")
            s.compatible = False
        if s.dhw == "electric":
            s.notes.append("Direct electric DHW: reference only; GLA para 9.10 will not accept it in most cases.")
        if s.heat == "gas" or s.dhw == "gas":
            s.notes.append("Gas combustion on site: no 'be green' saving and an air-quality assessment (GLA guidance §9).")
    return out


def _stand(b: SiteBasis, **kw: object) -> dict[str, object]:
    d: dict[str, object] = {
        "loop": None,
        "chw": CircuitSpec(flow_C=b.chw_flow_C, return_C=b.chw_return_C),
        "lthw": CircuitSpec(flow_C=b.lthw_flow_C, return_C=b.lthw_return_C),
        "deficit": [], "surplus": [], "pumps_included": b.pumps_included,
    }
    d.update(kw)
    return d


def _dhw_spec(b: SiteBasis, kind: str) -> object:
    if kind == "gas":
        g = DHWGenSpec(type="gas", model=lib("gas_boiler"), count=2, unit_kW=max(b.dhw_unit_kW * b.dhw_count / 2 * 1.1, 10.0))
        return A._dhw(b, g)
    if kind == "electric":
        return A._dhw(b, DHWGenSpec(type="electric", model=lib("electric"), count=1, unit_kW=1e6))
    if kind == "co2":
        cf = b.circulation_fraction or 0.25
        rh = FleetSpec(model=b.dhw_air_model, count=1, unit_kW=max(b.dhw_air_unit_kW, 20.0), source="air")
        return A._dhw(b, DHWGenSpec(type="co2", source="air", model=b.co2_model, count=b.co2_count, unit_kW=b.co2_unit_kW),
                      reheat=rh, circulation_fraction=cf)
    if kind == "loop":
        return A._dhw(b, DHWGenSpec(model=b.dhw_model, count=b.dhw_count, unit_kW=b.dhw_unit_kW))
    return A._dhw(b, DHWGenSpec(model=b.dhw_air_model, count=b.dhw_air_count, unit_kW=b.dhw_air_unit_kW, source="air"))


def build(s: Strategy, b: SiteBasis, has_cooling: bool, has_dhw: bool) -> Arrangement:
    """Arrangement for a strategy, from a site basis (temperatures and autosized plant)."""
    dhw = _dhw_spec(b, s.dhw) if has_dhw else None
    common = {"id": s.id, "archetype": "studio", "title": s.title, "why": [], "relies_on": list(s.notes),
              "strategy": {"heat": s.heat, "cool": s.cool, "dhw": s.dhw, "balance": s.bal}}
    gasheat = FleetSpec(model=lib("gas_boiler"), count=2, unit_kW=max(b.lthw_ashp_unit_kW * b.lthw_ashp_count / 2 * 1.1, 10.0),
                        source="gas")
    ashp = FleetSpec(model=b.lthw_ashp_model, count=b.lthw_ashp_count, unit_kW=b.lthw_ashp_unit_kW, source="air")
    acc = CoolingSpec(model=b.chiller_model, count=b.chiller_count, unit_kW=b.chiller_unit_kW, sink="air")
    if s.heat in ("gas", "ashp") and s.cool in ("acc", "none"):
        return Arrangement(**common, **_stand(b, heating=gasheat if s.heat == "gas" else ashp,  # type: ignore[arg-type]
                                              cooling=acc if s.cool == "acc" else None, dhw=dhw))
    if s.heat in ("gas", "ashp") and s.cool in ("wcc_dc", "wcc_ct"):
        # condenser-water loop floating with ambient: flow = T_db + 7 K (dry cooler) / + 4 K (tower), clamped
        reset = [(-10.0, 25.0), (18.0, 25.0), (33.0, 40.0)] if s.cool == "wcc_dc" else [(-10.0, 22.0), (18.0, 22.0), (30.0, 32.0)]
        loop = LoopSpec(mode="outdoor_reset", flow_C=30.0, return_C=25.0, reset=reset, pump=PumpSpec(head_kPa=200.0))
        wcc = CoolingSpec(model=lib("water_cooled_chiller"), count=b.chiller_count, unit_kW=b.chiller_unit_kW, sink="loop")
        rej = (BalancingSpec(type="dry_cooler", rated_kW=1.3 * b.chiller_count * b.chiller_unit_kW, rated_itd_K=8.0,
                             rated_fan_kW=0.03 * 1.3 * b.chiller_count * b.chiller_unit_kW, approach_min_K=3.0)
               if s.cool == "wcc_dc" else
               BalancingSpec(type="tower", rated_kW=1.3 * b.chiller_count * b.chiller_unit_kW, rated_fan_kW=0.012 * 1.3 * b.chiller_count * b.chiller_unit_kW))
        return Arrangement(**common, **_stand(b, loop=loop, heating=gasheat if s.heat == "gas" else ashp, cooling=wcc,  # type: ignore[arg-type]
                                              dhw=dhw, surplus=[rej]))
    if s.heat == "ashp2":
        return Arrangement(**common, changeover=True, **_stand(b, heating=ashp, cooling=acc, dhw=dhw))  # type: ignore[arg-type]
    if s.heat == "ashp4":
        hr = CoolingSpec(model=b.chiller_model, hr_model=b.cooling_model, count=b.chiller_count, unit_kW=b.chiller_unit_kW,
                         sink="air", hr_target="lthw")
        return Arrangement(**common, **_stand(b, heating=ashp, cooling=hr, dhw=dhw))  # type: ignore[arg-type]
    if s.heat == "vrf":
        vrf_h = FleetSpec(model=lib("vrf_heating"), count=b.lthw_ashp_count, unit_kW=b.lthw_ashp_unit_kW, source="air")
        vrf_c = CoolingSpec(model=lib("vrf_cooling"), hr_model=lib("vrf_heat_recovery"), count=b.chiller_count,
                            unit_kW=b.chiller_unit_kW, sink="air", hr_target="lthw")
        no_pump = PumpSpec(head_kPa=0.0)  # refrigerant distribution: no water pumps (indoor-unit fans out of scope)
        return Arrangement(**common, **_stand(b, heating=vrf_h, cooling=vrf_c, dhw=dhw,  # type: ignore[arg-type]
                                              lthw=CircuitSpec(flow_C=40.0, return_C=35.0, pump=no_pump),
                                              chw=CircuitSpec(flow_C=8.0, return_C=13.0, pump=no_pump)))
    # ambient loop
    if s.bal == "hr":
        arr = A.a23_buffer_preheat(b)
    else:
        arr = A.a1_shared_loop(b)
    d = arr.model_dump()
    d.update(common)
    if s.bal == "dc":
        d["surplus"] = [A._dry(b).model_dump(), A._ashp_cool(b).model_dump()]
    else:  # reversible ASHPs reject as well as supply heat; no dry cooler
        d["surplus"] = [A._ashp_cool(b).model_dump()]
    if has_dhw and dhw is not None:
        keep_pre = d.get("dhw", {}).get("preheat") if s.bal == "hr" else None
        d["dhw"] = dhw.model_dump()  # type: ignore[attr-defined]
        if keep_pre and s.dhw == "loop":
            d["dhw"]["preheat"] = keep_pre
    elif not has_dhw:
        d["dhw"] = None
    if not has_cooling:
        d["cooling"] = None
        d["heating"] = FleetSpec(model=b.cooling_model, count=b.cooling_count, unit_kW=b.cooling_unit_kW).model_dump()
        d["hr"] = None
        d["buffer"] = None
    return Arrangement.model_validate(d)
