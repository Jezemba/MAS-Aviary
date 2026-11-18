"""Min/max of every agent-settable variable over every value the agents produced in one 20-link study.

Writes agent_bounds.json, the optimizer baselines' search boxes (MAS-Aviary feat/optimizer-baselines-constrained,
scripts/optimizer_baselines/agent_bounds.json). The file records the study it was built from (`study`) and the run
folders it read (`run_roots`), and design_params.py refuses a bounds file of another study (D3: the re-run's
report was given the first study's file).

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.agent_bounds --study final --out /data/Jessica/<somewhere>/agent_bounds.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from analysis.link20.extract import STUDIES, read_json, read_jsonl, require_study

WING = {"Wing1": "wing", "Wing2H": "htail", "Wing3V": "vtail"}
ENG = {"splitter.BPR": "bypass_ratio", "T4_MAX": "turbine_inlet_temperature", "fan.PR": "fan_pressure_ratio",
       "lpc.PR": "lpc_pressure_ratio", "hpc.PR": "hpc_pressure_ratio"}
ORDER = ["wing_area", "wing_aspect_ratio", "wing_sweep", "fuselage_length", "fuselage_diameter",
         "htail_area", "htail_aspect_ratio", "htail_sweep", "vtail_area", "vtail_aspect_ratio", "vtail_sweep",
         "bypass_ratio", "turbine_inlet_temperature", "fan_pressure_ratio", "lpc_pressure_ratio", "hpc_pressure_ratio"]


def collect(sources) -> tuple[dict, int, Counter]:
    vals: dict = defaultdict(list)
    fus_keys: Counter = Counter()
    n = 0

    def add(name, v, where):
        try:
            vals[name].append((float(v), where))
        except (TypeError, ValueError):
            pass

    for _ds, root, combos in sources:
        prog = read_json(root / "stat_progress.json") or {}
        done = set(prog.get("completed", {})) | set(prog.get("failed", {}))
        for c in combos:
            for d in sorted(root.glob(f"repeat_*/{c}")):
                link = int(d.parent.name[7:]) + 1
                if f"r{link - 1:03d}_{c}" not in done:
                    continue
                n += 1
                where = f"{c[8:]} link {link}"
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
                    add(f"{p}_area", g.get("area_m2"), where)
                    add(f"{p}_aspect_ratio", g.get("aspect_ratio"), where)
                    add(f"{p}_sweep", g.get("sweep_quarter_chord_deg"), where)
                f = (dsp.get("geometry") or {}).get("Fuselage1") or {}
                add("fuselage_length", f.get("length_m"), where)
                add("fuselage_diameter", f.get("diameter_m"), where)
                for k, p in ENG.items():
                    add(p, (dsp.get("engine") or {}).get(k), where)
    return vals, n, fus_keys


def bounds_document(study: str) -> dict:
    study = require_study(study)
    sources = STUDIES[study]
    vals, n, fus_keys = collect(sources)
    print("links", n, "| fuselage morph 'after' keys:", dict(fus_keys))
    out = {"study": study, "run_roots": sorted({str(root) for _ds, root, _c in sources}), "links": n,
           "source": "measured geometry after each successful tigl morph, each successful pycycle set_inputs "
                     "value, and each finished link's end-state design.json", "bounds": {}}
    for name in ORDER:
        if not vals[name]:
            raise SystemExit(f"no value for {name} in study {study}: refusing to write an incomplete bounds file")
        lo, hi = min(vals[name]), max(vals[name])
        out["bounds"][name] = {"min": lo[0], "max": hi[0], "min_at": lo[1], "max_at": hi[1],
                               "n_values": len(vals[name])}
        print(f"{name:27s} n={len(vals[name]):4d} min {lo[0]:11.5f} ({lo[1]})  max {hi[0]:11.5f} ({hi[1]})")
    return out


def load_checked(path: Path, study: str) -> dict:
    """A bounds file, refused unless it was built from the given study (its `study` and `run_roots`)."""
    study = require_study(study)
    doc = json.loads(Path(path).read_text())
    if doc.get("study") != study:
        raise SystemExit(f"Bounds file {path} is from study {doc.get('study')!r}, not {study!r}: rebuild it with "
                         f"`python -m analysis.link20.agent_bounds --study {study} --out ...`.")
    want = sorted({str(root) for _ds, root, _c in STUDIES[study]})
    if sorted(doc.get("run_roots") or []) != want:
        raise SystemExit(f"Bounds file {path} read run folders {doc.get('run_roots')}, not study {study!r}'s {want}.")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--study", choices=sorted(STUDIES), required=True)
    ap.add_argument("--out", type=Path, required=True, help="the agent_bounds.json to write")
    args = ap.parse_args()
    doc = bounds_document(args.study)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1))
    print(f"wrote {args.out} ({args.study})")


if __name__ == "__main__":
    main()
