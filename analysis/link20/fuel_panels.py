"""Fuel burn per iteration, one panel per combination (JMD manuscript figure, 2026-11-17).

Reads the per-iteration table written by reports/scripts/ladder_and_table.py (iterations.csv: fuel_kg, flew,
mission_converged per combination and iteration) and the baseline mission JSON (baseline_mission.py). Filled marker =
the counted mission converged, hollow = it did not, x on the axis = the iteration did not fly (no fuel value; nothing
is interpolated). Neutral dashed line = the baseline's fuel, dotted line = the 15,000 kg fuel limit.

    python -m analysis.link20.fuel_panels --study final --table reports/ladder/iterations.csv \
        --baseline baseline/baseline_run1.json --out out/fuel_burn_line_panels
writes <out>.pdf (vector), <out>.png and <out>_data.csv (the plotted values).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from analysis.link20.extract import STUDIES, checked_baseline_fuel  # noqa: E402
from analysis.link20.plots import STRUCT_COLORS, setup_style  # noqa: E402

FUEL_LIMIT_KG = 15_000.0
# Panel order: rows = structure, columns = handler (networked has no staged pipeline).
LAYOUT = [["S_SP", "S_IF", "S_GR"], ["O_SP", "O_IF", "O_GR"], [None, "N_IF", "N_GR"]]
STRUCT = {"S": "sequential", "O": "orchestrated", "N": "networked"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--table", type=Path, required=True)
    ap.add_argument("--study", choices=sorted(STUDIES), required=True,
                    help="the study the table is from; the baseline file must be that study's")
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="output path without suffix")
    args = ap.parse_args()

    base = checked_baseline_fuel(args.baseline, args.study)     # refused unless it is this study's baseline
    t = pd.read_csv(args.table)
    setup_style()
    fig, axes = plt.subplots(3, 3, figsize=(11.0, 8.4), sharex=True, sharey=True)
    flown = t[t.flew == "y"]
    top = max(flown.fuel_kg.max(), base) * 1.05
    for r, row in enumerate(LAYOUT):
        for c, combo in enumerate(row):
            ax = axes[r][c]
            if combo is None:
                ax.axis("off")
                continue
            color = STRUCT_COLORS[STRUCT[combo[0]]]
            g = t[t.combo == combo].sort_values("iteration")
            f = g[g.flew == "y"]
            ax.plot(f.iteration, f.fuel_kg, color=color, linewidth=1.5, zorder=2)
            conv = f[f.mission_converged == "y"]
            unconv = f[f.mission_converged != "y"]
            ax.scatter(conv.iteration, conv.fuel_kg, color=color, s=28, zorder=3)
            ax.scatter(unconv.iteration, unconv.fuel_kg, facecolor="white", edgecolor=color, s=28, linewidth=1.2,
                       zorder=3)
            nofly = g[g.flew != "y"]
            ax.scatter(nofly.iteration, [0.0] * len(nofly), marker="x", color="0.3", s=36, zorder=3, clip_on=False)
            ax.axhline(base, color="0.45", linestyle="--", linewidth=1.2, zorder=1)
            ax.axhline(FUEL_LIMIT_KG, color="0.45", linestyle=":", linewidth=1.2, zorder=1)
            ax.set_title(combo, fontsize=12)
            ax.set_xlim(0.5, 20.5)
            ax.set_xticks([1, 5, 10, 15, 20])
            ax.set_ylim(0, top)
            if r == 2 or (r == 1 and c == 0):      # O_SP sits above the legend cell: give it its own iteration axis
                ax.set_xlabel("Iteration")
                ax.tick_params(labelbottom=True)
            if c == 0 or (r == 2 and c == 1):      # N_IF has no panel to its left
                ax.set_ylabel("Fuel burn (kg)")
                ax.tick_params(labelleft=True)
            ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
    handles = [Line2D([], [], color=STRUCT_COLORS[s], linewidth=2, label=s.capitalize()) for s in STRUCT.values()]
    handles += [Line2D([], [], marker="o", linestyle="", color="0.3", label="Converged"),
                Line2D([], [], marker="o", linestyle="", markerfacecolor="white", markeredgecolor="0.3",
                       label="Not converged"),
                Line2D([], [], marker="x", linestyle="", color="0.3", label="Did not fly"),
                Line2D([], [], color="0.45", linestyle="--", label=f"Baseline ({base:,.0f} kg)"),
                Line2D([], [], color="0.45", linestyle=":", label="Fuel limit (15,000 kg)")]
    axes[2][0].legend(handles=handles, loc="center", frameon=False)
    fig.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(args.out.with_suffix(".png"), bbox_inches="tight", dpi=300)
    t[["combo", "iteration", "fuel_kg", "flew", "mission_converged"]].to_csv(f"{args.out}_data.csv", index=False)
    print("wrote", args.out.with_suffix(".pdf"), args.out.with_suffix(".png"), f"{args.out}_data.csv")
    print("plotted: flown", len(flown), "| converged", int((flown.mission_converged == "y").sum()),
          "| did not fly", int((t.flew != "y").sum()), "| baseline", round(base, 1))


if __name__ == "__main__":
    main()
