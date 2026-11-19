"""Validation 2 (final_val2): every recommendation, its fate, and figures.

Input: the per-run dumps written by val2_rec_extract.py (--extract) and the hand-traced judgements in
analysis/link20/reports/data/val2_rec_curated.csv (--curated). Writes into --out:

    val2_recommendations_calls.csv     one row per record_recommendation call (77) + the recommendation-bearing
                                       blackboard / todo posts of the networked peers
    val2_recommendations.csv           one row per DISTINCT recommendation (same run, author, channel and text):
                                       the table of the report, automatic fields + curated fields
    val2_recommendations.md            the same table, readable
    fig_*.png / fig_*.pdf / fig_*.csv  figures (a)-(e) and the data each one plots

Automatic fields
- accepted: the framework recorded it (record_recommendation answered "recorded": true). Every other call was refused
  by the role guard ("Only the mdo_integrator records the recommendation").
- carried: the last accepted recommendation of iteration 1 with a change; the framework puts it into every
  iteration-2 prompt ("RECOMMENDATION from the previous iteration").
- reach: the recommendation text (whitespace-normalised, case-folded) occurs in a prompt that an agent received
  after the recommendation was made (same iteration; iteration 2 as well for a carried one). The target is the
  agent named like the target role (sequential, orchestrated); in networked any peer can hold any role, so any peer.
- relay (orchestrated): the first assign_task to the target agent after the recommendation (iteration 2 for a
  carried one), and the graph_router message that preceded it.
- actions: morphs (set_high_level_parameters) by anyone between the recommendation and the next accepted
  recommendation of the run (or the end of the run).
- fuel: prev = the last converged mission before the recommendation (iteration 1's last for a carried one);
  next = the first converged mission after the first following morph, or after the recommendation when there is
  no morph, still before the next accepted recommendation. delta = next - prev.

Usage (aipexws8, nice 15, from the analysis checkout):
    PYTHONPATH=. python analysis/link20/reports/scripts/val2_recommendations.py \
        --extract /data/Jessica/final_val2/figures/recommendations/extract \
        --curated analysis/link20/reports/data/val2_rec_curated.csv \
        --out /data/Jessica/final_val2/figures/recommendations
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.link20 import rec_params as rp
from analysis.link20.plots import (REF_STYLE, STRUCT_COLORS, STRUCT_LABELS, STRUCTURES, Line2D, Patch, plt,
                                   setup_style)

COMBO_ORDER = ["S_SP", "S_IF", "S_GR", "O_SP", "O_IF", "O_GR", "N_IF", "N_GR"]
STRUCT = {"S": "sequential", "O": "orchestrated", "N": "networked"}


def jl(p: Path) -> list[dict]:
    return [json.loads(line) for line in open(p) if line.strip()] if p.is_file() else []


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()


def load(extract: Path):
    runs = {(r["combo"], r["it"]): r for r in json.load(open(extract / "runs.json"))}
    ev = {k: jl(extract / f"{k[0]}_it{k[1]}_events.jsonl") for k in runs}
    pr = {k: jl(extract / f"{k[0]}_it{k[1]}_prompts.jsonl") for k in runs}
    return runs, ev, pr


def calls_table(runs, ev) -> pd.DataFrame:
    rows = []
    for (combo, it), evs in ev.items():
        t0 = runs[(combo, it)]["t0"]
        for e in evs:
            if e["src"] != "fw":
                continue
            a = e.get("args") or {}
            if e["tool"] == "record_recommendation":
                rows.append({"combo": combo, "it": it, "time": e["time"], "min": (e["time"] - t0) / 60,
                             "author": e["agent"], "channel": "record_recommendation",
                             "verdict": a.get("verdict"), "target": a.get("recommendation_to"),
                             "text": a.get("recommended_change") or "",
                             "accepted": '"recorded": true' in (e.get("obs") or ""),
                             "framework_reply": (e.get("obs") or "")[:200]})
            elif e["tool"] == "write_blackboard" and "recommendation" in str(a.get("key", "")).lower():
                rows.append({"combo": combo, "it": it, "time": e["time"], "min": (e["time"] - t0) / 60,
                             "author": e["agent"], "channel": "blackboard", "verdict": None,
                             "target": str(a.get("key")).replace("_recommendation", ""),
                             "text": a.get("value") or "", "accepted": None, "framework_reply": ""})
            elif e["tool"] == "mark_todo_done" and "Recommendation:" in str(a.get("result", "")):
                rows.append({"combo": combo, "it": it, "time": e["time"], "min": (e["time"] - t0) / 60,
                             "author": e["agent"], "channel": "todo result", "verdict": None, "target": None,
                             "text": str(a["result"]).split("Recommendation:", 1)[1].strip(), "accepted": None,
                             "framework_reply": ""})
    c = pd.DataFrame(rows).sort_values(["combo", "it", "time"])
    c["combo"] = pd.Categorical(c["combo"], COMBO_ORDER, ordered=True)
    return c.sort_values(["combo", "it", "time"]).reset_index(drop=True)


def distinct(calls: pd.DataFrame) -> pd.DataFrame:
    g = calls.groupby(["combo", "it", "author", "channel", "text"], observed=True, sort=False)
    d = g.agg(time=("time", "min"), min=("min", "min"), n_calls=("time", "size"), target=("target", "first"),
              verdict=("verdict", "first"),
              accepted=("accepted", lambda s: bool(s.fillna(False).any()) if s.notna().any() else None),
              n_refused=("accepted", lambda s: int((s == False).sum()))).reset_index()  # noqa: E712
    d = d.sort_values(["combo", "it", "time"]).reset_index(drop=True)
    d.insert(0, "rec_id", [f"{c}-{i}-{k + 1:02d}" for (c, i), grp in d.groupby(["combo", "it"], observed=True)
                           for k in range(len(grp))])
    return d


def missions(evs):
    return [e for e in evs if e["src"] == "mcp" and e["tool"] == "run_simulation" and e.get("converged")
            and e.get("fuel") is not None]


def auto_fields(d: pd.DataFrame, runs, ev, pr) -> pd.DataFrame:
    out = []
    for _, r in d.iterrows():
        combo, it, t = r.combo, r.it, r.time
        evs = ev[(combo, it)]
        # carried = last accepted CONTINUE recommendation of iteration 1
        acc1 = d[(d.combo == combo) & (d.it == 1) & (d.accepted == True) & (d.text != "")]  # noqa: E712
        carried = bool(it == 1 and len(acc1) and acc1.iloc[-1].rec_id == r.rec_id)
        # end of the window: next accepted recommendation of the run (a carried one: the end of iteration 2's
        # first accepted recommendation)
        later = d[(d.combo == combo) & (d.it == it) & (d.accepted == True) & (d.time > t)]  # noqa: E712
        t_end = later.time.min() if len(later) else np.inf
        scope = [(it, t, t_end)]
        if carried:
            acc2 = d[(d.combo == combo) & (d.it == 2) & (d.accepted == True)]  # noqa: E712
            scope.append((2, -np.inf, acc2.time.min() if len(acc2) else np.inf))
        key = norm(r.text)
        # a refused recommendation whose text the integrator later recorded word for word reaches the agents only
        # through that accepted one: search it only up to then
        same_acc = d[(d.combo == combo) & (d.accepted == True) & (d.text.map(norm) == key)  # noqa: E712
                     & (d.rec_id != r.rec_id)]
        same_acc = same_acc[(same_acc.it > it) | ((same_acc.it == it) & (same_acc.time > t))]
        cut = {}
        if r.accepted is not True and len(same_acc):
            f = same_acc.iloc[0]
            cut = {f.it: f.time} | ({1: np.inf} if f.it == 2 else {})
        hits = []
        own = re.compile(r"recommended_change:\s*" + re.escape(key))
        for (i, a, b) in scope:
            for p in pr[(combo, i)]:
                if not (p["time"] > a and key) or p["time"] >= cut.get(i, np.inf):
                    continue
                inb = norm(p["inbound"])
                # the author's own prompts count only when the framework injected the recommendation
                if (own.search(inb) if p["agent"] == r.author else key in inb):
                    hits.append((i, p["time"], p["agent"]))
        tgt = r.target if isinstance(r.target, str) else ""
        if combo.startswith("N_"):
            tgt_hits = [h for h in hits if h[2].startswith("agent_")]
        else:
            tgt_hits = [h for h in hits if h[2] == tgt]
        reached_agents = sorted({h[2] for h in hits})
        # orchestrator relay
        relay, router_msg = "", ""
        if combo.startswith("O_") and tgt:
            for (i, a, b) in scope:
                rel = [e for e in ev[(combo, i)] if e["src"] == "fw" and e["tool"] == "assign_task"
                       and e["agent"] == "orchestrator" and (e["args"] or {}).get("agent_name") == tgt
                       and e["time"] > a]
                if rel:
                    relay = rel[0]["args"]["task"]
                    rm = [e for e in ev[(combo, i)] if e["src"] == "turn" and e["agent"] == "orchestrator"
                          and a < e["time"] <= rel[0]["time"] and "graph_router" in e["text"]]
                    router_msg = rm[-1]["text"][:400] if rm else ""
                    break
        # actions and fuel
        acts, prev_m, next_m = [], None, None
        for (i, a, b) in scope:
            seg = [e for e in ev[(combo, i)] if a < e["time"] < b]
            morphs = [e for e in seg if e["src"] == "mcp" and e["tool"] == "set_high_level_parameters"]
            acts += [f"it{i} {e['agent']}: {e['text'][6:160]}" for e in morphs]
            t_from = morphs[0]["time"] if morphs else a
            ms = [m for m in missions(seg) if m["time"] > t_from]
            if ms and next_m is None:
                next_m = ms[0]
        before = [m for m in missions(evs) if m["time"] < t]
        prev_m = before[-1] if before else None
        out.append({"carried": carried, "reached_any": bool(hits), "reached_target": bool(tgt_hits),
                    "reached_agents": ";".join(reached_agents),
                    "first_reach_min": round((min(h[1] for h in tgt_hits) - runs[(combo, tgt_hits[0][0])]["t0"]) / 60, 1)
                    if tgt_hits else None,
                    "reach_iteration": tgt_hits[0][0] if tgt_hits else None,
                    "orchestrator_task": relay, "router_message": router_msg,
                    "morphs_after": " | ".join(acts),
                    "fuel_prev_kg": prev_m["fuel"] if prev_m else None,
                    "fuel_next_kg": next_m["fuel"] if next_m else None,
                    "fp_prev": prev_m.get("fp") if prev_m else None, "fp_next": next_m.get("fp") if next_m else None})
    return pd.concat([d, pd.DataFrame(out)], axis=1)


def parse_type(text: str) -> tuple[str, str]:
    """(auto_type, parsed items) from rec_params: specific = a parameter item with a value / range / bound / %;
    directional = a parameter item with a direction only; general = no parameter item."""
    items = [i for i in rp.parse(text) if i.get("param") and i.get("kind")]
    if not items:
        return "general", ""
    kinds = {i["kind"] for i in items}
    desc = "; ".join(f"{i['param'].split('.')[-1]} {i['kind']}" + (f" {i['lo']}" if i.get("lo") is not None else "")
                     for i in items)
    return ("specific" if kinds & {"value", "range", "bound", "pct"} else "directional"), desc


# ----------------------------------------------------------------------------------------------------------------
# Final table: automatic fields + the hand-traced judgements
# ----------------------------------------------------------------------------------------------------------------
REACHED = {"verbatim", "paraphrased", "via re-issue"}
FOLLOWED = {"followed", "followed (not flown)"}
TYPE_ORDER = ["specific", "directional", "general"]
TYPE_COLORS = {"specific": "#332288", "directional": "#AA4499", "general": "#BBBBBB"}
RELAY_ORDER = ["unchanged", "reworded", "reworded and expanded", "bypassed", "not relayed (run ended)"]
RELAY_COLORS = {"unchanged": "#000000", "reworded": "#777777", "reworded and expanded": "#CC6677",
                "bypassed": "#DDCC77", "not relayed (run ended)": "#FFFFFF"}
OUTCOME_ORDER = ["followed", "followed (not flown)", "not followed (no lever)", "not followed (re-ran analysis)",
                 "not followed (false claim)", "not acted (coordination stall)", "not acted (run ended)",
                 "not acted (not reached)", "duplicate", "no change asked"]
OUTCOME_STYLE = {   # (face colour, edge colour) for the timeline
    "followed": ("#000000", "#000000"), "followed (not flown)": ("#FFFFFF", "#000000"),
    "not followed (no lever)": ("#CC6677", "#CC6677"), "not followed (re-ran analysis)": ("#CC6677", "#CC6677"),
    "not followed (false claim)": ("#CC6677", "#CC6677"),
    "not acted (coordination stall)": ("#DDCC77", "#DDCC77"), "not acted (run ended)": ("#DDCC77", "#DDCC77"),
    "not acted (not reached)": ("#FFFFFF", "#999999"), "duplicate": ("#FFFFFF", "#999999"),
    "no change asked": ("#DDDDDD", "#DDDDDD")}
OUTCOME_GROUP = {"followed": "Followed", "followed (not flown)": "Followed, not flown",
                 "not followed (no lever)": "Not followed", "not followed (re-ran analysis)": "Not followed",
                 "not followed (false claim)": "Not followed (false claim)",
                 "not acted (coordination stall)": "Not acted (run ended or stalled)",
                 "not acted (run ended)": "Not acted (run ended or stalled)",
                 "not acted (not reached)": "Not reached or duplicate", "duplicate": "Not reached or duplicate",
                 "no change asked": "No change asked"}
MARKERS = {"specific": "o", "directional": "D", "general": "s"}


def final_table(d: pd.DataFrame, curated: Path) -> pd.DataFrame:
    cur = pd.read_csv(curated, dtype=str, keep_default_na=False)
    missing = set(d.rec_id) ^ set(cur.rec_id)
    assert not missing, f"curated table does not match the extracted recommendations: {sorted(missing)}"
    t = d.merge(cur, on="rec_id", how="left")
    t = t[t.type != "verdict"].reset_index(drop=True)          # COMPLETE verdicts carry no change
    t["target"] = t.target.fillna("")
    bad = set(t.followed) - set(OUTCOME_ORDER)
    assert not bad, bad
    t["reached"] = t.reach.isin(REACHED)
    t["is_followed"] = t.followed.isin(FOLLOWED)
    flown = t.fuel_prev_kg.notna() & t.fuel_next_kg.notna() & t.reached & (
        (t.followed == "followed") | t.followed.str.startswith("not followed"))
    t["fuel_delta_kg"] = np.where(flown, t.fuel_next_kg - t.fuel_prev_kg, np.nan)
    for c in ("fuel_prev_kg", "fuel_next_kg", "fp_prev", "fp_next"):
        t[c] = t[c].where(flown)
    t["fuel_effect"] = np.select([t.fuel_delta_kg < -0.5, t.fuel_delta_kg > 0.5, t.fuel_delta_kg.notna()],
                                 ["improved", "worsened", "unchanged"], "")
    t["recorded"] = np.select([t.accepted == True, t.accepted == False],  # noqa: E712
                              ["recorded", "refused (role guard)"], "board / todo post")
    return t


TABLE_COLS = ["rec_id", "combo", "it", "min", "author", "target", "channel", "n_calls", "recorded", "carried", "text",
              "type", "fine_type", "auto_type", "reach", "reach_evidence", "orch_relay", "orchestrator_task",
              "orch_reason", "action", "followed", "fuel_prev_kg", "fuel_next_kg", "fuel_delta_kg", "fuel_effect",
              "note"]


def write_tables(t: pd.DataFrame, out: Path) -> None:
    t[TABLE_COLS].to_csv(out / "val2_recommendations.csv", index=False)
    lines = ["# Validation 2: every recommendation (8 combinations x 2 iterations)", "",
             "One row per distinct recommendation (same run, author, channel and text). Columns and rules: INDEX.md.",
             "Fuel = delta of the first converged mission after the change vs the last converged mission before the",
             "recommendation (iteration 1's last for a carried one); only where the recommendation reached its target",
             "and a mission flew.", ""]
    for combo in COMBO_ORDER:
        for it in (1, 2):
            s = t[(t.combo == combo) & (t.it == it)]
            if s.empty:
                continue
            lines += [f"## {combo} iteration {it}", ""]
            for _, r in s.iterrows():
                fuel = (f"{r.fuel_prev_kg:,.2f} -> {r.fuel_next_kg:,.2f} kg (**{r.fuel_delta_kg:+,.2f}**)"
                        if pd.notna(r.fuel_delta_kg) else "no mission flew the result")
                lines += [f"**{r.rec_id}** ({r['min']:.1f} min) {r.author} -> {r.target or '-'}, "
                          f"{r.recorded}{' x' + str(r.n_calls) if r.n_calls > 1 else ''}"
                          f"{', carried to iteration 2' if r.carried else ''}",
                          f"- Text: \"{r.text}\"",
                          f"- Type: {r.type} ({r.fine_type})",
                          f"- Reached target: {r.reach}. {r.reach_evidence}"]
                if r.orch_relay:
                    lines.append(f"- Orchestrator: {r.orch_relay}. Task: \"{r.orchestrator_task}\". "
                                 f"Why: {r.orch_reason or 'not logged'}")
                lines += [f"- What was done: {r.action}", f"- Outcome: {r.followed}. Fuel: {fuel}"]
                if r.note:
                    lines.append(f"- Note: {r.note}")
                lines.append("")
    (out / "val2_recommendations.md").write_text("\n".join(lines))


# ----------------------------------------------------------------------------------------------------------------
# Figures
# ----------------------------------------------------------------------------------------------------------------
def save(fig, out: Path, stem: str, data: pd.DataFrame) -> None:
    fig.savefig(out / f"{stem}.png", dpi=300, bbox_inches="tight")
    fig.savefig(out / f"{stem}.pdf", bbox_inches="tight")
    data.to_csv(out / f"{stem}.csv", index=False)
    plt.close(fig)
    print("wrote", stem)


def ranked(counts: pd.Series, top: int) -> pd.Series:
    counts = counts.sort_values(ascending=False, kind="stable")
    if len(counts) > top + 1:
        counts = pd.concat([counts.iloc[:top], pd.Series({"Other": counts.iloc[top:].sum()})])
    return counts


def fig_a(t: pd.DataFrame, out: Path, top: int = 7) -> None:
    coarse = dict(zip(t.fine_type, t.type))
    # (a1) overall, ranked fine types, coloured by coarse type
    c = ranked(t.fine_type.value_counts(), top)
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    y = np.arange(len(c))[::-1]
    ax.barh(y, c.values, color=[TYPE_COLORS.get(coarse.get(k), "#DDDDDD") for k in c.index], edgecolor="none")
    for yi, v in zip(y, c.values):
        ax.text(v + 0.2, yi, str(v), va="center", fontsize=10)
    ax.set_yticks(y, c.index)
    ax.set_xlabel(f"Recommendations (n = {len(t)})")
    ax.legend(handles=[Patch(color=TYPE_COLORS[k], label=k.capitalize()) for k in TYPE_ORDER], loc="lower right")
    save(fig, out, "fig_a1_types_overall_ranked_bars",
         pd.DataFrame({"fine_type": c.index, "count": c.values, "type": [coarse.get(k, "") for k in c.index]}))
    # (a2) per structure, share of that structure's recommendations
    rows = []
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2), sharex=True)
    for ax, s in zip(axes, STRUCTURES):
        ts = t[t.structure == s]
        c = ranked(ts.fine_type.value_counts(), top)
        pct = 100 * c / len(ts)
        y = np.arange(len(c))[::-1]
        ax.barh(y, pct.values, color=STRUCT_COLORS[s], edgecolor="none")
        for yi, v, n in zip(y, pct.values, c.values):
            ax.text(v + 1, yi, str(n), va="center", fontsize=9)
        ax.set_yticks(y, c.index, fontsize=9)
        ax.set_title(f"{STRUCT_LABELS[s]} (n = {len(ts)})", fontsize=11)
        ax.set_xlabel("Share (%)")
        rows += [{"structure": s, "fine_type": k, "count": n, "pct": p} for k, n, p in zip(c.index, c.values, pct)]
    fig.tight_layout()
    save(fig, out, "fig_a2_types_per_structure_ranked_bars", pd.DataFrame(rows))
    # (a3) per combination, counts
    rows = []
    fig, axes = plt.subplots(2, 4, figsize=(16, 7), sharex=True)
    for ax, combo in zip(axes.flat, COMBO_ORDER):
        tc = t[t.combo == combo]
        c = ranked(tc.fine_type.value_counts(), 5)
        y = np.arange(len(c))[::-1]
        ax.barh(y, c.values, color=STRUCT_COLORS[STRUCT[combo[0]]], edgecolor="none")
        ax.set_yticks(y, c.index, fontsize=9)
        ax.set_title(f"{combo} (n = {len(tc)})", fontsize=11)
        for yi, n in zip(y, c.values):
            ax.text(n + 0.15, yi, str(n), va="center", fontsize=9)
        rows += [{"combo": combo, "fine_type": k, "count": n} for k, n in zip(c.index, c.values)]
    for ax in axes[-1]:
        ax.set_xlabel("Recommendations")
    fig.tight_layout()
    save(fig, out, "fig_a3_types_per_combination_ranked_bars", pd.DataFrame(rows))
    # (a4) coarse type per structure: all vs recorded by the framework
    rows = []
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.2), sharey=True)
    for ax, (lab, sub) in zip(axes, [("All recommendations", t), ("Recorded by the framework",
                                                                    t[t.recorded == "recorded"])]):
        for yi, s in enumerate(STRUCTURES[::-1]):
            ss = sub[sub.structure == s]
            left = 0.0
            for k in TYPE_ORDER:
                n = int((ss.type == k).sum())
                p = 100 * n / len(ss) if len(ss) else 0
                ax.barh(yi, p, left=left, color=TYPE_COLORS[k], edgecolor="white", linewidth=0.8)
                if n:
                    ax.text(left + p / 2, yi, str(n), ha="center", va="center", fontsize=9,
                            color="white" if k != "general" else "black")
                left += p
                rows.append({"panel": lab, "structure": s, "type": k, "count": n, "pct": p, "n": len(ss)})
        ax.set_yticks(range(3), [STRUCT_LABELS[s] for s in STRUCTURES[::-1]])
        ax.set_xlabel("Share (%)")
        ax.set_xlim(0, 100)
        ax.set_title(lab, fontsize=11)
    axes[1].legend(handles=[Patch(color=TYPE_COLORS[k], label=k.capitalize()) for k in TYPE_ORDER],
                   loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.tight_layout()
    save(fig, out, "fig_a4_specificity_per_structure_stacked", pd.DataFrame(rows))


def fate_columns(r) -> list:
    relay = "Not orchestrated"
    if r.structure == "orchestrated":
        relay = {"unchanged": "Passed unchanged", "reworded": "Reworded", "reworded and expanded":
                 "Reworded and expanded", "bypassed": "Bypassed (loop re-ran)"}.get(r.orch_relay, "No task given")
    rec = {"recorded": "Recorded", "refused (role guard)": "Refused (role guard)"}.get(r.recorded, "Board / todo post")
    if r.reach == "run ended first":
        return ["Made", rec, "Run ended first", None, None, None]
    if not r.reached:
        return ["Made", rec, "Not reached", None, None, None]
    out = OUTCOME_GROUP[r.followed]
    fuel = {"improved": "Lower fuel", "worsened": "Higher fuel", "unchanged": "Same fuel"}.get(r.fuel_effect)
    return ["Made", rec, "Reached target", relay, out, fuel]


def fig_b(t: pd.DataFrame, out: Path) -> None:
    # (b1) funnel per structure
    stages = [("Made", lambda s: s),
              ("Recorded by the framework", lambda s: s[s.recorded == "recorded"]),
              ("Reached the target agent", lambda s: s[s.reached]),
              ("Followed", lambda s: s[s.is_followed]),
              ("Followed and flown with lower fuel", lambda s: s[s.fuel_effect == "improved"])]
    rows = []
    fig, ax = plt.subplots(figsize=(8, 4.6))
    h = 0.26
    for k, (lab, f) in enumerate(stages):
        for j, s in enumerate(STRUCTURES):
            n = len(f(t[t.structure == s]))
            y = len(stages) - 1 - k + (1 - j) * h
            ax.barh(y, n, height=h, color=STRUCT_COLORS[s], edgecolor="none")
            ax.text(n + 0.3, y, str(n), va="center", fontsize=9)
            rows.append({"stage": lab, "structure": s, "count": n})
    ax.set_yticks(range(len(stages))[::-1], [s[0] for s in stages])
    ax.set_xlabel("Recommendations")
    ax.legend(handles=[Patch(color=STRUCT_COLORS[s], label=STRUCT_LABELS[s]) for s in STRUCTURES],
              loc="lower right")
    save(fig, out, "fig_b1_fate_funnel_per_structure", pd.DataFrame(rows))
    # (b2) alluvial, all recommendations, ribbons coloured by structure
    paths = [fate_columns(r) for _, r in t.iterrows()]
    colors = [STRUCT_COLORS[s] for s in t.structure]
    cols = ["Made", "Recorded?", "Reached?", "Orchestrator", "Outcome", "Fuel"]
    fig, ax = plt.subplots(figsize=(18, 10))
    nodes = alluvial_simple(ax, paths, colors, cols)
    ax.legend(handles=[Patch(color=STRUCT_COLORS[s], alpha=0.55, label=STRUCT_LABELS[s]) for s in STRUCTURES],
              loc="lower left", bbox_to_anchor=(0, -0.12), ncol=3)
    data = t[["rec_id", "structure"]].copy()
    for c, name in enumerate(cols):
        data[name] = [p[c] for p in paths]
    save(fig, out, "fig_b2_fate_alluvial", data)
    nodes.to_csv(out / "fig_b2_fate_alluvial_nodes.csv", index=False)


NODE_ORDER = ["Made", "Recorded", "Refused (role guard)", "Board / todo post", "Reached target", "Not reached",
              "Run ended first", "Not orchestrated", "Passed unchanged", "Reworded", "Reworded and expanded",
              "Bypassed (loop re-ran)", "No task given", "Followed", "Followed, not flown", "Not followed",
              "Not followed (false claim)", "Not acted (run ended or stalled)", "Not reached or duplicate",
              "No change asked", "Lower fuel", "Same fuel", "Higher fuel"]


def alluvial_simple(ax, paths, colors, cols, gap=2.0, w=0.08):
    """Alluvial with one unit ribbon per recommendation. Nodes are in the fixed NODE_ORDER; inside a node the
    outgoing ribbons are stacked in the order of their next node and the incoming ones in the order of their
    previous node, so ribbons cross as little as possible."""
    ncol = len(cols)
    rank = {k: i for i, k in enumerate(NODE_ORDER)}
    nodes = [sorted({p[c] for p in paths if p[c] is not None}, key=lambda v: rank.get(v, 99)) for c in range(ncol)]
    pos, rows = {}, []
    for c in range(ncol):
        y = 0.0
        for lab in nodes[c]:
            n = sum(1 for p in paths if p[c] == lab)
            pos[(c, lab)] = (y, y + n)
            rows.append({"column": cols[c], "node": lab, "count": n})
            y += n + gap
    seg_out, seg_in = {}, {}
    for c in range(ncol - 1):
        edges = [(i, p[c], p[c + 1]) for i, p in enumerate(paths) if p[c] is not None and p[c + 1] is not None]
        for lab in nodes[c]:
            cur = pos[(c, lab)][0]
            for i, a, b in sorted([e for e in edges if e[1] == lab], key=lambda e: (rank.get(e[2], 99), colors[e[0]])):
                seg_out[(c, i)] = cur
                cur += 1
        for lab in nodes[c + 1]:
            cur = pos[(c + 1, lab)][0]
            for i, a, b in sorted([e for e in edges if e[2] == lab], key=lambda e: (rank.get(e[1], 99), colors[e[0]])):
                seg_in[(c + 1, i)] = cur
                cur += 1
    for (c, i), y0 in seg_out.items():
        y1 = seg_in[(c + 1, i)]
        xs = np.linspace(c + w / 2, c + 1 - w / 2, 60)
        s = (1 - np.cos(np.pi * (xs - xs[0]) / (xs[-1] - xs[0]))) / 2
        ax.fill_between(xs, y0 + (y1 - y0) * s, y0 + 1 + (y1 - y0) * s, color=colors[i], alpha=0.5, lw=0)
    for (c, lab), (b0, b1) in pos.items():
        ax.add_patch(plt.Rectangle((c - w / 2, b0), w, b1 - b0, color="#222222", lw=0))
        ax.text(c, b0 - 0.25, f"{lab} ({int(b1 - b0)})", ha="center", va="bottom", fontsize=10,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.5, "alpha": 0.85})
    ymax = max(b for _, b in pos.values())
    ax.set_xlim(-0.6, ncol - 0.4)
    ax.set_ylim(ymax + 1, -3)
    ax.set_xticks(range(ncol), cols)
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", length=0, labelsize=12, pad=14)
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
    return pd.DataFrame(rows)


def fig_c(t: pd.DataFrame, runs, ev, out: Path) -> None:
    """Timeline: one row per combination, iteration 1 | iteration 2, x = minutes from the iteration's first event."""
    fig, axes = plt.subplots(1, 2, figsize=(15, 6.2), sharey=True)
    rows = []
    for ax, it in zip(axes, (1, 2)):
        for yi, combo in enumerate(COMBO_ORDER):
            y = len(COMBO_ORDER) - 1 - yi
            evs = ev[(combo, it)]
            t0 = runs[(combo, it)]["t0"]
            t_end = max(e["time"] for e in evs)
            ax.plot([0, (t_end - t0) / 60], [y, y], color="#DDDDDD", lw=1, zorder=0)
            for m in missions(evs):
                ax.plot([(m["time"] - t0) / 60] * 2, [y - 0.32, y + 0.32], color=STRUCT_COLORS[STRUCT[combo[0]]],
                        lw=1.2, zorder=1)
                rows.append({"combo": combo, "it": it, "kind": "mission", "min": (m["time"] - t0) / 60,
                             "fuel_kg": m["fuel"]})
            for _, r in t[(t.combo == combo) & (t.it == it)].iterrows():
                fc, ec = OUTCOME_STYLE[r.followed]
                ax.scatter(r["min"], y, marker=MARKERS[r.type], s=70 if r.followed != "no change asked" else 30,
                           facecolor=fc, edgecolor=ec, linewidth=1.2, zorder=3)
                rows.append({"combo": combo, "it": it, "kind": "recommendation", "rec_id": r.rec_id,
                             "min": r["min"], "type": r.type, "outcome": r.followed})
        ax.set_xlabel(f"Minutes into iteration {it}")
        ax.set_xscale("symlog", linthresh=30)
        ax.set_xlim(0, 400)
        ax.set_xticks([0, 10, 20, 30, 60, 120, 240])
        ax.set_xticklabels(["0", "10", "20", "30", "60", "120", "240"])
    axes[0].set_yticks(range(len(COMBO_ORDER)), COMBO_ORDER[::-1])
    groups = {}   # one legend entry per outcome group (the members share a style)
    for o in OUTCOME_ORDER:
        if o in set(t.followed):
            groups.setdefault(OUTCOME_GROUP[o].replace(" (false claim)", ""), o)
    h1 = [Line2D([], [], ls="", marker="o", markersize=8, markerfacecolor=OUTCOME_STYLE[o][0],
                 markeredgecolor=OUTCOME_STYLE[o][1], label=g) for g, o in groups.items()]
    h2 = [Line2D([], [], ls="", marker=MARKERS[k], markersize=8, markerfacecolor="white", markeredgecolor="black",
                 label=k.capitalize()) for k in TYPE_ORDER]
    h3 = [Line2D([], [], color="0.4", lw=1.2, label="Converged mission")]
    fig.legend(handles=h1 + h2 + h3, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.13), fontsize=9)
    fig.tight_layout()
    save(fig, out, "fig_c_timeline_per_combination", pd.DataFrame(rows))


