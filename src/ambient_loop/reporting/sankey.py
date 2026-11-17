"""Annual energy-flow Sankey (inline SVG) from the ledger.

Three columns:
  sources   electricity (by plant group), outdoor air, ground/external, cooling load (heat
            removed from the building)
  hubs      ambient loop, heat-recovery circuit, DHW primary
  sinks     LTHW, DHW, heat rejected outdoors, losses
Ribbon widths are proportional to MWh/yr on one scale. Flows below 0.5 % of the total are
omitted for legibility and listed in a note.
"""

from __future__ import annotations

from html import escape

from ambient_loop.sim.ledger import Ledger

PAL = {"Electricity": "#e8b22c", "Outdoor air": "#7fb3d5", "Ground / external": "#8a6d3b", "Cooling load": "#2f6fd6",
       "Ambient loop": "#1a9c8c", "Heat recovery": "#e07b24", "DHW primary": "#8e1b1b",
       "LTHW": "#e07b24", "DHW": "#d33a2c", "Rejected outdoors": "#7a7a7a", "Losses": "#b2b1b1"}


def flows(led: Ledger) -> list[tuple[str, str, float]]:
    """(source, target, MWh) links derived from the ledger."""
    o = {k[2:]: float(v.sum()) / 1e3 for k, v in led.derived.items() if k.startswith("o:")}
    E = lambda k: float(led.elec[k].sum()) / 1e3 if k in led.elec else 0.0  # noqa: E731
    L: list[tuple[str, str, float]] = []
    add = lambda a, b, v: L.append((a, b, v)) if v > 1e-6 else None  # noqa: E731
    ctot = max(o.get("cond_hr", 0) + o.get("cond_rej", 0) + o.get("cond_air", 0), 1e-9)
    add("Cooling load", "Heat recovery", o.get("cond_hr", 0) - E("cooling") * o.get("cond_hr", 0) / ctot)
    add("Cooling load", "Ambient loop", o.get("cond_rej", 0) - E("cooling") * o.get("cond_rej", 0) / ctot)
    add("Electricity", "Heat recovery", E("cooling") * o.get("cond_hr", 0) / ctot)
    add("Electricity", "Ambient loop", E("cooling") * o.get("cond_rej", 0) / ctot + E("pump:loop") + E("ashp"))
    add("Outdoor air", "Ambient loop", o.get("ashp_d", 0) - E("ashp"))
    cair = o.get("cond_air", 0)
    if cair > 0:  # air-cooled chillers: cooling load and compressor heat go straight outdoors
        tot_c = cair + o.get("cond_rej", 0) + o.get("cond_hr", 0)
        add("Cooling load", "Rejected outdoors", cair - E("cooling") * cair / max(tot_c, 1e-9))
        add("Electricity", "Rejected outdoors", E("cooling") * cair / max(tot_c, 1e-9))
    add("Outdoor air", "LTHW", o.get("evap_L_air", 0))
    add("Outdoor air", "DHW primary", o.get("evap_R_air", 0))
    add("Ground / external", "Ambient loop", o.get("ground_supply", 0) + o.get("ext_supply", 0))
    add("Electricity", "Ambient loop", E("loop_backup"))
    add("Ambient loop", "LTHW", o.get("evap_L", 0))
    add("Electricity", "LTHW", E("lthw_hp") + E("lthw_backup"))
    add("Heat recovery", "LTHW", o.get("hr_L", 0))
    add("Heat recovery", "DHW primary", o.get("hr_P", 0) + o.get("evap_gen_hr", 0))
    add("Ambient loop", "DHW primary", o.get("evap_gen_loop", 0) + o.get("evap_s1", 0) + o.get("evap_R", 0))
    add("Outdoor air", "DHW primary", o.get("evap_gen_air", 0))
    add("Electricity", "DHW primary", E("dhw_gen") + E("dhw_stage1") + E("dhw_reheat") + E("dhw_immersion"))
    add("DHW primary", "DHW", o.get("cal_out", 0))
    add("DHW primary", "Losses", o.get("cal_loss", 0) + max(o.get("cal_in", 0) - o.get("cal_out", 0) - o.get("cal_loss", 0) - o.get("cal_dU", 0), 0))
    add("Heat recovery", "Losses", o.get("buf_loss", 0))
    add("Ambient loop", "Rejected outdoors", o.get("dc_r", 0) + o.get("tw_r", 0) + o.get("ac_r", 0) + o.get("ground_rej", 0))
    add("Electricity", "Rejected outdoors", E("dry_cooler") + E("tower") + E("ashp_cooling"))
    add("Electricity", "Losses", E("pump:circuits"))
    return L


