"""Reconcile the two recommendation-scoring rules of reports 02 and 05, item by item, and score a proposed rule.

Report 02 (design_params.rec_vs_actual) and report 05 (recommendations_report.py, section 6) score the same 116
parsed items of recommended_changes.csv. This script:

1. runs report 02's own function `design_params.rec_vs_actual` (imported, unchanged);
2. runs report 05's follow-through block, copied verbatim from recommendations_report.py main() section 6 into
   `follow_through_05` (that block is inline in main(), so it cannot be imported), and checks both outputs against the
   CSVs the reports were built from (rec_vs_actual.csv, follow_through_items.csv);
3. cross-tabulates the two classifications over the 116 items and lists every item where they disagree;
4. scores the proposed paper rule (`proposed`), overall and per combination.

Read-only on the inputs; writes only to --out.

Usage (aipexws8, Avion venv, from ~/Jessica/avion_analysis/code):
    PYTHONPATH=. python analysis/link20/reports/scripts/rec_scoring_reconcile.py \
        --links ../out_20261028/links.csv --items ../out_20261028/recommended_changes.csv \
        --ref02 ../reports_20261028/design_params/rec_vs_actual.csv \
        --ref05 ../reports_20261028/recommendations/follow_through_items.csv \
        --out ../reports_20261028/methods_q7
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.link20.reports.scripts import design_params as dp
from analysis.link20.reports.scripts import recommendations_report as rr

ORDER = dp.ORDER
TOL = 1e-3          # 0.1 %, the tolerance both reports use for "moved"
REACH_REL = rr.REACH_REL

pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 500)


def follow_through_05(items: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """VERBATIM copy of recommendations_report.py main(), section 6 (the `fi` loop); only the names
    UNCHANGED_REL / REACH_REL are taken from the module."""
    UNCHANGED_REL, REACH_REL = rr.UNCHANGED_REL, rr.REACH_REL
    items = items.copy()
    items["abbr"] = [rr.ABBR[(s, h)] for s, h in zip(items.structure, items.handler)]
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
    return pd.DataFrame(fi)


def proposed(items: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Proposed paper rule (stated in the methods note, section 7).

    v0 = parameter at the end of link k (the design the next link received), v1 = at the end of link k+1.
    Wanted direction:
      direction / pct items  -> the stated up/down;
      value items            -> sign(target - v0), but "already met" if |target - v0| <= 0.1 % of max(|target|,|v0|);
      range items            -> "already met" if v0 lies in [lo, hi] widened by 0.1 %, else towards the range;
      bound items            -> "already met" if v0 is already on the requested side (direction) of the bound
                                (widened by 0.1 %), else the stated direction.
    Scored = both v0 and v1 recorded and not "already met". Moved = |v1 - v0| / max(|v0|,|v1|) > 0.1 %
    (design_params.sgn). Followed = moved in the wanted direction; opposite = moved the other way; unchanged = not moved.
    Reached (value / range / bound, scored items) = v1 within the target widened by 1 % (report 05's REACH_REL).
    """
    items = items.copy()
    items["abbr"] = [rr.ABBR[(s, h)] for s, h in zip(items.structure, items.handler)]
    val = df.set_index(["abbr", "link"])
    rows = []
    for _, it in items.iterrows():
        k1 = int(it.link)
        v0 = pd.to_numeric(val.at[(it.abbr, k1 - 1), it.param], errors="coerce") if (it.abbr, k1 - 1) in val.index \
            else np.nan
        v1 = pd.to_numeric(val.at[(it.abbr, k1), it.param], errors="coerce") if (it.abbr, k1) in val.index else np.nan
        lo, hi = (min(it.lo, it.hi), max(it.lo, it.hi)) if pd.notna(it.lo) else (np.nan, np.nan)
        want, met = 0, False
        if pd.notna(v0):
            if it.kind in ("direction", "pct"):
                want = int(it.direction)
            elif it.kind == "value":
                s = dp.sgn(v0, it.target)
                met, want = (s == 0), (s or 0)
            elif it.kind == "range":
                met = lo - TOL * abs(lo) <= v0 <= hi + TOL * abs(hi)
                want = 0 if met else int(np.sign((lo + hi) / 2 - v0))
            elif it.kind == "bound":
                d = int(it.direction) or int(np.sign(lo - v0))
                met = (v0 <= lo * (1 + TOL)) if d < 0 else (v0 >= lo * (1 - TOL))
                want = 0 if met else d
        r = {"abbrev": it.abbr, "link": k1, "param": it.param, "kind": it.kind, "v0": v0, "v1": v1,
             "target": it.target, "lo": it.lo, "hi": it.hi, "want": want}
        if pd.isna(v0) or pd.isna(v1):
            r["status"] = "no recorded actual"
        elif met:
            r["status"] = "already met (excluded)"
        else:
            a = dp.sgn(v0, v1)
            r["status"] = "unchanged" if a == 0 else ("followed" if a == want else "opposite")
            if it.kind == "bound":
                r["reached"] = bool(v1 <= lo * (1 + REACH_REL)) if want < 0 else bool(v1 >= lo * (1 - REACH_REL))
            elif it.kind in ("value", "range"):
                r["reached"] = bool(lo - REACH_REL * abs(lo) <= v1 <= hi + REACH_REL * abs(hi))
        rows.append(r)
    return pd.DataFrame(rows)


