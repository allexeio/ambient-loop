"""Glue between the web state and the analysis pipeline."""

from __future__ import annotations

import io
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ambient_loop.core.run import manifest
from ambient_loop.loads.synth import building_types
from ambient_loop.reporting.gla import calc_summary, scop_sensitivity
from ambient_loop.reporting.pdf import html_to_pdf
from ambient_loop.scenarios.run import evaluate, load_context
from ambient_loop.studio import ai
from ambient_loop.studio import catalogue as C
from ambient_loop.studio.building import Building, FreshAir, Zone
from ambient_loop.studio.pipeline import Screen, build_model, flags_for, screen_all, temperature_sweep
from ambient_loop.studio.reports import Detail, comparison_report, detailed_report, file_note
from ambient_loop.studio.terminals import SPACE_TYPES, TERMINALS, TerminalChoice, propose_terminals
from ambient_loop.web import store

LOGO = Path(__file__).parent / "static" / "brand" / "labs-lockup-black.svg"


def building(s: dict[str, Any]) -> Building:
    fa = s.get("fresh_air", {})
    return Building(s["name"], s.get("location", ""), [Zone.from_dict(z) for z in s.get("zones", [])],
                    FreshAir(fa.get("mode", "included"), float(fa.get("ahu_heating_kWh") or 0), float(fa.get("ahu_cooling_kWh") or 0),
                             float(fa.get("heat_recovery") or 0.75)),
                    float(s.get("dhw_store_C", 60)), float(s.get("circulation_fraction", 0.25)))


def choices(s: dict[str, Any]) -> list[TerminalChoice]:
    if not s.get("terminals"):
        return propose_terminals(s.get("zones", []))
    return [TerminalChoice(t["zone"], t["terminal"], tuple(t["lthw"]) if t.get("lthw") else None,  # type: ignore[arg-type]
                           tuple(t["chw"]) if t.get("chw") else None, t.get("why", ""), t.get("alternatives", []))
            for t in s["terminals"]]


def benchmark_fill(s: dict[str, Any]) -> None:
    """Fill blank zone demands from the space-type benchmarks [A-055]."""
    bt = building_types()
    for z in s["zones"]:
        st = SPACE_TYPES.get(z.get("space_type", "other"), SPACE_TYPES["other"])
        p = bt[st.profile]
        a = float(z.get("area_m2") or 0)
        if not float(z.get("heating_kWh") or 0):
            z["heating_kWh"] = round(float(p["heating_kWh_m2"]) * a)
        if not float(z.get("cooling_kWh") or 0) and st.id != "residential":
            z["cooling_kWh"] = round(float(p["cooling_kWh_m2"]) * a)
        if not float(z.get("dhw_kWh") or 0):
            occ = float(z.get("occupants") or 0)
            z["dhw_kWh"] = round(float(p["dhw_litres_per_occupant_day"]) * occ * 4.186 * 50 / 3600 * 365 / 0.75)
        z.setdefault("notes", "")
        z["notes"] = (z["notes"] + " benchmark-filled").strip()


def hourly_from_upload(path: Path, mapping: dict[str, str]) -> dict[str, np.ndarray]:
    df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    out = {}
    for svc, col in mapping.items():
        if col and col in df.columns:
            v = pd.to_numeric(df[col], errors="coerce").fillna(0).to_numpy(float)
            if len(v) == 8784:
                v = np.r_[v[:1416], v[1440:]]
            if len(v) != 8760:
                raise ValueError(f"{col}: {len(v)} rows, need 8,760")
            out[svc] = v
    return out


def model_path(pid: str) -> Path:
    return store.pdir(pid) / "model" / "project.yaml"


def run_screening(pid: str) -> dict[str, Any]:
    s = store.load(pid)
    d = store.pdir(pid)
    hourly = None
    if s.get("hourly"):
        hourly = hourly_from_upload(d / "uploads" / s["hourly"]["file"], s["hourly"]["mapping"])
    b = building(s)
    ch = choices(s)
    p = build_model(b, ch, s["weather_id"], d / "model", hourly)
    res = screen_all(p, ch, b.gia_m2)
    s["screen"] = [asdict(r) for r in res]
    s["step"] = max(s.get("step", 1), 4)
    store.save(pid, s)
    return s


def _strategy(sid: str, s: dict[str, Any]) -> C.Strategy:
    sc = next(x for x in s["screen"] if x["id"] == sid)
    st = sc["strategy"]
    return C.Strategy(st["heat"], st["cool"], st["dhw"], st.get("balance", ""), sc["notes"], sc["compatible"])


def _sweep_opts(s: dict[str, Any], strat: C.Strategy) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    if strat.heat == "vrf":
        return [], []
    ch = choices(s)
    lthw = [c for c in ch if c.lthw and TERMINALS[c.terminal].medium != "refrigerant"]
    chw = [c for c in ch if c.chw and TERMINALS[c.terminal].medium != "refrigerant"]
    lo = sorted({tuple(o) for c in lthw for o in TERMINALS[c.terminal].lthw_options if o[0] >= max(x.lthw[0] for x in lthw) - 5})[:3] if lthw else []  # type: ignore[union-attr]
    co = sorted({tuple(o) for c in chw for o in TERMINALS[c.terminal].chw_options if o[0] <= min(x.chw[0] for x in chw) + 4})[:3] if chw else []  # type: ignore[union-attr]
    return [tuple(x) for x in lo], [tuple(x) for x in co]  # type: ignore[misc]


