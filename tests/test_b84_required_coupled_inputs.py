"""B84: a coupled variable is a REQUIRED INPUT of the tool that consumes it.

Before this, every injector was a no-op when its upstream had not run, and every server
had a default for everything: no SU2 -> aviary's own drag polar, no mass-mcp -> aviary's
own FLOPS wing mass and pycycle's own default thrust. The framework only advised, and
agents ignored the advice every time (validate7 link 1: 5 aero warnings, 7 mass hints,
0 estimate_mass, 0 run_cycle). Nothing caught it at the end either.

Stubbed tools only -- no MCP server, no model, no .env.
"""

import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools import coupling_contract as cc
from src.tools import knowledge_base as kbm


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {
        "set_aircraft_parameters": "aviary", "run_simulation": "aviary",
        "run_su2_solver": "su2", "estimate_mass": "mass", "run_cycle": "pycycle"})
    kbm.configure_run(None, 0, 1, None)
    yield


def _store():
    return dp.get_design_state().data_store


def _su2_ran():
    dp._store_aero(0.52, 0.0182, source="run_su2_solver")


def _mass_ran(wing_kg=8100.0, mtom_kg=72000.0):
    state = dp.get_design_state()
    coupling.put_var(state, "mass.wing_kg", wing_kg, source_tool="estimate_mass")
    coupling.put_var(state, "mass.mtom_kg", mtom_kg, source_tool="estimate_mass")
    cc.note_capture("mass", {"wing_kg": wing_kg})


def _cycle_ran():
    dp._capture_cycle_outputs("run_cycle", {
        "success": True, "model_ran": True,
        "outputs": {"perf.TSFC": [0.5821], "perf.Fn": 28000.0},
    })


def _everything_ran():
    _su2_ran()
    _mass_ran()
    _cycle_ran()


def _morph_the_geometry():
    """Bump the B81 geometry epoch the way set_high_level_parameters does."""
    from src.tools.duplicate_guard import _state as guard_state

    gs = guard_state()
    assert gs is not None, "the knowledge base must be configured for the fingerprint to move"
    gs["geometry_epoch"]["tigl-1"] = gs["geometry_epoch"].get("tigl-1", 0) + 1


def _call(tool, **params):
    key = "values" if tool == "run_cycle" else "parameters"
    return cc.check(tool, {"session_id": "s1", key: params})


# ---- the rule: a consumed coupled variable is required -------------------------------------

