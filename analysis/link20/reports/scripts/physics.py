"""Physics outcomes of the 20-link DLR-F25 study: fuel, GTOW, wing mass, constraints, convergence.

Reads links.csv (built by analysis.link20.extract) and, for mission convergence of the exact mission each link's
fuel comes from, the raw knowledge_base.jsonl files (same SOURCES as the extractor). Read-only on the run folders.
Every table the report uses is written as CSV to --out and printed.

Usage (on aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.reports.scripts.physics \
        --links ~/Jessica/avion_analysis/out_20261028/links.csv \
        --out ~/Jessica/avion_analysis/reports_20261028/physics
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.link20.extract import SOURCES, ZERO_FUEL_FLOOR_KG, read_jsonl

# Baseline: the unchanged anchor design flown 2026-10-22 on a separate aviary server (SLSQP not converged).
BASELINE = {"fuel_kg": 18197.0, "gtow_kg": 91660.6, "wing_mass_kg": 8815.2}
# Limits from the agents' prompts.
FUEL_LIMIT_KG = 15000.0
GTOW_LIMIT_KG = 90000.0
# Two consecutive links count as "same fuel" when they differ by less than this.
SAME_FUEL_TOL_KG = 1.0

STRUCT_ABBR = {"sequential": "S", "orchestrated": "O", "networked": "N"}
HANDLER_ABBR = {"staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}
COMBO_ORDER = ["S_SP", "S_IF", "S_GR", "O_SP", "O_IF", "O_GR", "N_IF", "N_GR"]
CONTAM_COLS = ["b187", "b214"]


def load_links(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    df["combo"] = df["structure"].map(STRUCT_ABBR) + "_" + df["handler"].map(HANDLER_ABBR)
    df["combo"] = pd.Categorical(df["combo"], COMBO_ORDER, ordered=True)
    for c in ["flew", "b187", "b187_cycle", "b187_inherited_only", "b214"]:
        df[c] = df[c].astype(str).str.lower().eq("true")
    df["failed"] = df["run_status"].ne("completed")
    df["contaminated"] = df[CONTAM_COLS].any(axis=1) | df["failed"]
    return df.sort_values(["combo", "link"]).reset_index(drop=True)


def fuel_mission_details(kb: list[dict]) -> dict:
    """Convergence fields of the LAST successful mission with a fuel figure (the one links.csv takes fuel from)."""
    last = None
    flown_fuels = []
    for e in kb:
        if e.get("tool") not in ("run_simulation", "get_results") or e.get("status") != "success":
            continue
        outputs = e.get("outputs") or {}
        for holder in (outputs.get("summary"), outputs.get("outputs"), outputs):
            if isinstance(holder, dict) and holder.get("fuel_burned_kg") is not None:
                last = (e, outputs, holder)
                if float(holder["fuel_burned_kg"]) > ZERO_FUEL_FLOOR_KG:
                    flown_fuels.append(round(float(holder["fuel_burned_kg"]), 1))
                break
    if last is None:
        return {}
    e, outputs, holder = last
    failure = outputs.get("failure") or {}
    return {"mission_seq": e.get("seq"), "mission_tool": e.get("tool"),
            "mission_fuel_kg_raw": holder.get("fuel_burned_kg"),
            "mission_converged": outputs.get("converged"),
            "optimizer_message": failure.get("optimizer_message"),
            "violated_constraint_count": failure.get("violated_constraint_count"),
            # run_simulation and the get_results that follows report the same mission, so count distinct values.
            "n_distinct_flown_fuel": len(set(flown_fuels))}


def add_raw_convergence(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for dataset, root, combos in SOURCES:
        for combo in combos:
            for link_dir in sorted(root.glob(f"repeat_*/{combo}")):
                link = int(link_dir.parent.name.split("_")[1]) + 1
                d = fuel_mission_details(read_jsonl(link_dir / "knowledge_base.jsonl"))
                rows.append({"combination": combo, "link": link, **d})
    raw = pd.DataFrame(rows)
    out = df.merge(raw, on=["combination", "link"], how="left", validate="one_to_one")
    # Sanity: the raw mission must be the one links.csv took fuel from.
    flown = out[out["flew"]]
    mismatch = (flown["mission_fuel_kg_raw"] - flown["fuel_kg"]).abs() > 1e-6
    print(f"raw-vs-links.csv fuel mismatches on flown links: {int(mismatch.sum())}")
    return out


def metric_summary(df: pd.DataFrame, col: str, group: str = "combo") -> pd.DataFrame:
    base = BASELINE[col]
    out = []
    for key, g in df.groupby(group, observed=True):
        f = g[g["flew"]].dropna(subset=[col])
        v = f[col]
        last = g[g["link"] == g["link"].max()]
        last_val = last[col].iloc[0] if len(last) and last["flew"].iloc[0] else np.nan
        first10, last10 = f[f["link"] <= 10][col], f[f["link"] > 10][col]
        slope = np.polyfit(f["link"], v, 1)[0] if len(f) >= 2 else np.nan
        best = f.loc[v.idxmin()] if len(f) else None
        out.append({
            group: key, "n_links": len(g), "n_flown": len(f),
            "mean": v.mean(), "median": v.median(), "min": v.min(), "max": v.max(), "std": v.std(ddof=1),
            "mean_vs_base_kg": v.mean() - base, "mean_vs_base_pct": 100 * (v.mean() - base) / base,
            "final_link": int(g["link"].max()), "final_value": last_val,
            "final_vs_base_kg": last_val - base, "final_vs_base_pct": 100 * (last_val - base) / base,
            "best_link": int(best["link"]) if best is not None else None,
            "best_value": v.min(), "best_vs_base_kg": v.min() - base,
            "best_vs_base_pct": 100 * (v.min() - base) / base,
            "best_mission_converged": best["mission_converged"] if best is not None else None,
            "n_below_base": int((v < base).sum()),
            "links_below_base": " ".join(str(int(x)) for x in f.loc[v < base, "link"]),
            "n_first10": len(first10), "mean_first10": first10.mean(),
            "n_last10": len(last10), "mean_last10": last10.mean(),
            "last10_minus_first10": last10.mean() - first10.mean(),
            "ols_slope_per_link": slope,
        })
    out = pd.DataFrame(out)
    if group != "combo":
        # Final / best link and the link lists only make sense inside one combination.
        out = out.drop(columns=[c for c in out.columns if c.startswith(("final_", "best_link", "best_mission",
                                                                          "links_below"))])
    return out


def constraints_table(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for c, g in df.groupby("combo", observed=True):
        f = g[g["flew"]]
        fuel_ok = f["fuel_kg"] <= FUEL_LIMIT_KG
        gtow_ok = f["gtow_kg"] <= GTOW_LIMIT_KG
        both = fuel_ok & gtow_ok
        out.append({"combo": c, "n_flown": len(f), "n_fuel_ok": int(fuel_ok.sum()), "n_gtow_ok": int(gtow_ok.sum()),
                    "n_both_ok": int(both.sum()),
                    "links_fuel_ok": " ".join(str(int(x)) for x in f.loc[fuel_ok, "link"]),
                    "links_gtow_ok": " ".join(str(int(x)) for x in f.loc[gtow_ok, "link"]),
                    "links_both_ok": " ".join(str(int(x)) for x in f.loc[both, "link"]),
                    "fuel_ok_converged": int((fuel_ok & f["mission_converged"].eq(True)).sum()),
                    "min_gtow_kg": f["gtow_kg"].min()})
    return pd.DataFrame(out)


def convergence_table(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for c, g in df.groupby("combo", observed=True):
        f = g[g["flew"]]
        conv = f[f["mission_converged"].eq(True)]
        unconv = f[f["mission_converged"].eq(False)]
        ec = f["ec_converged"].astype(str).str.lower()
        out.append({
            "combo": c, "n_flown": len(f), "n_mission_converged": len(conv),
            "share_converged_pct": 100 * len(conv) / len(f) if len(f) else np.nan,
            "n_ec_converged_true": int(ec.eq("true").sum()),
            "n_ec_disagrees_with_mission": int((ec.eq("true") != f["mission_converged"].eq(True)).sum()),
            "conv_mean_fuel": conv["fuel_kg"].mean(), "conv_median_fuel": conv["fuel_kg"].median(),
            "conv_min_fuel": conv["fuel_kg"].min(),
            "conv_min_fuel_link": int(conv.loc[conv["fuel_kg"].idxmin(), "link"]) if len(conv) else None,
            "unconv_mean_fuel": unconv["fuel_kg"].mean(), "unconv_min_fuel": unconv["fuel_kg"].min(),
            "conv_n_below_base": int((conv["fuel_kg"] < BASELINE["fuel_kg"]).sum()),
            "unconv_n_below_base": int((unconv["fuel_kg"] < BASELINE["fuel_kg"]).sum()),
            "conv_mean_gtow": conv["gtow_kg"].mean(),
            "conv_links": " ".join(str(int(x)) for x in conv["link"]),
        })
    return pd.DataFrame(out)


def low_fuel_table(df: pd.DataFrame) -> pd.DataFrame:
    """Every flown link below the baseline fuel, with the raw evidence on its mission."""
    cols = ["combo", "link", "fuel_kg", "gtow_kg", "wing_mass_kg", "mission_converged", "ec_converged",
            "optimizer_message", "violated_constraint_count", "mission_tool", "mission_seq", "run_status",
            "b187", "b214", "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "Mission.Design.LIFT_COEFFICIENT"]
    f = df[df["flew"] & (df["fuel_kg"] < BASELINE["fuel_kg"])]
    return f[cols].sort_values("fuel_kg").reset_index(drop=True)


def headline(sub: pd.DataFrame) -> dict:
    f = sub[sub["flew"]]
    return {"n_flown": len(f), "mean_fuel": f["fuel_kg"].mean(), "median_fuel": f["fuel_kg"].median(),
            "min_fuel": f["fuel_kg"].min(), "n_below_base": int((f["fuel_kg"] < BASELINE["fuel_kg"]).sum()),
            "mean_gtow": f["gtow_kg"].mean(), "mean_wing": f["wing_mass_kg"].mean(),
            "n_fuel_ok": int((f["fuel_kg"] <= FUEL_LIMIT_KG).sum()),
            "n_gtow_ok": int((f["gtow_kg"] <= GTOW_LIMIT_KG).sum())}


def sensitivity_table(df: pd.DataFrame) -> pd.DataFrame:
    """Headline numbers per combination under four link filters."""
    filters = {
        "all_flown": df["flew"],
        "excl_contaminated": df["flew"] & ~df["contaminated"],
        "converged_only": df["flew"] & df["mission_converged"].eq(True),
        "clean_and_converged": df["flew"] & ~df["contaminated"] & df["mission_converged"].eq(True),
    }
    out = []
    for name, mask in filters.items():
        sub = df[mask]
        for c in COMBO_ORDER:
            out.append({"filter": name, "combo": c, **headline(sub[sub["combo"] == c])})
        out.append({"filter": name, "combo": "ALL", **headline(sub)})
    return pd.DataFrame(out)


def contamination_table(df: pd.DataFrame) -> pd.DataFrame:
    def links(mask) -> str:
        return " ".join(str(int(x)) for x in mask[mask].index)
    out = []
    for c, g in df.groupby("combo", observed=True):
        g = g.set_index("link")
        out.append({"combo": c, "did_not_fly": links(~g["flew"]), "failed": links(g["failed"]),
                    "failed_but_flew": links(g["failed"] & g["flew"]),
                    "b187_cycle": links(g["b187_cycle"]), "b187_inherited_only": links(g["b187_inherited_only"]),
                    "b214": links(g["b214"]),
                    "n_contaminated": int(g["contaminated"].sum()),
                    "n_contaminated_and_flown": int((g["contaminated"] & g["flew"]).sum())})
    return pd.DataFrame(out)


def failed_flew_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = ["combo", "link", "run_status", "fail_error", "flew", "fuel_kg", "gtow_kg", "mission_converged",
            "optimizer_message", "n_missions_flown", "has_result"]
    return df[df["failed"]][cols].reset_index(drop=True)


def within_link_table(df: pd.DataFrame) -> pd.DataFrame:
    """First mission of a link vs the link's final (last) mission, on links where both flew."""
    out = []
    for c, g in df.groupby("combo", observed=True):
        f = g[g["flew"] & (g["first_fuel_kg"] > ZERO_FUEL_FLOOR_KG)]
        d = f["fuel_kg"] - f["first_fuel_kg"]
        multi = f[f["n_missions_flown"] >= 2]
        distinct = g.loc[g["flew"], "n_distinct_flown_fuel"]
        out.append({"combo": c, "n_both_flew": len(f), "n_multi_mission": len(multi),
                    "n_links_gt1_distinct_fuel": int((distinct > 1).sum()),
                    "mean_distinct_fuel_per_link": distinct.mean(),
                    "mean_missions_flown": g.loc[g["flew"], "n_missions_flown"].mean(),
                    "n_lower_final": int((d < -SAME_FUEL_TOL_KG).sum()),
                    "n_higher_final": int((d > SAME_FUEL_TOL_KG).sum()),
                    "n_same": int((d.abs() <= SAME_FUEL_TOL_KG).sum()),
                    "mean_final_minus_first": d.mean(), "median_final_minus_first": d.median(),
                    "min_final_minus_first": d.min(), "max_final_minus_first": d.max()})
    return pd.DataFrame(out)


