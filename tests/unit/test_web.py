"""End-to-end walkthrough through the web app with Claude disabled."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MILIEU_DATA", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    w = tmp_path / "weather"
    w.mkdir()
    h = np.arange(8760)
    t = 11 - 8 * np.cos(2 * np.pi * (h / 8760 - 0.05)) + 4 * np.sin(2 * np.pi * (h % 24 - 9) / 24)
    (w / "test.csv").write_text("dry_bulb_C,RH_pct\n" + "\n".join(f"{x:.2f},70" for x in t))
    (w / "index.yaml").write_text("- {id: test, label: Synthetic test year, file: test.csv, kind: TRY}\n")
    from ambient_loop.web.app import app
    return TestClient(app)


def test_walkthrough(client: TestClient) -> None:
    assert client.get("/healthz").json()["weather_files"] == 1
    r = client.post("/projects", data={"name": "Test hotel", "location": "London", "weather_id": "test"}, follow_redirects=False)
    assert r.status_code == 303
    pid = re.search(r"/p/([a-f0-9]{12})/1", r.headers["location"]).group(1)  # type: ignore[union-attr]
    assert client.get(f"/p/{pid}/1").status_code == 200
    r = client.post(f"/p/{pid}/zones", data={"name": ["Bedrooms", "Apartments", ""], "space_type": ["hotel_bedroom", "residential", "other"],
                    "area_m2": ["8000", "4000", ""], "occupants": ["400", "150", ""], "notes": ["", "", ""]}, follow_redirects=False)
    assert r.headers["location"].endswith("/2")
    r = client.post(f"/p/{pid}/2", data={"heating_kWh": ["", ""], "cooling_kWh": ["", ""], "dhw_kWh": ["", ""], "fa_mode": "included",
                    "dhw_store_C": "60", "circulation_fraction": "0.25", "heat_recovery": "0.75", "action": "bench"}, follow_redirects=False)
    from ambient_loop.web import store
    s = store.load(pid)
    assert all(z["heating_kWh"] > 0 for z in s["zones"]) and s["zones"][1]["cooling_kWh"] == 0
    r = client.post(f"/p/{pid}/2", data={"heating_kWh": [str(z["heating_kWh"]) for z in s["zones"]],
                    "cooling_kWh": [str(z["cooling_kWh"]) for z in s["zones"]], "dhw_kWh": [str(z["dhw_kWh"]) for z in s["zones"]],
                    "fa_mode": "included", "dhw_store_C": "60", "circulation_fraction": "0.25", "heat_recovery": "0.75", "action": "save"},
                    follow_redirects=False)
    assert r.headers["location"].endswith("/3")
    page = client.get(f"/p/{pid}/3").text
    assert "4-pipe fan coil units" in page
    r = client.post(f"/p/{pid}/3", data={"zone": ["Bedrooms", "Apartments"], "terminal": ["fcu_4pipe", "ufh"], "lthw": ["45/40", "35/30"],
                    "chw": ["10/16", ""]}, follow_redirects=False)
    assert r.headers["location"].endswith("/4")
    r = client.post(f"/p/{pid}/4/run")
    assert r.status_code == 204, r.text
    s = store.load(pid)
    assert len(s["screen"]) > 10
    comp = [x["id"] for x in s["screen"] if x["compatible"]][:2]
    client.post(f"/p/{pid}/4/select", data={"ids": comp})
    r = client.post(f"/p/{pid}/5/build")
    assert r.status_code == 204, r.text
    assert client.get(f"/p/{pid}/out/comparison.html").status_code == 200
    client.post(f"/p/{pid}/5/select", data={"sid": comp[0]})
    r = client.post(f"/p/{pid}/6/build")
    assert r.status_code == 204, r.text
    for k in ("detailed_report", "file_note", "gla_calculation_summary"):
        assert client.get(f"/p/{pid}/out/{k}.html").status_code == 200
    assert client.get(f"/p/{pid}/out/../project.json").status_code != 200
    assert client.get(f"/p/{pid}/6").status_code == 200
