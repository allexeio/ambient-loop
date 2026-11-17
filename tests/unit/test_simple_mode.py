import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ambient_loop.reporting.allocation import (
    allocate_incremental,
    allocate_pro_rata,
    isolated,
)
from ambient_loop.sim.ledger import BALANCE_TOL_KWH
from ambient_loop.sim.simple_mode import LOOP, run_simple
from tests import synthetic as syn


def total(ledger):
    return ledger.total_elec().sum()


def test_balanced_case_needs_no_balancing_plant():
    # chiller EER 6: 100 kW cooling rejects 116.67 kW. DHW COP 3 extracts 2/3 of its output.
    dhw = 116.6666666666667 * 1.5
    led = run_simple(syn.project(), syn.inputs(chw=100.0, dhw=dhw))
    assert led.elec["ashp_h"].max() == pytest.approx(0.0, abs=1e-9)
    assert led.elec["ashp_c"].max() == pytest.approx(0.0, abs=1e-9)
    assert led.derived["loop:reused"].sum() == pytest.approx(116.6666666666667 * 8760)


def test_all_heating_hand_calc():
    # 100 kW LTHW at COP 5: W = 20, extraction 80; ASHP COP at 10 °C = 4.5
    led = run_simple(syn.project(), syn.inputs(lthw=100.0, db=10.0))
    assert led.elec["hp_l"][0] == pytest.approx(20.0)
    assert led.elec["ashp_h"][0] == pytest.approx(80.0 / 4.5)
    assert led.flags["ashp_h:outside_map"].sum() == 0


def test_all_cooling_dry_cooler_then_ashp():
    # 1,000 kW cooling at EER 6 rejects 1,166.7 kW; dry cooler capped at 800 kW
    led = run_simple(syn.project(), syn.inputs(chw=1000.0, db=20.0))
    r = 1000.0 * (1 + 1 / 6)
    assert -led.flow("dc", LOOP)[0] == pytest.approx(800.0)
    assert led.elec["dc"][0] == pytest.approx(80.0)
    assert led.elec["ashp_c"][0] == pytest.approx((r - 800.0) / 4.0)


@pytest.mark.parametrize("db, available", [(26.9, True), (27.0, True), (27.1, False)])
def test_dry_cooler_cutoff_is_inclusive(db, available):
    led = run_simple(syn.project(), syn.inputs(chw=100.0, db=db))
    assert bool(led.flags["dc:available"][0]) is available


def test_out_of_phase_demands_get_no_same_hour_reuse():
    led = run_simple(syn.project(), syn.out_of_phase())
    assert led.derived["loop:reused"].sum() == pytest.approx(0.0)


def test_capacity_shortfall_goes_to_unmet_and_balance_still_closes():
    led = run_simple(syn.project(ashp_capacity=10.0), syn.inputs(lthw=100.0))
    assert led.derived["unmet:heat"][0] == pytest.approx(70.0)
    assert max(led.residuals().values()) < BALANCE_TOL_KWH


def test_pump_switch_adds_visible_meters():
    off = run_simple(syn.project(pumps=False), syn.inputs(lthw=100.0))
    on = run_simple(syn.project(pumps=True), syn.inputs(lthw=100.0))
    assert "ashp_h:pumps" not in off.elec
    assert on.elec["ashp_h:pumps"][0] == pytest.approx(80.0 * 0.015)


@settings(max_examples=40, deadline=None)
@given(
    reuse=st.floats(0.0, 1.0),
    lthw=st.floats(0.0, 500.0),
    chw=st.floats(0.0, 2000.0),
    dhw=st.floats(0.0, 800.0),
    db=st.floats(-10.0, 35.0),
    pumps=st.booleans(),
)
def test_conservation_and_allocation_sums(reuse, lthw, chw, dhw, db, pumps):
    p = syn.project(reuse=reuse, pumps=pumps)
    inp = syn.inputs(lthw=lthw, chw=chw, dhw=dhw, db=db)
    led = run_simple(p, inp)
    assert max(led.residuals().values()) < BALANCE_TOL_KWH
    pr = allocate_pro_rata(p, led)
    iso = isolated(p, inp)
    inc = allocate_incremental(p, led, pr, iso)
    t = total(led)
    for a in (pr, inc):
        assert sum(a.annual().values()) == pytest.approx(t, rel=1e-9, abs=1e-6)
    np.testing.assert_allclose(inc.by_service_kWh["CHW"], iso.by_service_kWh["CHW"])


def test_reuse_never_increases_electricity():
    inp = syn.inputs(lthw=50.0, chw=300.0, dhw=200.0, db=5.0)
    e = [total(run_simple(syn.project(reuse=f), inp)) for f in (0.0, 0.5, 1.0)]
    assert e[0] >= e[1] >= e[2]
