"""Data inventory and integrity table for the 20-link reports (00_README.md).

One row per combination: where its links come from, how many completed / failed / flew, runner attempts, and the
contamination checks (B187, B214). Plus the link lists behind each count, and the status of the fixed-code
networked iterative-feedback re-run (v4net_iter), which is not part of the 20-link set.

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.reports.scripts.inventory --links ../out_20261028/links.csv \
        --out ../reports_20261028/inventory
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from analysis.link20.extract import HOME, SOURCES, read_json

ABBREV = {"sequential": "S", "orchestrated": "O", "networked": "N",
          "staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}
ORDER = ["S_SP", "S_IF", "S_GR", "O_SP", "O_IF", "O_GR", "N_IF", "N_GR"]
RERUN = HOME / "Jessica/Avion/MAS-Aviary/logs/stat_results/v4net_iter_qwen_nothink_aipexws8"


def links_where(g: pd.DataFrame, mask: pd.Series) -> str:
    links = g.loc[mask, "link"].astype(int).tolist()
    return ", ".join(map(str, links)) if links else "none"


def inventory(df: pd.DataFrame) -> pd.DataFrame:
    roots = {combo: (ds, root) for ds, root, combos in SOURCES for combo in combos}
    rows = []
    for combo, g in df.groupby("combination"):
        g = g.sort_values("link")
        ds, root = roots[combo]
        rows.append({
            "abbrev": f"{ABBREV[g.structure.iloc[0]]}_{ABBREV[g.handler.iloc[0]]}",
            "combination": combo, "dataset": ds, "folder": str(root).replace(str(HOME), "~"),
            "links": len(g), "completed": int((g.run_status == "completed").sum()),
            "failed": int((g.run_status == "failed").sum()), "flew": int(g.flew.sum()),
            "failed_links": links_where(g, g.run_status == "failed"),
            "failed_errors": "; ".join(sorted(set(g.fail_error.dropna()))) or "none",
            "did_not_fly_links": links_where(g, ~g.flew.astype(bool)),
            "failed_but_flew_links": links_where(g, (g.run_status == "failed") & g.flew.astype(bool)),
            "multi_attempt_links": links_where(g, g.step_log_attempts > 1),
            "b187_cycle_links": links_where(g, g.b187_cycle.astype(bool)),
            "b187_inherited_only_links": links_where(g, g.b187_inherited_only.astype(bool)),
            "b214_links": links_where(g, g.b214.astype(bool)),
            "clean_flown_links": int((g.flew.astype(bool) & ~g.b187.astype(bool) & ~g.b214.astype(bool)
                                      & (g.run_status == "completed")).sum()),
        })
    return pd.DataFrame(rows).set_index("abbrev").loc[ORDER].reset_index()


def rerun_status() -> dict:
    progress = read_json(RERUN / "stat_progress.json") or {}
    return {"folder": str(RERUN).replace(str(HOME), "~"),
            "completed": len(progress.get("completed", {})), "failed": len(progress.get("failed", {})),
            "repeat_folders": len(list(RERUN.glob("repeat_*")))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(args.links)
    inv = inventory(df)
    inv.to_csv(args.out / "inventory.csv", index=False)
    rerun = rerun_status()
    (args.out / "rerun_status.json").write_text(json.dumps(rerun, indent=2))
    pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 80)
    print(inv.T.to_string())
    print("totals:", {k: int(inv[k].sum()) for k in ("links", "completed", "failed", "flew", "clean_flown_links")})
    print("v4net_iter re-run:", rerun)


if __name__ == "__main__":
    main()
