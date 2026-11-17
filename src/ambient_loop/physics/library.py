"""Build performance models from library files or inline specs."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from ambient_loop.core.provenance import Tag
from ambient_loop.physics.performance import (
    AirSourceModel,
    CarnotModel,
    CO2Model,
    Envelope,
    FixedModel,
    MapModel,
    PartLoad,
    PerfModel,
)
from ambient_loop.schema.arrangement import ModelSpec

LIB = Path(__file__).resolve().parents[3] / "library"
_KEYS = {"id", "label", "type", "tag", "ref", "source_document", "selection_conditions",
         "envelope", "part_load", "unit_capacity_kW", "basis", "document_date"}


@cache
def library_index() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for folder in ("generic", "equipment"):
        for f in sorted((LIB / folder).glob("*.yaml")):
            d = yaml.safe_load(f.read_text())
            d["_file"] = f"library/{folder}/{f.name}"
            out[d["id"]] = d
    return out


def _build(kind: str, d: dict[str, Any], name: str, tag: Tag, ref: str) -> PerfModel:
    params = {k: v for k, v in d.items() if k not in _KEYS and not k.startswith("_")}
    if d.get("part_load"):
        pl = PartLoad(**{k: tuple(v) if isinstance(v, list) else v for k, v in d["part_load"].items()})
    elif kind == "fixed":
        pl = PartLoad(plr_min=1e-9)  # simple mode: no part-load or cycling effect by definition
    else:
        pl = PartLoad()
    env = Envelope(**{k: tuple(v) for k, v in d.get("envelope", {}).items()}) if d.get("envelope") else Envelope()
    common: dict[str, Any] = {"name": name, "tag": tag, "ref": ref, "part_load": pl, "envelope": env}
    if kind == "fixed":
        return FixedModel(**common, cop=float(params["cop"]))
    if kind == "carnot":
        return CarnotModel(**common, **params)
    if kind == "air_source":
        params = {k: tuple(v) if isinstance(v, list) else v for k, v in params.items()}
        return AirSourceModel(**common, **params)
    if kind == "co2":
        return CO2Model(**common, **params)
    if kind == "map":
        grid = params["cop_grid"]
        if d.get("basis", "heating") == "cooling":  # EER grid → heating COP
            grid = [[v + 1.0 for v in row] for row in grid]
        cap = params.get("cap_grid")
        return MapModel(**common, src_axis=tuple(params["src_axis"]), snk_axis=tuple(params["snk_axis"]),
                        cop_grid=tuple(tuple(r) for r in grid),
                        cap_grid=None if cap is None else tuple(tuple(r) for r in cap))
    raise ValueError(f"unknown model type {kind}")


def build_model(spec: ModelSpec, name: str = "") -> PerfModel:
    if spec.library is not None:
        idx = library_index()
        if spec.library not in idx:
            raise KeyError(f"unknown library model {spec.library!r}; have {sorted(idx)}")
        d = dict(idx[spec.library])
        d.update(spec.params)
        tag = Tag(spec.tag or d.get("tag", "generic"))
        ref = spec.ref or f"{d['_file']}: {d.get('label', '')}"
        return _build(d["type"], d, name or spec.library, tag, ref)
    assert spec.type is not None
    d = dict(spec.params)
    return _build(spec.type, d, name or spec.type, Tag(spec.tag or "assumption"), spec.ref or "inline")
