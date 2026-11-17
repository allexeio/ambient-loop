# Design — Heat Recovery & Ambient Loop Scenario Toolkit

Status: **Phase 1 built** (simple mode, ledger, allocation, workbook importer; Fixture 1
reproduced exactly — see `docs/FIXTURE1_MAPPING.md`). Sections 3–5 remain the plan for
Phases 2–4. Original proposal text follows. Nothing below is built yet. It answers the brief's
opening ask: package structure, data schema, and the pinch/recovery method, plus how Fixture 1
will be used to confirm the approach before the full engine is written.

Tags used throughout: [Fact] [Likely] [Assumption]. Model provenance tags (§2.6) are separate.

---

## 0. Headline decisions (the ones worth arguing about)

1. **Circuit-node energy model, not a thermofluid network solver.** Each hydraulic circuit is a
   thermal node with a temperature (fixed, scheduled/floating, or a store state). Components
   exchange heat with circuits through named ports. Flow, ΔT, minimum-flow limits and pump power
   are derived per circuit from the heat flows (Q = ṁ·cp·ΔT). This is the right fidelity for
   design-stage scenario work, keeps an 8,760 h run well under a second, and every number traces
   to one equation. It cannot resolve hydraulic interaction between parallel branches (e.g.
   PICV authority, bypass short-circuiting). That is out of scope and will be stated in reports.
2. **Balance closes by construction; shortfall is visible, never absorbed.** Each circuit's hourly
   balance is closed by balancing plant within its capacity. Anything left goes to an explicit
   `unmet` pseudo-component, counted in hours and kWh. The 1e-6 kWh residual test then checks
   the bookkeeping, and unmet demand can't hide in it.
3. **Pinch analysis works on load streams only, never on plant.** A cooling load is a hot stream
   at CHW temperature (e.g. 12→6 °C). The chiller's condenser heat at 30–45 °C is a *consequence
   of an arrangement*, not a stream. Mixing the two double-counts and is the commonest error in
   heat-recovery spreadsheets.
4. **Efficiency figures are typed objects that carry their boundary.** A `Metric` cannot be
   rendered without pumps in/out, allocation convention, DHW profile type, mode (simple/full)
   and worst provenance tag. This enforces working rule "never an efficiency without its
   boundary" in code, not by discipline.
5. **No fluid-property dependency.** Water and ethylene/propylene glycol properties come from
   published correlations (Melinder 2010 / ASHRAE Fundamentals ch.31 tables), coded and
   unit-tested against tabulated values. CoolProp is overkill for liquid secondary fluids.

---

## 1. Package structure

```
ambient-loop/
├── pyproject.toml            # Python ≥3.11, locked with uv (uv.lock)
├── ASSUMPTIONS.md            # live register; also surfaced in CLI/UI output
├── docs/                     # this file, equations reference (EQUATIONS.md), rule evidence
├── inputs/                   # user-supplied workbooks, CSVs, weather, PDFs (git-ignored)
├── library/
│   ├── equipment/*.yaml      # one file per manufacturer selection (map tables + source doc)
│   ├── generic/*.yaml        # Carnot-efficiency parameters by technology/refrigerant [Generic]
│   ├── profiles/*.yaml       # DHW / occupancy shapes with citations
│   └── costs/default.yaml    # capital-proxy table (indicative)
├── projects/<name>/project.yaml
├── src/ambient_loop/
│   ├── core/
│   │   ├── series.py         # Hourly: 8,760 float64 array + unit + tag set; calendar helpers
│   │   ├── provenance.py     # Tag enum, Tagged[T], tag propagation (worst-of)
│   │   ├── metric.py         # Metric with mandatory Boundary
│   │   └── run.py            # run manifest: config hash, git SHA, timestamp, lib versions
│   ├── schema/               # pydantic v2 models = the YAML contract (§2)
│   │   ├── project.py  streams.py  sources.py  components.py
│   │   ├── graph.py    controls.py scenarios.py targets.py tariffs.py
│   ├── io/
│   │   ├── loads_table.py    # CSV/XLSX with column mapping
│   │   ├── weather.py        # EPW, CIBSE TRY/DSY; mains water temperature by month
│   │   ├── milieu_workbook.py# Fixture 1 importer (Inputs / Hourly calculation / ...)
│   │   ├── equipment_pdf/    # Trane Select Assist parser (pdfplumber) + review/override
│   │   └── export/           # xlsx (with formulas), csv, html report
│   ├── loads/                # benchmark synthesis, DHW shaping, validation warnings
│   ├── physics/
│   │   ├── fluids.py         # cp, ρ, μ, k vs T and concentration
│   │   ├── hx.py             # fixed approach; ε-NTU counterflow; fluid correction
│   │   ├── pumps.py          # system curve, affinity laws, VSD efficiency, min speed
│   │   └── perfmap.py        # gridded maps, interpolation, extrapolation masks; Carnot fallback
│   ├── components/           # heat_pump, hr_chiller, co2_hp, dry_cooler, tower, store,
│   │                         # ground, hx, backup, unit_wshp, pump — one class each
│   ├── recovery/             # §3: hourly problem table, GCC, band matching, storage shift
│   ├── sim/
│   │   ├── engine.py         # hourly loop; vectorised where stateless
│   │   ├── dispatch.py       # priority order, modes, min-run, sequencing strategies
│   │   ├── ledger.py         # per-hour per-component energy ledger (single source of truth)
│   │   └── simple_mode.py    # workbook-compatible simplifications (§4)
│   ├── rules/                # one module per rule, registry, Evidence objects
│   ├── arrangements/         # archetype builders + generator driven by recovery results
│   ├── scenarios/            # batch, sweeps, optimiser, sensitivity, capex proxy
│   ├── reporting/            # allocation conventions, KPIs, Sankey data, SVG schematics
│   └── cli.py
└── tests/
    ├── unit/  fixtures/synthetic/  regression/fixture1/
```

