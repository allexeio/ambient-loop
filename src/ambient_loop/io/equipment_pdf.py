"""Parser for manufacturer selection reports (e.g. Trane Select Assist product reports).

Approach (pdfplumber plus heuristics):
  1. Extract every table on every page. Two-column tables are read as key → value pairs and
     matched to a field vocabulary by regex: capacities, power, COP/EER, entering and leaving
     temperatures, fluid, refrigerant, minimum flow.
  2. Multi-column tables are treated as performance tables. A table needs columns recognised
     as source-leaving and sink-leaving temperature plus COP, or capacity and power input.
  3. Output: a library YAML candidate with the source document, every extracted value, its
     page and row, and a confidence flag. With a full performance table it is a `map` model.
     With a single rating point it is a `carnot` model calibrated to that point,
     η = COP / COP_Carnot (approaches 2 K). The candidate is written for manual review, as
     `review_required: true`, and must be checked against the PDF before use (brief §8).
The vocabulary is extensible: add regexes to FIELDS when a manufacturer uses other wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdfplumber
import yaml

K0 = 273.15
NUM = re.compile(r"-?\d+(?:[.,]\d+)?")

FIELDS: dict[str, str] = {
    "model": r"^(unit )?model",
    "refrigerant": r"refrigerant",
    "heating_kW": r"heating capacity|heat output|condenser capacity",
    "cooling_kW": r"cooling capacity|evaporator capacity",
    "power_kW": r"power input|absorbed power|compressor power|total power",
    "cop": r"^cop|coefficient of performance",
    "eer": r"^eer|energy efficiency ratio",
    "evap_in_C": r"evaporator.*entering|source.*entering|evap.*inlet",
    "evap_out_C": r"evaporator.*leaving|source.*leaving|evap.*outlet",
    "cond_in_C": r"condenser.*entering|sink.*entering|cond.*inlet",
    "cond_out_C": r"condenser.*leaving|sink.*leaving|cond.*outlet",
    "evap_fluid": r"evaporator fluid|source fluid",
    "cond_fluid": r"condenser fluid|sink fluid",
    "min_cond_flow_ls": r"minimum condenser flow",
    "min_evap_flow_ls": r"minimum evaporator flow",
}
COLS = {"src": FIELDS["evap_out_C"], "snk": FIELDS["cond_out_C"], "cap": FIELDS["heating_kW"],
        "pow": FIELDS["power_kW"], "cop": FIELDS["cop"], "eer": FIELDS["eer"]}


@dataclass
class Extraction:
    source: str
    fields: dict[str, Any] = field(default_factory=dict)
    where: dict[str, str] = field(default_factory=dict)
    table: list[dict[str, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    m = NUM.search(s.replace("−", "-"))
    return float(m.group().replace(",", ".")) if m else None


def parse_pdf(path: Path) -> Extraction:
    ex = Extraction(path.name)
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for ti, tbl in enumerate(page.extract_tables()):
                rows = [[re.sub(r"\s+", " ", (c or "")).strip() for c in r] for r in tbl if r]
                if not rows:
                    continue
                if max(len(r) for r in rows) == 2:
                    for ri, (k, v) in enumerate(r for r in rows if len(r) == 2):
                        for name, rx in FIELDS.items():
                            if name not in ex.fields and re.search(rx, k.strip(), re.I):
                                val: Any = v if name in ("model", "refrigerant", "evap_fluid", "cond_fluid") else _num(v)
                                if val is not None:
                                    ex.fields[name] = val
                                    ex.where[name] = f"p{pno} t{ti + 1} r{ri + 1}"
                                break
                else:
                    head = rows[0]
                    idx: dict[str, int] = {}
                    for key, rx in COLS.items():
                        hits = [i for i, h in enumerate(head) if re.search(rx, h, re.I)]
                        if len(hits) == 1:
                            idx[key] = hits[0]
                    if {"src", "snk"} <= set(idx) and ({"cop"} <= set(idx) or {"cap", "pow"} <= set(idx)):
                        for r in rows[1:]:
                            rec = {k: _num(r[i]) for k, i in idx.items() if i < len(r)}
                            if rec.get("src") is None or rec.get("snk") is None:
                                continue
                            if rec.get("cop") is None and rec.get("cap") and rec.get("pow"):
                                rec["cop"] = rec["cap"] / rec["pow"]
                            ex.table.append({k: v for k, v in rec.items() if v is not None})
    if "cop" not in ex.fields and ex.fields.get("heating_kW") and ex.fields.get("power_kW"):
        ex.fields["cop"] = ex.fields["heating_kW"] / ex.fields["power_kW"]
        ex.warnings.append("COP derived as heating capacity ÷ power input")
    for k in ("evap_out_C", "cond_out_C"):
        if k not in ex.fields:
            ex.warnings.append(f"{k} not found")
    return ex


def to_library(ex: Extraction, model_id: str, label: str, approach_K: float = 2.0) -> dict[str, Any]:
    base: dict[str, Any] = {"id": model_id, "label": label, "tag": "manufacturer_data", "review_required": True,
                            "source_document": ex.source, "extracted": ex.fields, "extracted_where": ex.where,
                            "warnings": ex.warnings}
    pts = [r for r in ex.table if "cop" in r]
    if pts:
        srcs = sorted({r["src"] for r in pts})
        snks = sorted({r["snk"] for r in pts})
        grid = []
        complete = True
        for s in srcs:
            row = []
            for k in snks:
                v = next((r["cop"] for r in pts if r["src"] == s and r["snk"] == k), None)
                if v is None:
                    complete = False
                    v = float("nan")
                row.append(round(v, 4))
            grid.append(row)
        base.update({"type": "map", "src_axis": srcs, "snk_axis": snks, "cop_grid": grid})
        caps = [r for r in pts if "cap" in r]
        if caps and complete:
            ref = max(r["cap"] for r in caps)
            base["cap_grid"] = [[round(next(r["cap"] for r in caps if r["src"] == s and r["snk"] == k) / ref, 4)
                                 for k in snks] for s in srcs]
            base["unit_capacity_kW"] = ref
        if not complete:
            base["warnings"].append("performance table incomplete: missing grid points are NaN; fill before use")
        return base
    f = ex.fields
    if {"cop", "evap_out_C", "cond_out_C"} <= set(f):
        tc = f["cond_out_C"] + approach_K + K0
        te = f["evap_out_C"] - approach_K + K0
        eta = f["cop"] / (tc / (tc - te))
        base.update({"type": "carnot", "eta": round(eta, 4), "approach_evap_K": approach_K,
                     "approach_cond_K": approach_K, "calibrated_to": {"cop": f["cop"], "src_lvg_C": f["evap_out_C"],
                                                                      "snk_lvg_C": f["cond_out_C"]}})
        if f.get("heating_kW"):
            base["unit_capacity_kW"] = f["heating_kW"]
        base["warnings"].append("single rating point: generic Carnot shape calibrated to it; extrapolation risk")
    return base


def write_library(ex: Extraction, out: Path, model_id: str, label: str) -> Path:
    d = to_library(ex, model_id, label)
    out.write_text("# Candidate from equipment PDF parser: REVIEW against the source document before use\n"
                   + yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
    return out
