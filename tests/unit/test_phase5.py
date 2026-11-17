import numpy as np
import pytest

from ambient_loop.arrangements.archetypes import ARCHETYPES
from ambient_loop.arrangements.generator import propose
from ambient_loop.core.metric import DhwProfile
from ambient_loop.recovery.pinch import analyse
from ambient_loop.recovery.report import summarise_recovery
from ambient_loop.scenarios.economics import Economics, capex, tou_tariff
from ambient_loop.scenarios.run import ProjectContext
from ambient_loop.scenarios.sensitivity import tornado
from ambient_loop.scenarios.sets import variants
from ambient_loop.scenarios.sweep import Point, pareto, quick_eval, set_path
from tests.unit.test_full_mode import SITE, inputs, project


def ctx():
    p = project().model_copy(update={"streams": [s.model_copy(update={"T_in_C": t[0], "T_out_C": t[1]}) for s, t in
                                                 zip(project().streams, [(40, 45), (16, 10), ("mains", 65)], strict=True)]})
    i = inputs()
    pr, sk = analyse(p, i.demand_kW)
    return ProjectContext(p, None, i, DhwProfile.SHAPED, SITE, summarise_recovery(p, pr, sk), 0.1, [])  # type: ignore[arg-type]


def test_set_path_keeps_deltas_and_validates():
    a = ARCHETYPES["A2+3"](SITE)
    b = set_path(a, "loop.flow_C", 28.0)
    assert b.loop.flow_C == 28.0 and b.loop.return_C == 23.0
    assert set_path(a, "buffer.volume_m3", 20.0).buffer.volume_m3 == 20.0
    with pytest.raises(KeyError):
        set_path(ARCHETYPES["A1"](SITE), "buffer.volume_m3", 5.0)


def test_pareto_front_is_non_dominated():
    pts = [Point({}, n, 0, c, 0, k, 0, 0) for n, k, c in [("a", 1, 10), ("b", 2, 8), ("c", 3, 9), ("d", 4, 5)]]
    assert [p.arrangement_id for p in pareto(pts)] == ["a", "b", "d"]


def test_capex_and_tariff():
    total, items = capex(ARCHETYPES["A2+3"](SITE), Economics.default().rates)
    assert total > 0 and "Stratified buffer" in items and "Preheat PHX package" in items
    t = tou_tariff(0.3, 0.1, 0.5)
    assert t[3] == 0.1 and t[12] == 0.3 and t[17] == 0.5


def test_generator_records_reasons():
    c = ctx()
    svc = {s.id: s.service_name for s in c.project.streams}
    get = lambda k: c.inputs.demand_kW[[s for s, v in svc.items() if v == k][0]]  # noqa: E731
    props = propose(SITE, c.recovery, get("LTHW"), get("CHW"), get("DHW"))
    ids = [a.id for a, _ in props]
    assert "A1" in ids and "A5" in ids and "A3" in ids
    assert all(r for _, r in props)


def test_quick_eval_and_tornado_run():
    c = ctx()
    pt = quick_eval(c, ARCHETYPES["A1"](SITE), Economics.default(), {})
    assert pt.electricity_MWh > 0 and pt.carbon_t == pytest.approx(pt.electricity_MWh * 0.136, rel=1e-6)
    base, rows = tornado(c, "A1")
    assert base == pytest.approx(pt.electricity_MWh, rel=1e-9)
    dhw_row = next(r for r in rows if r[0].startswith("DHW demand"))
    assert dhw_row[1] < 0 < dhw_row[2]  # less DHW, less electricity


def test_variants_scale_demand():
    c = ctx()
    v = variants(c.project, None, c.inputs, {"dhw_scale": [0.5, 1.0]})  # type: ignore[arg-type]
    assert v[0][1].demand_kW["d"].sum() == pytest.approx(0.5 * c.inputs.demand_kW["d"].sum())
    assert np.allclose(v[0][1].demand_kW["l"], c.inputs.demand_kW["l"])
