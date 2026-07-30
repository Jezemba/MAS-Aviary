"""Consolidated live monitor for the 8-combo no-API subagent verification.

Scans every logs/live_run/<combo>.jsonl and writes ONE dashboard to
logs/live_run/MONITOR.txt (+ MONITOR.json) each cycle, so you can `tail -f` a single
file to watch all 8 combos at once. Flags problems LOUDLY (SU2 fail, uncoupled, non-
physical fuel, pipeline error) so it's obvious when to stop.

Run:  python scripts/live_monitor.py            # loop, refresh every 15s
      python scripts/live_monitor.py --once      # one snapshot (print + write)
"""
import argparse
import glob
import json
import os
import time

RUN_DIR = "logs/live_run"
N_LINKS = int(os.environ.get("N_LINKS", "5"))
FUEL_LO, FUEL_HI = 7000.0, 16000.0   # physical-plausibility band for cruise fuel


def _rows(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception:
        pass
    return out


def _combo_status(path):
    combo = os.path.basename(path)[:-6]
    rows = _rows(path)
    results = [r for r in rows if r.get("step") == "result"]
    last = rows[-1] if rows else {}
    issues = []
    for r in rows:
        if r.get("step") == "su2_done" and r.get("exit_code") not in (0, None):
            issues.append(f"L{r.get('link')} SU2 exit={r.get('exit_code')}")
        if r.get("step") == "result":
            if r.get("ok") is False:
                issues.append(f"L{r.get('link')} FAILED")
            if r.get("coupled") is False:
                issues.append(f"L{r.get('link')} UNCOUPLED")
            fuel = r.get("fuel")
            if isinstance(fuel, (int, float)) and not (FUEL_LO <= fuel <= FUEL_HI):
                issues.append(f"L{r.get('link')} fuel={fuel:.0f} out-of-band")
    fuels = [round(r.get("fuel"), 0) for r in results if isinstance(r.get("fuel"), (int, float))]
    done = len(results)
    state = "DONE" if done >= N_LINKS else ("ISSUE" if issues else "running")
    return {
        "combo": combo, "links_done": done, "state": state,
        "cur_link": last.get("link"), "cur_step": last.get("step"),
        "last_fuel": last.get("fuel"), "last_cd": last.get("cruise_cd"),
        "last_coupled": last.get("coupled"), "last_drag_factor": last.get("drag_factor"),
        "fuel_trajectory": fuels, "issues": issues,
    }


def snapshot():
    paths = sorted(p for p in glob.glob(os.path.join(RUN_DIR, "*.jsonl"))
                   if not os.path.basename(p).startswith(("MONITOR", "_")))
    combos = [_combo_status(p) for p in paths]
    lines = []
    ts = time.strftime("%H:%M:%S")
    n_issue = sum(1 for c in combos if c["issues"])
    n_done = sum(1 for c in combos if c["state"] == "DONE")
    lines.append(f"===== LIVE MONITOR  {ts}  |  {len(combos)} combos: {n_done} done, {n_issue} with ISSUES =====")
    lines.append(f"{'combo':40} {'link':>5} {'step':16} {'fuel':>9} {'cd':>8} {'coupled':>7}  status")
    for c in combos:
        flag = "  <-- ISSUE" if c["issues"] else ("  DONE" if c["state"] == "DONE" else "")
        fuel = f"{c['last_fuel']:.0f}" if isinstance(c["last_fuel"], (int, float)) else "-"
        cd = f"{c['last_cd']:.4f}" if isinstance(c["last_cd"], (int, float)) else "-"
        cp = str(c["last_coupled"]) if c["last_coupled"] is not None else "-"
        lines.append(f"{c['combo']:40} {str(c['links_done'])+'/'+str(N_LINKS):>5} "
                     f"{str(c['cur_step'] or '-'):16} {fuel:>9} {cd:>8} {cp:>7}  {c['state']}{flag}")
        if c["fuel_trajectory"]:
            lines.append(f"    fuel trajectory: {c['fuel_trajectory']}")
        for iss in c["issues"]:
            lines.append(f"    ! {iss}")
    if not combos:
        lines.append("(no combo logs yet)")
    text = "\n".join(lines) + "\n"
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(os.path.join(RUN_DIR, "MONITOR.txt"), "w") as f:
            f.write(text)
        with open(os.path.join(RUN_DIR, "MONITOR.json"), "w") as f:
            json.dump({"ts": ts, "combos": combos, "n_issue": n_issue, "n_done": n_done}, f, indent=2)
    except Exception:
        pass
    return text, n_done, len(combos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if args.once:
        text, _, _ = snapshot()
        print(text)
        return
    for _ in range(400):  # ~100 min ceiling
        text, n_done, n = snapshot()
        print(text, flush=True)
        if n and n_done >= n:
            print("ALL COMBOS DONE.", flush=True)
            break
        time.sleep(15)


if __name__ == "__main__":
    main()
