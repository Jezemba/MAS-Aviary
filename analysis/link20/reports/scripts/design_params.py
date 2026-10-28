"""Design-parameter tables for the 20-link reports (02_design_parameters.md).

How the design variables moved over the 20 chained links of each combination: what changed and how often, the
link-20 end state against the unchanged anchor (baseline), the range explored, drift versus oscillation, co-movement
with fuel (descriptive), recommended versus actual change, and the B187 scale factor 0.994.

Inputs: links.csv (analysis.link20.extract), recommended_changes.csv (analysis.link20.plots), agent_bounds.json
(analysis/link20/agent_bounds.py), the baseline refs line of plots_run.log, and the raw run folders (read-only) for
what links.csv does not hold: wing span (design.json mission_applied), configure_mission inputs, tigl morph
targets / measured results, and which aviary keys the agents set.

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    python -m analysis.link20.reports.scripts.design_params --links ../out_20261028/links.csv \
        --recs ../out_20261028/recommended_changes.csv --bounds ../out/agent_bounds.json \
        --plots-log ../out_20261028/plots_run.log --out ../reports_20261028/design_params
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.link20.extract import B187_SCALE, SOURCES, read_json, read_jsonl

ABBREV = {"sequential": "S", "orchestrated": "O", "networked": "N",
          "staged_pipeline": "SP", "iterative_feedback": "IF", "graph_routed": "GR"}
ORDER = ["S_SP", "S_IF", "S_GR", "O_SP", "O_IF", "O_GR", "N_IF", "N_GR"]

# (column, short label, unit). The 16 plotted parameters (plots.PARAMS) plus wing span read from design.json.
PARAMS = [
    ("Aircraft.Wing.AREA", "wing_area", "m2"),
    ("Aircraft.Wing.ASPECT_RATIO", "wing_AR", "-"),
    ("Aircraft.Wing.SWEEP", "wing_sweep", "deg"),
    ("Aircraft.Wing.TAPER_RATIO", "wing_taper", "-"),
    ("Aircraft.Wing.SPAN", "wing_span", "m"),
    ("Aircraft.Fuselage.LENGTH", "fus_length", "m"),
    ("Aircraft.Fuselage.MAX_HEIGHT", "fus_height", "m"),
    ("Aircraft.Fuselage.MAX_WIDTH", "fus_width", "m"),
    ("Aircraft.Engine.SCALE_FACTOR", "engine_scale", "-"),
    ("engine.T4_MAX", "T4", "degR"),
    ("engine.splitter.BPR", "BPR", "-"),
    ("engine.fan.PR", "fan_PR", "-"),
    ("engine.lpc.PR", "LPC_PR", "-"),
    ("engine.hpc.PR", "HPC_PR", "-"),
    ("Mission.Design.LIFT_COEFFICIENT", "design_CL", "-"),
    ("Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "drag_factor", "-"),
    ("Aircraft.Wing.MASS_SCALER", "wing_mass_scaler", "-"),
]
OUTPUTS = [("aero.cl_cruise", "aero_CL", "-"), ("aero.cd_cruise", "aero_CD", "-"),
           ("structures.mtom_kg", "MTOM", "kg"), ("propulsion.sfc_cruise", "SFC", "as recorded"),
           ("propulsion.fn_lbf", "thrust_Fn", "lbf")]
LABEL = {c: l for c, l, _ in PARAMS + OUTPUTS}
UNIT = {c: u for c, l, u in PARAMS + OUTPUTS}

# A value counts as changed when it differs by more than 0.1 % (tigl re-measures the morphed wing, so an
# untouched wing reads 122.83 instead of 122.78 m2 = 0.04 %).
REL_TOL = 1e-3
B187_TOL = 5e-4
# Descriptive flags for values far outside a 239-seat transport (thresholds chosen here, stated in the report).
FLAGS = {"Aircraft.Wing.SWEEP": ("<", 0.0, "negative sweep"),
         "Aircraft.Wing.ASPECT_RATIO": (">", 15.0, "AR > 15"),
         "engine.T4_MAX": ("<", 2000.0, "T4 < 2000 degR (value looks like K)")}
TAIL_UIDS = ("Wing2H", "Wing3V")


def changed(a, b) -> bool | None:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return None
    scale = max(abs(a), abs(b), 1e-12)
    return abs(a - b) / scale > REL_TOL


def sgn(a, b) -> int | None:
    c = changed(a, b)
    if c is None:
        return None
    return 0 if not c else (1 if b > a else -1)


def baseline_refs(plots_log: Path) -> dict:
    first = plots_log.read_text().splitlines()[0]
    return ast.literal_eval(first.split(":", 1)[1].strip())


# ---------------------------------------------------------------- raw scan

def raw_scan(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per link: span from design.json; per combination: what the agents set (tails, mission, aviary keys)."""
    roots = {c: root for _, root, combos in SOURCES for c in combos}
    span_rows, act = [], []
    for _, r in df.iterrows():
        d = roots[r.combination] / f"repeat_{int(r.link) - 1:03d}" / r.combination
        design = read_json(d / "design.json") or {}
        ma = design.get("mission_applied") or {}
        wing = ((design.get("design_space") or {}).get("geometry") or {}).get("Wing1") or {}
        span = ma.get("Aircraft.Wing.SPAN")
        span_rows.append({"combination": r.combination, "link": int(r.link),
                          "Aircraft.Wing.SPAN": float(span) if span is not None else
                          (float(wing["span_m"]) if wing.get("span_m") is not None else None),
                          "span_source": "mission_applied" if span is not None else
                          ("design_space.Wing1.span_m" if wing.get("span_m") is not None else None),
                          "design_mission": json.dumps(design.get("mission"), sort_keys=True)})
        for e in read_jsonl(d / "knowledge_base.jsonl"):
            if e.get("agent") == "framework":
                continue
            t, st, i, o = e.get("tool"), e.get("status"), e.get("inputs") or {}, e.get("outputs") or {}
            base = {"combination": r.combination, "link": int(r.link), "tool": t, "status": st}
            if t == "configure_mission":
                act.append({**base, "kind": "mission", "key": json.dumps(
                    {k: i.get(k) for k in ("range_nmi", "num_passengers", "cruise_mach", "cruise_altitude_ft")},
                    sort_keys=True)})
            elif t in ("set_high_level_parameters", "morph_wing", "morph_fuselage"):
                uid = i.get("component_uid") or o.get("component_uid") or t
                after = (o.get("geometry_morph") or {}).get("after") or o.get("after") or {}
                before = (o.get("geometry_morph") or {}).get("before") or o.get("before") or {}
                act.append({**base, "kind": "morph", "key": uid,
                            "updates": json.dumps(i.get("updates") or {k: v for k, v in i.items()
                                                                       if k not in ("session_id", "component_uid")}),
                            "after_area": after.get("reference_area"), "after_ar": after.get("aspect_ratio"),
                            "after_sweep": after.get("sweep_deg"), "before_area": before.get("reference_area"),
                            "before_ar": before.get("aspect_ratio"), "before_sweep": before.get("sweep_deg")})
            elif t == "set_aircraft_parameters":
                for k in (i.get("parameters") or {}):
                    act.append({**base, "kind": "aviary_key", "key": k})
            elif t == "set_inputs":
                for k in (i.get("values") or {}):
                    act.append({**base, "kind": "cycle_key", "key": k})
    return pd.DataFrame(span_rows), pd.DataFrame(act)