def wrap(s: str, n: int) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(s, n))


def fig_d(t: pd.DataFrame, out: Path) -> None:
    import difflib
    o = t[(t.structure == "orchestrated") & (t.recorded == "recorded")].copy()
    o["relay"] = o.orch_relay.replace("", "not relayed (run ended)")
    o["assigned"] = [r.orchestrator_task if r.relay not in ("bypassed", "not relayed (run ended)") else
                     ("(no new task; the loop re-ran the earlier task)" if r.relay == "bypassed" else "(none)")
                     for _, r in o.iterrows()]
    o["similarity"] = [difflib.SequenceMatcher(None, norm(r.text), norm(r.orchestrator_task)).ratio()
                       if r.relay in ("unchanged", "reworded", "reworded and expanded") else np.nan
                       for _, r in o.iterrows()]
    o["len_ratio"] = [len(r.orchestrator_task) / len(r.text) if r.relay in
                      ("unchanged", "reworded", "reworded and expanded") else np.nan for _, r in o.iterrows()]
    o = o.sort_values(["combo", "it", "min"])
    cols = ["rec_id", "relay", "text", "assigned", "similarity", "len_ratio", "orch_reason", "followed"]
    # (d1) counts by kind of change
    c = o.relay.value_counts().reindex(RELAY_ORDER).fillna(0).astype(int)
    fig, ax = plt.subplots(figsize=(7, 3.2))
    y = np.arange(len(c))[::-1]
    ax.barh(y, c.values, color=[RELAY_COLORS[k] for k in c.index], edgecolor="black", linewidth=0.6)
    for yi, v in zip(y, c.values):
        ax.text(v + 0.05, yi, str(v), va="center")
    ax.set_yticks(y, [k.capitalize() for k in c.index])
    ax.set_xlabel(f"Recorded recommendations in orchestrated runs (n = {len(o)})")
    ax.set_xticks(range(int(c.max()) + 2))
    save(fig, out, "fig_d1_orchestrator_relay_kinds", c.rename("count").reset_index())
    # (d2) asked vs assigned, text panel
    n = len(o)
    fig, ax = plt.subplots(figsize=(15, 1.35 * n + 0.8))
    ax.set_xlim(0, 1)
    ax.set_ylim(n, -0.6)
    ax.axis("off")
    ax.text(0.0, -0.4, "Recommendation", fontsize=10, weight="bold")
    ax.text(0.30, -0.4, "Integrator asked", fontsize=10, weight="bold")
    ax.text(0.62, -0.4, "Orchestrator assigned", fontsize=10, weight="bold")
    for i, (_, r) in enumerate(o.iterrows()):
        ax.scatter(0.005, i + 0.1, s=110, marker="s", color=RELAY_COLORS[r.relay], edgecolor="black", lw=0.6)
        ax.text(0.02, i + 0.1, f"{r.rec_id}\n{r.relay}", va="top", fontsize=9)
        ax.text(0.30, i + 0.1, wrap(r.text, 48), va="top", fontsize=9)
        ax.text(0.62, i + 0.1, wrap(r.assigned, 70), va="top", fontsize=9)
        if i:
            ax.axhline(i - 0.05, color="#EEEEEE", lw=0.8)
    save(fig, out, "fig_d2_orchestrator_asked_vs_assigned", o[cols])
    # (d3) similarity of the assigned task to the recommendation
    r = o[o.similarity.notna()]
    fig, ax = plt.subplots(figsize=(7, 2.6))
    y = np.arange(len(r))[::-1]
    ax.barh(y, r.similarity, color=[RELAY_COLORS[k] for k in r.relay], edgecolor="black", linewidth=0.6)
    for yi, v, lr in zip(y, r.similarity, r.len_ratio):
        ax.text(v + 0.01, yi, f"{v:.2f}", va="center", fontsize=9)
    ax.set_yticks(y, r.rec_id)
    ax.set_xlim(0, 1.1)
    ax.set_xlabel("Text similarity, task vs recommendation")
    ax.legend(handles=[Patch(facecolor=RELAY_COLORS[k], edgecolor="black", label=k.capitalize())
                       for k in RELAY_ORDER[:3]], loc="lower left", bbox_to_anchor=(1.01, 0), fontsize=9)
    save(fig, out, "fig_d3_orchestrator_task_similarity", r[cols])


