"""Efficiency tables for the 20-link study (report 03_efficiency.md): time, tokens and compute effort.

Read-only. Reads links.csv (analysis/link20/extract.py) and, for what links.csv doesn't hold, the raw per-link
files of the runs (paths = extract.SOURCES): step_log_attempt<k>.jsonl (per-agent output tokens, prompt tokens
as sent, generations, thinking text, step-log spans of every attempt), result.json (context trims, generation
waits, summary-model time) and knowledge_base.jsonl (the runtime_seconds SU2 and aviary report themselves).

Every number in the report comes from the CSVs this writes. Nothing is interpolated: a value a link did not
record stays empty.

Usage (on aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.reports.scripts.efficiency \
        --links ~/Jessica/avion_analysis/out_20261028/links.csv \
        --out ~/Jessica/avion_analysis/reports_20261028/efficiency
"""

from __future__ import annotations

import argparse
import warnings
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from analysis.link20.extract import SOURCES, counted_step_log, read_json, read_jsonl

BASELINE_FUEL_KG = 18197.0          # unchanged anchor design, flown 2026-10-22 (shared brief)
TIMEOUT_S = 480 * 60                # runner's per-link timeout
COMBOS = [("sequential", "staged_pipeline"), ("sequential", "iterative_feedback"), ("sequential", "graph_routed"),
          ("orchestrated", "staged_pipeline"), ("orchestrated", "iterative_feedback"),
          ("orchestrated", "graph_routed"), ("networked", "iterative_feedback"), ("networked", "graph_routed")]
ABBR = {(s, h): f"{s[0].upper()}_{''.join(w[0].upper() for w in h.split('_'))}" for s, h in COMBOS}

# Same grouping as plots.py time_tables (5b_time_breakdown), so the report matches the plots.
MODEL_ONLY = {"(no tool call)", "final_answer"}
BOARD = {"read_todos", "claim_todo", "mark_todo_done", "mark_todo_failed", "release_todo", "wait_for_board",
         "read_blackboard", "write_blackboard", "spawn_peer", "request_complexity", "mark_task_done", "read_procedure"}
ORCH = {"create_agent", "assign_task", "message_orchestrator", "reply_to_agent", "set_complexity", "give_tools",
        "list_graph_roles"}
SIGNALS = {"record_recommendation", "respond_to_recommendation"}
SERVERS = ["tigl", "su2", "mass", "pycycle", "aviary"]
GROUPS = ["no tool call", "final_answer", "recommendations", "orchestrator tools", "board tools",
          "tigl", "su2", "mass", "pycycle", "aviary", "other framework"]


def tool_group(tool: str, servers: dict) -> str:
    if tool == "(no tool call)":
        return "no tool call"
    if tool == "final_answer":
        return "final_answer"
    if tool in SIGNALS:
        return "recommendations"
    if tool in ORCH:
        return "orchestrator tools"
    if tool in BOARD:
        return "board tools"
    srv = str(servers.get(tool) or "").lower()
    for key in SERVERS:
        if key in srv:
            return key
    return "other framework"


def link_dirs() -> dict:
    out = {}
    for _, root, combos in SOURCES:
        for combo in combos:
            for d in sorted(root.glob(f"repeat_*/{combo}")):
                out[(combo, int(d.parent.name.split("_")[1]) + 1)] = d
    return out


