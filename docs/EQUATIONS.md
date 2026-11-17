# Equations, units and references

Each equation in the code is listed here with its units, the module that implements it, and its source. Quantities are hourly: Q in kWh per hour, which is numerically equal to mean kW. Temperatures are in °C unless marked K.

## 1. Energy balance (sim/ledger.py)

A flow q(c, n) is heat (kWh) that component c delivers into node n. The ledger checks the following every hour.

- **Internal node (loop, heat-recovery circuit, DHW primary):** Σ_c q(c, n) = 0
- **Component:** Σ_n q(c, n) = W_c − ΔU_c, where W is electricity and ΔU is the change in stored energy (stores only)
- **Service node:** Σ_c q(c, svc) = ±demand

The tolerance is 1e-6 kWh, and tests assert it for every archetype. Shortfalls go to an explicit `unmet` component (design principle).

## 2. Heat pumps and chillers (physics/performance.py)

**Carnot-referenced COP** (generic models):

COP_h = η · T_c / (T_c − T_e), with T_c = T_snk,lvg + Δ_c + 273.15 K and T_e = T_src,lvg − Δ_e + 273.15 K.

- Leaving temperatures set COP (brief rules 3 and 4).
- Δ is the refrigerant-to-fluid approach, 2 K by default.
- T_c − T_e is floored at 10 K.

**Cooling:** EER = COP_h − 1 at the same operating point, because Q_evap = Q_cond − W (first law). A-019.

