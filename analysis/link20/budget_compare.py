"""MAS combinations vs optimizers per FLIGHT (the shared budget unit), three ways (Jessica 2026-11-01, option c).

No design from any method met GTOW <= 90,000 kg, so best feasible fuel is empty everywhere. Instead, after each
flight, three running values:
  best_conv_fuel  lowest fuel of a CONVERGED flight so far, GTOW ignored
  best_loss       lowest paper loss (WAFER D1: fuel; + 1e6 + 1e5 G over the GTOW limit) of any flight so far
  min_conv_gtow   lowest GTOW of a converged flight so far (how close to the limit)

A flight = run_simulation succeeded with fuel > 100 kg. MAS flights are taken in chain order (link 1..20, KB order
inside a link); optimizer flights in attempt order (evaluations.jsonl of the budget runs, B = 20). The comparison
point is flight min(20, flights available).

Also counts optimizer flights whose SU2 aero was NOT injected into aviary (coupled CL outside aviary's declared
[0.05, 1.0]; the data plane then leaves aviary on its default drag polar).

Usage (aipexws8, Avion venv):
    python -m analysis.link20.budget_compare --runs /data/Jessica/optimizer_budget/runs --out out_20261028/budget_compare
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from analysis.link20.extract import SOURCES, ZERO_FUEL_FLOOR_KG, read_jsonl  # noqa: E402

BUDGET = 20
GTOW_LIMIT_KG = 90_000.0
LAMBDA_BASE, LAMBDA_GAP = 1.0e6, 1.0e5          # MAS-Aviary scripts/optimizer_budget/budget.py
CL_BOUNDS = (0.05, 1.0)                          # aviary Mission.Design.LIFT_COEFFICIENT, as the data plane checks
OPTIMIZERS = ("bo", "ga", "mads", "slsqp", "random")
SHORT = {"sequential": "S", "orchestrated": "O", "networked": "N",
         "staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}


def flight_loss(fuel: float, gtow: float | None) -> float:
    g = max(0.0, (gtow or 0.0) - GTOW_LIMIT_KG) / GTOW_LIMIT_KG
    return fuel + (LAMBDA_BASE + LAMBDA_GAP * g if g > 0 else 0.0)


def running(flights: list[dict]) -> list[dict]:
    """flights: dicts with fuel, gtow, converged, in budget order -> the three running values after each."""
    out, best_fuel, best_loss, min_gtow = [], None, None, None
    for k, f in enumerate(flights, 1):
        loss = flight_loss(f["fuel"], f["gtow"])
        best_loss = loss if best_loss is None else min(best_loss, loss)
        if f["converged"]:
            best_fuel = f["fuel"] if best_fuel is None else min(best_fuel, f["fuel"])
            if f["gtow"] is not None:
                min_gtow = f["gtow"] if min_gtow is None else min(min_gtow, f["gtow"])
        out.append({**f, "flight": k, "best_conv_fuel": best_fuel, "best_loss": best_loss,
                    "min_conv_gtow": min_gtow})
    return out


def mas_flights(kb: list[dict]) -> list[dict]:
    out = []
    for e in kb:
        if e.get("tool") != "run_simulation" or e.get("status") != "success":
            continue
        o = e.get("outputs") or {}
        s = o.get("summary") if isinstance(o.get("summary"), dict) else o
        fuel = s.get("fuel_burned_kg")
        if fuel is not None and float(fuel) > ZERO_FUEL_FLOOR_KG:
            gtow = s.get("gtow_kg")
            out.append({"fuel": float(fuel), "gtow": None if gtow is None else float(gtow),
                        "converged": bool(o.get("converged"))})
    return out


def mas_series(sources=SOURCES) -> pd.DataFrame:
    rows = []
    for _dataset, root, combos in sources:
        for combo in combos:
            structure, handler = combo.removeprefix("mdo_f25_").split("_", 1)
            name = f"{SHORT[structure]}_{SHORT[handler]}"
            flights = []
            for link_dir in sorted(root.glob(f"repeat_*/{combo}")):
                link = int(link_dir.parent.name.split("_")[1]) + 1
                if link > 20:
                    continue
                flights += [{**f, "link": link} for f in mas_flights(read_jsonl(link_dir / "knowledge_base.jsonl"))]
            rows += [{"method": "mas", "name": name, "seed": 0, **r} for r in running(flights)]
    return pd.DataFrame(rows)


def optimizer_series(runs: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, aero = [], []
    for algo in OPTIMIZERS:
        for seed_dir in sorted((runs / algo).glob("seed*")):
            recs = [r for r in read_jsonl(seed_dir / "evaluations.jsonl") if r.get("kind") == "evaluation"]
            flown = [r for r in recs if r.get("flight")]
            flights = [{"fuel": float(r["fuel_burned_kg"]), "gtow": r.get("gtow_kg"),
                        "converged": bool(r.get("converged")), "attempt": r["attempt"]} for r in flown]
            rows += [{"method": "optimizer", "name": algo, "seed": int(seed_dir.name[4:]), **r}
                     for r in running(flights)]
            cl = [(r.get("coupled") or {}).get("cl") for r in flown]
            aero.append({"name": algo, "seed": int(seed_dir.name[4:]), "attempts": len(recs), "flights": len(flown),
                         "finished": (seed_dir / "summary.json").exists(),
                         "aero_not_injected": sum(1 for c in cl if c is None or not CL_BOUNDS[0] <= c <= CL_BOUNDS[1])})
    return pd.DataFrame(rows), pd.DataFrame(aero)


def at_budget(series: pd.DataFrame) -> pd.DataFrame:
    """Each method's values at flight min(BUDGET, flights it has); optimizers: mean, sd, min, max over seeds."""
    last = (series[series.flight <= BUDGET].sort_values("flight")
            .groupby(["method", "name", "seed"]).tail(1))
    metrics = ["best_conv_fuel", "best_loss", "min_conv_gtow"]
    agg = last.groupby(["method", "name"]).agg(
        seeds=("seed", "size"), flights=("flight", "min"),
        **{f"{m}_{s}": (m, s) for m in metrics for s in ("mean", "std", "min", "max")})
    return agg.reset_index().sort_values(["method", "best_conv_fuel_mean"])