def raw_link(d: Path) -> dict:
    """Numbers links.csv doesn't hold, from the raw files of one link."""
    k, path = counted_step_log(d)
    rows = read_jsonl(path) if path else []
    steps = [s for s in rows if s.get("kind") == "step"]
    summaries = [s for s in rows if s.get("kind") == "summary"]
    tok_by_agent, steps_by_agent = Counter(), Counter()
    n_gen = n_gen_prompt = n_gen_think = n_gen_think_enabled = n_gen_first = 0
    tok_think_gens = tok_think_enabled = 0
    prompt_tokens = []
    think_chars = visible_chars = 0
    for s in steps:
        a = s.get("agent")
        steps_by_agent[a] += 1
        for g in s.get("generations") or []:
            n_gen += 1
            tok_by_agent[a] += g.get("output_tokens") or 0
            p = g.get("prompt")
            if isinstance(p, dict) and p.get("prompt_tokens") is not None:
                n_gen_prompt += 1
                prompt_tokens.append(int(p["prompt_tokens"]))
            if not g.get("attempt"):
                n_gen_first += 1
            if g.get("thinking_enabled"):
                n_gen_think_enabled += 1
                tok_think_enabled += g.get("output_tokens") or 0
            if g.get("think"):
                n_gen_think += 1
                tok_think_gens += g.get("output_tokens") or 0
                think_chars += len(g["think"])
            visible_chars += len(g.get("visible") or "")
    starts = [float(s["start_time"]) for s in steps if s.get("start_time") is not None]
    ends = [float(s["end_time"]) for s in steps if s.get("end_time") is not None]
    # Earlier (discarded) attempts: wall span of their step logs, a lower bound on the time they took.
    earlier_span = earlier_step = 0.0
    for p in d.glob("step_log_attempt*.jsonl"):
        m = re.fullmatch(r"step_log_attempt(\d+)\.jsonl", p.name)
        if not m or int(m.group(1)) == k:
            continue
        st = [r for r in read_jsonl(p) if r.get("kind") == "step"]
        a0 = [float(r["start_time"]) for r in st if r.get("start_time") is not None]
        a1 = [float(r["end_time"]) for r in st if r.get("end_time") is not None]
        if a0 and a1:
            earlier_span += max(a1) - min(a0)
        earlier_step += sum(float(r["end_time"]) - float(r["start_time"]) for r in st
                            if r.get("start_time") is not None and r.get("end_time") is not None)
    res = read_json(d / "result.json") or {}
    kb = read_jsonl(d / "knowledge_base.jsonl")
    su2_rt = sum(float((e.get("outputs") or {}).get("runtime_seconds") or 0) for e in kb
                 if e.get("tool") == "run_su2_solver" and e.get("status") == "success")
    av_rt = sum(float((e.get("outputs") or {}).get("runtime_seconds") or 0) for e in kb
                if e.get("tool") == "run_simulation" and e.get("status") == "success")
    orch_tok = tok_by_agent.get("orchestrator", 0)
    tot = sum(tok_by_agent.values())
    return {
        "step_span_s": (max(ends) - min(starts)) if starts and ends else None,
        "earlier_attempts_span_s": earlier_span, "earlier_attempts_step_time_s": earlier_step,
        "n_generations_thinking_enabled": n_gen_think_enabled, "n_generations_first_attempt": n_gen_first,
        "output_tokens_thinking_enabled": tok_think_enabled, "output_tokens_gens_with_think": tok_think_gens,
        "n_generations": n_gen, "n_generations_with_prompt_tokens": n_gen_prompt,
        "prompt_tokens_sent_sum": sum(prompt_tokens) if prompt_tokens else None,
        "prompt_tokens_sent_max": max(prompt_tokens) if prompt_tokens else None,
        "n_generations_with_think": n_gen_think,
        "think_chars": think_chars, "visible_chars": visible_chars,
        "orchestrator_output_tokens": orch_tok,
        "max_agent_output_tokens": max(tok_by_agent.values()) if tok_by_agent else None,
        "n_summary_calls_steplog": len(summaries),
        "su2_runtime_reported_s": su2_rt, "aviary_runtime_reported_s": av_rt,
        "n_su2_solves": sum(1 for e in kb if e.get("tool") == "run_su2_solver" and e.get("status") == "success"),
        "kb_summary_seconds": res.get("kb_summary_seconds"), "context_trims": res.get("context_trims"),
        "prompt_over_limit_count": res.get("prompt_over_limit_count"),
        "message_truncations": res.get("message_truncations"),
        "max_concurrent_generations": res.get("max_concurrent_generations"),
        "generation_wait_seconds": res.get("generation_wait_seconds"),
        "result_generations": res.get("generations"),
        "orch_token_share": orch_tok / tot if tot else None,
    }


