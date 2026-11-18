"""Before / after: the first 20-link study (2026-10-28) against the re-run after the fixes (2026-11-12).

Fuel per link for each of the 8 combinations, both studies in one panel, from each study's links.csv (extract.py).
The two studies have different baselines (the unchanged starting design flown on that study's aviary-mcp; each
study's value is in extract.BASELINES and each baseline file is checked against it), so a second figure shows
the saving against each study's own baseline.

A link's fuel is the last successful mission in its knowledge base (the runner's rule, extract.py). Filled = that
mission converged, hollow = it did not. Links that did not fly, and links still running, are left out.

Usage (aipexws8, Avion venv):
    python -m analysis.link20.compare_studies \\
        --before ~/Jessica/avion_analysis/out_20261028/links.csv --before-study 20261028 \\
        --before-baseline ~/Jessica/avion_analysis/baseline/baseline_run1.json \\
        --after /data/Jessica/l20_20261112/links.csv --after-study 20261112 \\
        --after-baseline /data/Jessica/l20_20261112/baseline/baseline_run1.json \\
        --out /data/Jessica/l20_compare_20261028_vs_20261112
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from analysis.link20.extract import STUDIES, check_links_study, checked_baseline_fuel  # noqa: E402
from analysis.link20.plots import HANDLER_LABELS, HANDLERS, STRUCT_COLORS, STRUCT_LABELS, STRUCTURES  # noqa: E402

BEFORE_COLOUR = "0.6"


def load(links: Path, study: str, study_name: str | None = None) -> pd.DataFrame:
    """links.csv as one side of the comparison (`study` = before / after). With study_name, the file must have been
    extracted for that study (its `study` column)."""
    d = pd.read_csv(links)
    if study_name is not None:
        check_links_study(d, study_name, links)
    d = d[d.run_status != "in_progress"].copy()
    d["flew"] = d["flew"].astype(bool)
    d = d[d.flew & d.fuel_kg.notna()].copy()
    # ec_converged is True on links that did not fly (00_README), so it is only read on flown links.
    d["converged"] = d.ec_converged.fillna(False).astype(bool)
    d["study"] = study
    return d


def baseline_fuel(path: Path) -> float:
    return float(json.loads(path.read_text())["run_simulation"]["summary"]["fuel_burned_kg"])


def summary(d: pd.DataFrame, base: dict) -> pd.DataFrame:
    rows = []
    for (study, s, h), g in d.groupby(["study", "structure", "handler"]):
        g = g.sort_values("link")
        conv = g[g.converged]
        best = conv.fuel_kg.min() if not conv.empty else None
        rows.append({"study": study, "structure": s, "handler": h, "flown_links": len(g),
                     "converged_links": len(conv), "last_link": int(g.link.max()),
                     "fuel_link1_kg": g.fuel_kg.iloc[0] if g.link.iloc[0] == 1 else None,
                     "fuel_last_kg": g.fuel_kg.iloc[-1], "mean_fuel_kg": g.fuel_kg.mean(),
                     "best_converged_fuel_kg": best, "baseline_kg": base[study],
                     "best_converged_saving_pct": None if best is None else
                     100.0 * (base[study] - best) / base[study]})
    return pd.DataFrame(rows).sort_values(["structure", "handler", "study"])


def panels(d: pd.DataFrame, base: dict, value: str, ylabel: str, path: Path) -> None:
    """2 x 4: one panel per combination; before grey, after in the structure colour."""
    combos = [(s, h) for s in STRUCTURES for h in HANDLERS if not (s == "networked" and h == "staged_pipeline")]
    fig, axes = plt.subplots(2, 4, figsize=(15, 6.6), facecolor="white", sharex=True, sharey=True)
    for ax, (s, h) in zip(axes.flat, combos):
        for study, colour in (("before", BEFORE_COLOUR), ("after", STRUCT_COLORS[s])):
            g = d[(d.study == study) & (d.structure == s) & (d.handler == h)].sort_values("link")
            if g.empty:
                continue
            ax.plot(g.link, g[value], color=colour, lw=1.2, zorder=1)
            for conv, face in ((True, colour), (False, "white")):
                p = g[g.converged == conv]
                ax.scatter(p.link, p[value], s=22, facecolors=face, edgecolors=colour, linewidths=1, zorder=2)
        if value == "fuel_kg":
            ax.axhline(base["before"], color=BEFORE_COLOUR, lw=0.9, ls="--")
            ax.axhline(base["after"], color="black", lw=0.9, ls="--")
        else:
            ax.axhline(0, color="black", lw=0.8)
        ax.set_title(f"{STRUCT_LABELS[s]}, {HANDLER_LABELS[h].lower()}", fontsize=10)
        ax.set_xlim(0, 21)
        ax.set_xticks([1, 5, 10, 15, 20])
    for ax in axes[1, :]:
        ax.set_xlabel("Link")
    for ax in axes[:, 0]:
        ax.set_ylabel(ylabel)
    handles = [Line2D([], [], color=BEFORE_COLOUR, marker="o", label="Before (2026-10-28)"),
               Line2D([], [], color="black", marker="o", label="After (2026-11-12), colour = structure"),
               Line2D([], [], color="0.3", marker="o", ls="", markerfacecolor="white", label="Mission not converged")]
    if value == "fuel_kg":
        handles += [Line2D([], [], color=BEFORE_COLOUR, ls="--", label=f"Baseline before {base['before']:,.0f} kg"),
                    Line2D([], [], color="black", ls="--", label=f"Baseline after {base['after']:,.0f} kg")]
    fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--before-study", choices=sorted(STUDIES), required=True)
    ap.add_argument("--before-baseline", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--after-study", choices=sorted(STUDIES), required=True)
    ap.add_argument("--after-baseline", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    # Each side against its own study's baseline; a baseline file of the other study is refused.
    base = {"before": checked_baseline_fuel(args.before_baseline, args.before_study),
            "after": checked_baseline_fuel(args.after_baseline, args.after_study)}
    d = pd.concat([load(args.before, "before", args.before_study), load(args.after, "after", args.after_study)],
                  ignore_index=True)
    d["saving_pct"] = [100.0 * (base[st] - f) / base[st] for st, f in zip(d.study, d.fuel_kg)]
    d[["study", "combination", "structure", "handler", "link", "fuel_kg", "gtow_kg", "converged",
       "saving_pct"]].to_csv(args.out / "fuel_by_link_before_after.csv", index=False)
    table = summary(d, base)
    table.to_csv(args.out / "summary_before_after.csv", index=False)
    panels(d, base, "fuel_kg", "Fuel burn (kg)", args.out / "fuel_by_link_before_after.png")
    panels(d, base, "saving_pct", "Fuel saving vs own baseline (%)", args.out / "fuel_saving_by_link_before_after.png")
    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(table.round(1).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
