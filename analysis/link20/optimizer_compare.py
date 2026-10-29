"""Fuel burn per link (the 8 combinations) next to fuel burn per iteration (the optimizer baselines), links 1-5.

The optimizer value at iteration k is the lowest fuel burn among the designs it flew IN that iteration (not the best
so far), so it can rise as well as fall, like a link's fuel. Iteration 0 is the starting design and is left out, as
link 1 is the agents' first link. Hollow optimizer markers: that design's mission did not converge. With
--converged-only the optimizers use converged missions only (an iteration with none is left out).

Usage (aipexws8, Avion venv):
    python -m analysis.link20.optimizer_compare --links out_20261028/links.csv \
        --baseline baseline/baseline_run1.json --runs <c2>/pso <c2>/ga <c2>/slsqp \
        --out out_20261028/plots/9_optimizer_baselines
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from matplotlib.lines import Line2D

from analysis.link20.plots import (COMBOS, HANDLER_LABELS, HANDLER_LS, HANDLERS, REF_STYLE, STRUCT_COLORS,
                                   STRUCT_LABELS, STRUCTURES, plt, save, setup_style)

LINKS = 5
OPTIMIZERS = {"pso": ("Particle swarm", "D"), "ga": ("Genetic algorithm", "v"), "slsqp": ("SLSQP (gradient)", "P")}
OPT_STYLE = {"color": "black", "linestyle": (0, (4, 1.5, 1, 1.5)), "linewidth": 1.3, "zorder": 4}


def optimizer_series(run: Path, converged_only: bool) -> pd.DataFrame:
    """One row per iteration 1..LINKS: the lowest fuel flown in that iteration and whether its mission converged."""
    rows = [json.loads(line) for line in (run / "evaluations.jsonl").read_text().splitlines() if line.strip()]
    evals = [r for r in rows if r.get("kind") == "evaluation" and r.get("flown")
             and (r.get("converged") or not converged_only)]
    out = []
    for k in range(1, LINKS + 1):
        here = [r for r in evals if r["iteration"] == k]
        if here:
            best = min(here, key=lambda r: r["fuel_burned_kg"])
            out.append({"link": k, "value": best["fuel_burned_kg"], "converged": bool(best.get("converged"))})
    return pd.DataFrame(out, columns=["link", "value", "converged"])


def _draw_optimizers(ax, opt: dict) -> None:
    for algo, d in opt.items():
        if d.empty:
            continue
        marker = OPTIMIZERS[algo][1]
        ax.plot(d.link, d.value, **OPT_STYLE)
        for conv, face in ((True, "black"), (False, "white")):
            p = d[d.converged == conv]
            ax.scatter(p.link, p.value, marker=marker, s=34, facecolor=face, edgecolor="black", linewidth=1.1,
                       zorder=5)


def _optimizer_handles(opt: dict) -> list:
    handles = [Line2D([], [], marker=OPTIMIZERS[a][1], markerfacecolor="black", markeredgecolor="black",
                      label=OPTIMIZERS[a][0], **{k: v for k, v in OPT_STYLE.items() if k != "zorder"})
               for a, d in opt.items() if not d.empty]
    if any((~d.converged).any() for d in opt.values() if not d.empty):
        handles.append(Line2D([], [], marker="D", linestyle="", markerfacecolor="white", markeredgecolor="black",
                              label="Hollow: mission not converged"))
    return handles


def figure(links: pd.DataFrame, opt: dict, baseline: float | None, panels: bool, path: Path) -> None:
    d = links[links.link <= LINKS].copy()
    d["value"] = pd.to_numeric(d.fuel_kg, errors="coerce").where(d.flew.astype(bool))
    if panels:
        fig, axes = plt.subplots(1, len(HANDLERS), figsize=(3.6 * len(HANDLERS) + 1.6, 3.8), sharex=True, sharey=True)
        ax_of = dict(zip(HANDLERS, axes))
    else:
        fig, ax = plt.subplots(figsize=(7.2, 4.0))
        axes, ax_of = [ax], {h: ax for h in HANDLERS}
    nofly = []
    for s, h in COMBOS:
        g = d[(d.structure == s) & (d.handler == h)].sort_values("link")
        ls = "-" if panels else HANDLER_LS[h]
        ax_of[h].plot(g.link, g.value, color=STRUCT_COLORS[s], linestyle=ls, marker="o", markersize=3.5,
                      linewidth=1.5, zorder=3)
        if g.value.isna().any():
            nofly.append((ax_of[h], s, g.loc[g.value.isna(), "link"].to_numpy()))
    for ax in axes:
        _draw_optimizers(ax, opt)
        if baseline is not None:
            ax.axhline(baseline, **REF_STYLE)
        ax.set_xlim(0.6, LINKS + 0.4)
        ax.set_xticks(range(1, LINKS + 1))
        ax.set_xlabel("Link or optimizer iteration")
    axes[0].set_ylabel("Fuel burn (kg)")
    if nofly:
        y0 = min(a.get_ylim()[0] for a in axes)
        for ax, s, ks in nofly:
            ax.scatter(ks, [y0] * len(ks), s=34, facecolor="white", edgecolor=STRUCT_COLORS[s], linewidth=1.3,
                       clip_on=False, zorder=5)
    if panels:
        for h in HANDLERS:
            ax_of[h].set_title(HANDLER_LABELS[h], fontsize=11)

    handles = [Line2D([], [], color=STRUCT_COLORS[s], linewidth=2, label=STRUCT_LABELS[s]) for s in STRUCTURES]
    if not panels:
        handles += [Line2D([], [], color="0.2", linestyle=HANDLER_LS[h], linewidth=1.5, label=HANDLER_LABELS[h])
                    for h in HANDLERS]
    handles += _optimizer_handles(opt)
    if nofly:
        handles.append(Line2D([], [], marker="o", linestyle="", markerfacecolor="white", markeredgecolor="0.3",
                              markersize=6, label="Did not fly"))
    if baseline is not None:
        handles.append(Line2D([], [], label="Baseline (unchanged design)",
                              **{k: v for k, v in REF_STYLE.items() if k != "zorder"}))
    axes[-1].legend(handles=handles, loc="upper left", bbox_to_anchor=(1.02, 1.0))
    save(fig, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--baseline", type=Path)
    ap.add_argument("--runs", type=Path, nargs="+", required=True, help="optimizer run dirs, named pso/ga/slsqp")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    setup_style()
    links = pd.read_csv(args.links)
    baseline = None
    if args.baseline and args.baseline.is_file():
        baseline = json.loads(args.baseline.read_text())["run_simulation"]["summary"]["fuel_burned_kg"]
    for converged_only, suffix in ((False, ""), (True, "_converged_only")):
        opt = {r.name: optimizer_series(r, converged_only) for r in args.runs if r.name in OPTIMIZERS}
        for panels, variant in ((True, "panels"), (False, "single")):
            figure(links, opt, baseline, panels, args.out / f"fuel_links1-5_vs_optimizers_{variant}{suffix}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
