# Assumptions register

Every assumption the toolkit's results rest on. Each entry has an ID, its basis, its effect on
results and a status. The CLI, UI and reports print the register, and the code cites the IDs
where each assumption enters (search for `A-0xx`).

## Review session: priority order (proposed for the pre-launch interrogation)

| Priority | IDs | Why it matters |
|---|---|---|
| 1 | A-040, A-014, A-019 | The heat-recovery EER at 45 °C condensing comes from a generic η = 0.49 model (≈3.0), not the brief's 4.5. This is the largest single driver of the gap between simplified and full-physics results for archetypes 2/3/4/6. Get the CXWF 078 (or equivalent) map at 10 °C CHW / 45 °C condensing. |
| 2 | Data: the DHW profile (rule 6) | The fixture DHW is effectively flat (median peak hour 4.2 % of daily). Recovery and storage benefits, plant duty and immersion hours all depend on the shape. |
| 3 | A-041 | Definition of "no loop changeover". The brief's 956 MWh is not reproduced under the toolkit's definition. |
| 4 | A-023, A-022, A-033 | CO₂ performance is a three-point fit, not a Data Book map; the circulation-loss share is assumed. The CO₂ archetype ranks first, so it carries the most decision weight. |
| 5 | A-020, A-024, A-038 | Pump modelling basis (pumps are about 2–3 % of electricity here). |
| 6 | A-046, A-047, A-048 | Capex, carbon factor and tariff: indicative placeholders. |
| 7 | A-039, A-007 | Targets and their basis; client-data handling. |
| 8 | All others | Screening and secondary effects. |

## Register

