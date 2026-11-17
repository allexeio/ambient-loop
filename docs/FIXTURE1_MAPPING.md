# Fixture 1: workbook ↔ simple-mode mapping

This maps each column of the Milieu ambient-loop workbook's `Hourly calculation` sheet to the
toolkit quantity that reproduces it. The importer reads cached values by header text and label,
using `library/mappings/milieu_ambient_loop_workbook.yaml`. Every parameter keeps its cell
address as its provenance `ref`.

| Workbook column | Meaning | Toolkit |
|---|---|---|
| D, E, F | LTHW, CHW, DHW kW | stream duties (`loads.csv`) |
| G | dry bulb °C | `weather.csv` |
| H | ASHP heat COP: piecewise-linear on Inputs map, end values held | `Curve1D`; held hours flagged `outside_map` (workbook's AK) |
| J, K, L | unit kWe = Q / COP or EER | unit electricity |
| M, N | loop extraction = Q − W | `derived[extraction:*]` |
| O | loop rejection = Q + W | `derived[rejection:*]` |
| P | reused = f · min(M+N, O) | `derived[loop:reused]` |
| Q, R | residual heat / cooling | `loop:residual_deficit` / `loop:residual_surplus` |
| S | roof heating kWe = Q / H | `roof_ashp_heat` electricity |
| AM, AN | dry cooler: min(R, 800) if T ≤ 27 °C; × kWe/kW | `dry_cooler` flow and electricity |
| AO, T | remainder to CXAF at fixed EER | `roof_ashp_cool` |
| U, V | listed pumps × switch (Inputs B15) | `<plant>:pumps` meters, only when `pumps_included` |
| W, X, Y | allocated kWe | `PRO_RATA` allocation |
| AA, AB, AC | isolated kWe (no reuse, no dry cooler) | `ISOLATED` reference = pro-rata of a reuse-0, no-dry-cooler run |
| AG, AH, AI | workbook balance checks | `Ledger.residuals()` (stricter: per node and per component, every hour) |

Not reproduced: these are capacity diagnostics, not energy.

- AE, AF, AJ: units-needed counts
- AL: ASHP capacity per unit

They come back with the plant capacity checks in Phase 3.

## Result (17 Nov 2026 workbook)

`ambient-loop compare-workbook projects/fixture1/project.yaml` matches all 17 annual checks
exactly (0.0000 %). It matches all 11 hourly series to ≤ 3e-14 kW in every hour. The test is
`tests/regression/test_fixture1.py`; it skips when the workbook is absent.
