import numpy as np
import pytest

from ambient_loop.arrangements.archetypes import ARCHETYPES
from ambient_loop.core.series import HOURS
from ambient_loop.io.new_project import create_project, parse_mapping
from ambient_loop.loads.synth import SynthSpec, synthesise
from ambient_loop.scenarios.run import evaluate, load_context


def weather():
    h = np.arange(HOURS)
    return 11 - 7 * np.cos(2 * np.pi * (h / 24 - 15) / 365) + 4 * np.sin(2 * np.pi * ((h % 24) - 9) / 24)


def test_synthesis_hits_annual_targets():
    sp = SynthSpec("hotel", 10_000, 400, heating_kWh_m2=40, cooling_kWh_m2=60, dhw_litres_per_occupant_day=100,
                   dhw_store_C=60, circulation_fraction=0.25)
    out = synthesise(sp, weather())
    assert out["LTHW_kW"].sum() == pytest.approx(400_000)
    assert out["CHW_kW"].sum() == pytest.approx(600_000)
    draw = 100 * 400 * 4.186 * 50 / 3600 * 365
    assert out["DHW_kW"].sum() == pytest.approx(draw / 0.75, rel=1e-6)
    # heating peaks in winter, cooling in summer
    assert out["LTHW_kW"][:744].sum() > out["LTHW_kW"][4344:5088].sum()
    assert out["CHW_kW"][4344:5088].sum() > out["CHW_kW"][:744].sum()


def test_mapping_parser():
    m = parse_mapping("LTHW=Heating Profile (W)*0.001, chw=Cooling kW")
    assert m == {"LTHW": ("Heating Profile (W)", 0.001), "CHW": ("Cooling kW", 1.0)}


def test_created_project_runs_the_ladder(tmp_path):
    out = synthesise(SynthSpec("office", 8000, 800), weather())
    loads = {k.replace("_kW", ""): v for k, v in out.items()}
    p = create_project(tmp_path / "p", "p", loads, weather(), load_tag="assumption")
    ctx = load_context(p)
    assert ctx.site.cooling_unit_kW > 0  # autosized from peaks
    for aid in ("S1", "S3", "A1"):
        c = evaluate(ctx, ARCHETYPES[aid](ctx.site))
        assert c.electricity_MWh > 0
        assert c.flags["Unmet cooling hours"] == 0
