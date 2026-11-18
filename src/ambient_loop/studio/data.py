"""Milieu data: rate table and bundled weather files.

Location: environment variable MILIEU_DATA (default ./milieu_data).
  rates.yaml            the Milieu rate table (template: library/milieu/rates.template.yaml)
  weather/index.yaml    bundled CIBSE weather files, e.g.
                          - {id: london_try, label: "London (Heathrow) TRY 2016", file: London_TRY.epw, kind: TRY}
                          - {id: london_dsy1, label: "London DSY1 2020s High", file: London_DSY1.epw, kind: DSY}
  weather/<files>       EPW or CSV with a dry_bulb_C column (and RH_pct if available)
CIBSE weather data is licensed: keep MILIEU_DATA out of git and off public hosting.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ambient_loop.core.series import FloatArray
from ambient_loop.io.weather import read_epw_dry_bulb, read_table_dry_bulb

ROOT = Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    return Path(os.environ.get("MILIEU_DATA", ROOT / "milieu_data"))


@lru_cache(maxsize=1)
def _rates_cached(path: str, mtime: float) -> dict[str, Any]:
    del mtime
    return yaml.safe_load(Path(path).read_text())  # type: ignore[no-any-return]


def rates() -> dict[str, Any]:
    p = data_dir() / "rates.yaml"
    if not p.exists():
        p = ROOT / "library" / "milieu" / "rates.template.yaml"
    return _rates_cached(str(p), p.stat().st_mtime)


def rates_are_placeholder() -> bool:
    return rates().get("status") != "milieu"


@dataclass(frozen=True)
class WeatherFile:
    id: str
    label: str
    path: Path
    kind: str


def weather_files() -> list[WeatherFile]:
    idx = data_dir() / "weather" / "index.yaml"
    if not idx.exists():
        return []
    out = []
    for w in yaml.safe_load(idx.read_text()) or []:
        out.append(WeatherFile(w["id"], w["label"], data_dir() / "weather" / w["file"], w.get("kind", "TRY")))
    return out


def load_weather(wid: str) -> tuple[FloatArray, FloatArray | None, str]:
    """(dry bulb, wet bulb or None, label)."""
    from ambient_loop.physics.rejection import wet_bulb_stull
    w = next((x for x in weather_files() if x.id == wid), None)
    if w is None:
        raise KeyError(f"weather file {wid!r} not found in {data_dir() / 'weather'}")
    if w.path.suffix.lower() == ".epw":
        import pandas as pd
        df = pd.read_csv(w.path, skiprows=8, header=None)
        db = read_epw_dry_bulb(w.path)
        rh = pd.to_numeric(df[8], errors="coerce").to_numpy(dtype=float)[: len(db)]
        return db, wet_bulb_stull(db, rh), w.label
    db = read_table_dry_bulb(w.path, "dry_bulb_C", None, 0)
    try:
        rh = read_table_dry_bulb(w.path, "RH_pct", None, 0)
        wb = wet_bulb_stull(db, rh)
    except Exception:  # noqa: BLE001
        wb = wet_bulb_stull(db, np.full_like(db, 70.0))  # [A-068] RH 70 % when the file has none
    return db, wb, w.label
