from pathlib import Path

import pytest

from ambient_loop.io.equipment_pdf import parse_pdf, to_library

PDF = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_selection_report.pdf"


def test_parse_key_values_and_table():
    ex = parse_pdf(PDF)
    f = ex.fields
    assert f["heating_kW"] == pytest.approx(220.5)
    assert f["cop"] == pytest.approx(3.59)
    assert f["evap_out_C"] == 20.0 and f["cond_out_C"] == 65.0
    assert f["refrigerant"] == "R1234ze"
    assert f["min_cond_flow_ls"] == pytest.approx(3.2)
    assert len(ex.table) == 6


def test_library_candidate_is_a_reviewable_map():
    d = to_library(parse_pdf(PDF), "xyz100", "XYZ 100")
    assert d["type"] == "map" and d["review_required"] is True
    assert d["src_axis"] == [10.0, 20.0, 30.0] and d["snk_axis"] == [55.0, 65.0]
    assert d["cop_grid"][1][1] == pytest.approx(3.59)
    assert d["cap_grid"][2][1] == pytest.approx(1.0)
