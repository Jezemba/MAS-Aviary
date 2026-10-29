"""Per-iteration table for every combination, and the recommendation ladder (levels: scored, actionable, feasible,
effective) under the single definition proposed in 07_methods_answers.md Q7.

Inputs (all already computed, read only):
    links.csv (extract.py), physics/links_physics.csv (mission_converged of the mission each link's fuel comes from),
    methods_q7/rec_scoring_items.csv (rec_scoring_reconcile.py; status_proposed per item).
Design ranges: the GLOBAL DESIGN SPACE table of the agents' prompt, config/mdo_f25_sequential_agents.yaml:29-71
@117b55e (identical in orchestrated and, up to ASCII symbols, networked.md).

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.reports.scripts.ladder_and_table --root ../reports_20261028 \
        --links ../out_20261028/links.csv --out ../reports_20261028/ladder
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

ORDER = ["S_SP", "S_IF", "S_GR", "O_SP", "O_IF", "O_GR", "N_IF", "N_GR"]
ABBREV = {"sequential": "S", "orchestrated": "O", "networked": "N",
          "staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}

# Agent-set variables the recommendation can name, with the prompt's stated range (lo, hi); None = open end.
# Sweep: the prompt range is for tigl's `sweep` key; the item's Aircraft.Wing.SWEEP is the aviary quarter-chord
# sweep that follows from it, so the range is applied to the recommended value as written.
PROMPT_RANGES = {
    "Aircraft.Wing.AREA": (100.0, 160.0),
    "Aircraft.Wing.ASPECT_RATIO": (7.0, 17.0),
    "Aircraft.Wing.SWEEP": (15.0, 40.0),
    "engine.splitter.BPR": (None, 10.0),
    "engine.T4_MAX": (2857.0, None),
    "engine.fan.PR": (1.4, 1.7),
    "engine.hpc.PR": (None, 30.0),
}
# Framework-set or follows-geometry in the prompt table: agents can't set them, so no agent range exists.
NOT_ACTIONABLE = {"Aircraft.Engine.SCALE_FACTOR": "set by framework", "Aircraft.Wing.MASS_SCALER": "set by framework",
                  "Mission.Design.LIFT_COEFFICIENT": "set by framework", "Aircraft.Wing.TAPER_RATIO": "follows geometry"}
SCORED = ("followed", "unchanged", "opposite")


def in_range(x: float, lo, hi) -> bool:
    return (lo is None or x >= lo) and (hi is None or x <= hi)


def feasibility(item: pd.Series) -> str:
    """Is the recommended target inside the prompt's design range?"""
    if item.param not in PROMPT_RANGES:
        return "no agent range (not actionable)"
    lo, hi = PROMPT_RANGES[item.param]
    if item.kind == "direction":
        return "no target (direction only)"
    if item.kind == "range":
        return "inside" if in_range(item.lo, lo, hi) and in_range(item.hi, lo, hi) else "outside"
    return "inside" if in_range(item.target, lo, hi) else "outside"


def iteration_table(links: pd.DataFrame, phys: pd.DataFrame) -> pd.DataFrame:
    links = links.assign(combo=[f"{ABBREV[s]}_{ABBREV[h]}" for s, h in zip(links.structure, links.handler)])
    df = links.merge(phys[["combo", "link", "mission_converged"]], on=["combo", "link"], how="left")
    out = pd.DataFrame({
        "combo": df.combo, "iteration": df.link, "run_status": df.run_status,
        "fuel_kg": df.fuel_kg.round(1), "gtow_kg": df.gtow_kg.round(1), "wing_mass_kg": df.wing_mass_kg.round(1),
        "flew": np.where(df.flew, "y", "n"),
        "mission_converged": [("y" if c else "n") if f else "" for f, c in zip(df.flew, df.mission_converged)],
        "agent_steps": df.agent_steps, "duration_h": (df.duration_s / 3600).round(2),
        "output_tokens": df.output_tokens,
    })
    out["combo"] = pd.Categorical(out.combo, ORDER, ordered=True)
    return out.sort_values(["combo", "iteration"]).reset_index(drop=True)


