"""Plots and the iteration table for the optimizer baselines (clean white seaborn PNGs, no titles).

    ../.venv/bin/python scripts/optimizer_baselines/plot_baselines.py --runs logs/baselines/pso logs/baselines/ga \
        logs/baselines/slsqp --out logs/baselines/plots

fuel_vs_iteration.png   best feasible fuel burn found so far, per optimizer; the default design's fuel as a line
gtow_vs_iteration.png   the gross take-off weight of that best design; the 90,000 kg limit as a line
pareto_fuel_gtow.png    every design that flew (fuel vs GTOW), each optimizer's non-dominated set joined
best_at_iterations.csv  the best feasible design at iterations 5, 10, 15, 20 and at convergence
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_space import GTOW_LIMIT_KG  # noqa: E402

LABELS = {"pso": "Particle swarm", "ga": "Genetic algorithm", "slsqp": "SLSQP (gradient)"}
CHECKPOINTS = (5, 10, 15, 20)


def load(run: Path) -> dict:
    meta = json.loads((run / "run.json").read_text())
    evals, iters = [], []
    for line in (run / "evaluations.jsonl").read_text().splitlines():
        rec = json.loads(line)
        (evals if rec.get("kind") == "evaluation" else iters).append(rec)
    return {"algo": meta["algo"], "label": LABELS.get(meta["algo"], meta["algo"]), "evals": evals, "iters": iters}


def pareto(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Non-dominated (fuel, gtow) points, both minimised, sorted by fuel."""
    front, best_gtow = [], float("inf")
    for fuel, gtow in sorted(points):
        if gtow < best_gtow:
            front.append((fuel, gtow))
            best_gtow = gtow
    return front


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="white", context="talk")
    palette = dict(zip(LABELS.values(), sns.color_palette("colorblind", 3)))
    runs = [load(r) for r in args.runs]
    args.out.mkdir(parents=True, exist_ok=True)

    # The default design is every optimizer's first evaluation (iteration 0, individual 0).
    default = next((r["evals"][0] for r in runs if r["evals"] and r["evals"][0].get("flown")), None)

    def series(run, key):
        xs, ys = [], []
        for row in run["iters"]:
            if row.get(key) is not None:
                xs.append(row["iteration"])
                ys.append(row[key])
        return xs, ys

    for key, ylabel, ref, fname in (
            ("best_feasible_fuel_kg", "Fuel burn (kg)", default and default["fuel_burned_kg"], "fuel_vs_iteration.png"),
            ("best_feasible_gtow_kg", "Gross take-off weight (kg)", GTOW_LIMIT_KG, "gtow_vs_iteration.png")):
        fig, ax = plt.subplots(figsize=(9, 5.5))
        for run in runs:
            xs, ys = series(run, key)
            if xs:
                ax.plot(xs, ys, marker="o", markersize=4, linewidth=2, color=palette[run["label"]], label=run["label"])
        if ref is not None:
            ref_label = "Default design" if key.endswith("fuel_kg") else "GTOW limit"
            ax.axhline(ref, color="0.35", linestyle="--", linewidth=1.5, label=ref_label)
        ax.set_xlabel("Iteration")
        ax.set_ylabel(ylabel)
        ax.legend(frameon=False)
        sns.despine(fig)
        fig.tight_layout()
        fig.savefig(args.out / fname, dpi=200, facecolor="white")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 6))
    for run in runs:
        pts = [(e["fuel_burned_kg"], e["gtow_kg"]) for e in run["evals"] if e.get("flown") and e.get("gtow_kg")]
        if not pts:
            continue
        color = palette[run["label"]]
        ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=18, alpha=0.35, color=color)
        front = pareto(pts)
        ax.plot([p[0] for p in front], [p[1] for p in front], marker="o", linewidth=2, color=color, label=run["label"])
    ax.axhline(GTOW_LIMIT_KG, color="0.35", linestyle="--", linewidth=1.5, label="GTOW limit")
    ax.set_xlabel("Fuel burn (kg)")
    ax.set_ylabel("Gross take-off weight (kg)")
    ax.legend(frameon=False)
    sns.despine(fig)
    fig.tight_layout()
    fig.savefig(args.out / "pareto_fuel_gtow.png", dpi=200, facecolor="white")
    plt.close(fig)

    with open(args.out / "best_at_iterations.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["optimizer", "iteration", "evaluations", "best_feasible_fuel_kg", "best_feasible_gtow_kg",
                    "design"])
        for run in runs:
            rows = {r["iteration"]: r for r in run["iters"]}
            last = run["iters"][-1] if run["iters"] else None
            for it in CHECKPOINTS:
                r = rows.get(it)
                if r:
                    w.writerow([run["label"], it, r["evaluations"], r["best_feasible_fuel_kg"],
                                r["best_feasible_gtow_kg"], json.dumps(r["best_feasible_design"])])
            if last:
                w.writerow([run["label"], f"converged ({last['iteration']})", last["evaluations"],
                            last["best_feasible_fuel_kg"], last["best_feasible_gtow_kg"],
                            json.dumps(last["best_feasible_design"])])
    print(f"plots and table in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
