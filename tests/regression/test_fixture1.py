"""Fixture 1 regression: reproduce the Milieu workbook (brief §9.1).

Needs the client workbook at inputs/fixture1.xlsx. The file is git-ignored for
confidentiality, so this test skips when it is missing (e.g. in CI).
"""

from pathlib import Path

import numpy as np
import pytest

from ambient_loop.io.milieu_workbook import import_workbook, write_project
from ambient_loop.reporting.allocation import summarise
from ambient_loop.reporting.compare import compare
from ambient_loop.schema.project import load_project
from ambient_loop.sim.inputs import build_inputs
from ambient_loop.sim.simple_mode import run_simple

ROOT = Path(__file__).resolve().parents[2]
WB = ROOT / "inputs/fixture1.xlsx"
MAPPING = ROOT / "library/mappings/milieu_ambient_loop_workbook.yaml"

pytestmark = pytest.mark.skipif(not WB.exists(), reason="Fixture 1 workbook not supplied")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixture1")
    write_project(import_workbook(WB, MAPPING), out, "fixture1")
    project, base = load_project(out / "project.yaml")
    inputs, profile, _w, _f = build_inputs(project, base)
    led = run_simple(project, inputs)
    s = summarise(project, inputs, led, profile)
    return project, inputs, led, s, base


def test_brief_headline_figures(run):
    _p, _i, _l, s, _b = run
    pr = s.per_service["pro_rata"]
    # brief §9.1, ±0.5 %
    for svc, target in {"LTHW": 3.01, "DHW": 2.74, "CHW": 6.44}.items():
        assert pr[svc].value == pytest.approx(target, rel=0.005)
    assert s.system.value == pytest.approx(3.14, rel=0.005)
    assert s.electricity_MWh == pytest.approx(926, rel=0.005)
    for svc, target in {"LTHW": 82.9, "CHW": 634.2, "DHW": 2195.3}.items():
        assert s.demand_MWh[svc] == pytest.approx(target, rel=0.005)


def test_matches_workbook_cells_and_hours(run):
    p, i, led, s, base = run
    checks, hourly = compare(p, i, led, s, base)
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    for h in hourly:
        assert h.max_abs < 1e-6, h


def test_balance_closed(run):
    _p, _i, led, _s, _b = run
    assert max(led.residuals().values()) < 1e-6
    assert np.isfinite(led.total_elec()).all()
