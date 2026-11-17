"""Command-line interface.

  ambient-loop run projects/<name>/project.yaml
  ambient-loop inspect-workbook inputs/<file>.xlsx
  ambient-loop import-workbook inputs/<file>.xlsx --out projects/<name>
  ambient-loop compare-workbook projects/<name>/project.yaml
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer
import yaml

from ambient_loop.core.run import manifest
from ambient_loop.io.milieu_workbook import import_workbook, inspect_workbook, write_project
from ambient_loop.reporting.allocation import Summary, summarise
from ambient_loop.reporting.compare import compare
from ambient_loop.schema.project import load_project
from ambient_loop.sim.inputs import build_inputs
from ambient_loop.sim.simple_mode import run_simple

app = typer.Typer(add_completion=False, no_args_is_help=True)
DEFAULT_MAPPING = (
    Path(__file__).resolve().parents[2] / "library/mappings/milieu_ambient_loop_workbook.yaml"
)


def _print_summary(s: Summary, warnings: list[str]) -> None:
    typer.echo("\nDemand (useful output)")
    for k, v in s.demand_MWh.items():
        typer.echo(f"  {k:<6} {v:>10,.1f} MWh")
    typer.echo(f"\nElectricity total {s.electricity_MWh:,.1f} MWh")
    for k, v in s.elec_by_component_MWh.items():
        typer.echo(f"  {k:<22} {v:>10,.1f} MWh")
    typer.echo(f"\nLoop: reused {s.reused_MWh:,.1f} | residual deficit "
               f"{s.residual_deficit_MWh:,.1f} | residual surplus {s.residual_surplus_MWh:,.1f}"
               f" | unmet heat {s.unmet_heat_MWh:,.1f} | unmet reject {s.unmet_reject_MWh:,.1f}"
               " MWh")
    typer.echo(f"\n{s.system}")
    for conv, metrics in s.per_service.items():
        typer.echo(f"\n{conv}:")
        for m in metrics.values():
            typer.echo(f"  {m}")
    if s.flags_hours:
        typer.echo("\nFlags (hours):")
        for k, v in s.flags_hours.items():
            typer.echo(f"  {k:<32} {v:>5}")
    worst = max(s.residuals_kWh.values()) if s.residuals_kWh else 0.0
    typer.echo(f"\nMax energy-balance residual: {worst:.2e} kWh (tolerance 1e-6)")
    for w in warnings:
        typer.echo(f"WARNING {w}")


@app.command()
def run(project_file: Path, out: Path | None = None) -> None:
    """Run a project in simple mode and write hourly ledger, summary and manifest."""
    t0 = time.perf_counter()
    project, base = load_project(project_file)
    inputs, profile, warns, files = build_inputs(project, base)
    ledger = run_simple(project, inputs)
    s = summarise(project, inputs, ledger, profile)
    dt = time.perf_counter() - t0
    out = out or base / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    man = manifest(project.model_dump(mode="json"), [project_file, *files])
    ledger.to_frame().to_csv(out / "hourly_ledger.csv")
    (out / "manifest.json").write_text(json.dumps(man, indent=2))
    (out / "summary.json").write_text(json.dumps(_summary_json(s), indent=2))
    typer.echo(f"{project.name}: run {man['config_hash']} in {dt:.2f} s -> {out}")
    _print_summary(s, [f"[{w.code}] {w.message} ({w.evidence})" for w in warns])


def _summary_json(s: Summary) -> dict[str, object]:
    return {
        "demand_MWh": s.demand_MWh,
        "electricity_MWh": s.electricity_MWh,
        "elec_by_component_MWh": s.elec_by_component_MWh,
        "system": s.system.model_dump(mode="json"),
        "per_service": {c: {k: m.model_dump(mode="json") for k, m in d.items()}
                        for c, d in s.per_service.items()},
        "flags_hours": s.flags_hours,
        "residuals_kWh": s.residuals_kWh,
    }


@app.command()
def recovery(project_file: Path, out: Path | None = None) -> None:
    """Hourly pinch / recovery analysis of a project's loads (no plant), with HTML report."""
    from ambient_loop.recovery.pinch import analyse
    from ambient_loop.recovery.report import recovery_report, summarise_recovery

    t0 = time.perf_counter()
    project, base = load_project(project_file)
    inputs, _profile, _w, files = build_inputs(project, base)
    pr, skipped = analyse(project, inputs.demand_kW)
    s = summarise_recovery(project, pr, skipped)
    out = out or base / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    man = manifest(project.model_dump(mode="json"), [project_file, *files])
    (out / "recovery.json").write_text(json.dumps({"manifest": man, **s.to_json()}, indent=2))
    (out / "recovery.html").write_text(
        recovery_report(project, s, pr, [f"run {man['config_hash']}", man["timestamp_utc"],
                                         f"code {man['code_version']} ({man['git_sha']})"]))
    typer.echo(f"{project.name}: recovery analysis in {time.perf_counter() - t0:.2f} s -> {out}")
    for k, v in s.annual_MWh.items():
        typer.echo(f"  {k:<34} {v:>10,.1f} MWh/yr")
    typer.echo(f"  coincidence factor                 {s.coincidence:>10.3f}")


