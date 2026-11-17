"""HTML → PDF through the bundled Chromium (Playwright for Node). Returns None if unavailable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "html2pdf.js"


def html_to_pdf(html_path: Path, pdf_path: Path) -> Path | None:
    if shutil.which("node") is None:
        return None
    r = subprocess.run(["node", str(SCRIPT), str(html_path), str(pdf_path)], capture_output=True, text=True, timeout=120)
    return pdf_path if r.returncode == 0 and pdf_path.exists() else None
