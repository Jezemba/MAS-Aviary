"""Min/max of every agent-settable variable over every value the agents produced in the 20-link study.

Writes out/agent_bounds.json, the optimizer baselines' search boxes (MAS-Aviary feat/optimizer-baselines-constrained,
scripts/optimizer_baselines/agent_bounds.json). Run on aipexws8 from ~/Jessica/avion_analysis: python -m ... or as a script.
"""
import json, sys
sys.path.insert(0, "code")
from collections import defaultdict, Counter
from analysis.link20.extract import SOURCES, read_json, read_jsonl
WING = {"Wing1": "wing", "Wing2H": "htail", "Wing3V": "vtail"}
ENG = {"splitter.BPR": "bypass_ratio", "T4_MAX": "turbine_inlet_temperature", "fan.PR": "fan_pressure_ratio",
       "lpc.PR": "lpc_pressure_ratio", "hpc.PR": "hpc_pressure_ratio"}
vals = defaultdict(list); fus_keys = Counter(); n = 0

def add(name, v, where):
    try:
        vals[name].append((float(v), where))
    except (TypeError, ValueError):
        pass

for ds, root, combos in SOURCES:
    prog = read_json(root / "stat_progress.json") or {}
    done = set(prog.get("completed", {})) | set(prog.get("failed", {}))
    for c in combos:
        for d in sorted(root.glob(f"repeat_*/{c}")):
            link = int(d.parent.name[7:]) + 1
            if f"r{link - 1:03d}_{c}" not in done:
                continue
            n += 1; where = f"{c[8:]} link {link}"
            for e in read_jsonl(d / "knowledge_base.jsonl"):
                if e.get("status") != "success" or e.get("agent") == "framework":
                    continue
                t, i, o = e["tool"], e.get("inputs") or {}, e.get("outputs") or {}
                if t in ("set_high_level_parameters", "morph_wing", "morph_fuselage"):
                    uid = i.get("component_uid") or o.get("component_uid") or ""
                    after = (o.get("geometry_morph") or {}).get("after") or o.get("after") or {}
                    if uid in WING or t == "morph_wing":
                        p = WING.get(uid, "wing")
                        add(f"{p}_area", after.get("reference_area"), where + " (morph)")
                        add(f"{p}_aspect_ratio", after.get("aspect_ratio"), where + " (morph)")
                        add(f"{p}_sweep", after.get("sweep_deg"), where + " (morph)")
                    elif uid.startswith("Fuselage") or t == "morph_fuselage":
                        fus_keys.update(after.keys())
                        add("fuselage_length", after.get("length"), where + " (morph)")
                        w, h = after.get("width"), after.get("height")
                        if w is not None and h is not None:
                            add("fuselage_diameter", max(float(w), float(h)), where + " (morph)")
                elif t == "set_inputs":
                    for k, v in (i.get("values") or {}).items():
                        if k in ENG:
                            add(ENG[k], v, where + " (set_inputs)")
            dsp = (read_json(d / "design.json") or {}).get("design_space") or {}
            for uid, p in WING.items():
                g = (dsp.get("geometry") or {}).get(uid) or {}
                add(f"{p}_area", g.get("area_m2"), where); add(f"{p}_aspect_ratio", g.get("aspect_ratio"), where)
                add(f"{p}_sweep", g.get("sweep_quarter_chord_deg"), where)
            f = (dsp.get("geometry") or {}).get("Fuselage1") or {}
            add("fuselage_length", f.get("length_m"), where); add("fuselage_diameter", f.get("diameter_m"), where)
            for k, p in ENG.items():
                add(p, (dsp.get("engine") or {}).get(k), where)
print("links", n, "| fuselage morph 'after' keys:", dict(fus_keys))
ORDER = ["wing_area", "wing_aspect_ratio", "wing_sweep", "fuselage_length", "fuselage_diameter",
         "htail_area", "htail_aspect_ratio", "htail_sweep", "vtail_area", "vtail_aspect_ratio", "vtail_sweep",
         "bypass_ratio", "turbine_inlet_temperature", "fan_pressure_ratio", "lpc_pressure_ratio", "hpc_pressure_ratio"]
out = {"links": n, "source": "measured geometry after each successful tigl morph, each successful pycycle set_inputs "
                             "value, and each finished link's end-state design.json", "bounds": {}}
for name in ORDER:
    lo, hi = min(vals[name]), max(vals[name])
    out["bounds"][name] = {"min": lo[0], "max": hi[0], "min_at": lo[1], "max_at": hi[1], "n_values": len(vals[name])}
    print(f"{name:27s} n={len(vals[name]):4d} min {lo[0]:11.5f} ({lo[1]})  max {hi[0]:11.5f} ({hi[1]})")
json.dump(out, open("out/agent_bounds.json", "w"), indent=1)
