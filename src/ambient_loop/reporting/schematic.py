"""Auto-generated principle schematic (SVG) for an arrangement.

Style follows the Milieu reference schematic: one colour per circuit, flow solid and return
dashed, equipment tags, temperatures on pipes, numbered callouts tied to notes. It is a
principle schematic, not for construction: valves, isolation, strainers, metering and
expansion are omitted.

Layout is fixed by role and drawn only for the blocks present:
  - bottom: ambient loop header; balancing plant at the left
  - left-centre: reversible WSHP fleet with CV-S / CV-U changeovers
  - centre: CHW header (below) and the LTHW / heat-recovery header with buffer (above)
  - right: DHW preheat PHX, calorifiers, DHW generator, circulation reheat
"""

from __future__ import annotations

from html import escape

from ambient_loop.schema.arrangement import Arrangement

COL = {"loop": "#1a9c8c", "lthw": "#e07b24", "chw": "#2f6fd6", "cold": "#6aa7d8", "dhw": "#d33a2c",
       "prim": "#8e1b1b", "air": "#7a7a7a", "ground": "#8a6d3b", "text": "#231f20", "muted": "#5b5859",
       "box": "#ffffff", "sheet": "#ffffff", "rule": "#d3d2d2"}


class _SVG:
    def __init__(self, w: int, h: int) -> None:
        self.w, self.h = w, h
        self.parts: list[str] = []

    def line(self, x1: float, y1: float, x2: float, y2: float, col: str, dashed: bool = False, w: float = 2.2) -> None:
        d = ' stroke-dasharray="7 5"' if dashed else ""
        self.parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-width="{w}"{d}/>')

    def path(self, pts: list[tuple[float, float]], col: str, dashed: bool = False, w: float = 2.2, arrow: bool = False) -> None:
        d = " ".join(("M" if i == 0 else "L") + f"{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        mk = f' marker-end="url(#a-{col[1:]})"' if arrow else ""
        self.parts.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{w}"{dash}{mk}/>')

    def box(self, x: float, y: float, w: float, h: float, title: str, sub: str = "", col: str = COL["text"]) -> None:
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{COL["box"]}" stroke="{col}" stroke-width="1.4"/>')
        self.text(x + w / 2, y + h / 2 - (5 if sub else -4), title, 11.5, "middle", bold=True)
        if sub:
            for i, s in enumerate(sub.split("\n")):
                self.text(x + w / 2, y + h / 2 + 10 + i * 12, s, 9.5, "middle", col=COL["muted"])

    def vessel(self, x: float, y: float, w: float, h: float, title: str, sub: str, top: str, bottom: str) -> None:
        gid = f"g{len(self.parts)}"
        self.parts.append(f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
                          f'<stop offset="1" stop-color="{bottom}"/></linearGradient></defs>')
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{w / 2}" fill="url(#{gid})" stroke="{COL["text"]}" stroke-width="1.4"/>')
        self.text(x + w / 2, y + h / 2 - 8, title, 11, "middle", bold=True)
        for i, s in enumerate(sub.split("\n")):
            self.text(x + w / 2, y + h / 2 + 8 + i * 12, s, 9.5, "middle", col=COL["text"])

    def text(self, x: float, y: float, s: str, size: float = 10, anchor: str = "start", col: str = COL["text"],
             bold: bool = False) -> None:
        fw = ' font-weight="700"' if bold else ""
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" fill="{col}"{fw}>{escape(s)}</text>')

    def pump(self, x: float, y: float, col: str, tag: str) -> None:
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="7" fill="#fff" stroke="{col}" stroke-width="1.6"/>'
                          f'<path d="M{x - 3},{y - 4} L{x + 4},{y} L{x - 3},{y + 4} Z" fill="{col}"/>')
        self.text(x, y - 11, tag, 8.5, "middle", col=COL["muted"])

    def valve(self, x: float, y: float, tag: str) -> None:
        self.parts.append(f'<rect x="{x - 8}" y="{y - 12}" width="16" height="24" fill="#fff" stroke="{COL["text"]}"/>'
                          f'<path d="M{x - 8},{y - 12} L{x + 8},{y + 12} M{x + 8},{y - 12} L{x - 8},{y + 12}" stroke="{COL["text"]}"/>')
        self.text(x, y + 23, tag, 8, "middle", col=COL["muted"])

    def callout(self, x: float, y: float, n: int) -> None:
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="9" fill="#6b4fd8"/>')
        self.text(x, y + 3.5, str(n), 10, "middle", col="#fff", bold=True)

    def render(self, notes: list[str], title: str) -> str:
        defs = "".join(
            f'<marker id="a-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>' for c in set(COL.values()))
        legend = [("Ambient loop", COL["loop"]), ("LTHW / heat recovery", COL["lthw"]), ("CHW", COL["chw"]),
                  ("Cold feed", COL["cold"]), ("DHW (potable)", COL["dhw"]), ("DHW primary", COL["prim"])]
        lg = []
        x = 20
        for name, c in legend:
            lg.append(f'<line x1="{x}" y1="{self.h - 18}" x2="{x + 26}" y2="{self.h - 18}" stroke="{c}" stroke-width="3"/>')
            lg.append(f'<text x="{x + 32}" y="{self.h - 14}" font-size="10" fill="{COL["text"]}">{escape(name)}</text>')
            x += 40 + 7 * len(name)
        lg.append(f'<text x="{x + 10}" y="{self.h - 14}" font-size="10" fill="{COL["muted"]}">- - - dashed = return</text>')
        body = "".join(self.parts)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {self.w} {self.h}" font-family="Raleway, Helvetica, Arial, sans-serif" '
                f'role="img" aria-label="{escape(title)}"><defs>{defs}</defs>'
                f'<rect width="{self.w}" height="{self.h}" fill="{COL["sheet"]}" stroke="{COL["rule"]}"/>'
                f'<text x="20" y="26" font-size="15" font-weight="700" fill="{COL["text"]}">{escape(title)}</text>'
                f'<text x="20" y="42" font-size="10" fill="{COL["muted"]}">Principle schematic, auto-generated. Not for construction: '
                f'valves, isolation, strainers, metering and expansion omitted.</text>{body}{"".join(lg)}</svg>')


def schematic(arr: Arrangement) -> tuple[str, list[str]]:
    """Return (svg, numbered notes)."""
    s = _SVG(1120, 700)
    notes: list[str] = []

    def note(x: float, y: float, text: str) -> None:
        notes.append(text)
        s.callout(x, y, len(notes))

    yL, yR = 590, 612  # loop flow / return
    lp = arr.loop
    if lp:
        s.line(40, yL, 1080, yL, COL["loop"])
        s.line(40, yR, 1080, yR, COL["loop"], dashed=True)
        t = f"Ambient loop {lp.flow_C:g} °C flow / {lp.return_C:g} °C return ({lp.fluid.replace('_', ' ')})"
        if lp.mode != "fixed":
            t += f", {lp.mode.replace('_', ' ')}"
        s.text(330, yL - 8, t, 10.5, col=COL["loop"], bold=True)
        s.pump(300, yL, COL["loop"], "P-L1")
    # balancing plant (left)
    by = 470
    nb = 0
    for b in arr.deficit + arr.surplus:
        label = {"ashp": "Rooftop ASHP", "dry_cooler": "Dry cooler", "ashp_cooling": "Air-cooled rejection",
                 "ground": "Borehole field", "tower": "Cooling tower", "external": "Greywater HX"}[b.type]
        if any(label in p for p in s.parts):
            continue
        sub = ""
        if b.fleet:
            sub = f"{b.fleet.count} × {b.fleet.unit_kW:,.0f} kW"
        elif b.rated_kW:
            sub = f"{b.rated_kW:,.0f} kW"
        if b.type == "dry_cooler" and b.simple_cutoff_C is None:
            sub += f"\nruns when T_db < loop − {b.approach_min_K:g} K"
        col = COL["ground"] if b.type == "ground" else COL["air"]
        s.box(40, by - 100, 150, 46, label, sub, col)
        xl = 30 - nb * 6
        s.path([(40, by - 77), (xl, by - 77), (xl, yL)], col)
        nb += 1
        by -= 62 if by > 300 else 0
        if by <= 250:
            break
    # cooling fleet
    xf = 250
    if arr.cooling:
        c = arr.cooling
        for k in range(min(c.count, 3)):
            y = 250 + k * 95
            s.box(xf + 40, y, 130, 52, f"WSHP-{k + 1:02d}", f"{c.unit_kW:,.0f} kW heat\n{'4-pipe reversible' if c.hr_target != 'none' or c.shared_with_heating else 'chiller'}")
            s.valve(xf + 18, y + 26, "CV-S")
            s.valve(xf + 192, y + 26, "CV-U")
            s.line(xf + 18, y + 38, xf + 18, yL, COL["loop"])
            s.line(xf + 10, y + 38, xf + 10, yR, COL["loop"], dashed=True)
            s.line(xf + 26, y + 26, xf + 40, y + 26, COL["text"], w=1.4)
            s.line(xf + 170, y + 26, xf + 184, y + 26, COL["text"], w=1.4)
            # user side to CHW / LTHW headers
            s.line(xf + 200, y + 18, 470, y + 18, COL["chw"])
            s.line(xf + 200, y + 34, 470, y + 34, COL["chw"], dashed=True)
        ylast = 250 + (min(c.count, 3) - 1) * 95
        s.line(470, 268, 470, max(ylast + 18, 380), COL["chw"])
        s.line(462, 284, 462, max(ylast + 34, 402), COL["chw"], dashed=True)
        if c.count > 3:
            s.text(xf + 105, 250 + 3 * 95 - 10, f"… {c.count} units in total", 10, "middle", col=COL["muted"])
        if c.hr_target != "none" or c.shared_with_heating:
            s.line(xf + 18, 170, xf + 18, 250, COL["lthw"])
            s.line(xf + 10, 178, xf + 10, 250, COL["lthw"], dashed=True)
            note(xf + 18, 228, "CV-S source changeover per unit: flow and return switch together (6-port or paired 3-port valves) so "
                 "loop and LTHW/HR water never mix.")
            note(xf + 192, 228, "CV-U user changeover per unit between LTHW and CHW headers; purge/sequence changeovers.")
    # CHW
    if arr.chw:
        s.box(478, 360, 102, 64, "CHW header", f"{arr.chw.flow_C:g}/{arr.chw.return_C:g} °C")
        s.line(470, 380, 478, 380, COL["chw"])
        s.line(462, 402, 478, 402, COL["chw"], dashed=True)
        s.path([(580, 380), (660, 380)], COL["chw"], arrow=True)
        s.path([(660, 402), (580, 402)], COL["chw"], dashed=True)
        s.text(664, 384, f"To CHW circuits {arr.chw.flow_C:g}/{arr.chw.return_C:g} °C", 9.5, col=COL["chw"])
        s.pump(620, 380, COL["chw"], "P-C1")
    # LTHW and HR
    yH = 170
    hr_present = arr.cooling is not None and arr.cooling.hr_target != "none"
    if arr.lthw:
        s.line(xf + 18, yH, 700 if not arr.buffer else 470, yH, COL["lthw"])
        s.line(xf + 10, yH + 8, 700 if not arr.buffer else 470, yH + 8, COL["lthw"], dashed=True)
        s.path([(720, 80), (900, 80)], COL["lthw"], arrow=True)
        s.text(905, 84, f"To LTHW circuits {arr.lthw.flow_C:g}/{arr.lthw.return_C:g} °C", 9.5, col=COL["lthw"])
        s.pump(800, 80, COL["lthw"], "P-H1")
    if hr_present and arr.hr:
        s.text(xf + 30, yH - 26, f"Heat-recovery circuit: condensers at {arr.hr.flow_C:g} °C", 10, col=COL["lthw"], bold=True)
        pr = " → ".join(p.replace("_", " ") for p in arr.hr.priority)
        note(xf + 18, yH - 30, f"Recovered-heat priority: {pr}.")
    if arr.buffer:
        b = arr.buffer
        dT = (arr.hr.flow_C if arr.hr else 45) - b.t_return_C
        cap = b.volume_m3 * 4.18 * dT / 3.6
        s.vessel(470, 110, 90, 200, "Buffer", f"stratified\n{b.volume_m3:g} m³ · {b.nodes} nodes\n≈{cap:,.0f} kWh", "#f3a46b", "#cfe3f7")
        s.text(515, 104, f"{arr.hr.flow_C if arr.hr else 45:g} °C", 9, "middle")
        s.text(515, 324, f"{b.t_return_C:g} °C", 9, "middle")
        s.path([(560, 130), (720, 130), (720, 80)], COL["lthw"])
        note(560, 214, f"Charge at the top, draw cold from the bottom: across {dT:g} K the buffer holds ≈{cap:,.0f} kWh "
             f"vs ≈{b.volume_m3 * 4.18 * 5 / 3.6:,.0f} kWh at 5 K (rule 7). Primary side only (HSG274).")
    elif arr.lthw:
        s.path([(700, yH), (720, yH), (720, 80)], COL["lthw"])
    # DHW
    if arr.dhw:
        d = arr.dhw
        xc = 930
        s.vessel(xc, 150, 90, 190, "Calorifiers", f"{d.calorifier_m3 * 1000:,.0f} L\n{d.store_C:g} °C", "#f6c3bd", "#f9e3e1")
        s.path([(xc + 45, 150), (xc + 45, 110), (1090, 110)], COL["dhw"], arrow=True)
        s.text(1000, 104, f"To DHW outlets ≥ {min(d.store_C - 5, 60):g} °C", 9.5, col=COL["dhw"])
        s.path([(1090, 220), (xc + 90, 220)], COL["dhw"], dashed=True)
        s.text(1030, 214, f"DHWS return ~{d.circulation_return_C:g} °C", 9, col=COL["dhw"])
        note(1060, 236, "DHWS secondary return goes back to the calorifiers and never passes the preheat PHX (rule 8).")
        if d.preheat and d.preheat.enabled:
            s.box(760, 200, 90, 70, "PHX-01", f"preheat\n10 → {d.preheat.max_C:g} °C")
            s.path([(560 if arr.buffer else 700, 150), (740, 150), (740, 215), (760, 215)], COL["lthw"])
            s.path([(760, 255), (740, 255), (740, 300), (560 if arr.buffer else 700, 300)], COL["lthw"], dashed=True)
            s.path([(805, 420), (805, 270)], COL["cold"], arrow=True)
            s.text(810, 430, "Boosted cold feed 10 °C", 9, col=COL["cold"])
            s.path([(850, 235), (xc, 235)], COL["dhw"], arrow=True)
            s.pump(745, 175, COL["lthw"], "P-PH1")
            note(800, 192, f"PHX-01 counterflow: primary {arr.hr.flow_C if arr.hr else 45:g} → ~15 °C, cold feed 10 → "
                 f"{d.preheat.max_C:g} °C. No warm potable storage: WRAS double-wall PHX.")
        else:
            s.path([(1000, 420), (xc + 60, 340)], COL["cold"], arrow=True)
            s.text(960, 430, "Cold feed", 9, col=COL["cold"])
        g = d.generator
        gt = {"wshp": "DHW heat pumps", "co2": "CO₂ heat pumps", "cascade": "Cascade stage 2", "electric": "Electric"}[g.type]
        s.box(760, 470, 140, 60, gt, f"{g.count} × {g.unit_kW:,.0f} kW\n→ {d.generator_flow_C or d.store_C:g} °C primary")
        s.path([(900, 490), (xc + 45, 490), (xc + 45, 340)], COL["prim"], arrow=True)
        s.path([(xc + 30, 340), (xc + 30, 512), (900, 512)], COL["prim"], dashed=True)
        s.pump(915, 490, COL["prim"], "P-D1")
        if g.type == "co2":
            s.text(830, 545, "air source (rooftop)", 9, "middle", col=COL["air"])
            note(770, 466, "CO₂ COP is set by gas-cooler inlet temperature (rule 4b): feed it cold mains only.")
        elif g.type == "cascade":
            s.box(600, 470, 120, 60, "Cascade stage 1", f"loop → {g.intermediate_C:g} °C")
            s.line(720, 500, 760, 500, COL["lthw"])
            s.line(660, 530, 660, yL, COL["loop"])
            note(610, 466, "Double lift with generated intermediate heat: shown as a known-poor baseline (rule 1).")
        elif g.source in ("hr_changeover", "hr_dedicated"):
            s.path([(830, 470), (830, 330), (560, 330)], COL["lthw"])
            s.line(820, 530, 820, yL, COL["loop"], dashed=g.source == "hr_dedicated")
            note(840, 380, "DHW HP source on the heat-recovery circuit" + (" with changeover to the loop." if g.source == "hr_changeover"
                                                                           else "; one unit dedicated, no loop changeover."))
        else:
            s.line(830, 530, 830, yL, COL["loop"])
            s.line(840, 530, 840, yR, COL["loop"], dashed=True)
            if d.preheat:
                note(860, 560, "DHW heat pumps stay on the ambient loop and top up from the preheat outlet: each kWh of recovered "
                     "heat is used once, with no second compressor stage.")
        if d.reheat:
            s.box(960, 380, 120, 50, "Circulation reheat", f"{d.reheat.count} × {d.reheat.unit_kW:,.0f} kW")
            s.line(1020, 430, 1020, yL, COL["loop"])
    return s.render(notes, f"{arr.id}: {arr.title}"), notes