# ---------------------------------------------------------------- tables

def series(g: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(g.set_index("link")[col], errors="coerce").reindex(range(1, 21))


def change_counts(df, refs) -> pd.DataFrame:
    rows = []
    for ab, g in df.groupby("abbrev"):
        for col, lab, _ in PARAMS:
            v = series(g, col)
            ref = refs.get(col)
            vs_base = [changed(ref, x) for x in v.dropna()] if ref is not None else []
            prev = ref
            moves = []
            for k in range(1, 21):
                x = v.loc[k]
                if pd.isna(x):
                    continue           # gap: the next recorded link is compared with the last recorded value
                if prev is not None:
                    moves.append((k, changed(prev, x)))
                prev = x
            moved_links = [k for k, c in moves if c]
            rows.append({"abbrev": ab, "param": lab, "links_with_value": int(v.notna().sum()),
                         "links_differing_from_baseline": sum(bool(c) for c in vs_base) if ref is not None else None,
                         "transitions": len(moves), "transitions_moved": len(moved_links),
                         "moved_at_links": ",".join(map(str, moved_links)),
                         "distinct_values": int(v.dropna().round(6).nunique())})
    return pd.DataFrame(rows)


def end_state(df, refs) -> pd.DataFrame:
    rows = []
    for ab, g in df.groupby("abbrev"):
        for col, lab, unit in PARAMS + OUTPUTS:
            v = series(g, col).dropna()
            ref = refs.get(col)
            if v.empty:
                rows.append({"abbrev": ab, "param": lab, "unit": unit})
                continue
            last_link = int(v.index[-1])
            end = float(v.iloc[-1])
            rows.append({"abbrev": ab, "param": lab, "unit": unit, "baseline": ref,
                         "link1": float(v.loc[1]) if 1 in v.index else None, "end_link": last_link, "end": end,
                         "pct_vs_baseline": 100 * (end - ref) / abs(ref) if ref not in (None, 0) else None,
                         "pct_vs_link1": 100 * (end - v.loc[1]) / abs(v.loc[1]) if 1 in v.index and v.loc[1] else None,
                         "min": float(v.min()), "min_link": int(v.idxmin()), "max": float(v.max()),
                         "max_link": int(v.idxmax())})
    return pd.DataFrame(rows)


def drift(df, refs) -> pd.DataFrame:
    rows = []
    for ab, g in df.groupby("abbrev"):
        for col, lab, unit in PARAMS:
            v = series(g, col).dropna()
            ref = refs.get(col)
            seq = ([ref] if ref is not None else []) + list(v.values)
            if len(seq) < 2:
                continue
            d = np.diff(seq)
            cum, net = float(np.abs(d).sum()), float(abs(seq[-1] - seq[0]))
            signs = [s for s in (sgn(a, b) for a, b in zip(seq, seq[1:])) if s]
            rows.append({"abbrev": ab, "param": lab, "unit": unit,
                         "start": "baseline" if ref is not None else "link 1",
                         "cumulative_abs_move": cum, "net_move": net,
                         "net_over_cumulative": net / cum if cum > 0 else None,
                         "cumulative_pct_of_start": 100 * cum / abs(seq[0]) if seq[0] else None,
                         "direction_reversals": sum(1 for a, b in zip(signs, signs[1:]) if a != b),
                         "nonzero_moves": len(signs)})
    return pd.DataFrame(rows)


def extremes(df) -> pd.DataFrame:
    rows = []
    for col, (op, thr, what) in FLAGS.items():
        v = pd.to_numeric(df[col], errors="coerce")
        hit = df[(v < thr) if op == "<" else (v > thr)]
        for _, r in hit.sort_values(["abbrev", "link"]).iterrows():
            rows.append({"flag": what, "abbrev": r.abbrev, "link": int(r.link), "param": LABEL[col],
                         "value": float(r[col]), "flew": bool(r.flew), "fuel_kg": r.fuel_kg})
    return pd.DataFrame(rows)


def b187_scale(df) -> pd.DataFrame:
    v = pd.to_numeric(df["Aircraft.Engine.SCALE_FACTOR"], errors="coerce")
    rows = []
    for ab, g in df.assign(_sf=v).groupby("abbrev"):
        hit = g[(g._sf - B187_SCALE).abs() < B187_TOL]
        rows.append({"abbrev": ab, "end_scale_0p994_links": ",".join(map(str, sorted(hit.link.astype(int)))) or "none",
                     "n_end_scale_0p994": len(hit),
                     "b187_flag_links": ",".join(map(str, sorted(g[g.b187.astype(bool)].link.astype(int)))) or "none",
                     "n_b187_flag": int(g.b187.astype(bool).sum())})
    return pd.DataFrame(rows)


def fuel_comovement(df) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Consecutive link pairs (k-1, k) where both flew: sign of each parameter's move vs sign of the fuel change."""
    pairs = []
    for ab, g in df.groupby("abbrev"):
        g = g.set_index("link").sort_index()
        for k in range(2, 21):
            if k not in g.index or k - 1 not in g.index:
                continue
            a, b = g.loc[k - 1], g.loc[k]
            if not (a.flew and b.flew):
                continue
            row = {"abbrev": ab, "link": k, "dfuel_kg": b.fuel_kg - a.fuel_kg,
                   "both_converged": bool(a.ec_converged is True or a.ec_converged == "True") and
                   bool(b.ec_converged is True or b.ec_converged == "True"),
                   "any_b187": bool(a.b187) or bool(b.b187)}
            for col, lab, _ in PARAMS:
                x0, x1 = pd.to_numeric(a[col], errors="coerce"), pd.to_numeric(b[col], errors="coerce")
                row[f"d_{lab}"] = sgn(x0, x1)
                row[f"pct_{lab}"] = 100 * (x1 - x0) / abs(x0) if pd.notna(x0) and pd.notna(x1) and x0 else None
            pairs.append(row)
    p = pd.DataFrame(pairs)
    rows = []
    for subset, q in (("all flown pairs", p), ("both converged", p[p.both_converged]),
                      ("no B187 in either link", p[~p.any_b187])):
        for _, lab, _ in PARAMS:
            d = q[f"d_{lab}"]
            for direction, name in ((1, "up"), (-1, "down")):
                m = q[d == direction]
                rows.append({"subset": subset, "param": lab, "move": name, "n_pairs": len(m),
                             "fuel_dropped": int((m.dfuel_kg < 0).sum()),
                             "median_dfuel_kg": float(m.dfuel_kg.median()) if len(m) else None})
            x = pd.to_numeric(q[f"pct_{lab}"], errors="coerce")
            ok = x.notna() & (d != 0) & d.notna()
            if ok.sum() >= 5:
                rho = float(x[ok].rank().corr(q.dfuel_kg[ok].rank()))
            else:
                rho = None
            rows.append({"subset": subset, "param": lab, "move": "spearman(pct move, dfuel) over moved pairs",
                         "n_pairs": int(ok.sum()), "rho": rho})
        rows.append({"subset": subset, "param": "(no parameter moved)", "move": "none",
                     "n_pairs": int((q[[f"d_{l}" for _, l, _ in PARAMS]].fillna(0) == 0).all(axis=1).sum()),
                     "fuel_dropped": int(((q[[f"d_{l}" for _, l, _ in PARAMS]].fillna(0) == 0).all(axis=1)
                                          & (q.dfuel_kg < 0)).sum())})
    return p, pd.DataFrame(rows)


def rec_vs_actual(df, recs) -> pd.DataFrame:
    rows = []
    for _, it in recs.iterrows():
        ab = f"{ABBREV[it.structure]}_{ABBREV[it.handler]}"
        g = df[df.abbrev == ab].set_index("link")
        k = int(it.link)
        col = it.param
        prev = pd.to_numeric(g[col].get(k - 1), errors="coerce") if k - 1 in g.index else np.nan
        cur = pd.to_numeric(g[col].get(k), errors="coerce") if k in g.index else np.nan
        if pd.notna(it.target) and pd.notna(it.current):
            # same 0.1 % tolerance as the actual move: a target that restates the current value has no direction
            rdir = sgn(it.current, it.target)
        else:
            rdir = int(it.direction)
        adir = sgn(prev, cur)
        if adir is None:
            outcome = "actual not recorded"
        elif rdir == 0:
            outcome = "no direction (target = current, or none)"
        elif adir == 0:
            outcome = "did not move"
        else:
            outcome = "same direction" if adir == rdir else "opposite direction"
        reached = None
        if it.kind in ("value", "range", "bound") and pd.notna(cur) and pd.notna(it.target):
            reached = (it.lo - 0.02 * abs(it.lo) <= cur <= it.hi + 0.02 * abs(it.hi)) if it.kind != "bound" else None
        rows.append({"abbrev": ab, "link": k, "param": LABEL.get(col, col), "kind": it.kind, "rec_dir": rdir,
                     "prev": prev, "actual": cur, "actual_dir": adir, "outcome": outcome,
                     "target": it.target, "within_2pct_of_target": reached})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--recs", type=Path, required=True)
    ap.add_argument("--bounds", type=Path, required=True)
    ap.add_argument("--plots-log", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 500)

    refs = baseline_refs(a.plots_log)
    print("baseline refs:", refs)
    df = pd.read_csv(a.links)
    df["abbrev"] = df.structure.map(ABBREV) + "_" + df.handler.map(ABBREV)
    df["flew"] = df.flew.astype(bool)
    print("rows", len(df), "| run_status", dict(Counter(df.run_status)))

    span, act = raw_scan(df)
    df = df.merge(span[["combination", "link", "Aircraft.Wing.SPAN", "span_source"]], on=["combination", "link"])
    print("span source:", dict(Counter(span.span_source.fillna("none"))))
    span.to_csv(a.out / "span_and_mission_per_link.csv", index=False)
    act.to_csv(a.out / "agent_setter_calls.csv", index=False)

    def save(t: pd.DataFrame, name: str, title: str) -> None:
        t.to_csv(a.out / f"{name}.csv", index=False)
        print(f"\n=== {title} ({name}.csv)\n{t.to_string(index=False)}")

    # what the agents set
    ok = act[act.status == "success"]
    m = ok[ok.kind == "mission"]
    base_mission = json.dumps({"cruise_altitude_ft": 33000, "cruise_mach": 0.78, "num_passengers": 239,
                               "range_nmi": 2500}, sort_keys=True)
    t = m.assign(abbrev=m.combination.map(dict(zip(df.combination, df.abbrev)))).groupby("abbrev").agg(
        configure_mission_calls=("key", "size"),
        calls_differing_from_baseline_mission=("key", lambda s: int((s != base_mission).sum())),
        distinct_inputs=("key", lambda s: " | ".join(sorted(set(s)))))
    save(t.reset_index(), "mission_settings", "configure_mission inputs (successful calls)")
    print("design.json mission values over 160 links:", dict(Counter(span.design_mission)))
    mo = ok[ok.kind == "morph"].assign(abbrev=lambda x: x.combination.map(dict(zip(df.combination, df.abbrev))))
    t = mo.groupby(["abbrev", "key"]).agg(calls=("link", "size"), links=("link", "nunique")).reset_index()
    save(t, "morph_calls_by_component", "successful tigl morph calls by component")
    tails = mo[mo.key.isin(TAIL_UIDS)]
    save(tails[["abbrev", "link", "key", "updates", "before_area", "after_area", "before_ar", "after_ar",
                "before_sweep", "after_sweep"]], "tail_morph_calls", "tail morph calls")
    wm = mo[mo.key == "Wing1"]
    t = wm.groupby("abbrev").agg(n_morphs=("link", "size"),
                                 area_min=("after_area", "min"), area_max=("after_area", "max"),
                                 ar_min=("after_ar", "min"), ar_max=("after_ar", "max"),
                                 sweep_min=("after_sweep", "min"), sweep_max=("after_sweep", "max")).reset_index()
    save(t, "wing_morph_range", "Wing1 measured geometry after every successful morph (incl. intermediate)")
    for kind in ("aviary_key", "cycle_key"):
        k = ok[ok.kind == kind].assign(abbrev=lambda x: x.combination.map(dict(zip(df.combination, df.abbrev))))
        t = k.groupby(["key", "abbrev"]).size().unstack(fill_value=0)
        t = t.reindex(columns=[c for c in ORDER if c in t.columns])
        save(t.reset_index(), f"{kind}_counts", f"successful {kind} settings (calls) per combination")

    # tables from links.csv
    cc = change_counts(df, refs)
    save(cc, "change_counts", "how often each parameter changed")
    piv = cc.pivot(index="param", columns="abbrev", values="transitions_moved")[ORDER].reindex(
        [l for _, l, _ in PARAMS])
    save(piv.reset_index(), "change_counts_pivot", "transitions where the parameter moved (>0.1 %)")
    piv2 = cc.pivot(index="param", columns="abbrev", values="transitions")[ORDER].reindex([l for _, l, _ in PARAMS])
    save(piv2.reset_index(), "transitions_pivot", "transitions with a value on both sides")
    es = end_state(df, refs)
    save(es, "end_state", "end state (last recorded link) vs baseline and link 1, range explored")
    pe = es.pivot(index="param", columns="abbrev", values="pct_vs_baseline")[ORDER].reindex(
        [l for _, l, _ in PARAMS])
    save(pe.round(1).reset_index(), "end_pct_vs_baseline_pivot", "end state % vs baseline")
    pv = es.pivot(index="param", columns="abbrev", values="end")[ORDER].reindex([l for _, l, _ in PARAMS + OUTPUTS])
    save(pv.reset_index(), "end_value_pivot", "end-state value")
    p1 = es.pivot(index="param", columns="abbrev", values="link1")[ORDER].reindex([l for _, l, _ in OUTPUTS])
    save(p1.reset_index(), "outputs_link1_pivot", "outputs at link 1")
    save(es[["abbrev", "param", "end_link"]].pivot(index="param", columns="abbrev", values="end_link")[ORDER]
         .reset_index(), "end_link_pivot", "which link the end state comes from")
    dr = drift(df, refs)
    save(dr, "drift", "drift vs oscillation")
    save(dr.pivot(index="param", columns="abbrev", values="net_over_cumulative")[ORDER]
         .reindex([l for _, l, _ in PARAMS]).round(2).reset_index(), "drift_ratio_pivot",
         "net / cumulative movement (1 = one-way drift, 0 = back where it started)")
    save(dr.pivot(index="param", columns="abbrev", values="cumulative_pct_of_start")[ORDER]
         .reindex([l for _, l, _ in PARAMS]).round(1).reset_index(), "cumulative_pct_pivot",
         "cumulative movement as % of the start value")
    save(extremes(df), "extremes_flagged", "flagged values (sweep < 0, AR > 15, T4 < 2000 degR)")
    save(b187_scale(df), "b187_scale", "end-state engine scale factor ~0.994 (B187)")
    sf = pd.to_numeric(df["Aircraft.Engine.SCALE_FACTOR"], errors="coerce")
    un = df[((sf - B187_SCALE).abs() < B187_TOL) & ~df.b187.astype(bool)]
    save(un[["abbrev", "link", "Aircraft.Engine.SCALE_FACTOR", "flew", "fuel_kg", "ec_converged", "run_status"]],
         "b187_scale_unflagged", "links ending at scale 0.994 that the b187 flag does not mark")
    bounds = json.loads(a.bounds.read_text())["bounds"]
    save(pd.DataFrame([{"variable": k, **v} for k, v in bounds.items()]), "agent_bounds",
         "agent_bounds.json (every value set, incl. intermediate morphs and set_inputs)")

    pairs, fc = fuel_comovement(df)
    pairs.to_csv(a.out / "fuel_pairs.csv", index=False)
    print("\nflown consecutive pairs:", len(pairs), "| both converged:", int(pairs.both_converged.sum()),
          "| no B187:", int((~pairs.any_b187).sum()))
    print("pairs per combo:", dict(Counter(pairs.abbrev)))
    save(fc, "fuel_comovement", "parameter moves vs fuel change between consecutive flown links")

    recs = pd.read_csv(a.recs)
    rv = rec_vs_actual(df, recs)
    save(rv, "rec_vs_actual", "recommended vs actual change (per item)")
    t = rv.groupby(["abbrev", "outcome"]).size().unstack(fill_value=0).reindex(ORDER).fillna(0).astype(int)
    save(t.reset_index(), "rec_vs_actual_by_combo", "recommended vs actual, outcome counts per combination")
    t = rv.groupby(["param", "outcome"]).size().unstack(fill_value=0)
    save(t.reset_index(), "rec_vs_actual_by_param", "recommended vs actual, outcome counts per parameter")
    t = rv.groupby(["kind", "outcome"]).size().unstack(fill_value=0)
    save(t.reset_index(), "rec_vs_actual_by_kind", "recommended vs actual, outcome counts per item kind")
    print("\nrec items:", len(rv), "| outcome totals:", dict(Counter(rv.outcome)))
    moved = rv[rv.outcome.isin(["same direction", "opposite direction"])]
    print("of items that moved with a recommended direction: same", int((moved.outcome == "same direction").sum()),
          "of", len(moved))
    judged = rv[rv.outcome.isin(["same direction", "opposite direction", "did not move"])]
    print("of items with a direction and a recorded actual: same", int((judged.outcome == "same direction").sum()),
          "of", len(judged))
    print("same-direction share without S_SP:", int((judged[judged.abbrev != "S_SP"].outcome == "same direction")
                                                     .sum()), "of", int((judged.abbrev != "S_SP").sum()))
    print("within 2% of target (value/range items with actual):",
          {str(k): v for k, v in Counter(rv.within_2pct_of_target.dropna()).items()})

    # values that look like caps, defaults or broken outputs
    def where(mask) -> pd.DataFrame:
        h = df[mask]
        return h.groupby("abbrev").link.apply(lambda s: ",".join(map(str, sorted(s.astype(int)))))
    num = lambda c: pd.to_numeric(df[c], errors="coerce")
    checks = {
        "drag_factor == 2.0": where(num("Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR") == 2.0),
        "thrust_Fn == 25000 lbf": where(num("propulsion.fn_lbf") == 25000.0),
        "thrust_Fn == 5900 lbf (B187 seed)": where((num("propulsion.fn_lbf") - 5900.0).abs() < 1.0),
        "MTOM == 78126 kg": where(num("structures.mtom_kg") == 78126.0),
        "SFC != 0.643235 (link-1 value)": where((num("propulsion.sfc_cruise") - 0.643235).abs() > 1e-5),
        "aero_CD < 0.01": where(num("aero.cd_cruise") < 0.01),
        "aero_CD > 0.1": where(num("aero.cd_cruise") > 0.1),
        "engine_scale > 3": where(num("Aircraft.Engine.SCALE_FACTOR") > 3.0),
        "design_CL != aero_CL": where((num("Mission.Design.LIFT_COEFFICIENT") - num("aero.cl_cruise")).abs() > 1e-6),
    }
    t = pd.DataFrame(checks).reindex(ORDER).fillna("none")
    save(t.T.reset_index(), "value_checks", "values that look like caps, defaults or broken outputs (links)")
    sfc = df[(num("propulsion.sfc_cruise") - 0.643235).abs() > 1e-5][["abbrev", "link", "propulsion.sfc_cruise",
                                                                      "engine.T4_MAX", "engine.splitter.BPR",
                                                                      "engine.fan.PR", "engine.hpc.PR"]]
    save(sfc.sort_values(["abbrev", "link"]), "sfc_changes", "links whose SFC differs from 0.643235")
    lo = df[(num("aero.cd_cruise") < 0.01) | (num("aero.cd_cruise") > 0.1)][
        ["abbrev", "link", "aero.cl_cruise", "aero.cd_cruise", "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "flew",
         "fuel_kg"]]
    save(lo.sort_values(["abbrev", "link"]), "cd_outliers", "aero CD outside 0.01-0.1")
    print("\nitems per parameter:", dict(Counter(rv.param)))
    nd = rv[rv.outcome.str.startswith("no direction")]
    print("no-direction items:", nd[["abbrev", "link", "param", "kind", "prev", "target"]].to_string(index=False))
    print("MTOM == 78126 kg link-ends:", int((num("structures.mtom_kg") == 78126.0).sum()), "of",
          int(num("structures.mtom_kg").notna().sum()))
    ex = extremes(df)
    for f, g in ex.groupby("flag"):
        print(f"{f}: {len(g)} link-ends, flew {int(g.flew.sum())}")
    print("N_GR link-20 thrust repr:", repr(df[(df.abbrev == "N_GR") & (df.link == 20)]["propulsion.fn_lbf"].iloc[0]))
    v = num("Aircraft.Engine.SCALE_FACTOR")
    print("\nengine scale over all links: n", int(v.notna().sum()), "min", v.min(), "max", v.max(),
          "| links below 1.0:", int((v < 1.0).sum()))
    # base rate: share of transitions in which a recommended parameter moved at all, regardless of recommendation
    moved_rate = cc.groupby("param")[["transitions", "transitions_moved"]].sum()
    save(moved_rate.reset_index(), "move_base_rate", "all transitions: how often each parameter moved at all")


if __name__ == "__main__":
    main()
