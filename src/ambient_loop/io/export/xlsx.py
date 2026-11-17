"""XLSX export: inputs, hourly results and a summary built from formulas, for audit in Excel.

Sheets:
  README     boundary, run manifest, conventions, how the formulas work
  Inputs     project streams, arrangement (flattened), site basis, assumptions in force
  Hourly     8,760 rows: loads, temperatures, every plant electricity meter, key heat flows
  Summary    annual totals as =SUM() over the Hourly columns; ratios as formulas
  Findings   rules-engine output
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ambient_loop.reporting.full_summary import CaseResult
from ambient_loop.reporting.scenario_report import assumptions_rows
from ambient_loop.schema.project import Project

BOLD = Font(bold=True)
HEAD = PatternFill("solid", fgColor="E8E8E8")


def _flatten(d: object, prefix: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(d, dict):
        for k, v in d.items():
            out += _flatten(v, f"{prefix}{k}.")
    elif isinstance(d, list) and d and isinstance(d[0], dict):
        for i, v in enumerate(d):
            out += _flatten(v, f"{prefix}{i}.")
    else:
        out.append((prefix.rstrip("."), json.dumps(d) if isinstance(d, (list, dict)) else str(d)))
    return out


def export_case(project: Project, c: CaseResult, demand_kW: dict[str, np.ndarray], dry_bulb: np.ndarray,
                manifest: dict[str, str], path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    b = c.system.boundary
    lines = [f"{project.name}: {c.arrangement.id} {c.arrangement.title}", "",
             f"Boundary: {b.short()}", "System ratio = (heat + coolth delivered) / total electricity.",
             "Summary sheet values are Excel formulas over the Hourly sheet, so every total can be traced.",
             "Units: kWh per hour (numerically = mean kW); MWh in Summary.", ""] + [f"{k}: {v}" for k, v in manifest.items()]
    for i, t in enumerate(lines, 1):
        ws.cell(i, 1, t)
    ws["A1"].font = Font(bold=True, size=13)

    wi = wb.create_sheet("Inputs")
    wi.append(["Section", "Field", "Value"])
    for s in project.streams:
        wi.append(["stream", s.id, f"{s.kind.value}; service {s.service_name}; {s.T_in_C}→{s.T_out_C} °C; tag {s.tag.value}"])
    for k, v in _flatten(c.arrangement.model_dump(mode="json")):
        wi.append(["arrangement", k, v])
    for k, v in _flatten(project.site):
        wi.append(["site", k, v])
    for row in assumptions_rows():
        wi.append(["assumption", row[0], f"{row[1]} | {row[2]} | {row[3]}"])
    for cell in wi[1]:
        cell.font, cell.fill = BOLD, HEAD

    wh = wb.create_sheet("Hourly")
    led = c.result.ledger
    cols: list[tuple[str, np.ndarray]] = [("hour", np.arange(8760, dtype=float)), ("dry_bulb_C", dry_bulb)]
    svc = {s.id: s.service_name for s in project.streams}
    for sid, q in demand_kW.items():
        cols.append((f"load_{svc[sid]}_{sid}_kW", q))
    for k in ("T_loop_flow", "T_loop_return"):
        if k in led.derived:
            cols.append((k + "_C", led.derived[k]))
    elec_cols = []
    for k, v in sorted(led.elec.items()):
        if v.sum() > 0:
            cols.append((f"W_{k}_kWh", v))
            elec_cols.append(len(cols))
    for k in ("cond_hr", "cond_rej", "hr_L", "hr_P", "ashp_d", "dc_r", "ac_r", "imm", "L_backup", "buf_ch", "buf_dis"):
        v = led.derived.get(f"o:{k}")
        if v is not None and v.sum() > 0:
            cols.append((f"Q_{k}_kWh", v))
    wh.append([n for n, _ in cols])
    data = np.column_stack([v for _, v in cols])
    for row in data:
        wh.append([round(float(x), 4) for x in row])
    for cell in wh[1]:
        cell.font, cell.fill = BOLD, HEAD
    wh.freeze_panes = "A2"

    wsum = wb.create_sheet("Summary")
    wsum.append(["Quantity", "MWh/yr (formula)", "Note"])
    n = 8761
    load_rows = []
    for i, (name, _) in enumerate(cols, 1):
        if name.startswith("load_"):
            L = get_column_letter(i)
            wsum.append([name, f"=SUM(Hourly!{L}2:{L}{n})/1000", "useful energy delivered"])
            load_rows.append(wsum.max_row)
    first_e = wsum.max_row + 1
    for i in elec_cols:
        L = get_column_letter(i)
        wsum.append([cols[i - 1][0], f"=SUM(Hourly!{L}2:{L}{n})/1000", "electricity"])
    last_e = wsum.max_row
    wsum.append(["Total electricity", f"=SUM(B{first_e}:B{last_e})", "all meters"])
    tot = wsum.max_row
    wsum.append(["Total useful (heat + coolth)", "=" + "+".join(f"B{r}" for r in load_rows), ""])
    use = wsum.max_row
    wsum.append(["System ratio", f"=B{use}/B{tot}", b.short()])
    wsum.append([])
    wsum.append(["Per-service ratios (computed by the toolkit)", "", ""])
    for conv in ("pro_rata", "incremental", "isolated"):
        for s, m in sorted(c.per_service[conv].items()):
            wsum.append([f"{s} {conv}", round(m.value, 4), f"{m.numerator_kWh / 1e3:,.1f} / {m.denominator_kWh / 1e3:,.1f} MWh"])
    for cell in wsum[1]:
        cell.font, cell.fill = BOLD, HEAD
    wsum.column_dimensions["A"].width = 46

    wf = wb.create_sheet("Findings")
    wf.append(["Rule", "Severity", "Title", "Message", "Evidence"])
    for f in c.findings:
        wf.append([f.rule, f.severity, f.title, f.message, f.evidence])
    for cell in wf[1]:
        cell.font, cell.fill = BOLD, HEAD
    wb.save(path)
    return path


def export_csv(c: CaseResult, path: Path) -> Path:
    c.result.ledger.to_frame().to_csv(path)
    return path
