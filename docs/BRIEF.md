# Claude Code brief: Heat Recovery & Ambient Loop Scenario Toolkit

> Paste this into Claude Code from an empty project folder. Optional: put any project workbooks, demand CSVs, weather files and manufacturer selection reports in `./inputs/` to use as test cases.

---

## 1. What we're building

A **project-agnostic engineering toolkit** for Milieu, a London MEP and sustainability consultancy. On any building or campus, it should:

1. **Take loads in:** hourly (8,760 h) heating, cooling, DHW and process loads, each with the **temperature level** it needs. If no hourly data exists, synthesise loads from benchmarks.
2. **Find the recovery potential:** identify where heat rejected by one load can offset another, at what temperature, at what time, and how much.
3. **Propose hydraulic arrangements** that capture that potential. Examples: ambient loop, heat-recovery chillers, preheat, cascades, CO₂ DHW, storage, external sources and sinks.
4. **Scenario-plan:** simulate each arrangement hour by hour with realistic plant performance, and rank them by efficiency, carbon, cost and capital proxy against user targets.
5. **Explain the results:** why one arrangement beats another, what it depends on, and which assumptions carry the most risk.

The users are experienced building services engineers. Don't simplify the physics, and make every number auditable. Outputs must be defensible in planning energy statements (e.g. GLA) and design reports.

**Nothing project-specific in the code.** Projects, plant selections and tariffs are data. One real project, a hotel with residential units on an ambient loop, is supplied only as a **validation fixture** (§9).

---

## 2. Core concepts

### 2.1 Loads, sources and sinks: everything is a heat stream with a temperature
Model each load as a stream with:
- hourly duty in kW
- supply and return temperatures, or a required delivery temperature
- type (heating, cooling, DHW, process)
- owner or meter (hotel, residential, retail tenant, etc.)

Typical streams to support out of the box:
- Space heating (LTHW at 35–55 °C or more, fan coils, underfloor, AHU heating coils)
- Space cooling (CHW 6/12, 10/16, 14/18 °C, chilled beams)
- DHW (make-up 10→60/65 °C; secondary circulation losses as a separate stream at about 55 °C)
- Process cooling: server rooms, kitchen refrigeration, cold rooms, comms rooms (year-round heat sources)
- Process heating: pools, laundries, kitchens
- Ventilation: exhaust air heat available to recover
- Residential units with their own WSHPs, and tenant loads on a shared loop

Sources and sinks:
- outdoor air
- ground (closed or open loop)
- surface water
- wastewater (grey or black water heat recovery)
- neighbouring buildings or networks (5GDHC)
- data centres
- the loop itself

### 2.2 Recovery potential before hardware: hourly pinch and heat-cascade analysis
- Each hour, build hot and cold composite curves from the streams (with a user-set ΔT_min per exchange type). Compute:
  - **directly recoverable heat** (heat exchange alone, no heat pump)
  - **heat-pump-assisted recoverable heat** (needs a lift), and the lift required
  - **unmatched surplus** (to reject) and **unmatched deficit** (to generate)
- Repeat with **storage**: a time-shift window of N hours and a capacity in kWh. This shows how much extra recovery storage unlocks.
- Output: monthly and annual "recovery opportunity" figures by temperature band, before any arrangement is chosen. The arrangement generator (§4) is driven from this.

### 2.3 Performance must depend on temperature and part load
Never use a single fixed COP unless the user explicitly selects a "simple mode". Machines are modelled from performance maps:
- **Manufacturer data**, e.g. imported from selection software reports. Tables of COP/EER against source temperature, sink temperature, part load and fluid.
- **Generic fallback:** COP = η_Carnot × T_sink/(T_sink − T_source), using temperatures that include heat-exchanger approaches. η is set per technology and refrigerant (screw, scroll, centrifugal, CO₂ transcritical) and depends on part load.
- Interpolating within a map is fine. **Extrapolating beyond it is always flagged.**

---

## 3. Design knowledge to encode as rules

These come from real project reviews. Implement them as a **rules and insights engine**: it checks each scenario, emits warnings and explanations, and seeds the arrangement generator. Each rule must be testable and must name the evidence it is based on.

