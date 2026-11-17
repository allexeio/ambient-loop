"""Produce the concise report and the GLA calculation summary (HTML and PDF)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ambient_loop.arrangements.archetypes import ARCHETYPES
from ambient_loop.core.run import manifest
from ambient_loop.reporting.gla import calc_summary, concise_report, recommend, scop_sensitivity
from ambient_loop.reporting.pdf import html_to_pdf
from ambient_loop.scenarios.economics import Economics
from ambient_loop.scenarios.run import arrangements_for, load_context, run_many
from ambient_loop.scenarios.sensitivity import tornado


def make_reports(project_file: Path, proposed: str | None = None, ids: list[str] | None = None, out: Path | None = None,
                 pdf: bool = True, workers: int | None = None, carbon: float = 0.136, tariff: float = 0.25) -> dict[str, Any]:
    ctx = load_context(project_file)
    p = ctx.project
    if ids:
        arrs = arrangements_for(ctx, ids)
    elif p.archetypes or p.arrangements:
        arrs = arrangements_for(ctx)
    else:
        from ambient_loop.arrangements.generator import propose
        from ambient_loop.scenarios.study import _totals
        L, C, D = _totals(ctx)
        arrs = [a for a, _ in propose(ctx.site, ctx.recovery, L, C, D, bool(p.site.get("ground")), bool(p.site.get("greywater_column")))]
    if proposed and proposed not in [a.id for a in arrs]:
        arrs += arrangements_for(ctx, [proposed]) if proposed in ARCHETYPES else []
    cases = run_many(project_file, arrs, workers, ctx)
    econ = Economics.default(p.capex, carbon, tariff)
    best, reasons = recommend(cases, ctx, econ)
    chosen = next((c for c in cases if c.arrangement.id == proposed), best) if proposed else best
    if proposed and chosen is not best:
        reasons = reasons + [f"The calculation summary covers the proposed {chosen.arrangement.id}, as instructed."]
    _, trows = tornado(ctx, "", custom=best.arrangement)
    man = manifest(p.model_dump(mode="json"), [project_file, *ctx.files])
    meta = [f"run {man['config_hash']}", man["timestamp_utc"][:10], f"code {man['code_version']} ({man['git_sha']})"]
    out = out or ctx.base / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    cr = out / "concise_report.html"
    cr.write_text(concise_report(ctx, cases, best, reasons, econ, trows, meta))
    sens = scop_sensitivity(ctx, chosen)
    safe = chosen.arrangement.id.replace("+", "_")
    cs = out / f"calculation_summary_{safe}.html"
    cs.write_text(calc_summary(ctx, chosen, sens, meta, man, carbon=carbon, tariff=tariff))
    res: dict[str, Any] = {"concise_html": cr, "summary_html": cs, "recommended": best.arrangement.id,
                           "proposed": chosen.arrangement.id, "cases": cases}
    if pdf:
        res["concise_pdf"] = html_to_pdf(cr, cr.with_suffix(".pdf"))
        res["summary_pdf"] = html_to_pdf(cs, cs.with_suffix(".pdf"))
    return res
