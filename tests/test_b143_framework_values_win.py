"""B143: the mission agent's own numbers must not replace the coupled values (2026-10-05).

v2verbatim run, attempt 3, mission_architect: set_aircraft_parameters carried
LIFT_COEFFICIENT 0.5, SUBSONIC_DRAG_COEFF_FACTOR 1.02, MASS_SCALER 1.0, SWEEP 27.33 (the
leading-edge sweep from the prompt table, not the quarter-chord sweep aviary uses) and
MAX_WIDTH 4.15. The injections only filled keys the caller had left out, so every one of
those was flown, against SU2's CL 0.2403 / drag factor 0.902 recorded in design.json.

Stubbed: no MCP server, no model, no .env.
"""

import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling

ATTEMPT3_CALL = {
    "Aircraft.Wing.AREA": 122.3256, "Aircraft.Wing.ASPECT_RATIO": 9.4017, "Aircraft.Wing.SWEEP": 27.33,
    "Aircraft.Wing.TAPER_RATIO": 0.246, "Aircraft.Fuselage.LENGTH": 37.68, "Aircraft.Fuselage.MAX_WIDTH": 4.15,
    "Aircraft.Fuselage.MAX_HEIGHT": 4.15, "Aircraft.Engine.SCALE_FACTOR": 1.0,
    "Mission.Design.LIFT_COEFFICIENT": 0.5, "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR": 1.02,
    "Aircraft.Wing.MASS_SCALER": 1.0,
}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AVION_DESIGN_AUTHORITY", raising=False)
    ds = DesignState()
    # What the run had captured before the mission stage: the measured wing (area, AR only),
    # SU2's cruise CL/CD, and mass-mcp's wing mass.
    ds.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.32557375401029,
                                      "Aircraft.Wing.ASPECT_RATIO": 9.401736532837946}
    coupling.put_var(ds, "aero.cl_cruise", 0.2403152789, source_tool="test")
    coupling.put_var(ds, "aero.cd_cruise", 0.002537317818457623, source_tool="test")
    ds.data_store["mass_wing_kg"] = 8736.345205942507
    monkeypatch.setattr(dp, "_design_state", ds)
    dp._call_ctx.__dict__.clear()
    yield
    dp._call_ctx.__dict__.clear()


def _resolve(params):
    return dp.resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": dict(params)})["parameters"]


def test_su2_and_mass_values_replace_the_callers():
    p = _resolve(ATTEMPT3_CALL)
    assert p["Mission.Design.LIFT_COEFFICIENT"] == pytest.approx(0.2403152789)
    assert p["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] != 1.02
    assert p["Aircraft.Wing.MASS_SCALER"] == pytest.approx(
        coupling.wing_mass_to_aviary_scaler(8736.345205942507))


def test_a_placeholder_string_is_replaced_not_sent():
    """Attempt 1 sent the table's 'from su2' / 'from mass' as values (aviary TYPE_ERROR)."""
    p = _resolve({"Mission.Design.LIFT_COEFFICIENT": "from su2",
                  "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR": "from su2",
                  "Aircraft.Wing.MASS_SCALER": "from mass"})
    assert all(isinstance(p[k], float) for k in p)


def test_unmeasured_geometry_keeps_the_session_value():
    p = _resolve(ATTEMPT3_CALL)
    for key in ("Aircraft.Wing.SWEEP", "Aircraft.Wing.TAPER_RATIO", "Aircraft.Fuselage.LENGTH",
                "Aircraft.Fuselage.MAX_WIDTH", "Aircraft.Fuselage.MAX_HEIGHT"):
        assert key not in p, key
    assert p["Aircraft.Wing.AREA"] == pytest.approx(122.3256)          # measured: from the geometry
    assert p["Aircraft.Engine.SCALE_FACTOR"] == 1.0                     # the mission's own choice


def test_the_caller_is_told_what_was_replaced():
    _resolve(ATTEMPT3_CALL)
    out = json.loads(dp.intercept_response("set_aircraft_parameters",
                                           json.dumps({"success": True, "applied": []})))
    fa = out["framework_applied"]["overridden"]
    assert fa["Mission.Design.LIFT_COEFFICIENT"]["asked"] == 0.5
    assert fa["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"]["asked"] == 1.02
    ga = out["geometry_authority"]["overridden"]
    assert ga["Aircraft.Wing.SWEEP"]["asked"] == 27.33


def test_matching_values_are_not_reported_as_overrides():
    _resolve({"Mission.Design.LIFT_COEFFICIENT": 0.2403152789})
    out = json.loads(dp.intercept_response("set_aircraft_parameters", json.dumps({"success": True})))
    assert "Mission.Design.LIFT_COEFFICIENT" not in (out.get("framework_applied") or {}).get("overridden", {})