1. **Double lifts lose.** Lifting loop heat to an intermediate temperature with one heat pump and then again with a second (e.g. 25→45 °C, then 45→65 °C through a water circuit) is worse than one lift from 25 to 65 °C. Each stage adds heat-exchanger approach losses and its own compressor inefficiency. It only makes sense when the intermediate heat is **recovered** (free), not generated.
2. **Use recovered heat directly where its temperature allows.** Using waste heat at 45 °C to preheat DHW (10→40 °C) through a heat exchanger beats feeding the same heat to a heat pump's evaporator, because no compressor is needed.
3. **Evaporator temperature is set by the leaving fluid.** A 45/25 °C source gives about the same COP as 25/20 °C. A large temperature fall belongs on heating cold water (preheat), not on an evaporator.
4. **Condenser COP is set by the leaving temperature.** A colder entering temperature helps only marginally. A wide temperature difference is also limited by the machine's **minimum flow**: compute the maximum achievable ΔT from minimum flow and capacity, and flag any selection that breaks it.
5. **Heat recovery only pays when demands overlap.** The value of an ambient loop or heat-recovery chiller depends on the hourly coincidence of heat rejected and heat demanded, plus whatever storage can shift. Report the coincidence factor explicitly.
6. **A flat DHW profile flatters heat recovery.** If a DHW profile's peak hour is below about 6 % of the daily total, warn the user and offer shaped profiles (occupancy × litres per person or bed per day × hourly shape, CIBSE Guide G / CIPHE type). Plant duty is not the same as the peak-hour draw-off.
7. **Storage is what lets recovered heat be used.** Stratified primary-side stores with a wide temperature difference (e.g. charged at 45 °C, drawn back at 15 °C) store several times more energy than at 5 K. Keep stores on the primary (non-potable) side to avoid Legionella risk.
8. **Legionella rules (HSG274):** store DHW at 60 °C or above and distribute at 50 °C or above (55 °C in healthcare). Flag any arrangement that stores potable water at 20–50 °C. DHW secondary return must not pass through a preheat stage.
9. **Dry coolers and free cooling only work below the loop temperature.** A dry cooler is available only when ambient < fluid entering − approach (typically 5–8 K). Don't screen by the selection's rating condition. On a loop that is short of heat, a dry cooler can be almost worthless.
10. **Part load and sequencing matter.** Some machines lose COP at part load (e.g. high-temperature screw DHW heat pumps at constant condenser flow); others gain (scroll chillers). Compare "lead unit at full load cycling against storage" with "equal load sharing". Flag heavily oversized plant: high plant-to-peak ratios mean long low-load running.
11. **Pumps often decide the real result.** Model them explicitly (affinity laws, variable speed, minimum speed, system curve, circuit ΔT). A 20–30 kW average pump load can cut a scheme's seasonal ratio by 20–25 %. Never report an efficiency without saying whether pumps are included.
12. **Fluids and freeze protection:**
    - Glycol increases pumping energy and reduces heat-exchanger performance.
    - Directly connecting circuits with different fluids, or different pressurisation units, isn't acceptable.
    - Compare three options: glycol throughout; water in the building with a roof-level glycol heat exchanger; water with freeze protection (trace heating doesn't protect the unit's own heat exchanger during a power cut).
13. **Allocation conventions change the per-service ratios, not the total.** Always report the system-level combined ratio alongside per-service figures, under at least two conventions:
    - **pro-rata:** generation electricity split by heat extracted
    - **incremental heat recovery:** the cooling service keeps its stand-alone electricity; the extra compressor energy for heat recovery is charged to the heat it serves
14. **Targets have a headroom budget.** For example, a cooling target close to the machine's own EER leaves almost no allowance for rejection energy. Compute and show the allowed parasitic electricity for each target.

Add any further rules you think are standard good practice. Mark them as yours, with a reference.

---

## 4. Arrangement generator

Build arrangements as **composable hydraulic graphs**: components connected by circuits, with control modes. Users can draw or edit them. The generator proposes candidates automatically from the stream data and the recovery analysis (§2.2).

### 4.1 Building blocks (component library)
- **Heat pumps:**
  - water-to-water, reversible on the refrigerant or water side
  - 4-pipe / multi-purpose heat recovery
  - high-temperature DHW
  - CO₂ transcritical
  - air-to-water (2-pipe reversible, 4-pipe)
  - in-unit residential WSHPs
- **Chillers:** with condenser heat recovery, desuperheaters, dry coolers, cooling towers, adiabatic coolers
- **Loops:** ambient loop at fixed or floating temperature; 2-pipe or 4-pipe distribution; campus or network connections
- **Heat exchangers:** plate (counterflow, set approach or ε-NTU), wastewater heat exchangers, run-around coils
- **Stores:**
  - stratified multi-node (≥ 5 nodes) with inlet and outlet heights
  - simple 1-node mode
  - phase-change stores (optional)
  - calorifiers with coils
- **Ground and borehole field** (simple g-function or fixed-temperature mode)
- **Electric backup:** immersions, direct electric heating
- **Pumps and valves:** changeover pairs, mixing and diverting valves, PICVs

