"""Pumps: system curve, affinity laws, variable- or constant-speed control.

Hydraulic power P_h = ρ g H V̇. Electrical P_e = P_h / (η_pump · η_motor · η_drive).

System curve: H(V̇) = H_s + (H_d − H_s)(V̇/V̇_d)², where H_s is the controlled or static head
(e.g. the Δp set-point at the index circuit) and H_d is the design head (CIBSE Guide C §4).

Variable speed (Δp control): flow follows the load at the design ΔT, down to a minimum speed.
  - Operating point on the system curve. Affinity laws: V̇ ∝ n, H ∝ n², P ∝ n³ (pump only).
  - Below n_min the pump holds n_min, and the flow is bypassed or ΔT reduced (rule 11).
  - Part-load efficiency of motor plus drive follows the EN 50598-2 IES2 reference loss points
    (relative losses at 25/50/100 % torque), approximated as
    η_md(x) = η_md,100 · (1 − 0.25·(1 − x)²) for load fraction x [A-024].
Constant speed: full design flow and power whenever the circuit is live.
Glycol: hydraulic power scales by fluids.pressure_drop_factor (Blasius).

Units: V̇ m³/s, H m, P kW.
"""

from __future__ import annotations

from dataclasses import dataclass

G = 9.81


@dataclass(frozen=True)
class Pump:
    name: str
    design_flow_m3s: float
    design_head_m: float
    eta_pump: float = 0.70
    eta_motor_drive: float = 0.88
    control: str = "variable"  # or "constant"
    min_speed: float = 0.30
    static_fraction: float = 0.25  # H_s / H_d (controlled head share)
    glycol_factor: float = 1.0  # from fluids.pressure_drop_factor
    rho: float = 1000.0

    def design_power_kW(self) -> float:
        return self.rho * G * self.design_head_m * self.design_flow_m3s / (
            self.eta_pump * self.eta_motor_drive) / 1e3 * self.glycol_factor

    def power_kW(self, flow_m3s: float) -> float:
        """Electrical power at a required flow (the circuit is assumed live when flow > 0)."""
        if self.design_flow_m3s <= 0:
            return 0.0
        if flow_m3s <= 1e-12:
            return 0.0
        if self.control == "constant":
            return self.design_power_kW()
        x = min(max(flow_m3s / self.design_flow_m3s, self.min_speed), 1.2)
        hs = self.static_fraction * self.design_head_m
        head = hs + (self.design_head_m - hs) * x * x
        ph = self.rho * G * head * x * self.design_flow_m3s / 1e3 * self.glycol_factor
        eta_md = self.eta_motor_drive * (1 - 0.25 * (1 - min(x, 1.0)) ** 2)
        return ph / (self.eta_pump * eta_md)


def flow_for_duty(q_kW: float, dT_K: float, rho: float, cp: float) -> float:
    """Volume flow (m³/s) for a heat duty at a circuit ΔT: V̇ = Q / (ρ cp ΔT)."""
    return q_kW * 1e3 / (rho * cp * dT_K) if dT_K > 0 else 0.0


def max_dT_from_min_flow(capacity_kW: float, min_flow_m3s: float, rho: float, cp: float) -> float:
    """Largest ΔT a machine can run at full capacity without dropping below its minimum flow (rule 4)."""
    return capacity_kW * 1e3 / (rho * cp * min_flow_m3s) if min_flow_m3s > 0 else float("inf")
