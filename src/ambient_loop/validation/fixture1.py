"""Fixture 1 site bases: the brief's simplified assumptions (§9.2), and full physics.

Simplified basis (§9.2): fixed COPs taken from the workbook, plus the brief's figures:
  - cooling reject EER 6.753 (workbook Inputs B11); cooling in heat recovery EER 4.5 (brief)
  - LTHW WSHP COP 5.443 (B10) on its own fleet
  - DHW HP COP 3.585 from the loop (B12); 5.3 on a 45/40 °C heat-recovery source (brief)
  - cascade first stage (loop → 45 °C) COP 5.443; second stage 45/40 → 65 °C COP 5.3
  - rooftop ASHP map from the workbook (Inputs A69:D73), end values held
  - ASHP cooling EER 4.91 (B14); dry cooler 800 kW at or below 27 °C, 0.1009 kWe/kW (B134–B141)
  - pumps excluded; DHW served in the same hour (no calorifier shift); incremental allocation
Full basis: the generic library models (calibrated to the same project points), the
fixture ASHP map, physical dry-cooler availability, and pumps included.
"""

from __future__ import annotations

from typing import Any

from ambient_loop.arrangements.archetypes import SiteBasis
from ambient_loop.schema.arrangement import ModelSpec


def fixed(cop: float, ref: str) -> ModelSpec:
    return ModelSpec(type="fixed", params={"cop": cop}, tag="project_data", ref=ref)


def ashp_map(x: list[float], cop: list[float], cap_rel: list[float], ref: str, part_load: bool = True) -> ModelSpec:
    params: dict[str, Any] = {"src_axis": x, "snk_axis": [25.0], "cop_grid": [[c] for c in cop],
                              "cap_grid": [[c] for c in cap_rel]}
    if not part_load:
        params["part_load"] = {"plr_min": 1e-9}
    return ModelSpec(type="map", params=params, tag="manufacturer_data", ref=ref)


FIXTURE_ASHP = {
    "x": [-5.0, -3.0, 0.0, 5.0, 10.0],
    "heat_kW": [418.34, 443.24, 480.48, 549.07, 622.55],
    "input_kW": [118.35, 118.69, 121.53, 118.12, 120.5],
}


def fixture_ashp_model(part_load: bool = True) -> ModelSpec:
    f = FIXTURE_ASHP
    cop = [h / w for h, w in zip(f["heat_kW"], f["input_kW"], strict=True)]
    cap = [h / 622.55 for h in f["heat_kW"]]
    return ashp_map(f["x"], cop, cap, "Fixture workbook Inputs A69:D73 (rooftop map at 25 °C LWT)", part_load)


def simplified_basis(**over: Any) -> SiteBasis:
    d: dict[str, Any] = dict(
        cooling_model=fixed(6.753 + 1.0, "Inputs B11: CHW EER 6.753"),
        cooling_hr_model=fixed(4.5 + 1.0, "Brief §9.2: HR cooling EER 4.5"),
        heating_model=fixed(5.443, "Inputs B10: LTHW COP 5.443"),
        dhw_model=fixed(3.585, "Inputs B12: DHW COP 3.585 (loop source)"),
        dhw_hr_model=fixed(5.3, "Brief §9.2: DHW HP COP 5.3 on 45/40 source"),
        stage1_model=fixed(5.443, "Cascade stage 1 at LTHW COP (B10)"),
        ashp_model=fixture_ashp_model(part_load=False),
        ashp_unit_kW=622.55, ashp_count=4,
        ashp_cooling_model=fixed(4.91 + 1.0, "Inputs B14: rooftop cooling EER 4.91"),
        dry_cooler_simple_cutoff_C=27.0, dry_cooler_simple_kW_per_kW=0.10094,
        calorifier_m3=0.0, pumps_included=False, dhw_count=4, cooling_count=4,
    )
    d.update(over)
    return SiteBasis(**d)


def full_basis(**over: Any) -> SiteBasis:
    d: dict[str, Any] = dict(ashp_model=fixture_ashp_model(), ashp_unit_kW=622.55, ashp_count=4,
                             dry_cooler_fan_kW=80.75)
    d.update(over)
    return SiteBasis(**d)


# Brief §9.2 regression expectations (electricity MWh/yr, overall ratio), ±3 %
REGRESSION = {
    "A1": (926.0, 3.14),
    "A5": (1040.0, 2.80),
    "A4-nc": (956.0, 3.05),
    "A4": (896.0, 3.25),
    "A3": (808.0, 3.60),
    "A2+3-store60": (770.0, 3.78),
}
