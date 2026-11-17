"""Read hourly tables (CSV or XLSX) into 8,760-row frames, with validation."""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from ambient_loop.core.series import HOURS


class TableError(ValueError):
    pass


def read_table(path: Path, sheet: str | None = None, header_row: int = 0) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in (".csv", ".txt"):
        df = pd.read_csv(path, header=header_row)
    elif suffix in (".xlsx", ".xlsm"):
        df = pd.read_excel(path, sheet_name=sheet or 0, header=header_row, engine="openpyxl")
    else:
        raise TableError(f"unsupported table format: {path.name}")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def to_model_year(df: pd.DataFrame, drop_feb29: bool = True, source: str = "") -> pd.DataFrame:
    """Coerce a frame to exactly 8,760 rows. A leap year (8,784 rows) loses 29 February."""
    n = len(df)
    if n == HOURS:
        return df.reset_index(drop=True)
    if n == 8784 and drop_feb29:
        warnings.warn(f"{source}: 8,784 rows, dropping 29 February (hours 1416–1439)", stacklevel=2)
        return df.drop(index=df.index[1416:1440]).reset_index(drop=True)
    raise TableError(f"{source}: expected {HOURS} hourly rows, got {n}")


def hourly_column(df: pd.DataFrame, column: str, source: str = "") -> np.ndarray:
    if column not in df.columns:
        raise TableError(f"{source}: column '{column}' not found; have {list(df.columns)}")
    s = pd.to_numeric(df[column], errors="coerce")
    bad = int(s.isna().sum())
    if bad:
        raise TableError(f"{source}: column '{column}' has {bad} non-numeric or empty cells")
    return s.to_numpy(dtype=np.float64)