def describe(x: pd.Series) -> dict:
    x = x.dropna()
    if x.empty:
        return {"n": 0}
    return {"n": int(len(x)), "mean": x.mean(), "median": x.median(), "min": x.min(), "max": x.max(),
            "total": x.sum()}


def spearman(x, y) -> float | None:
    m = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(m) < 3:
        return None
    return float(np.corrcoef(m.x.rank(), m.y.rank())[0, 1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    warnings.simplefilter("ignore", pd.errors.PerformanceWarning)
    args.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 60)

    df = pd.read_csv(args.links).copy()
    df["combo"] = [ABBR[(s, h)] for s, h in zip(df.structure, df.handler)]
    df["timed_out"] = df.fail_error.fillna("").str.contains("480m timeout")
    dirs = link_dirs()
    raw = pd.DataFrame([{"combination": c, "link": l, **raw_link(d)} for (c, l), d in dirs.items()])
    df = df.merge(raw, on=["combination", "link"], how="left").copy()
    df["hours"] = df.duration_s / 3600
    df["step_hours"] = df.step_time_s / 3600
    df["ktok"] = df.output_tokens / 1000
    df["tok_per_min"] = df.output_tokens / (df.duration_s / 60)
    df["tok_per_agent_k"] = df.output_tokens / df.n_agents_stepping / 1000
    df["orch_step_share"] = np.where(df.structure == "orchestrated", df.orchestrator_steps / df.agent_steps, np.nan)
    df["step_over_duration"] = df.step_time_s / df.duration_s
    df["step_over_span"] = df.step_time_s / df.step_span_s
    df["span_over_duration"] = df.step_span_s / df.duration_s
    df["fuel_saved_kg"] = np.where(df.flew, BASELINE_FUEL_KG - df.fuel_kg, np.nan)
    df["prompt_ktok_sent"] = df.prompt_tokens_sent_sum / 1000
    df["think_gen_share"] = df.n_generations_with_think / df.n_generations
    df["su2_plus_aviary_reported_h"] = (df.su2_runtime_reported_s + df.aviary_runtime_reported_s) / 3600
    df["reported_sim_share_of_duration"] = (df.su2_runtime_reported_s + df.aviary_runtime_reported_s) / df.duration_s
    df.to_csv(args.out / "efficiency_links.csv", index=False)

    order = [ABBR[c] for c in COMBOS]

    def by_combo(col: str, name: str, scale=1.0) -> pd.DataFrame:
        t = pd.DataFrame({c: describe(df.loc[df.combo == c, col] * scale) for c in order}).T
        t.index.name = "combo"
        t.to_csv(args.out / f"{name}.csv")
        print(f"\n=== {name} ({col}) ===")
        print(t.round(3).to_string())
        return t

    # 1. duration and failures
    by_combo("hours", "duration_hours")
    st = df.groupby("combo").agg(links=("link", "size"), with_duration=("duration_s", "count"),
                                 failed=("run_status", lambda s: int((s == "failed").sum())),
                                 timed_out=("timed_out", "sum"),
                                 durations_ge_480min=("duration_s", lambda s: int((s >= TIMEOUT_S).sum())),
                                 links_retried=("step_log_attempts", lambda s: int((s > 1).sum())),
                                 earlier_attempts_span_h=("earlier_attempts_span_s", lambda s: s.sum() / 3600),
                                 earlier_attempts_step_h=("earlier_attempts_step_time_s", lambda s: s.sum() / 3600),
                                 ).reindex(order)
    st.to_csv(args.out / "status_timeouts.csv")
    print("\n=== status_timeouts ===\n", st.round(3).to_string())
    print("\nfailed links:\n", df[df.run_status == "failed"][["combo", "link", "fail_error", "step_span_s",
                                                              "step_time_s", "output_tokens", "flew"]].to_string())
    print("\nretried links:\n", df[df.step_log_attempts > 1][["combo", "link", "step_log_attempts",
                                                             "earlier_attempts_span_s", "earlier_attempts_step_time_s",
                                                             "duration_s"]].to_string())
    print("\nstep-log span vs duration (links with both):")
    print(df.groupby("combo").span_over_duration.describe().reindex(order).round(4).to_string())

    # 2. tokens
    by_combo("ktok", "output_ktokens")
    by_combo("tok_per_min", "output_tokens_per_min")
    by_combo("tok_per_agent_k", "output_ktokens_per_stepping_agent")
    by_combo("max_agent_output_tokens", "max_agent_output_ktokens", 1 / 1000)
    by_combo("orch_token_share", "orchestrator_output_token_share")
    by_combo("max_prompt_tokens", "max_prompt_tokens_untrimmed")
    by_combo("prompt_tokens_sent_max", "max_prompt_tokens_sent")
    by_combo("prompt_ktok_sent", "prompt_ktokens_sent_sum")
    by_combo("context_trims", "context_trims")
    cov = df.groupby("combo").agg(gens=("n_generations", "sum"), gens_with_prompt=("n_generations_with_prompt_tokens", "sum"),
                                  gens_with_think=("n_generations_with_think", "sum"),
                                  gens_thinking_enabled=("n_generations_thinking_enabled", "sum"),
                                  gens_first_attempt=("n_generations_first_attempt", "sum"),
                                  result_json_generations=("result_generations", "sum"),
                                  out_tok=("output_tokens", "sum"),
                                  out_tok_thinking_enabled=("output_tokens_thinking_enabled", "sum"),
                                  out_tok_gens_with_think=("output_tokens_gens_with_think", "sum"),
                                  think_chars=("think_chars", "sum"), visible_chars=("visible_chars", "sum"),
                                  prompt_over_limit=("prompt_over_limit_count", "sum"),
                                  message_truncations=("message_truncations", "sum"),
                                  summary_calls_steplog=("n_summary_calls_steplog", "sum"),
                                  kb_summary_h=("kb_summary_seconds", lambda s: s.sum() / 3600)).reindex(order)
    cov["think_char_share"] = cov.think_chars / (cov.think_chars + cov.visible_chars)
    cov["tok_share_thinking_enabled"] = cov.out_tok_thinking_enabled / cov.out_tok
    cov["tok_share_gens_with_think"] = cov.out_tok_gens_with_think / cov.out_tok
    cov["out_tokens_per_generation"] = df.groupby("combo").output_tokens.sum().reindex(order) / cov.gens
    cov.to_csv(args.out / "generation_coverage.csv")
    print("\n=== generation_coverage ===\n", cov.round(4).to_string())

    # 3. steps
    by_combo("agent_steps", "agent_steps")
    by_combo("n_agents_stepping", "agents_stepping")
    by_combo("orch_step_share", "orchestrator_step_share")

    # 4. where the step time goes
    servers = {}
    for js in df.tool_servers.dropna():
        servers.update(json.loads(js))
    rows, tool_rows = [], []
    for _, r in df.iterrows():
        if not isinstance(r.step_time_by_tool, str):
            continue
        g = Counter()
        for t, sec in json.loads(r.step_time_by_tool).items():
            g[tool_group(t, servers)] += sec
            tool_rows.append({"combo": r.combo, "link": r.link, "tool": t, "group": tool_group(t, servers), "s": sec})
        rows.append({"combo": r.combo, "link": r.link, **g})
    grp = pd.DataFrame(rows).fillna(0.0)
    for c in GROUPS:
        if c not in grp:
            grp[c] = 0.0
    tot_h = grp.groupby("combo")[GROUPS].sum().reindex(order) / 3600
    tot_h["n_links"] = grp.groupby("combo").size().reindex(order)
    tot_h["all_h"] = tot_h[GROUPS].sum(axis=1)
    tot_h.to_csv(args.out / "step_time_by_group_total_hours.csv")
    share = tot_h[GROUPS].div(tot_h.all_h, axis=0) * 100
    share["n_links"] = tot_h.n_links
    share.to_csv(args.out / "step_time_by_group_share_pct.csv")
    print("\n=== step time by group, total hours over all links ===\n", tot_h.round(2).to_string())
    print("\n=== step time by group, share % ===\n", share.round(1).to_string())
    all_tot = tot_h[GROUPS].sum()
    print("\nall combos, hours by group:\n", all_tot.round(2).to_string(), "\n total", round(all_tot.sum(), 2))
    tr = pd.DataFrame(tool_rows)
    top = (tr.groupby(["combo", "tool", "group"]).s.sum() / 3600).rename("hours").reset_index()
    top["share_pct"] = top.hours / top.groupby("combo").hours.transform("sum") * 100
    top = top.sort_values(["combo", "hours"], ascending=[True, False])
    top.to_csv(args.out / "step_time_by_tool_hours.csv", index=False)
    print("\n=== top 6 tools per combo (hours, share %) ===")
    for c in order:
        print(c, top[top.combo == c].head(6)[["tool", "group", "hours", "share_pct"]].round(2).values.tolist())
    # reported simulation runtimes (the only tool-side times recorded)
    rt = df.groupby("combo").agg(n=("duration_s", "count"), su2_solves=("n_su2_solves", "sum"),
                                 su2_reported_h=("su2_runtime_reported_s", lambda s: s.sum() / 3600),
                                 aviary_reported_h=("aviary_runtime_reported_s", lambda s: s.sum() / 3600),
                                 su2_group_step_h=("combo", lambda s: np.nan)).reindex(order)
    rt["su2_group_step_h"] = tot_h["su2"]
    rt["aviary_group_step_h"] = tot_h["aviary"]
    rt["reported_sim_share_of_duration_mean"] = df.groupby("combo").reported_sim_share_of_duration.mean().reindex(order)
    rt["reported_sim_share_n"] = df.groupby("combo").reported_sim_share_of_duration.count().reindex(order)
    rt.to_csv(args.out / "reported_sim_runtimes.csv")
    print("\n=== tool-reported simulation runtimes ===\n", rt.round(3).to_string())

    # 5. effort vs outcome (flown links only)
    fl = df[df.flew & df.duration_s.notna()]
    eo = []
    for c in order:
        for label, g in (("flown", fl[fl.combo == c]), ("flown_converged", fl[(fl.combo == c) & (fl.ec_converged == True)])):  # noqa: E712
            g2 = df[(df.combo == c) & df.flew]
            eo.append({"combo": c, "subset": label, "n": len(g),
                       "mean_hours": g.hours.mean(), "mean_ktok": g.ktok.mean(),
                       "mean_fuel_saved_kg": g.fuel_saved_kg.mean(),
                       "sum_saved_per_sum_hour_kg_h": g.fuel_saved_kg.sum() / g.hours.sum() if len(g) else np.nan,
                       "sum_saved_per_sum_Mtok_kg": g.fuel_saved_kg.sum() / (g.output_tokens.sum() / 1e6) if len(g) else np.nan,
                       "median_saved_per_hour_kg_h": (g.fuel_saved_kg / g.hours).median(),
                       "median_saved_per_Mtok_kg": (g.fuel_saved_kg / (g.output_tokens / 1e6)).median(),
                       "n_flown_incl_no_duration": len(g2) if label == "flown" else np.nan})
    eo = pd.DataFrame(eo)
    eo.to_csv(args.out / "effort_vs_outcome.csv", index=False)
    print("\n=== effort vs outcome (flown links with a duration) ===\n", eo.round(2).to_string())
    # whole-chain effort: everything spent on the 20 links vs the best/last fuel of the chain
    ch = []
    for c in order:
        g = df[df.combo == c]
        f = g[g.flew]
        fc = f[f.ec_converged == True]  # noqa: E712
        ch.append({"combo": c, "links": len(g), "hours_total_recorded": g.hours.sum(),
                   "links_with_duration": int(g.duration_s.notna().sum()),
                   # failed links have no duration_s; their counted attempt's step-log span stands in (lower bound)
                   "hours_incl_failed_span": (g.duration_s.sum() + g[g.duration_s.isna()].step_span_s.sum()) / 3600,
                   "earlier_attempts_span_h": g.earlier_attempts_span_s.sum() / 3600,
                   "step_hours_total": g.step_hours.sum(), "Mtok_total": g.output_tokens.sum() / 1e6,
                   "n_flown": len(f), "n_flown_converged": len(fc),
                   "n_flown_below_baseline": int((f.fuel_kg < BASELINE_FUEL_KG).sum()),
                   "n_converged_below_baseline": int((fc.fuel_kg < BASELINE_FUEL_KG).sum()),
                   "best_fuel_flown_kg": f.fuel_kg.min(), "best_fuel_converged_kg": fc.fuel_kg.min(),
                   "link20_fuel_kg": g[g.link == 20].fuel_kg.iloc[0] if (g.link == 20).any() else np.nan,
                   "link20_converged": g[g.link == 20].ec_converged.iloc[0] if (g.link == 20).any() else np.nan})
    ch = pd.DataFrame(ch)
    ch["best_converged_saved_kg"] = BASELINE_FUEL_KG - ch.best_fuel_converged_kg
    ch["best_converged_saved_per_chain_hour"] = ch.best_converged_saved_kg / ch.hours_total_recorded
    ch["best_converged_saved_per_chain_Mtok"] = ch.best_converged_saved_kg / ch.Mtok_total
    ch.to_csv(args.out / "chain_totals.csv", index=False)
    print("\n=== chain totals ===\n", ch.round(3).to_string())

    # 6. trend over the chain
    tr_rows = []
    for c in order:
        g = df[df.combo == c]
        row = {"combo": c}
        for col in ("hours", "ktok", "agent_steps", "tok_per_min", "step_hours", "max_prompt_tokens"):
            row[f"rho_{col}"] = spearman(g.link, g[col])
            m_ = g[["link", col]].dropna()
            row[f"p_{col}"] = float(spearmanr(m_.link, m_[col]).pvalue) if len(m_) >= 3 else None
            e, l_ = g[g.link <= 5][col].dropna(), g[g.link >= 16][col].dropna()
            row[f"{col}_links1_5_mean"], row[f"{col}_links1_5_n"] = e.mean(), len(e)
            row[f"{col}_links16_20_mean"], row[f"{col}_links16_20_n"] = l_.mean(), len(l_)
        tr_rows.append(row)
    trd = pd.DataFrame(tr_rows)
    trd.to_csv(args.out / "trend_over_chain.csv", index=False)
    print("\n=== trend over chain ===\n", trd.round(3).T.to_string())

    # 7. structure / handler summaries
    for key in ("structure", "handler"):
        t = df.groupby(key).agg(links=("link", "size"), n_dur=("duration_s", "count"), mean_h=("hours", "mean"),
                                median_h=("hours", "median"), total_h=("hours", "sum"), mean_ktok=("ktok", "mean"),
                                median_ktok=("ktok", "median"), mean_tok_per_min=("tok_per_min", "mean"),
                                mean_steps=("agent_steps", "mean"), mean_step_h=("step_hours", "mean"))
        t.to_csv(args.out / f"by_{key}.csv")
        print(f"\n=== by {key} ===\n", t.round(3).to_string())

    # 8. parallelism
    by_combo("step_over_duration", "step_time_over_duration")
    by_combo("step_over_span", "step_time_over_steplog_span")
    by_combo("max_concurrent_generations", "max_concurrent_generations")
    by_combo("generation_wait_seconds", "generation_wait_hours", 1 / 3600)
    print("\nlinks with step_time/duration > 1.05:\n",
          df[df.step_over_duration > 1.05][["combo", "link", "step_over_duration"]].round(3).to_string())
    print("\nlinks with step_time/duration < 0.8:\n",
          df[df.step_over_duration < 0.8][["combo", "link", "step_over_duration", "hours"]].round(3).to_string())
    print("\nresult.json generations vs step-log generations (mismatch count per combo):")
    m = df[df.result_generations.notna()]
    print(m.assign(mis=m.result_generations != m.n_generations).groupby("combo").mis.sum().to_string())
    # correlations
    print("\nspearman duration vs output tokens, per combo:")
    for c in order:
        g = df[df.combo == c]
        print(c, round(spearman(g.hours, g.ktok), 3), "n", int(g[["hours", "ktok"]].dropna().shape[0]))


if __name__ == "__main__":
    main()
