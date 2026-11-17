# ambient-loop

Milieu's heat recovery and ambient loop scenario toolkit. It is project-agnostic: give it
hourly loads with their temperatures and it finds the recovery potential, proposes hydraulic
arrangements, simulates each one hour by hour with temperature- and part-load-dependent plant,
ranks them on efficiency, carbon, cost and a capital proxy, and explains why.

Status: **v1, all six phases built.** Ready for the assumptions review before internal launch
(see `ASSUMPTIONS.md`, priority table at the top).

## Quick start

```bash
uv sync
uv run ambient-loop import-workbook inputs/fixture1.xlsx --out projects/fixture1   # or write project.yaml by hand
uv run ambient-loop new-project my_site --loads loads.csv --map "LTHW=Heating kW,CHW=Cooling kW,DHW=DHW kW" --weather London_TRY.epw
uv run ambient-loop report projects/my_site/project.yaml                         # options ladder + GLA calculation summary (PDF)
uv run ambient-loop study projects/fixture1/project.yaml                         # the whole workflow, HTML report
uv run ambient-loop ui                                                           # Streamlit interface
uv run pytest
```

## Commands

| Command | What it does |
|---|---|
| `import-workbook` / `inspect-workbook` | Read a Milieu ambient-loop workbook into a project, keeping cell references. |
| `run` / `compare-workbook` | Workbook-compatible simple mode, and its reproduction check. |
| `recovery` | Hourly pinch analysis: direct and heat-pump-assisted potential, lift, coincidence, storage surface. |
| `scenarios` | Full-physics run of chosen archetypes, with the rules engine and a ranked report. |
| `study` | Recovery → generator → scenarios → carbon, cost, capex → sensitivity → demand variants → storage sweep → reports, XLSX, CSV. |
| `optimise` | Differential evolution over arrangement fields, e.g. `buffer.volume_m3=1:40`. |
| `departure` | Step-by-step attribution from the workbook simple mode to full physics (brief §9.1). |
| `parse-equipment` | Manufacturer selection PDF to a library YAML candidate, marked for review. |
| `new-project` | Create a project from hourly loads (CSV/XLSX with column mapping) or from benchmark synthesis (building type, GIA, occupants). |
| `report` | Concise options report and the GLA calculation summary substantiating the SBEM/BRUKL SCOP/SEER inputs (HTML + PDF). |
| `ui` | Streamlit front end. |

## Read these

- `docs/BRIEF.md`: the brief
- `docs/DESIGN.md`: architecture and schema
- `docs/EQUATIONS.md`: every equation, with units and references
- `docs/FIXTURE1_MAPPING.md`: workbook ↔ model mapping, and the reproduction result
- `ASSUMPTIONS.md`: the assumptions register, with review priorities

## Layout

```
src/ambient_loop/
  core/          provenance tags, metrics with boundaries, run manifests
  schema/        project and arrangement schemas (pydantic)
  io/            loads, weather, workbook importer, equipment PDF parser, XLSX/CSV export
  recovery/      hourly pinch, heat-pump matching, storage shift, report
  physics/       fluids, performance models, stores, pumps, rejection, ground, library loader
  sim/           ledger, simple mode, full mode
  arrangements/  archetypes 1–10 and the generator
  rules/         rules and insights engine (brief rules 1–14 plus additions)
  scenarios/     runner, sweeps, optimiser, sensitivity, scenario sets, economics, study
  reporting/     allocation, HTML reports, schematic SVG, Sankey, compare
  validation/    fixture bases, simple-to-full departure
  ui/            Streamlit app
library/         generic and manufacturer performance data, fluids, costs, workbook mapping
```

Client data (`inputs/`, imported projects) is git-ignored.
