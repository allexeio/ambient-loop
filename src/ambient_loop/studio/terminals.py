"""Terminal devices and space types for the walkthrough (step 3).

Each terminal sets the water (or refrigerant) temperatures the central plant must deliver, which
services it provides, and whether it needs heating and cooling at the same time. The central
plant options in step 4 are screened for compatibility against these selections.

Temperatures are design-stage defaults chosen for heat-pump-led systems. Each terminal lists
alternatives the engineer can pick; the step 4 temperature sweep shows the energy effect.
References: CIBSE Guide B1 (2016) for emitter types and temperatures; CIBSE AM17 (2022) for
low-temperature heat-pump design; BSRIA BG 69 (chilled beams) [A-065].
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Terminal:
    id: str
    label: str
    heats: bool
    cools: bool
    medium: str  # "water" | "refrigerant" | "air"
    lthw: tuple[float, float] | None = None  # default flow/return °C
    chw: tuple[float, float] | None = None
    lthw_options: tuple[tuple[float, float], ...] = ()
    chw_options: tuple[tuple[float, float], ...] = ()
    simultaneous: bool = False  # 4-pipe: heating and cooling available at the same time
    fresh_air: str = "separate"  # how ventilation is normally provided alongside it
    note: str = ""


TERMINALS: dict[str, Terminal] = {t.id: t for t in [
    Terminal("fcu_4pipe", "4-pipe fan coil units", True, True, "water", (45, 40), (10, 16),
             ((40, 35), (45, 40), (45, 35), (50, 40), (55, 45)), ((6, 12), (8, 14), (10, 16), (12, 18)), True,
             note="Heating and cooling at the same time, room by room. 10/16 °C CHW works when a separate "
                  "fresh-air system handles latent load; otherwise 6–8 °C."),
    Terminal("fcu_2pipe", "2-pipe changeover fan coil units", True, True, "water", (45, 40), (10, 16),
             ((40, 35), (45, 40), (50, 40)), ((6, 12), (10, 16)), False,
             note="Heating or cooling for the whole system at any one time (seasonal changeover)."),
    Terminal("ufh", "Underfloor heating", True, False, "water", (35, 30), None, ((35, 30), (40, 35)),
             note="Lowest flow temperature; slow response."),
    Terminal("radiators_lt", "Low-temperature radiators (oversized)", True, False, "water", (45, 40), None,
             ((45, 40), (50, 40), (55, 45))),
    Terminal("radiators", "Conventional radiators", True, False, "water", (55, 45), None, ((55, 45), (60, 50), (70, 50)),
             note="Higher flow temperature penalises heat pumps."),
    Terminal("chilled_beams", "Active chilled beams (with heating)", True, True, "water", (40, 35), (14, 17),
             ((35, 30), (40, 35)), ((14, 17), (15, 18)), True,
             note="High CHW temperature favours chiller and free-cooling efficiency; needs dehumidified primary air."),
    Terminal("vav", "Full-volume VAV (all-air, AHU coils)", True, True, "air", (50, 40), (6, 12),
             ((45, 35), (50, 40), (55, 45)), ((6, 12), (7, 13)), True, fresh_air="included",
             note="Heating and cooling delivered by air from AHUs; fresh air load sits in the AHU coils."),
    Terminal("vrf_indoor", "VRF indoor units (DX cassettes / ducted)", True, True, "refrigerant", None, None, (), (), True,
             note="Refrigerant to the room. Only VRF central plant can serve it."),
    Terminal("none_cooling", "No cooling", False, False, "water", None, None),
]}

# terminals that can be served by water-based central plant
WATER = {k for k, t in TERMINALS.items() if t.medium in ("water", "air")}


@dataclass(frozen=True)
class SpaceType:
    id: str
    label: str
    profile: str  # key in library/profiles/building_types.yaml
    terminal: str  # recommended terminal
    alternatives: tuple[str, ...]
    fresh_air_ls_per_person: float
    why: str = ""


SPACE_TYPES: dict[str, SpaceType] = {s.id: s for s in [
    SpaceType("hotel_bedroom", "Hotel bedrooms", "hotel", "fcu_4pipe", ("fcu_2pipe", "vrf_indoor", "chilled_beams"), 10.0,
              "Guests expect independent heating or cooling in every room at any time: 4-pipe FCUs are the norm."),
    SpaceType("hotel_public", "Hotel public areas / restaurant", "hotel", "vav", ("fcu_4pipe", "chilled_beams"), 10.0,
              "Variable occupancy and high fresh-air rates suit all-air or FCU with a dedicated AHU."),
    SpaceType("residential", "Residential apartments", "residential", "ufh", ("radiators_lt", "fcu_2pipe", "fcu_4pipe"), 8.0,
              "Low-temperature UFH or oversized radiators give the best heat-pump efficiency; cooling, if any, by FCU."),
    SpaceType("office", "Offices", "office", "fcu_4pipe", ("chilled_beams", "vav", "vrf_indoor"), 12.0,
              "4-pipe FCUs with a separate fresh-air AHU are the commercial default; chilled beams for high CHW temperature."),
    SpaceType("retail", "Retail / F&B units", "retail", "fcu_4pipe", ("vrf_indoor", "fcu_2pipe"), 10.0,
              "Tenant fit-out usually on a landlord water loop or tenant VRF."),
    SpaceType("other", "Other / ancillary", "office", "radiators_lt", ("fcu_4pipe", "ufh"), 8.0, ""),
]}


@dataclass
class TerminalChoice:
    zone: str
    terminal: str
    lthw: tuple[float, float] | None
    chw: tuple[float, float] | None
    why: str = ""
    alternatives: list[str] = field(default_factory=list)


def propose_terminals(zones: list[dict[str, object]]) -> list[TerminalChoice]:
    """Rule-based proposal: the recommended terminal for each zone's space type."""
    out = []
    for z in zones:
        st = SPACE_TYPES.get(str(z.get("space_type", "other")), SPACE_TYPES["other"])
        t = TERMINALS[st.terminal]
        cools = float(z.get("cooling_kWh", 0) or 0) > 0
        tid = t.id
        if not cools and t.cools and st.id == "residential":
            tid = "ufh"
        tt = TERMINALS[tid]
        out.append(TerminalChoice(str(z.get("name")), tid, tt.lthw, tt.chw if cools else None, st.why, list(st.alternatives)))
    return out


def circuit_temperatures(choices: list[TerminalChoice]) -> dict[str, float]:
    """Central-plant temperatures that satisfy every water terminal: highest LTHW flow, lowest CHW flow."""
    lthw = [c.lthw for c in choices if c.lthw and TERMINALS[c.terminal].medium != "refrigerant"]
    chw = [c.chw for c in choices if c.chw and TERMINALS[c.terminal].medium != "refrigerant"]
    out: dict[str, float] = {}
    if lthw:
        f = max(x[0] for x in lthw)
        dT = min(x[0] - x[1] for x in lthw if x[0] == f)
        out.update(lthw_flow_C=f, lthw_return_C=f - dT)
    if chw:
        f = min(x[0] for x in chw)
        dT = min(x[1] - x[0] for x in chw if x[0] == f)
        out.update(chw_flow_C=f, chw_return_C=f + dT)
    return out


def needs_simultaneous(choices: list[TerminalChoice]) -> bool:
    return any(TERMINALS[c.terminal].simultaneous and c.chw for c in choices)


def all_refrigerant(choices: list[TerminalChoice]) -> bool:
    active = [c for c in choices if c.terminal != "none_cooling"]
    return bool(active) and all(TERMINALS[c.terminal].medium == "refrigerant" for c in active)