The ledger is the single source of truth: allocation, KPIs, Sankey, exports and rules all read
the same hourly table (component × hour × {Q_in, Q_out, W_elec, mode, PLR, flags}), so no
output can disagree with another.

---

## 2. Data schema

YAML in, pydantic-validated. Every physical quantity is either a scalar, a monthly list of 12,
an hourly series reference, or a schedule. Units are fixed by field name (`_kW`, `_C`, `_K`,
`_kWh`, `_l`, `_kPa`) — no unit parsing.

### 2.1 Streams (loads)

```yaml
streams:
  - id: hotel_dhw_makeup
    kind: dhw                    # space_heating | space_cooling | dhw | dhw_circulation |
                                 # process_heating | process_cooling | exhaust_air
    owner: hotel                 # meter / tenant for allocation and reporting
    duty_kW: {series: loads.csv#Hotel_DHW_kW}   # or {synth: {...}} or {profile: ..., annual_kWh: ...}
    T_in_C:  {monthly: mains_water}             # cold-feed temperature, from weather module
    T_out_C: 60
    exchange_class: liquid_liquid               # picks ΔT_min
    tag: project_data
  - id: hotel_chw
    kind: space_cooling
    owner: hotel
    duty_kW: {series: loads.csv#Hotel_Cooling_kW}
    T_in_C: 12                   # return (stream enters exchanger at this temperature)
    T_out_C: 6
    exchange_class: liquid_liquid
```

Convention: every stream is described by the temperature it **enters** and **leaves** the
exchange at. Hot vs cold follows from the sign (T_in > T_out ⇒ hot, it gives heat). This removes
the supply/return ambiguity between heating and cooling.

DHW is split by default into make-up (T_mains→T_store) and circulation losses (≈55→60 °C) because
they have very different recovery value and the CO₂ archetype depends on the split.

### 2.2 Sources and sinks

```yaml
sources_sinks:
  - id: outdoor_air   ; kind: ambient_air  ; T_C: {weather: dry_bulb}
  - id: ground        ; kind: borehole_field; model: g_function   # or fixed_temperature
    params: {n_bores: 40, depth_m: 200, spacing_m: 6, k_ground: 2.2, T_undisturbed_C: 12.5}
  - id: greywater     ; kind: wastewater ; T_C: 25 ; available_kW: {series: ...}
```

### 2.3 Components

Discriminated union on `type`. Each component names its **ports**; each port joins a circuit.
Performance is a reference into the library, never inline numbers in the project file.

```yaml
components:
  - id: dhw_hp_1
    type: heat_pump_ww
    ports: {evap: loop, cond: dhw_primary}
    performance: library/equipment/trane_RTSF_DHW_2024-03.yaml   # or generic/screw_R1234ze.yaml
    capacity_kW: 450
    count: 2
    min_flow_evap_ls: 6.1 ; min_flow_cond_ls: 4.0
    envelope: {T_evap_lvg_C: [5, 30], T_cond_lvg_C: [35, 70]}
    part_load: {model: map}                   # or {model: en14825_cd, Cd: 0.25}
```

Library equipment file (one per selection):

