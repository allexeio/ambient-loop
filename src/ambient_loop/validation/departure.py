"""How full-physics mode departs from the workbook simple mode (brief §9.1), step by step.

Start from the simplified basis, which reproduces the workbook at 926 MWh/yr for A1, and
swap one simplification at a time for its physical model. Steps are cumulative, so each
delta is attributable:
  1. fixed COPs → temperature-dependent models (generic, calibrated to the same project points)
  2. + part-load and cycling (EN 14825) and fleet sequencing
  3. + dry cooler physics (available below loop flow − approach, not a 27 °C cutoff)
  4. + DHW calorifier time-shift (plant capacity vs draw-off, standing loss)
  5. + pumps (loop, circuits)
The same steps run for any archetype, so the departure can be shown for the preferred scheme
as well as the baseline.
"""

from __future__ import annotations

from typing import Any

from ambient_loop.arrangements.archetypes import ARCHETYPES, SiteBasis, lib
from ambient_loop.scenarios.run import ProjectContext
from ambient_loop.sim.full_mode import run_arrangement
from ambient_loop.validation.fixture1 import fixture_ashp_model, simplified_basis


def steps(site_full: SiteBasis) -> list[tuple[str, SiteBasis]]:
    s0 = simplified_basis()
    noPL = {"part_load": {"plr_min": 1e-9}}
    s1 = s0.model_copy(update={
        "cooling_model": lib("wshp_scroll_inverter").model_copy(update={"params": noPL}),
        "cooling_hr_model": None, "heating_model": None,
        "dhw_model": lib("wshp_screw_ht").model_copy(update={"params": noPL}), "dhw_hr_model": None,
        "stage1_model": lib("wshp_scroll_inverter").model_copy(update={"params": noPL}),
        "ashp_cooling_model": lib("chiller_air_cooled").model_copy(update={"params": noPL}),
        "cooling_count": site_full.cooling_count, "dhw_count": site_full.dhw_count,
    })
    s2 = s1.model_copy(update={"cooling_model": site_full.cooling_model, "dhw_model": site_full.dhw_model,
                               "stage1_model": site_full.stage1_model, "ashp_cooling_model": site_full.ashp_cooling_model,
                               "ashp_model": site_full.ashp_model if site_full.ashp_model.type == "map" else fixture_ashp_model()})
    s3 = s2.model_copy(update={"dry_cooler_simple_cutoff_C": None, "dry_cooler_simple_kW_per_kW": None,
                               "dry_cooler_fan_kW": site_full.dry_cooler_fan_kW, "dry_cooler_itd_K": site_full.dry_cooler_itd_K})
    s4 = s3.model_copy(update={"calorifier_m3": site_full.calorifier_m3})
    s5 = s4.model_copy(update={"pumps_included": True})
    return [("Workbook-compatible simplified basis", s0),
            ("Temperature-dependent COPs (generic, project-calibrated)", s1),
            ("+ part load, cycling and sequencing", s2),
            ("+ dry cooler available only below loop − approach", s3),
            ("+ DHW calorifier time-shift and standing loss", s4),
            ("+ pumps (loop and circuits)", s5)]


def departure(ctx: ProjectContext, aid: str = "A1") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    prev = None
    demand = sum(float(v.sum()) for v in ctx.inputs.demand_kW.values()) / 1e3
    for label, s in steps(ctx.site):
        led = run_arrangement(ctx.project, ARCHETYPES[aid](s), ctx.inputs).ledger
        e = float(led.total_elec().sum()) / 1e3
        comps = {k: float(v.sum()) / 1e3 for k, v in led.elec.items() if v.sum() > 0}
        rows.append({"step": label, "electricity_MWh": e, "delta_MWh": None if prev is None else e - prev,
                     "ratio": demand / e, "components": comps})
        prev = e
    return rows