| ID | Assumption | Basis | Effect on results | Status |
|---|---|---|---|---|
| A-001 | ΔT_min defaults: liquid–liquid 2 K, air–liquid 5 K, via store 2 × 2 K | Typical plate HX and coil approaches | Changes direct-recovery potential only (§3.1) | Adopted |
| A-002 | Pinch band width 2.5 K for heat-pump-assisted matching | Resolution vs runtime | Small | Adopted |
| A-003 | Greedy minimum-lift matching for HP-assisted potential | Heuristic; LP cross-check on sampled days | Potential is a screening figure, not a design figure | Adopted |
| A-004 | Fixture 1 "overall ratio" = (heat + coolth delivered) / electricity | Arithmetic on brief figures (2,912.4 / 926 = 3.145) | Convention must be labelled in every report | Confirmed by arithmetic |
| A-005 | Fluid properties from Melinder (2010) / ASHRAE Fundamentals ch.31 correlations | Published data; avoids a CoolProp dependency | — | Superseded by A-017 (CoolProp-generated tables) |
| A-006 | Default VSD pump and motor efficiency curve | EN 50598 IE classes | — | Superseded by A-024 |
| A-007 | Client inputs (`inputs/`, `projects/fixture1/`) are git-ignored; the Fixture 1 regression test skips without them | Confidentiality | None on results | Adopted (provisional answer, not yet confirmed) |
| A-008 | A bare number in a project file is tagged [Assumption] until a source is given | Conservative provenance | Headline tags only | Adopted |
| A-009 | Dry cooler available when dry bulb ≤ cutoff (inclusive) | Matches workbook column AM | Hours at exactly 27.0 °C | Adopted |
| A-010 | Incremental convention: cooling keeps its isolated electricity; the remainder is split between heat services in proportion to their pro-rata electricity | Brief silent on the split between heat services | Per-service heat ratios only; totals unchanged | Adopted, review |
| A-011 | Simple mode: listed pump heat is not returned to the loop | Matches workbook ("excludes pump heat") | Small, only when pumps are switched on | Adopted (simple mode only) |
| A-012 | Isolated reference excludes the dry cooler and has no reuse | Matches workbook columns AA–AC | Isolated ratios only | Adopted |
| A-013 | ASHP COP held at the map end values outside −5 to 10 °C, and those hours are flagged [Extrapolated] | Brief: extrapolation is always flagged | Every Fixture 1 metric carries the [Extrapolated] tag (4,954 h) | Adopted |
| A-014 | Recovery screening uses a generic heat pump at η = 0.50 of Carnot | Fixture plant-schedule points imply η of 0.46–0.52 on the same basis: WSHP heating 0.49, cooling 0.46, DHW 0.52 (see docs/EQUATIONS.md) | Heat-pump-assisted screening figures only | Adopted |
| A-015 | 3 K refrigerant-to-fluid approach on each side for screening COPs | Typical plate evaporator/condenser approach is 2–4 K | Screening COPs only | Adopted |
| A-016 | The storage time-shift aggregates temperature bands and ignores the extra 2 × ΔT_min through a store; standing loss is 0.5 %/h of content | Simplification for screening; full stratified-store physics arrives in Phase 4 | The storage surface is an upper bound | Adopted |
| A-017 | Fluid properties come from tables generated with CoolProp 6.6 (Melinder 2010 for MPG/MEG, IAPWS-95 for water) at 3 bar | Published correlations; CoolProp is not a runtime dependency | Pump and HX corrections for glycol | Adopted |
| A-018 | Fixture stream temperatures: LTHW 40→45 °C, CHW 16→10 °C, DHW mains→65 °C; mains 10 °C constant | Taken from the Option B schematic and workbook Inputs B95/B96 | Recovery analysis only; simple mode ignores temperatures | Adopted, confirm mains profile |
| A-019 | Reversible units use one performance model for both modes: EER = COP_h − 1 at the same operating point | First law (Q_evap = Q_cond − W); generic η 0.49 reproduces both fixture points | All cooling figures | Adopted |
| A-020 | Loop pump flow = (Σ unit extraction + Σ unit rejection) / (ρ c_p ΔT_loop): parallel units, no diversity | Conservative for a 2-pipe ambient loop | Loop pump energy | Adopted, review |
| A-021 | Generic air-source model: T_e = T_db − 8 K, 10 % defrost derate between −3 and +5 °C | Calibrated to the fixture rooftop map (η 0.43–0.47) | ASHP COP when no map is supplied | Adopted |
| A-022 | CO₂ COP knee at 29 °C inlet, with an extra 3 %/K above it | Manufacturer FAQ: COP "starts to reduce" above 29 °C; slope assumed | CO₂ cases with warm inlet (rule 4b) | Assumption |
| A-023 | CO₂ correlation fitted to three published rating points; −1 %/K outlet sensitivity | The full Data Book was not reachable (network policy) | CO₂ COP away from the rating points | Assumption, replace with Data Book map |
| A-024 | Motor and drive part-load efficiency η_md(x) = η_md,100 (1 − 0.25 (1 − x)²) | Approximation to EN 50598-2 IES2 loss points | Pump energy at low flow | Adopted |
| A-025 | Dry cooler: capacity linear in ITD; fan airflow fraction (Q/Q_av)^(1/0.6); fan power ∝ a³; 20 % minimum speed | Eurovent rating basis; fan affinity | Rejection energy | Adopted |
| A-026 | Cooling tower: 4 K approach, 0.01 kWe/kW fan; water treatment and bleed not modelled | Typical open-circuit tower | Tower variants only | Adopted |
| A-027 | Borehole field: ILS with monthly superposition × field-interaction factor 2.0; 50 W/m limit | Screening; size with EED or GLD (CIBSE TM51) | Ground variants only | Assumption |
| A-028 | Inverter scroll part-load COP multipliers 1.03 / 1.08 / 1.05 / 1.00 at 25 / 50 / 75 / 100 % | EN 14825 practice; replace with manufacturer data | Part-load effects | Assumption |
| A-029 | HT screw part-load multipliers 0.78 / 0.89 / 0.96 / 1.00 (constant condenser flow) | Brief rule 10 | DHW heat pump part load | Assumption |
| A-030 | Centrifugal VSD η 0.55; part-load curve | ASHRAE HVAC Systems & Equipment ch. 43 | Only when selected | Assumption |
| A-031 | Air-cooled chiller (rejecting loop heat) η 0.45 with a 10 K condensing approach to air | Typical microchannel condenser | ASHP-cooling rejection | Assumption |
| A-032 | Heating units evaporate against the loop return; cooling units condense against the loop flow | Matches the fixture selections (25/20 source, 20/25 sink) | All loop COPs | Adopted |
| A-033 | Where DHW is a single stream, CO₂ archetypes take circulation losses as 25 % of DHW energy | CIBSE Guide G / BSRIA hotel secondary losses 20–30 % | A7 cases only | Assumption, supply split if known |
| A-034 | Effective COP floored at 1.5 under deep cycling (EN 14825 C_c formula as CR → 0) | Keeps evaporator heat positive and EER finite | Negligible (sub-1 % hours) | Adopted |
| A-035 | Calorifier usable energy = 0.75 V ρ c_p (T_store − T_mains) | Displacement with mixing losses | DHW peak shaving, immersion hours | Assumption |
| A-036 | Standing losses: calorifier 30 W/K, buffer 15 W/K | Typical insulated 5–15 m³ vessels (BS EN 12897) | Store losses | Assumption |
| A-037 | Mixed-mode cooling fleets: units split between HR and reject in proportion to load | Simplification of per-unit mode scheduling | Part-load COP in mixed hours | Adopted |
| A-038 | Pump heat on non-loop circuits goes to ambient; loop pump heat enters the loop | Simplification | Small | Adopted |
| A-039 | Fixture targets: DHW 3.0, LTHW 4.0, CHW 6.44 (incremental basis unless stated) | Option B principle schematic, 17 Nov 2026 | Traffic lights only | Confirm basis |
| A-040 | Full mode computes the HR-mode EER from the model (≈3.0 at 10 °C CHW / 45 °C condensing with η 0.49), not the brief's 4.5 | Generic model calibrated to project points; manufacturer to confirm 45 °C condensing | HR archetypes 2/3/4/6 | Key risk, obtain 078 map |
| A-041 | "No loop changeover" = one DHW HP dedicated to the HR source, the others on the loop | The brief's 956 MWh implies a different definition | A4-nc only | Clarify definition |
| A-042 | Greywater source: 50 kW available at 25 °C unless a profile column is given | Placeholder | A8 only | Assumption |
| A-043 | Default borehole field 40 × 200 m | Placeholder for comparison | Ground variants | Assumption |
| A-044 | Generator capacity limits respected; DHW shortfall goes to immersion (flagged) | Design principle | Backup hours | Adopted |
| A-045 | Floating-loop variant reset: 18 °C at −5 °C, 22 °C at 10 °C, 30 °C at 25 °C dry bulb | Illustrative | A9-float only | Assumption |
| A-046 | Capital proxy rates in library/costs/default.yaml (e.g. WSHP £380/kW, DHW HP £650/kW, CO₂ £1,400/kW, buffer £2,200/m³) | Order-of-magnitude placeholders for ranking only | Capex and Pareto ranking | Assumption, replace with QS rates |
| A-047 | Carbon factor 0.136 kgCO₂e/kWh (SAP 10.2) unless an hourly series is supplied | Part L 2021 / GLA basis; verify against the guidance edition in force | Carbon figures | Verify at submission |
| A-048 | Flat tariff £0.25/kWh unless a time-of-use profile is supplied | Placeholder | Running cost | Assumption |
| A-049 | Generator thresholds: material opportunity > 2 % of heat demand; LTHW/CHW overlap > 500 h for a 4-pipe HRC; DHW share > 40 % for CO₂ | Engineering judgement | Which candidates are proposed | Adopted, review |
| A-050 | Sensitivity ranges: η ±10 %, demands ±20 %, mains ±3 K, loop ±2 K, pump head ±30 %, outdoor ±1.5 K; independent sampling | Engineering judgement | Tornado and Monte Carlo | Adopted, review |
| A-051 | Climate variant is a uniform dry-bulb offset unless a future weather file (e.g. CIBSE DSY/TRY 2050) is supplied | Proxy | Climate variants | Assumption |