```yaml
source_document: "Trane Select Assist product report, ref XXXX"
document_date: 2024-03-11
selection_conditions: {fluid_evap: water, fluid_cond: water, ...}
tag: manufacturer_data
axes: {T_src_lvg_C: [...], T_snk_lvg_C: [...], PLR: [0.25, 0.5, 0.75, 1.0]}
capacity_kW: [[[...]]]           # heating capacity on the same grid
cop:         [[[...]]]
```

Axes are **leaving** temperatures (rules 3 and 4). For CO₂ transcritical the sink axis is the
**gas-cooler entering water temperature**, because that sets the COP (see rule note in §5).

### 2.4 Graph (circuits)

```yaml
circuits:
  - id: loop
    fluid: {type: water}                       # or {type: propylene_glycol, mass_fraction: 0.3}
    pressurisation: pu_1                       # direct coupling across different PUs is rejected
    temperature: {mode: floating, min_C: 15, max_C: 28, deadband_K: 2}
    design_dT_K: 5
    volume_l: 8000                             # thermal mass for the floating mode
    pump: {design_flow_ls: 40, design_head_kPa: 180, control: vsd_dp, min_speed: 0.3,
           static_head_fraction: 0.2, eta_pump_motor: 0.62}
  - id: dhw_primary
    fluid: {type: water}
    temperature: {mode: fixed, flow_C: 65, return_C: 15}
```

The validator rejects: a component port on a circuit whose fluid or pressurisation unit differs
from what the component allows; potable water at 20–50 °C in a store (HSG274); DHW secondary
return routed through a preheat stage.

### 2.5 Controls, targets, tariffs, scenarios

```yaml
controls:
  recovered_heat_priority: [space_heating, dhw_preheat, store, dhw_hp_source, reject]
  sequencing: {dhw_hp: lead_lag_full_load}      # or equal_share
  min_run_h: 1 ; changeover_penalty_kWh: 0
  store_dispatch: rule_based                    # or rolling_lp {horizon_h: 24}
targets:
  - {service: dhw, metric: seasonal_ratio, value: 2.8, boundary: {pumps: included}}
  - {service: chw, metric: seasonal_ratio, value: 6.0}
tariffs: {electricity: {type: tou, file: tariffs/uk_2026.csv}, carbon: {type: hourly, file: ...}}
scenarios:
  base: {arrangement: archetype_1}
  sets:
    - name: dhw_sensitivity
      vary: {streams.hotel_dhw_makeup.duty_kW.scale: [0.8, 1.0, 1.25]}
```

### 2.6 Provenance

`Tag` = manufacturer_data < project_data < generic < assumption < extrapolated (worst wins).
Inputs carry a tag at field level. Each output series carries the set of tags of everything
that fed it, plus an **hourly extrapolation mask** from performance maps. The report shows,
for example, "DHW ratio 2.91 [Manufacturer data; 312 h extrapolated]".

---

## 3. Pinch / recovery analysis method (Phase 2)

### 3.1 Per hour: the problem table

For each hour *t*:

1. Shift stream temperatures by ±ΔT_min/2 per exchange class. Defaults [Assumption]:
   liquid–liquid plate 2 K, air–liquid 5 K, via an intermediate store 2 × liquid–liquid.
2. Build temperature intervals and the heat cascade (Linnhoff problem table). This gives the
   pinch temperature, Q_H,min (deficit), Q_C,min (surplus) and the grand composite curve (GCC)
   for that hour. It is fully vectorised across hours: streams × intervals × 8,760 arrays.
3. **Directly recoverable heat** = Σ cold-stream duty − Q_H,min.

[Likely] In most commercial buildings direct recovery is small. CHW at 6–16 °C cannot heat
anything useful. The direct opportunities are warm sources to cold feeds: wastewater (~25 °C)
to mains water (~10 °C), exhaust air to supply air, server or process cooling at 25–35 °C to
DHW preheat. The heat-pump-assisted figures in 3.2 will carry most of the story, and the reports
will say so.

### 3.2 Heat-pump-assisted potential and lift

The GCC below the pinch gives the surplus, as kWh per temperature band. The GCC above the pinch
gives the deficit by band. A heat pump between them delivers Q_snk = Q_src + W, so
Q_src = Q_snk · (1 − 1/COP).

Method: discretise both into 2.5 K bands. Match sources to sinks by **minimum lift first** (the
warmest surplus goes to the coolest deficit). Use the generic Carnot COP of each lift to work out
the source heat consumed. The outputs are:

- HP-assisted recoverable heat (kWh)
- a lift distribution (kWh by lift band)
- the implied compressor work at generic η
- the unmatched surplus to reject and unmatched deficit to generate

