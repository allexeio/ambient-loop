"""Carbon, operating cost and capital proxy.

Carbon: an annual factor, or an hourly series from a CSV. Default 0.136 kgCO₂e/kWh, the SAP 10.2
electricity factor used in Part L 2021 / GLA energy statements [A-047: verify against the GLA
guidance edition in force at submission; GLA has also published hourly or future factors].
Tariff: flat £/kWh, or a time-of-use table by hour of day (weekday/weekend) [A-048].
Capital proxy: library/costs/default.yaml with project overrides, clearly labelled indicative [A-046].
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ambient_loop.core.series import HOURS, FloatArray
from ambient_loop.schema.arrangement import Arrangement

LIB = Path(__file__).resolve().parents[3] / "library" / "costs" / "default.yaml"
SAP10_2_ELEC = 0.136


@dataclass(frozen=True)
class Economics:
    carbon_kg_per_kWh: FloatArray
    tariff_gbp_per_kWh: FloatArray
    rates: dict[str, float]

    @staticmethod
    def default(overrides: dict[str, float] | None = None, carbon: float | FloatArray = SAP10_2_ELEC,
                tariff: float | FloatArray = 0.25) -> Economics:
        rates = dict(yaml.safe_load(LIB.read_text())["rates"])
        rates.update(overrides or {})
        c = np.full(HOURS, carbon) if np.isscalar(carbon) else np.asarray(carbon, float)
        t = np.full(HOURS, tariff) if np.isscalar(tariff) else np.asarray(tariff, float)
        return Economics(c, t, rates)

    def carbon_t(self, elec_kWh: FloatArray) -> float:
        return float(np.sum(elec_kWh * self.carbon_kg_per_kWh)) / 1e3

    def cost_gbp(self, elec_kWh: FloatArray) -> float:
        return float(np.sum(elec_kWh * self.tariff_gbp_per_kWh))


def tou_tariff(day_rate: float, night_rate: float, peak_rate: float | None = None,
               night_hours: tuple[int, int] = (0, 7), peak_hours: tuple[int, int] = (16, 19)) -> FloatArray:
    h = np.arange(HOURS) % 24
    t = np.where((h >= night_hours[0]) & (h < night_hours[1]), night_rate, day_rate).astype(float)
    if peak_rate is not None:
        t = np.where((h >= peak_hours[0]) & (h < peak_hours[1]), peak_rate, t)
    return t


def capex(arr: Arrangement, rates: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Indicative capital proxy (£) and breakdown."""
    r = rates
    items: dict[str, float] = {}
    if arr.cooling:
        items["WSHP fleet"] = arr.cooling.count * arr.cooling.unit_kW * r["wshp_per_kW"]
        if arr.cooling.hr_target != "none":
            items["Heat-recovery circuit and changeover"] = r["hr_circuit_item"]
    if arr.heating:
        items["LTHW heat pumps"] = arr.heating.count * arr.heating.unit_kW * r["wshp_per_kW"]
    if arr.dhw:
        g = arr.dhw.generator
        if g.type == "co2":
            items["CO₂ heat pumps"] = g.count * g.unit_kW * r["co2_hp_per_kW"]
        elif g.type != "electric":
            items["DHW heat pumps"] = g.count * g.unit_kW * r["dhw_hp_per_kW"]
        if g.stage1:
            items["Cascade stage 1"] = g.stage1.count * g.stage1.unit_kW * r["wshp_per_kW"]
        if arr.dhw.reheat:
            items["Circulation reheat"] = arr.dhw.reheat.count * arr.dhw.reheat.unit_kW * r["wshp_per_kW"]
        if arr.dhw.preheat:
            items["Preheat PHX package"] = r["phx_preheat_item"]
        items["Calorifiers"] = arr.dhw.calorifier_m3 * r["calorifier_per_m3"]
    if arr.buffer:
        items["Stratified buffer"] = arr.buffer.volume_m3 * r["buffer_per_m3"]
    for b in arr.deficit + arr.surplus:
        if b.type == "ashp" and b.fleet:
            items["Rooftop ASHPs"] = b.fleet.count * b.fleet.unit_kW * r["ashp_per_kW"]
        elif b.type == "ashp_cooling" and b.fleet:
            items["Air-cooled rejection"] = b.fleet.count * b.fleet.unit_kW * 0.78 * r["ashp_cooling_per_kW"]
        elif b.type == "dry_cooler" and b.rated_kW:
            items["Dry cooler"] = b.rated_kW * r["dry_cooler_per_kW"]
        elif b.type == "tower" and b.rated_kW:
            items["Cooling tower"] = b.rated_kW * r["tower_per_kW"]
        elif b.type == "ground" and "Borehole field" not in items:
            g2: dict[str, Any] = b.ground
            items["Borehole field"] = float(g2.get("n_bores", 40)) * float(g2.get("depth_m", 200)) * r["borehole_per_m"]
        elif b.type == "external":
            items["Greywater recovery"] = r["greywater_hx_item"]
    return float(sum(items.values())), items