**Generic η calibration** (library/generic/*.yaml), using the fixture's selection points on the same basis:

| Machine (fixture point) | Quoted performance | Implied η |
|---|---|---|
| WSHP heating, 45 °C from a 25/20 source | COP 5.443 | 0.49 |
| WSHP cooling, 10 °C from a 20/25 sink | EER 6.753 | 0.49 |
| High-temperature DHW heat pump, 65/55 from a 25/20 source | COP 3.585 | 0.52 |
| Rooftop ASHP map, 25 °C LWT, T_e = T_db − 8 K | — | 0.43–0.47 |

The literature range is 0.40–0.60 (Arpagaus et al. 2018, *Energy* 152:985; CIBSE AM17 2022).

**Air source:** T_e = T_db − 8 K. A 10 % defrost derate applies between −3 and +5 °C (A-021).

**Map models:** bilinear interpolation over (T_src,lvg, T_snk,lvg). Outside the map the end values are held, and each such hour is flagged [Extrapolated] (brief §2.3).

**Transcritical CO₂** (rule 4b, A-022/023):

COP = COP_ref · (1 − k_in(T_in − 9) − k_in2·max(0, T_in − 29)) · (1 + k_src(T_src − 7)) · (1 − k_out(T_out − 65))

- k_in = 0.0096 K⁻¹ and k_src = 0.0168 K⁻¹ are fixed exactly by the published QAHV-N560 points (3.88, 3.65, 3.44).
- Nekså (2002), *Int. J. Refrig.* 25:421.

**Part load:** f(PLR) is a multiplier between PLR_min and 1. Below PLR_min, EN 14825:2022 cycling applies:

COP_bin = COP(PLR_min) · CR / (C_c·CR + 1 − C_c), with CR = PLR / PLR_min and C_c = 0.9.

Effective COP is floored at 1.5 (A-034).

**Sequencing:**
- **lead_lag:** k units at full load plus one unit at r = Q/Q_u − k.
- **equal_share:** all units at Q / (n·Q_u).

## 3. Hourly pinch analysis (recovery/pinch.py)

**Shifted temperatures:** T* = T − ΔT_min/2 for hot streams and T + ΔT_min/2 for cold streams. ΔT_min is 2 K liquid–liquid and 5 K air–liquid (A-001).

**Interval heat:** CP = Q / |T_in − T_out| (kW/K). Each stream contributes CP × overlap to each 0.5 K interval.

**Cascade:** Q_H,min = max(0, −min cumulative surplus). Direct recovery = Σ Q_cold − Q_H,min (Linnhoff & Hindmarsh 1983; Kemp 2007, ch. 2–4).

**GCC envelopes:**
- Source below the pinch: E(T) = min over T'' ≤ T of Q(T'').
- Sink above the pinch: D(T) = min over T'' ≥ T of Q(T'').

**Heat-pump matching** (minimum lift first): Q_src = Q_snk (1 − 1/COP), using the generic COP with η 0.5 and 3 K approaches (A-003, A-014, A-015).

**Coincidence factor:** CF = Σ_t Q_src,matched / min(Σ surplus, Σ source need).

**Storage shift** (recovery/storage.py): a greedy FIFO store with capacity C and holding window N. Tests check it equals the LP optimum for a lossless single store (A-016).

## 4. Stores (physics/store.py)

**Usable energy:** E = Σ m_i c_p (T_i − T_ref) / 3.6e6 kWh. Capacity = V ρ c_p (T_hot − T_ref) / 3.6e6.

**Plug flow:** charge mass m = Q / (c_p (T_in − T_bottom)) enters at the top and leaves at the bottom. Discharge m = Q / (c_p (T_top − T_ret)). Layers are sorted by temperature, which models buoyant insertion and mixing. The stack is then re-gridded to N equal-mass nodes, conserving energy exactly. Reference: Duffie & Beckman, 4th ed., §8.4.

**Standing loss:** q = UA_node (T_i − T_amb) Δt (A-036).

**DHW calorifier:** E_max = 0.75 V ρ c_p (T_store − T_mains) (A-035). Generator target = net draw + loss + (E_max − E), capped at fleet capacity. Any shortfall goes to electric immersion, which is flagged.

## 5. Pumps and hydraulics (physics/pumps.py, physics/fluids.py)

**Flow:** V̇ = Q / (ρ c_p ΔT) in m³/s.

**System curve:** H = H_s + (H_d − H_s)(V̇/V̇_d)², with H_s/H_d = 0.25 by default (CIBSE Guide C §4).

**Electrical power:** P = ρ g H V̇ / (η_pump · η_md(x)). The motor-and-drive efficiency η_md(x) = η_md,100 (1 − 0.25(1 − x)²), where x = V̇/V̇_d (EN 50598-2 approximation, A-024).

**Speed limits:** minimum speed is held at 30 %. A constant-speed pump draws full power whenever the circuit is live.

**Glycol:** relative hydraulic power = (ρ_g/ρ_w)^0.75 (μ_g/μ_w)^0.25 (V̇_g/V̇_w)^2.75. This follows from Blasius friction, f = 0.316 Re^−0.25.

**Convective HTC ratio** (Dittus–Boelter): (k^0.6 ρ^0.8 c_p^0.4 μ^−0.4)_g / (…)_w.

**Fluid properties:** CoolProp 6.6 tables. IAPWS-95 for water; Melinder (2010) for MPG/MEG (A-017).

**Minimum-flow ΔT limit** (rule 4): ΔT_max = Q_unit / (ρ c_p V̇_min).

## 6. Rejection and sources (physics/rejection.py, physics/ground.py)

**Dry cooler:**
- Available when T_db < T_fluid,in − approach (5 K).
- Capacity Q_av = Q_rated (T_in − T_db) / ITD_rated.
- Fan: airflow fraction a = (Q/Q_av)^(1/0.6), fan power P = P_rated a³ with a ≥ 0.2 (A-025). Basis: Eurovent rating method and fan affinity.

**Cooling tower:** available when T_wb + approach < T_in. Wet bulb from Stull (2011), *J. Appl. Meteor. Climatol.* 50:2267.

**Borehole field:** ΔT_b = Σ Δq'_i / (4πk) · E1(r_b² / (4α(t − t_i))) · F_field. Fluid temperature T_f = T_g − ΔT_b − q' R_b. Monthly superposition with a 3-year spin-up, run as two passes (A-027). References: CIBSE TM51 (2013); Carslaw & Jaeger (1959).

**External source** (e.g. greywater): available when T_ext − approach ≥ loop return temperature.

## 7. Allocation (reporting/allocation.py)

**Pro-rata** (the workbook's method):
- Unit electricity goes to the services it serves.
- Deficit plant electricity is split by each service's loop extraction, hour by hour.
- Surplus plant electricity is split by each service's loop rejection.
- Pumps are split by their circuit's service duty.

**Incremental:** cooling keeps its isolated electricity, from a run with no reuse and no dry cooler. Heat services carry the remainder, split by their pro-rata electricity (A-010).

**Check:** every convention sums to the system total each hour, to 1e-6 kWh.

**System ratio** = (heat + coolth delivered) / total electricity (A-004).

## 8. Economics (scenarios/economics.py)

**Carbon:** Σ_t W_t · c_t. Default c = 0.136 kgCO₂e/kWh, the SAP 10.2 electricity factor; verify it at submission (A-047).

**Running cost:** Σ_t W_t · tariff_t. Flat or time-of-use (A-048).

**Capital proxy:** Σ rate × size from library/costs/default.yaml. Indicative only (A-046).

**Pareto front:** non-dominated on (capex, carbon).

## 9. Legionella and DHW (rules/engine.py, rule R8)

These rules follow HSE HSG274 Part 2 (2014):
- DHW is stored at 60 °C or above.
- Outlets reach 50 °C or above within one minute, or 55 °C in healthcare.
- No potable storage between 20 and 45 °C.
- The secondary return never passes through the preheat.

These conditions are enforced by validation and flagged as failures.