def latex_rows(table: pd.DataFrame) -> str:
    def cell(v, fmt="{:,.0f}"):
        return "--" if v is None or v == "" or (isinstance(v, float) and math.isnan(v)) else fmt.format(v)
    lines = []
    for combo, g in table.groupby("combo", observed=True):
        lines.append(f"% {combo}")
        for r in g.itertuples():
            lines.append(" & ".join([
                str(combo).replace("_", r"\_"), str(r.iteration), cell(r.fuel_kg), cell(r.gtow_kg), cell(r.wing_mass_kg),
                r.flew, r.mission_converged or "--", cell(r.agent_steps), cell(r.duration_h, "{:.2f}"),
                cell(r.output_tokens)]) + r" \\")
    return "\n".join(lines) + "\n"


def change_stats(pairs: pd.DataFrame, col: str) -> dict:
    d = pairs[col].dropna()
    return {"n": len(d), "mean": round(d.mean(), 1) if len(d) else None,
            "median": round(d.median(), 1) if len(d) else None, "improved": int((d < 0).sum())}


def effectiveness(items: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """Fuel / GTOW change from iteration k to k+1 for each scored item. Item `link` = k+1 (the link that acts)."""
    t = table.set_index(["combo", "iteration"])
    rows = []
    for it in items.itertuples():
        k1, k = (it.abbrev, it.link), (it.abbrev, it.link - 1)
        if k not in t.index or k1 not in t.index:
            continue
        a, b = t.loc[k], t.loc[k1]
        both_flew = a.flew == "y" and b.flew == "y"
        rows.append({"abbrev": it.abbrev, "link": it.link, "param": it.param, "followed": it.status_proposed == "followed",
                     "both_flew": both_flew, "both_converged": both_flew and a.mission_converged == "y"
                     and b.mission_converged == "y",
                     "d_fuel_kg": b.fuel_kg - a.fuel_kg if both_flew else np.nan,
                     "d_gtow_kg": b.gtow_kg - a.gtow_kg if both_flew else np.nan})
    pairs = pd.DataFrame(rows)
    summary = []
    for subset, mask in (("all pairs (both flew)", pairs.both_flew), ("converged-only pairs", pairs.both_converged)):
        for followed in (True, False):
            p = pairs[mask & (pairs.followed == followed)]
            for col, name in (("d_fuel_kg", "fuel"), ("d_gtow_kg", "GTOW")):
                summary.append({"pairs": subset, "group": "followed" if followed else "not followed", "metric": name,
                                **change_stats(p, col)})
    return pairs, pd.DataFrame(summary)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 200, "display.max_columns", 20)

    table = iteration_table(pd.read_csv(args.links), pd.read_csv(args.root / "physics" / "links_physics.csv"))
    table.to_csv(args.out / "iterations.csv", index=False)
    (args.out / "iterations_latex_rows.tex").write_text(latex_rows(table))
    print("iterations:", len(table), "rows")

    items = pd.read_csv(args.root / "methods_q7" / "rec_scoring_items.csv")
    scored = items[items.status_proposed.isin(SCORED)].copy()
    scored["actionable"] = scored.param.isin(PROMPT_RANGES.keys())
    scored["feasibility"] = scored.apply(feasibility, axis=1)
    scored.to_csv(args.out / "scored_items.csv", index=False)
    print(f"scored {len(scored)}, followed {(scored.status_proposed == 'followed').sum()}")

    split = scored.groupby(["actionable", "param"]).status_proposed.value_counts().unstack(fill_value=0)
    split.to_csv(args.out / "split_by_param.csv")
    print(split)
    for flag, g in scored.groupby("actionable"):
        print("actionable" if flag else "not actionable", "scored", len(g), "followed",
              int((g.status_proposed == "followed").sum()))

    feas = scored.groupby(["actionable", "feasibility"]).status_proposed.value_counts().unstack(fill_value=0)
    feas.to_csv(args.out / "feasibility.csv")
    print(feas)
    print("outside items:\n", scored[scored.feasibility == "outside"][
        ["abbrev", "link", "param", "kind", "target", "lo", "hi", "status_proposed"]].to_string())

    pairs, eff = effectiveness(scored, table)
    pairs.to_csv(args.out / "effective_pairs.csv", index=False)
    eff.to_csv(args.out / "effective_summary.csv", index=False)
    print(eff.to_string())
    print("pairs:", len(pairs), "both flew:", int(pairs.both_flew.sum()), "both converged:", int(pairs.both_converged.sum()))


if __name__ == "__main__":
    main()
