"""Fast 2-link pipeline verification for one combo.

Runs run_link.py twice for the given combo — link 0 (seed-42 anchor) and link 1
(anchor end-state with one WING change) — to confirm the full coupled pipeline works
and mass responds to geometry, WITHOUT a full 5-link optimization. Coordination-agnostic
(run_link is deterministic); this is a pipeline stress/health check per combo.

Writes its own per-combo log (logs/live_run/<combo>.jsonl) and a verdict JSON
(logs/live_run/<combo>_verify.json). Never overwrites other combos' data.

Usage:  python scripts/verify_combo.py <combo>
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = "/home/aipexws3/Jessica/Avion/.venv/bin/python3"


def run_link(combo, link, design, logf, statef):
    env = dict(os.environ, PYTHONPATH=".", MCP_LOG_FILE=logf, MCP_STATE_FILE=statef)
    p = subprocess.run(
        [PY, "scripts/run_link.py", "--link", str(link), "--combo", combo,
         "--design", json.dumps(design)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )
    # run_link prints one JSON object on stdout (may be followed by teardown noise)
    for line in reversed(p.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"ok": False, "notes": [f"no JSON from run_link (stderr tail: {p.stderr[-200:]})"]}


def wing_from_log(logf, link):
    """Read the mass-step wing_kg + geom for a link from the combo log."""
    try:
        with open(logf) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except OSError:
        return None, None
    for r in rows:
        if r.get("link") == link and r.get("step") == "mass":
            return r.get("wing_kg"), r.get("geom")
    return None, None


def main():
    combo = sys.argv[1]
    logf = os.path.join(ROOT, "logs", "live_run", f"{combo}.jsonl")
    statef = os.path.join(ROOT, "logs", "live_run", f"{combo}_state.json")
    os.makedirs(os.path.dirname(logf), exist_ok=True)
    # fresh log for this combo
    for f in (logf, statef):
        if os.path.exists(f):
            os.remove(f)

    print(f"[{combo}] link 0 (anchor)...", flush=True)
    r0 = run_link(combo, 0, {}, logf, statef)
    # link 1: carry forward + one clear WING change (bump AREA) so mass MUST move
    d1 = dict(r0.get("end_design") or {})
    base_area = d1.get("Aircraft.Wing.AREA", 126.0)
    d1["Aircraft.Wing.AREA"] = round(base_area + 20.0, 3)  # clear wing change
    print(f"[{combo}] link 1 (AREA {base_area}->{d1['Aircraft.Wing.AREA']})...", flush=True)
    r1 = run_link(combo, 1, d1, logf, statef)

    w0, g0 = wing_from_log(logf, 0)
    w1, g1 = wing_from_log(logf, 1)

    checks = {
        "link0_ok": bool(r0.get("ok")),
        "link1_ok": bool(r1.get("ok")),
        "link0_coupled": bool(r0.get("coupled")),
        "link1_coupled": bool(r1.get("coupled")),
        "link0_morphed": g0 == "morphed",
        "link1_morphed": g1 == "morphed",
        "mass_responds": (w0 is not None and w1 is not None and abs((w0 or 0) - (w1 or 0)) > 1.0),
    }
    verdict = {
        "combo": combo,
        "pass": all(checks.values()),
        "checks": checks,
        "link0": {"fuel": r0.get("fuel_burned_kg"), "cd": r0.get("cruise_cd_avg"),
                  "wing_kg": w0, "geom": g0, "notes": r0.get("notes")},
        "link1": {"fuel": r1.get("fuel_burned_kg"), "cd": r1.get("cruise_cd_avg"),
                  "wing_kg": w1, "geom": g1, "area": d1.get("Aircraft.Wing.AREA"),
                  "notes": r1.get("notes")},
    }
    with open(os.path.join(ROOT, "logs", "live_run", f"{combo}_verify.json"), "w") as f:
        json.dump(verdict, f, indent=2, default=str)
    status = "PASS" if verdict["pass"] else "FAIL"
    print(f"[{combo}] {status}  checks={checks}", flush=True)
    print(f"[{combo}] fuel {r0.get('fuel_burned_kg')}->{r1.get('fuel_burned_kg')}  "
          f"wing {w0}->{w1}  geom {g0}/{g1}", flush=True)
    print(json.dumps(verdict, default=str))


if __name__ == "__main__":
    main()
