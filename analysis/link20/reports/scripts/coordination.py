"""Coordination / organisational-design tables for the 20-link reports (04_coordination_org.md).

One family of tables per structure / handler: activity common to all combinations, staged pipeline, iterative
feedback, graph-routed, orchestrated, networked, the recorded org-theory metrics (od.*), pooled structure and handler
comparisons, trends over the 20 links, and coordination activity next to the outcome (flew / fuel).

Every number is read from links.csv (analysis/link20/extract.py) or, for the checks at the end, from the raw run
files. Gaps stay gaps: a metric a link did not record is left out of that combination's n, never set to 0.

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.reports.scripts.coordination --links ../out_20261028/links.csv \
        --out ../reports_20261028/coordination
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from analysis.link20.extract import SOURCES, read_json, read_jsonl

ABBREV = {"sequential": "S", "orchestrated": "O", "networked": "N",
          "staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}
ORDER = ["S_SP", "S_IF", "S_GR", "O_SP", "O_IF", "O_GR", "N_IF", "N_GR"]

COMMON = ["agent_steps", "total_turns", "n_agents_stepping", "step_log_attempts"]
STAGED = ["stage_count", "stages_not_met", "stages_redone", "early_abort", "od.completion_rate",
          "od.completion_rate_workers"]
ITER = ["n_retry_messages", "stuck_detected", "early_abort", "mean_attempts_to_success", "od.ambidexterity_score",
        "od.escalation_length"]
GRAPH = ["route_length", "backward_moves", "graph_level_num", "graph_level_changes", "n_request_complexity",
         "n_set_complexity", "graph_reopens", "routing_accuracy_1m_misroute", "od.misroute_rate"]
ORCH = ["orchestrator_steps", "orchestrator_turns", "worker_turns", "agents_spawned", "n_create_agent",
        "n_assign_task", "n_message_orchestrator", "n_reply_to_agent", "n_set_complexity",
        "od.orchestrator_overhead_ratio", "orch_step_share", "od.cost_per_agent", "od.information_ratio"]
NET = ["n_read_blackboard", "n_write_blackboard", "n_read_todos", "n_claim_todo", "n_claim_granted",
       "n_claim_refused", "claim_conflicts", "claim_violations_total", "n_wait_for_board", "n_mark_todo_done",
       "n_mark_todo_failed", "n_release_todo", "n_spawn_peer", "n_request_complexity", "agents_spawned",
       "blackboard_reads_total", "blackboard_writes_total", "od.todo_revisit_rate", "od.duplicate_work_rate",
       "od.blackboard_utilization", "od.convergence_score"]
OD = ["od.misroute_rate", "od.ambidexterity_score", "od.completion_rate", "od.completion_rate_workers",
      "od.propagation_time", "od.cost_per_agent", "od.convergence_score", "od.orchestrator_overhead_ratio",
      "od.information_ratio", "od.blackboard_utilization", "od.duplicate_work_rate", "od.escalation_length",
      "od.stage_independence_score", "od.todo_revisit_rate"]
TREND = COMMON + ["n_retry_messages", "stuck_detected", "route_length", "backward_moves", "graph_level_changes",
                  "orchestrator_steps", "n_assign_task", "n_message_orchestrator", "n_claim_granted",
                  "n_claim_refused", "n_wait_for_board", "n_mark_todo_failed", "od.todo_revisit_rate",
                  "od.completion_rate_workers", "od.ambidexterity_score", "od.misroute_rate",
                  "od.orchestrator_overhead_ratio", "od.convergence_score", "od.duplicate_work_rate"]
OUTCOME = COMMON + ["orchestrator_steps", "n_retry_messages", "route_length", "n_claim_granted",
                    "n_mark_todo_failed"]
# Metrics that belong to only some combinations (others are left out of their table, not shown as 0).
APPLIES = {
    "staged": ["S_SP", "O_SP"], "iter": ["S_IF", "O_IF", "N_IF"], "graph": ["S_GR", "O_GR", "N_GR"],
    "orch": ["O_SP", "O_IF", "O_GR"], "net": ["N_IF", "N_GR"],
}


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["ab"] = df.structure.map(ABBREV) + "_" + df.handler.map(ABBREV)
    df["flew"] = df["flew"].astype(bool)
    df["graph_level_num"] = df["graph_level"].map({"simple": 1, "moderate": 2, "complex": 3})
    # Recorded routing_accuracy is 0 by construction (counts states named output_reviewer/COMPLETE); use
    # 1 - misroute_rate, and not for networked graph-routed (its misroute counts peers, not roles).
    df["routing_accuracy_1m_misroute"] = np.where(df.ab.isin(["S_GR", "O_GR"]), 1.0 - df["od.misroute_rate"], np.nan)
    # Step-based orchestrator share (derived here): orchestrator agent steps / all agent steps in the link.
    df["orch_step_share"] = np.where(df.structure == "orchestrated", df.orchestrator_steps / df.agent_steps, np.nan)
    return df


def describe(df: pd.DataFrame, cols: list[str], combos: list[str]) -> pd.DataFrame:
    rows = []
    for ab in combos:
        g = df[df.ab == ab]
        for c in cols:
            v = pd.to_numeric(g[c], errors="coerce").dropna()
            rows.append({"combo": ab, "metric": c, "n": len(v),
                         "mean": v.mean() if len(v) else np.nan, "std": v.std(ddof=1) if len(v) > 1 else np.nan,
                         "median": v.median() if len(v) else np.nan, "min": v.min() if len(v) else np.nan,
                         "max": v.max() if len(v) else np.nan, "sum": v.sum() if len(v) else np.nan,
                         "links_gt0": int((v > 0).sum())})
    return pd.DataFrame(rows)


def wide(d: pd.DataFrame) -> pd.DataFrame:
    """mean (n) per combo x metric, for the report tables."""
    d = d.copy()
    d["cell"] = [f"{m:.3g} ± {s:.3g} (n={n})" if n > 1 and pd.notna(s) else (f"{m:.3g} (n={n})" if n else "gap")
                 for m, s, n in zip(d["mean"], d["std"], d["n"])]
    return d.pivot(index="metric", columns="combo", values="cell")


def links_list(g: pd.DataFrame, mask) -> str:
    v = g.loc[mask, "link"].astype(int).tolist()
    return ", ".join(map(str, v)) if v else "none"


def graph_stop(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ab in APPLIES["graph"]:
        g = df[df.ab == ab].sort_values("link")
        cnt = Counter(g.graph_stop_reason.fillna("(none recorded)"))
        for reason, n in sorted(cnt.items()):
            rows.append({"combo": ab, "stop_reason": reason, "links": n,
                         "which": links_list(g, g.graph_stop_reason.fillna("(none recorded)") == reason)})
    return pd.DataFrame(rows)


def graph_levels(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ab in APPLIES["graph"]:
        g = df[df.ab == ab]
        cnt = Counter(g.graph_level.fillna("(none)"))
        rows.append({"combo": ab, **{k: cnt.get(k, 0) for k in ("simple", "moderate", "complex", "(none)")}})
    return pd.DataFrame(rows)


def pooled(df: pd.DataFrame, by: str, cols: list[str]) -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(by):
        for c in cols:
            v = pd.to_numeric(g[c], errors="coerce").dropna()
            rows.append({by: key, "metric": c, "n_links": len(v), "n_combos": g.loc[v.index, "ab"].nunique(),
                         "mean": v.mean(), "std": v.std(ddof=1), "median": v.median()})
    return pd.DataFrame(rows)


def trends(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ab in ORDER:
        g = df[df.ab == ab]
        for c in TREND:
            d = g[["link", c]].apply(pd.to_numeric, errors="coerce").dropna()
            if len(d) < 5 or d[c].nunique() < 2:
                continue
            rho, p = spearmanr(d.link, d[c])
            first, last = d[d.link <= 10][c], d[d.link > 10][c]
            rows.append({"combo": ab, "metric": c, "n": len(d), "spearman_rho": rho, "p": p,
                         "mean_links_1_10": first.mean(), "n_1_10": len(first),
                         "mean_links_11_20": last.mean(), "n_11_20": len(last)})
    return pd.DataFrame(rows)


def outcome(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, corr = [], []
    for ab in ORDER:
        g = df[df.ab == ab]
        for c in OUTCOME:
            v = pd.to_numeric(g[c], errors="coerce")
            fl, nf = v[g.flew].dropna(), v[~g.flew].dropna()
            rows.append({"combo": ab, "metric": c, "mean_flew": fl.mean(), "n_flew": len(fl),
                         "mean_not_flew": nf.mean(), "n_not_flew": len(nf)})
            d = pd.DataFrame({"x": v, "fuel": g.fuel_kg}).dropna()
            if len(d) >= 5 and d.x.nunique() > 1:
                rho, p = spearmanr(d.x, d.fuel)
                corr.append({"combo": ab, "metric": c, "n_flown": len(d), "spearman_rho_vs_fuel": rho, "p": p})
    return pd.DataFrame(rows), pd.DataFrame(corr)


def pooled_outcome(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for c in OUTCOME:
        d = df[["ab", "flew", "fuel_kg", c]].copy()
        d[c] = pd.to_numeric(d[c], errors="coerce")
        d = d.dropna(subset=[c])
        flew_rate = d.groupby("ab").flew.mean()
        means = d.groupby("ab")[c].mean()
        fuel = df[df.flew].groupby("ab").fuel_kg.mean()
        idx = [a for a in ORDER if a in means.index and (d[d.ab == a][c] != 0).any()]
        if len(idx) < 5:
            continue
        rho_fly, p_fly = spearmanr(means.loc[idx], flew_rate.loc[idx])
        rho_fuel, p_fuel = spearmanr(means.loc[idx], fuel.loc[idx])
        rows.append({"metric": c, "n_combos": len(idx), "rho_combo_mean_vs_share_flew": rho_fly, "p_fly": p_fly,
                     "rho_combo_mean_vs_mean_fuel": rho_fuel, "p_fuel": p_fuel})
    return pd.DataFrame(rows)


def basic_flight(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ab in ORDER:
        g = df[df.ab == ab]
        f = g[g.flew].fuel_kg
        rows.append({"combo": ab, "links": len(g), "flew": int(g.flew.sum()), "mean_fuel_kg_flown": f.mean(),
                     "n_fuel": len(f), "dataset": g.dataset.iloc[0]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- raw-file checks

def link_dirs():
    for ds, root, combos in SOURCES:
        for combo in combos:
            for d in sorted(root.glob(f"repeat_*/{combo}")):
                s, h = combo.removeprefix("mdo_f25_").split("_", 1)
                yield f"{ABBREV[s]}_{ABBREV[h]}", int(d.parent.name.split("_")[1]) + 1, d


def raw_checks() -> dict[str, pd.DataFrame]:
    abort_rows, tok_rows, warn_rows = [], [], []
    for ab, link, d in link_dirs():
        res = read_json(d / "result.json")
        if not res:
            continue
        msgs = res.get("messages") or []
        # 1. Staged early_abort: which stage carries it, did later stages still run, and does the KB show a
        #    set_aircraft_parameters by that agent with valid == true (which _validation_exhausted should have seen).
        if ab in APPLIES["staged"]:
            kb = read_jsonl(d / "knowledge_base.jsonl")
            for i, m in enumerate(msgs):
                ea = (m.get("metadata") or {}).get("early_abort")
                if not ea:
                    continue
                agent = m.get("agent_name")
                sap = [e for e in kb if e.get("tool") == "set_aircraft_parameters" and e.get("agent") == agent]
                abort_rows.append({
                    "combo": ab, "link": link, "stage": agent, "value": ea,
                    "worker_stages_after": sum(1 for x in msgs[i + 1:] if x.get("agent_name") != "orchestrator"),
                    "kb_set_params_by_agent": len(sap),
                    "kb_any_valid_true": any((e.get("outputs") or {}).get("valid") is True for e in sap)})
        # 2. token_count on orchestrated messages (input of information_ratio).
        if ab in APPLIES["orch"]:
            orch = [m.get("token_count") for m in msgs if m.get("agent_name") == "orchestrator"]
            work = [m.get("token_count") for m in msgs if m.get("agent_name") != "orchestrator"]
            tok_rows.append({"combo": ab, "link": link, "orch_msgs": len(orch),
                             "orch_token_values": json.dumps(sorted(set(map(str, orch)))),
                             "worker_msgs": len(work), "worker_token_none": sum(1 for t in work if t is None)})
        # 3. Warnings the metric code recorded (fallback estimates / defaults).
        for w in (res.get("org_theory_metrics") or {}).get("warnings") or []:
            warn_rows.append({"combo": ab, "link": link, "warning": w.split(":")[0] + ": " +
                              (w.split(":", 1)[1].strip()[:70] if ":" in w else "")})
    warn = pd.DataFrame(warn_rows)
    warn_sum = (warn.groupby(["combo", "warning"]).size().rename("links").reset_index()
                if len(warn) else warn)
    return {"check_early_abort": pd.DataFrame(abort_rows), "check_orch_token_counts": pd.DataFrame(tok_rows),
            "check_od_warnings": warn_sum}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_rows", 500)
    pd.set_option("display.max_colwidth", 120)
    df = load(args.links)

    tables = {
        "flight_context": basic_flight(df),
        "common": describe(df, COMMON + ["orchestrator_steps"], ORDER),
        "staged": describe(df, STAGED, APPLIES["staged"]),
        "iterative": describe(df, ITER, APPLIES["iter"]),
        "graph": describe(df, GRAPH, APPLIES["graph"]),
        "graph_stop_reasons": graph_stop(df),
        "graph_levels": graph_levels(df),
        "orchestrated": describe(df, ORCH, APPLIES["orch"]),
        "networked": describe(df, NET, APPLIES["net"]),
        "od_metrics": describe(df, OD, ORDER).query("n > 0"),
        "pooled_structure": pooled(df, "structure", COMMON),
        "pooled_handler": pooled(df, "handler", COMMON),
        "trends": trends(df),
    }
    tables["outcome_flew_vs_not"], tables["outcome_corr_fuel"] = outcome(df)
    tables["outcome_pooled_combo_level"] = pooled_outcome(df)
    # Link lists behind the counts the report quotes.
    lists = []
    for ab in ORDER:
        g = df[df.ab == ab].sort_values("link")
        lists.append({"combo": ab,
                      "multi_attempt_links": links_list(g, g.step_log_attempts > 1),
                      "no_result_links": links_list(g, ~g.has_result.astype(bool)),
                      "did_not_fly_links": links_list(g, ~g.flew),
                      "retry_links": links_list(g, g.n_retry_messages > 0),
                      "stuck_links": links_list(g, g.stuck_detected > 0),
                      "ambidexterity_defined_links": links_list(g, g["od.ambidexterity_score"].notna()),
                      "spawn_peer_links": links_list(g, g.n_spawn_peer > 0),
                      "claim_violation_links": links_list(g, g.claim_violations_total > 0),
                      "graph_reopen_links": links_list(g, g.graph_reopens > 0)})
    tables["link_lists"] = pd.DataFrame(lists)
    # How many of the exploratory Spearman tests reach p < 0.05 (multiple-comparison context for the report).
    tables["test_counts"] = pd.DataFrame([
        {"family": name, "tests": len(t), "p_lt_0.05": int((t[p] < 0.05).sum()),
         "which": "; ".join(f"{r.combo if 'combo' in t else ''} {r.metric}" for r in t[t[p] < 0.05].itertuples())}
        for name, t, p in [("trends", tables["trends"], "p"), ("outcome_corr_fuel", tables["outcome_corr_fuel"], "p"),
                           ("outcome_pooled_vs_flew", tables["outcome_pooled_combo_level"], "p_fly"),
                           ("outcome_pooled_vs_fuel", tables["outcome_pooled_combo_level"], "p_fuel")]])
    # The link with the most agent steps / orchestrator steps / assign_task calls per combination (outliers).
    ext = []
    for ab in ORDER:
        g = df[df.ab == ab]
        for c in ("agent_steps", "orchestrator_steps", "n_assign_task", "n_message_orchestrator", "total_turns"):
            v = pd.to_numeric(g[c], errors="coerce")
            if v.notna().any() and v.max() > 0:
                i = v.idxmax()
                ext.append({"combo": ab, "metric": c, "max": v.max(), "link": int(g.loc[i, "link"]),
                            "median": v.median(), "n": int(v.notna().sum()),
                            "link_flew": bool(g.loc[i, "flew"]), "link_status": g.loc[i, "run_status"]})
    tables["max_links"] = pd.DataFrame(ext)
    tables.update(raw_checks())

    for name, t in tables.items():
        t.to_csv(args.out / f"{name}.csv", index=False)
        print(f"\n===== {name} =====")
        if name in ("common", "staged", "iterative", "graph", "orchestrated", "networked", "od_metrics"):
            print(wide(t).to_string())
            print(t[["combo", "metric", "n", "median", "min", "max", "sum", "links_gt0"]].to_string(index=False))
        else:
            print(t.to_string(index=False, float_format=lambda x: f"{x:.4g}"))
    print(f"\nwrote {len(tables)} tables to {args.out}")


if __name__ == "__main__":
    main()
