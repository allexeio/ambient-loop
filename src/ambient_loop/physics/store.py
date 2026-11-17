"""Thermal stores.

StratifiedStore: N equal-mass nodes (N ≥ 5 for stratified, N = 1 for a single mixed node).
Plug-flow, layer-stack method:
  - charging pushes a layer of mass m at T_in into the top and removes the same mass from the
    bottom, back to the heat source
  - discharging takes mass from the top and returns a layer at T_ret into the bottom
  - after each move the layers are sorted hottest-first. This is buoyant insertion: an inlet
    colder than the top settles at its own level, and any inversion mixes. The stack is then
    re-gridded into N equal-mass nodes, conserving energy exactly.
  - standing loss: UA per node to the ambient temperature, explicit per sub-step

Energy content is measured above a reference temperature (the design return temperature), so
"capacity" means usable energy between T_ref and T_charge. The schematic's buffer example:
10 m³ water from 45 to 15 °C holds 10 × 4.18 × 30 / 3.6 ≈ 349 kWh, against ≈ 58 kWh at
5 K (rule 7).

Inlet and outlet heights: charge enters at the top and discharge leaves the top. The returns
enter at the bottom, or at their buoyant level when that differs.
References: Duffie & Beckman, Solar Engineering of Thermal Processes, 4th ed., §8.4 (plug-flow
stratified tank model); CIBSE Guide G (2014) §2 for DHW storage temperatures.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StratifiedStore:
    volume_m3: float
    nodes: int = 6
    t_init_C: float = 20.0
    ua_W_K: float = 10.0  # whole-store standing-loss coefficient
    t_amb_C: float = 18.0
    rho: float = 990.0
    cp: float = 4180.0
    temps: list[float] = field(default_factory=list)  # top → bottom

    def __post_init__(self) -> None:
        if not self.temps:
            self.temps = [self.t_init_C] * self.nodes
        self.m_node = self.volume_m3 * self.rho / self.nodes  # kg

    # ---- state --------------------------------------------------------------------------
    @property
    def top(self) -> float:
        return self.temps[0]

    @property
    def bottom(self) -> float:
        return self.temps[-1]

    def energy_kWh(self, t_ref: float) -> float:
        return sum(self.m_node * self.cp * (t - t_ref) for t in self.temps) / 3.6e6

    def capacity_kWh(self, t_hot: float, t_ref: float) -> float:
        return self.volume_m3 * self.rho * self.cp * (t_hot - t_ref) / 3.6e6

    # ---- moves --------------------------------------------------------------------------
    def _regrid(self, layers: list[list[float]]) -> None:
        layers.sort(key=lambda x: -x[1])
        out: list[float] = []
        need = self.m_node
        acc_m = acc_e = 0.0
        for m, t in layers:
            while m > 1e-12:
                take = min(m, need - acc_m)
                acc_m += take
                acc_e += take * t
                m -= take
                if acc_m >= need - 1e-9:
                    out.append(acc_e / acc_m)
                    acc_m = acc_e = 0.0
                    if len(out) == self.nodes:
                        break
            if len(out) == self.nodes:
                break
        if len(out) < self.nodes and acc_m > 0:
            out.append(acc_e / acc_m)
        while len(out) < self.nodes:
            out.append(out[-1])
        self.temps = out

    def charge(self, q_kWh: float, t_in: float, approach_K: float = 2.0) -> float:
        """Push heat in at the top from a source at t_in. Water leaves the bottom.

        Returns the heat accepted (kWh). The store accepts only while its bottom is colder
        than t_in − approach (the source's return).
        """
        if q_kWh <= 1e-12 or self.volume_m3 <= 0:
            return 0.0
        accepted = 0.0
        for _ in range(self.nodes * 4):  # bounded iterations
            t_out = self.bottom
            dT = t_in - t_out
            if dT <= approach_K or q_kWh - accepted <= 1e-9:
                break
            m = min((q_kWh - accepted) * 3.6e6 / (self.cp * dT), self.m_node)
            layers = [[self.m_node, t] for t in self.temps]
            # remove mass m from the bottom
            rem = m
            while rem > 1e-12 and layers:
                take = min(rem, layers[-1][0])
                layers[-1][0] -= take
                rem -= take
                if layers[-1][0] <= 1e-12:
                    layers.pop()
            layers.append([m, t_in])
            accepted += m * self.cp * dT / 3.6e6
            self._regrid(layers)
        return accepted

    def discharge(self, q_kWh: float, t_ret: float, t_min_useful: float) -> float:
        """Draw heat from the top. The return enters at the bottom (buoyant level).

        Returns the heat delivered (kWh). The store delivers only while its top is at or above
        t_min_useful.
        """
        if q_kWh <= 1e-12 or self.volume_m3 <= 0:
            return 0.0
        delivered = 0.0
        for _ in range(self.nodes * 4):
            t_top = self.top
            dT = t_top - t_ret
            if t_top < t_min_useful or dT <= 0.1 or q_kWh - delivered <= 1e-9:
                break
            m = min((q_kWh - delivered) * 3.6e6 / (self.cp * dT), self.m_node)
            layers = [[self.m_node, t] for t in self.temps]
            rem = m
            while rem > 1e-12 and layers:
                take = min(rem, layers[0][0])
                layers[0][0] -= take
                rem -= take
                if layers[0][0] <= 1e-12:
                    layers.pop(0)
            layers.append([m, t_ret])
            delivered += m * self.cp * dT / 3.6e6
            self._regrid(layers)
        return delivered

    def lose(self, hours: float) -> float:
        """Standing loss over `hours`; returns heat lost (kWh)."""
        if self.volume_m3 <= 0:
            return 0.0
        lost = 0.0
        ua_node = self.ua_W_K / self.nodes
        for i, t in enumerate(self.temps):
            q = ua_node * (t - self.t_amb_C) * hours * 3600  # J
            dt = q / (self.m_node * self.cp)
            self.temps[i] = t - dt
            lost += q / 3.6e6
        return lost


def ua_from_standing_loss(volume_m3: float, kWh_per_24h: float, dT_K: float = 45.0) -> float:
    """UA (W/K) from a declared standing loss (kWh per 24 h at ΔT, BS EN 12897 / ErP basis)."""
    del volume_m3
    return kWh_per_24h * 1000 / 24 / dT_K
