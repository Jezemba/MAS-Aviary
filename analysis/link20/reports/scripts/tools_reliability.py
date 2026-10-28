"""MCP tool use and run reliability for the 20-link reports (06_tools_reliability.md).

Read-only on the run folders. Every number is counted from a saved file:
    knowledge_base.jsonl (every MCP tool call: agent, tool, server, status, inputs, outputs, error_code),
    links.csv (run_status, attempts, flew, B187/B214 flags, built by analysis.link20.extract),
    stat_progress.json (failed links and their error), and the runner logs (why a link was retried).

Who made a call:
    agent "framework"  -> the runner's link_start record (not an MCP call; dropped everywhere here),
    agent "unknown"    -> graph-routed pre-initialisation (src/coordination/graph_preinit.py: the framework opens
                          the TiGL / SU2 / pyCycle sessions and meshes before the first agent turn; on link 1 it
                          also runs the baseline mass, SU2 solve, set_inputs and run_cycle). Counted separately.
    anything else      -> an agent's call.

A knowledge base keeps the calls of every runner attempt (field `attempt`); all attempts are counted here, the same
as links.csv's kb_* columns.

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.reports.scripts.tools_reliability --links ../out_20261028/links.csv \
        --out ../reports_20261028/tools_reliability
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

from analysis.link20.extract import (B187_FN_LBF, B187_SCALE, HOME, SOURCES, ZERO_FUEL_FLOOR_KG, error_code,
                                     read_json, read_jsonl)

ABBREV = {"sequential": "S", "orchestrated": "O", "networked": "N",
          "staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}
ORDER = ["S_SP", "S_IF", "S_GR", "O_SP", "O_IF", "O_GR", "N_IF", "N_GR"]
SERVERS = ["tigl", "su2", "mass", "pycycle", "aviary"]
STATUSES = ["success", "failed", "refused", "unknown"]
PREINIT_AGENT = "unknown"

# Tools each MCP server exposes: Outil/docs/MCP_TOOL_INVENTORY.md (live snapshot 2026-10-01, 60 tools).
INVENTORY = {
    "tigl": ["ping", "open_cpacs", "close_cpacs", "export_cpacs", "get_configuration_summary",
             "list_geometric_components", "get_component_metadata", "get_wing_summary", "get_fuselage_summary",
             "sample_component_surface", "intersect_with_plane", "intersect_components", "export_component_mesh",
             "export_configuration_cad", "get_high_level_parameters", "set_high_level_parameters", "morph_wing",
             "morph_fuselage", "generate_volume_mesh"],
    "su2": ["ping", "create_su2_session", "close_su2_session", "get_session_info", "get_config_text",
            "parse_config", "update_config_entries", "configure_from_cpacs", "get_valid_config_options", "set_mesh",
            "generate_mesh_from_step", "analyze_mesh", "run_su2_solver", "generate_deformed_mesh", "get_su2_status",
            "list_result_files", "get_result_file_base64", "read_history_csv", "sample_surface_solution"],
    "pycycle": ["ping", "create_cycle_model", "close_cycle_model", "get_cycle_summary", "list_variables",
                "get_design_inputs", "set_inputs", "get_outputs", "run_cycle", "sweep_inputs", "compute_totals"],
    "aviary": ["get_design_space", "create_session", "set_aircraft_parameters", "configure_mission",
               "run_simulation", "get_results", "get_trajectory", "check_constraints"],
    "mass": ["estimate_mass", "validate_cpacs_inputs", "get_cpacs_mass_breakdown"],
}

# Canonical pipeline stages, in chain order: (name, tools whose successful call counts).
STAGES = [("geometry_morph", {"set_high_level_parameters", "morph_wing", "morph_fuselage"}),
          ("mesh", {"generate_volume_mesh"}), ("su2_solve", {"run_su2_solver"}), ("mass", {"estimate_mass"}),
          ("cycle", {"run_cycle"}), ("mission", {"run_simulation"})]

# Runner logs (to read why a link was retried). v3combos logs also hold an older networked graph-routed run,
# filtered out by combination below.
LOGS = {
    "v2full": [HOME / "Jessica/Avion/MAS-Aviary/logs/v2full_qwen_nothink_20261014_083911.log"],
    "v3combos": sorted((HOME / "Jessica/avion_analysis/data/v3combos_from_ws3/logs").glob("v3combos_*.log")),
    "v4net": sorted((HOME / "Jessica/Avion/MAS-Aviary/logs").glob("v4net_graph_qwen_nothink_*.log")),
}
HEADER_RE = re.compile(r"^\[(\d+)/(\d+)\] repeat=(\d+) combo=(\S+) \(chain link (\d+)/20\)")
ATTEMPT_RE = re.compile(r"^\s+attempt (\d+)/(\d+): (.*)$")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def abbrev(combo: str) -> str:
    structure, handler = combo.removeprefix("mdo_f25_").split("_", 1)
    return f"{ABBREV[structure]}_{ABBREV[handler]}"


def who(e: dict) -> str:
    a = e.get("agent")
    return "framework" if a == "framework" else ("preinit" if a == PREINIT_AGENT else "agent")


def fuel_of(e: dict):
    o = e.get("outputs") or {}
    for holder in (o.get("summary"), o.get("outputs"), o):
        if isinstance(holder, dict) and holder.get("fuel_burned_kg") is not None:
            try:
                return float(holder["fuel_burned_kg"])
            except (TypeError, ValueError):
                return None
    return None


def message(e: dict) -> str:
    """The error text a failed / refused call returned, as saved (the KB shortens long strings to '<N chars>')."""
    o = e.get("outputs")
    msg = ""
    if isinstance(o, dict):
        err = o.get("error")
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
        msg = str(err or "")
        if not msg and o.get("config_errors"):
            msg = "config_errors: " + json.dumps(o["config_errors"])[:200]
        if not msg and isinstance(o.get("messages"), list):
            msg = " | ".join(str(m) for m in o["messages"] if m != "Ran model")
        if not msg and isinstance(o.get("failure"), dict) and o["failure"].get("optimizer_message"):
            msg = f"aviary optimiser: {o['failure']['optimizer_message']}"
    note = str(e.get("note") or "")
    if not msg and note.startswith("Error calling tool"):
        msg = note
    if not msg and e.get("status") == "failed" and error_code(e) == "unspecified" and isinstance(o, dict) and not o:
        msg = "(empty outputs)"
    return msg


def normalise(msg: str) -> str:
    msg = UUID_RE.sub("<id>", msg)
    msg = re.sub(r"'<uuid>'", "'<id>'", msg)
    msg = re.sub(r"\d[\d,]*(\.\d+)?", "<n>", msg)
    return msg[:150]


def retry_reasons() -> dict:
    """{(dataset, combo, link): ["attempt k/3: reason", ...]} from the runner logs."""
    wanted = {ds: set(combos) for ds, _, combos in SOURCES}
    out = defaultdict(list)
    for ds, files in LOGS.items():
        for f in files:
            if not f.is_file():
                continue
            cur = None
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if m := HEADER_RE.match(line):
                    combo = m.group(4)
                    cur = (ds, combo, int(m.group(5))) if combo in wanted[ds] else None
                elif cur and (m := ATTEMPT_RE.match(line)):
                    out[cur].append(f"attempt {m.group(1)}/{m.group(2)}: {m.group(3).strip()} [{f.name}]")
    return out


def b187_trace(kb: list[dict]) -> dict:
    """How an unsized engine (B187) showed up in one link, from the KB (inputs are the resolved call)."""
    preinit_cycle = {(e.get("outputs") or {}).get("session_id") for e in kb
                     if e.get("agent") == PREINIT_AGENT and e.get("tool") == "create_cycle_model"}
    sized_sessions: set = set()
    cycle_hits, scale_sets = [], []
    scale = None                       # SCALE_FACTOR last applied by a successful set_aircraft_parameters
    missions = []                      # (seq, status, scale in effect, fuel)
    for e in kb:
        tool, ok = e.get("tool"), e.get("status") == "success"
        ins = e.get("inputs") or {}
        if tool == "set_inputs" and ok:
            sized_sessions.add(ins.get("session_id"))
        if tool == "run_cycle" and ok:
            fn = ((e.get("outputs") or {}).get("outputs") or {}).get("perf.Fn")
            if isinstance(fn, (int, float)) and abs(fn - B187_FN_LBF) < 1.0:
                sid = ins.get("session_id")
                cycle_hits.append({"seq": e.get("seq"), "agent": e.get("agent"),
                                   "preinit_session": sid in preinit_cycle,
                                   "set_inputs_before_on_session": sid in sized_sessions})
        if tool == "set_aircraft_parameters" and ok:
            sf = (ins.get("parameters") or {}).get("Aircraft.Engine.SCALE_FACTOR")
            if isinstance(sf, (int, float)):
                scale = float(sf)
                if abs(sf - B187_SCALE) < 5e-4:
                    scale_sets.append({"seq": e.get("seq"), "agent": e.get("agent")})
        if tool == "run_simulation" and e.get("status") in ("success", "failed"):
            missions.append((e.get("seq"), e.get("status"), scale, fuel_of(e)))
    at_b187 = [m for m in missions if m[2] is not None and abs(m[2] - B187_SCALE) < 5e-4]
    flown = [m for m in missions if m[1] == "success" and m[3] is not None and m[3] > ZERO_FUEL_FLOOR_KG]
    last = flown[-1] if flown else None
    return {
        "cycle_hits": cycle_hits, "scale_0994_sets": scale_sets,
        "missions_run_at_0994": len(at_b187),
        "missions_at_0994_detail": "; ".join(
            f"seq {s} {st} fuel={'n/a' if f is None else f'{f:.4g}'} kg" for s, st, _, f in at_b187[:6]),
        "counted_mission_seq": last[0] if last else None,
        "counted_mission_scale": last[2] if last else None,
        "counted_mission_at_0994": bool(last and last[2] is not None and abs(last[2] - B187_SCALE) < 5e-4),
    }


def link_record(ds: str, combo: str, link: int, kb: list[dict]) -> tuple[dict, list[dict]]:
    """One per-link summary row, plus one row per MCP call (for the per-tool tables)."""
    calls = []
    for e in kb:
        if who(e) == "framework":
            continue
        calls.append({"dataset": ds, "abbrev": abbrev(combo), "link": link, "who": who(e),
                      "server": e.get("server") or "", "tool": e.get("tool"), "status": e.get("status"),
                      "code": error_code(e) if e.get("status") in ("failed", "refused") else "",
                      "message": message(e) if e.get("status") in ("failed", "refused") else "",
                      "attempt": e.get("attempt"), "seq": e.get("seq"),
                      "mesh_inputs": json.dumps({k: v for k, v in (e.get("inputs") or {}).items()
                                                 if k != "session_id"}, sort_keys=True)
                      if e.get("tool") == "generate_volume_mesh" else ""})
    c = pd.DataFrame(calls)
    ag = c[c.who == "agent"] if len(c) else c
    row = {"dataset": ds, "abbrev": abbrev(combo), "combination": combo, "link": link,
           "agent_calls": len(ag), "preinit_calls": int((c.who == "preinit").sum()) if len(c) else 0}
    for s in SERVERS:
        row[f"calls_{s}"] = int((ag.server == s).sum()) if len(ag) else 0
    for st in STATUSES:
        row[f"agent_{st}"] = int((ag.status == st).sum()) if len(ag) else 0
        row[f"all_{st}"] = int((c.status == st).sum()) if len(c) else 0
    max_att = max((e.get("attempt") or 1 for e in kb), default=1)
    row["kb_attempts"] = max_att
    row["calls_in_earlier_attempts"] = int((c.attempt < max_att).sum()) if len(c) else 0
    # Pipeline stages: success at least once, by agents only and by agents or pre-init.
    for name, tools in STAGES:
        ok = c[(c.tool.isin(tools)) & (c.status == "success")] if len(c) else c
        row[f"stage_{name}_agent"] = bool(len(ok) and (ok.who == "agent").any())
        row[f"stage_{name}_any"] = bool(len(ok))
    sized = [e for e in kb if e.get("tool") == "run_cycle" and e.get("status") == "success"
             and isinstance(fn := ((e.get("outputs") or {}).get("outputs") or {}).get("perf.Fn"), (int, float))
             and abs(fn - B187_FN_LBF) >= 1.0]
    row["stage_cycle_sized_any"] = bool(sized)
    # Missions.
    sims = [e for e in kb if e.get("tool") == "run_simulation"]
    row["sim_success"] = sum(1 for e in sims if e.get("status") == "success")
    row["sim_failed"] = sum(1 for e in sims if e.get("status") == "failed")
    row["sim_refused"] = sum(1 for e in sims if e.get("status") == "refused")
    row["sim_failed_codes"] = json.dumps(dict(Counter(error_code(e) for e in sims if e.get("status") == "failed")))
    row["sim_refused_codes"] = json.dumps(dict(Counter(error_code(e) for e in sims if e.get("status") == "refused")))
    fuels = [fuel_of(e) for e in sims if e.get("status") == "success"]
    row["sim_success_same_fuel_as_previous"] = sum(
        1 for a, b in zip(fuels, fuels[1:]) if a is not None and b is not None and abs(a - b) < 1e-6)
    row["refused_codes"] = json.dumps(dict(Counter(x["code"] for x in calls if x["status"] == "refused")))
    row["failed_codes"] = json.dumps(dict(Counter(x["code"] for x in calls if x["status"] == "failed")))
    return row, calls


def chain_break(r: pd.Series) -> str:
    """Where the canonical chain stops on a did-not-fly link (agents or pre-init)."""
    missing = [n for n, _ in STAGES if not r[f"stage_{n}_any"]]
    if r.sim_success + r.sim_failed + r.sim_refused == 0:
        where = "no run_simulation attempted"
    elif r.sim_success + r.sim_failed == 0:
        where = f"run_simulation only refused {r.sim_refused_codes}"
    elif r.sim_success == 0:
        where = f"run_simulation ran, never flew {r.sim_failed_codes}"
    else:
        where = "run_simulation succeeded but fuel <= floor"
    return where + ("; stages never successful: " + ", ".join(missing) if missing else "; every stage ran")


def spearman(x: pd.Series, y: pd.Series):
    if len(x) < 3 or x.nunique() < 2 or y.nunique() < 2:
        return None
    return round(float(x.rank().corr(y.rank())), 3)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 60)
    pd.set_option("display.max_colwidth", 200)

    links = pd.read_csv(args.links)
    links = links.assign(abbrev=[abbrev(c) for c in links.combination])
    retries = retry_reasons()
    rows, calls, traces, fn_des = [], [], [], []
    for ds, root, combos in SOURCES:
        for combo in combos:
            for link_dir in sorted(root.glob(f"repeat_*/{combo}")):
                link = int(link_dir.parent.name.split("_")[1]) + 1
                kb = read_jsonl(link_dir / "knowledge_base.jsonl")
                row, cl = link_record(ds, combo, link, kb)
                row["retry_log"] = " || ".join(retries.get((ds, combo, link), []))
                rows.append(row)
                calls.extend(cl)
                tr = b187_trace(kb)
                tr.update({"abbrev": abbrev(combo), "link": link})
                traces.append(tr)
                for e in kb:
                    if e.get("tool") == "set_inputs" and e.get("status") == "success":
                        v = ((e.get("inputs") or {}).get("values") or {}).get("Fn_DES")
                        fn_des.append({"abbrev": abbrev(combo), "link": link, "who": who(e),
                                       "fn_des": float(v) if isinstance(v, (int, float)) else None})
    per = pd.DataFrame(rows)
    per = per.merge(links[["abbrev", "link", "run_status", "fail_error", "attempt", "step_log_attempts", "flew",
                           "n_missions_flown", "kb_refused", "kb_failed", "b187", "b187_cycle",
                           "b187_inherited_only", "b214", "ec_converged"]], on=["abbrev", "link"], how="left")
    per["abbrev"] = pd.Categorical(per.abbrev, ORDER, ordered=True)
    per = per.sort_values(["abbrev", "link"])
    calls = pd.DataFrame(calls)
    calls["abbrev"] = pd.Categorical(calls.abbrev, ORDER, ordered=True)
    per.to_csv(args.out / "per_link.csv", index=False)

    # Consistency with links.csv (extract counts refused / failed over all KB entries, incl. pre-init).
    print("check all_refused == links.kb_refused:", bool((per.all_refused == per.kb_refused).all()),
          "| all_failed == links.kb_failed:", bool((per.all_failed == per.kb_failed).all()))
    print("pre-init status mix:", calls[calls.who == "preinit"].status.value_counts().to_dict())
    print("links per combination:", per.groupby("abbrev", observed=True).size().to_dict())

    g = per.groupby("abbrev", observed=True)
    n = g.size()
    tot = calls.groupby(["who", "status"]).size()
    agent_total = int(tot.loc["agent"].sum())
    print("overall: agent calls", agent_total, "| by status", tot.loc["agent"].to_dict(), "| shares",
          (tot.loc["agent"] / agent_total).round(3).to_dict(), "| pre-init calls", int(tot.loc["preinit"].sum()),
          "| calls in earlier runner attempts", int(per.calls_in_earlier_attempts.sum()),
          "| links", len(per), "| did-not-fly", int((~per.flew.astype(bool)).sum()),
          "| failed links", int((per.run_status == "failed").sum()))

    # 1. Calls per link by server.
    t1 = pd.DataFrame({"n_links": n, "agent_calls_mean": g.agent_calls.mean().round(1),
                       "agent_calls_median": g.agent_calls.median(), "agent_calls_min": g.agent_calls.min(),
                       "agent_calls_max": g.agent_calls.max(), "preinit_calls_mean": g.preinit_calls.mean().round(1)})
    for s in SERVERS:
        t1[f"{s}_mean"] = g[f"calls_{s}"].mean().round(1)
    t1["earlier_attempt_calls_total"] = g.calls_in_earlier_attempts.sum()
    t1.to_csv(args.out / "calls_by_server.csv")
    print("\n== 1. MCP calls per link (agent calls; pre-init separate)\n", t1.to_string())

    # 2. Calls by tool (agent calls), and status per tool.
    ag = calls[calls.who == "agent"]
    t2 = ag.pivot_table(index=["server", "tool"], columns="status", values="seq", aggfunc="count", fill_value=0)
    t2 = t2.reindex(columns=STATUSES, fill_value=0)
    t2["total"] = t2.sum(axis=1)
    t2["fail_or_refuse_share"] = ((t2.failed + t2.refused) / t2.total).round(3)
    t2 = t2.sort_values("total", ascending=False)
    t2.to_csv(args.out / "status_by_tool_all_combos.csv")
    print("\n== 2a. Agent calls by tool, all combinations\n", t2.to_string())
    t2c = ag.pivot_table(index="tool", columns="abbrev", values="seq", aggfunc="count", fill_value=0, observed=False)
    t2c = t2c.reindex(columns=ORDER, fill_value=0)
    (t2c / n.reindex(ORDER).values).round(2).to_csv(args.out / "calls_per_link_by_tool.csv")
    t2c.to_csv(args.out / "calls_by_tool_by_combo.csv")
    print("\n== 2b. Agent calls per link by tool (n=20 per combination)\n",
          (t2c / n.reindex(ORDER).values).round(2).loc[t2c.sum(axis=1).sort_values(ascending=False).index].to_string())

    # 3. Never used tools.
    used_any = set(calls.tool)
    used_agent = set(ag.tool)
    nu = []
    for s, tools in INVENTORY.items():
        for t in tools:
            per_combo_unused = [a for a in ORDER if t2c.get(a, pd.Series(dtype=int)).get(t, 0) == 0]
            nu.append({"server": s, "tool": t, "used_by_agents": t in used_agent, "used_by_preinit_only":
                       (t in used_any) and (t not in used_agent),
                       "combos_where_agents_never_called_it": ",".join(per_combo_unused)})
    nu = pd.DataFrame(nu)
    nu.to_csv(args.out / "tool_usage_vs_inventory.csv", index=False)
    print("\n== 3. Inventory tools never called by any agent in any combination (inventory = 60 tools)")
    print(nu[~nu.used_by_agents].groupby("server").tool.apply(list).to_string())
    print("count never called by agents:", int((~nu.used_by_agents).sum()), "of", len(nu))
    print("tools called by agents that are not in the inventory:", sorted(used_agent - set(nu.tool)))
    print("used but rare (<= 5 agent calls in all 160 links):",
          t2[t2.total <= 5][["total"]].reset_index().values.tolist())

    # 4. Status mix per server and combination (agent calls).
    t4 = ag.pivot_table(index=["abbrev", "server"], columns="status", values="seq", aggfunc="count", fill_value=0,
                        observed=True).reindex(columns=STATUSES, fill_value=0)
    t4["total"] = t4.sum(axis=1)
    for st in ("success", "failed", "refused"):
        t4[f"{st}_share"] = (t4[st] / t4.total).round(3)
    t4.to_csv(args.out / "status_by_server_combo.csv")
    print("\n== 4a. Status by server and combination (agent calls)\n", t4.to_string())
    t4b = ag.pivot_table(index="server", columns="status", values="seq", aggfunc="count", fill_value=0)
    t4b = t4b.reindex(columns=STATUSES, fill_value=0)
    t4b["total"] = t4b.sum(axis=1)
    for st in ("success", "failed", "refused"):
        t4b[f"{st}_share"] = (t4b[st] / t4b.total).round(3)
    print("\n== 4b. Status by server, all combinations\n", t4b.to_string())
    t4c = pd.DataFrame({st: g[f"agent_{st}"].sum() for st in STATUSES})
    t4c["total"] = t4c.sum(axis=1)
    for st in ("success", "failed", "refused"):
        t4c[f"{st}_share"] = (t4c[st] / t4c.total).round(3)
        t4c[f"{st}_per_link_mean"] = g[f"agent_{st}"].mean().round(2)
    t4c["n_links"] = n
    t4c.to_csv(args.out / "status_by_combo.csv")
    print("\n== 4c. Status by combination (agent calls)\n", t4c.to_string())

    # 5. Refusals and failures by code.
    for st in ("refused", "failed"):
        sub = calls[calls.status == st]
        t5 = sub.pivot_table(index="code", columns="abbrev", values="seq", aggfunc="count", fill_value=0,
                             observed=False).reindex(columns=ORDER, fill_value=0)
        t5["total"] = t5.sum(axis=1)
        t5 = t5.sort_values("total", ascending=False)
        t5.to_csv(args.out / f"{st}_by_code.csv")
        print(f"\n== 5. {st} calls by error code (counts; every combination n=20 links)\n", t5.to_string())
        print(f"   per-link mean ({st}):\n", (t5[ORDER] / 20).round(2).to_string())
        tc = sub.groupby(["code", "tool"]).size().sort_values(ascending=False)
        print(f"   {st}: code x tool (top 40)\n", tc.head(40).to_string())
        tc.to_csv(args.out / f"{st}_by_code_and_tool.csv")
    # Refusals / failures per agent vs pre-init.
    print("\n   refused / failed by who:", calls[calls.status.isin(["refused", "failed"])]
          .groupby(["who", "status"]).size().to_dict())

    # 6. Top failing tools and their typical messages (agent + pre-init; verbatim, ids and numbers masked).
    bad = calls[calls.status.isin(["failed", "refused"])].copy()
    bad["norm"] = bad.message.map(normalise)
    top = bad.groupby(["tool", "status"]).size().sort_values(ascending=False).head(15)
    msg_rows = []
    for (tool, st), cnt in top.items():
        sub = bad[(bad.tool == tool) & (bad.status == st)]
        for (code, norm), k in sub.groupby(["code", "norm"]).size().sort_values(ascending=False).head(3).items():
            example = sub[(sub.code == code) & (sub.norm == norm)].message.iloc[0]
            msg_rows.append({"tool": tool, "status": st, "tool_status_total": cnt, "code": code, "count": k,
                             "message_example": UUID_RE.sub("<id>", example)[:220]})
    msgs = pd.DataFrame(msg_rows)
    msgs.to_csv(args.out / "top_failing_tools_messages.csv", index=False)
    print("\n== 6. Top failing / refused tools with their most common messages\n", msgs.to_string())
    print("   truncated message (KB '<N chars>'):",
          int(bad.message.str.fullmatch(r"<\d+ chars>").sum()), "of", len(bad))
    cap = bad[(bad.tool == "generate_volume_mesh") & bad.message.str.contains("exceeds the 3,000,000 cap")]
    print("   generate_volume_mesh refused by the server's 3,000,000-cell cap:", len(cap), "calls;",
          "per combination:", cap.groupby("abbrev", observed=False).size().to_dict(), "| links:",
          cap.groupby("abbrev", observed=True).link.nunique().to_dict())
    print("   estimated cell counts quoted:", cap.message.str.extract(r"~([\d,]+) cells")[0].value_counts().to_dict())
    print("   inputs of the capped calls (top 5):", cap.mesh_inputs.value_counts().head(5).to_dict())
    mesh_ok = calls[(calls.tool == "generate_volume_mesh") & (calls.status == "success") & (calls.who == "agent")]
    print("   inputs of successful agent mesh calls (top 5):", mesh_ok.mesh_inputs.value_counts().head(5).to_dict())
    stale = bad[bad.message.str.contains(r"Unknown session_id|Session '<id>' not found|not found\", 'details'",
                                         regex=True) | bad.message.str.contains("Unknown session", regex=False)]
    print("   calls failing on an unknown / expired session id:", len(stale), "by tool:",
          stale.groupby("tool").size().to_dict(), "by combination:",
          stale.groupby("abbrev", observed=True).size().to_dict())
    rc = bad[(bad.tool == "run_cycle") & (bad.status == "failed")]
    print("   run_cycle failed with 'is not an output of this cycle model':",
          int(rc.message.str.contains("is not an output of this cycle model").sum()), "of", len(rc))

    # 7. Pipeline stages.
    st_rows = {}
    for name, _ in STAGES:
        st_rows[f"{name}_agent"] = g[f"stage_{name}_agent"].mean().round(3)
        st_rows[f"{name}_any"] = g[f"stage_{name}_any"].mean().round(3)
    st_rows["cycle_sized_any"] = g.stage_cycle_sized_any.mean().round(3)
    st_rows["flew"] = g.flew.mean().round(3)
    t7 = pd.DataFrame(st_rows)
    t7["n_links"] = n
    t7.to_csv(args.out / "pipeline_stages.csv")
    print("\n== 7. Share of links where each stage succeeded at least once (agent / agent-or-pre-init)\n",
          t7.to_string())
    dnf = per[~per.flew.astype(bool)].copy()
    dnf["chain_break"] = dnf.apply(chain_break, axis=1)
    dnf[["abbrev", "link", "run_status", "sim_success", "sim_failed", "sim_refused", "sim_failed_codes",
         "sim_refused_codes", "chain_break"]].to_csv(args.out / "did_not_fly_chain_break.csv", index=False)
    print("\n== 7b. Did-not-fly links: where the chain breaks\n",
          dnf[["abbrev", "link", "run_status", "sim_success", "sim_failed", "sim_refused", "chain_break"]]
          .to_string(index=False))
    print("   did-not-fly links missing each stage (agents or pre-init):",
          {name: int((~dnf[f"stage_{name}_any"]).sum()) for name, _ in STAGES}, "of", len(dnf))
    print("   did-not-fly with a ZERO_FUEL run_simulation:",
          int(dnf.sim_failed_codes.str.contains("ZERO_FUEL").sum()), "of", len(dnf))

    # 8. run_simulation.
    t8 = pd.DataFrame({"n_links": n, "sim_calls": g.sim_success.sum() + g.sim_failed.sum() + g.sim_refused.sum(),
                       "success": g.sim_success.sum(), "failed": g.sim_failed.sum(), "refused": g.sim_refused.sum()})
    t8["success_rate_of_all_calls"] = (t8.success / t8.sim_calls).round(3)
    t8["success_rate_of_executed"] = (t8.success / (t8.success + t8.failed)).round(3)
    t8["missions_flown_mean"] = g.n_missions_flown.mean().round(2)
    t8["missions_flown_median"] = g.n_missions_flown.median()
    t8["missions_flown_max"] = g.n_missions_flown.max()
    t8["links_flew"] = g.flew.sum()
    t8["same_fuel_reflights_total"] = g.sim_success_same_fuel_as_previous.sum()
    t8["links_with_same_fuel_reflight"] = g.sim_success_same_fuel_as_previous.apply(lambda s: int((s > 0).sum()))
    t8.to_csv(args.out / "run_simulation.csv")
    print("\n== 8. run_simulation\n", t8.to_string())
    sf = calls[(calls.tool == "run_simulation") & (calls.status != "success")]
    print("   run_simulation non-success by code:\n",
          sf.groupby(["status", "code"]).size().sort_values(ascending=False).to_string())

    # 9. Run reliability.
    t9 = pd.DataFrame({"n_links": n, "completed": g.run_status.apply(lambda s: int((s == "completed").sum())),
                       "failed": g.run_status.apply(lambda s: int((s == "failed").sum())),
                       "did_not_fly": g.flew.apply(lambda s: int((~s.astype(bool)).sum())),
                       "links_kb_attempts_gt1": g.kb_attempts.apply(lambda s: int((s > 1).sum())),
                       "links_step_log_attempts_gt1": g.step_log_attempts.apply(lambda s: int((s > 1).sum())),
                       "links_with_retry_log": g.retry_log.apply(lambda s: int((s != "").sum()))})
    t9.to_csv(args.out / "run_reliability.csv")
    print("\n== 9. Run reliability\n", t9.to_string())
    rel = per[(per.run_status != "completed") | (per.kb_attempts > 1) | (per.step_log_attempts > 1)
              | (per.retry_log != "")][["abbrev", "link", "run_status", "fail_error", "attempt", "step_log_attempts",
                                        "kb_attempts", "calls_in_earlier_attempts", "flew", "retry_log"]]
    rel.to_csv(args.out / "run_reliability_links.csv", index=False)
    print(rel.to_string(index=False))
    for ds, root, combos in SOURCES:
        prog = read_json(root / "stat_progress.json") or {}
        for k, v in (prog.get("failed") or {}).items():
            if any(k.endswith(c) for c in combos):
                print("   stat_progress failed:", ds, k, {kk: v.get(kk) for kk in ("error", "attempts")})

    # 10. Contamination per link.
    tr = pd.DataFrame(traces)
    tr["abbrev"] = pd.Categorical(tr.abbrev, ORDER, ordered=True)
    cont = per.merge(tr, on=["abbrev", "link"])
    hit = cont[cont.b187.astype(bool) | cont.b214.astype(bool)].copy()
    hit["cycle_hit_seqs"] = hit.cycle_hits.map(lambda h: ",".join(str(x["seq"]) for x in h))
    hit["cycle_hit_on_preinit_session"] = hit.cycle_hits.map(lambda h: ",".join(str(x["preinit_session"]) for x in h))
    hit["set_inputs_before_hit"] = hit.cycle_hits.map(
        lambda h: ",".join(str(x["set_inputs_before_on_session"]) for x in h))
    hit["scale_0994_set_seqs"] = hit.scale_0994_sets.map(lambda h: ",".join(str(x["seq"]) for x in h))
    cols = ["abbrev", "link", "dataset", "b187_cycle", "b187_inherited_only", "b214", "flew", "run_status",
            "cycle_hit_seqs", "cycle_hit_on_preinit_session", "set_inputs_before_hit", "scale_0994_set_seqs",
            "missions_run_at_0994", "missions_at_0994_detail", "counted_mission_seq", "counted_mission_scale",
            "counted_mission_at_0994"]
    hit[cols].to_csv(args.out / "contamination_links.csv", index=False)
    print("\n== 10. Contamination (B187 / B214) per link\n", hit[cols].to_string(index=False))
    dnf_b = cont[~cont.flew.astype(bool)]
    print("   did-not-fly links with at least one mission run at the B187 scale 0.994:",
          [(str(a), int(l)) for a, l, k in zip(dnf_b.abbrev, dnf_b.link, dnf_b.missions_run_at_0994) if k > 0],
          "of", len(dnf_b))
    flew_b = cont[cont.flew.astype(bool) & cont.b187.astype(bool)]
    print("   flown B187 links whose counted (last flown) mission ran at scale 0.994:",
          [(str(a), int(l)) for a, l, k in zip(flew_b.abbrev, flew_b.link, flew_b.counted_mission_at_0994) if k],
          "| counted mission scale not set in the link's KB:",
          [(str(a), int(l)) for a, l, s in zip(flew_b.abbrev, flew_b.link, flew_b.counted_mission_scale) if pd.isna(s)],
          "| of", len(flew_b), "flown B187 links")
    summ = []
    for a in ORDER:
        p = per[per.abbrev == a]
        lst = lambda m: ", ".join(map(str, p.loc[m, "link"].astype(int))) or "none"
        summ.append({"abbrev": a, "b187_cycle": lst(p.b187_cycle.astype(bool)),
                     "b187_inherited_only": lst(p.b187_inherited_only.astype(bool)), "b214": lst(p.b214.astype(bool)),
                     "failed": lst(p.run_status == "failed"), "did_not_fly": lst(~p.flew.astype(bool))})
    summ = pd.DataFrame(summ)
    summ.to_csv(args.out / "contamination_summary.csv", index=False)
    print(summ.to_string(index=False))

    # 10b. Thrust the pyCycle sessions were sized on (Fn_DES in successful set_inputs, the resolved call).
    fd = pd.DataFrame(fn_des)
    fd["abbrev"] = pd.Categorical(fd.abbrev, ORDER, ordered=True)
    t10 = fd.groupby("abbrev", observed=False).agg(
        set_inputs_success=("link", "size"), with_fn_des=("fn_des", "count"),
        fn_des_min_lbf=("fn_des", "min"), fn_des_median_lbf=("fn_des", "median"), fn_des_max_lbf=("fn_des", "max"))
    t10["fn_des_exactly_25000"] = fd.groupby("abbrev", observed=False).fn_des.apply(
        lambda s: int((s.round(6) == 25000.0).sum()))
    t10["links_with_25000"] = fd[fd.fn_des.round(6) == 25000.0].groupby("abbrev", observed=False).link.nunique()
    t10["fn_des_exactly_5900"] = fd.groupby("abbrev", observed=False).fn_des.apply(
        lambda s: int((s.round(6) == 5900.0).sum()))
    t10.to_csv(args.out / "set_inputs_fn_des.csv")
    print("\n== 10b. Fn_DES passed to successful set_inputs (lbf)\n", t10.to_string())
    print("   by who (N_GR):", fd[fd.abbrev == "N_GR"].groupby("who").size().to_dict())

    # 11. Trend over links.
    per["agent_bad"] = per.agent_failed + per.agent_refused
    per["bad_share"] = per.agent_bad / per.agent_calls
    trend = []
    for a in ORDER:
        p = per[per.abbrev == a]
        early, late = p[p.link <= 10], p[p.link > 10]
        trend.append({"abbrev": a, "n_early": len(early), "n_late": len(late),
                      "refused_mean_1_10": round(early.agent_refused.mean(), 2),
                      "refused_mean_11_20": round(late.agent_refused.mean(), 2),
                      "failed_mean_1_10": round(early.agent_failed.mean(), 2),
                      "failed_mean_11_20": round(late.agent_failed.mean(), 2),
                      "bad_share_mean_1_10": round(early.bad_share.mean(), 3),
                      "bad_share_mean_11_20": round(late.bad_share.mean(), 3),
                      "calls_mean_1_10": round(early.agent_calls.mean(), 1),
                      "calls_mean_11_20": round(late.agent_calls.mean(), 1),
                      "rho_refused_vs_link": spearman(p.link, p.agent_refused),
                      "rho_failed_vs_link": spearman(p.link, p.agent_failed),
                      "rho_bad_share_vs_link": spearman(p.link, p.bad_share),
                      "rho_calls_vs_link": spearman(p.link, p.agent_calls)})
    trend = pd.DataFrame(trend)
    trend.to_csv(args.out / "trend_over_links.csv", index=False)
    print("\n== 11. Trend over links (agent calls; Spearman rho over the 20 links)\n", trend.to_string(index=False))
    allp = per
    print("   all 160 links: rho refused", spearman(allp.link, allp.agent_refused), "rho failed",
          spearman(allp.link, allp.agent_failed), "rho bad_share", spearman(allp.link, allp.bad_share))
    wide = per.pivot_table(index="link", columns="abbrev", values="agent_bad", observed=False)
    wide.to_csv(args.out / "failed_plus_refused_per_link_wide.csv")
    print("   failed+refused agent calls per link:\n", wide.to_string())


if __name__ == "__main__":
    main()