### 4.2 Arrangement archetypes the generator should propose and test
1. **Shared ambient loop:** all heat pumps on the loop; balance by air-source / dry cooler / ground.
2. **Loop plus heat recovery to an LTHW buffer:** cooling units condense into a stratified buffer that serves space heating first.
3. **Heat recovery to DHW preheat** (heat exchanger on the cold feed), with a DHW heat pump topping up from the loop.
4. **Heat recovery as the DHW heat pump's source,** with a changeover back to the loop.
5. **Cascade** (included as a known-poor baseline, so the comparison is explicit).
6. **4-pipe heat-recovery chiller** linking CHW and LTHW directly.
7. **CO₂ heat pump for DHW make-up,** plus separate reheat for circulation losses.
8. **External sources:** wastewater, ground or neighbouring rejection feeding the loop.
9. **Temperature strategy variants:** floating the loop seasonally; lower LTHW flow temperature; DHW storage at 60 vs 65 °C.
10. **Rejection variants:** with or without dry cooler; air-source vs ground rejection.

For each candidate the generator records **why** it was proposed (which recovery opportunity it targets) and the **assumptions it relies on**.

### 4.3 Controls and dispatch
- Per-unit mode selection each hour: heating, cooling-reject, cooling-heat-recovery, off. Minimum run times and changeover penalties.
- Configurable **priority order for recovered heat**, e.g. space heating → preheat → store → DHW heat pump source → reject.
- Sequencing strategies (rule 10).
- Store dispatch: rule-based by default. Optional look-ahead optimisation (e.g. a 24 h rolling horizon) if it's tractable.

---

## 5. Simulation engine
- 8,760 hourly steps, with optional sub-hourly steps for storage dispatch.
- Energy balance closed at every node every hour; the residual is reported, and tests assert it is below 1e-6 kWh.
- Fluid properties (water, ethylene or propylene glycol at a set concentration): cp, density, viscosity, and their effect on pump power and heat-exchanger performance.
- Weather: EPW and CIBSE TRY/DSY formats. Mains water temperature by month.
- Pumps: per circuit, with design flow, head, efficiency, variable or constant speed, affinity law and system curve. Includes pumps built into the equipment.
- Envelope checks: source and sink temperature limits, minimum and maximum flows, maximum condensing temperature. Every violation is counted in hours and surfaced.

---

## 6. Scenario planning and optimisation
- **Scenario sets:** demand variants (e.g. DHW low / central / high, occupancy, climate files), plant variants and control variants, all run as one batch.
- **Sweeps and optimisation** over:
  - loop temperatures and floating limits
  - circuit ΔTs
  - store volumes
  - preheat split temperature
  - DHW storage temperature
  - unit counts and sizes
  - sequencing
  - rejection plant
  - glycol strategy
  - pump heads
- **Objectives:** minimum electricity; maximum combined seasonal ratio; minimum carbon (annual or hourly grid factors, user-supplied); minimum operating cost (tariffs, including time-of-use); meet all targets at minimum capital proxy.
- **Capital proxy:** user-editable cost table (£ per kW, £ per litre, £ per item). Pareto plots of efficiency or carbon against capex, clearly labelled indicative.
- **Sensitivity:** tornado charts on key assumptions; optional Monte Carlo.
- Use NumPy, SciPy and pandas. Vectorise where possible. A typical run of one arrangement over 8,760 hours should take under a second; a sweep of a few hundred cases should take under a few minutes.

---

## 7. User workflow and UI
Streamlit is fine for v1; propose a better option if you have one. Ship a CLI too.

1. **Project setup:** site, weather, targets (per-service seasonal ratios, carbon, cost), boundary preferences.
2. **Loads:** import CSV or XLSX (with column mapping), or synthesise from benchmarks (building type, area, occupancy, editable profiles). Validation and warnings, e.g. flat DHW, peaks out of proportion to installed plant.
3. **Recovery analysis:** hourly composite curves, opportunity by temperature band, coincidence factor, effect of storage.
4. **Arrangements:** auto-proposed candidates, plus manual edit or draw. Each has an **auto-generated principle schematic** (SVG, coloured by circuit, flow solid and return dashed, with tags).
5. **Scenario results:**
   - ranked table against targets (traffic lights), every figure stating its boundary
   - Sankey diagram of energy flows
   - monthly bars; hourly heatmaps
   - part-load histograms per machine; envelope violations
   - insights panel from the rules engine, in plain engineering English