def short_change(text: str) -> str:
    items = [i for i in rp.parse(text) if i.get("param") and i.get("kind")]
    if not items:
        return text[:40]
    lab = {"Aircraft.Wing.AREA": "area", "Aircraft.Wing.ASPECT_RATIO": "AR"}
    parts = []
    for i in items:
        nm = lab.get(i["param"], i["param"].split(".")[-1])
        parts.append(f"{nm} {'up' if i['direction'] > 0 else 'down'}" if i["kind"] == "direction"
                     else f"{nm} {i['from'] if i.get('from') is not None else ''}->{i['lo']:g}".replace(" ->", " ->"))
    return ", ".join(parts)


def fig_e(t: pd.DataFrame, out: Path) -> None:
    f = t[(t.followed == "followed") & t.fuel_delta_kg.notna()].copy()
    f["label"] = [f"{r.rec_id}  " + (short_change(r.text) if r.type == "specific" else
                  r.text.split(" to ")[0].replace("aspect ratio", "AR").replace("Increase", "increase")
                  .replace("Reduce", "reduce")) for _, r in f.iterrows()]
    f = f.sort_values("fuel_delta_kg")
    types = [k for k in TYPE_ORDER if k in set(f.type)]
    hs = [max(1, (f.type == k).sum()) for k in types]
    fig, axes = plt.subplots(len(types), 1, figsize=(8.5, 0.42 * len(f) + 1.6), sharex=True,
                             gridspec_kw={"height_ratios": hs})
    axes = np.atleast_1d(axes)
    lo = min(0, f.fuel_delta_kg.min()) * 1.1
    hi = max(50, f.fuel_delta_kg.max() * 1.1)
    for ax, k in zip(axes, types):
        s = f[f.type == k]
        y = np.arange(len(s))[::-1]
        ax.barh(y, s.fuel_delta_kg, color=[STRUCT_COLORS[x] for x in s.structure], edgecolor="none")
        for yi, v in zip(y, s.fuel_delta_kg):
            ax.text(v - 8, yi, f"{v:,.0f}", va="center", ha="right", fontsize=9)
        ax.axvline(0, **REF_STYLE)
        ax.set_yticks(y, s.label, fontsize=9)
        ax.set_ylabel(k.capitalize(), fontsize=11)
        ax.set_xlim(lo - 60, hi)
    axes[-1].set_xlabel("Fuel change of the mission that followed (kg)")
    fig.legend(handles=[Patch(color=STRUCT_COLORS[s], label=STRUCT_LABELS[s]) for s in STRUCTURES
                        if s in set(f.structure)], loc="lower center", ncol=3, bbox_to_anchor=(0.6, -0.06), fontsize=10)
    fig.tight_layout()
    save(fig, out, "fig_e_fuel_delta_followed_diverging",
         f[["rec_id", "combo", "it", "structure", "type", "fine_type", "text", "fuel_prev_kg", "fuel_next_kg",
            "fuel_delta_kg", "fp_prev", "fp_next"]])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", required=True)
    ap.add_argument("--curated", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--draft", action="store_true", help="write the automatic table only (before curation)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    runs, ev, pr = load(Path(a.extract))
    calls = calls_table(runs, ev)
    calls.to_csv(out / "val2_recommendations_calls.csv", index=False)
    d = auto_fields(distinct(calls), runs, ev, pr)
    d[["auto_type", "parsed_items"]] = [parse_type(t) for t in d.text]
    d["structure"] = [STRUCT[c[0]] for c in d.combo]
    if a.draft:
        pd.set_option("display.width", 250, "display.max_columns", 50, "display.max_colwidth", 90,
                      "display.max_rows", 500)
        d.drop(columns=["time"]).to_csv(out / "val2_recommendations_auto.csv", index=False)
        for _, r in d.iterrows():
            print(f"\n{r.rec_id} [{r.author}->{r.target}] {r.channel} acc={r.accepted} n={r.n_calls} "
                  f"carried={r.carried} t={r['min']:.1f}m type={r.auto_type} ({r.parsed_items})\n   TEXT: {r.text}\n"
                  f"   reach_any={r.reached_any} target={r.reached_target} agents={r.reached_agents} "
                  f"first={r.first_reach_min} it={r.reach_iteration}\n   relay: {r.orchestrator_task[:300]}\n"
                  f"   router: {r.router_message[:200]}\n   morphs: {r.morphs_after[:400]}\n"
                  f"   fuel {r.fuel_prev_kg} -> {r.fuel_next_kg}  fp {r.fp_prev} -> {r.fp_next}")
        return
    t = final_table(d, Path(a.curated))
    write_tables(t, out)
    setup_style()
    fig_a(t, out)
    fig_b(t, out)
    fig_c(t, runs, ev, out)
    fig_d(t, out)
    fig_e(t, out)
    summary(t, out)


def summary(t: pd.DataFrame, out: Path) -> None:
    """Counts quoted in INDEX.md and the report (printed and saved)."""
    lines = [f"distinct recommendations: {len(t)} (record_recommendation calls with a change: "
             f"{int(t[t.channel == 'record_recommendation'].n_calls.sum())}; board/todo posts: "
             f"{int(t[t.channel != 'record_recommendation'].n_calls.sum())})"]
    for col in ("structure", "combo"):
        g = t.groupby(col, observed=True, sort=False)
        s = pd.DataFrame({"made": g.size(), "recorded": g.apply(lambda x: (x.recorded == "recorded").sum()),
                          "specific": g.apply(lambda x: (x.type == "specific").sum()),
                          "directional": g.apply(lambda x: (x.type == "directional").sum()),
                          "general": g.apply(lambda x: (x.type == "general").sum()),
                          "reached": g.apply(lambda x: x.reached.sum()),
                          "followed": g.apply(lambda x: x.is_followed.sum()),
                          "fuel_lower": g.apply(lambda x: (x.fuel_effect == "improved").sum()),
                          "fuel_delta_sum_kg": g.apply(lambda x: x[x.followed == "followed"].fuel_delta_kg.sum())})
        lines += ["", s.to_string()]
        s.to_csv(out / f"summary_by_{col}.csv")
    for col in ("type", "fine_type", "reach", "followed", "orch_relay", "recorded"):
        lines += ["", t[col].value_counts().to_string()]
    lines += ["", pd.crosstab(t.type, t.followed).to_string(), "",
              pd.crosstab(t.recorded, t.reach).to_string()]
    (out / "summary.txt").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
