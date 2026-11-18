from __future__ import annotations

from ambient_loop.studio import catalogue as C
from ambient_loop.studio.terminals import TerminalChoice, circuit_temperatures, needs_simultaneous, propose_terminals


def test_circuit_temperatures_take_worst_case() -> None:
    ch = [TerminalChoice("a", "fcu_4pipe", (45, 40), (10, 16)), TerminalChoice("b", "ufh", (35, 30), None),
          TerminalChoice("c", "chilled_beams", (40, 35), (14, 17))]
    t = circuit_temperatures(ch)
    assert t == {"lthw_flow_C": 45, "lthw_return_C": 40, "chw_flow_C": 10, "chw_return_C": 16}
    assert needs_simultaneous(ch)


def test_proposal_residential_without_cooling_gets_ufh() -> None:
    p = propose_terminals([{"name": "Flats", "space_type": "residential", "cooling_kWh": 0}])
    assert p[0].terminal == "ufh" and p[0].chw is None


def test_catalogue_compatibility() -> None:
    s = C.enumerate_strategies(True, True, True, False)
    ids = {x.id: x for x in s}
    assert len(s) > 15
    assert not any(x.compatible for x in s if x.heat == "vrf")  # water terminals
    assert not any(x.compatible for x in s if x.heat == "ashp2")  # simultaneous demand
    r = C.enumerate_strategies(True, True, True, True)
    assert all(not x.compatible for x in r if x.heat != "vrf")
    assert any(x.heat == "gas" for x in ids.values())
