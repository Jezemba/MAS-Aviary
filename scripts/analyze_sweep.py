"""Turn a stat_batch_runner sweep into the numbers the paper actually needs.

Reads the design ledger plus the sweep's stdout log and reports, per combo and
overall:

  * coupling rate, and WHY each uncoupled run failed to couple
  * where the aero coefficients were captured from (B23: run_su2_solver vs
    read_history_csv) -- the mechanism that used to decide coupling by accident
  * identical-retry rate, the error-quality metric: a byte-identical repeat of
    the immediately preceding call means the caller read a response and got
    nothing actionable from it (16% across the 2026-08 sweeps, pre-fix)
  * SU2 solve success rate, split into config rejections vs other fatal exits
  * no-op links: a chain link whose end_params equal its start_params
    contributes no information (B32), so it must not be counted as a data point
  * fuel by coupling status (B17), which is the comparison the objective hinges on

Usage:
    python scripts/analyze_sweep.py <sweep_log> [--ledger logs/design_ledger.jsonl]
                                    [--since-combo N]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

CALL_RE = re.compile(r"Calling tool: '([a-z_0-9]+)' with arguments: (\{.{0,300})")
RUN_HDR_RE = re.compile(r"^\[(\d+)/(\d+)\] repeat=(\d+) combo=(\S+)", re.M)


OUTCOME_RE = re.compile(r"(?:\u2192|->)\s+(success|failed)\s*\|")


def split_runs(log_text: str, finished_only: bool = True) -> list[tuple[str, str]]:
    """Split the sweep log into (combo, text) per run, in order.

    Only runs that have actually FINISHED are returned by default. An
    in-progress run has no ledger row yet, and the backward scan that matches
    rows to runs would then reach past this sweep into an older one -- which it
    did: an in-flight orchestrated_staged_pipeline was attributed a 24908 kg
    result from a previous sweep. Analysing partial data is how a stale number
    ends up in a table.
    """
    marks = [(m.start(), m.group(4)) for m in RUN_HDR_RE.finditer(log_text)]
    out = []
    for i, (pos, combo) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(log_text)
        text = log_text[pos:end]
        if finished_only and not OUTCOME_RE.search(text):
            continue
        out.append((combo, text))
    return out


def retry_stats(run_text: str) -> tuple[int, int, Counter]:
    """Byte-identical consecutive repeats -- the error-quality metric."""
    calls = CALL_RE.findall(run_text)
    repeats = 0
    per_tool: Counter = Counter()
    prev = None
    for tool, args in calls:
        key = (tool, args)
        if key == prev:
            repeats += 1
            per_tool[tool] += 1
        prev = key
    return len(calls), repeats, per_tool


def su2_stats(run_text: str) -> dict[str, int]:
    solves = len(re.findall(r"Calling tool: 'run_su2_solver'", run_text))
    cfg_rejected = len(re.findall(r'"config_errors"', run_text))
    fatal = len(re.findall(r'"solver_error"', run_text))
    coeffs = len(re.findall(r'"final_coefficients"', run_text))
    return {
        "solves": solves,
        "config_rejected": cfg_rejected,
        "other_fatal": max(fatal - cfg_rejected, 0),
        "reported_coefficients": coeffs,
    }


def capture_source(run_text: str) -> str:
    """Which read path could have supplied the coefficients (B23)."""
    solver = '"final_coefficients"' in run_text
    history = "Calling tool: 'read_history_csv'" in run_text
    if solver and history:
        return "both"
    if solver:
        return "solver_only"      # would NOT have coupled before B23
    if history:
        return "history_only"
    return "neither"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep_log")
    ap.add_argument("--ledger", default="logs/design_ledger.jsonl")
    ap.add_argument("--last", type=int, default=0,
                    help="only the last N ledger rows (a sweep's own rows)")
    args = ap.parse_args()

    log_text = Path(args.sweep_log).read_text(errors="ignore")
    runs = split_runs(log_text)

    all_rows = [json.loads(line) for line in Path(args.ledger).read_text().splitlines()
                if line.strip()]
    if args.last:
        rows = all_rows[-args.last:]
    else:
        # The ledger is GLOBAL and accumulates across sweeps, so taking the last
        # N rows silently mixes in runs from a previous sweep -- the first draft
        # of this script attributed a discarded sweep's networked run to the
        # live one. Match the log's (combo, link) sequence instead, newest
        # occurrence first, so only this sweep's rows are analysed.
        wanted = []
        for combo, text in runs:
            m = re.search(r"chain link (\d+)/", text)
            wanted.append((combo, int(m.group(1)) - 1 if m else None))
        rows = []
        used = set()
        for combo, link in wanted:
            for i in range(len(all_rows) - 1, -1, -1):
                if i in used:
                    continue
                r = all_rows[i]
                if r["combo"] == combo and (link is None or r["chain"].get("link") == link):
                    rows.append(r)
                    used.add(i)
                    break
        rows.reverse()

    by_combo: dict[str, list] = defaultdict(list)
    for r in rows:
        by_combo[r["combo"]].append(r)

    print(f"runs in log: {len(runs)}   ledger rows analysed: {len(rows)}\n")

    # ---- per combo -------------------------------------------------------
    hdr = f"{'combo':44} {'lk':>2} {'coupled':>7} {'capture':>13} {'fuel':>8} {'cons':>5} {'noop':>5}"
    print(hdr)
    print("-" * len(hdr))
    noop_total = coupled_total = 0
    for combo, entries in by_combo.items():
        texts = [t for c, t in runs if c == combo]
        for i, d in enumerate(entries):
            a, o, ob, ch = d["aero_coupling"], d["outcomes"], d["objective"], d["chain"]
            coupled = bool(a.get("coupled"))
            coupled_total += coupled
            noop = ch.get("start_params") == ch.get("end_params")
            noop_total += noop
            cap = capture_source(texts[i]) if i < len(texts) else "?"
            fuel = o.get("fuel_burned_kg")
            print(f"{combo[:44]:44} {ch.get('link', '?'):>2} {str(coupled):>7} "
                  f"{cap:>13} {(f'{fuel:.0f}' if fuel else 'None'):>8} "
                  f"{ob['constraints_passed']}/{ob['constraints_total']:<3} {str(noop):>5}")

    # ---- coupling, split by whether it was actually MEASURED -------------
    # `cd_threshold_fallback` is not a measurement. It is the mis-calibrated
    # cruise_cd heuristic the ledger itself flags as unreliable ("under-reports
    # coupling; treat as unreliable"), used when data-plane state is missing.
    # Counting those rows as uncoupled is precisely the B1 false negative that
    # produced the original "7 of 8 combos fail to couple" claim, so they are
    # reported as NOT MEASURED and excluded from the rate.
    reliable = [r for r in rows if r["aero_coupling"].get("detection") == "data_plane"]
    unreliable = [r for r in rows if r["aero_coupling"].get("detection") != "data_plane"]
    rel_coupled = sum(1 for r in reliable if r["aero_coupling"].get("coupled"))

    print(f"\ncoupling (reliably measured only): {rel_coupled}/{len(reliable)}"
          f"{f' ({100 * rel_coupled / len(reliable):.0f}%)' if reliable else ''}")
    reasons = Counter(
        r["aero_coupling"].get("aero_coupling_status") or "unknown"
        for r in reliable if not r["aero_coupling"].get("coupled")
    )
    for reason, n in reasons.most_common():
        print(f"  uncoupled - {reason}: {n}")
    if unreliable:
        print(f"  NOT MEASURED (detection fell back): {len(unreliable)}"
              "   <- excluded; the fallback under-reports coupling")
        for r in unreliable:
            print(f"     {r['combo'][:44]} lk{r['chain'].get('link')} "
                  f"detection={r['aero_coupling'].get('detection')}")

    # ---- B23: how many runs depended on the new capture path --------------
    caps = Counter(capture_source(t) for _, t in runs)
    print("\ncapture path available (B23):")
    for k, n in caps.most_common():
        note = "  <- would NOT have coupled before B23" if k == "solver_only" else ""
        print(f"  {k}: {n}{note}")

    # ---- error quality ----------------------------------------------------
    tot_calls = tot_rep = 0
    worst: Counter = Counter()
    for _, t in runs:
        c, rep, per = retry_stats(t)
        tot_calls += c
        tot_rep += rep
        worst.update(per)
    print(f"\nidentical-retry rate: {tot_rep}/{tot_calls} "
          f"({100 * tot_rep / max(tot_calls, 1):.0f}%)   [pre-fix baseline: 16%]")
    for tool, n in worst.most_common(5):
        print(f"  {tool}: {n}")

    # ---- SU2 --------------------------------------------------------------
    agg = Counter()
    for _, t in runs:
        for k, v in su2_stats(t).items():
            agg[k] += v
    solves = agg["solves"]
    failed = agg["config_rejected"] + agg["other_fatal"]
    print(f"\nSU2 solves: {solves}, failed {failed} "
          f"(config {agg['config_rejected']}, other fatal {agg['other_fatal']})")
    if solves:
        print(f"  success rate: {100 * (solves - failed) / solves:.0f}%   "
              f"[progression: 8.7% -> 20% -> 34%]")

    # ---- B32 / B17 ---------------------------------------------------------
    print(f"\nno-op links (start_params == end_params): {noop_total}/{len(rows)}"
          "   [these carry no information -- B32]")
    empty_design = sum(1 for r in rows if not r.get("design_applied"))
    print(f"runs with empty design_applied: {empty_design}   [B32]")

    def fuels(want: bool):
        return [r["outcomes"]["fuel_burned_kg"] for r in rows
                if bool(r["aero_coupling"].get("coupled")) is want
                and r["outcomes"].get("fuel_burned_kg")]

    cf, uf = fuels(True), fuels(False)
    print("\nfuel by coupling status (B17):")
    for label, vals in (("coupled", cf), ("uncoupled", uf)):
        if vals:
            print(f"  {label:9} n={len(vals):2}  mean {sum(vals) / len(vals):8.0f}  "
                  f"min {min(vals):8.0f}  max {max(vals):8.0f}")
    if cf and uf:
        delta = sum(cf) / len(cf) - sum(uf) / len(uf)
        print(f"  coupled - uncoupled = {delta:+.0f} kg"
              f"{'   <- coupling still penalised (B17 open)' if delta > 0 else ''}")


if __name__ == "__main__":
    main()
