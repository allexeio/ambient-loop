"""Importer for the Milieu hourly ambient-loop workbook (Fixture 1 format).

The importer reads cached cell values (the values Excel last calculated), never formulas. Every
imported parameter keeps its sheet and cell address as its provenance `ref`, so any figure can
be traced back to the workbook. A mapping YAML (library/mappings/) holds all the labels. When a
label matches no cell, or more than one, the import stops. A changed workbook layout therefore
fails loudly instead of being silently misread.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import openpyxl
import pandas as pd
import yaml
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from ambient_loop.core.series import HOURS


class WorkbookError(ValueError):
    pass


@dataclass
class ImportedWorkbook:
    hourly: pd.DataFrame  # model inputs, 8,760 rows
    reference_hourly: pd.DataFrame  # workbook outputs, 8,760 rows
    params: dict[str, tuple[float, str]]  # name -> (value, cell ref)
    curves: dict[str, tuple[list[float], list[float], str]]  # name -> (x, y, ref)
    results: dict[str, Any] = field(default_factory=dict)
    source: str = ""


def _open(path: Path) -> openpyxl.Workbook:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # conditional-formatting extension warnings
        return openpyxl.load_workbook(path, data_only=True, read_only=False)


def _find_label(ws: Worksheet, col: str, pattern: str, exact: bool = False) -> int:
    rx = re.compile(pattern, re.I)
    ci = column_index_from_string(col)
    rows = []
    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, ci).value
        if isinstance(v, str):
            hit = v.strip() == pattern if exact else bool(rx.search(v.strip()))
            if hit:
                rows.append(r)
    if len(rows) != 1:
        raise WorkbookError(
            f"{ws.title}!{col}: label /{pattern}/ matched {len(rows)} cells (rows {rows[:5]})"
        )
    return rows[0]


def _num(ws: Worksheet, ref: str) -> float:
    v = ws[ref].value
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise WorkbookError(
            f"{ws.title}!{ref}: expected a number, found {v!r}. If formulas show as None, "
            "open and save the workbook in Excel so cached values exist."
        )
    return float(v)


def _header_columns(ws: Worksheet, patterns: dict[str, str]) -> tuple[int, dict[str, int]]:
    """Find the header row (first 15 rows) holding all patterns; return (row, name->col)."""
    for r in range(1, 16):
        texts = {c: str(ws.cell(r, c).value).strip() for c in range(1, ws.max_column + 1)
                 if ws.cell(r, c).value is not None}
        found: dict[str, int] = {}
        for name, pat in patterns.items():
            hits = [c for c, t in texts.items() if re.search(pat, t, re.I)]
            if len(hits) == 1:
                found[name] = hits[0]
        if len(found) == len(patterns):
            return r, found
    raise WorkbookError(f"{ws.title}: no header row contains all of {list(patterns.values())}")


def _read_columns(ws: Worksheet, patterns: dict[str, str]) -> pd.DataFrame:
    hdr, cols = _header_columns(ws, patterns)
    data: dict[str, np.ndarray] = {}
    for name, c in cols.items():
        vals = [ws.cell(r, c).value for r in range(hdr + 1, hdr + 1 + HOURS)]
        arr = pd.to_numeric(pd.Series(vals), errors="coerce").to_numpy(dtype=np.float64)
        if np.isnan(arr).any():
            n = int(np.isnan(arr).sum())
            raise WorkbookError(
                f"{ws.title}!{get_column_letter(c)}: {n} empty/non-numeric of {HOURS} rows "
                f"from row {hdr + 1}. Cached values missing? Open and save in Excel."
            )
        data[name] = arr
    return pd.DataFrame(data)


def import_workbook(path: Path, mapping_path: Path) -> ImportedWorkbook:
    m = yaml.safe_load(mapping_path.read_text())
    wb = _open(path)

    hs = wb[m["hourly"]["sheet"]]
    hourly = _read_columns(hs, m["hourly"]["columns"])
    reference = _read_columns(hs, m["hourly"]["reference"])

    ins = wb[m["inputs"]["sheet"]]
    lc, vc = m["inputs"]["label_column"], m["inputs"]["value_column"]
    params: dict[str, tuple[float, str]] = {}
    for name, pat in m["inputs"]["params"].items():
        r = _find_label(ins, lc, pat)
        ref = f"{vc}{r}"
        params[name] = (_num(ins, ref), f"{ins.title}!{ref}")

    curves: dict[str, tuple[list[float], list[float], str]] = {}
    for name, spec in m["inputs"].get("curves", {}).items():
        r = _find_label(ins, lc, spec["header_label"])
        ycol = next(
            (c for c in range(1, ins.max_column + 1)
             if re.search(spec["y_header"], str(ins.cell(r, c).value or "").strip(), re.I)),
            None,
        )
        if ycol is None:
            raise WorkbookError(f"{ins.title} row {r}: no column /{spec['y_header']}/")
        xs: list[float] = []
        ys: list[float] = []
        rr = r + 1
        lci = column_index_from_string(lc)
        while isinstance(xv := ins.cell(rr, lci).value, (int, float)):
            xs.append(float(xv))
            ys.append(_num(ins, f"{get_column_letter(ycol)}{rr}"))
            rr += 1
        if len(xs) < 2:
            raise WorkbookError(f"{ins.title}: curve {name} has fewer than 2 points")
        ref = f"{ins.title}!{lc}{r + 1}:{get_column_letter(ycol)}{rr - 1}"
        curves[name] = (xs, ys, ref)

    rs = wb[m["results"]["sheet"]]
    rlc = m["results"]["label_column"]
    results: dict[str, Any] = {"services": {}, "totals": {}}
    for svc in m["results"]["services"]:
        r = _find_label(rs, rlc, svc, exact=True)
        results["services"][svc] = {
            k: _num(rs, f"{col}{r}") for k, col in m["results"]["service_columns"].items()
        }
    tvc = m["results"]["totals_value_column"]
    for k, pat in m["results"]["totals"].items():
        r = _find_label(rs, rlc, pat)
        results["totals"][k] = _num(rs, f"{tvc}{r}")

    step = params.get("time_step_h", (1.0, ""))[0]
    if step != 1.0:
        raise WorkbookError(f"time step {step} h not supported; expected 1 h")

    return ImportedWorkbook(hourly, reference, params, curves, results, source=path.name)


def write_project(imp: ImportedWorkbook, out_dir: Path, name: str) -> Path:
    """Write project.yaml, loads.csv, weather.csv and the workbook reference files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    imp.hourly[["LTHW_kW", "CHW_kW", "DHW_kW"]].to_csv(out_dir / "loads.csv", index=False)
    imp.hourly[["dry_bulb_C"]].to_csv(out_dir / "weather.csv", index=False)
    imp.reference_hourly.to_csv(out_dir / "reference_hourly.csv", index=False)
    (out_dir / "reference_results.yaml").write_text(
        yaml.safe_dump({"source": imp.source, **imp.results}, sort_keys=False)
    )

    p = imp.params

    def tf(key: str) -> dict[str, Any]:
        v, ref = p[key]
        return {"value": v, "tag": "project_data", "ref": f"{imp.source} {ref}"}

    cx, cy, cref = imp.curves["ashp_heating_cop"]
    project: dict[str, Any] = {
        "name": name,
        "description": f"Imported from {imp.source} (workbook-compatible simple mode).",
        "mode": "simple",
        "loads": {"file": "loads.csv"},
        "weather": {"file": "weather.csv", "format": "table", "dry_bulb_column": "dry_bulb_C"},
        "streams": [
            {"id": "lthw", "kind": "space_heating", "service": "LTHW",
             "duty_kW": {"column": "LTHW_kW"}, "T_in_C": 40, "T_out_C": 45},
            {"id": "chw", "kind": "space_cooling", "service": "CHW",
             "duty_kW": {"column": "CHW_kW"}, "T_in_C": 16, "T_out_C": 10},
            {"id": "dhw", "kind": "dhw", "service": "DHW",
             "duty_kW": {"column": "DHW_kW"}, "T_in_C": "mains", "T_out_C": 65},
        ],
        "plant": {
            "reuse_fraction": tf("reuse_fraction"),
            "pumps_included": bool(p["pumps_switch"][0]),
            "units": [
                {"id": "lthw_wshp", "kind": "heat_pump", "serves": ["lthw"],
                 "efficiency": tf("cop_lthw")},
                {"id": "chw_wshp", "kind": "chiller", "serves": ["chw"],
                 "efficiency": tf("eer_chw")},
                {"id": "dhw_wshp", "kind": "heat_pump", "serves": ["dhw"],
                 "efficiency": tf("cop_dhw")},
            ],
            "deficit_plant": [
                {"id": "roof_ashp_heat", "kind": "ashp_heating",
                 # Workbook holds the end values outside the map; we hold them too but flag
                 # those hours as extrapolated.
                 "efficiency_curve": {"x": cx, "y": cy, "tag": "manufacturer_data",
                                      "ref": f"{imp.source} {cref}"},
                 "pump_kW_per_kW": tf("pump_heat_ratio")},
            ],
            "surplus_plant": [
                {"id": "dry_cooler", "kind": "dry_cooler",
                 "capacity_kW": p["dc_capacity_kW"][0],
                 "cutoff_dry_bulb_C": p["dc_cutoff_C"][0],
                 "fan_kW_per_kW": tf("dc_elec_ratio")},
                {"id": "roof_ashp_cool", "kind": "ashp_cooling",
                 "efficiency": tf("eer_roof_cool"),
                 "pump_kW_per_kW": tf("pump_cool_ratio")},
            ],
        },
    }
    out = out_dir / "project.yaml"
    out.write_text(
        "# Generated by `ambient-loop import-workbook`. Stream temperatures are placeholders\n"
        "# (simple mode does not use them); confirm them before running the recovery analysis.\n"
        + yaml.safe_dump(project, sort_keys=False)
    )
    return out


def inspect_workbook(path: Path, rows: int = 8) -> str:
    """Markdown overview of sheets, sizes and first rows, for building a new mapping."""
    wb = _open(path)
    lines = [f"# {path.name}", ""]
    for ws in wb.worksheets:
        lines.append(f"## {ws.title} ({ws.max_row} rows x {ws.max_column} cols)")
        for r in range(1, min(ws.max_row, rows) + 1):
            cells = [f"{ws.cell(r, c).coordinate}={ws.cell(r, c).value!r}"
                     for c in range(1, ws.max_column + 1) if ws.cell(r, c).value is not None]
            if cells:
                lines.append("- " + " | ".join(cells)[:400])
        lines.append("")
    return "\n".join(lines)
