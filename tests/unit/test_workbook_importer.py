"""Importer machinery tested on a synthetic workbook laid out like the Milieu workbook.

The synthetic workbook holds plain values, which openpyxl reads exactly as it reads cached
formula results.
"""

from pathlib import Path

import numpy as np
import openpyxl
import pytest

from ambient_loop.io.milieu_workbook import WorkbookError, import_workbook, write_project
from ambient_loop.reporting.allocation import summarise
from ambient_loop.schema.project import load_project
from ambient_loop.sim.inputs import build_inputs
from ambient_loop.sim.simple_mode import run_simple

MAPPING = Path(__file__).resolve().parents[2] / "library/mappings/milieu_ambient_loop_workbook.yaml"

HOURLY_HEADERS = [
    "Day", "Hour ending", "Month", "LTHW kW", "CHW kW", "DHW kW", "London TRY DBT C",
    "Loop heat reused kW", "Residual roof heat kW", "Residual roof cooling kW", "ASHP heat COP",
    "Dry cooler rejection kW", "LTHW total kWe", "DHW total kWe", "CHW total kWe",
    "Total plant kWe", "LTHW isolated kWe", "DHW isolated kWe", "CHW isolated kWe",
]
PARAMS = [
    ("CXWF LTHW gross COP", 5.0), ("CXWF CHW gross EER", 6.0), ("Gross DHW COP", 3.0),
    ("Rooftop cooling fallback EER", 4.0), ("Hourly heat-reuse fraction", 1.0),
    ("Include separately listed rooftop pumps", 0), ("Rooftop heating pump / heat output", 0.01),
    ("Rooftop cooling pump / cooling output", 0.01), ("Rated heat rejection", 800),
    ("Dry cooler electrical input per kW rejected", 0.1), ("Outdoor screening cutoff", 27),
    ("Time-step duration", 1),
]


def make_workbook(path: Path, duplicate_label: bool = False) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hourly calculation"
    ws.append(["Hourly ambient-loop energy balance"])
    ws.append([])
    ws.append([])
    ws.append(["note"])
    ws.append(HOURLY_HEADERS)
    rng = np.random.default_rng(1)
    for h in range(8760):
        ws.append([h // 24 + 1, h % 24 + 1, 1, *rng.uniform(0, 100, 3), 10.0] + [0.0] * 12)
    ins = wb.create_sheet("Inputs")
    ins.append(["Plant performance"])
    for label, v in PARAMS:
        ins.append([label, v])
    if duplicate_label:
        ins.append(["Another LTHW gross COP", 4.0])
    ins.append([])
    ins.append(["Outdoor C", "Heat output kW", "Gross input kW", "Gross COP"])
    for x, cop in [(-5, 3.0), (0, 3.5), (10, 4.5)]:
        ins.append([x, 1, 1, cop])
    res = wb.create_sheet("Results")
    res.append(["Service", "Useful", "Elec", "Ratio", "Isolated"])
    for s in ("LTHW", "DHW", "CHW"):
        res.append([s, 1.0, 1.0, 1.0, 1.0])
    for label in ("Total plant electricity", "Isolated electricity, no heat reuse",
                  "Ambient-loop heat reused", "Residual rooftop heating",
                  "Surplus heat for outdoor rejection"):
        res.append([label, 1.0])
    wb.save(path)


def test_import_roundtrip_and_cell_refs(tmp_path):
    wbp = tmp_path / "wb.xlsx"
    make_workbook(wbp)
    imp = import_workbook(wbp, MAPPING)
    assert imp.params["cop_lthw"] == (5.0, "Inputs!B2")
    assert imp.curves["ashp_heating_cop"][1] == [3.0, 3.5, 4.5]
    assert len(imp.hourly) == 8760
    proj_file = write_project(imp, tmp_path / "proj", "synthetic_wb")
    project, base = load_project(proj_file)
    inputs, profile, _w, _f = build_inputs(project, base)
    led = run_simple(project, inputs)
    s = summarise(project, inputs, led, profile)
    assert s.demand_MWh["LTHW"] == pytest.approx(imp.hourly["LTHW_kW"].sum() / 1e3)
    assert project.plant.units[0].efficiency.ref.endswith("Inputs!B2")


def test_ambiguous_label_fails_loudly(tmp_path):
    wbp = tmp_path / "wb.xlsx"
    make_workbook(wbp, duplicate_label=True)
    with pytest.raises(WorkbookError, match="matched 2 cells"):
        import_workbook(wbp, MAPPING)
