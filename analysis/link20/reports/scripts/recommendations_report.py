"""Tables for report 05_recommendations.md: integrator verdicts, recommendations, targets and follow-through.

Read-only. Inputs: links.csv and recommended_changes.csv (extract.py / plots.py), plus each link's design.json
(for the full list of recommendation signals, to count distinct texts per link). Every number in the report comes
from the tables this script prints / writes; nothing is interpolated.

Definitions used here (also stated in the report):
- carried-forward recommendation of link k = its LAST recorded recommendation (extract.py `last_rec_*`); link k+1
  receives it in carry_over.json (`inherited_rec_*`). Checked below for every pair (k, k+1).
- distinct recommendations in a link = distinct `recommended_change` texts after lower-casing and collapsing
  whitespace.
- follow-through: one item = one parameter change parsed from link k's carried-forward recommendation
  (rec_params.parse, as in recommended_changes.csv). Wanted direction = the item's direction, or, if it has none,
  the sign of (target - value at end of link k). Actual change = value at end of link k+1 minus value at end of
  link k (links.csv). |relative change| < UNCHANGED_REL counts as unchanged. An item is "followed" when the
  parameter moved in the wanted direction. "Reached" (value / range / bound items only): the link k+1 value lies in
  the target range widened by REACH_REL relative, or satisfies the bound.
- a link "followed" its inherited recommendation when every measurable item was followed; "partly" when some were.

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    PYTHONPATH=. python analysis/link20/reports/scripts/recommendations_report.py \
        --links ../out_20261028/links.csv --items ../out_20261028/recommended_changes.csv \
        --out ../reports_20261028/recommendations
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.link20 import rec_params as rp
from analysis.link20 import recommendations as rec
from analysis.link20.extract import SOURCES, counted_step_log, read_jsonl

COMBOS = [("sequential", "staged_pipeline", "S_SP"), ("sequential", "iterative_feedback", "S_IF"),
          ("sequential", "graph_routed", "S_GR"), ("orchestrated", "staged_pipeline", "O_SP"),
          ("orchestrated", "iterative_feedback", "O_IF"), ("orchestrated", "graph_routed", "O_GR"),
          ("networked", "iterative_feedback", "N_IF"), ("networked", "graph_routed", "N_GR")]
ABBR = {(s, h): a for s, h, a in COMBOS}
UNCHANGED_REL = 1e-3      # 0.1 %
REACH_REL = 0.01          # 1 %

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_colwidth", 200)


def norm(text) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower()) if isinstance(text, str) and text.strip() else ""


def link_dir(combo: str, link: int) -> Path | None:
    for _, root, combos in SOURCES:
        if combo in combos:
            return root / f"repeat_{link - 1:03d}" / combo
    return None


def signal_stats(combo: str, link: int) -> dict:
    """Recommendation texts of one link from design.json (in recorded order)."""
    d = link_dir(combo, link)
    p = d / "design.json" if d else None
    if p is None or not p.is_file():
        return {"design_json": False}
    sig = (json.loads(p.read_text(encoding="utf-8")).get("coordination_signals") or [])
    recs = [s for s in sig if s.get("kind") == "recommendation"]
    texts = [norm(s.get("recommended_change")) for s in recs]
    out = {"design_json": True, "n_distinct_rec": len(set(t for t in texts if t)),
           "n_distinct_rec_to": len({s.get("recommendation_to") for s in recs if s.get("recommendation_to")}),
           "max_same_text": max(Counter(texts).values()) if texts else 0,
           "first_eq_last": (texts[0] == texts[-1]) if texts else None,
           "first_verdict": str(recs[0].get("verdict")).upper() if recs else None,
           "rec_agents": ",".join(sorted({str(s.get("agent")) for s in recs}))}
    return out


def call_outcomes(combo: str, link: int) -> dict:
    """record_recommendation calls in the counted step log, by the tool's reply (observations of the step)."""
    d = link_dir(combo, link)
    _, p = counted_step_log(d) if d else (0, None)
    out = Counter()
    for s in read_jsonl(p) if p else []:
        if s.get("kind") != "step":
            continue
        names = [tc.get("name") for tc in s.get("tool_calls") or [] if isinstance(tc, dict)]
        if "record_recommendation" not in names:
            continue
        o = str(s.get("observations"))
        out["calls"] += names.count("record_recommendation")
        out["recorded"] += len(re.findall(r'"recorded":\s*true', o))
        out["refused_not_integrator"] += o.count("Only the mdo_integrator records")
        out["refused_already_recorded"] += o.count("Already recorded")
    out["other"] = out["calls"] - out["recorded"] - out["refused_not_integrator"] - out["refused_already_recorded"]
    return {f"rr_{k}": v for k, v in out.items()}


