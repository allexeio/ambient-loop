"""Hourly energy ledger: the single source of truth for every output.

Sign convention: flow[(component, node)][t] is the heat (kWh) the component delivers INTO the
node in hour t. A negative value means the component takes heat from the node.

Nodes:
  "loop"           an internal circuit; its balance must close every hour
  "svc:<stream>"   a load (boundary). Heating streams receive +demand; cooling streams −demand.
  "ambient"        outdoor air (boundary)

Conservation, checked every hour:
  - internal nodes:   Σ_components flow[(c, node)] = 0
  - each component:   Σ_nodes flow[(c, node)] = W_elec[c]
    (energy in = energy out; electricity ends up as heat somewhere). The exception is the
    `unmet` pseudo-component, which records energy that no plant could supply or reject.
  - each load:        Σ flow into svc node = ±demand
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import pandas as pd

from ambient_loop.core.provenance import Tag
from ambient_loop.core.series import HOURS, FloatArray

BALANCE_TOL_KWH = 1e-6


class Role(StrEnum):
    UNIT = "unit"  # serves loads directly
    DEFICIT = "deficit"  # balancing plant adding heat to an internal circuit
    SURPLUS = "surplus"  # balancing plant removing heat from an internal circuit
    UNMET = "unmet"  # pseudo-component: energy no plant could provide or reject


@dataclass
class ComponentInfo:
    id: str
    kind: str
    role: Role
    tags: set[Tag] = field(default_factory=set)
    serves: list[str] = field(default_factory=list)  # stream ids, for units


@dataclass
class Ledger:
    internal_nodes: set[str]
    components: dict[str, ComponentInfo] = field(default_factory=dict)
    flows: dict[tuple[str, str], FloatArray] = field(default_factory=dict)
    elec: dict[str, FloatArray] = field(default_factory=dict)
    flags: dict[str, np.ndarray] = field(default_factory=dict)  # name -> bool[8760]
    # derived hourly quantities for reporting (not part of conservation), e.g. loop:reused
    derived: dict[str, FloatArray] = field(default_factory=dict)

    def add_component(self, info: ComponentInfo) -> None:
        if info.id in self.components:
            raise ValueError(f"duplicate component {info.id}")
        self.components[info.id] = info
        self.elec[info.id] = np.zeros(HOURS)

    def add_flow(self, component: str, node: str, q: FloatArray) -> None:
        key = (component, node)
        self.flows[key] = self.flows.get(key, np.zeros(HOURS)) + q

    def add_elec(self, component: str, w: FloatArray) -> None:
        self.elec[component] = self.elec[component] + w

    # ---- queries -------------------------------------------------------------------------
    def node_net(self, node: str) -> FloatArray:
        out = np.zeros(HOURS)
        for (_c, n), q in self.flows.items():
            if n == node:
                out = out + q
        return out

    def component_net(self, component: str) -> FloatArray:
        out = np.zeros(HOURS)
        for (c, _n), q in self.flows.items():
            if c == component:
                out = out + q
        return out

    def flow(self, component: str, node: str) -> FloatArray:
        return self.flows.get((component, node), np.zeros(HOURS))

    def total_elec(self) -> FloatArray:
        return np.sum(list(self.elec.values()), axis=0) if self.elec else np.zeros(HOURS)

    # ---- conservation --------------------------------------------------------------------
    def residuals(self) -> dict[str, float]:
        """Maximum absolute hourly residual (kWh) per conservation check."""
        res: dict[str, float] = {}
        for node in sorted(self.internal_nodes):
            res[f"node:{node}"] = float(np.max(np.abs(self.node_net(node))))
        for cid, info in self.components.items():
            if info.role is Role.UNMET:
                continue
            res[f"component:{cid}"] = float(
                np.max(np.abs(self.component_net(cid) - self.elec[cid]))
            )
        return res

    def assert_closed(self, tol: float = BALANCE_TOL_KWH) -> None:
        bad = {k: v for k, v in self.residuals().items() if v > tol}
        if bad:
            raise AssertionError(f"energy balance not closed (kWh): {bad}")

    def to_frame(self) -> pd.DataFrame:
        """Wide hourly table: one column per flow and per electricity meter."""
        cols: dict[str, FloatArray] = {}
        for (c, n), q in sorted(self.flows.items()):
            cols[f"Q[{c}->{n}]_kWh"] = q
        for c, w in sorted(self.elec.items()):
            cols[f"W[{c}]_kWh"] = w
        for name, d in sorted(self.derived.items()):
            cols[f"derived[{name}]_kWh"] = d
        for name, m in sorted(self.flags.items()):
            cols[f"flag[{name}]"] = m.astype(int)
        df = pd.DataFrame(cols)
        df.index.name = "hour"
        return df