def chain_table(df: pd.DataFrame) -> pd.DataFrame:
    """Chaining checks: does link k start from link k-1's fuel, and how often does fuel not move at all."""
    out = []
    for c, g in df.groupby("combo", observed=True):
        g = g.sort_values("link")
        prev_fuel = g["fuel_kg"].shift(1)
        both = g["chain_start_fuel_kg"].notna() & prev_fuel.notna()
        match = (g["chain_start_fuel_kg"] - prev_fuel).abs() < SAME_FUEL_TOL_KG
        same = (g["fuel_kg"] - prev_fuel).abs() < SAME_FUEL_TOL_KG
        out.append({"combo": c, "n_chain_start_and_prev_flown": int(both.sum()),
                    "n_chain_start_equals_prev_fuel": int((both & match).sum()),
                    "n_chain_start_present": int(g["chain_start_fuel_kg"].notna().sum()),
                    "links_chain_start_not_prev_fuel": " ".join(
                        f"{int(r.link)}(start {r.chain_start_fuel_kg:.1f} vs prev {p:.1f})"
                        for r, p, ok in zip(g.itertuples(), prev_fuel, both & ~match) if ok),
                    "links_chain_start_but_prev_not_flown": " ".join(
                        str(int(x)) for x in g.loc[g["chain_start_fuel_kg"].notna() & prev_fuel.isna(), "link"]),
                    "n_fuel_same_as_prev_link": int(same.sum()),
                    "links_fuel_same_as_prev": " ".join(str(int(x)) for x in g.loc[same, "link"]),
                    "longest_same_fuel_run": longest_run(same.tolist())})
    return pd.DataFrame(out)


