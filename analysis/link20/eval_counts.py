"""How many end-to-end evaluations each method spends: the 8 combinations per link, the optimizers per iteration.

The budget unit is a FLIGHT: one Aviary run_simulation that succeeded and burned more than ZERO_FUEL_FLOOR_KG.
Alongside it every pipeline tool is counted on its own (calls and successful calls), so SU2, meshing, mass and
pyCycle effort can be compared without charging them to the budget.

MAS counts come from each link's knowledge_base.jsonl; optimizer counts come from the "steps" of each record in
evaluations.jsonl (one record = one design pushed through the pipeline).

Usage (aipexws8, Avion venv):
    python -m analysis.link20.eval_counts --out out_20261028/eval_counts --runs <c2>/pso <c2>/ga <c2>/slsqp
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from analysis.link20.extract import STUDIES, ZERO_FUEL_FLOOR_KG, mission_summaries, read_jsonl, require_study

# Pipeline stage -> the MAS (server, tool) that performs it and the optimizer step name(s) that record it.
STAGES = {
    "morph": (("tigl", "set_high_level_parameters"), ("morph Wing1", "morph Fuselage1")),
    "mesh": (("tigl", "generate_volume_mesh"), ("generate_volume_mesh",)),
    "su2": (("su2", "run_su2_solver"), ("run_su2_solver",)),
    "mass": (("mass", "estimate_mass"), ("estimate_mass",)),
    "pycycle": (("pycycle", "run_cycle"), ("run_cycle",)),
    "aviary_params": (("aviary", "set_aircraft_parameters"), ("set_aircraft_parameters",)),
    "simulation": (("aviary", "run_simulation"), ("run_simulation",)),
}


def mas_link_counts(kb: list[dict]) -> dict:
    """Calls and successful calls of every stage in one link, plus the flights (the budget unit)."""
    row = {}
    for stage, ((server, tool), _) in STAGES.items():
        hits = [e for e in kb if e.get("tool") == tool and e.get("server") in (server, None)]
        row[f"{stage}_calls"] = len(hits)
        row[f"{stage}_ok"] = sum(1 for e in hits if e.get("status") == "success")
    row["flights"] = sum(1 for m in mission_summaries(kb)
                         if m["tool"] == "run_simulation" and float(m["fuel_burned_kg"]) > ZERO_FUEL_FLOOR_KG)
    return row


def evaluation_counts(record: dict) -> dict:
    """The same counts for one optimizer evaluation (a morph of both components counts as one morph)."""
    steps = record.get("steps") or {}
    row = {}
    for stage, (_, names) in STAGES.items():
        seen = [steps[n] for n in names if n in steps]
        row[f"{stage}_calls"] = int(bool(seen))
        row[f"{stage}_ok"] = int(bool(seen) and all(s == "ok" for s in seen))
    fuel = record.get("fuel_burned_kg")
    row["flights"] = int(bool(record.get("flown")) and fuel is not None and float(fuel) > ZERO_FUEL_FLOOR_KG)
    return row


def mas_table(sources) -> pd.DataFrame:
    rows = []
    for dataset, root, combos in sources:
        for combo in combos:
            structure, handler = combo.removeprefix("mdo_f25_").split("_", 1)
            for link_dir in sorted(root.glob(f"repeat_*/{combo}")):
                kb = read_jsonl(link_dir / "knowledge_base.jsonl")
                rows.append({"dataset": dataset, "combination": combo, "structure": structure, "handler": handler,
                             "link": int(link_dir.parent.name.split("_")[1]) + 1, **mas_link_counts(kb)})
    return pd.DataFrame(rows)


def optimizer_table(runs: list[Path]) -> pd.DataFrame:
    """One row per optimizer iteration: the counts summed over that iteration's evaluations."""
    rows = []
    for run in runs:
        evals = [r for r in read_jsonl(run / "evaluations.jsonl") if r.get("kind") == "evaluation"]
        per = pd.DataFrame([{"iteration": r["iteration"], **evaluation_counts(r)} for r in evals])
        if per.empty:
            continue
        agg = per.groupby("iteration").sum().reset_index()
        agg.insert(0, "evaluations", per.groupby("iteration").size().to_numpy())
        agg.insert(0, "optimizer", run.name)
        rows.append(agg)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def summary(mas: pd.DataFrame, opt: pd.DataFrame) -> pd.DataFrame:
    """Mean per link (MAS, links 1..max) and mean per iteration (optimizers, iteration 0 = the start excluded)."""
    cols = ["flights"] + [f"{s}_{k}" for s in STAGES for k in ("calls", "ok")]
    m = mas.groupby("combination")[cols].mean().assign(method="mas", units=mas.groupby("combination").size())
    if opt.empty:
        return m.rename_axis("name").reset_index()
    o = opt[opt.iteration >= 1].groupby("optimizer")[cols + ["evaluations"]].mean()
    o = o.assign(method="optimizer", units=opt[opt.iteration >= 1].groupby("optimizer").size())
    return pd.concat([m, o]).rename_axis("name").reset_index()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runs", type=Path, nargs="*", default=[], help="optimizer run dirs (pso/ga/slsqp)")
    ap.add_argument("--study", choices=sorted(STUDIES), required=True, help="which study's MAS runs to count")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    mas = mas_table(STUDIES[require_study(args.study)])
    opt = optimizer_table(args.runs)
    mas.to_csv(args.out / "eval_counts_links.csv", index=False)
    opt.to_csv(args.out / "eval_counts_optimizer_iterations.csv", index=False)
    s = summary(mas, opt)
    s.to_csv(args.out / "eval_counts_summary.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(s[["name", "method", "units", "flights", "simulation_calls", "su2_calls", "mesh_calls", "morph_calls",
                 "mass_calls", "pycycle_calls"]].round(2).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
