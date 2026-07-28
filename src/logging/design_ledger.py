"""Design ledger — one JSONL row per run capturing the full chain:
    design params the agents APPLIED  ->  discipline outcomes across the 5 MCPs
    ->  objective (fuel / optimality gap / constraints)  ->  cost.

Passive post-run logging: it reads the trace and result, it does NOT feed anything
back to the agents, so it cannot affect the coordination experiment. It gives the
cross-run trace to study how the model's chosen parameters and the resulting values
shift across repeats (and, later, a substrate for a design-recall tool as a
SEPARATE study). Appended to logs/design_ledger.jsonl.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

LEDGER_PATH = Path("logs/design_ledger.jsonl")

# Design params live in the ARGUMENTS of the design-setting tool calls — parse
# those, not the whole trace (prompt text mentions example AR/AREA values too).
_DESIGN_TOOLS = {"set_aircraft_parameters", "set_high_level_parameters"}
_DESIGN_KEYS = [
    ("aspect_ratio", r'ASPECT_RATIO"?\s*[:=]\s*([0-9.]+)'),
    ("area_m2", r'\bAREA"?\s*[:=]\s*([0-9.]+)'),
    ("sweep_deg", r'\bSWEEP"?\s*[:=]\s*([0-9.]+)'),
    ("taper_ratio", r'TAPER_RATIO"?\s*[:=]\s*([0-9.]+)'),
    ("span_m", r'\bspan"?\s*[:=]\s*([0-9.]+)'),
    ("engine_scale", r'SCALE_FACTOR"?\s*[:=]\s*([0-9.]+)'),
]
# Intermediate discipline outputs live in tool-result observations (best-effort).
_OUTPUT_KEYS = [
    ("cl", r'LIFT_COEFFICIENT"?[:=\s]+([0-9.]+)'),
    ("cd", r'DRAG_COEFFICIENT"?[:=\s]+([0-9.]+)'),
    ("sfc", r'\bSFC"?[:=\s]+([0-9.]+)'),
    ("net_thrust_lbf", r'Fn"?[:=\s]+([0-9.]+)'),
]
# PINNED discipline controls the agent is told to hold constant. We record what it
# ACTUALLY passed so post-sweep we can verify every run held them fixed (and flag
# any deviation) WITHOUT re-running — the final 5-repeat sweep can't be redone.
_CONTROL_TOOLS = {"estimate_mass", "configure_mission"}
_CONTROL_KEYS = [
    ("wing_mass_method", r'wing_mass_method"?\s*[:=]\s*"?([a-zA-Z]+)"?'),
    ("material", r'\bmaterial"?\s*[:=]\s*"?([a-zA-Z]+)"?'),
    ("num_passengers", r'num_passengers"?\s*[:=]\s*"?([0-9]+)"?'),
    ("range_nmi", r'range_nmi"?\s*[:=]\s*"?([0-9]+)"?'),
    ("cruise_mach", r'cruise_mach"?\s*[:=]\s*"?([0-9.]+)"?'),
    ("cruise_altitude_ft", r'cruise_altitude_ft"?\s*[:=]\s*"?([0-9]+)"?'),
]
# What each pinned control MUST equal (from the canonical baseline).
_CONTROL_EXPECTED = {
    "wing_mass_method": "flops", "material": "aluminum",
    "num_passengers": "239", "range_nmi": "2500",
    "cruise_mach": "0.78", "cruise_altitude_ft": "33000",
}


def _last_float(pattern: str, text: str) -> float | None:
    m = re.findall(pattern, text)
    return float(m[-1]) if m else None


def _applied_design(traces: dict[str, Any]) -> dict[str, float]:
    """Last-write-wins design params, read only from the design tool calls."""
    out: dict[str, float] = {}
    for body in (traces or {}).values():
        if not isinstance(body, dict):
            continue
        for st in body.get("steps", []) or []:
            for tc in st.get("tool_calls") or []:
                name = tc.get("name") or (tc.get("function") or {}).get("name")
                if name not in _DESIGN_TOOLS:
                    continue
                args = tc.get("arguments") or (tc.get("function") or {}).get("arguments")
                s = args if isinstance(args, str) else json.dumps(args)
                for key, pat in _DESIGN_KEYS:
                    v = _last_float(pat, s)
                    if v is not None:
                        out[key] = v
    return out


def _controls_applied(traces: dict[str, Any]) -> dict[str, Any]:
    """What pinned discipline controls the agent actually passed to estimate_mass,
    plus a per-control deviation flag vs the canonical baseline. Last-write-wins."""
    applied: dict[str, str] = {}
    for body in (traces or {}).values():
        if not isinstance(body, dict):
            continue
        for st in body.get("steps", []) or []:
            for tc in st.get("tool_calls") or []:
                name = tc.get("name") or (tc.get("function") or {}).get("name")
                if name not in _CONTROL_TOOLS:
                    continue
                args = tc.get("arguments") or (tc.get("function") or {}).get("arguments")
                s = args if isinstance(args, str) else json.dumps(args)
                for key, pat in _CONTROL_KEYS:
                    m = re.findall(pat, s)
                    if m:
                        applied[key] = m[-1].lower()
    deviations = {
        k: {"got": applied[k], "expected": exp}
        for k, exp in _CONTROL_EXPECTED.items()
        if k in applied and applied[k] != exp
    }
    return {"applied": applied, "held_constant": not deviations, "deviations": deviations}


def _discipline_outputs(traces: dict[str, Any]) -> dict[str, float]:
    """Best-effort CL/CD/SFC/thrust from tool-result observations."""
    obs = []
    for body in (traces or {}).values():
        if not isinstance(body, dict):
            continue
        for st in body.get("steps", []) or []:
            o = st.get("observations")
            if o:
                obs.append(o if isinstance(o, str) else json.dumps(o))
    blob = "\n".join(obs)
    out: dict[str, float] = {}
    for key, pat in _OUTPUT_KEYS:
        v = _last_float(pat, blob)
        if v is not None:
            out[key] = v
    return out


def build_record(result_dict: dict[str, Any], traces: dict[str, Any]) -> dict[str, Any]:
    ec = result_dict.get("eval_classification") or {}
    tb = result_dict.get("token_breakdown") or {}
    return {
        "combo": result_dict.get("name"),
        "org_structure": result_dict.get("org_structure"),
        "handler": result_dict.get("handler"),
        "repeat": result_dict.get("repeat_index"),
        "seed": result_dict.get("seed"),
        "status": result_dict.get("status"),
        "model_id": tb.get("model_id"),
        # cumulative-chain trajectory: the design this link STARTED from and LEFT.
        # link 0 starts at the shared anchor; link k>0 starts at link k-1's end-state.
        "chain": {
            "link": result_dict.get("chain_link"),
            "start_params": result_dict.get("chain_start_params"),
            "end_params": result_dict.get("chain_end_params"),
        },
        # design the agents actually applied (from tool-call args)
        "design_applied": _applied_design(traces),
        # pinned discipline controls the agent passed + deviation flag (must be constant)
        "controls_applied": _controls_applied(traces),
        # discipline outputs across the MCPs
        "discipline_outputs": _discipline_outputs(traces),
        # final mission outcomes (authoritative, from the eval)
        "outcomes": {
            "fuel_burned_kg": ec.get("fuel_burned_kg"),
            "gtow_kg": ec.get("gtow_kg"),
            "wing_mass_kg": ec.get("wing_mass_kg"),
            "reserve_fuel_kg": ec.get("reserve_fuel_kg"),
            "zero_fuel_weight_kg": ec.get("zero_fuel_weight_kg"),
        },
        # objective / feasibility
        "objective": {
            "optimality_gap_pct": ec.get("optimality_gap_pct"),
            "converged": ec.get("converged"),
            "eval_result": ec.get("result"),
            "constraints_passed": sum(1 for k, v in ec.items() if k.endswith("_pass") and v),
            "constraints_total": sum(1 for k in ec if k.endswith("_pass")),
        },
        # cost / efficiency
        "cost": {
            "cost_usd": result_dict.get("cost_usd"),
            "total_tokens": result_dict.get("total_tokens"),
            "duration_seconds": result_dict.get("duration_seconds"),
            "total_turns": result_dict.get("total_turns"),
        },
    }


def append_record(result_dict: dict[str, Any], traces: dict[str, Any], timestamp: str | None = None) -> None:
    """Append one ledger row. Never raises — logging must not break a run."""
    try:
        rec = build_record(result_dict, traces)
        if timestamp:
            rec["timestamp"] = timestamp
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER_PATH, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [design_ledger] skipped (non-fatal): {e}")


def load_ledger(path: Path = LEDGER_PATH) -> list[dict[str, Any]]:
    """Read all ledger rows (for analysis / a future recall tool)."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
