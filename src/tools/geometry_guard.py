"""No mesh of the baseline when the design has moved on (B95).

WHAT VALIDATE14 SHOWED (7960, 2026-09-17, first networked link)

    2  agent_3  open_cpacs            success
    8  agent_3  generate_volume_mesh  failed (B69-A cell cap -- retryable since B87)

Steps 2 and 3 of the geometry procedure never happened: ``set_high_level_parameters`` (the morph,
which only fires when span, root chord, tip chord and sweep are given together) and
``export_cpacs`` (the file mass-mcp sizes on). So the mesh is of the DLR-F25 baseline, while the
mission holder had already applied ASPECT_RATIO 11.5, AREA 124.6, SWEEP 28.0 and fuselage 35.0 m
to the aviary session. Geometry, aero and mass then describe one aircraft and the mission
describes another.

This is the stage BEFORE B91, and it is why B91 recurs in every run: ``estimate_mass`` falls back
to ``tests/fixtures/D150_simple.xml`` because no morphed export is ever produced -- not because a
peer was refused ``export_cpacs`` (validate10), and not only because geometry had not got there
yet (validate11), but because peers jump straight from opening the CPACS file to meshing it.

Nothing caught it: B31 requires that a mesh EXISTS, not that it is a mesh of the current design;
B93's next-step line reaches self-claims and refusals, and a peer that simply skips ahead hits
neither.

The signal is already in the guard state. ``geometry_epoch[session]`` is set to 0 by
``open_cpacs`` and incremented by every ``GEOMETRY_CHANGE_TOOLS`` call, so epoch 0 means "opened,
never morphed". Combined with the design parameters the data plane captures from
``set_aircraft_parameters``, the framework can say exactly which aircraft is about to be meshed.
"""
from __future__ import annotations

import os

# Meshing the baseline is the error; exporting it is the same error one step later.
_GUARDED = ("generate_volume_mesh", "export_cpacs")
# Shown in the refusal so the message names real numbers rather than "the design".
_INTERESTING = ("Aircraft.Wing.ASPECT_RATIO", "Aircraft.Wing.AREA", "Aircraft.Wing.SWEEP",
                "Aircraft.Wing.SPAN", "Aircraft.Wing.TAPER_RATIO", "Aircraft.Fuselage.LENGTH")


def _applied_design() -> dict:
    """Design parameters the mission holder has applied, from the data plane."""
    try:
        from src.tools.data_plane import get_design_state

        state = get_design_state()
        return dict((getattr(state, "data_store", None) or {}).get("design_params_applied") or {})
    except Exception:      # pragma: no cover - a guard problem must never block work
        return {}


def _never_morphed(session_id) -> bool:
    """True when this tigl session has been opened and no morph has run on it."""
    try:
        from src.tools.duplicate_guard import _state

        gs = _state()
        if not gs:
            return False
        epochs = gs.get("geometry_epoch") or {}
        return str(session_id) in {str(k) for k in epochs} and epochs.get(session_id, 0) == 0
    except Exception:      # pragma: no cover
        return False



def _main_wing_uid() -> str:
    """The real UID of the main wing, from the UIDs captured for this CPACS (B86 3.5)."""
    try:
        from src.tools.procedures import component_uids

        uids = component_uids()
        return next((u for u in uids if u.lower().startswith("wing")), uids[0] if uids else "Wing1")
    except Exception:      # pragma: no cover
        return "Wing1"


