"""Recovery analysis summary and HTML report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ambient_loop.core.series import monthly_sum
from ambient_loop.recovery.pinch import PinchResult
from ambient_loop.recovery.storage import storage_surface
from ambient_loop.reporting import html as H
from ambient_loop.schema.project import Project


@dataclass
class RecoverySummary:
    annual_MWh: dict[str, float]
    monthly_MWh: dict[str, list[float]]
    by_band_MWh: list[tuple[float, float, float]]  # (band lo °C shifted, source, sink)
    lift_MWh: dict[float, float]
    coincidence: float
    storage_MWh: dict[tuple[float, int], float]
    pinch_median_C: float
    skipped_streams: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "annual_MWh": self.annual_MWh,
            "monthly_MWh": self.monthly_MWh,
            "by_band_MWh": [{"band_lo_C": a, "source": b, "sink": c} for a, b, c in self.by_band_MWh],
            "lift_MWh": {str(k): v for k, v in self.lift_MWh.items()},
            "coincidence": self.coincidence,
            "storage_extra_MWh": [{"capacity_kWh": c, "window_h": w, "extra_MWh": v}
                                  for (c, w), v in self.storage_MWh.items()],
            "pinch_median_C": self.pinch_median_C,
            "skipped_streams": self.skipped_streams,
        }


def summarise_recovery(project: Project, pr: PinchResult, skipped: list[str]) -> RecoverySummary:
    st = project.recovery.storage
    surf = storage_surface(pr.unmatched_surplus, pr.source_need_unmet, st.capacities_kWh,
                           st.windows_h, st.loss_fraction_per_h)
    series = {
        "Direct (HX only)": pr.direct,
        "Heat-pump assisted (delivered)": pr.hp_sink,
        "Unmatched deficit (generate)": pr.unmatched_deficit,
        "Unmatched surplus (reject)": pr.unmatched_surplus,
        "HP source heat used": pr.hp_source,
        "HP compressor work (generic)": pr.hp_work,
        "Total heat demand": pr.cold_total,
        "Total heat rejected by loads": pr.hot_total,
    }
    src_b = pr.source_by_band.sum(axis=0) / 1e3
    snk_b = pr.sink_by_band.sum(axis=0) / 1e3
    bands = [(float(lo), float(s), float(k)) for lo, s, k in zip(pr.band_lo_C, src_b, snk_b, strict=True)
             if s > 0.05 or k > 0.05]
    return RecoverySummary(
        annual_MWh={k: float(v.sum()) / 1e3 for k, v in series.items()},
        monthly_MWh={k: list(monthly_sum(v) / 1e3) for k, v in series.items()},
        by_band_MWh=bands,
        lift_MWh={k: v / 1e3 for k, v in pr.hp_lift_kWh.items()},
        coincidence=pr.coincidence,
        storage_MWh={k: v / 1e3 for k, v in surf.items()},
        pinch_median_C=float(np.nanmedian(pr.pinch_C)) if np.isfinite(pr.pinch_C).any() else float("nan"),
        skipped_streams=skipped,
    )


def recovery_report(project: Project, s: RecoverySummary, pr: PinchResult, meta: list[str]) -> str:
    rs = project.recovery
    a = s.annual_MWh
    r = H.Report(
        title=f"{project.name}: recovery potential",
        eyebrow="Ambient loop toolkit · Recovery analysis",
        lead=("Heat that one load rejects and another could use, before any plant is chosen. "
              "Every hour is analysed as its own pinch problem."),
        boundary=(f"Loads only, no plant. ΔT_min {rs.dT_min_K}. Heat-pump figures use a generic "
                  f"η = {rs.eta_carnot} of Carnot with {rs.hp_approach_K} K approaches [Generic] and "
                  "are screening values, not design values."),
        meta=meta,
    )
    r.section("Headline", H.kpis([
        (H.fmt(a["Direct (HX only)"], 0), "MWh/yr directly recoverable by heat exchange alone"),
        (H.fmt(a["Heat-pump assisted (delivered)"], 0), "MWh/yr deliverable by heat pumps from load surplus"),
        (H.fmt(a["Unmatched deficit (generate)"], 0), "MWh/yr of heat still to generate from outside"),
        (H.fmt(a["Unmatched surplus (reject)"], 0), "MWh/yr of surplus still to reject"),
        (f"{s.coincidence:.2f}" if np.isfinite(s.coincidence) else "–", "coincidence factor (1 = timing costs nothing)"),
    ]), H.p(_verdict(s), ""))
    months = H.MONTHS
    r.section("Month by month", H.bar_chart(months, {
        "Direct (HX only)": s.monthly_MWh["Direct (HX only)"],
        "Heat-pump assisted": s.monthly_MWh["Heat-pump assisted (delivered)"],
        "Unmatched deficit": s.monthly_MWh["Unmatched deficit (generate)"],
    }, "MWh"), H.p("Bars add up to total heat demand each month.", "note"))
    r.section(
        "Where the heat sits (shifted temperature bands)",
        H.bar_chart([f"{lo:.1f}" for lo, _, _ in s.by_band_MWh],
                    {"Source after direct recovery": [x for _, x, _ in s.by_band_MWh],
                     "Sink after direct recovery": [y for _, _, y in s.by_band_MWh]},
                    "MWh/yr", stacked=False),
        H.p("Band lower edge in °C on the shifted scale: hot streams sit ΔT_min/2 lower and cold "
            "streams ΔT_min/2 higher than their actual temperatures.", "note"),
    )
    lifts = sorted(s.lift_MWh)
    r.section("Lift the heat pumps would need",
              H.bar_chart([f"{k:.0f}–{k + 5:.0f} K" for k in lifts], {"Heat delivered": [s.lift_MWh[k] for k in lifts]}, "MWh/yr"),
              H.p("Minimum-lift matching: the warmest surplus serves the coolest demand first. A "
                  "wide spread of lifts suggests splitting duties, such as preheat plus top-up, "
                  "instead of one high lift (rules 1 and 2).", "note"))
    caps = sorted({c for c, _ in s.storage_MWh})
    wins = sorted({w for _, w in s.storage_MWh})
    rows = [[f"{c:,.0f}"] + [H.fmt(s.storage_MWh[(c, w)], 1) for w in wins] for c in caps]
    r.section("What storage adds", H.table(["Capacity kWh"] + [f"{w} h window" for w in wins], rows,
                                           numeric=list(range(1, len(wins) + 1))),
              H.p(f"Extra heat-pump source heat recovered (MWh/yr) by shifting unmatched surplus to "
                  f"later unmet need. Standing loss {rs.storage.loss_fraction_per_h:.1%}/h. Bands are "
                  "aggregated (assumption A-016).", "note"))
    r.section("Hourly pattern of the heat-pump opportunity", H.heatmap(pr.hp_sink, "kWh"))
    if s.skipped_streams:
        r.section("Streams left out", H.p("No temperatures given for: " + ", ".join(s.skipped_streams)))
    return r.render()


def _verdict(s: RecoverySummary) -> str:
    a = s.annual_MWh
    dem, rej = a["Total heat demand"], a["Total heat rejected by loads"]
    if dem <= 0 and rej <= 0:
        return "No loads."
    if rej < 0.05 * dem:
        return "Almost all heating, very little rejected heat: recovery is marginal; the choice is the heat source."
    if dem < 0.05 * rej:
        return "Almost all cooling: little to recover into; the choice is the rejection route."
    shift = max(s.storage_MWh.values()) if s.storage_MWh else 0.0
    side = ("short of heat: rejected heat can nearly all be used, and the balance must be generated"
            if a["Unmatched deficit (generate)"] > a["Unmatched surplus (reject)"]
            else "long on heat: surplus has to be rejected, and recovery is limited by demand")
    return (f"The site is {side}. Coincidence factor {s.coincidence:.2f}: "
            + ("same-hour matching captures almost everything, so storage adds little "
               f"(at most {shift:,.0f} MWh/yr in the range tested)." if s.coincidence > 0.9 else
               f"timing limits recovery; storage could add up to {shift:,.0f} MWh/yr in the range tested."))