6. **Compare** 2–4 scenarios side by side, with deltas and an explanation of the drivers.
7. **Export:**
   - XLSX (inputs, hourly results, summary; formulas where practical so it can be audited in Excel)
   - CSV
   - HTML/PDF report suitable as an energy statement appendix

---

## 8. Data model and libraries
- `projects/<name>/project.yaml` for streams, sources, plant, arrangement graph, controls, targets, tariffs and scenarios.
- `library/equipment/*.yaml`: one file per product selection, holding the map tables with the source document, date, selection conditions and fluid. Write a **parser for manufacturer selection PDFs** (start with Trane Select Assist product reports: pdfplumber plus a table-extraction heuristic), with manual review and override.
- `library/generic/*.yaml`: generic technology maps (Carnot-efficiency parameters by type and refrigerant), clearly labelled as generic.
- `library/profiles/`: DHW and occupancy profile shapes, with sources cited.
- Every run is versioned (config hash, code version, timestamp) and its outputs are reproducible.

---

## 9. Validation and test fixtures
1. **Fixture 1 (real project, hotel plus residential on an ambient loop).** Supplied in `./inputs/` as a Milieu workbook with sheets `Inputs`, `Hourly calculation`, `Original CSV`, `London TRY` and `Plant schedule`. Write an importer, and a "workbook-compatible simple mode" that uses the same simplifications:
   - fixed COPs; same-hour ideal reuse; pumps off
   - ASHP COP from dry bulb, held constant above 10 °C
   - dry cooler cutoff fixed at 27 °C ambient

   Reproduce its results within 0.5 %:
   - service ratios: LTHW 3.01, DHW 2.74, CHW 6.44; overall 3.14
   - 926 MWh/yr electricity
   - annual demands: LTHW 82.9, CHW 634.2, DHW 2,195.3 MWh/yr

   Then show how the full-physics mode departs from it, and why.
2. **Regression expectations for the fixture.** Simplified assumptions:
   - heat-recovery cooling EER 4.5
   - DHW heat pump COP 5.3 on a 45/40 °C source
   - preheat covering 30 of the 55 K rise
   - incremental allocation; pumps excluded

   Within ±3 %:

   | Arrangement | Electricity, MWh/yr | Overall ratio |
   |---|---|---|
   | Shared loop (archetype 1) | ~926 | ~3.14 |
   | Cascade | ~1,040 | ~2.80 |
   | Heat recovery as DHW heat pump source, no loop changeover | ~956 | ~3.05 |
   | Same, with loop changeover | ~896 | ~3.25 |
   | Heat recovery → LTHW first → DHW preheat → DHW heat pump top-up from loop | ~808 | ~3.60 |
   | Same, plus 500–1,000 kWh stratified primary storage and 60 °C DHW | ~765–775 | ~3.75–3.8 |

3. **Synthetic fixtures** that test the rules engine in isolation:
   - perfectly balanced heating and cooling
   - all-heating
   - all-cooling
   - out-of-phase demands that need storage
   - a flat vs shaped DHW profile
4. **Unit tests:** map interpolation and extrapolation flags, energy balance closure, store conservation, pump affinity law, dry cooler availability, minimum-flow ΔT limits, Legionella triggers, allocation conventions summing to the system total.

---

## 10. Delivery phases
Stop and show me results at the end of each phase.

1. **Data model, stream importer, workbook importer and simple-mode engine.** Reproduce Fixture 1 (§9.1). CLI only.
2. **Hourly pinch and recovery analysis** (§2.2), with storage time-shift.
3. **Component library** with maps, part load, envelope checks and fluids; archetypes 1–5; rules engine v1.
4. **Stratified storage, dispatch and controls;** pumps and hydraulics; archetypes 6–10.
5. **Arrangement generator, scenario sets, optimiser, sensitivity, capital proxy.**
6. **UI, schematics, comparison views and exports.**

---

## 11. Working rules
- Ask before making assumptions that change results materially. Otherwise assume sensibly, log it in `ASSUMPTIONS.md` and in the UI, and continue.
- Tag every input and output as **[Manufacturer data] / [Project data] / [Generic] / [Assumption] / [Extrapolated]**.
- Never show an efficiency without its boundary: pumps in or out, allocation convention, DHW profile type, simple or full mode.
- Document equations with units and references (EN 14511/14825, CIBSE guides, HSG274, manufacturer data). Engineers will audit the code.
- Ask before adding heavy dependencies. Python 3.11+, locked dependencies, pytest, type hints.
- Don't over-build the UI before phases 1–3 are validated.

Start by proposing the package structure, data schema (streams, components, graph, scenarios) and the approach for the pinch/recovery analysis. Then confirm against Fixture 1 before writing the full engine.
