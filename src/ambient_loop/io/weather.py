"""Weather readers. Phase 1 needs dry bulb only.

EPW: EnergyPlus weather format. There are 8 header lines; dry bulb (°C) is field 7 (index 6).
CIBSE TRY/DSY files are licensed and supplied by the user, either as EPW (CIBSE now issues EPW
versions) or as a table with a mapped dry-bulb column (e.g. the workbook's "London TRY" sheet).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ambient_loop.core.series import FloatArray
from ambient_loop.io.tables import hourly_column, read_table, to_model_year


def read_epw_dry_bulb(path: Path) -> FloatArray:
    df = pd.read_csv(path, skiprows=8, header=None)
    df = to_model_year(df, source=path.name)
    db = pd.to_numeric(df[6], errors="coerce").to_numpy(dtype=np.float64)
    if np.isnan(db).any() or (db > 70).any() or (db < -70).any():
        raise ValueError(f"{path.name}: dry bulb missing or out of range (EPW missing = 99.9)")
    return db


def read_table_dry_bulb(path: Path, column: str, sheet: str | None, header_row: int) -> FloatArray:
    df = to_model_year(read_table(path, sheet, header_row), source=path.name)
    return hourly_column(df, column, source=path.name)
