"""Full-physics hourly simulation of an arrangement.

Each hour, in order:
  1. DHW: work out the calorifier's recharge need and the generator target G. Preheat is
     known only after step 2; generators sourced from the heat-recovery circuit cannot be
     combined with preheat (validated).
  2. Cooling and heat recovery. Cooling units run in HR mode only while the heat-recovery
     circuit can absorb heat, taken in the configured priority order:
       space heating → DHW preheat → store → DHW heat-pump source → reject.
     The buffer store runs in sub-steps: direct use first, then charging, then discharge to
     LTHW and preheat. Cooling load not in HR mode is cooled in reject mode, to the loop.
  3. LTHW remainder: heat pumps on the loop, or the shared reversible fleet. Anything they
     cannot serve goes to electric backup (flagged).
  4. DHW generation: loop or heat-recovery source, cascade, CO₂ or electric. Circulation
     reheat. Electric immersion backup when the calorifier is empty (flagged).
  5. Pumps. The loop pump's flow comes from unit exchanges; its heat enters the loop in the
     same hour.
  6. Loop balance: net exchange (or gross, when reuse is off) goes to deficit plant in order
     (external, ground, ASHP, then electric backup) and surplus plant in order (dry cooler,
     tower, ground, ASHP cooling, then unmet).

Performance: every machine's COP comes from its model at that hour's leaving temperatures
(rules 3, 4), then a part-load multiplier from fleet sequencing (rule 10). For cooling,
EER_eff = COP_eff − 1 and condenser heat = Q_evap + W, so each machine closes exactly.

Every flow goes to the ledger. tests/ assert closure at every node and component in every
hour to 1e-6 kWh.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ambient_loop.core.provenance import Tag
from ambient_loop.core.series import HOURS, FloatArray, as_hourly
from ambient_loop.physics.fluids import get_fluid, pressure_drop_factor
from ambient_loop.physics.ground import BoreholeField
from ambient_loop.physics.library import build_model
from ambient_loop.physics.performance import Fleet
from ambient_loop.physics.pumps import Pump, flow_for_duty
from ambient_loop.physics.rejection import CoolingTower, DryCooler
from ambient_loop.physics.store import StratifiedStore
from ambient_loop.schema.arrangement import Arrangement, BalancingSpec, FleetSpec, PumpSpec
from ambient_loop.schema.project import Project, StreamKind
from ambient_loop.sim.ledger import ComponentInfo, Ledger, Role

LOOP, HR, DHWP, DINT, AMB, GROUND, EXT = "loop", "hr", "dhw_primary", "dhw_int", "ambient", "ground", "external"
RHO_CP_W = 4180.0 * 995.0  # J/m³K, water at ~30 °C for flow sizing where the fluid is water


@dataclass(frozen=True)
class FullInputs:
    demand_kW: dict[str, FloatArray]
    dry_bulb_C: FloatArray
    mains_C: FloatArray
    wet_bulb_C: FloatArray | None = None
    columns: dict[str, FloatArray] = field(default_factory=dict)


@dataclass
class FullResult:
    ledger: Ledger
    arrangement: Arrangement
    plr: dict[str, list[float]]  # lead-unit PLR in running hours, per fleet
    envelope_hours: dict[str, int]  # hours outside the model map/envelope while running
    flags: dict[str, int]  # named counts (hours)
    notes: list[str]
    peaks_kW: dict[str, float]
    pumps_kWh: dict[str, float]


def _fleet(spec: FleetSpec, name: str) -> Fleet:
    return Fleet(name, build_model(spec.model, name), spec.count, spec.unit_kW, spec.sequencing)


def _cop_eff(fl: Fleet, cond: float, cop: float, capf: float, units: float | None = None) -> tuple[float, float, float]:
    """Effective COP for a condenser load. Returns (cop_eff, unmet_cond, lead PLR)."""
    if cond <= 1e-3:  # below 1 W: treat as off
        return cop, 0.0, 0.0
    w, unmet, plr = fl.electricity(cond, cop, capf, units)
    served = cond - unmet
    # EN 14825 cycling floors electricity as CR → 0; keep COP_eff ≥ 1.5 so evaporator heat stays
    # positive and EER_eff = COP_eff − 1 stays finite [A-034]
    return (max(served / w, 1.5) if w > 0 else cop), unmet, plr


def loop_temperatures(arr: Arrangement, tdb: FloatArray) -> tuple[FloatArray, FloatArray]:
    lp = arr.loop
    assert lp is not None
    dT = lp.flow_C - lp.return_C
    if lp.mode == "fixed":
        f = np.full(HOURS, lp.flow_C)
    elif lp.mode == "outdoor_reset":
        xs, ys = zip(*sorted(lp.reset), strict=True)
        f = np.interp(tdb, xs, ys)
    else:
        f = as_hourly(lp.monthly_flow_C)
    return f, f - dT


def _pump(spec: PumpSpec, name: str, peak_kW: float, dT: float, fluid: str, t_mean: float) -> Pump:
    fl = get_fluid(fluid)
    water = get_fluid("water")
    flow = spec.design_flow_m3s or flow_for_duty(peak_kW, spec.dT_K or dT, fl.rho(t_mean), fl.cp(t_mean))
    gf = pressure_drop_factor(fl, water, t_mean, same_duty=False) if fluid != "water" else 1.0
    return Pump(name, flow, spec.head_kPa * 1e3 / (fl.rho(t_mean) * 9.81), spec.eta_pump,
                spec.eta_motor_drive, spec.control, spec.min_speed, glycol_factor=gf, rho=fl.rho(t_mean))


def run_full(project: Project, arr: Arrangement, inp: FullInputs, *, isolated: bool = False,
             ground_fluid_C: FloatArray | None = None) -> FullResult:
    H = HOURS
    notes: list[str] = []
    streams = project.streams
    lthw_ids = [s.id for s in streams if s.kind in (StreamKind.SPACE_HEATING, StreamKind.PROCESS_HEATING)]
    chw_ids = [s.id for s in streams if s.kind.is_cooling]
    dhw_ids = [s.id for s in streams if s.kind in (StreamKind.DHW, StreamKind.DHW_CIRCULATION)]
    circ_ids = [s.id for s in streams if s.kind is StreamKind.DHW_CIRCULATION]
    svc_of = {s.id: s.service_name for s in streams}
    dem = inp.demand_kW

    def total(ids: list[str]) -> FloatArray:
        return np.sum([dem[i] for i in ids], axis=0) if ids else np.zeros(H)

    qL, qC, qD = total(lthw_ids), total(chw_ids), total(dhw_ids)
    dhw = arr.dhw
    cf = dhw.circulation_fraction if dhw else 0.0
    qDc = total(circ_ids) + (qD - total(circ_ids)) * cf
    qDm = qD - qDc
    tdb = np.asarray(inp.dry_bulb_C, float)
    tm = np.asarray(inp.mains_C, float)

    has_loop = arr.loop is not None
    if has_loop:
        lf, lr = loop_temperatures(arr, tdb)
        loop_dT = arr.loop.flow_C - arr.loop.return_C  # type: ignore[union-attr]
    else:  # stand-alone plant: no ambient loop; nothing may draw on it
        lf, lr, loop_dT = np.full(H, 25.0), np.full(H, 20.0), 5.0
    cool_air = bool(arr.cooling and arr.cooling.sink == "air")
    heat_air = bool((arr.heating and arr.heating.source == "air") or
                    (arr.cooling and arr.cooling.shared_with_heating and cool_air))
    gen_air = bool(arr.dhw and arr.dhw.generator.type == "wshp" and arr.dhw.generator.source == "air")
    reheat_air = bool(arr.dhw and arr.dhw.reheat and arr.dhw.reheat.source == "air")
    if not has_loop:
        uses = [n for n, f in (("cooling (sink loop)", arr.cooling and not cool_air), ("LTHW heat pumps on loop", arr.heating and not heat_air),
                               ("DHW generator on loop", arr.dhw and arr.dhw.generator.type in ("wshp", "cascade") and not gen_air
                                and arr.dhw.generator.source == "loop"),
                               ("balancing plant", arr.deficit or arr.surplus)) if f]
        if uses:
            raise ValueError(f"{arr.id}: no loop, but these blocks need one: {uses}")

    # ---- fleets and COP arrays ----------------------------------------------------------
    env_masks: dict[str, np.ndarray] = {}
    chw_flow = arr.chw.flow_C if arr.chw else 7.0
    lthw_flow = arr.lthw.flow_C if arr.lthw else 45.0
    lthw_ret = arr.lthw.return_C if arr.lthw else 40.0

    cool = _fleet(arr.cooling, "cooling") if arr.cooling else None
    hr_on = bool(arr.cooling and arr.cooling.hr_target != "none" and not isolated)
    hr_flow = (arr.hr.flow_C if arr.hr else lthw_flow) if arr.cooling else 45.0
    hr_ret = (arr.hr.return_C if arr.hr else lthw_ret)
    if qC.sum() > 0 and cool is None:
        raise ValueError(f"{arr.id}: cooling load but no cooling fleet")
    if cool:
        snk_rej = tdb if cool_air else lf
        cop_rej, m1 = cool.model.cop_h(np.full(H, chw_flow), snk_rej)
        cap_rej = cool.model.capacity_factor(np.full(H, chw_flow), snk_rej)
        hr_m = build_model(arr.cooling.hr_model, "cooling_hr") if arr.cooling and arr.cooling.hr_model else cool.model
        cop_hr, m2 = hr_m.cop_h(np.full(H, chw_flow), np.full(H, hr_flow))
        cap_hr = cool.model.capacity_factor(np.full(H, chw_flow), np.full(H, hr_flow))
        env_masks["cooling:reject"] = m1
        env_masks["cooling:hr"] = m2
    heat = None
    if qL.sum() > 0:
        if arr.cooling and arr.cooling.shared_with_heating:
            heat = cool
        elif arr.heating:
            heat = _fleet(arr.heating, "lthw_hp")
        else:
            notes.append("No LTHW plant: space heating falls to electric backup.")
    if heat:
        src_L = tdb if heat_air else lr
        cop_L, mL = heat.model.cop_h(src_L, np.full(H, lthw_flow))
        cap_L = heat.model.capacity_factor(src_L, np.full(H, lthw_flow))
        env_masks["lthw_hp"] = mL

    gen = gen1 = reheat = None
    if dhw:
        gflow = dhw.generator_flow_C or dhw.store_C
        g = dhw.generator
        gen = _fleet(g, "dhw_gen")
        if g.type == "wshp":
            src_D = tdb if gen_air else lr
            cop_Dl, mD = gen.model.cop_h(src_D, np.full(H, gflow))
            cap_Dl = gen.model.capacity_factor(src_D, np.full(H, gflow))
            env_masks["dhw_gen:loop"] = mD
            if g.source in ("hr_changeover", "hr_dedicated"):
                gh_m = build_model(g.hr_model, "dhw_hr") if g.hr_model else gen.model
                cop_Dh, mDh = gh_m.cop_h(np.full(H, hr_ret), np.full(H, gflow))
                cap_Dh = gen.model.capacity_factor(np.full(H, hr_ret), np.full(H, gflow))
                env_masks["dhw_gen:hr"] = mDh
        elif g.type == "cascade":
            assert g.stage1 is not None
            gen1 = _fleet(g.stage1, "dhw_stage1")
            s2_m = build_model(g.hr_model, "dhw_stage2") if g.hr_model else gen.model
            cop_D2, mD2 = s2_m.cop_h(np.full(H, g.intermediate_C - 5.0), np.full(H, gflow))
            cap_D2 = gen.model.capacity_factor(np.full(H, g.intermediate_C - 5.0), np.full(H, gflow))
            cop_D1, mD1 = gen1.model.cop_h(lr, np.full(H, g.intermediate_C))
            cap_D1 = gen1.model.capacity_factor(lr, np.full(H, g.intermediate_C))
            env_masks["dhw_stage2"] = mD2
            env_masks["dhw_stage1"] = mD1
        elif g.type == "co2":
            src = tdb if g.source == "air" else lr
            # COP with mains inlet and with fully preheated inlet; blended hourly by preheat outlet
            cop_co2_m, mc = gen.model.cop_h(src, np.full(H, dhw.store_C), tm)
            pre_out = np.full(H, dhw.preheat.max_C) if dhw.preheat and dhw.preheat.enabled else tm
            cop_co2_p, _ = gen.model.cop_h(src, np.full(H, dhw.store_C), pre_out)
            cop_co2_c, _ = gen.model.cop_h(src, np.full(H, dhw.store_C), np.full(H, dhw.circulation_return_C))
            env_masks["dhw_gen:co2"] = mc
        if dhw.reheat:
            reheat = _fleet(dhw.reheat, "dhw_reheat")
            cop_R, mR = reheat.model.cop_h(tdb if reheat_air else lr, np.full(H, dhw.store_C + 5.0))
            env_masks["dhw_reheat"] = mR

    # balancing plant
    bal_def: list[tuple[BalancingSpec, object, FloatArray | None, FloatArray | None]] = []
    bal_sur: list[tuple[BalancingSpec, object, FloatArray | None, FloatArray | None]] = []
    gfield: BoreholeField | None = None
    for b in arr.deficit + arr.surplus:
        if b.type == "ground" and gfield is None:
            gfield = BoreholeField(**b.ground)
    t_gf = ground_fluid_C if ground_fluid_C is not None else (
        np.full(H, gfield.t_ground_C) if gfield else None)
    for b in arr.deficit:
        if b.type == "ashp":
            assert b.fleet
            fl = _fleet(b.fleet, "ashp")
            c, m = fl.model.cop_h(tdb, lf)
            env_masks["ashp"] = m
            bal_def.append((b, fl, c, fl.model.capacity_factor(tdb, lf)))
        else:
            bal_def.append((b, None, None, None))
    for b in arr.surplus:
        if isolated and b.type == "dry_cooler":
            continue
        if b.type == "ashp_cooling":
            assert b.fleet
            fl = _fleet(b.fleet, "ashp_cooling")
            c, m = fl.model.cop_h(lr, tdb)
            env_masks["ashp_cooling"] = m
            bal_sur.append((b, fl, c, fl.model.capacity_factor(lr, tdb)))
        elif b.type == "dry_cooler":
            assert b.rated_kW
            bal_sur.append((b, DryCooler(b.rated_kW, b.rated_itd_K, b.rated_fan_kW, b.approach_min_K,
                                         simple_cutoff_C=b.simple_cutoff_C, simple_kW_per_kW=b.simple_kW_per_kW), None, None))
        elif b.type == "tower":
            if inp.wet_bulb_C is None:
                notes.append("Cooling tower skipped: no wet-bulb temperature in the weather data.")
                continue
            assert b.rated_kW
            bal_sur.append((b, CoolingTower(b.rated_kW, fan_kW_per_kW=b.rated_fan_kW / b.rated_kW if b.rated_fan_kW else 0.01), None, None))
        else:
            bal_sur.append((b, None, None, None))

    # stores
    use_buf = arr.buffer is not None and not isolated and hr_on
    buf = None
    if use_buf:
        bs = arr.buffer
        assert bs is not None
        buf = StratifiedStore(bs.volume_m3, bs.nodes, t_init_C=bs.t_return_C, ua_W_K=bs.ua_W_K, t_amb_C=bs.t_amb_C)
    cal_max = 0.0
    if dhw and dhw.calorifier_m3 > 0:
        cal_max = dhw.calorifier_usable_fraction * dhw.calorifier_m3 * RHO_CP_W * (dhw.store_C - float(np.mean(tm))) / 3.6e6
    cal_E = cal_max
    cal_loss_kWh = (dhw.calorifier_ua_W_K * (dhw.store_C - 20.0) / 1000.0) if dhw and cal_max > 0 else 0.0

    # pumps
    peak_loop = float(np.max(qL + qC * 1.2 + qD))
    pumps: dict[str, Pump] = {}
    if arr.pumps_included:
        if has_loop:
            pumps["loop"] = _pump(arr.loop.pump, "loop", peak_loop, loop_dT, arr.loop.fluid, float(np.mean(lr)))  # type: ignore[union-attr]
        if arr.chw and qC.max() > 0:
            pumps["chw"] = _pump(arr.chw.pump, "chw", float(qC.max()), arr.chw.return_C - arr.chw.flow_C, arr.chw.fluid, chw_flow)
        if arr.lthw and qL.max() > 0:
            pumps["lthw"] = _pump(arr.lthw.pump, "lthw", float(qL.max()), lthw_flow - lthw_ret, arr.lthw.fluid, lthw_flow)
        if hr_on and arr.hr:
            pumps["hr"] = _pump(arr.hr.pump, "hr", float(qC.max()) * 1.25, arr.hr.flow_C - (arr.buffer.t_return_C if arr.buffer else arr.hr.return_C), "water", hr_flow)
        if dhw:
            pumps["dhw_primary"] = _pump(dhw.pump, "dhw_primary", float(qD.max()), 10.0, "water", dhw.store_C)
    hr_dT = (hr_flow - (arr.buffer.t_return_C if arr.buffer else hr_ret)) if arr.cooling else 5.0
    lfluid = arr.loop.fluid if arr.loop else "water"
    rho_cp_loop = get_fluid(lfluid).rho(20.0) * get_fluid(lfluid).cp(20.0)

    # ---- output arrays --------------------------------------------------------------------
    Z = lambda: np.zeros(H)  # noqa: E731
    o = {k: Z() for k in (
        "C_served", "C_unmet", "W_cool", "cond_hr", "cond_rej", "hr_L", "hr_P", "buf_ch", "buf_dis", "buf_loss",
        "buf_dU", "evap_L", "L_hp", "W_L", "L_backup", "G", "W_gen", "evap_gen_loop", "evap_gen_hr", "evap_gen_air",
        "W_s1", "evap_s1", "int_flow", "R", "W_R", "evap_R", "imm", "cal_in", "cal_out", "cal_loss", "cal_dU",
        "ext_supply", "ground_supply", "ashp_d", "W_ashp", "loop_backup", "dc_r", "W_dc", "tw_r", "W_tw",
        "ground_rej", "ac_r", "W_ac", "unmet_rej", "W_pump_loop", "W_pump_other", "W_bal_pumps",
        "cond_air", "evap_L_air", "evap_R_air", "W_pump_dist", "W_cool_hr",
        "hr_to_dhw_share")}
    plr: dict[str, list[float]] = {}
    run_mask: dict[str, np.ndarray] = {}

    def rec(name: str, v: float, t: int) -> None:
        if v > 0:
            plr.setdefault(name, []).append(v)
            run_mask.setdefault(name, np.zeros(H, bool))[t] = True

    pre = dhw.preheat if (dhw and dhw.preheat and dhw.preheat.enabled and not isolated and hr_on) else None
    prio = arr.hr.priority if arr.hr else ["space_heating", "reject"]
    nsub = arr.buffer.substeps if use_buf and arr.buffer else 1
    gtype = dhw.generator.type if dhw else None
    gsrc = dhw.generator.source if dhw else None
    hr_dhw_src = hr_on and gtype == "wshp" and gsrc in ("hr_changeover", "hr_dedicated")
    if pre is not None and hr_dhw_src:
        raise ValueError(f"{arr.id}: DHW preheat cannot be combined with a heat-recovery-sourced DHW heat pump")
    ext_col = {}
    for b in arr.deficit:
        if b.type == "external" and b.external_column:
            ext_col[id(b)] = inp.columns.get(b.external_column, np.zeros(H))

    for t in range(H):
        L, C, Dm, Dc = float(qL[t]), float(qC[t]), float(qDm[t]), float(qDc[t])
        tmt = float(tm[t])
        # ---------- 1. DHW target ----------
        draw_gen = 0.0
        if dhw:
            draw_gen = Dm + (Dc if dhw.reheat is None else 0.0)  # before preheat
        # ---------- 2. cooling and heat recovery ----------
        cop_hr_eff = 0.0
        dhw_src_evap_demand = 0.0
        hr_used = 0.0
        pre_done = 0.0
        L_from_hr = 0.0
        evap_hr_dhw = 0.0
        dhw_src_cond_demand = 0.0
        if cool and C > 0:
            if hr_on:
                ce, _, p_ = _cop_eff(cool, C * cop_hr[t] / (cop_hr[t] - 1), float(cop_hr[t]), float(cap_hr[t]))
                cop_hr_eff = ce
            offer_total = C * cop_hr_eff / (cop_hr_eff - 1) if hr_on else 0.0
            if hr_on:
                # DHW heat-pump source demand (cond output that could come from HR source)
                if hr_dhw_src and dhw:
                    G_pre = draw_gen + cal_loss_kWh + (cal_max - cal_E)
                    gcap = gen.count * gen.unit_capacity_kW * float(cap_Dh[t])  # type: ignore[union-attr]
                    if gsrc == "hr_dedicated":
                        gcap = dhw.generator.hr_units * gen.unit_capacity_kW * float(cap_Dh[t])  # type: ignore[union-attr]
                    dhw_src_cond_demand = min(G_pre, gcap)
                    ce_d, _, _ = _cop_eff(gen, dhw_src_cond_demand, float(cop_Dh[t]), float(cap_Dh[t]))  # type: ignore[arg-type]
                    dhw_src_evap_demand = dhw_src_cond_demand * (1 - 1 / ce_d)
                else:
                    dhw_src_evap_demand = 0.0
                def pre_frac(t_source: float, tmt: float = tmt) -> float:
                    t_out = min(t_source - pre.approach_K, pre.max_C)  # type: ignore[union-attr]
                    return max(0.0, (t_out - tmt) / max(dhw.store_C - tmt, 1.0))  # type: ignore[union-attr]

                for _s in range(nsub):
                    offer = offer_total / nsub
                    Ls_rem = L / nsub
                    pre_sub = 0.0
                    for sink in prio:
                        if offer <= 1e-12:
                            break
                        if sink == "space_heating" and hr_flow >= lthw_flow - 1e-9:
                            take = min(offer, Ls_rem)
                            Ls_rem -= take
                            L_from_hr += take
                            offer -= take
                        elif sink == "dhw_preheat" and pre is not None:
                            take = min(offer, max(Dm / nsub * pre_frac(hr_flow) - pre_sub, 0.0))
                            pre_sub += take
                            offer -= take
                        elif sink == "store" and buf is not None:
                            acc = buf.charge(offer, hr_flow, approach_K=2.0)
                            o["buf_ch"][t] += acc
                            offer -= acc
                        elif sink == "dhw_hp_source" and hr_dhw_src:
                            take = min(offer, dhw_src_evap_demand / nsub)
                            evap_hr_dhw += take
                            offer -= take
                    hr_used += offer_total / nsub - offer
                    if buf is not None:  # stored heat to whatever is still unmet this sub-step
                        if Ls_rem > 1e-9:
                            d = buf.discharge(Ls_rem, lthw_ret, lthw_flow)
                            o["buf_dis"][t] += d
                            L_from_hr += d
                            Ls_rem -= d
                        if pre is not None:
                            want = Dm / nsub * pre_frac(buf.top) - pre_sub
                            if want > 1e-9:
                                d = buf.discharge(want, tmt + pre.approach_K, tmt + pre.approach_K + 2.0)
                                o["buf_dis"][t] += d
                                pre_sub += d
                    pre_done += pre_sub
                if buf is not None:
                    o["buf_loss"][t] = buf.lose(1.0)
            # mode split
            cond_hr = hr_used  # heat delivered into the HR circuit by condensers
            evap_hr = cond_hr * (cop_hr_eff - 1) / cop_hr_eff if cond_hr > 0 else 0.0
            evap_rej = max(C - evap_hr, 0.0)
            W_hr = cond_hr - evap_hr
            cond_rej = 0.0
            W_rej = 0.0
            unmet_c = 0.0
            if evap_rej > 1e-12:
                cond_est = evap_rej * cop_rej[t] / (cop_rej[t] - 1)
                units_av = cool.count - (cond_hr / (cool.unit_capacity_kW * float(cap_hr[t])) if cond_hr > 0 else 0.0)
                cop_rj, un, p_ = _cop_eff(cool, cond_est, float(cop_rej[t]), float(cap_rej[t]), max(units_av, 0.0))
                if un > 1e-9:
                    unmet_c = un * (cop_rj - 1) / cop_rj
                served_e = evap_rej - unmet_c
                W_rej = served_e / (cop_rj - 1)
                cond_rej = served_e + W_rej
                rec("cooling", p_, t)
                used_units = (cond_hr + cond_rej) / (cool.unit_capacity_kW * float(cap_rej[t]))
            else:
                used_units = cond_hr / (cool.unit_capacity_kW * float(cap_hr[t])) if cond_hr > 0 else 0.0
                rec("cooling", min(cond_hr / (cool.unit_capacity_kW * float(cap_hr[t])), 1.0) if cond_hr > 0 else 0, t)
            o["C_served"][t] = C - unmet_c
            o["C_unmet"][t] = unmet_c
            o["W_cool"][t] = W_hr + W_rej
            o["W_cool_hr"][t] = W_hr
            o["cond_hr"][t] = cond_hr
            o["cond_rej"][t] = cond_rej
        else:
            used_units = 0.0
            if buf is not None:
                # no cooling: buffer can still serve LTHW / preheat from stored heat
                Ls_rem = L
                if L > 0:
                    d = buf.discharge(L, lthw_ret, lthw_flow)
                    o["buf_dis"][t] += d
                    L_from_hr += d
                if pre is not None and dhw:
                    t_av = min(buf.top - pre.approach_K, pre.max_C)
                    want = Dm * max(0.0, (t_av - tmt) / max(dhw.store_C - tmt, 1.0))
                    if want > 1e-9:
                        d = buf.discharge(want, tmt + pre.approach_K, tmt + pre.approach_K + 2.0)
                        o["buf_dis"][t] += d
                        pre_done += d
                o["buf_loss"][t] = buf.lose(1.0)
        o["hr_L"][t] = L_from_hr
        o["hr_P"][t] = pre_done
        o["evap_gen_hr"][t] = evap_hr_dhw

        # ---------- 3. LTHW remainder ----------
        L_rem = max(L - L_from_hr, 0.0)
        if L_rem > 1e-3:
            if heat is not None:
                units = (heat.count - used_units) if (arr.cooling and arr.cooling.shared_with_heating) else None
                ce, un, p_ = _cop_eff(heat, L_rem, float(cop_L[t]), float(cap_L[t]), units)
                served = L_rem - un
                o["L_hp"][t] = served
                o["W_L"][t] = served / ce if served > 0 else 0.0
                o["evap_L"][t] = served - o["W_L"][t]
                o["L_backup"][t] = un
                rec("lthw_hp", p_, t)
            else:
                o["L_backup"][t] = L_rem

        # ---------- 4. DHW generation ----------
        if dhw:
            net_draw = max(draw_gen - pre_done, 0.0)
            G_need = net_draw + cal_loss_kWh + (cal_max - cal_E)
            if gtype == "wshp":
                cap_all = gen.count * gen.unit_capacity_kW * float(cap_Dl[t])  # type: ignore[union-attr]
                G = min(G_need, cap_all) if cal_max > 0 else min(net_draw, cap_all)
                G_hr = 0.0
                if hr_dhw_src and evap_hr_dhw > 0:
                    ce_h, _, p_h = _cop_eff(gen, dhw_src_cond_demand, float(cop_Dh[t]), float(cap_Dh[t]))  # type: ignore[arg-type]
                    G_hr = min(evap_hr_dhw / (1 - 1 / ce_h), G)
                    # evap actually drawn from HR at that output
                    evap_used = G_hr * (1 - 1 / ce_h)
                    spill = evap_hr_dhw - evap_used
                    if spill > 1e-9:  # unusable HR heat: cooling switches that share to reject
                        o["evap_gen_hr"][t] = evap_used
                        _shift_hr_to_reject(o, t, spill, cop_hr_eff, cop_rej[t])
                    W_h = G_hr / ce_h
                    rec("dhw_gen", p_h, t)
                else:
                    W_h = 0.0
                G_loop = G - G_hr
                units_left = None
                if gsrc == "hr_dedicated":
                    units_left = gen.count - dhw.generator.hr_units  # type: ignore[union-attr]
                    G_loop = min(G_loop, units_left * gen.unit_capacity_kW * float(cap_Dl[t]))  # type: ignore[union-attr]
                    G = G_hr + G_loop
                ce_l, un, p_l = _cop_eff(gen, G_loop, float(cop_Dl[t]), float(cap_Dl[t]), units_left)  # type: ignore[arg-type]
                W_l = G_loop / ce_l if G_loop > 0 else 0.0
                o["G"][t] = G
                o["W_gen"][t] = W_h + W_l
                o["evap_gen_loop"][t] = G_loop - W_l
                rec("dhw_gen", p_l, t)
            elif gtype == "cascade":
                cap2 = gen.count * gen.unit_capacity_kW * float(cap_D2[t])  # type: ignore[union-attr]
                G = min(G_need if cal_max > 0 else net_draw, cap2)
                ce2, _, p2 = _cop_eff(gen, G, float(cop_D2[t]), float(cap_D2[t]))  # type: ignore[arg-type]
                W2 = G / ce2 if G > 0 else 0.0
                e2 = G - W2
                ce1, un1, p1 = _cop_eff(gen1, e2, float(cop_D1[t]), float(cap_D1[t]))  # type: ignore[arg-type]
                W1 = e2 / ce1 if e2 > 0 else 0.0
                o["G"][t] = G
                o["W_gen"][t] = W2
                o["int_flow"][t] = e2
                o["W_s1"][t] = W1
                o["evap_s1"][t] = e2 - W1
                rec("dhw_gen", p2, t)
                rec("dhw_stage1", p1, t)
            elif gtype == "co2":
                cap_all = gen.count * gen.unit_capacity_kW  # type: ignore[union-attr]
                G = min(G_need if cal_max > 0 else net_draw, cap_all)
                # blended COP: share of make-up preheated vs not, and circulation share
                mk = max(Dm - pre_done, 0.0)
                circ_part = Dc if dhw.reheat is None else 0.0
                fpre = (pre_done / Dm) if Dm > 0 else 0.0
                cop_mk = float(cop_co2_p[t]) if fpre > 0.05 else float(cop_co2_m[t])
                tot = mk + circ_part
                cop_bl = (tot / (mk / cop_mk + circ_part / float(cop_co2_c[t]))) if tot > 0 else cop_mk
                ce, _, p_ = _cop_eff(gen, G, cop_bl, 1.0)  # type: ignore[arg-type]
                Wc = G / ce if G > 0 else 0.0
                o["G"][t] = G
                o["W_gen"][t] = Wc
                if gsrc == "air":
                    o["evap_gen_air"][t] = G - Wc
                else:
                    o["evap_gen_loop"][t] = G - Wc
                rec("dhw_gen", p_, t)
            else:  # electric
                G = G_need if cal_max > 0 else net_draw
                o["G"][t] = G
                o["W_gen"][t] = G
            # circulation reheat
            if dhw.reheat and Dc > 0 and reheat is not None:
                ce_r, unr, p_r = _cop_eff(reheat, Dc, float(cop_R[t]), 1.0)
                served = Dc - unr
                o["R"][t] = served
                o["W_R"][t] = served / ce_r
                o["evap_R"][t] = served - served / ce_r if dhw.reheat.model.library != "electric" else 0.0
                if dhw.reheat.model.library == "electric":
                    o["W_R"][t] = served
                o["imm"][t] += unr
                rec("dhw_reheat", p_r, t)
            # calorifier balance
            inflow = o["G"][t] + pre_done + o["R"][t]
            out = Dm + Dc
            if cal_max > 0:
                E_new = cal_E + inflow - out - cal_loss_kWh
                if E_new < 0:
                    o["imm"][t] += -E_new
                    E_new = 0.0
                if E_new > cal_max:  # cannot over-fill: excess generation not taken
                    E_new = cal_max
                o["cal_dU"][t] = E_new - cal_E
                o["cal_loss"][t] = cal_loss_kWh
                cal_E = E_new
            else:
                short = out - inflow
                if short > 1e-9:
                    o["imm"][t] += short
            o["cal_in"][t] = inflow + o["imm"][t]
            o["cal_out"][t] = out

        # ---------- 5/6. loop balance ----------
        if gen_air:  # air-source DHW heat pump: evaporator heat from outdoor air
            o["evap_gen_air"][t] += o["evap_gen_loop"][t]
            o["evap_gen_loop"][t] = 0.0
        if heat_air:
            o["evap_L_air"][t], o["evap_L"][t] = o["evap_L"][t], 0.0
        if reheat_air:
            o["evap_R_air"][t], o["evap_R"][t] = o["evap_R"][t], 0.0
        if cool_air:
            o["cond_air"][t], o["cond_rej"][t] = o["cond_rej"][t], 0.0
        ext = o["evap_L"][t] + o["evap_gen_loop"][t] + o["evap_s1"][t] + o["evap_R"][t]
        rej = o["cond_rej"][t]
        if "loop" in pumps:
            fl_loop = (ext + rej) * 1e3 / (rho_cp_loop * loop_dT)
            wp = pumps["loop"].power_kW(fl_loop)
            o["W_pump_loop"][t] = wp
            rej += wp
        if arr.reuse and not isolated:
            net = rej - ext
            deficit, surplus = max(-net, 0.0), max(net, 0.0)
        else:
            deficit, surplus = ext, rej
        for b, fl, c, capf in bal_def:
            if deficit <= 1e-12:
                break
            if b.type == "external":
                avail = float(ext_col.get(id(b), np.full(H, b.rated_kW or 0.0))[t]) if b.external_column else (b.rated_kW or 0.0)
                if b.external_T_C - b.approach_min_K < lr[t]:
                    avail = 0.0
                d = min(deficit, avail)
                o["ext_supply"][t] += d
                o["W_bal_pumps"][t] += d * b.pump_kW_per_kW
                deficit -= d
            elif b.type == "ground" and gfield is not None and t_gf is not None:
                if t_gf[t] - b.approach_min_K >= lr[t]:
                    d = min(deficit, gfield.capacity_kW())
                    o["ground_supply"][t] += d
                    o["W_bal_pumps"][t] += d * b.pump_kW_per_kW
                    deficit -= d
            elif b.type == "ashp" and isinstance(fl, Fleet):
                assert c is not None and capf is not None
                ce, un, p_ = _cop_eff(fl, deficit, float(c[t]), float(capf[t]))
                d = deficit - un
                o["ashp_d"][t] += d
                o["W_ashp"][t] += d / ce if d > 0 else 0.0
                o["W_bal_pumps"][t] += d * b.pump_kW_per_kW
                rec("ashp", p_, t)
                deficit -= d
        if deficit > 1e-12:
            o["loop_backup"][t] = deficit
        for b, dev, c, capf in bal_sur:
            if surplus <= 1e-12:
                break
            if b.type == "dry_cooler" and isinstance(dev, DryCooler):
                av = dev.available_kW(float(lf[t]), float(tdb[t]))
                r = min(surplus, av)
                if r > 0:
                    o["dc_r"][t] += r
                    o["W_dc"][t] += dev.fan_kW(r, av) + r * b.pump_kW_per_kW
                    surplus -= r
            elif b.type == "tower" and isinstance(dev, CoolingTower) and inp.wet_bulb_C is not None:
                av = dev.available_kW(float(lf[t]), float(inp.wet_bulb_C[t]))
                r = min(surplus, av)
                if r > 0:
                    o["tw_r"][t] += r
                    o["W_tw"][t] += dev.fan_kW(r, av) + r * b.pump_kW_per_kW
                    surplus -= r
            elif b.type == "ground" and gfield is not None and t_gf is not None:
                if t_gf[t] + b.approach_min_K <= lf[t]:
                    r = min(surplus, gfield.capacity_kW())
                    o["ground_rej"][t] += r
                    o["W_bal_pumps"][t] += r * b.pump_kW_per_kW
                    surplus -= r
            elif b.type == "ashp_cooling" and isinstance(dev, Fleet):
                assert c is not None and capf is not None
                cond_est = surplus * c[t] / (c[t] - 1)
                ce, un, p_ = _cop_eff(dev, cond_est, float(c[t]), float(capf[t]))
                r = surplus - (un * (ce - 1) / ce if un > 0 else 0.0)
                o["ac_r"][t] += r
                o["W_ac"][t] += r / (ce - 1)
                o["W_bal_pumps"][t] += r * b.pump_kW_per_kW
                rec("ashp_cooling", p_, t)
                surplus -= r
        o["unmet_rej"][t] = max(surplus, 0.0)

        # distribution pumps (LTHW/CHW secondary; SBEM counts these as auxiliary energy) and
        # generation-side pumps (heat recovery, DHW primary, balancing); heat to ambient
        wd = 0.0
        if "chw" in pumps:
            wd += pumps["chw"].power_kW(flow_for_duty(C, arr.chw.return_C - arr.chw.flow_C, 1000, 4190))  # type: ignore[union-attr]
        if "lthw" in pumps:
            wd += pumps["lthw"].power_kW(flow_for_duty(L, lthw_flow - lthw_ret, 990, 4180))
        o["W_pump_dist"][t] = wd
        wo = 0.0
        if "hr" in pumps:
            hr_heat = o["cond_hr"][t] + o["buf_dis"][t]
            wo += pumps["hr"].power_kW(flow_for_duty(hr_heat, hr_dT, 990, 4180))
        if "dhw_primary" in pumps:
            wo += pumps["dhw_primary"].power_kW(flow_for_duty(o["G"][t] + o["R"][t], 10.0, 985, 4185))
        o["W_pump_other"][t] = wo
        hr_tot = o["hr_L"][t] + o["hr_P"][t] + o["evap_gen_hr"][t]
        o["hr_to_dhw_share"][t] = (o["hr_P"][t] + o["evap_gen_hr"][t]) / hr_tot if hr_tot > 0 else 0.0
        if buf is not None:
            o["buf_dU"][t] = o["buf_ch"][t] - o["buf_dis"][t] - o["buf_loss"][t]

    # ---- ledger ---------------------------------------------------------------------------
    led = _build_ledger(project, arr, o, qL, qC, qDm, qDc, lthw_ids, chw_ids, dhw_ids, svc_of, dem, gtype, gsrc, use_buf, cal_max > 0)
    led.assert_closed()

    # envelope hours while running
    env_hours = {}
    for k, m in env_masks.items():
        base = k.split(":")[0]
        rm = run_mask.get(base, np.zeros(H, bool))
        env_hours[k] = int((m & rm).sum())
        if env_hours[k]:
            for cid, info in led.components.items():
                if cid == base:
                    info.tags.add(Tag.EXTRAPOLATED)
    flag_counts = {
        "LTHW electric backup hours": int((o["L_backup"] > 1e-6).sum()),
        "DHW immersion hours": int((o["imm"] > 1e-6).sum()),
        "Loop electric backup hours": int((o["loop_backup"] > 1e-6).sum()),
        "Unmet rejection hours": int((o["unmet_rej"] > 1e-6).sum()),
        "Unmet cooling hours": int((o["C_unmet"] > 1e-6).sum()),
        "Heat-recovery hours": int((o["cond_hr"] > 1e-6).sum()),
        "Dry cooler running hours": int((o["dc_r"] > 1e-6).sum()),
    }
    peaks = {"LTHW": float(qL.max()), "CHW": float(qC.max()), "DHW": float(qD.max()),
             "ASHP to loop": float(o["ashp_d"].max()), "Loop rejection": float((o["dc_r"] + o["ac_r"] + o["tw_r"] + o["ground_rej"]).max())}
    pumps_kWh = {"loop": float(o["W_pump_loop"].sum()), "distribution (LTHW/CHW)": float(o["W_pump_dist"].sum()),
                 "other circuits": float(o["W_pump_other"].sum()),
                 "listed balancing pumps": float(o["W_bal_pumps"].sum())}
    for k, arr_ in o.items():
        led.derived[f"o:{k}"] = arr_
    led.derived["T_loop_flow"] = lf
    led.derived["T_loop_return"] = lr
    return FullResult(led, arr, plr, env_hours, flag_counts, notes, peaks, pumps_kWh)


def _shift_hr_to_reject(o: dict[str, FloatArray], t: int, spill_evap_heat: float, cop_hr: float, cop_rej: float) -> None:
    """HR heat that the DHW HP could not use: move that cooling share to reject mode."""
    cond_spill = spill_evap_heat
    evap_spill = cond_spill * (cop_hr - 1) / cop_hr
    o["cond_hr"][t] -= cond_spill
    o["W_cool"][t] -= cond_spill - evap_spill
    o["W_cool_hr"][t] -= cond_spill - evap_spill
    w_rej = evap_spill / (cop_rej - 1)
    o["W_cool"][t] += w_rej
    o["cond_rej"][t] += evap_spill + w_rej


def _split(total: FloatArray, ids: list[str], dem: dict[str, FloatArray]) -> dict[str, FloatArray]:
    if not ids:
        return {}
    tot = np.sum([dem[i] for i in ids], axis=0)
    return {i: np.divide(total * dem[i], tot, out=np.zeros(HOURS), where=tot > 0) for i in ids}


def _build_ledger(project, arr, o, qL, qC, qDm, qDc, lthw_ids, chw_ids, dhw_ids, svc_of, dem, gtype, gsrc, use_buf, has_cal) -> Ledger:  # type: ignore[no-untyped-def]
    nodes = {LOOP, HR, DHWP}
    if gtype == "cascade":
        nodes.add(DINT)
    led = Ledger(internal_nodes=nodes)

    def comp(cid: str, kind: str, role: Role, weights: dict[str, FloatArray] | None = None,
             tags: set[Tag] | None = None) -> None:
        led.add_component(ComponentInfo(cid, kind, role, tags or set(), service_weights=weights))

    one = np.ones(HOURS)
    svc_set = sorted({svc_of[s.id] for s in project.streams})
    lthw_svc = {svc_of[i] for i in lthw_ids}
    dhw_svc = {svc_of[i] for i in dhw_ids}
    chw_svc = {svc_of[i] for i in chw_ids}

    def w_of(svcs: set[str]) -> dict[str, FloatArray] | None:
        return {s: one for s in svcs} if svcs else {s: one for s in svc_set}

    # services
    lthw_split = lambda x: _split(x, lthw_ids, dem)  # noqa: E731
    dhw_split = lambda x: _split(x, dhw_ids, dem)  # noqa: E731
    chw_split = lambda x: _split(x, chw_ids, dem)  # noqa: E731

    if arr.cooling and qC.sum() > 0:
        comp("cooling", "cooling_fleet", Role.UNIT, w_of(chw_svc), {Tag.GENERIC})
        for sid, v in chw_split(o["C_served"]).items():
            led.add_flow("cooling", f"svc:{sid}", -v)
        led.add_flow("cooling", HR, o["cond_hr"])
        led.add_flow("cooling", LOOP, o["cond_rej"])
        if o["cond_air"].sum() > 0:
            led.add_flow("cooling", AMB, o["cond_air"])
        led.add_elec("cooling", o["W_cool"])
        for sid, v in chw_split(o["cond_rej"]).items():
            led.derived[f"rejection:{sid}"] = v
    if o["C_unmet"].sum() > 0:
        comp("unmet_cooling", "unmet", Role.UNMET)
        for sid, v in chw_split(o["C_unmet"]).items():
            led.add_flow("unmet_cooling", f"svc:{sid}", -v)
    # heat-recovery consumers
    if o["hr_L"].sum() > 0:
        comp("hr_lthw_hx", "hx", Role.UNIT, w_of(lthw_svc))
        led.add_flow("hr_lthw_hx", HR, -o["hr_L"] + 0.0)
        for sid, v in lthw_split(o["hr_L"]).items():
            led.add_flow("hr_lthw_hx", f"svc:{sid}", v)
    if o["hr_P"].sum() > 0:
        comp("preheat_hx", "hx", Role.UNIT, w_of(dhw_svc))
        led.add_flow("preheat_hx", HR, -o["hr_P"])
        led.add_flow("preheat_hx", DHWP, o["hr_P"])
    if use_buf:
        comp("buffer", "store", Role.UNIT, w_of(lthw_svc | dhw_svc))
        led.add_flow("buffer", HR, -o["buf_ch"] + o["buf_dis"])
        led.add_flow("buffer", AMB, o["buf_loss"])
        led.stored["buffer"] = o["buf_dU"]
    # LTHW plant
    if o["L_hp"].sum() > 0:
        comp("lthw_hp", "heat_pump_fleet", Role.UNIT, w_of(lthw_svc), {Tag.GENERIC})
        led.add_flow("lthw_hp", LOOP, -o["evap_L"])
        if o["evap_L_air"].sum() > 0:
            led.add_flow("lthw_hp", AMB, -o["evap_L_air"])
        for sid, v in lthw_split(o["L_hp"]).items():
            led.add_flow("lthw_hp", f"svc:{sid}", v)
        led.add_elec("lthw_hp", o["W_L"])
        for sid, v in lthw_split(o["evap_L"]).items():
            led.derived[f"extraction:{sid}"] = led.derived.get(f"extraction:{sid}", 0) + v
    if o["L_backup"].sum() > 0:
        comp("lthw_backup", "electric", Role.UNIT, w_of(lthw_svc))
        for sid, v in lthw_split(o["L_backup"]).items():
            led.add_flow("lthw_backup", f"svc:{sid}", v)
        led.add_elec("lthw_backup", o["L_backup"])
    # DHW plant
    if arr.dhw:
        comp("dhw_gen", f"dhw_{gtype}", Role.UNIT, w_of(dhw_svc), {Tag.GENERIC})
        if gtype == "cascade":
            led.add_flow("dhw_gen", DINT, -o["int_flow"])
            comp("dhw_stage1", "heat_pump_fleet", Role.UNIT, w_of(dhw_svc), {Tag.GENERIC})
            led.add_flow("dhw_stage1", LOOP, -o["evap_s1"])
            led.add_flow("dhw_stage1", DINT, o["int_flow"])
            led.add_elec("dhw_stage1", o["W_s1"])
        elif gtype == "electric":
            pass
        else:
            led.add_flow("dhw_gen", LOOP, -o["evap_gen_loop"])
            led.add_flow("dhw_gen", HR, -o["evap_gen_hr"])
            led.add_flow("dhw_gen", AMB, -o["evap_gen_air"])
        led.add_flow("dhw_gen", DHWP, o["G"])
        led.add_elec("dhw_gen", o["W_gen"])
        ext_dhw = o["evap_gen_loop"] + o["evap_s1"] + o["evap_R"]
        for sid, v in dhw_split(ext_dhw).items():
            led.derived[f"extraction:{sid}"] = led.derived.get(f"extraction:{sid}", 0) + v
        if o["R"].sum() > 0:
            comp("dhw_reheat", "reheat", Role.UNIT, w_of(dhw_svc), {Tag.GENERIC})
            led.add_flow("dhw_reheat", LOOP, -o["evap_R"])
            if o["evap_R_air"].sum() > 0:
                led.add_flow("dhw_reheat", AMB, -o["evap_R_air"])
            led.add_flow("dhw_reheat", DHWP, o["R"])
            led.add_elec("dhw_reheat", o["W_R"])
            resid = o["R"] - o["W_R"] - o["evap_R"] - o["evap_R_air"]
            if np.abs(resid).max() > 1e-12:
                led.add_flow("dhw_reheat", AMB, -resid)
        if o["imm"].sum() > 0:
            comp("dhw_immersion", "electric", Role.UNIT, w_of(dhw_svc))
            led.add_flow("dhw_immersion", DHWP, o["imm"])
            led.add_elec("dhw_immersion", o["imm"])
        comp("calorifier", "store" if has_cal else "pass_through", Role.UNIT, w_of(dhw_svc))
        led.add_flow("calorifier", DHWP, -o["cal_in"])
        for sid, v in dhw_split(o["cal_out"]).items():
            led.add_flow("calorifier", f"svc:{sid}", v)
        led.add_flow("calorifier", AMB, o["cal_loss"])
        # excess generation that the full calorifier could not take is returned unused
        excess = o["cal_in"] - o["cal_out"] - o["cal_loss"] - o["cal_dU"]
        if np.abs(excess).max() > 1e-9:
            led.add_flow("calorifier", AMB, excess)
        led.stored["calorifier"] = o["cal_dU"]
    # balancing
    ex_tot = np.zeros(HOURS)
    for k, v in led.derived.items():
        if k.startswith("extraction:"):
            ex_tot = ex_tot + v
    if o["ext_supply"].sum() > 0:
        comp("external_source", "external", Role.DEFICIT)
        led.add_flow("external_source", LOOP, o["ext_supply"])
        led.add_flow("external_source", EXT, -o["ext_supply"])
    if o["ground_supply"].sum() + o["ground_rej"].sum() > 0:
        comp("ground", "borehole_field", Role.DEFICIT)
        led.add_flow("ground", LOOP, o["ground_supply"] - o["ground_rej"])
        led.add_flow("ground", GROUND, -o["ground_supply"] + o["ground_rej"])
    if o["ashp_d"].sum() > 0:
        comp("ashp", "ashp", Role.DEFICIT, tags={Tag.GENERIC})
        led.add_flow("ashp", LOOP, o["ashp_d"])
        led.add_flow("ashp", AMB, -(o["ashp_d"] - o["W_ashp"]))
        led.add_elec("ashp", o["W_ashp"])
    if o["loop_backup"].sum() > 0:
        comp("loop_backup", "electric", Role.DEFICIT)
        led.add_flow("loop_backup", LOOP, o["loop_backup"])
        led.add_elec("loop_backup", o["loop_backup"])
    if o["dc_r"].sum() + o["W_dc"].sum() > 0:
        comp("dry_cooler", "dry_cooler", Role.SURPLUS)
        led.add_flow("dry_cooler", LOOP, -o["dc_r"])
        led.add_flow("dry_cooler", AMB, o["dc_r"] + o["W_dc"])
        led.add_elec("dry_cooler", o["W_dc"])
    if o["tw_r"].sum() > 0:
        comp("tower", "cooling_tower", Role.SURPLUS)
        led.add_flow("tower", LOOP, -o["tw_r"])
        led.add_flow("tower", AMB, o["tw_r"] + o["W_tw"])
        led.add_elec("tower", o["W_tw"])
    if o["ac_r"].sum() > 0:
        comp("ashp_cooling", "ashp_cooling", Role.SURPLUS, tags={Tag.GENERIC})
        led.add_flow("ashp_cooling", LOOP, -o["ac_r"])
        led.add_flow("ashp_cooling", AMB, o["ac_r"] + o["W_ac"])
        led.add_elec("ashp_cooling", o["W_ac"])
    if o["unmet_rej"].sum() > 0:
        comp("unmet", "unmet", Role.UNMET)
        led.add_flow("unmet", LOOP, -o["unmet_rej"])
    # pumps
    if o["W_pump_loop"].sum() > 0:
        rej_tot = np.zeros(HOURS)
        for k, v in led.derived.items():
            if k.startswith("rejection:"):
                rej_tot = rej_tot + v
        w: dict[str, FloatArray] = {}
        for k, v in led.derived.items():
            if k.startswith(("extraction:", "rejection:")):
                s = svc_of[k.split(":", 1)[1]]
                w[s] = w.get(s, np.zeros(HOURS)) + v
        comp("pump:loop", "pump", Role.UNIT, w or None)
        led.add_flow("pump:loop", LOOP, o["W_pump_loop"])
        led.add_elec("pump:loop", o["W_pump_loop"])
    if o["W_pump_dist"].sum() > 0:
        wd_: dict[str, FloatArray] = {}
        for s_ in chw_svc:
            wd_[s_] = qC
        for s_ in lthw_svc:
            wd_[s_] = wd_.get(s_, 0) + qL
        comp("pump:distribution", "pump", Role.UNIT, wd_ or None)
        led.add_flow("pump:distribution", AMB, o["W_pump_dist"])
        led.add_elec("pump:distribution", o["W_pump_dist"])
    if o["W_pump_other"].sum() + o["W_bal_pumps"].sum() > 0:
        w2: dict[str, FloatArray] = {}
        if qC.sum() > 0:
            for s in chw_svc:
                w2[s] = qC
        if qL.sum() > 0:
            for s in lthw_svc:
                w2[s] = w2.get(s, 0) + qL
        if (qDm + qDc).sum() > 0:
            for s in dhw_svc:
                w2[s] = w2.get(s, 0) + qDm + qDc
        comp("pump:generation", "pump", Role.UNIT, w2 or None)
        led.add_flow("pump:generation", AMB, o["W_pump_other"] + o["W_bal_pumps"])
        led.add_elec("pump:generation", o["W_pump_other"] + o["W_bal_pumps"])
    for k in list(led.derived):
        if k.startswith(("extraction:", "rejection:")) and not isinstance(led.derived[k], np.ndarray):
            led.derived[k] = np.asarray(led.derived[k], float) * np.ones(HOURS)
    # every stream must have an extraction/rejection key for allocation (zeros if none)
    for sid in lthw_ids + dhw_ids:
        led.derived.setdefault(f"extraction:{sid}", np.zeros(HOURS))
    for sid in chw_ids:
        led.derived.setdefault(f"rejection:{sid}", np.zeros(HOURS))
    led.derived["loop:extraction"] = ex_tot
    return led


def run_arrangement(project: Project, arr: Arrangement, inp: FullInputs, *, isolated: bool = False) -> FullResult:
    """Run an arrangement. With a borehole field, run twice: the first pass at the undisturbed
    ground temperature gives the ground load profile, and the second uses the ILS fluid
    temperatures that profile produces."""
    res = run_full(project, arr, inp, isolated=isolated)
    gspec = next((b for b in arr.deficit + arr.surplus if b.type == "ground"), None)
    if gspec is None:
        return res
    gf = BoreholeField(**gspec.ground)
    q = res.ledger.derived["o:ground_supply"] - res.ledger.derived["o:ground_rej"]
    t_fluid = gf.fluid_temperature(q)
    res2 = run_full(project, arr, inp, isolated=isolated, ground_fluid_C=t_fluid)
    res2.ledger.derived["T_ground_fluid"] = t_fluid
    return res2
