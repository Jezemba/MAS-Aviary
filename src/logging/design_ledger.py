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


# LEGACY heuristic threshold. Kept ONLY as a fallback for runs where the data
# plane state is unavailable (e.g. re-scoring an old trace offline).
#
# It is no longer valid as a primary signal: it was calibrated when the CD->drag
# transform was the old fixed "+0.005" fudge, under which a coupled run flew
# cruise_cd_avg ~0.014 vs aviary's ~0.0208 default. The physics-based drag
# build-up (Option A', 2026-07-28) adds a real Schlichting skin-friction term, so
# a COUPLED run now flies ~0.020-0.030 — at or ABOVE this threshold. Every
# coupled run since that change was therefore mislabelled "uncoupled". See
# .claude/BUGS.md B1.
_AERO_COUPLED_CD_MAX = 0.018


def _aero_coupled(traces: dict[str, Any]) -> dict[str, Any]:
    """Whether SU2 aero actually reached aviary this run.

    AUTHORITATIVE source: the data plane records the outcome of the Phase-H
    injection directly — ``aero_coupling_status`` is "injected" (with the exact
    CL/CD/drag-factor used) or "MISSING_no_su2_aero". That is ground truth: it is
    written by the injector itself at the tool boundary, not inferred.

    This function used to IGNORE that and re-derive coupling from the flown
    ``cruise_cd_avg`` with a threshold calibrated for a superseded drag
    transform, which produced a systematic FALSE NEGATIVE: e.g. the
    sequential_staged_pipeline link 0 was genuinely coupled (the mission stage's
    own output records the framework-injected LIFT_COEFFICIENT=0.0922 and
    SUBSONIC_DRAG_COEFF_FACTOR=0.918) yet the ledger recorded
    "uncoupled_default_drag" because it flew cruise_cd_avg 0.0299 > 0.018. This
    is the measurement error behind B1's "7 of 8 combos fail to couple".

    The heuristic is retained only as a flagged fallback when no data-plane
    state is available.
    """
    obs = []
    for body in (traces or {}).values():
        if not isinstance(body, dict):
            continue
        for st in body.get("steps", []) or []:
            o = st.get("observations")
            if o:
                obs.append(o if isinstance(o, str) else json.dumps(o))
    blob = "\n".join(obs)
    # match cruise_cd_avg even through CSV/JSON escaping of the quote before the colon
    cds = re.findall(r'cruise_cd_avg\\?"?\s*[:=]\s*([0-9.]+)', blob)
    cd = float(cds[-1]) if cds else None
    # How many times the model hit the UNCOUPLED_MISSION advisory before recovering —
    # a coordination signal (0 = coupled aero before mission on the first try).
    retries = blob.count("UNCOUPLED_MISSION")

    record = {"cruise_cd_avg": cd, "coupling_retries": retries}

    # --- Authoritative path: ask the data plane what actually happened. ---
    # append_record() runs immediately after the link, before the next link's
    # reset_design_state(), so this state still belongs to THIS link.
    try:
        from src.tools.data_plane import get_design_state

        ds = get_design_state()
        store = getattr(ds, "data_store", None) if ds is not None else None
        status = (store or {}).get("aero_coupling_status")
        if status is not None:
            coupled = status == "injected"
            record.update({
                "coupled": coupled,
                "status": "coupled" if coupled else "uncoupled_default_drag",
                "detection": "data_plane",
                "aero_coupling_status": status,
                "injected_cl": (store or {}).get("aero_injected_cl"),
                "injected_cd": (store or {}).get("aero_injected_cd"),
                "injected_drag_factor": (store or {}).get("aero_injected_drag_factor"),
                # typed_registry | legacy_fallback — which capture path supplied the aero.
                "aero_coupling_source": (store or {}).get("aero_coupling_source"),
            })
            return record
    except Exception:  # logging must never break a run
        pass

    # --- Fallback: the superseded cruise_cd_avg heuristic, clearly flagged. ---
    coupled = cd is not None and cd < _AERO_COUPLED_CD_MAX
    record.update({
        "coupled": coupled,
        "status": "coupled" if coupled else ("uncoupled_default_drag" if cd is not None else "unknown"),
        "detection": "cd_threshold_fallback",
        "detection_warning": (
            "No data-plane state available; fell back to the cruise_cd_avg "
            "threshold, which is mis-calibrated for the physics-based drag "
            "build-up and under-reports coupling. Treat as unreliable."
        ),
    })
    return record


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
        # was SU2 aero coupled into aviary? (fully-coupled run vs default-drag fallback)
        "aero_coupling": _aero_coupled(traces),
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
