"""Workbook-compatible simple mode: a shared ambient loop with fixed efficiencies.

Simplifications, by definition of this mode (brief §9.1):
  - fixed unit COP/EER
  - same-hour reuse: heat rejected to the loop in hour t offsets heat extracted in hour t,
    scaled by a reuse fraction f (1 = ideal). No temperature, flow or timing constraint
    applies inside the hour, and there is no storage.
  - pumps off by default; listed pumps can be switched on as a duty-proportional allowance
  - ASHP COP from dry bulb, endpoints held outside the supplied map. Held hours are flagged
    as extrapolated.
  - dry cooler available only when dry bulb <= a fixed cutoff

Equations (per hour; Q in kWh over 1 h, numerically equal to mean kW):
  heat pump serving heating load Q:   W = Q / COP;   extraction from loop E = Q − W
  chiller serving cooling load Q:     W = Q / EER;   rejection to loop    R = Q + W
  reused heat           P   = f · min(ΣE, ΣR)
  residual deficit      D   = ΣE − P   → deficit plant, in order
  residual surplus      S   = ΣR − P   → surplus plant, in order
  ASHP heating:   delivers d to loop, W = d / COP(T_db), heat from ambient = d − W
  dry cooler:     rejects r = min(S, capacity) if T_db <= cutoff; W = r · fan ratio
  ASHP cooling:   rejects r, W = r / EER(T_db)
  listed pumps:   W_pump = q · pump ratio, where q is the heat moved by that plant
  Anything still unbalanced goes to `unmet`. Unlimited plant never leaves any.

Loop balance: in = ΣR + D, out = ΣE + S. With D = ΣE − P and S = ΣR − P, the loop closes
exactly for any f in [0, 1].

These equations follow the Milieu ambient-loop workbook's hourly sheet column by column.
The mapping is in docs/FIXTURE1_MAPPING.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ambient_loop.core.provenance import Tag
from ambient_loop.core.series import HOURS, FloatArray
from ambient_loop.schema.project import BalancingKind, BalancingPlant, Project, UnitKind
from ambient_loop.sim.ledger import ComponentInfo, Ledger, Role

LOOP = "loop"
AMBIENT = "ambient"


def svc(stream_id: str) -> str:
    return f"svc:{stream_id}"


def pump_id(plant_id: str) -> str:
    return f"{plant_id}:pumps"


@dataclass(frozen=True)
class SimpleInputs:
    demand_kW: dict[str, FloatArray]  # stream id -> hourly kW (non-negative magnitude)
    dry_bulb_C: FloatArray


def _efficiency(b: BalancingPlant, t_db: FloatArray, ledger: Ledger) -> FloatArray:
    info = ledger.components[b.id]
    if b.efficiency is not None:
        info.tags.add(b.efficiency.tag)
        return np.full(HOURS, b.efficiency.value)
    assert b.efficiency_curve is not None
    y, extrap = b.efficiency_curve(t_db)
    info.tags.add(b.efficiency_curve.tag)
    ledger.flags[f"{b.id}:outside_map"] = extrap
    if extrap.any():
        info.tags.add(Tag.EXTRAPOLATED)
    return y


def _pumps(b: BalancingPlant, q: FloatArray, role: Role, project: Project, ledger: Ledger) -> None:
    """Listed pump allowance, a separate meter so it is always visible."""
    if not project.plant.pumps_included or b.pump_kW_per_kW.value == 0.0:
        return
    pid = pump_id(b.id)
    ledger.add_component(ComponentInfo(pid, "pump_allowance", role, {b.pump_kW_per_kW.tag}))
    w = q * b.pump_kW_per_kW.value
    ledger.add_flow(pid, AMBIENT, w)  # pump heat not returned to the loop (simplification)
    ledger.add_elec(pid, w)
    ledger.derived[f"{pid}:serves"] = np.asarray(q)


def run_simple(
    project: Project,
    inputs: SimpleInputs,
    *,
    reuse_fraction: float | None = None,
    exclude_kinds: frozenset[BalancingKind] = frozenset(),
) -> Ledger:
    """Run the shared-loop simple model.

    reuse_fraction and exclude_kinds override the project, for reference cases. The
    "isolated" reference uses reuse 0 and no dry cooler.
    """
    streams = {s.id: s for s in project.streams}
    plant = project.plant
    f = plant.reuse_fraction.value if reuse_fraction is None else reuse_fraction
    ledger = Ledger(internal_nodes={LOOP})

    extraction = np.zeros(HOURS)
    rejection = np.zeros(HOURS)

    # 1. Units serving loads
    for u in plant.units:
        ledger.add_component(
            ComponentInfo(
                u.id,
                u.kind.value,
                Role.UNIT,
                {u.efficiency.tag} | {streams[s].tag for s in u.serves},
                list(u.serves),
            )
        )
        eff = u.efficiency.value
        for sid in u.serves:
            q = inputs.demand_kW[sid]
            w = q / eff
            if u.kind is UnitKind.HEAT_PUMP:
                ledger.add_flow(u.id, svc(sid), q)
                ledger.add_flow(u.id, LOOP, -(q - w))
                ledger.derived[f"extraction:{sid}"] = q - w
                extraction += q - w
            elif u.kind is UnitKind.CHILLER:
                ledger.add_flow(u.id, svc(sid), -q)
                ledger.add_flow(u.id, LOOP, q + w)
                ledger.derived[f"rejection:{sid}"] = q + w
                rejection += q + w
            else:  # direct electric: no loop interaction
                ledger.add_flow(u.id, svc(sid), q)
                ledger.add_flow(u.id, AMBIENT, -(q - w))  # zero when eff = 1
            ledger.add_elec(u.id, w)

    reused = f * np.minimum(extraction, rejection)
    ledger.derived["loop:extraction"] = extraction
    ledger.derived["loop:rejection"] = rejection
    ledger.derived["loop:reused"] = reused
    t_db = inputs.dry_bulb_C

    # 2. Residual deficit
    remaining = extraction - reused
    ledger.derived["loop:residual_deficit"] = remaining.copy()
    for b in plant.deficit_plant:
        if b.kind in exclude_kinds:
            continue
        ledger.add_component(ComponentInfo(b.id, b.kind.value, Role.DEFICIT))
        cap = np.inf if b.capacity_kW is None else b.capacity_kW
        d = np.minimum(remaining, cap)
        w = d / _efficiency(b, t_db, ledger)
        ledger.add_flow(b.id, LOOP, d)
        ledger.add_flow(b.id, AMBIENT, -(d - w))
        ledger.add_elec(b.id, w)
        _pumps(b, d, Role.DEFICIT, project, ledger)
        remaining = remaining - d
    unmet_heat = remaining

    # 3. Residual surplus
    remaining = rejection - reused
    ledger.derived["loop:residual_surplus"] = remaining.copy()
    for b in plant.surplus_plant:
        if b.kind in exclude_kinds:
            continue
        ledger.add_component(ComponentInfo(b.id, b.kind.value, Role.SURPLUS))
        cap = np.inf if b.capacity_kW is None else b.capacity_kW
        if b.kind is BalancingKind.DRY_COOLER:
            assert b.cutoff_dry_bulb_C is not None
            available = t_db <= b.cutoff_dry_bulb_C
            ledger.flags[f"{b.id}:available"] = available
            r = np.where(available, np.minimum(remaining, cap), 0.0)
            w = r * b.fan_kW_per_kW.value
            ledger.components[b.id].tags.add(b.fan_kW_per_kW.tag)
        else:  # ASHP cooling mode
            r = np.minimum(remaining, cap)
            w = r / _efficiency(b, t_db, ledger)
            _pumps(b, r, Role.SURPLUS, project, ledger)
        ledger.add_flow(b.id, LOOP, -r)
        ledger.add_flow(b.id, AMBIENT, r + w)
        ledger.add_elec(b.id, w)
        remaining = remaining - r
    unmet_reject = remaining

    # 4. Unmet: closes the loop balance and stays visible
    ledger.add_component(ComponentInfo("unmet", "unmet", Role.UNMET))
    ledger.add_flow("unmet", LOOP, unmet_heat - unmet_reject)
    ledger.derived["unmet:heat"] = unmet_heat
    ledger.derived["unmet:reject"] = unmet_reject

    ledger.assert_closed()
    return ledger