@app.command()
def scenarios(project_file: Path, ids: str = typer.Option("", help="comma-separated archetype ids; default: project list or all"),
              out: Path | None = None, workers: int = 0) -> None:
    """Full-physics comparison of arrangements, with rules engine and HTML report."""
    from ambient_loop.arrangements.archetypes import ARCHETYPES
    from ambient_loop.reporting.scenario_report import scenario_report
    from ambient_loop.scenarios.run import arrangements_for, load_context, run_many

    t0 = time.perf_counter()
    ctx = load_context(project_file)
    chosen = [i for i in ids.split(",") if i] or ctx.project.archetypes or list(ARCHETYPES)
    arrs = arrangements_for(ctx, chosen)
    cases = run_many(project_file, arrs, workers or None, ctx)
    out = out or ctx.base / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    man = manifest(ctx.project.model_dump(mode="json"), [project_file, *ctx.files])
    rec = ctx.recovery
    line = (f"Recovery screening: {rec.annual_MWh['Direct (HX only)']:,.0f} MWh/yr direct, "
            f"{rec.annual_MWh['Heat-pump assisted (delivered)']:,.0f} MWh/yr heat-pump assisted, coincidence "
            f"{rec.coincidence:.2f}.")
    html_ = scenario_report(ctx.project, cases, [f"run {man['config_hash']}", man["timestamp_utc"],
                                                 f"code {man['code_version']} ({man['git_sha']})"], line)
    (out / "scenarios.html").write_text(html_)
    rows = [c.row() for c in sorted(cases, key=lambda c: c.electricity_MWh)]
    (out / "scenarios.json").write_text(json.dumps({"manifest": man, "cases": rows,
        "findings": {c.arrangement.id: [f.__dict__ for f in c.findings] for c in cases}}, indent=2, default=str))
    typer.echo(f"{ctx.project.name}: {len(cases)} arrangements in {time.perf_counter() - t0:.1f} s -> {out}")
    for r in rows:
        typer.echo(f"  {r['id']:<14} {r['electricity_MWh']:>8,.1f} MWh/yr  ratio {r['system_ratio']:.2f}  {r['title']}")


@app.command("parse-equipment")
def parse_equipment(pdf: Path, model_id: str, label: str = "", out: Path | None = None) -> None:
    """Parse a manufacturer selection PDF into a library YAML candidate for review."""
    from ambient_loop.io.equipment_pdf import parse_pdf, write_library

    ex = parse_pdf(pdf)
    out = out or Path("library/equipment") / f"{model_id}.yaml"
    write_library(ex, out, model_id, label or model_id)
    typer.echo(f"Wrote {out} (review_required). Fields: {sorted(ex.fields)}; table rows: {len(ex.table)}")
    for w in ex.warnings:
        typer.echo(f"WARNING {w}")


@app.command("inspect-workbook")
def inspect_cmd(workbook: Path, rows: int = 8) -> None:
    """Print sheets and first rows of a workbook (to build a mapping for a new layout)."""
    typer.echo(inspect_workbook(workbook, rows))


@app.command("import-workbook")
def import_cmd(
    workbook: Path,
    out: Path = typer.Option(..., help="project folder to write"),
    mapping: Path = DEFAULT_MAPPING,
    name: str = "fixture1",
) -> None:
    """Import a Milieu ambient-loop workbook into a project folder."""
    imp = import_workbook(workbook, mapping)
    p = write_project(imp, out, name)
    typer.echo(f"Wrote {p} and loads/weather/reference files")
    for k, (v, ref) in imp.params.items():
        typer.echo(f"  {k:<18} {v:>12.6g}   {ref}")


@app.command("compare-workbook")
def compare_cmd(project_file: Path) -> None:
    """Run an imported project and check it against the workbook's own results."""
    project, base = load_project(project_file)
    inputs, profile, _w, _f = build_inputs(project, base)
    ledger = run_simple(project, inputs)
    s = summarise(project, inputs, ledger, profile)
    checks, hourly = compare(project, inputs, ledger, s, base)
    typer.echo(f"{'Check':<46}{'Ours':>12}{'Workbook':>12}{'Diff':>10}")
    for c in checks:
        typer.echo(f"{c.name:<46}{c.ours:>12.4f}{c.theirs:>12.4f}{c.rel:>9.4%} "
                   f"{'PASS' if c.ok else 'FAIL'}")
    typer.echo(f"\n{'Hourly series':<24}{'max |diff| kW':>14}{'ours MWh':>12}{'wb MWh':>12}")
    for h in hourly:
        typer.echo(f"{h.name:<24}{h.max_abs:>14.2e}{h.annual_ours / 1e3:>12.3f}"
                   f"{h.annual_theirs / 1e3:>12.3f}")
    ok = all(c.ok for c in checks)
    typer.echo(f"\nReproduction within 0.5 %: {'PASS' if ok else 'FAIL'}")
    (base / "outputs").mkdir(exist_ok=True)
    (base / "outputs" / "comparison.yaml").write_text(yaml.safe_dump(
        {"annual": [{"name": c.name, "ours": c.ours, "workbook": c.theirs, "rel": c.rel}
                    for c in checks],
         "hourly": [h.__dict__ for h in hourly]}, sort_keys=False))
    raise typer.Exit(0 if ok else 1)


if __name__ == "__main__":
    app()
