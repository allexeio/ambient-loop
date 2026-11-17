"""Run manifest: makes every result reproducible and traceable."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import ambient_loop


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def config_hash(config: dict[str, Any], input_files: list[Path]) -> str:
    """Hash of the canonical config plus the content of every input file it refers to."""
    h = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode())
    for p in sorted(input_files):
        h.update(p.name.encode())
        h.update(sha256_file(p).encode())
    return h.hexdigest()[:16]


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def manifest(config: dict[str, Any], input_files: list[Path]) -> dict[str, str]:
    return {
        "config_hash": config_hash(config, input_files),
        "code_version": ambient_loop.__version__,
        "git_sha": git_sha(),
        "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }
