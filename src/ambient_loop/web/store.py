"""Project store: one folder per project under MILIEU_DATA/projects/<id>/ (project.json, uploads/, model/, outputs/)."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ambient_loop.studio.data import data_dir


def root() -> Path:
    p = data_dir() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def pdir(pid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", pid):
        raise KeyError(pid)
    return root() / pid


def create(name: str, location: str, weather_id: str) -> str:
    pid = uuid.uuid4().hex[:12]
    d = pdir(pid)
    for sub in ("uploads", "model", "outputs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    save(pid, {"id": pid, "name": name, "location": location, "weather_id": weather_id, "created": time.time(),
               "description": "", "uploads": [], "zones": [], "fresh_air": {"mode": "included", "ahu_heating_kWh": 0,
               "ahu_cooling_kWh": 0, "heat_recovery": 0.75}, "dhw_store_C": 60.0, "circulation_fraction": 0.25,
               "ai": {}, "terminals": [], "hourly": None, "screen": [], "compare": [], "selected": None, "step": 1})
    return pid


def load(pid: str) -> dict[str, Any]:
    return json.loads((pdir(pid) / "project.json").read_text())  # type: ignore[no-any-return]


def save(pid: str, state: dict[str, Any]) -> None:
    p = pdir(pid) / "project.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str))
    tmp.replace(p)


def all_projects() -> list[dict[str, Any]]:
    out = []
    for f in sorted(root().glob("*/project.json"), key=lambda x: -x.stat().st_mtime):
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out
