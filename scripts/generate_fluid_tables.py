import CoolProp.CoolProp as CP, yaml, math, sys
out = sys.argv[1]
temps = list(range(-20, 96, 5))
def table(fluid, label, ref):
    rows = {"T_C": [], "cp_J_kgK": [], "rho_kg_m3": [], "mu_Pa_s": [], "k_W_mK": []}
    for t in temps:
        T = t + 273.15
        try:
            vals = [CP.PropsSI(p, 'T', T, 'P', 3e5, fluid) for p in ('C', 'D', 'V', 'L')]
        except Exception:
            continue
        if any(math.isnan(v) for v in vals): continue
        rows["T_C"].append(t)
        for k, v in zip(["cp_J_kgK","rho_kg_m3","mu_Pa_s","k_W_mK"], vals):
            rows[k].append(float(f"{v:.6g}"))
    tf = None
    try: tf = CP.PropsSI('T_freeze', 'T', 293.15, 'P', 3e5, fluid) - 273.15
    except Exception: pass
    return {"label": label, "coolprop_fluid": fluid, "freeze_C": None if tf is None else round(tf, 2),
            "source": ref, **rows}
lib = {"generated_by": f"CoolProp {CP.get_global_param_string('version')} (Bell et al. 2014, Ind. Eng. Chem. Res. 53(6)); "
       "incompressible MPG/MEG from Melinder (2010) 'Properties of secondary working fluids for indirect systems', IIR.",
       "pressure_Pa": 3e5, "fluids": {}}
lib["fluids"]["water"] = table("Water", "Water", "IAPWS-95 via CoolProp")
for name, cp_name in (("propylene_glycol", "MPG"), ("ethylene_glycol", "MEG")):
    for x in (0.2, 0.25, 0.3, 0.35, 0.4):
        lib["fluids"][f"{name}_{int(x*100)}"] = table(f"INCOMP::{cp_name}[{x}]", f"{name.replace('_',' ').title()} {int(x*100)}% by mass", "Melinder (2010) via CoolProp INCOMP")
open(out, "w").write("# Generated table - do not edit by hand. Regenerate with scripts/generate_fluid_tables.py\n" + yaml.safe_dump(lib, sort_keys=False))
print({k: (v["freeze_C"], len(v["T_C"])) for k, v in lib["fluids"].items()})
