"""Build hourly model inputs from a validated project."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ambient_loop.core.metric import DhwProfile
from ambient_loop.io.tables import hourly_column, read_table, to_model_year
from ambient_loop.io.weather import read_epw_dry_bulb, read_table_dry_bulb
from ambient_loop.loads.validation import Warning_, classify_dhw, validate_loads
from ambient_loop.schema.project import Project
from ambient_loop.sim.simple_mode import SimpleInputs


def build_inputs(
    project: Project, base: Path
) -> tuple[SimpleInputs, DhwProfile, list[Warning_], list[Path]]:
    """Returns (inputs, DHW profile class, warnings, input files used)."""
    lp = base / project.loads.file
    df = to_model_year(
        read_table(lp, project.loads.sheet, project.loads.header_row),
        project.loads.drop_feb29,
        source=lp.name,
    )
    demand = {
        s.id: hourly_column(df, s.duty_kW.column, source=lp.name) * s.duty_kW.scale
        for s in project.streams
    }

    w = project.weather
    wp = base / w.file
    if w.format == "epw":
        db = read_epw_dry_bulb(wp)
    else:
        if w.dry_bulb_column is None:
            raise ValueError("weather table needs dry_bulb_column")
        db = read_table_dry_bulb(wp, w.dry_bulb_column, w.sheet, w.header_row)

    services = {s.id: (s.service_name, demand[s.id]) for s in project.streams}
    warnings = validate_loads(services)
    dhw = [demand[s.id] for s in project.streams if s.service_name == "DHW"]
    profile = classify_dhw(np.sum(dhw, axis=0)) if dhw else DhwProfile.IMPORTED
    files = sorted({lp, wp})
    return SimpleInputs(demand_kW=demand, dry_bulb_C=db), profile, warnings, files


def build_full_inputs(project: Project, base: Path) -> tuple["FullInputs", DhwProfile, list[Warning_], list[Path]]:
    """Inputs for full mode: adds mains water temperature and any extra load-table columns."""
    from ambient_loop.core.series import as_hourly
    from ambient_loop.sim.full_mode import FullInputs

    si, profile, warns, files = build_inputs(project, base)
    mw = project.mains_water_C
    mains = as_hourly(mw if isinstance(mw, list) else mw.value)
    lp = base / project.loads.file
    df = to_model_year(read_table(lp, project.loads.sheet, project.loads.header_row), project.loads.drop_feb29,
                       source=lp.name)
    cols = {c: df[c].to_numpy(dtype=np.float64) for c in df.columns if np.issubdtype(df[c].dtype, np.number)}
    return FullInputs(si.demand_kW, si.dry_bulb_C, mains, None, cols), profile, warns, files
