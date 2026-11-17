"""Scenario sets: demand and climate variants run as one batch (brief §6).

A variant is a label plus modifications to the inputs: per-service demand scales, an
outdoor-temperature offset (a simple climate-change proxy [A-051]), or a replacement weather
file (EPW or table).
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path
from typing import Any

from ambient_loop.io.weather import read_epw_dry_bulb, read_table_dry_bulb
from ambient_loop.schema.project import Project
from ambient_loop.sim.full_mode import FullInputs


def variants(project: Project, base: Path, inp: FullInputs, vary: dict[str, list[Any]]) -> list[tuple[str, FullInputs]]:
    svc = {s.id: s.service_name for s in project.streams}
    keys = list(vary)
    out = []
    for combo in itertools.product(*[vary[k] for k in keys]):
        i = inp
        label = []
        for k, v in zip(keys, combo, strict=True):
            if k.endswith("_scale"):
                service = k[: -len("_scale")].upper()
                i = replace(i, demand_kW={s: (q * v if svc[s] == service else q) for s, q in i.demand_kW.items()})
                label.append(f"{service}×{v:g}")
            elif k == "weather_offset_K":
                i = replace(i, dry_bulb_C=i.dry_bulb_C + v)
                label.append(f"T+{v:g}K")
            elif k == "weather_file":
                f = base / v
                db = read_epw_dry_bulb(f) if f.suffix.lower() == ".epw" else read_table_dry_bulb(f, "dry_bulb_C", None, 0)
                i = replace(i, dry_bulb_C=db)
                label.append(f.stem)
            else:
                raise KeyError(f"unknown variant key {k}")
        out.append((", ".join(label) or "central", i))
    return out
