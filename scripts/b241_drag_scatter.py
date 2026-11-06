"""B241: is SU2's drag a smooth function of the geometry, or does it jump between meshes?

The B3 sweep (oat_run3) ran the same baseline twice and got bit-identical CD (0.02318554817): one geometry always
gives the same mesh and the same solution, so re-meshing the unchanged baseline would show no scatter at all and
say nothing. What B241 saw is CD jumping 10-30 % for small geometry changes. This script measures that directly:
it moves ONE geometry variable by a ladder of small steps (all else at the baseline) and records SU2's CL / CD,
the mesh size and the mission fuel for each. A smooth response grows with the step; mesh noise does not.

Full coupled pipeline on our own servers (no LLM), same evaluator as oat_sensitivity. Runs one evaluation at a time.
Results: <out>/drag_scatter.jsonl and drag_scatter.md (rewritten after each evaluation).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "optimizer_baselines"))

from oat_sensitivity import all_variables  # noqa: E402

# Relative steps per variable. wing_area +5 % and htail_area +10 % were already run in oat_run3.
LADDERS = {
    "wing_area": (0.0025, 0.005, 0.01, 0.02),
    "htail_area": (0.01, 0.025, 0.05),
}


def plan(vs, ladders=LADDERS) -> list[tuple[str, list[float]]]:
    base = [v.default for v in vs]
    names = [v.name for v in vs]
    runs = []
    for name, steps in ladders.items():
        i = names.index(name)
        for step in steps:
            x = list(base)
            x[i] = base[i] * (1.0 + step)
            runs.append((f"{name}+{step * 100:g}%", x))
    return runs


def table(records: list[dict], baseline: dict | None) -> str:
    out = ["| Run | CL | CD | dCD % | Mesh elements | Fuel kg | dFuel % | Converged |", "|---|---|---|---|---|---|---|---|"]
    rows = ([dict(baseline, label="baseline (oat_run3)")] if baseline else []) + records
    cd0 = ((baseline or {}).get("coupled") or {}).get("cd")
    f0 = (baseline or {}).get("fuel_burned_kg")
    for r in rows:
        c = r.get("coupled") or {}
        cd, fuel = c.get("cd"), r.get("fuel_burned_kg")
        dcd = f"{(cd / cd0 - 1) * 100:+.2f}" if cd and cd0 else "--"
        dfu = f"{(fuel / f0 - 1) * 100:+.2f}" if fuel and f0 else "--"
        out.append(f"| {r['label']} | {c.get('cl')} | {cd} | {dcd} | {r.get('mesh_elements')} | "
                   f"{fuel if fuel is None else f'{fuel:,.1f}'} | {dfu} | {r.get('converged')} |")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, help="oat.jsonl holding baseline_1 (not re-run)")
    ap.add_argument("--config", default="config/mdo_f25_run_qwen32b_2gpu.yaml",
                    help="MCP server list only (no LLM is loaded); never the paid-API config")
    ap.add_argument("--server-base", type=int, required=True)
    ap.add_argument("--t4-takeoff", type=float, required=True)
    ap.add_argument("--t4-climb", type=float, required=True)
    args = ap.parse_args()
    if "claude" in args.config:
        ap.error("the paid-API config is never used")

    from evaluator import CoupledEvaluator

    baseline = None
    if args.baseline and args.baseline.exists():
        baseline = next((json.loads(l) for l in args.baseline.open() if '"baseline_1"' in l), None)
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "drag_scatter.jsonl"
    done = {json.loads(l)["label"] for l in log.open()} if log.exists() else set()
    vs = all_variables(args.t4_takeoff, args.t4_climb)
    ports = {"tigl": 0, "su2": 1, "pycycle": 2, "aviary": 3, "mass": 4}
    ev = CoupledEvaluator(args.config, args.out / "evaluator", coarse_mesh=False,
                          server_ports={k: args.server_base + s for k, s in ports.items()})
    for label, x in plan(vs):
        if label in done:
            continue
        rec = ev.evaluate(x, vs)
        rec["label"] = label
        with log.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        (args.out / "drag_scatter.md").write_text(table([json.loads(l) for l in log.open()], baseline))
        c = rec.get("coupled") or {}
        print(f"{label}: cd {c.get('cd')} cl {c.get('cl')} mesh {rec.get('mesh_elements')} "
              f"fuel {rec.get('fuel_burned_kg')} failure {rec.get('failure')} ({rec.get('seconds')} s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
