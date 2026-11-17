import numpy as np
import pytest
from scipy.optimize import linprog

from ambient_loop.core.series import HOURS
from ambient_loop.recovery.pinch import StreamT, generic_cop, match_heat_pumps, problem_table
from ambient_loop.recovery.storage import greedy_shift
from ambient_loop.schema.project import RecoverySettings

S = RecoverySettings()


def st(sid, t_in, t_out, q):
    h = np.full(HOURS, float(q)) if np.isscalar(q) else np.asarray(q, float)
    return StreamT(sid, t_in > t_out, h, np.full(HOURS, float(t_in)), np.full(HOURS, float(t_out)), 1.0)


def run(streams):
    return match_heat_pumps(problem_table(streams, S), S)


def test_textbook_direct_recovery_balanced():
    # hot 80->40 (100 kW), cold 30->70 (100 kW), ΔTmin 2 K: fully recoverable directly
    pr = run([st("h", 80, 40, 100), st("c", 30, 70, 100)])
    assert pr.direct[0] == pytest.approx(100.0, rel=1e-6)
    assert pr.q_hmin[0] == pytest.approx(0.0, abs=1e-6)
    assert pr.q_cmin[0] == pytest.approx(0.0, abs=1e-6)


def test_temperature_cross_limits_direct_recovery():
    # hot 30->20, cold 25->45: only the 25-29 part of the cold stream can be served directly
    pr = run([st("h", 30, 20, 100), st("c", 25, 45, 100)])
    # cold CP = 5 kW/K over shifted 26..46; hot shifted 19..29 -> overlap 26..29 = 3 K x 5 = 15
    assert pr.direct[0] == pytest.approx(15.0, rel=1e-3)
    assert pr.q_hmin[0] == pytest.approx(85.0, rel=1e-3)


def test_all_heating_has_no_recovery():
    pr = run([st("c", 40, 45, 100)])
    assert pr.direct.sum() == 0 and pr.hp_sink.sum() == 0
    assert pr.unmatched_deficit[0] == pytest.approx(100.0)


def test_all_cooling_has_no_recovery():
    pr = run([st("h", 12, 6, 100)])
    assert pr.hp_sink.sum() == 0
    assert pr.unmatched_surplus[0] == pytest.approx(100.0, rel=1e-6)


def test_hp_matching_respects_first_law():
    pr = run([st("h", 16, 10, 100), st("c", 40, 45, 300)])
    assert pr.direct[0] == pytest.approx(0.0, abs=1e-6)
    # heat pump: delivered = source + work, all source used (deficit is larger)
    assert pr.hp_source[0] == pytest.approx(100.0, rel=1e-6)
    assert pr.hp_sink[0] == pytest.approx(pr.hp_source[0] + pr.hp_work[0])
    cop = pr.hp_sink[0] / pr.hp_work[0]
    assert 3.0 < cop < 7.0
    assert pr.coincidence == pytest.approx(1.0)


def test_out_of_phase_gives_low_coincidence():
    day = np.tile((np.arange(24) >= 8) & (np.arange(24) < 20), 365)
    pr = run([st("h", 16, 10, np.where(day, 100.0, 0.0)), st("c", 40, 45, np.where(day, 0.0, 120.0))])
    assert pr.hp_sink.sum() == 0
    assert pr.unmatched_surplus.sum() > 0 and pr.unmatched_deficit.sum() > 0


def test_generic_cop_floor_and_monotonic():
    assert generic_cop(20, 45, 0.5, 3) > generic_cop(10, 45, 0.5, 3)
    assert generic_cop(20, 20, 0.5, 3) < 40  # floored lift


def test_storage_shifts_out_of_phase_heat():
    s = np.tile(np.r_[np.full(12, 10.0), np.zeros(12)], 365)
    n = np.tile(np.r_[np.zeros(12), np.full(12, 10.0)], 365)
    tot, _, soc = greedy_shift(s, n, capacity=200, window_h=24)
    assert tot == pytest.approx(s.sum(), rel=1e-6)  # every day's charge is used the same evening
    assert soc.max() <= 200 + 1e-9
    assert greedy_shift(s, n, capacity=200, window_h=6)[0] < tot


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_greedy_matches_lp_for_lossless_store(seed):
    rng = np.random.default_rng(seed)
    T = 48
    s = rng.uniform(0, 10, T) * (rng.random(T) > 0.5)
    n = rng.uniform(0, 10, T) * (rng.random(T) > 0.5)
    cap = 15.0
    # LP: variables c_t (charge), d_t (discharge); soc_t = Σ(c - d) in [0, cap]; c<=s, d<=n
    A, b = [], []
    for t in range(T):
        row = np.r_[np.where(np.arange(T) <= t, 1, 0), np.where(np.arange(T) <= t, -1, 0)]
        A.append(row)
        b.append(cap)
        A.append(-row)
        b.append(0.0)
    res = linprog(np.r_[np.zeros(T), -np.ones(T)], A_ub=np.array(A), b_ub=b,
                  bounds=[(0, x) for x in s] + [(0, x) for x in n], method="highs")
    best = -res.fun
    got = greedy_shift(s, n, capacity=cap, window_h=T + 1)[0]
    assert got == pytest.approx(best, rel=1e-6, abs=1e-6)
