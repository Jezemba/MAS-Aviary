"""Fuel burn of the unchanged baseline design against the SLSQP iteration cap (optimizer_max_iter).

Inputs are the JSON lines written by baseline_mission.py --sweep (one fresh aviary session per cap).

Usage (aipexws8, Avion venv):
    python -m analysis.link20.maxiter_plot --requested baseline/maxiter_requested.jsonl \
        --fine baseline/maxiter_fine.jsonl --links out/links.csv --out out/plots/8_optimizer_iterations
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from analysis.link20.plots import REF_STYLE, save, setup_style  # noqa: E402

UNCAPPED = 1000   # far above where SLSQP stops by itself on this design (44 reported iterations)
POINT = {"color": "0.15", "s": 34, "zorder": 3}


def load(path: Path) -> pd.DataFrame:
    return pd.DataFrame([json.loads(line) for line in path.read_text().splitlines() if line.strip()])


def requested_plot(req: pd.DataFrame, ylim, path: Path) -> None:
    capped = req[req.optimizer_max_iter <= 20].sort_values("optimizer_max_iter")
    free = req[req.optimizer_max_iter == UNCAPPED].iloc[0]
    labels = [str(n) for n in capped.optimizer_max_iter] + ["No cap"]
    fig, ax = plt.subplots(figsize=(5.4, 4.0))
    ax.plot(range(len(labels)), list(capped.fuel_burned_kg) + [free.fuel_burned_kg], color="0.15", linewidth=1.2)
    ax.scatter(range(len(labels)), list(capped.fuel_burned_kg) + [free.fuel_burned_kg], **POINT)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_xlabel("SLSQP iteration cap (optimizer_max_iter)")
    ax.set_ylabel("Fuel burn (kg)")
    if ylim:
        ax.set_ylim(ylim)
    else:
        ax.ticklabel_format(axis="y", useOffset=False)
    save(fig, path)


def fine_plot(fine: pd.DataFrame, req: pd.DataFrame, ylim, path: Path) -> None:
    free = req[req.optimizer_max_iter == UNCAPPED].iloc[0]
    f = fine.sort_values("optimizer_max_iter")
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    ax.plot(f.optimizer_max_iter, f.fuel_burned_kg, color="0.15", linewidth=1.2, marker="o", markersize=3.5)
    ax.axhline(free.fuel_burned_kg, label="No cap", **REF_STYLE)
    ax.set_xlabel("SLSQP iteration cap (optimizer_max_iter)")
    ax.set_ylabel("Fuel burn (kg)")
    if ylim:
        ax.set_ylim(ylim)
    else:
        ax.ticklabel_format(axis="y", useOffset=False)
    ax.legend(loc="upper right")
    save(fig, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requested", type=Path, required=True)
    ap.add_argument("--fine", type=Path, required=True)
    ap.add_argument("--links", type=Path, required=True, help="links.csv, for the fuel plots' y-range")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    setup_style()
    req, fine = load(args.requested), load(args.fine)
    links = pd.read_csv(args.links)
    fuel = links.loc[links.flew.astype(bool), "fuel_kg"]
    pad = 0.05 * (fuel.max() - fuel.min())
    study_ylim = (min(fuel.min(), req.fuel_burned_kg.min()) - pad, max(fuel.max(), req.fuel_burned_kg.max()) + pad)
    requested_plot(req, None, args.out / "maxiter_requested_zoom.png")
    requested_plot(req, study_ylim, args.out / "maxiter_requested_study_scale.png")
    fine_plot(fine, req, None, args.out / "maxiter_1_to_50_zoom.png")
    fine_plot(fine, req, study_ylim, args.out / "maxiter_1_to_50_study_scale.png")
    pd.concat([req.assign(sweep="requested"), fine.assign(sweep="1-50")]).to_csv(
        args.out / "maxiter_sweep.csv", index=False)


if __name__ == "__main__":
    main()
