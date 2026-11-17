import numpy as np
import pytest

from ambient_loop.core.curves import Curve1D
from ambient_loop.core.metric import Allocation, Boundary, DhwProfile, Mode, ratio
from ambient_loop.core.provenance import Tag, TaggedFloat, worst
from ambient_loop.core.series import HOURS, monthly_sum, monthly_to_hourly


def test_tagged_float_bare_number_is_assumption():
    t = TaggedFloat.model_validate(3.2)
    assert t.value == 3.2 and t.tag is Tag.ASSUMPTION


def test_worst_tag():
    assert worst([Tag.MANUFACTURER_DATA, Tag.EXTRAPOLATED, Tag.GENERIC]) is Tag.EXTRAPOLATED


def test_curve_interpolates_and_flags_extrapolation():
    c = Curve1D(x=[-5, 0, 10], y=[3.0, 3.5, 5.0])
    y, ex = c(np.array([-10.0, -5.0, 5.0, 10.0, 20.0]))
    np.testing.assert_allclose(y, [3.0, 3.0, 4.25, 5.0, 5.0])
    assert ex.tolist() == [True, False, False, False, True]


def test_curve_hold_by_definition_is_not_flagged():
    c = Curve1D(x=[0, 10], y=[3, 5], hold_above=True)
    _, ex = c(np.array([20.0]))
    assert not ex.any()


def test_curve_rejects_non_monotonic():
    with pytest.raises(ValueError):
        Curve1D(x=[0, 0], y=[1, 2])


def test_monthly_roundtrip():
    h = monthly_to_hourly([1.0] * 12)
    assert h.shape == (HOURS,)
    np.testing.assert_allclose(monthly_sum(h)[:2], [744, 672])


def test_metric_always_prints_boundary():
    b = Boundary(pumps_included=False, allocation=Allocation.PRO_RATA,
                 dhw_profile=DhwProfile.FLAT, mode=Mode.SIMPLE)
    s = str(ratio("DHW", 300.0, 100.0, b, frozenset({Tag.PROJECT_DATA})))
    assert "3.00" in s and "pumps excl." in s and "pro_rata" in s and "simple" in s
