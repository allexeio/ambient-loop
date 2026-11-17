"""Storage time-shift: how much extra recovery a store unlocks.

The store carries heat in time at the heat-pump source level. It charges from surplus left
unmatched in one hour and discharges to the source heat that the unmet deficit of a later hour
would need. Its parameters are capacity C (kWh) and a holding window N (h): heat older than N
hours is discarded. A standing loss λ per hour applies to the stored content.

For one commodity, a chronological greedy policy maximises recovered energy: pass
same-hour surplus straight through, discharge whenever there is need, charge whenever there is surplus, oldest heat first, and when full
displace the oldest with the newest. That holds for a lossless store with a capacity limit;
with small losses it is close to optimal. tests/unit/test_recovery.py checks it against an
exact LP on random cases. [Assumption A-016: all temperature bands are aggregated; the extra
2 × ΔT_min approach through a store is not deducted.]
"""

from __future__ import annotations

from collections import deque

import numpy as np

from ambient_loop.core.series import HOURS, FloatArray


def greedy_shift(surplus: FloatArray, need: FloatArray, capacity: float, window_h: int,
                 loss: float = 0.0) -> tuple[float, FloatArray, FloatArray]:
    """Returns (total recovered kWh, hourly discharge, hourly state of charge)."""
    if capacity <= 0:
        return 0.0, np.zeros(HOURS), np.zeros(HOURS)
    q: deque[list[float]] = deque()  # [age_h, kWh]
    dis = np.zeros(len(surplus))
    soc = np.zeros(len(surplus))
    keep = 1.0 - loss
    for t in range(len(surplus)):
        # age and decay
        for item in q:
            item[0] += 1
            item[1] *= keep
        while q and q[0][0] > window_h:
            q.popleft()
        # same-hour pass-through first (zero storage time), then discharge oldest first
        direct = min(surplus[t], need[t])
        dis[t] += direct
        n = need[t] - direct
        while n > 1e-12 and q:
            take = min(n, q[0][1])
            q[0][1] -= take
            n -= take
            dis[t] += take
            if q[0][1] <= 1e-12:
                q.popleft()
        # charge, displacing oldest when full
        s = surplus[t] - direct
        if s > 1e-12:
            content = sum(i[1] for i in q)
            room = capacity - content
            if s > room:
                over = s - room
                while over > 1e-12 and q:
                    d = min(over, q[0][1])
                    q[0][1] -= d
                    over -= d
                    if q[0][1] <= 1e-12:
                        q.popleft()
                s = min(s, capacity)
            q.append([0, s])
        soc[t] = sum(i[1] for i in q)
    return float(dis.sum()), dis, soc


def storage_surface(surplus: FloatArray, need: FloatArray, capacities: list[float],
                    windows: list[int], loss: float) -> dict[tuple[float, int], float]:
    return {(c, w): greedy_shift(surplus, need, c, w, loss)[0] for c in capacities for w in windows}
