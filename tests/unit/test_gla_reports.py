
from ambient_loop.arrangements.archetypes import ARCHETYPES
from ambient_loop.reporting.full_summary import run_case
from ambient_loop.reporting.gla import calc_summary, concise_report, conservative_allocation, recommend, service_ratios
from ambient_loop.scenarios.economics import Economics
from tests.unit.test_phase5 import ctx


def test_conservative_convention_never_flatters_heat():
    c0 = ctx()
    c = run_case(c0.project, ARCHETYPES["A2+3"](c0.site), c0.inputs, c0.profile)
    cons = service_ratios(c, "conservative", exclude_distribution=False)
    inc = service_ratios(c, "incremental", exclude_distribution=False)
    assert cons["LTHW"]["ratio"] <= inc["LTHW"]["ratio"] + 1e-9
    assert cons["LTHW"]["ratio"] < 8  # physically recognisable heat pump SCOP
    alloc = conservative_allocation(c)
    total = sum(v.sum() for v in alloc.values())
    assert total >= c.result.ledger.total_elec().sum() - 1e-6  # double counting is only ever upward


def test_sbem_boundary_excludes_distribution_pumps():
    c0 = ctx()
    c = run_case(c0.project, ARCHETYPES["S1"](c0.site), c0.inputs, c0.profile)
    a = service_ratios(c, "conservative", exclude_distribution=True)
    b = service_ratios(c, "conservative", exclude_distribution=False)
    assert a["CHW"]["ratio"] > b["CHW"]["ratio"]


def test_reports_render():
    c0 = ctx()
    cases = [run_case(c0.project, ARCHETYPES[k](c0.site), c0.inputs, c0.profile) for k in ("S0", "S1", "A1", "A2+3")]
    for c in cases:
        c.findings = []
    econ = Economics.default()
    best, reasons = recommend(cases, c0, econ)
    assert best.arrangement.id != "S0"
    html = concise_report(c0, cases, best, reasons, econ, [], ["test"])
    assert "Options: basic to complex" in html and "S0" in html and "A2+3" in html
    s = calc_summary(c0, best, [], ["test"], {"config_hash": "x", "code_version": "0", "git_sha": "y"})
    assert "Seasonal performance values for the energy modelling" in s and "para 10.9" in s and "be seen" in s