def mean_n(s: pd.Series) -> str:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return f"{s.mean():.2f} (n={len(s)})" if len(s) else "n=0"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.links)
    df["abbr"] = [ABBR[(s, h)] for s, h in zip(df.structure, df.handler)]
    df = df.sort_values(["abbr", "link"]).reset_index(drop=True)
    sig = pd.DataFrame([{**signal_stats(c, int(l)), **call_outcomes(c, int(l))}
                        for c, l in zip(df.combination, df.link)]).fillna({"rr_calls": 0})
    df = pd.concat([df, sig], axis=1).copy()
    for c in ("last_verdict", "inherited_rec_verdict"):
        df[c] = df[c].where(df[c].isna(), df[c].astype(str).str.upper())
    df["last_norm"] = df.last_rec_change.map(norm)
    df["inh_norm"] = df.inherited_rec_change.map(norm)
    order = [a for _, _, a in COMBOS]

    # ---------------------------------------------------------------- 1. carry-over check
    rows = []
    for a in order:
        g = df[df.abbr == a].set_index("link")
        for k in range(1, 20):
            if k not in g.index or k + 1 not in g.index:
                continue
            last, inh = g.at[k, "last_norm"], g.at[k + 1, "inh_norm"]
            rows.append({"abbr": a, "link": k, "last_norm_empty": last == "", "inherited_empty": inh == "",
                         "match": last == inh,
                         "role_match": str(g.at[k, "last_rec_to"]) == str(g.at[k + 1, "inherited_rec_to"])})
    cc = pd.DataFrame(rows)
    cc.to_csv(args.out / "carry_over_check.csv", index=False)
    print("== 1. carry_over(k+1).recommendation == last recommendation of k")
    print(cc.groupby("abbr").agg(pairs=("match", "size"), text_match=("match", "sum"),
                                 role_match=("role_match", "sum"), k_without_rec=("last_norm_empty", "sum"),
                                 next_inherits_none=("inherited_empty", "sum")).reindex(order))
    print(cc[~cc.match].to_string())

    # ---------------------------------------------------------------- 2. per-link verdict table
    vt = df.pivot(index="abbr", columns="link", values="last_verdict").reindex(order)
    vt = vt.fillna("-").replace({"COMPLETE": "C", "CONTINUE": "N", "RETRY": "R"})
    vt.to_csv(args.out / "last_verdict_per_link.csv")
    print("\n== 2. last (carried-forward) verdict per link  C=COMPLETE N=CONTINUE R=RETRY -=none")
    print(vt.to_string())

    # ---------------------------------------------------------------- 3. per-combination summary
    summ = []
    for a in order:
        g = df[df.abbr == a]
        lv = g.last_verdict.value_counts()
        fv = g.first_verdict.value_counts()
        l20 = g[g.link == 20]
        summ.append({
            "abbr": a, "dataset": g.dataset.iloc[0], "links": len(g),
            "links_no_rec": int((g.n_recommendations == 0).sum()),
            "last_COMPLETE": int(lv.get("COMPLETE", 0)), "last_CONTINUE": int(lv.get("CONTINUE", 0)),
            "last_RETRY": int(lv.get("RETRY", 0)),
            "first_COMPLETE": int(fv.get("COMPLETE", 0)), "first_CONTINUE": int(fv.get("CONTINUE", 0)),
            "first_RETRY": int(fv.get("RETRY", 0)),
            "all_COMPLETE": int(g.verdict_complete.sum()), "all_CONTINUE": int(g.verdict_continue.sum()),
            "all_RETRY": int(g.verdict_retry.sum()),
            "final_verdict_link20": l20.last_verdict.iloc[0] if len(l20) else None,
            "recs_total": int(g.n_recommendations.sum()), "recs_mean": mean_n(g.n_recommendations),
            "recs_median": float(g.n_recommendations.median()), "recs_max": int(g.n_recommendations.max()),
            "record_rec_calls_total": int(g.n_record_recommendation.sum()),
            "distinct_mean": mean_n(g.n_distinct_rec), "distinct_max": int(g.n_distinct_rec.max()),
            "links_gt1_distinct": int((g.n_distinct_rec > 1).sum()),
            "max_same_text_max": int(g.max_same_text.max()),
            "links_first_eq_last": int((g.first_eq_last == True).sum()),  # noqa: E712
            "links_with_rec": int((g.n_recommendations > 0).sum()),
            "responses_total": int(g.n_responses.sum()), "responses_mean": mean_n(g.n_responses),
            "links_no_response": int((g.n_responses == 0).sum()),
            "respond_calls_total": int(g.n_respond_to_recommendation.sum()),
            "rec_agents": ";".join(sorted({x for v in g.rec_agents.dropna() for x in v.split(",") if x})),
        })
    summ = pd.DataFrame(summ)
    summ.to_csv(args.out / "summary_per_combination.csv", index=False)
    print("\n== 3. per-combination summary")
    print(summ.T.to_string())

    rr_cols = [c for c in ("rr_calls", "rr_recorded", "rr_refused_not_integrator", "rr_refused_already_recorded",
                           "rr_other") if c in df]
    rr = df.groupby("abbr")[rr_cols].sum().reindex(order).fillna(0).astype(int)
    rr["n_recommendations_design_json"] = df.groupby("abbr").n_recommendations.sum().reindex(order)
    rr.to_csv(args.out / "record_recommendation_calls.csv")
    print("\n== 3b. record_recommendation calls (step logs) by the tool's reply")
    print(rr.to_string())
    nr = df[df.n_recommendations == 0][["abbr", "link", "run_status"] + rr_cols]
    print("   links with no recorded recommendation:")
    print(nr.to_string())

    per_link = df[["abbr", "link", "run_status", "flew", "n_recommendations", "n_distinct_rec", "max_same_text",
                   "first_verdict", "last_verdict", "first_eq_last", "n_responses", "n_record_recommendation",
                   "last_rec_to", "last_rec_change"]]
    per_link.to_csv(args.out / "per_link.csv", index=False)

    # ---------------------------------------------------------------- 4. roles and variables (carried-forward)
    role_rows, var_rows = [], []
    for a in order:
        g = df[df.abbr == a]
        rc = Counter(rec.role_label(r) for r in g.last_rec_to)
        vc = Counter(lab for c in g.last_rec_change for lab in rec.variable_labels(c))
        role_rows += [{"abbr": a, "role": k, "links": v} for k, v in rc.items()]
        var_rows += [{"abbr": a, "variable": k, "links": v} for k, v in vc.items()]
    roles = pd.DataFrame(role_rows).pivot(index="role", columns="abbr", values="links").reindex(columns=order)
    roles = roles.fillna(0).astype(int)
    roles["total"] = roles.sum(axis=1)
    roles = roles.sort_values("total", ascending=False)
    vars_ = pd.DataFrame(var_rows).pivot(index="variable", columns="abbr", values="links").reindex(columns=order)
    vars_ = vars_.fillna(0).astype(int)
    vars_["total"] = vars_.sum(axis=1)
    vars_ = vars_.sort_values("total", ascending=False)
    roles.to_csv(args.out / "carried_rec_by_role.csv")
    vars_.to_csv(args.out / "carried_rec_by_variable.csv")
    print("\n== 4a. carried-forward recommendation by target role (links, of 20)")
    print(roles.to_string())
    print("\n== 4b. carried-forward recommendation by design variable (links; one rec can name several)")
    print(vars_.to_string())

    # all recommendations (every signal) by role, from design.json, for comparison
    all_role = Counter()
    for a, c, l in zip(df.abbr, df.combination, df.link):
        d = link_dir(c, int(l))
        if d and (d / "design.json").is_file():
            for s in json.loads((d / "design.json").read_text(encoding="utf-8")).get("coordination_signals") or []:
                if s.get("kind") == "recommendation":
                    all_role[(a, rec.role_label(s.get("recommendation_to")))] += 1
    ar = pd.Series(all_role).unstack(0).reindex(columns=order).fillna(0).astype(int)
    ar.to_csv(args.out / "all_rec_signals_by_role.csv")
    print("\n== 4c. every recorded recommendation by target role (signals, inflated by loop in v2full)")
    print(ar.to_string())

    # ---------------------------------------------------------------- 5. repetition across links
    rep = []
    for a in order:
        g = df[df.abbr == a].set_index("link").sort_index()
        texts = [g.at[k, "last_norm"] for k in g.index]
        nonempty = [t for t in texts if t]
        same_text = same_role = same_vars = same_role_vars = pairs = 0
        run, longest_role_run, prev_role = 0, 0, None
        for k in range(1, 20):
            if k not in g.index or k + 1 not in g.index:
                continue
            t1, t2 = g.at[k, "last_norm"], g.at[k + 1, "last_norm"]
            if not t1 or not t2:
                continue
            pairs += 1
            same_text += t1 == t2
            r1, r2 = g.at[k, "last_rec_to"], g.at[k + 1, "last_rec_to"]
            v1 = set(rec.variable_labels(g.at[k, "last_rec_change"]))
            v2 = set(rec.variable_labels(g.at[k + 1, "last_rec_change"]))
            same_role += r1 == r2
            same_vars += v1 == v2
            same_role_vars += (r1 == r2) and (v1 == v2)
        for k in g.index:
            r = g.at[k, "last_rec_to"] if g.at[k, "last_norm"] else None
            run = run + 1 if (r is not None and r == prev_role) else (1 if r is not None else 0)
            prev_role = r
            longest_role_run = max(longest_role_run, run)
        cnt = Counter(nonempty)
        top_text, top_n = cnt.most_common(1)[0] if cnt else ("", 0)
        rep.append({"abbr": a, "links_with_rec": len(nonempty), "distinct_carried_texts": len(cnt),
                    "consecutive_pairs_both_rec": pairs, "pairs_identical_text": same_text,
                    "pairs_same_role": same_role, "pairs_same_variable_set": same_vars,
                    "pairs_same_role_and_variables": same_role_vars, "longest_same_role_run_links": longest_role_run,
                    "most_repeated_text_count": top_n,
                    "most_repeated_text": g.last_rec_change[g.last_norm == top_text].iloc[0] if top_n > 1 else ""})
    rep = pd.DataFrame(rep)
    rep.to_csv(args.out / "repetition.csv", index=False)
    print("\n== 5. repetition of the carried-forward recommendation across consecutive links")
    print(rep.drop(columns="most_repeated_text").to_string())
    for _, r in rep.iterrows():
        if r.most_repeated_text:
            print(f"  {r.abbr} x{r.most_repeated_text_count}: {r.most_repeated_text}")

    # ---------------------------------------------------------------- 6. follow-through
    items = pd.read_csv(args.items)
    items["abbr"] = [ABBR[(s, h)] for s, h in zip(items.structure, items.handler)]
    # parse audit: how many carried recs give >=1 parameter item
    audit = []
    for a in order:
        g = df[(df.abbr == a) & (df.link < 20)]
        n_rec = int((g.last_norm != "").sum())
        n_items_rec = sum(1 for t in g.last_rec_change if any(it.get("kind") for it in rp.parse(t)))
        audit.append({"abbr": a, "carried_recs_links1to19": n_rec, "recs_with_param_item": n_items_rec,
                      "items": int((items.abbr == a).sum())})
    audit = pd.DataFrame(audit)
    print("\n== 6a. carried recs (links 1-19) that name at least one plotted parameter")
    print(audit.to_string())
    # check items.csv against a fresh parse
    fresh = sum(1 for t in df[df.link < 20].last_rec_change for it in rp.parse(t) if it.get("kind"))
    print(f"  items in recommended_changes.csv: {len(items)}; fresh parse of last_rec_change (links 1-19): {fresh}")

    val = df.set_index(["abbr", "link"])
    fi = []
    for _, it in items.iterrows():
        k1 = int(it.link)            # receiving link
        k0 = k1 - 1
        cur = val.at[(it.abbr, k0), it.param] if (it.abbr, k0) in val.index else np.nan
        new = val.at[(it.abbr, k1), it.param] if (it.abbr, k1) in val.index else np.nan
        cur, new = pd.to_numeric(cur, errors="coerce"), pd.to_numeric(new, errors="coerce")
        want = int(it.direction) if it.direction else 0
        if want == 0 and pd.notna(it.target) and pd.notna(cur):
            want = int(np.sign(it.target - cur))
        rec_ = {"abbr": it.abbr, "link": k1, "param": it.param, "kind": it.kind, "want": want,
                "value_k": cur, "value_k1": new, "target": it.target, "lo": it.lo, "hi": it.hi}
        if pd.isna(cur) or pd.isna(new) or want == 0:
            rec_["status"] = "not measurable"
        else:
            rel = (new - cur) / abs(cur) if cur != 0 else np.sign(new - cur)
            rec_["rel_change_pct"] = 100 * rel
            if abs(rel) < UNCHANGED_REL:
                rec_["status"] = "unchanged"
            elif np.sign(rel) == want:
                rec_["status"] = "followed"
            else:
                rec_["status"] = "opposite"
            if it.kind in ("value", "range"):
                lo, hi = min(it.lo, it.hi), max(it.lo, it.hi)
                rec_["reached"] = bool(lo - REACH_REL * abs(lo) <= new <= hi + REACH_REL * abs(hi))
            elif it.kind == "bound":
                rec_["reached"] = bool(new <= it.lo * (1 + REACH_REL)) if want < 0 else bool(
                    new >= it.lo * (1 - REACH_REL))
        fi.append(rec_)
    fi = pd.DataFrame(fi)
    fi.to_csv(args.out / "follow_through_items.csv", index=False)
    st = fi.groupby(["abbr", "status"]).size().unstack(fill_value=0).reindex(order).fillna(0).astype(int)
    for c in ("followed", "unchanged", "opposite", "not measurable"):
        if c not in st:
            st[c] = 0
    st = st[["followed", "unchanged", "opposite", "not measurable"]]
    st.loc["ALL"] = st.sum()
    st["measurable"] = st[["followed", "unchanged", "opposite"]].sum(axis=1)
    st["followed_pct"] = (100 * st.followed / st.measurable.replace(0, np.nan)).round(1)
    tgt = fi[fi.get("reached").notna()] if "reached" in fi else fi.iloc[0:0]
    reach = tgt.groupby("abbr").reached.agg(["sum", "size"]).reindex(order)
    st["target_items"] = reach["size"].reindex(st.index)
    st["reached"] = reach["sum"].reindex(st.index)
    st.loc["ALL", "target_items"] = len(tgt)
    st.loc["ALL", "reached"] = int(tgt.reached.sum()) if len(tgt) else 0
    st.to_csv(args.out / "follow_through_by_combination.csv")
    print(f"\n== 6b. follow-through per parsed item (unchanged = |rel| < {100*UNCHANGED_REL}%, "
          f"reached = within {100*REACH_REL}% of target range / bound)")
    print(st.to_string())
    bp = fi[fi.status != "not measurable"].groupby(["param", "status"]).size().unstack(fill_value=0)
    bp.to_csv(args.out / "follow_through_by_param.csv")
    print("\n   by parameter")
    print(bp.to_string())
    print("\n   not measurable items:")
    print(fi[fi.status == "not measurable"][["abbr", "link", "param", "kind", "want", "value_k", "value_k1"]].to_string())

    # base rate: how often a parameter changes between consecutive links when it was / was not recommended
    params = [c for c, _ in rp.PARAM_PATTERNS if c in df.columns]
    recd = {(r.abbr, int(r.link), r.param) for r in items.itertuples()}
    br = []
    for a in order:
        for k1 in range(2, 21):
            if (a, k1 - 1) not in val.index or (a, k1) not in val.index:
                continue
            for prm in params:
                v0 = pd.to_numeric(val.at[(a, k1 - 1), prm], errors="coerce")
                v1 = pd.to_numeric(val.at[(a, k1), prm], errors="coerce")
                if pd.isna(v0) or pd.isna(v1) or v0 == 0:
                    continue
                br.append({"abbr": a, "link": k1, "param": prm, "recommended": (a, k1, prm) in recd,
                           "changed": abs(v1 - v0) / abs(v0) >= UNCHANGED_REL})
    br = pd.DataFrame(br)
    brt = br.groupby("recommended").changed.agg(transitions="size", changed="sum")
    brt["changed_pct"] = (100 * brt.changed / brt.transitions).round(1)
    brp = br.groupby(["param", "recommended"]).changed.agg(["size", "sum"]).unstack()
    brt.to_csv(args.out / "change_base_rate.csv")
    brp.to_csv(args.out / "change_base_rate_by_param.csv")
    print("\n== 6b2. share of (parameter, link transition) pairs where the parameter changed (>= 0.1%), "
          "recommended vs not (all combinations)")
    print(brt.to_string())
    print(br.groupby(["abbr", "recommended"]).changed.agg(["size", "sum"]).unstack().to_string())
    print(brp.to_string())

    # per receiving link
    fl = []
    for (a, k1), g in fi[fi.status != "not measurable"].groupby(["abbr", "link"]):
        n_f = int((g.status == "followed").sum())
        lab = "followed" if n_f == len(g) else ("partly" if n_f else "not followed")
        r0, r1 = val.loc[(a, k1 - 1)], val.loc[(a, k1)]
        f0 = r0.fuel_kg if r0.flew else np.nan
        f1 = r1.fuel_kg if r1.flew else np.nan
        fl.append({"abbr": a, "link": k1, "items": len(g), "items_followed": n_f, "link_follow": lab,
                   "fuel_k_kg": f0, "fuel_k1_kg": f1, "fuel_change_kg": f1 - f0,
                   "conv_k": r0.ec_converged, "conv_k1": r1.ec_converged,
                   "b187_k1": bool(r1.b187), "run_status_k1": r1.run_status})
    fl = pd.DataFrame(fl)
    fl.to_csv(args.out / "follow_through_links.csv", index=False)
    print("\n== 6c. per receiving link: followed all / partly / not")
    print(fl.groupby(["abbr", "link_follow"]).size().unstack(fill_value=0).reindex(order).fillna(0).astype(int))
    print(fl.link_follow.value_counts())

    def fuel_tab(sub: pd.DataFrame, label: str) -> pd.DataFrame:
        t = sub.dropna(subset=["fuel_change_kg"]).groupby("link_follow").fuel_change_kg.agg(
            n="size", mean_kg="mean", median_kg="median", improved=lambda x: int((x < 0).sum()))
        t.insert(0, "subset", label)
        return t

    ft = pd.concat([fuel_tab(fl, "both links flew"),
                    fuel_tab(fl[(fl.conv_k == True) & (fl.conv_k1 == True)], "both flew and converged"),  # noqa: E712
                    fuel_tab(fl[~fl.b187_k1], "both flew, receiving link not B187")])
    ft.to_csv(args.out / "follow_vs_fuel.csv")
    print("\n== 6d. fuel change k -> k+1 (kg, negative = less fuel) by whether k+1 followed the recommendation")
    print(ft.to_string())
    print("\n   per combination (both links flew)")
    print(fl.dropna(subset=["fuel_change_kg"]).groupby(["abbr", "link_follow"]).fuel_change_kg.agg(
        ["size", "mean", "median"]).to_string())

    # ---------------------------------------------------------------- 7. verbatim examples
    ex = []
    for a in order:
        g = df[(df.abbr == a) & (df.last_norm != "") & (df.link < 20)]
        if g.empty:
            continue
        top_role = Counter(g.last_rec_to).most_common(1)[0][0]
        top_var = Counter(lab for c in g.last_rec_change for lab in rec.variable_labels(c)).most_common(1)[0][0]
        pick = g[(g.last_rec_to == top_role) & g.last_rec_change.map(lambda c: top_var in rec.variable_labels(c))]
        r = (pick if len(pick) else g).iloc[0]
        nxt = df[(df.abbr == a) & (df.link == r.link + 1)].iloc[0]
        ex.append({"abbr": a, "link": int(r.link), "verdict": r.last_verdict, "to": r.last_rec_to,
                   "modal_role": top_role, "modal_variable": top_var, "text": r.last_rec_change,
                   "inherited_by_next_identical": norm(nxt.inherited_rec_change) == r.last_norm})
    ex = pd.DataFrame(ex)
    ex.to_csv(args.out / "examples.csv", index=False)
    print("\n== 7. verbatim examples (first link 1-19 whose carried rec has the modal role and modal variable)")
    for _, r in ex.iterrows():
        print(f"  {r.abbr} link {r.link} [{r.verdict} -> {r.to}] modal=({r.modal_role}, {r.modal_variable}) "
              f"inherited_identical={r.inherited_by_next_identical}\n    \"{r.text}\"")
    fi_ex = fi.merge(ex[["abbr", "link"]].assign(link=ex.link + 1), on=["abbr", "link"])
    print("   follow-through of those examples in the next link:")
    print(fi_ex[["abbr", "link", "param", "kind", "want", "value_k", "value_k1", "target", "status"]].to_string())


if __name__ == "__main__":
    main()