[Assumption] Greedy minimum-lift matching is a heuristic, not a proven optimum for total
compressor work. It will be cross-checked against an exact transport LP (scipy HiGHS) on sampled
days, and the gap reported.

**Coincidence factor (rule 5)** = Σ_t min(surplus_t, src-equivalent deficit_t) / min(Σ surplus, Σ
deficit), per month and per year. 1.0 means timing costs nothing; low values mean storage or
nothing.

### 3.3 Storage time-shift

Model a store as a band-to-band carrier. It is charged by surplus at band *b* and discharges to
deficit at bands ≤ b − 2·ΔT_min. Its parameters are capacity (kWh) and a maximum holding window
N (h), with heat older than N h discarded.

- **Single temperature band:** a chronological greedy (charge on surplus, discharge on deficit,
  FIFO ageing) is optimal for maximising recovered energy. This holds with no losses and a
  capacity limit. When full, it displaces the oldest heat with the newest [Likely: holds for a
  lossless single store; I'll write out the exchange argument in EQUATIONS.md and check it
  against an exact LP in tests before relying on it].
- **Several bands:** cascade the bands hot to cold. This is a heuristic, cross-checked against a
  rolling 7-day LP.

Output: a surface of extra recovery against (capacity, N), monthly and annual, by band. This is
the "what is storage worth before choosing hardware" chart. It also gives the arrangement
generator its store sizing range.

### 3.4 Hand-off to the arrangement generator

Each opportunity is stored as a typed record with its band, kWh, coincidence, lift and storage
sensitivity. Examples: "direct: wastewater→cold feed, 140 MWh/yr"; "HP: 10–16 °C surplus →
55–65 °C deficit, lift ≈ 50 K, coincidence 0.82". Archetypes declare which opportunity types they
target. The generator proposes those whose targets are material (above a threshold, default 2 %
of annual heat demand) and records the reason.

---

## 4. Full-physics simulation (Phases 3–4), in brief

- **Hour loop.** Order of work in each hour:
  1. terminal loads onto their secondary circuits
  2. dispatch recovery by priority order
  3. the heat pumps and chillers resolve PLR, COP and capacity from their maps, at the circuit
     temperatures from the previous state
  4. balancing plant closes each circuit within its capacity
  5. anything left goes to `unmet`
  6. the store and floating-loop states advance

  Hours without state are vectorised. The stateful path is a lean scalar loop; target < 1 s.
  If it misses that, I'll ask about adding numba rather than add it quietly.
- **COP model:** COP = f(T_src_lvg, T_snk_lvg, PLR) from the map. Generic fallback is
  η(PLR, type) · T_c/(T_c − T_e), where T_c = T_snk_lvg + approach_cond and
  T_e = T_src_lvg − approach_evap (both in K). Part load follows the map, or EN 14825 cycling
  degradation (C_d) below minimum capacity.
- **Hydraulics per circuit:** ṁ = Q/(cp·ΔT). Flow is clamped to the machine minimum, which raises
  the ΔT ceiling and fires the rule 4 flag. Pump power uses a system curve
  H = H_static + k·V̇², affinity laws, and VSD part-load efficiency (motor plus drive curve,
  EN 50598 IE classes) [Assumption: default curve in library].
- **Fluids:** glycol changes cp, ρ, μ. That feeds pump power (through flow and friction factor)
  and HX U-value (through a Reynolds-number correction on fixed approach, or ε-NTU).

### Workbook-compatible simple mode (Fixture 1)

Exactly the brief's simplifications:

- fixed COPs
- same-hour ideal reuse
- pumps off
- ASHP COP from dry bulb, held constant above 10 °C
- dry cooler cutoff at 27 °C ambient

It is the same ledger and allocation code with simplified component models swapped in. Matching
the workbook therefore validates the bookkeeping and allocation separately from the physics.

---

## 5. Rules engine — notes and two additions/qualifications

Each rule is a class with `evidence` (citation), `applies(scenario)`, `check(ledger) → findings`.
Each also has a synthetic unit test in which the rule must fire and one in which it must not.

- **Qualification to rule 4 (my addition).** "Condenser COP is set by leaving temperature" holds
  for subcritical machines. It is **false for CO₂ transcritical**: there, the gas-cooler entering
  (return) water temperature dominates COP. This is the physical reason archetype 7 separates
  circulation reheat. Encoded as rule 4b. [Fact — e.g. Nekså, Int. J. Refrigeration 2002; the
  same behaviour appears in manufacturer CO₂ DHW data.]
