import numpy as np
import pytest

from ambient_loop.physics.fluids import get_fluid, htc_factor, pressure_drop_factor
from ambient_loop.physics.ground import BoreholeField
from ambient_loop.physics.library import build_model
from ambient_loop.physics.performance import CarnotModel, CO2Model, Fleet, MapModel, PartLoad
from ambient_loop.physics.pumps import Pump, max_dT_from_min_flow
from ambient_loop.physics.rejection import DryCooler, wet_bulb_stull
from ambient_loop.physics.store import StratifiedStore
from ambient_loop.schema.arrangement import ModelSpec
from ambient_loop.core.provenance import Tag


def test_water_properties_match_iapws():
    w = get_fluid("water")
    assert w.cp(20) == pytest.approx(4184, rel=2e-3)
    assert w.rho(20) == pytest.approx(998.2, rel=1e-3)


def test_glycol_freeze_and_penalties():
    pg = get_fluid("propylene_glycol_30")
    assert pg.freeze_C == pytest.approx(-12.8, abs=0.5)
    w = get_fluid("water")
    assert pressure_drop_factor(pg, w, 10) > 1.2  # more flow and viscosity
    assert htc_factor(pg, w, 10) < 0.8


def test_carnot_cop_uses_leaving_temperatures_and_approaches():
    m = CarnotModel("x", Tag.GENERIC, "", eta=0.5, approach_evap_K=2, approach_cond_K=2)
    cop, _ = m.cop_h(np.array([20.0]), np.array([45.0]))
    tc, te = 47 + 273.15, 18 + 273.15
    assert cop[0] == pytest.approx(0.5 * tc / (tc - te))


def test_generic_wshp_reproduces_fixture_points_within_3pc():
    m = build_model(ModelSpec(library="wshp_scroll_inverter"))
    cop_h, _ = m.cop_h(np.array([20.0]), np.array([45.0]))  # 45 °C from 25/20 source
    assert cop_h[0] == pytest.approx(5.443, rel=0.03)
    cop_c, _ = m.cop_h(np.array([10.0]), np.array([25.0]))  # 10 °C CHW to 20/25 sink
    assert cop_c[0] - 1 == pytest.approx(6.753, rel=0.03)


def test_map_interpolates_and_flags_extrapolation():
    m = MapModel("m", Tag.MANUFACTURER_DATA, "", src_axis=(0.0, 10.0), snk_axis=(35.0, 55.0),
                 cop_grid=((4.0, 3.0), (5.0, 4.0)))
    cop, ex = m.cop_h(np.array([5.0, 15.0]), np.array([45.0, 45.0]))
    assert cop[0] == pytest.approx(4.0)
    assert cop[1] == pytest.approx(4.5) and ex.tolist() == [False, True]


def test_co2_model_reproduces_published_points():
    m = build_model(ModelSpec(library="mitsubishi_qahv_n560_co2"))
    for air, tin, cop in [(16, 17, 3.88), (7, 9, 3.65), (7, 15, 3.44)]:
        c, _ = m.cop_h(np.array([float(air)]), np.array([65.0]), np.array([float(tin)]))
        assert c[0] == pytest.approx(cop, rel=0.005)
    hot, _ = m.cop_h(np.array([7.0]), np.array([65.0]), np.array([55.0]))
    assert hot[0] < 2.6  # rule 4b: warm return collapses CO2 COP


def test_part_load_en14825_cycling():
    pl = PartLoad(plr_min=0.5, cd=0.9)
    assert pl.factor(0.5) == pytest.approx(1.0)
    assert pl.factor(0.25) == pytest.approx(0.5 / (0.9 * 0.5 + 0.1))


def test_fleet_sequencing_lead_lag_vs_equal_share():
    m = CarnotModel("x", Tag.GENERIC, "", part_load=PartLoad((0.25, 1.0), (0.8, 1.0), 0.25, 0.9))
    ll = Fleet("f", m, 3, 100, "lead_lag").electricity(150, 4.0)
    es = Fleet("f", m, 3, 100, "equal_share").electricity(150, 4.0)
    assert ll[0] < es[0]  # this model loses COP at part load, so lead-lag wins
    assert Fleet("f", m, 3, 100).electricity(400, 4.0)[1] == pytest.approx(100)  # unmet


def test_store_conserves_energy_and_capacity():
    s = StratifiedStore(10.0, 6, t_init_C=15.0, ua_W_K=0.0, rho=1000, cp=4180)
    cap = s.capacity_kWh(45, 15)
    assert cap == pytest.approx(10 * 4180 * 30 / 3600, rel=1e-6)  # ~348 kWh (rule 7)
    acc = s.charge(1000.0, 45.0, approach_K=0.5)
    assert acc == pytest.approx(s.energy_kWh(15.0), rel=1e-6)
    assert acc <= cap + 1e-6
    d = s.discharge(100.0, 15.0, 40.0)
    assert d == pytest.approx(100.0, rel=1e-6)
    assert s.energy_kWh(15.0) == pytest.approx(acc - d, rel=1e-6)


def test_store_stays_stratified_under_partial_charge():
    s = StratifiedStore(10.0, 8, t_init_C=15.0, ua_W_K=0.0)
    s.charge(100.0, 45.0)
    assert s.top > 40 and s.bottom < 16


def test_pump_affinity_and_min_speed():
    p = Pump("p", 0.05, 20.0, static_fraction=0.0, min_speed=0.3)
    full = p.power_kW(0.05)
    half = p.power_kW(0.025)
    assert half / full == pytest.approx(0.125 / (1 - 0.25 * 0.25), rel=1e-6)  # n³ and drive loss
    assert p.power_kW(0.001) == pytest.approx(p.power_kW(0.015))  # held at min speed
    assert Pump("c", 0.05, 20.0, control="constant").power_kW(0.001) == pytest.approx(full / (1.0))


def test_min_flow_dT_limit():
    assert max_dT_from_min_flow(300, 0.005, 1000, 4180) == pytest.approx(300e3 / (1000 * 4180 * 0.005))


def test_dry_cooler_needs_air_below_loop():
    dc = DryCooler(800, 8, 80, approach_min_K=5)
    assert dc.available_kW(25, 21) == 0.0  # only 4 K
    assert dc.available_kW(25, 17) == pytest.approx(800)
    assert dc.fan_kW(400, 800) < 80 * 0.5


def test_wet_bulb_stull_reference_point():
    assert wet_bulb_stull(np.array([20.0]), np.array([50.0]))[0] == pytest.approx(13.7, abs=0.3)


def test_ground_ils_cools_with_extraction():
    g = BoreholeField(n_bores=20, depth_m=150)
    t = g.fluid_temperature(np.full(8760, 100.0))
    assert t.mean() < g.t_ground_C - 1.0
    t0 = g.fluid_temperature(np.zeros(8760))
    assert np.allclose(t0, g.t_ground_C)
