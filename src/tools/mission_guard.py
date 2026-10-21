"""B188: refuse a run_simulation that repeats a failed one with nothing changed (Jessica, 2026-10-21).

Live (aipexws8 networked graph-routed, link 1): agent_1 called run_simulation 37 times on the same session
with no change in between; every call burned ~3.5 minutes and gave the same zero-fuel result. B81 refuses a
repeated SU2 solve, but a failed mission was never covered. The mission only changes through calls that
set it (set_aircraft_parameters, configure_mission), so a repeat after a failure with none of those in
between is the same mission and the same answer.
"""
from __future__ import annotations

import json
from typing import Any

ERROR_CODE = "NO_CHANGE_SINCE_FAILED_RUN"
_CHANGES_MISSION = frozenset({"set_aircraft_parameters", "configure_mission", "create_session"})
_ZERO_FUEL_FLOOR_KG = 100.0


def _store() -> dict | None:
    from src.tools.data_plane import get_design_state

    state = get_design_state()
    return state.data_store if state is not None else None


def _session(resolved: dict) -> str:
    from src.tools.data_plane import get_design_state

    sid = (resolved or {}).get("session_id")
    if not sid:
        state = get_design_state()
        sid = ((state.sessions or {}).get("aviary") if state is not None else None)
    return str(sid or "default")


def _flown(result: Any) -> tuple[bool, str]:
    data = result
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return False, "no readable result"
    if not isinstance(data, dict):
        return False, "no readable result"
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else data
    try:
        fuel = float(summary.get("fuel_burned_kg"))
    except (TypeError, ValueError):
        fuel = None
    if fuel is not None and fuel > _ZERO_FUEL_FLOOR_KG:
        return True, ""
    code = data.get("error_code") or ("ZERO_FUEL" if fuel is not None else "no fuel result")
    return False, f"{code}, fuel_burned_kg {fuel}" if fuel is not None else str(code)


def check(tool_name: str, resolved: dict) -> dict | None:
    """The refusal payload when this run_simulation repeats a failed one unchanged, else None."""
    store = _store()
    if tool_name != "run_simulation" or store is None:
        return None
    sid = _session(resolved)
    failed = (store.get("_failed_missions") or {}).get(sid)
    if failed is None or failed.get("rev") != (store.get("_mission_rev") or {}).get(sid, 0):
        return None
    failed["refused"] = int(failed.get("refused") or 0) + 1
    return {"success": False, "error_code": ERROR_CODE,
            "error": (f"run_simulation already ran on session {sid} with nothing set since, and that mission "
                      f"did not fly ({failed.get('why')}). The same mission gives the same result."),
            "session_id": sid}


def observe(tool_name: str, resolved: dict, result: Any, status: str) -> None:
    """Track mission changes and failed missions, after a call completed."""
    store = _store()
    if store is None:
        return
    sid = _session(resolved)
    revs = store.setdefault("_mission_rev", {})
    if tool_name in _CHANGES_MISSION and status == "success":
        revs[sid] = int(revs.get(sid, 0)) + 1
        return
    if tool_name != "run_simulation" or status == "refused":
        return
    flown, why = _flown(result)
    failed = store.setdefault("_failed_missions", {})
    if flown:
        failed.pop(sid, None)
    else:
        failed[sid] = {"rev": int(revs.get(sid, 0)), "why": why}