def details(pid: str, ids: list[str], with_sweep: bool = True) -> tuple[Any, list[Detail]]:
    s = store.load(pid)
    ctx = load_context(model_path(pid))
    hc, hd = flags_for(ctx)
    out = []
    for sid in ids:
        strat = _strategy(sid, s)
        arr = C.build(strat, ctx.site, hc, hd)
        case = evaluate(ctx, arr)
        sw = []
        if with_sweep:
            lo, co = _sweep_opts(s, strat)
            if lo:
                sw = temperature_sweep(ctx, strat, lo, co)
        scr = Screen(**next(x for x in s["screen"] if x["id"] == sid))
        out.append(Detail(scr, case, sw))
    return ctx, out


def _logo() -> str:
    return LOGO.read_text() if LOGO.exists() else ""


def _facts(ds: list[Detail]) -> str:
    from ambient_loop.reporting.gla import service_ratios
    lines = []
    for d in ds:
        sr = service_ratios(d.case, "conservative", False)
        lines.append(f"{d.screen.id} ({d.screen.title}): electricity {d.screen.electricity_MWh:.0f} MWh/yr, gas {d.screen.gas_MWh:.0f} MWh/yr, "
                     f"carbon {d.screen.carbon_t:.0f} tCO2e/yr, capex £{d.screen.capex_gbp / 1e3:.0f}k, roof {d.screen.roof_m2:.0f} m², "
                     f"plant room {d.screen.plantroom_m2:.0f} m², efficiencies " + ", ".join(f"{k} {v['ratio']:.2f}" for k, v in sr.items())
                     + ". Warnings: " + "; ".join(f.message for f in d.case.findings if f.severity != "info")[:600])
    return "\n".join(lines)


def build_comparison(pid: str) -> Path:
    s = store.load(pid)
    ctx, ds = details(pid, s["compare"])
    narrative = None
    if ai.available():
        try:
            narrative = ai.narrate("comparison summary for an engineer choosing between these strategies", _facts(ds))
        except Exception:  # noqa: BLE001
            narrative = None
    man = manifest(ctx.project.model_dump(mode="json"), [model_path(pid)])
    html = comparison_report(s["name"], ds, [f"run {man['config_hash']}", man["timestamp_utc"][:10]], narrative, _logo())
    out = store.pdir(pid) / "outputs" / "comparison.html"
    out.write_text(html)
    html_to_pdf(out, out.with_suffix(".pdf"))
    s["step"] = max(s.get("step", 1), 5)
    store.save(pid, s)
    return out


def build_final(pid: str) -> dict[str, Path]:
    s = store.load(pid)
    ctx, ds = details(pid, [s["selected"]])
    d = ds[0]
    narrative = None
    if ai.available():
        try:
            narrative = ai.narrate("summary for a file note recording the chosen energy strategy", _facts(ds))
        except Exception:  # noqa: BLE001
            narrative = None
    man = manifest(ctx.project.model_dump(mode="json"), [model_path(pid)])
    meta = [f"run {man['config_hash']}", man["timestamp_utc"][:10]]
    others = [Screen(**x) for x in s["screen"] if x["compatible"]]
    outd = store.pdir(pid) / "outputs"
    files = {"detailed": outd / "detailed_report.html", "filenote": outd / "file_note.html",
             "gla": outd / "gla_calculation_summary.html"}
    files["detailed"].write_text(detailed_report(s["name"], d, others, meta, narrative, _logo()))
    files["filenote"].write_text(file_note(s["name"], d, meta, narrative, _logo()))
    sens = scop_sensitivity(ctx, d.case)
    files["gla"].write_text(calc_summary(ctx, d.case, sens, meta, man, weather_label=s.get("weather_label", "")))
    for f in list(files.values()):
        html_to_pdf(f, f.with_suffix(".pdf"))
    s["step"] = 6
    store.save(pid, s)
    return files


def area_schedule(data: bytes, name: str) -> list[dict[str, Any]]:
    """Heuristic area-schedule reader: rows with a name/space column and an area column."""
    df = pd.read_csv(io.BytesIO(data)) if name.lower().endswith(".csv") else pd.read_excel(io.BytesIO(data))
    cols = {c.lower(): c for c in df.columns.astype(str)}
    ncol = next((cols[c] for c in cols if any(k in c for k in ("space", "zone", "room", "name", "use"))), None)
    acol = next((cols[c] for c in cols if "area" in c or "m2" in c or "m²" in c), None)
    if ncol is None or acol is None:
        return []
    zones = []
    for _, r in df.iterrows():
        a = pd.to_numeric(r[acol], errors="coerce")
        if pd.isna(a) or a <= 0:
            continue
        nm = str(r[ncol])
        low = nm.lower()
        st = ("hotel_bedroom" if "bed" in low or "guest" in low else "residential" if any(k in low for k in ("apart", "flat", "resi"))
              else "office" if "office" in low else "retail" if any(k in low for k in ("retail", "shop", "f&b")) else
              "hotel_public" if any(k in low for k in ("lobby", "restaurant", "bar", "public")) else "other")
        zones.append({"name": nm, "space_type": st, "area_m2": float(a), "occupants": 0, "heating_kWh": 0, "cooling_kWh": 0,
                      "dhw_kWh": 0, "notes": f"from {name}"})
    return zones
