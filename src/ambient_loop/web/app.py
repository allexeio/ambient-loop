"""Milieu Labs / Energy Strategy: six-step walkthrough web app (FastAPI + Jinja + htmx).

Run with `ambient-loop web` or `uvicorn ambient_loop.web.app:app`. Data lives under $MILIEU_DATA
(projects, weather, rates); nothing client-specific is stored in the package.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ambient_loop.studio import ai
from ambient_loop.studio.data import rates_are_placeholder, weather_files
from ambient_loop.studio.terminals import SPACE_TYPES, TERMINALS, circuit_temperatures
from ambient_loop.web import store
from ambient_loop.web import workflow as W

HERE = Path(__file__).parent
app = FastAPI(title="Milieu Labs / Energy Strategy", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
tpl = Jinja2Templates(directory=HERE / "templates")
OUTPUTS = {"comparison", "detailed_report", "file_note", "gla_calculation_summary"}
MAX_UPLOAD = 40 * 1024 * 1024


def _ctx(request: Request, p: dict[str, Any] | None = None, step: int = 0, **kw: Any) -> dict[str, Any]:
    return {"request": request, "p": p, "step": step, "ai_on": ai.available(), "rates_placeholder": rates_are_placeholder(),
            "space_types": SPACE_TYPES, "terminals": TERMINALS, **kw}


def _load(pid: str) -> dict[str, Any]:
    try:
        return store.load(pid)
    except (KeyError, FileNotFoundError) as e:
        raise HTTPException(404, "Project not found") from e


def _go(pid: str, n: int, htmx: bool = False) -> Response:
    url = f"/p/{pid}/{n}"
    if htmx:
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def _err(request: Request, p: dict[str, Any], step: int, msg: str) -> HTMLResponse:
    return tpl.TemplateResponse(request, "error.html", _ctx(request, p, step, message=msg), status_code=400)


def _f(x: str | None) -> float:
    try:
        return float(x) if x not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)[:120] or "upload"


async def _save_upload(pid: str, f: UploadFile) -> tuple[str, bytes]:
    data = await f.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (40 MB limit)")
    name = _safe_name(f.filename or "upload")
    (store.pdir(pid) / "uploads" / name).write_bytes(data)
    return name, data


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return tpl.TemplateResponse(request, "index.html", _ctx(request, projects=store.all_projects(), weather=weather_files()))


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "ai": ai.available(), "weather_files": len(weather_files()), "placeholder_rates": rates_are_placeholder()}


@app.post("/projects")
def create_project(name: str = Form(...), location: str = Form(""), weather_id: str = Form("")) -> Response:
    pid = store.create(name.strip()[:120] or "Untitled", location.strip()[:120], weather_id)
    s = store.load(pid)
    s["weather_label"] = next((w.label for w in weather_files() if w.id == weather_id), "")
    store.save(pid, s)
    return _go(pid, 1)


@app.get("/p/{pid}/{n}", response_class=HTMLResponse)
def step_page(request: Request, pid: str, n: int) -> HTMLResponse:
    p = _load(pid)
    if n < 1 or n > 6:
        raise HTTPException(404)
    kw: dict[str, Any] = {}
    if n == 3:
        ch = W.choices(p)
        kw["choices"] = [asdict(c) for c in ch]
        kw["temps"] = circuit_temperatures(ch)
        kw["all_lthw"] = sorted({tuple(o) for t in TERMINALS.values() for o in t.lthw_options})
        kw["all_chw"] = sorted({tuple(o) for t in TERMINALS.values() for o in t.chw_options})
    if n == 5:
        out = store.pdir(pid) / "outputs"
        kw["has_report"] = (out / "comparison.html").exists()
        kw["has_pdf"] = (out / "comparison.pdf").exists()
        kw["titles"] = {x["id"]: x["title"] for x in p.get("screen", [])}
    if n == 6:
        out = store.pdir(pid) / "outputs"
        kw["files"] = (out / "gla_calculation_summary.html").exists()
        kw["pdfs"] = {k for k in OUTPUTS if (out / f"{k}.pdf").exists()}
        kw["title"] = next((x["title"] for x in p.get("screen", []) if x["id"] == p.get("selected")), "No strategy selected yet")
    return tpl.TemplateResponse(request, f"step{n}.html", _ctx(request, p, n, **kw))


# ---- step 1: describe the building ------------------------------------------------------------------
@app.post("/p/{pid}/1")
async def step1(request: Request, pid: str) -> Response:
    p = _load(pid)
    form = await request.form()
    p["description"] = str(form.get("description", ""))[:20000]
    docs: list[tuple[str, bytes]] = []
    for f in form.getlist("files"):
        if isinstance(f, UploadFile) and f.filename:
            name, data = await _save_upload(pid, f)
            docs.append((name, data))
            if name not in p["uploads"]:
                p["uploads"].append(name)
    if not docs:  # re-use earlier uploads for a re-analysis
        for name in p["uploads"]:
            fp = store.pdir(pid) / "uploads" / name
            if fp.exists():
                docs.append((name, fp.read_bytes()))
    zones: list[dict[str, Any]] = []
    for name, data in docs:
        if name.lower().endswith((".csv", ".xlsx", ".xls")):
            try:
                zones += W.area_schedule(data, name)
            except Exception:  # noqa: BLE001 - unreadable schedule: fall through to Claude/manual
                pass
    if ai.available() and (p["description"].strip() or docs):
        try:
            prop = ai.propose_building(p["description"], [d for d in docs if not d[0].lower().endswith((".xlsx", ".xls"))])
            p["ai"].update({"observations": prop.observations, "questions": prop.questions})
            if not zones:
                zones = [{**z.model_dump(), "notes": z.source} for z in prop.zones]
            if prop.fresh_air in ("included", "separate", "estimate"):
                p["fresh_air"]["mode"] = prop.fresh_air
        except Exception as e:  # noqa: BLE001
            p["ai"]["observations"] = [f"Claude was not available for this request ({type(e).__name__}); enter zones manually."]
    if zones:
        p["zones"] = zones
        p["terminals"] = []
    store.save(pid, p)
    return _go(pid, 1)


@app.post("/p/{pid}/zones")
async def zones(request: Request, pid: str) -> Response:
    p = _load(pid)
    form = await request.form()
    old = {z["name"]: z for z in p["zones"]}
    cols = [form.getlist(k) for k in ("name", "space_type", "area_m2", "occupants", "notes")]
    out = []
    for name, st, area, occ, notes in zip(*cols, strict=False):
        name = str(name).strip()
        if not name:
            continue
        z = dict(old.get(name, {"heating_kWh": 0, "cooling_kWh": 0, "dhw_kWh": 0}))
        z.update({"name": name[:120], "space_type": st if st in SPACE_TYPES else "other", "area_m2": _f(str(area)),
                  "occupants": _f(str(occ)), "notes": str(notes)[:300]})
        out.append(z)
    if not out:
        return _err(request, p, 1, "Add at least one zone with a name and area.")
    p["zones"] = out
    p["terminals"] = []
    p["step"] = max(p["step"], 2)
    store.save(pid, p)
    return _go(pid, 2)


# ---- step 2: demands ---------------------------------------------------------------------------------
@app.post("/p/{pid}/2")
async def step2(request: Request, pid: str) -> Response:
    p = _load(pid)
    form = await request.form()
    for z, h, c, d in zip(p["zones"], form.getlist("heating_kWh"), form.getlist("cooling_kWh"), form.getlist("dhw_kWh"), strict=False):
        z.update({"heating_kWh": _f(str(h)), "cooling_kWh": _f(str(c)), "dhw_kWh": _f(str(d))})
    mode = str(form.get("fa_mode", "included"))
    p["fresh_air"] = {"mode": mode if mode in ("included", "separate", "estimate") else "included",
                      "ahu_heating_kWh": _f(str(form.get("ahu_heating_kWh"))), "ahu_cooling_kWh": _f(str(form.get("ahu_cooling_kWh"))),
                      "heat_recovery": min(max(_f(str(form.get("heat_recovery"))) or 0.75, 0.0), 0.95)}
    p["dhw_store_C"] = _f(str(form.get("dhw_store_C"))) or 60.0
    p["circulation_fraction"] = min(max(_f(str(form.get("circulation_fraction"))), 0.0), 1.0)
    up = form.get("hourly")
    if isinstance(up, UploadFile) and up.filename:
        name, _ = await _save_upload(pid, up)
        mapping = {k: str(form.get(f"col_{k}", "")).strip() for k in ("LTHW", "CHW", "DHW")}
        try:
            W.hourly_from_upload(store.pdir(pid) / "uploads" / name, mapping)
        except Exception as e:  # noqa: BLE001
            store.save(pid, p)
            return _err(request, p, 2, f"Hourly file not used: {e}")
        p["hourly"] = {"file": name, "mapping": mapping}
    if form.get("action") == "bench":
        W.benchmark_fill(p)
        store.save(pid, p)
        return _go(pid, 2)
    if not any(_f(str(z.get(k))) for z in p["zones"] for k in ("heating_kWh", "cooling_kWh", "dhw_kWh")) and not p.get("hourly"):
        store.save(pid, p)
        return _err(request, p, 2, "Enter at least one demand, or use 'Fill blanks from benchmarks'.")
    p["terminals"] = []
    p["step"] = max(p["step"], 3)
    store.save(pid, p)
    return _go(pid, 3)


# ---- step 3: terminals ------------------------------------------------------------------------------
def _pair(x: str) -> list[float] | None:
    try:
        a, b = x.split("/")
        return [float(a), float(b)]
    except ValueError:
        return None


@app.post("/p/{pid}/3")
async def step3(request: Request, pid: str) -> Response:
    p = _load(pid)
    form = await request.form()
    prev = {c.zone: c for c in W.choices(p)}
    out = []
    for zone, term, lthw, chw in zip(form.getlist("zone"), form.getlist("terminal"), form.getlist("lthw"), form.getlist("chw"), strict=False):
        t = TERMINALS.get(str(term))
        if t is None:
            continue
        old = prev.get(str(zone))
        changed = old is None or old.terminal != t.id
        lt = None if not t.heats else (list(t.lthw) if changed and t.lthw else _pair(str(lthw)) or (list(t.lthw) if t.lthw else None))
        ct = None if not t.cools else (list(t.chw) if changed and t.chw else _pair(str(chw)) or (list(t.chw) if t.chw else None))
        z = next((z for z in p["zones"] if z["name"] == zone), {})
        if not _f(str(z.get("cooling_kWh"))):
            ct = None
        out.append({"zone": str(zone), "terminal": t.id, "lthw": lt, "chw": ct, "why": old.why if old else "",
                    "alternatives": old.alternatives if old else []})
    p["terminals"] = out
    p["step"] = max(p["step"], 4)
    store.save(pid, p)
    return _go(pid, 4)


@app.post("/p/{pid}/3/review", response_class=HTMLResponse)
def step3_review(request: Request, pid: str) -> HTMLResponse:
    p = _load(pid)
    if not ai.available():
        return HTMLResponse("<p class='note'>Claude assistance is off on this server.</p>")
    try:
        rv = ai.review_terminals(p["description"], [asdict(c) for c in W.choices(p)], {k: t.label for k, t in TERMINALS.items()})
        p["ai"]["terminal_review"] = rv.model_dump()
        store.save(pid, p)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<p class='note'>Review unavailable ({type(e).__name__}).</p>")
    return tpl.TemplateResponse(request, "_review.html", _ctx(request, p, 3))


# ---- step 4: screening ------------------------------------------------------------------------------
@app.post("/p/{pid}/4/run")
def step4_run(request: Request, pid: str) -> Response:
    p = _load(pid)
    if not p.get("weather_id"):
        return _err(request, p, 4, "This project has no weather file. Load weather files into MILIEU_DATA/weather and create a new project.")
    try:
        W.run_screening(pid)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<div class='panel'><div class='banner'>Screening failed: {type(e).__name__}: {str(e)[:400]}</div></div>", status_code=200,
                            headers={"HX-Retarget": "#sp", "HX-Reswap": "outerHTML"})
    return _go(pid, 4, htmx=True)


@app.post("/p/{pid}/4/select")
async def step4_select(request: Request, pid: str) -> Response:
    p = _load(pid)
    form = await request.form()
    valid = {x["id"] for x in p.get("screen", [])}
    ids = [str(i) for i in form.getlist("ids") if str(i) in valid][:3]
    if not ids:
        return _err(request, p, 4, "Tick between one and three strategies to compare.")
    p["compare"] = ids
    if p.get("selected") not in ids:
        p["selected"] = None
    p["step"] = max(p["step"], 5)
    store.save(pid, p)
    return _go(pid, 5)


# ---- step 5: comparison -----------------------------------------------------------------------------
@app.post("/p/{pid}/5/build")
def step5_build(pid: str) -> Response:
    _load(pid)
    try:
        W.build_comparison(pid)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<span class='note'>Report failed: {type(e).__name__}: {str(e)[:400]}</span>",
                            headers={"HX-Retarget": "#s5", "HX-Reswap": "outerHTML"})
    return _go(pid, 5, htmx=True)


@app.post("/p/{pid}/5/select")
def step5_select(request: Request, pid: str, sid: str = Form("")) -> Response:
    p = _load(pid)
    if sid not in p.get("compare", []):
        return _err(request, p, 5, "Choose one of the compared strategies.")
    p["selected"] = sid
    p["step"] = max(p["step"], 6)
    store.save(pid, p)
    return _go(pid, 6)


# ---- step 6: final reports --------------------------------------------------------------------------
@app.post("/p/{pid}/6/build")
def step6_build(pid: str) -> Response:
    p = _load(pid)
    if not p.get("selected"):
        return HTMLResponse("<span class='note'>Select a strategy at step 5 first.</span>", headers={"HX-Retarget": "#s6", "HX-Reswap": "outerHTML"})
    try:
        W.build_final(pid)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<span class='note'>Reports failed: {type(e).__name__}: {str(e)[:400]}</span>",
                            headers={"HX-Retarget": "#s6", "HX-Reswap": "outerHTML"})
    return _go(pid, 6, htmx=True)


@app.get("/p/{pid}/out/{name}")
def output(pid: str, name: str) -> FileResponse:
    _load(pid)
    stem, _, ext = name.partition(".")
    if stem not in OUTPUTS or ext not in ("html", "pdf"):
        raise HTTPException(404)
    f = store.pdir(pid) / "outputs" / name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, media_type="text/html" if ext == "html" else "application/pdf")