def link1_table(df: pd.DataFrame) -> pd.DataFrame:
    """Link 1 starts from the anchor design, but its first mission already carries the agents' changes."""
    g = df[df["link"] == 1]
    return pd.DataFrame({"combo": g["combo"], "flew": g["flew"], "first_fuel_kg": g["first_fuel_kg"],
                         "first_vs_base_kg": g["first_fuel_kg"] - BASELINE["fuel_kg"],
                         "first_vs_base_pct": 100 * (g["first_fuel_kg"] - BASELINE["fuel_kg"]) / BASELINE["fuel_kg"],
                         "fuel_kg": g["fuel_kg"]}).reset_index(drop=True)


def longest_run(flags: list[bool]) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def write(table: pd.DataFrame, out: Path, name: str) -> None:
    table.to_csv(out / f"{name}.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", 60, "display.max_rows", 200,
                           "display.float_format", "{:,.1f}".format):
        print(f"\n=== {name} ===\n{table.to_string(index=False)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    df = add_raw_convergence(load_links(args.links))

    for col in ["fuel_kg", "gtow_kg", "wing_mass_kg"]:
        write(metric_summary(df, col), args.out, f"summary_{col}")
    for group in ["structure", "handler"]:
        write(metric_summary(df, "fuel_kg", group), args.out, f"summary_fuel_kg_by_{group}")
    write(constraints_table(df), args.out, "constraints")
    write(convergence_table(df), args.out, "convergence")
    write(low_fuel_table(df), args.out, "below_baseline_links")
    write(contamination_table(df), args.out, "contamination")
    write(failed_flew_table(df), args.out, "failed_links")
    write(sensitivity_table(df), args.out, "sensitivity")
    write(within_link_table(df), args.out, "within_link")
    write(chain_table(df), args.out, "chain")
    write(link1_table(df), args.out, "link1")
    cols = ["combo", "link", "run_status", "flew", "fuel_kg", "first_fuel_kg", "gtow_kg", "wing_mass_kg",
            "n_missions_flown", "n_distinct_flown_fuel", "mission_converged", "ec_converged", "optimizer_message",
            "violated_constraint_count", "chain_start_fuel_kg", "b187_cycle", "b187_inherited_only", "b214",
            "contaminated"]
    df[cols].to_csv(args.out / "links_physics.csv", index=False)
    print(f"\nwrote tables to {args.out}")


if __name__ == "__main__":
    main()
