# ambient-loop

Milieu's heat recovery and ambient loop scenario toolkit. It is project-agnostic: you supply
hourly loads, it finds the recovery potential, proposes hydraulic arrangements, then simulates
and ranks them.

Status: **Phase 1** — data model, loads/weather import, workbook importer, workbook-compatible
simple mode, energy ledger, allocation conventions. Fixture 1 reproduced exactly.

## Use

```bash
uv sync
uv run ambient-loop import-workbook inputs/fixture1.xlsx --out projects/fixture1
uv run ambient-loop compare-workbook projects/fixture1/project.yaml
uv run ambient-loop run projects/fixture1/project.yaml
uv run pytest
```

## Read these

- `docs/BRIEF.md`: the brief
- `docs/DESIGN.md`: design, schema and the plan for later phases
- `docs/FIXTURE1_MAPPING.md`: workbook column ↔ model mapping, and the reproduction result
- `ASSUMPTIONS.md`: the assumptions register
