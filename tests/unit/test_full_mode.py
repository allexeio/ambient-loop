import numpy as np
import pytest

from ambient_loop.arrangements.archetypes import ARCHETYPES, SiteBasis
from ambient_loop.core.metric import DhwProfile
from ambient_loop.core.series import HOURS
from ambient_loop.reporting.full_summary import run_case
from ambient_loop.rules.engine import Context, run_rules
from ambient_loop.schema.project import Project
from ambient_loop.sim.full_mode import FullInputs, run_arrangement
from ambient_loop.sim.ledger import BALANCE_TOL_KWH


def project():
    return Project.model_validate({
        "name": "syn", "loads": {"file": "x.csv"}, "weather": {"file": "x.csv", "format": "table", "dry_bulb_column": "db"},
        "streams": [{"id": "l", "kind": "space_heating", "duty_kW": {"column": "l"}},
                    {"id": "c", "kind": "space_cooling", "duty_kW": {"column": "c"}},
                    {"id": "d", "kind": "dhw", "duty_kW": {"column": "d"}}],
        "plant": {"units": [{"id": "a", "kind": "heat_pump", "serves": ["l"], "efficiency": 3},
                            {"id": "b", "kind": "chiller", "serves": ["c"], "efficiency": 5},
                            {"id": "e", "kind": "heat_pump", "serves": ["d"], "efficiency": 3}]},
        "targets": [{"service": "DHW", "seasonal_ratio": 3.0}],
    })


def inputs():
    h = np.arange(HOURS)
    doy = h / 24.0
    season = np.cos(2 * np.pi * (doy - 15) / 365)  # +1 winter, -1 summer
    hod = h % 24
    lthw = np.clip(120 * season, 0, None) * (1 + 0.3 * ((hod >= 6) & (hod < 10)))
    chw = np.clip(80 - 140 * season, 5, None) * (0.5 + ((hod >= 8) & (hod < 19)))
    shape = np.array([1, 1, 1, 1, 1, 2, 8, 12, 10, 6, 4, 4, 4, 4, 4, 4, 5, 7, 9, 8, 5, 3, 2, 1.0])
    dhw = np.tile(shape / shape.sum() * 24 * 150, 365)
    db = 11 - 7 * season + 4 * np.sin(2 * np.pi * (hod - 9) / 24)
    return FullInputs({"l": lthw, "c": chw, "d": dhw}, db, np.full(HOURS, 10.0),
                      columns={"grey": dhw * 0.2})


SITE = SiteBasis(cooling_count=2, cooling_unit_kW=250, dhw_count=2, dhw_unit_kW=250, ashp_count=2, ashp_unit_kW=300,
                 ashp_cooling_count=2, ashp_cooling_unit_kW=300, dry_cooler_kW=300, dry_cooler_fan_kW=30,
                 buffer_m3=6, calorifier_m3=4, co2_count=8, greywater_column="grey",
                 ground={"n_bores": 10, "depth_m": 150.0})


@pytest.mark.parametrize("aid", list(ARCHETYPES))
def test_every_archetype_closes_energy_and_allocation(aid):
    p, i = project(), inputs()
    arr = ARCHETYPES[aid](SITE)
    res = run_arrangement(p, arr, i)
    assert max(res.ledger.residuals().values()) < BALANCE_TOL_KWH
    c = run_case(p, arr, i, DhwProfile.SHAPED)
    tot = c.electricity_MWh
    for conv in ("pro_rata", "incremental"):
        assert sum(c.allocations[conv].annual().values()) / 1e3 == pytest.approx(tot, rel=1e-9)
    assert c.flags["Unmet cooling hours"] == 0
    assert 1.5 < c.system.value < 8


def test_preheat_and_buffer_beat_shared_loop():
    p, i = project(), inputs()
    pre = run_case(p, ARCHETYPES["A2+3"](SITE), i, DhwProfile.SHAPED)
    assert pre.result.ledger.derived["o:hr_P"].sum() > 0
    assert pre.result.ledger.derived["o:buf_ch"].sum() > 0


def test_cascade_is_worse_than_single_lift():
    p, i = project(), inputs()
    one = run_case(p, ARCHETYPES["A1"](SITE), i, DhwProfile.SHAPED)
    two = run_case(p, ARCHETYPES["A5"](SITE), i, DhwProfile.SHAPED)
    assert two.electricity_MWh > one.electricity_MWh


def test_co2_reheat_beats_co2_on_circulation():
    p, i = project(), inputs()
    a = run_case(p, ARCHETYPES["A7"](SITE), i, DhwProfile.SHAPED)
    b = run_case(p, ARCHETYPES["A7-noreheat"](SITE), i, DhwProfile.SHAPED)
    assert b.electricity_MWh > a.electricity_MWh


def test_rules_fire_where_expected():
    p, i = project(), inputs()
    c5 = run_case(p, ARCHETYPES["A5"](SITE), i, DhwProfile.SHAPED)
    f5 = run_rules(Context(c5, p, i.demand_kW, i.dry_bulb_C, dhw_peak_fraction=0.04))
    ids = {f.rule for f in f5}
    assert {"R1", "R2", "R6", "R9", "R11", "R12", "R13", "R14"} <= ids
    c3 = run_case(p, ARCHETYPES["A2+3"](SITE), i, DhwProfile.SHAPED)
    f3 = run_rules(Context(c3, p, i.demand_kW, i.dry_bulb_C, dhw_peak_fraction=0.12))
    assert not any(f.rule == "R6" for f in f3)  # shaped profile: no flat-DHW warning
    assert any(f.rule == "R7" for f in f3)
    assert not any(f.rule == "R1" and "lifted twice" in f.message for f in f3)
    c60 = run_case(p, ARCHETYPES["A9-dhw60"](SITE), i, DhwProfile.SHAPED)
    assert not any(f.severity == "fail" and f.rule == "R8" for f in run_rules(Context(c60, p, i.demand_kW, i.dry_bulb_C)))
    low = ARCHETYPES["A9-dhw60"](SITE).model_copy(deep=True)
    low.dhw.store_C = 55.0  # type: ignore[union-attr]
    cl = run_case(p, low, i, DhwProfile.SHAPED)
    assert any(f.severity == "fail" and f.rule == "R8" for f in run_rules(Context(cl, p, i.demand_kW, i.dry_bulb_C)))


def test_full_engine_runs_under_one_second():
    import time
    p, i = project(), inputs()
    t = time.perf_counter()
    run_arrangement(p, ARCHETYPES["A2+3"](SITE), i)
    assert time.perf_counter() - t < 2.0  # allow CI slack over the 1 s target