def sankey_svg(led: Ledger, width: int = 940, height: int = 420) -> str:
    L = flows(led)
    total = sum(v for _, _, v in L)
    small = [(a, b, v) for a, b, v in L if v < 0.005 * total]
    L = [x for x in L if x not in small]
    cols = [["Electricity", "Outdoor air", "Ground / external", "Cooling load"],
            ["Ambient loop", "Heat recovery"],
            ["DHW primary"],
            ["LTHW", "DHW", "Rejected outdoors", "Losses"]]
    val: dict[str, float] = {}
    for a, b, v in L:
        val[a] = val.get(a, 0) + v
        val[b] = val.get(b, 0) + v
    for c in cols[1] + cols[2]:  # hubs: max(in, out)
        vin = sum(v for a, b, v in L if b == c)
        vout = sum(v for a, b, v in L if a == c)
        val[c] = max(vin, vout)
    for c in cols[0]:
        val[c] = sum(v for a, b, v in L if a == c)
    for c in cols[3]:
        val[c] = sum(v for a, b, v in L if b == c)
    gap = 22
    colmax = max(sum(val.get(n, 0) for n in col) for col in cols)
    scale = (height - 40 - gap * 4) / max(colmax, 1e-9)
    xs = [40, width * 0.36, width * 0.58, width - 170]
    pos: dict[str, tuple[float, float, float]] = {}
    for ci, col in enumerate(cols):
        y = 20.0
        for n in col:
            h = val.get(n, 0) * scale
            if h <= 0:
                continue
            pos[n] = (xs[ci], y, h)
            y += h + gap
    out = [f'<svg width="100%" viewBox="0 0 {width} {height}" role="img" aria-label="energy flow Sankey" font-family="Raleway, Arial, sans-serif">']
    off_out = {n: 0.0 for n in pos}
    off_in = {n: 0.0 for n in pos}
    for a, b, v in sorted(L, key=lambda x: (list(pos).index(x[0]) if x[0] in pos else 0, list(pos).index(x[1]) if x[1] in pos else 0)):
        if a not in pos or b not in pos:
            continue
        xa, ya, _ = pos[a]
        xb, yb, _ = pos[b]
        w = v * scale
        y1 = ya + off_out[a] + w / 2
        y2 = yb + off_in[b] + w / 2
        off_out[a] += w
        off_in[b] += w
        x1, x2 = xa + 18, xb
        mx = (x1 + x2) / 2
        out.append(f'<path d="M{x1:.1f},{y1:.1f} C{mx:.1f},{y1:.1f} {mx:.1f},{y2:.1f} {x2:.1f},{y2:.1f}" fill="none" '
                   f'stroke="{PAL.get(a, "#999")}" stroke-opacity="0.45" stroke-width="{max(w, 0.8):.1f}">'
                   f'<title>{escape(a)} → {escape(b)}: {v:,.0f} MWh/yr</title></path>')
    for n, (x, y, h) in pos.items():
        out.append(f'<rect x="{x}" y="{y:.1f}" width="18" height="{h:.1f}" fill="{PAL.get(n, "#999")}"/>')
        tx = x + 24 if x < width - 200 else x + 24
        out.append(f'<text x="{tx}" y="{y + min(h / 2, 14) + 4:.1f}" font-size="11.5" fill="var(--text, #231f20)">{escape(n)} '
                   f'<tspan fill="var(--muted, #5b5859)">{val.get(n, 0):,.0f}</tspan></text>')
    out.append("</svg>")
    note = ("" if not small else '<p class="note">Omitted (&lt;0.5 %): ' + ", ".join(f"{escape(a)} → {escape(b)} {v:,.1f}"
                                                                                for a, b, v in small) + " MWh/yr.</p>")
    return f'<div class="chart">{"".join(out)}</div><p class="note">MWh/yr. Electricity enters as heat (compressor work) or is lost (pumps); the cooling load is heat removed from the building.</p>{note}'
