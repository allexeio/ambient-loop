"""Run sets of arrangements for a project (in parallel), with rules and recovery context."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ambient_loop.arrangements.archetypes import ARCHETYPES, SiteBasis
from ambient_loop.core.metric import DhwProfile
from ambient_loop.loads.validation import dhw_peak_fraction
from ambient_loop.recovery.pinch import analyse
from ambient_loop.recovery.report import RecoverySummary, summarise_recovery
from ambient_loop.reporting.full_summary import CaseResult, run_case
from ambient_loop.rules.engine import Context, run_rules
from ambient_loop.schema.arrangement import Arrangement
from ambient_loop.schema.project import Project, load_project
from ambient_loop.sim.full_mode import FullInputs
from ambient_loop.sim.inputs import build_full_inputs


@dataclass
class ProjectContext:
    project: Project
    base: Path
    inputs: FullInputs
    profile: DhwProfile
    site: SiteBasis
    recovery: RecoverySummary
    dhw_peak: float
    files: list[Path]


def load_context(project_file: Path) -> ProjectContext:
    project, base = load_project(project_file)
    inp, prof, _w, files = build_full_inputs(project, base)
    pr, skipped = analyse(project, inp.demand_kW)
    rec = summarise_recovery(project, pr, skipped)
    dhw = [inp.demand_kW[s.id] for s in project.streams if s.service_name == "DHW"]
    peak = dhw_peak_fraction(np.sum(dhw, axis=0)) if dhw else float("nan")
    return ProjectContext(project, base, inp, prof, SiteBasis(**project.site), rec, peak, files)


def arrangements_for(ctx: ProjectContext, ids: list[str] | None = None) -> list[Arrangement]:
    p = ctx.project
    ids = ids or p.archetypes
    out = [ARCHETYPES[i](ctx.site) for i in ids]
    out += [Arrangement.model_validate(d) for d in p.arrangements]
    return out


def evaluate(ctx: ProjectContext, arr: Arrangement) -> CaseResult:
    c = run_case(ctx.project, arr, ctx.inputs, ctx.profile)
    c.findings = run_rules(Context(c, ctx.project, ctx.inputs.demand_kW, ctx.inputs.dry_bulb_C, ctx.recovery,
                                   ctx.dhw_peak, ctx.project.healthcare))
    return c


_CTX: ProjectContext | None = None


def _init(project_file: str) -> None:
    global _CTX
    _CTX = load_context(Path(project_file))


def _work(arr_json: dict[str, Any]) -> CaseResult:
    assert _CTX is not None
    return evaluate(_CTX, Arrangement.model_validate(arr_json))


def run_many(project_file: Path, arrs: list[Arrangement], workers: int | None = None,
             ctx: ProjectContext | None = None) -> list[CaseResult]:
    workers = workers if workers is not None else min(len(arrs), os.cpu_count() or 1, 8)
    if workers <= 1 or len(arrs) <= 1:
        ctx = ctx or load_context(project_file)
        return [evaluate(ctx, a) for a in arrs]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(str(project_file),)) as ex:
        return list(ex.map(_work, [a.model_dump() for a in arrs]))
