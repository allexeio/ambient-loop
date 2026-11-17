"""Streamlit front end (v1, internal use).

Run with:  ambient-loop ui   (or: streamlit run src/ambient_loop/ui/app.py)

The engine is the same one the CLI uses; this file only arranges inputs and outputs. Every
figure shows its boundary, as in the reports.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import yaml

from ambient_loop.arrangements.archetypes import ARCHETYPES
from ambient_loop.arrangements.generator import propose
from ambient_loop.core.run import manifest
from ambient_loop.core.series import monthly_sum
from ambient_loop.io.export.xlsx import export_case
from ambient_loop.io.milieu_workbook import import_workbook, write_project
from ambient_loop.recovery.pinch import analyse
from ambient_loop.recovery.report import recovery_report, summarise_recovery
from ambient_loop.reporting.case_report import case_page, compare_page
from ambient_loop.reporting.sankey import sankey_svg
from ambient_loop.reporting.scenario_report import assumptions_rows
from ambient_loop.reporting.schematic import schematic
from ambient_loop.scenarios.economics import Economics, capex
from ambient_loop.scenarios.run import evaluate, load_context
from ambient_loop.schema.arrangement import Arrangement

ROOT = Path(__file__).resolve().parents[3]
MAPPING = ROOT / "library/mappings/milieu_ambient_loop_workbook.yaml"
st.set_page_config(page_title="Ambient loop toolkit", layout="wide")


@st.cache_resource(show_spinner="Loading project and running the recovery analysis…")
def ctx_for(path: str, mtime: float):  # type: ignore[no-untyped-def]
    del mtime
    return load_context(Path(path))


@st.cache_resource(show_spinner="Simulating 8,760 hours…")
def case_for(path: str, mtime: float, arr_json: str):  # type: ignore[no-untyped-def]
    ctx = ctx_for(path, mtime)
    return evaluate(ctx, Arrangement.model_validate_json(arr_json))


def svc_total(ctx, service: str) -> np.ndarray:  # type: ignore[no-untyped-def]
    svc = {s.id: s.service_name for s in ctx.project.streams}
    a = [v for k, v in ctx.inputs.demand_kW.items() if svc[k] == service]
    return np.sum(a, axis=0) if a else np.zeros(8760)


st.sidebar.title("Ambient loop toolkit")
page = st.sidebar.radio("Step", ["1 Project", "2 Loads", "3 Recovery", "4 Arrangements", "5 Results", "6 Compare",
                                 "7 Export", "Assumptions"])
projects = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "projects").glob("*/project.yaml"))
if "project" not in st.session_state and projects:
    st.session_state.project = projects[0]

if page == "1 Project":
    st.header("Project setup")
    if projects:
        st.session_state.project = st.selectbox("Project", projects, index=projects.index(st.session_state.get("project", projects[0])))
    with st.expander("Import a Milieu ambient-loop workbook"):
        up = st.file_uploader("Workbook (.xlsx)", type=["xlsx"])
        name = st.text_input("Project name", "new_project")
        if up and st.button("Import"):
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                f.write(up.getbuffer())
            imp = import_workbook(Path(f.name), MAPPING)
            out = write_project(imp, ROOT / "projects" / name, name)
            st.success(f"Imported to {out.relative_to(ROOT)}; parameters keep their cell references.")
            st.session_state.project = str(out.relative_to(ROOT))
    if "project" in st.session_state:
        p = ROOT / st.session_state.project
        ctx = ctx_for(str(p), p.stat().st_mtime)
        st.subheader(ctx.project.name)
        st.write(ctx.project.description)
        st.dataframe(pd.DataFrame([{"service": t.service, "target ratio": t.seasonal_ratio, "basis": t.allocation}
                                   for t in ctx.project.targets]), hide_index=True)
        st.caption("Edit targets, site basis and archetypes in project.yaml; the file is the record of the study.")
        st.code(p.read_text()[:4000], language="yaml")
    st.stop() if "project" not in st.session_state else None

if "project" not in st.session_state:
    st.info("Create or import a project first.")
    st.stop()
P = ROOT / st.session_state.project
CTX = ctx_for(str(P), P.stat().st_mtime)
MT = P.stat().st_mtime

if page == "2 Loads":
    st.header("Loads")
    svcs = sorted({s.service_name for s in CTX.project.streams})
    tot = {s: svc_total(CTX, s) for s in svcs}
    c = st.columns(len(svcs) + 1)
    for i, s in enumerate(svcs):
        c[i].metric(s, f"{tot[s].sum() / 1e3:,.1f} MWh/yr", f"peak {tot[s].max():,.0f} kW")
    c[-1].metric("DHW peak-hour share", f"{CTX.dhw_peak:.1%}" if np.isfinite(CTX.dhw_peak) else "–",
                 "flat (<6 %)" if CTX.dhw_peak < 0.06 else "shaped", delta_color="inverse")
    if CTX.dhw_peak < 0.06:
        st.warning("Rule 6: the DHW profile is effectively flat and will flatter heat recovery. Peak-hour draw-off is not "
                   "plant duty; test a shaped (CIBSE Guide G / CIPHE) profile before relying on recovery figures.")
    st.subheader("Monthly demand (MWh)")
    st.bar_chart(pd.DataFrame({s: monthly_sum(v) / 1e3 for s, v in tot.items()},
                              index=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]))
    st.subheader("Hourly profile (first week of each season)")
    wk = {k: np.r_[v[0:168], v[2160:2328], v[4344:4512], v[6552:6720]] for k, v in tot.items()}
    st.line_chart(pd.DataFrame(wk))
    st.subheader("Outdoor dry bulb")
    st.line_chart(pd.Series(CTX.inputs.dry_bulb_C, name="°C"))

elif page == "3 Recovery":
    st.header("Recovery potential (before choosing plant)")
    pr, sk = analyse(CTX.project, CTX.inputs.demand_kW)
    s = summarise_recovery(CTX.project, pr, sk)
    components.html(recovery_report(CTX.project, s, pr, ["interactive session"]), height=2600, scrolling=True)

elif page == "4 Arrangements":
    st.header("Arrangements")
    props = propose(CTX.site, CTX.recovery, svc_total(CTX, "LTHW"), svc_total(CTX, "CHW"), svc_total(CTX, "DHW"))
    default = CTX.project.archetypes or [a.id for a, _ in props]
    reasons = {a.id: r for a, r in props}
    chosen = st.multiselect("Arrangements to simulate", list(ARCHETYPES), default=[d for d in default if d in ARCHETYPES])
    st.session_state.chosen = chosen
    for aid in chosen:
        arr = ARCHETYPES[aid](CTX.site)
        with st.expander(f"{aid}: {arr.title}", expanded=aid == chosen[0] if chosen else False):
            if aid in reasons:
                st.info("Proposed because: " + reasons[aid])
            svg, notes = schematic(arr)
            components.html(f'<div style="max-width:1120px">{svg}</div>', height=720)
            for i, n in enumerate(notes, 1):
                st.caption(f"{i}. {n}")
            cap, items = capex(arr, Economics.default(CTX.project.capex).rates)
            st.write(f"Capital proxy (indicative): £{cap / 1e3:,.0f}k")
            st.code(yaml.safe_dump(arr.model_dump(mode="json"), sort_keys=False)[:6000], language="yaml")
    st.subheader("Custom arrangement (edit YAML)")
    txt = st.text_area("Paste or edit an arrangement", height=200, value=st.session_state.get("custom", ""))
    if st.button("Validate custom arrangement") and txt.strip():
        try:
            a = Arrangement.model_validate(yaml.safe_load(txt))
            st.session_state.custom = txt
            st.success(f"Valid: {a.id}. It will be included in Results.")
            components.html(schematic(a)[0], height=720)
        except Exception as e:  # noqa: BLE001
            st.error(str(e))

elif page in ("5 Results", "6 Compare", "7 Export"):
    chosen = st.session_state.get("chosen") or CTX.project.archetypes or ["A1", "A2+3", "A3", "A7"]
    arrs = [ARCHETYPES[a](CTX.site) for a in chosen if a in ARCHETYPES]
    if st.session_state.get("custom"):
        arrs.append(Arrangement.model_validate(yaml.safe_load(st.session_state.custom)))
    cases = [case_for(str(P), MT, a.model_dump_json()) for a in arrs]
    cases.sort(key=lambda c: c.electricity_MWh)
    if page == "5 Results":
        st.header("Scenario results")
        st.caption(f"Boundary: {cases[0].system.boundary.short()}. Ratios: pro-rata / incremental.")
        rows = []
        for c in cases:
            r = {"arrangement": c.arrangement.id, "title": c.arrangement.title, "electricity MWh/yr": round(c.electricity_MWh, 1),
                 "system ratio": round(c.system.value, 2), "pumps MWh/yr": round(c.pumps_MWh, 1)}
            for t in CTX.project.targets:
                m = c.per_service.get(t.allocation, {}).get(t.service)
                r[f"{t.service} ({t.allocation[:4]}) ≥{t.seasonal_ratio:g}"] = round(m.value, 2) if m else None
            r["warnings"] = sum(1 for f in c.findings if f.severity != "info")
            rows.append(r)
        df = pd.DataFrame(rows)

        def colour(v, target):  # type: ignore[no-untyped-def]
            if v is None:
                return ""
            return "background-color:#e2eddc" if v >= target else ("background-color:#fbe9c6" if v >= 0.95 * target else "background-color:#f6d5d1")

        sty = df.style.format(precision=2)
        for t in CTX.project.targets:
            col = f"{t.service} ({t.allocation[:4]}) ≥{t.seasonal_ratio:g}"
            sty = sty.map(lambda v, tt=t.seasonal_ratio: colour(v, tt), subset=[col])
        st.dataframe(sty, hide_index=True, width="stretch")
        pick = st.selectbox("Detail", [c.arrangement.id for c in cases])
        c = next(x for x in cases if x.arrangement.id == pick)
        tabs = st.tabs(["Energy flows", "Monthly", "Part load", "Insights", "Full page"])
        with tabs[0]:
            components.html(f'<div style="font-family:Raleway,Arial,sans-serif">{sankey_svg(c.result.ledger)}</div>', height=560)
        with tabs[1]:
            st.bar_chart(pd.DataFrame({k: monthly_sum(v) / 1e3 for k, v in c.result.ledger.elec.items() if v.sum() > 0},
                                      index=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]))
        with tabs[2]:
            for k, v in c.result.plr.items():
                st.write(k)
                st.bar_chart(pd.Series(np.histogram(np.clip(v, 0, 1), bins=10, range=(0, 1))[0],
                                       index=[f"{i / 10:.1f}" for i in range(10)]))
            st.write("Hours outside maps while running:", c.envelope_hours)
        with tabs[3]:
            for f in c.findings:
                (st.error if f.severity == "fail" else st.warning if f.severity == "warn" else st.info)(
                    f"**{f.rule} {f.title}.** {f.message}\n\n_{f.evidence}_")
        with tabs[4]:
            components.html(case_page(CTX.project, c, ["interactive session"]), height=3000, scrolling=True)
    elif page == "6 Compare":
        st.header("Compare 2–4 arrangements")
        sel = st.multiselect("Arrangements (first is the reference)", [c.arrangement.id for c in cases],
                             default=[c.arrangement.id for c in cases[:3]], max_selections=4)
        if len(sel) >= 2:
            cs = [next(c for c in cases if c.arrangement.id == s) for s in sel]
            components.html(compare_page(CTX.project, cs, ["interactive session"]), height=2400, scrolling=True)
    else:
        st.header("Export")
        man = manifest(CTX.project.model_dump(mode="json"), [P, *CTX.files])
        for c in cases:
            col = st.columns(3)
            buf = io.BytesIO()
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                export_case(CTX.project, c, CTX.inputs.demand_kW, CTX.inputs.dry_bulb_C, man, Path(f.name))
                buf.write(Path(f.name).read_bytes())
            col[0].download_button(f"{c.arrangement.id} XLSX (formulas)", buf.getvalue(), f"{c.arrangement.id}.xlsx")
            col[1].download_button(f"{c.arrangement.id} hourly CSV", c.result.ledger.to_frame().to_csv().encode(),
                                   f"{c.arrangement.id}_hourly.csv")
            col[2].download_button(f"{c.arrangement.id} report HTML", case_page(CTX.project, c, [man["config_hash"]]).encode(),
                                   f"{c.arrangement.id}.html")

elif page == "Assumptions":
    st.header("Assumptions register")
    st.caption("Every assumption the results rest on. Review before internal launch.")
    st.dataframe(pd.DataFrame(assumptions_rows(), columns=["ID", "Assumption", "Effect", "Status"]), hide_index=True,
                 width="stretch")