def test_the_mission_will_not_run_on_aviarys_default_drag_polar():
    refusal = _call("run_simulation")
    assert refusal is not None
    assert refusal["error_code"] == "MISSING_REQUIRED_PARAMETERS"
    assert refusal["success"] is False
    missing = {m["parameter"]: m["state"] for m in refusal["missing"]}
    assert missing["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] == "missing"
    assert "run_su2_solver" in refusal["error"]
    assert "DEFAULT drag polar" in refusal["error"]


def test_the_mission_will_not_run_on_aviarys_internal_flops_wing_mass():
    _su2_ran()
    _cycle_ran()

    refusal = _call("run_simulation")
    assert refusal is not None
    missing = {m["parameter"]: m["state"] for m in refusal["missing"]}
    assert missing["Aircraft.Wing.MASS_SCALER"] == "missing"
    assert "estimate_mass" in refusal["error"]
    assert "MORPHED" in refusal["error"]           # not the baseline fixture (B21/B30)


def test_pycycle_will_not_size_the_engine_on_its_own_default_thrust():
    """The same rule one link upstream: MTOM from mass-mcp sizes Fn_DES."""
    refusal = _call("run_cycle")
    assert refusal is not None
    missing = {m["parameter"]: m["state"] for m in refusal["missing"]}
    assert missing["Fn_DES"] == "missing"
    assert "estimate_mass" in refusal["error"]

    _mass_ran()
    assert _call("run_cycle") is None


def test_a_fully_coupled_link_passes_every_call():
    _everything_ran()
    assert _call("set_aircraft_parameters") is None
    assert _call("run_simulation") is None
    assert _call("run_cycle") is None


def test_only_the_consuming_tools_are_affected():
    for tool in ("generate_volume_mesh", "run_su2_solver", "estimate_mass",
                 "get_design_state", "set_inputs", "create_cycle_model"):
        assert cc.check(tool, {"session_id": "s1"}) is None


# ---- stale counts as missing -----------------------------------------------------------------

def test_a_solve_for_an_earlier_geometry_does_not_count():
    _everything_ran()
    assert _call("run_simulation") is None

    _morph_the_geometry()
    refusal = _call("run_simulation")
    assert refusal is not None
    states = {m["producer"]: m["state"] for m in refusal["missing"]}
    assert states["su2"] == "stale" and states["mass"] == "stale"
    assert "EARLIER geometry" in refusal["error"] and "design has changed" in refusal["error"]


def test_re_running_the_discipline_clears_the_staleness():
    _everything_ran()
    _morph_the_geometry()
    assert _call("run_simulation") is not None

    _everything_ran()          # solved again, for the geometry as it is now
    assert _call("run_simulation") is None


# ---- a value passed by hand counts ----------------------------------------------------------------

def test_a_hand_passed_parameter_satisfies_the_contract():
    """The contract is about what reaches the solver, not about who produced it."""
    _mass_ran()
    _cycle_ran()

    assert _call("run_simulation") is not None          # no aero yet
    assert _call("run_simulation",
                 **{"Mission.Design.LIFT_COEFFICIENT": 0.51,
                    "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR": 0.66}) is None


# ---- propulsion: required as a run, never claimed as coupled (B3) -----------------------------------

def test_run_cycle_outputs_are_captured_at_all():
    """Nothing captured pycycle's results before B84 -- prop.sfc_cruise was never written."""
    _cycle_ran()
    state = dp.get_design_state()
    assert coupling.get_var(state, "prop.sfc_cruise") == pytest.approx(0.5821)
    assert coupling.get_var(state, "prop.fn_lbf") == pytest.approx(28000.0)


def test_a_cycle_that_resolved_nothing_is_not_a_cycle_result():
    dp._capture_cycle_outputs("run_cycle", {
        "success": False, "model_ran": True,
        "outputs": {"SFC": None, "Fn": None}, "missing_outputs": ["SFC", "Fn"],
    })
    assert "prop_cycle_outputs" not in _store()
    assert cc.coupling_status()["propulsion_ran"] is False


def test_propulsion_is_never_reported_as_coupled():
    _everything_ran()
    status = cc.coupling_status()
    assert status["propulsion_ran"] is True
    assert status["propulsion_coupled"] is False        # B3: aviary burns a tabulated deck
    assert status["mission_coupled"] is True            # aero + mass are the real couplings


# ---- the agent is told BEFORE it calls ----------------------------------------------------------------

def test_the_tool_description_states_the_requirement():
    class FakeTool:
        name = "run_simulation"
        description = "Run the mission."
        inputs = {"session_id": {"type": "string"}}

    tool = FakeTool()
    cc.apply_to_tool(tool)
    assert "REQUIRED COUPLED INPUTS" in tool.description
    assert "run_su2_solver" in tool.description and "estimate_mass" in tool.description
    assert "run_cycle" in tool.description

    before = tool.description
    cc.apply_to_tool(tool)
    assert tool.description == before, "the block must be added once, not on every load"


def test_a_tool_that_consumes_nothing_is_not_annotated():
    class FakeTool:
        name = "generate_volume_mesh"
        description = "Mesh it."
        inputs = {"session_id": {"type": "string"}}

    tool = FakeTool()
    cc.apply_to_tool(tool)
    assert "REQUIRED COUPLED INPUTS" not in tool.description


# ---- a refusal is not a tool failure ---------------------------------------------------------------------

def test_a_refusal_is_recorded_as_refused_not_failed():
    """It never reaches a server, so it must not count against B72/eval failure metrics."""
    from src.tools.type_coercion import _kb_record

    refusal = _call("run_simulation")
    entry = _kb_record("run_simulation", {"session_id": "s1"}, refusal, status="refused")
    assert entry["status"] == "refused"
    assert kbm.classify_result(json.dumps(refusal))[0] == "failed", (
        "the payload looks like an error to a generic classifier -- which is exactly why the "
        "wrapper records it explicitly as refused")


def test_refusals_are_counted_per_tool_and_producer():
    _call("run_simulation")
    _call("run_simulation")
    _call("run_cycle")
    counts = kbm.get_kb().metrics.get("missing_data_refusals") or {}
    assert counts["run_simulation"]["su2:missing"] == 2
    assert counts["run_simulation"]["mass:missing"] == 2
    assert counts["run_cycle"]["mass:missing"] == 1


def test_the_contract_can_be_switched_off_for_a_probe(monkeypatch):
    monkeypatch.setenv("AVION_REQUIRE_COUPLED_INPUTS", "0")
    assert _call("run_simulation") is None


# ---- coupling status for result.json -------------------------------------------------------------------------

def test_coupling_status_reports_what_reached_each_solver():
    assert cc.coupling_status()["mission_coupled"] is False

    _everything_ran()
    status = cc.coupling_status()
    assert status["mission_coupled"] is True
    assert status["aero_status"] == "ok" and status["mass_status"] == "ok"
    assert status["engine_sizing_status"] == "ok"

    _morph_the_geometry()
    assert cc.coupling_status()["aero_status"] == "stale"
    assert cc.coupling_status()["mission_coupled"] is False


# ---- the evaluator excludes uncoupled fuel figures ---------------------------------------------------------------

def test_the_evaluator_excludes_uncoupled_runs_from_fuel_ranking():
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "analyze_sweep", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "analyze_sweep.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._row_coupled({"objective": {"mission_coupled": True}}) is True
    assert module._row_coupled({"objective": {"mission_coupled": False}}) is False
    # Rows written before B84 have no flag: absent must not retro-label them uncoupled.
    assert module._row_coupled({"objective": {}, "aero_coupling_status": "injected"}) is True
    assert module._row_coupled({"objective": {}, "aero_coupling_status": "none"}) is False
