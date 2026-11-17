"""Create a project folder from uploaded loads (with column mapping) or synthesised loads.

Writes project.yaml, loads.csv and weather.csv, ready for `recovery`, `scenarios`, `study`
and `report`. Stream temperatures default to common UK design values and can be edited in
project.yaml [A-056].
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ambient_loop.core.series import HOURS, FloatArray
from ambient_loop.io.tables import hourly_column, read_table, to_model_year
from ambient_loop.io.weather import read_epw_dry_bulb, read_table_dry_bulb

SERVICES = {
    "LTHW": {"kind": "space_heating", "T_in_C": 40.0, "T_out_C": 45.0},
    "CHW": {"kind": "space_cooling", "T_in_C": 16.0, "T_out_C": 10.0},
    "DHW": {"kind": "dhw", "T_in_C": "mains", "T_out_C": 60.0},
    "PROCESS_COOL": {"kind": "process_cooling", "T_in_C": 30.0, "T_out_C": 24.0},
    "PROCESS_HEAT": {"kind": "process_heating", "T_in_C": 35.0, "T_out_C": 45.0},
}


def parse_mapping(spec: str) -> dict[str, tuple[str, float]]:
    """'LTHW=Heating (W)*0.001,CHW=Cooling (W)*0.001,DHW=DHW kW' -> {service: (column, scale)}."""
    out: dict[str, tuple[str, float]] = {}
    for part in [p for p in spec.split(",") if p.strip()]:
        svc, col = part.split("=", 1)
        scale = 1.0
        if "*" in col:
            col, sc = col.rsplit("*", 1)
            scale = float(sc)
        out[svc.strip().upper()] = (col.strip(), scale)
    return out


def load_columns(path: Path, mapping: dict[str, tuple[str, float]], sheet: str | None = None,
                 header_row: int = 0) -> dict[str, FloatArray]:
    df = to_model_year(read_table(path, sheet, header_row), source=path.name)
    return {svc: hourly_column(df, col, source=path.name) * scale for svc, (col, scale) in mapping.items()}


def read_weather(path: Path, column: str | None = None, sheet: str | None = None) -> FloatArray:
    if path.suffix.lower() == ".epw":
        return read_epw_dry_bulb(path)
    if column is None:
        raise ValueError("weather table needs the dry-bulb column name")
    return read_table_dry_bulb(path, column, sheet, 0)


def create_project(out_dir: Path, name: str, loads_kW: dict[str, FloatArray], dry_bulb_C: FloatArray, *,
                   description: str = "", mains_C: float = 10.0, dhw_store_C: float = 60.0,
                   temps: dict[str, dict[str, Any]] | None = None, targets: list[dict[str, Any]] | None = None,
                   load_tag: str = "project_data", site: dict[str, Any] | None = None,
                   weather_source: Path | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for k, v in loads_kW.items():
        if len(v) != HOURS:
            raise ValueError(f"{k}: need {HOURS} hourly values, got {len(v)}")
        if np.any(np.asarray(v) < 0):
            raise ValueError(f"{k}: negative loads")
    cols = {f"{svc}_kW": np.asarray(v, float) for svc, v in loads_kW.items()}
    pd.DataFrame(cols).to_csv(out_dir / "loads.csv", index=False)
    pd.DataFrame({"dry_bulb_C": np.asarray(dry_bulb_C, float)}).to_csv(out_dir / "weather.csv", index=False)
    if weather_source is not None:
        shutil.copy(weather_source, out_dir / f"source_{weather_source.name}")
    streams = []
    for svc in loads_kW:
        base = dict(SERVICES.get(svc, {"kind": "space_heating", "T_in_C": None, "T_out_C": None}))
        if svc == "DHW":
            base["T_out_C"] = dhw_store_C
        base.update((temps or {}).get(svc, {}))
        streams.append({"id": svc.lower(), "service": svc, "duty_kW": {"column": f"{svc}_kW"}, "tag": load_tag, **base})
    units = []
    for s in streams:
        kind = "chiller" if s["kind"] in ("space_cooling", "process_cooling") else "heat_pump"
        eff = 4.5 if kind == "chiller" else 3.0
        units.append({"id": f"{s['id']}_unit", "kind": kind, "serves": [s["id"]],
                      "efficiency": {"value": eff, "tag": "assumption", "ref": "placeholder for simple mode"}})
    project = {
        "name": name, "description": description, "mode": "full",
        "loads": {"file": "loads.csv"},
        "weather": {"file": "weather.csv", "format": "table", "dry_bulb_column": "dry_bulb_C"},
        "mains_water_C": {"value": mains_C, "tag": "assumption", "ref": "constant mains temperature; replace with monthly profile"},
        "streams": streams,
        "plant": {"units": units},
        "site": {"dhw_store_C": dhw_store_C, **(site or {})},
        "archetypes": [],
        "targets": targets or [],
    }
    p = out_dir / "project.yaml"
    p.write_text("# Created by ambient-loop new-project. Edit temperatures, targets and site plant as needed.\n"
                 + yaml.safe_dump(project, sort_keys=False, allow_unicode=True))
    return p