def stale_geometry(tool_name: str, resolved: dict) -> dict | None:
    """Refuse to mesh or export a session that is still the baseline while the design has moved."""
    if tool_name not in _GUARDED:
        return None
    if os.environ.get("AVION_REQUIRE_MORPH_BEFORE_MESH", "1") != "1":
        return None
    # Under GEOMETRY authority (the default since 2026-09-18) the geometry IS the design, so meshing
    # it is always correct and this mission-authority rule stands down; `mission_off_geometry` below
    # enforces agreement from the other side. AVION_DESIGN_AUTHORITY=mission restores it.
    from src.tools.data_plane import design_authority

    if design_authority() == "geometry":
        return None
    session_id = (resolved or {}).get("session_id")
    if session_id is None or not _never_morphed(session_id):
        return None
    applied = _applied_design()
    if not applied:
        return None        # nothing has been designed yet: the baseline IS the current design

    shown = [f"{name.split('.')[-1]}={value}" for name, value in applied.items()
             if name in _INTERESTING][:4] or [f"{k.split('.')[-1]}={v}" for k, v in list(applied.items())[:3]]
    # The morph fires on ANY of area / aspect_ratio / sweep (tigl-mcp `_wing_targets`), and those are
    # exactly the values the mission already holds -- so write the call out rather than describing it.
    updates = {key: applied[param] for key, param in
               (("area", "Aircraft.Wing.AREA"), ("aspect_ratio", "Aircraft.Wing.ASPECT_RATIO"),
                ("sweep", "Aircraft.Wing.SWEEP")) if param in applied}
    wing = _main_wing_uid()
    call = (f"set_high_level_parameters(session_id='{session_id}', component_uid='{wing}', "
            f"updates={updates})" if updates else
            f"set_high_level_parameters(session_id='{session_id}', component_uid='{wing}', "
            "updates={'area': <m^2>, 'aspect_ratio': <->, 'sweep': <deg>})")
    return {
        "success": False,
        "error_code": "STALE_GEOMETRY",
        "error": (
            f"session {session_id} is still the BASELINE aircraft -- no morph has run on it -- but the "
            f"design has already moved to {', '.join(shown)}. {tool_name} now would describe the wrong "
            "aircraft, and every result taken from it (CFD, structural mass, the mission's drag polar) "
            "would belong to a design nobody is evaluating.\n"
            "Apply the design to the geometry first. This exact call does it:\n  "
            + call +
            "\nANY of area / aspect_ratio / sweep fires the morph -- you do not need span, root chord and "
            "tip chord, and you do not need get_high_level_parameters first (it returns {} for a real CPACS "
            f"wing). Then export_cpacs so mass-mcp sizes the morphed file, then {tool_name} again."
        ),
        "session_id": session_id,
        "design_parameters": applied,
    }


# -- Geometry authority: the mission may only fly the geometry's wing ------------------------------

_WING_CHECK = ("Aircraft.Wing.AREA", "Aircraft.Wing.ASPECT_RATIO")


def mission_off_geometry(tool_name: str, resolved: dict) -> dict | None:
    """Refuse `run_simulation` while the mission's wing differs from the geometry's.

    horizon10 sequential measured 13 links: only 3 flew a mission whose wing matched the geometry
    that was designed -- 5 flew a different area (B103), 2 flew HALF the wing (B106). The two best
    fuel figures of the run were both among the consistent three. set_aircraft_parameters now takes
    the wing from the geometry automatically, so this refusal only fires when the geometry changed
    AFTER the mission's parameters were last set, or the mission never set them this link.
    """
    import os

    if tool_name != "run_simulation":
        return None
    if os.environ.get("AVION_REQUIRE_GEOMETRY_MATCH", "1") != "1":
        return None
    try:
        from src.tools.data_plane import design_authority, get_design_state

        if design_authority() != "geometry":
            return None
        state = get_design_state()
        store = (getattr(state, "data_store", None) or {}) if state else {}
        wing = store.get("geometry_wing") or {}
        applied = store.get("design_params_applied") or {}
        session = ((getattr(state, "sessions", None) or {}).get("aviary")) if state else None
    except Exception:      # pragma: no cover - a guard problem must never block work
        return None
    if not wing:
        return None                      # geometry has not been read or reshaped: nothing to compare
    mismatches = []
    for key in _WING_CHECK:
        target = wing.get(key)
        if target is None:
            continue
        have = applied.get(key)
        try:
            if have is None or abs(float(have) / float(target) - 1.0) > 0.02:
                mismatches.append((key, have, float(target)))
        except (TypeError, ValueError, ZeroDivisionError):
            mismatches.append((key, have, float(target)))
    if not mismatches:
        return None
    detail = "; ".join(f"{k.split('.')[-1]}: mission {'not set this link' if h is None else h}, "
                       f"geometry {t:.4g}" for k, h, t in mismatches)
    return {
        "success": False,
        "error_code": "GEOMETRY_NOT_APPLIED",
        "error": (
            "The mission would fly a different wing from the geometry (" + detail + "). Geometry is "
            "authoritative for the wing, and CFD and structural mass were computed on the geometry's. "
            f"Call set_aircraft_parameters(session_id='{session or '<aviary session>'}', parameters={{...}}) "
            "-- the wing's AREA, ASPECT_RATIO and SWEEP are filled in from the geometry automatically, "
            "you only need to pass anything else you want to change -- then run_simulation again."
        ),
        "geometry_wing": {k: v for k, v in wing.items() if not k.startswith("_")},
        "mission_applied": {k: applied.get(k) for k in _WING_CHECK},
    }
