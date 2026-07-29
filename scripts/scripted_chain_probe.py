"""Deterministic (NO-LLM, NO-API) probe of the 5-link MDO chain.

Replays the real MCP tool sequence WITHOUT any Claude API call, so we can see —
cheaply and in real time — what the pipeline itself does, isolated from the
stochastic LLM agent. Key questions it answers:

  1. REPRODUCIBILITY: run aviary TWICE per link with the byte-identical design +
     mission. If fuel_a != fuel_b, aviary/SLSQP itself is non-deterministic. If
     they match but the real (LLM) run varied, the variance came from the agent.
  2. SLSQP WRITE-BACK: does get_results' aircraft_params (what we carry forward)
     equal what we SET, or did SLSQP move AREA/SCALE_FACTOR internally?
  3. COUPLING (SKIP_SU2=0): run geometry+SU2+pycycle too and log CL/CD/SFC, to
     confirm those outputs do NOT change the aviary fuel.

Streams one JSON record per sub-step to logs/probe_chain.jsonl (flushed), so you
can `tail -f` it live. Set SKIP_SU2=0 to include the slow SU2 leg.

Run:  SKIP_SU2=1 PYTHONPATH=. .venv/bin/python scripts/scripted_chain_probe.py
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from src.config.loader import load_config  # noqa: E402
from scripts.stat_batch_runner import _load_mcp_tools, generate_random_params, PARAM_RANGES  # noqa: E402

OUT = Path("logs/probe_chain.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("")
T0 = time.time()
N_LINKS = int(os.environ.get("N_LINKS", "5"))
SKIP_SU2 = os.environ.get("SKIP_SU2", "1") == "1"


def log(**rec):
    rec["t"] = round(time.time() - T0, 1)
    with open(OUT, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
        f.flush()
    print(f"[{rec['t']:7.1f}s] {rec.get('link',''):>3} {rec.get('step',''):<16} {rec.get('summary','')}", flush=True)


def P(r):
    return r if isinstance(r, dict) else json.loads(r)


def run_aviary(tm, design, mission):
    """One full aviary evaluation of a FIXED design. Returns key outputs +
    the end-state aircraft_params (what the chain carries forward)."""
    sid = P(tm["create_session"].forward(initial_parameters={}))["session_id"]
    P(tm["configure_mission"].forward(session_id=sid, **mission))
    sap = P(tm["set_aircraft_parameters"].forward(session_id=sid, parameters=design))
    rs = P(tm["run_simulation"].forward(session_id=sid, timeout_seconds=300))
    gr = P(tm["get_results"].forward(session_id=sid))
    ap = (gr.get("design_parameters") or {}).get("aircraft_params") or {}
    end = {k: ap.get(k) for k in PARAM_RANGES}
    return {
        "session": sid[:8],
        "set_valid": sap.get("valid"),
        "converged": rs.get("converged"),
        "fuel": gr.get("fuel_burned_kg"),
        "gtow": gr.get("gtow_kg"),
        "wing": gr.get("wing_mass_kg"),
        "cl": gr.get("cruise_cl_avg"),
        "cd": gr.get("cruise_cd_avg"),
        "end_design": end,
    }


def main():
    log(step="init", summary=f"loading MCP tools (SKIP_SU2={SKIP_SU2}, N_LINKS={N_LINKS})")
    config = load_config("config/mdo_f25_run_claude.yaml")
    tm = _load_mcp_tools(config)
    log(step="init", summary=f"loaded {len(tm)} tools")

    mission = dict(range_nmi=2500, num_passengers=239, cruise_mach=0.78,
                   cruise_altitude_ft=33000, optimizer_max_iter=200)

    anc = generate_random_params(42)
    design = {k: anc[k] for k in PARAM_RANGES}
    log(step="anchor", summary=f"seed-42 anchor: " + " ".join(f"{k.split('.')[-1]}={v}" for k, v in design.items()))

    for link in range(N_LINKS):
        tag = f"L{link}"
        log(link=tag, step="link_start",
            summary=f"AR={design['Aircraft.Wing.ASPECT_RATIO']} AREA={design['Aircraft.Wing.AREA']} "
                    f"SWEEP={design['Aircraft.Wing.SWEEP']} SCALE={design['Aircraft.Engine.SCALE_FACTOR']}",
            design=dict(design))

        # --- REPRODUCIBILITY: run aviary twice on the identical design ---
        a = run_aviary(tm, design, mission)
        log(link=tag, step="aviary_run_a",
            summary=f"fuel={a['fuel']} gtow={a['gtow']} cl={a['cl']} conv={a['converged']}", **a)
        b = run_aviary(tm, design, mission)
        repro = (a["fuel"] == b["fuel"])
        log(link=tag, step="aviary_run_b",
            summary=f"fuel={b['fuel']}  REPRODUCIBLE={repro} (a={a['fuel']} b={b['fuel']})",
            reproducible=repro, **b)

        # --- SLSQP write-back: did get_results move the design we set? ---
        moved = {k: (design[k], a["end_design"].get(k)) for k in PARAM_RANGES
                 if a["end_design"].get(k) is not None and abs(float(a["end_design"][k]) - float(design[k])) > 1e-9}
        log(link=tag, step="slsqp_writeback",
            summary=("SLSQP moved: " + ", ".join(f"{k.split('.')[-1]} {v[0]}->{v[1]}" for k, v in moved.items())) if moved else "get_results design == set design (no write-back)",
            moved=moved)

        # carry forward exactly as the real chain does
        if a["end_design"] and all(v is not None for v in a["end_design"].values()):
            design = {**design, **a["end_design"]}

    log(step="DONE", summary="probe complete — see logs/probe_chain.jsonl")


if __name__ == "__main__":
    main()
