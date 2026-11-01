"""One-at-a-time sensitivity of fuel burn and GTOW to every design variable the agents can set (Avion B3).

Jessica 2026-11-01: "hold everything constant and vary one and see if the fuel and GTOW changes" -- for every
variable MAS can set, so an inert variable is found before the 8 combinations are re-run (B231; the engine
variables were inert in the 20-link study: identical 19,019.6 kg for every engine step).

No LLM. Each evaluation is the coupled pipeline the agents get (scripts/optimizer_baselines/evaluator.py, shared
with the optimizer baselines): morph -> mesh -> SU2 -> mass -> pycycle sized from mass and drag -> engine deck ->
aviary. The baseline is evaluated twice (determinism / noise floor); then each variable is stepped alone.

    PYTHONPATH=. ../.venv/bin/python scripts/oat_sensitivity.py --out <dir> --server-base 9600
Resumable: evaluations already in <dir>/oat.jsonl are not repeated.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "optimizer_baselines"))

from design_space import ENGINE, GEOMETRY, TAILS_FIXED, Variable  # noqa: E402

# Settable but fixed in the prompt table ("Set (fixed)"): the component efficiencies.
EFFICIENCIES = (
    Variable("fan_efficiency", "pycycle", "fan.eff", "", 0.8948, 0.8, 0.95, "-"),
    Variable("lpc_efficiency", "pycycle", "lpc.eff", "", 0.9243, 0.8, 0.95, "-"),
    Variable("hpc_efficiency", "pycycle", "hpc.eff", "", 0.8707, 0.8, 0.95, "-"),
    Variable("hpt_efficiency", "pycycle", "hpt.eff", "", 0.8888, 0.8, 0.95, "-"),
    Variable("lpt_efficiency", "pycycle", "lpt.eff", "", 0.8996, 0.8, 0.95, "-"),
)


def ratings(t4_takeoff: float, t4_climb: float) -> tuple[Variable, ...]:
    """The take-off / climb ratings (pycycle-mcp fix/b3-engine-deck), at the defaults the study will use."""
    return (Variable("t4_takeoff", "pycycle", "T4_TAKEOFF", "", t4_takeoff, 2500.0, 3800.0, "degR"),
            Variable("t4_climb", "pycycle", "T4_CLIMB", "", t4_climb, 2500.0, 3800.0, "degR"))


# The step for each variable: small enough to stay in the agents' range, large enough to clear solver noise.
# Wing sweep steps down: +3 deg would pass the agents' maximum quarter-chord sweep (23.62 deg, design_space).
STEP = {
    "wing_area": ("rel", 0.05), "wing_aspect_ratio": ("abs", 0.63), "wing_sweep": ("abs", -3.0),
    "fuselage_length": ("rel", 0.02), "fuselage_diameter": ("abs", 0.2),
    "htail_area": ("rel", 0.10), "htail_aspect_ratio": ("rel", 0.10), "htail_sweep": ("abs", 3.0),
    "vtail_area": ("rel", 0.10), "vtail_aspect_ratio": ("rel", 0.10), "vtail_sweep": ("abs", 3.0),
    "bypass_ratio": ("abs", 0.895), "turbine_inlet_temperature": ("abs", 100.0),
    "fan_pressure_ratio": ("abs", 0.065), "lpc_pressure_ratio": ("abs", 0.165), "hpc_pressure_ratio": ("abs", 1.131),
    "fan_efficiency": ("abs", 0.01), "lpc_efficiency": ("abs", 0.01), "hpc_efficiency": ("abs", 0.01),
    "hpt_efficiency": ("abs", 0.01), "lpt_efficiency": ("abs", 0.01),
    "t4_takeoff": ("abs", 150.0), "t4_climb": ("abs", 100.0),
}


def all_variables(t4_takeoff: float, t4_climb: float) -> tuple[Variable, ...]:
    return GEOMETRY + TAILS_FIXED + ENGINE + EFFICIENCIES + ratings(t4_takeoff, t4_climb)


def stepped(v: Variable) -> float:
    kind, size = STEP[v.name]
    return v.default * (1.0 + size) if kind == "rel" else v.default + size


def plan(vs: tuple[Variable, ...], repeats: int = 2) -> list[tuple[str, list[float]]]:
    """(label, x): the baseline `repeats` times, then each variable moved alone."""
    base = [v.default for v in vs]
    runs = [(f"baseline_{i + 1}", list(base)) for i in range(repeats)]
    for i, v in enumerate(vs):
        x = list(base)
        x[i] = stepped(v)
        runs.append((v.name, x))
    return runs


def engine_record() -> dict:
    """What the data plane recorded about the engine for the evaluation that just finished."""
    from src.tools import coupling_contract
    from src.tools.data_plane import get_design_state

    store = get_design_state().data_store
    deck = store.get("engine_deck") or {}
    status = coupling_contract.coupling_status()
    return {"deck_key": deck.get("key"), "deck_cached": deck.get("cached"), "design_tsfc": deck.get("design_tsfc"),
            "envelope_holes": len(deck.get("envelope_holes") or []), "deck_error": store.get("engine_deck_error"),
            "engine_scale": store.get("engine_injected_scale_factor"), "scale_basis": store.get("engine_scale_basis"),
            "mission_engine": status.get("mission_engine"), "propulsion_coupled": status.get("propulsion_coupled")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--config", default="config/mdo_f25_run_qwen32b_2gpu.yaml",
                    help="MCP server list only (no LLM is loaded); never the paid-API config")
    ap.add_argument("--server-base", type=int, required=True, help="own server set: tigl +0, su2 +1, pycycle +2, aviary +3, mass +4")
    ap.add_argument("--t4-takeoff", type=float, required=True)
    ap.add_argument("--t4-climb", type=float, required=True)
    ap.add_argument("--only", nargs="*", help="labels to run (default: all)")
    args = ap.parse_args()
    if "claude" in args.config:
        ap.error("the paid-API config is never used")

    from evaluator import CoupledEvaluator

    ports = {"tigl": 0, "su2": 1, "pycycle": 2, "aviary": 3, "mass": 4}
    args.out.mkdir(parents=True, exist_ok=True)
    log = args.out / "oat.jsonl"
    done = {json.loads(line)["label"] for line in log.open()} if log.exists() else set()
    vs = all_variables(args.t4_takeoff, args.t4_climb)
    ev = CoupledEvaluator(args.config, args.out / "evaluator", coarse_mesh=False,
                          server_ports={k: args.server_base + s for k, s in ports.items()})
    for label, x in plan(vs):
        if label in done or (args.only and label not in args.only):
            continue
        rec = ev.evaluate(x, vs)
        rec.update(label=label, engine=engine_record(),
                   changed={v.name: [v.default, xi] for v, xi in zip(vs, x) if xi != v.default})
        with log.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        print(f"{label}: fuel {rec['fuel_burned_kg']} gtow {rec['gtow_kg']} converged {rec.get('converged')} "
              f"deck {rec['engine']['deck_key']} coupled {rec['engine']['propulsion_coupled']} "
              f"failure {rec['failure']} ({rec['seconds']} s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