def counts(t: pd.DataFrame, cats: list[str], col: str = "status", by: str = "abbrev") -> pd.DataFrame:
    c = t.groupby([by, col]).size().unstack(fill_value=0).reindex(ORDER).fillna(0).astype(int)
    for k in cats:
        if k not in c:
            c[k] = 0
    c = c[cats]
    c.loc["ALL"] = c.sum()
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--links", type=Path, required=True)
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--ref02", type=Path, required=True)
    ap.add_argument("--ref05", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    links = pd.read_csv(a.links)
    items = pd.read_csv(a.items)
    print("items:", len(items))

    # ---- report 02, its own function, on the same links table its main() uses for these columns
    d02 = links.copy()
    d02["abbrev"] = d02.structure.map(dp.ABBREV) + "_" + d02.handler.map(dp.ABBREV)
    r02 = dp.rec_vs_actual(d02, items)

    # ---- report 05, verbatim block, on the table its main() builds (abbr column, sorted)
    d05 = links.copy()
    d05["abbr"] = [rr.ABBR[(s, h)] for s, h in zip(d05.structure, d05.handler)]
    d05 = d05.sort_values(["abbr", "link"]).reset_index(drop=True)
    r05 = follow_through_05(items, d05)

    # ---- reproduction checks against the CSVs behind the reports
    ref02, ref05 = pd.read_csv(a.ref02), pd.read_csv(a.ref05)
    ok02 = (len(ref02) == len(r02)) and (ref02.outcome.tolist() == r02.outcome.tolist()) and \
        (ref02[["abbrev", "link"]].values.tolist() == r02[["abbrev", "link"]].values.tolist())
    ok05 = (len(ref05) == len(r05)) and (ref05.status.tolist() == r05.status.tolist()) and \
        (ref05[["abbr", "link", "param"]].values.tolist() == r05[["abbr", "link", "param"]].values.tolist())
    print("02 reproduces rec_vs_actual.csv:", ok02, "| 05 reproduces follow_through_items.csv:", ok05)
    print("02 outcome totals:", r02.outcome.value_counts().to_dict())
    print("05 status totals:", r05.status.value_counts().to_dict())

    # ---- item-by-item join (both iterate recommended_changes.csv in row order)
    assert (r02.link.values == r05.link.values).all() and (r02.abbrev.values == r05.abbr.values).all()
    cur_match = np.allclose(pd.to_numeric(items.current, errors="coerce").fillna(-1e99).values,
                            pd.to_numeric(r05.value_k, errors="coerce").fillna(-1e99).values)
    print("recommended_changes.current == value at end of link k (05's value_k):", cur_match)
    j = pd.DataFrame({"abbrev": r02.abbrev, "link": r02.link, "param": items.param, "kind": items.kind,
                      "parser_direction": items.direction, "target": items.target, "lo": items.lo, "hi": items.hi,
                      "value_k": r05.value_k, "value_k1": r05.value_k1, "rel_change_pct": r05.get("rel_change_pct"),
                      "rec_dir_02": r02.rec_dir, "outcome_02": r02.outcome, "want_05": r05.want,
                      "status_05": r05.status, "within_2pct_02": r02.within_2pct_of_target,
                      "reached_05": r05.get("reached")})
    prop = proposed(items, d05)
    j["status_proposed"] = prop.status.values
    j["want_proposed"] = prop.want.values
    j["reached_proposed"] = prop.get("reached").values if "reached" in prop else None
    j.to_csv(a.out / "rec_scoring_items.csv", index=False)

    ct = pd.crosstab(j.outcome_02, j.status_05, margins=True, margins_name="total")
    ct.to_csv(a.out / "crosstab_02_vs_05.csv")
    print("\n== cross-tab: report 02 outcome (rows) x report 05 status (columns), 116 items\n", ct.to_string())

    agree = {("same direction", "followed"), ("opposite direction", "opposite"), ("did not move", "unchanged"),
             ("actual not recorded", "not measurable")}
    dis = j[[(o, s) not in agree for o, s in zip(j.outcome_02, j.status_05)]]
    dis.to_csv(a.out / "disagreements_02_vs_05.csv", index=False)
    print(f"\n== items classified differently ({len(dis)})")
    print(dis[["abbrev", "link", "param", "kind", "parser_direction", "value_k", "target", "lo", "hi", "value_k1",
               "rel_change_pct", "rec_dir_02", "want_05", "outcome_02", "status_05"]].to_string(index=False))
    # within the no-direction group: does 05's want come from the parser direction or from sign(target - value)?
    nd = j[j.outcome_02.str.startswith("no direction")]
    print("\n02 no-direction items:", len(nd), "| parser direction nonzero:", int((nd.parser_direction != 0).sum()),
          "| |target - value_k| / |value_k| max %:",
          float((100 * (nd.target - nd.value_k).abs() / nd.value_k.abs()).max()))
    print("   05 want vs parser direction equal:", int((nd.want_05 == nd.parser_direction).sum()), "of", len(nd))
    # same-direction items where the two rules disagree on wanted direction
    both = j[(j.rec_dir_02.notna()) & (j.rec_dir_02 != 0) & (j.want_05 != 0)]
    print("items where both have a direction but they differ:", int((both.rec_dir_02 != both.want_05).sum()))
    # the two "reached" rules
    print("\nreached: 02 within 2 % (value/range, bound excluded):",
          j.within_2pct_02.dropna().astype(bool).value_counts().to_dict(),
          "| 05 within 1 % (value/range/bound, measurable):", j.reached_05.dropna().astype(bool).value_counts().to_dict())

    # ---- per-combination tables for both reports and the proposal
    c02 = counts(j.rename(columns={"outcome_02": "o"}), ["same direction", "opposite direction", "did not move",
                                                         "no direction (target = current, or none)",
                                                         "actual not recorded"], col="o")
    c05 = counts(j.rename(columns={"status_05": "o"}), ["followed", "unchanged", "opposite", "not measurable"], col="o")
    cp = counts(j.rename(columns={"status_proposed": "o"}), ["followed", "unchanged", "opposite",
                                                             "already met (excluded)", "no recorded actual"], col="o")
    cp["scored"] = cp[["followed", "unchanged", "opposite"]].sum(axis=1)
    cp["followed_pct"] = (100 * cp.followed / cp.scored.replace(0, np.nan)).round(1)
    cp["moved"] = cp.followed + cp.opposite
    cp["followed_of_moved_pct"] = (100 * cp.followed / cp.moved.replace(0, np.nan)).round(1)
    sc = j[j.reached_proposed.notna()]
    rch = sc.groupby("abbrev").reached_proposed.agg(lambda s: int(s.astype(bool).sum())).reindex(ORDER)
    rn = sc.groupby("abbrev").size().reindex(ORDER)
    cp["target_items"] = list(rn.fillna(0).astype(int)) + [len(sc)]
    cp["reached_1pct"] = list(rch.fillna(0).astype(int)) + [int(sc.reached_proposed.astype(bool).sum())]
    for name, t in (("counts_02", c02), ("counts_05", c05), ("counts_proposed", cp)):
        t.to_csv(a.out / f"{name}.csv")
        print(f"\n== {name}\n{t.to_string()}")
    print("\nproposed: already-met items:")
    print(j[j.status_proposed.str.startswith("already")][["abbrev", "link", "param", "kind", "value_k", "target", "lo",
                                                          "hi", "outcome_02", "status_05"]].to_string(index=False))
    print("\nproposed vs 05 cross-tab:\n", pd.crosstab(j.status_proposed, j.status_05, margins=True).to_string())
    print("\nproposed vs 02 cross-tab:\n", pd.crosstab(j.status_proposed, j.outcome_02, margins=True).to_string())
    print("\nproposed by parameter:\n", j.groupby(["param", "status_proposed"]).size().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    main()
