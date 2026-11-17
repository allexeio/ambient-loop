import numpy as np
import pytest
from pydantic import ValidationError

from ambient_loop.core.metric import DhwProfile
from ambient_loop.loads.validation import classify_dhw, dhw_peak_fraction, validate_loads
from ambient_loop.schema.project import Project
from tests import synthetic as syn


def _raw():
    return syn.project().model_dump(mode="json")


def test_unserved_stream_rejected():
    raw = _raw()
    raw["plant"]["units"] = raw["plant"]["units"][:2]
    with pytest.raises(ValidationError, match="streams with no unit"):
        Project.model_validate(raw)


def test_chiller_cannot_serve_heating():
    raw = _raw()
    raw["plant"]["units"][1]["serves"] = ["lthw"]
    raw["plant"]["units"][0]["serves"] = ["chw"]
    with pytest.raises(ValidationError, match="cannot serve"):
        Project.model_validate(raw)


def test_reuse_fraction_bounds():
    raw = _raw()
    raw["plant"]["reuse_fraction"] = {"value": 1.2}
    with pytest.raises(ValidationError):
        Project.model_validate(raw)


def test_flat_vs_shaped_dhw():
    flat = np.full(8760, 10.0)
    shape = np.array([1, 1, 1, 1, 1, 2, 8, 12, 10, 6, 4, 4, 4, 4, 4, 4, 5, 7, 9, 8, 5, 3, 2, 1.0])
    shaped = np.tile(shape, 365)
    assert dhw_peak_fraction(flat) == pytest.approx(1 / 24)
    assert classify_dhw(flat) is DhwProfile.FLAT
    assert classify_dhw(shaped) is DhwProfile.SHAPED
    w = validate_loads({"dhw": ("DHW", flat)})
    assert [x.code for x in w] == ["R6-FLAT-DHW"]
    assert validate_loads({"dhw": ("DHW", shaped)}) == []