def plot(series: pd.DataFrame, path: Path) -> None:
    """Minimal: two panels, optimizers = seed mean (solid, coloured), MAS combinations = thin grey, labelled. The loss
    is left to the table: every flight so far carries the 1e6 GTOW penalty, so a loss axis only shows that offset."""
    panels = [("best_conv_fuel", "Best converged fuel (kg)"), ("min_conv_gtow", "Lowest converged GTOW (kg)")]
    colours = dict(zip(OPTIMIZERS, ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#7f7f7f")))
    fig, axes = plt.subplots(1, len(panels), figsize=(11, 4.2), facecolor="white")
    s = series[series.flight <= BUDGET]
    for ax, (col, label) in zip(axes, panels):
        for name, d in s[s.method == "mas"].groupby("name"):
            d = d.dropna(subset=[col])
            if not d.empty:
                ax.plot(d.flight, d[col], color="0.7", lw=1)
                ax.annotate(name, (d.flight.iloc[-1], d[col].iloc[-1]), fontsize=7, color="0.4",
                            xytext=(2, 0), textcoords="offset points", va="center")
        for algo in OPTIMIZERS:
            d = s[(s.method == "optimizer") & (s.name == algo)].dropna(subset=[col])
            if not d.empty:
                # the mean only where every seed has a value (a seed still running would shift it)
                g = d.groupby("flight")[col]
                m = g.mean()[g.size() == d.seed.nunique()]
                ax.plot(m.index, m.values, color=colours[algo], lw=2, label=algo.upper())
        if col == "min_conv_gtow":
            ax.axhline(GTOW_LIMIT_KG, color="black", lw=1, ls="--")
            ax.annotate("GTOW limit", (1, GTOW_LIMIT_KG), fontsize=8, xytext=(0, 3), textcoords="offset points")
        ax.set_xlabel("Flights used")
        ax.set_ylabel(label)
        ax.set_xlim(1, BUDGET + 2)
        ax.set_xticks(range(0, BUDGET + 1, 5))
    axes[0].legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="white")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=Path, required=True, help="optimizer budget runs root (<algo>/seed<k>/)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    opt, aero = optimizer_series(args.runs)
    series = pd.concat([mas_series(), opt], ignore_index=True)
    series.to_csv(args.out / "budget_compare_series.csv", index=False)
    table = at_budget(series)
    table.to_csv(args.out / "budget_compare_at_20_flights.csv", index=False)
    aero.to_csv(args.out / "optimizer_aero_not_injected.csv", index=False)
    plot(series, args.out / "budget_compare.png")
    cols = ["method", "name", "seeds", "flights", "best_conv_fuel_mean", "best_conv_fuel_min", "best_conv_fuel_max",
            "best_loss_mean", "min_conv_gtow_mean", "min_conv_gtow_min"]
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(table[cols].round(0).to_string(index=False))
        print(aero.groupby("name")[["attempts", "flights", "aero_not_injected"]].sum().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