- **Further rules I'll add, marked as mine:**
  - Evaporator freeze margin with glycol fraction (ASHRAE Fundamentals ch.31).
  - Store standing loss and stratification degradation at low draw rates (CIBSE Guide G / BS EN
    12897 test-loss basis).
  - Minimum compressor runtime vs store size (cycling under 6 starts/h; manufacturer limits).
  - SAP 10.2/GLA carbon factor basis declared on every carbon figure (GLA Energy Assessment
    Guidance, latest edition — to be verified at Phase 5, not assumed).

---

## 6. Fixture 1 — what I can already check, and what's blocked

**Blocked:** the workbook is not in `./inputs/` (the repo was empty apart from the brief). Phase 1
validation needs it.

**Already checked — headline figures are internally consistent [Fact, arithmetic]:**

| | MWh/yr | Ratio | Electricity, MWh/yr |
|---|---|---|---|
| LTHW | 82.9 | 3.01 | 27.5 |
| CHW | 634.2 | 6.44 | 98.5 |
| DHW | 2,195.3 | 2.74 | 801.2 |
| **Total** | **2,912.4** | **3.145** (2,912.4 / 926) | **927.2** vs 926 stated (0.13 %, ratio rounding) |

The regression table's ratios are likewise all 2,912.4 MWh divided by the stated electricity
(1,040 → 2.80; 808 → 3.60; 765–775 → 3.81–3.76). The "overall ratio" therefore counts **coolth
delivered as useful output** alongside heat. That's a legitimate convention, but it should be
named. GLA submissions report heating SCOP and cooling SEER separately, and a combined
heat-plus-coolth ratio will be challenged if it isn't labelled.

**The physics story the fixture is likely to tell [Likely, pending workbook]:**

- **DHW is about 75 % of useful output.** The scheme stands or falls on DHW heat-pump COP and the
  DHW profile shape (rule 6). LTHW is noise at 3 %.
- **The loop is heat-short by more than 1 GWh/yr.**
  - Heat-recovery cooling rejects ≈ 634.2 × (1 + 1/4.5) = 775 MWh.
  - The DHW heat pumps need ≈ 2,195.3 × (1 − 1/5.3) = 1,781 MWh of source heat.
  - That puts nearly all rejected heat to use when coincident. A dry cooler is close to worthless
    (rule 9), and storage only buys timing. That fits the regression table's modest 808 → ~770
    step from storage.
- **Where full-physics mode will depart.** The simplified model holds heat-recovery EER at 4.5
  whether the chillers condense into a ~28 °C loop or a 45 °C preheat/LTHW circuit. In reality the
  45 °C case costs perhaps 20–30 % of EER [Likely, Carnot-scaled]. That means the simple mode
  flatters archetypes 3–4 and the "LTHW-first" row. I expect the gap between 808 and 926 to
  narrow in full mode. That is the departure the Phase 1 report should quantify and explain.

---

## 7. Dependencies (approval requested)

Proposed runtime: numpy, pandas, scipy, pydantic ≥2, PyYAML, openpyxl, pdfplumber, typer (CLI).
Dev: pytest, hypothesis (property tests for conservation), ruff, mypy. Locking: uv.

Later (Phase 6): plotly and streamlit. Only if needed: numba. Explicitly not: CoolProp,
TESPy/Modelica.

Streamlit is fine for v1 internal use. If this becomes client-facing or multi-user, the better
long-term shape is the same engine behind a small FastAPI service with a static front end.
Decide at Phase 6, not now.

---

## 8. Reference schematic (received 17 Nov 2026)

`inputs/sample_wshp_schematic.pdf` (git-ignored) is the reference for two later pieces of work.

**(a) The graph schema (Phase 3–4)** must be able to express it:

- 4-pipe reversible WSHPs, each with a source changeover (CV-S) and a user changeover (CV-U).
  Each changeover switches flow and return together.
- Per-unit modes: heating, cooling with heat recovery, cooling with rejection. Each has an
  entry condition (buffer state, demand).
- A stratified primary buffer with charge (top) and draw (bottom) heights and a sensor count.
- A PHX preheat on the cold feed, with secondary outlet temperature control.
- A DHW secondary return that bypasses preheat. The validator checks this (rule 8).
- Calorifiers with coils, and a DHW heat pump top-up sourced from the loop.
- Tagged pumps per circuit.

**(b) The auto-generated schematic style (Phase 6).** Target output:

- colour per circuit, matching the legend
- flow solid, return dashed
- equipment tags, and temperatures on the pipes
- numbered callouts linked to design notes
- an operating-modes table and an "expected performance" strip with the boundary stated

This arrangement is archetype 2 + 3 ("HR → LTHW buffer → DHW preheat → DHW HP top-up from
loop"). It will be the first hand-written arrangement YAML in Phase 3.
