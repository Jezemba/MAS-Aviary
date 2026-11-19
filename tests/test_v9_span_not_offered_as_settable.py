"""V9 (validation 2): the read-only Aircraft.Wing.SPAN refusal still happened (15 calls, 11 refusals in the KB).

Where it came from (every step log of the 8 val2 combinations, each set_aircraft_parameters call that named SPAN,
its own model input searched): all 15 had design.json's ``mission_applied`` with Aircraft.Wing.SPAN in it (the
framework puts design.json in the context, 2026-10-08); none had a set reply's derived_read_only (B372 already moved
SPAN out of ``applied``) and none a get_results aircraft_params block. The refused call's parameters ARE that block:
fixture tests/fixtures/v9_s_sp_records.json (S_SP it1, ws5 tool log: the successful set whose reply lists SPAN as
derived, and the mission_architect's refused call). No network, no API, no MCP servers.
"""
import json
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import design_json

REC = json.loads((Path(__file__).parent / "fixtures" / "v9_s_sp_records.json").read_text())
SPAN = "Aircraft.Wing.SPAN"


@pytest.fixture
def state(monkeypatch):
    s = DesignState()
    s.sessions.update({"aviary": REC["set_ok"]["args"]["session_id"]})
    dp.init_data_plane(s, {"set_aircraft_parameters": "aviary"})
    monkeypatch.setattr(dp, "_registered_tools", {})
    dp._call_ctx.__dict__.clear()
    yield s
    dp._call_ctx.__dict__.clear()
    dp.init_data_plane(DesignState(), {})


def _apply_recorded_set():
    dp._call_ctx.aviary_set_sid = REC["set_ok"]["args"]["session_id"]
    dp._call_ctx.aviary_sid = REC["set_ok"]["args"]["session_id"]
    return json.loads(dp.intercept_response("set_aircraft_parameters", json.dumps(REC["set_ok"]["reply"])))


def test_v9_the_refused_call_was_design_json_mission_applied():
    refused = REC["set_refused"]["args"]["parameters"]
    assert REC["set_refused"]["reply"]["error_code"] == "READ_ONLY_PARAMETER" and SPAN in refused
    applied = {a["name"]: a["new_value"] for a in REC["set_ok"]["reply"]["applied"]}
    assert refused == applied                    # the block copied verbatim, SPAN 33.9127 included


def test_v9_design_json_lists_span_apart_as_read_only(state):
    reply = _apply_recorded_set()
    assert SPAN not in {a["name"] for a in reply["applied"]}            # B372: the reply already separated it
    design = design_json.build_design(state.data_store, state.sessions)
    refused = REC["set_refused"]["args"]["parameters"]
    assert design["mission_applied"] == {k: v for k, v in refused.items() if k != SPAN}
    assert design["mission_derived_read_only"]["values"] == {SPAN: 33.9127}
    assert "Do not send" in design["mission_derived_read_only"]["note"]


def test_v9_copying_mission_applied_is_now_a_valid_set(state):
    _apply_recorded_set()
    design = design_json.build_design(state.data_store, state.sessions)
    read_only = design_json.AVIARY_READ_ONLY       # aviary-mcp 36b3a53 READONLY_PARAMETER_NAMES
    assert not (set(design["mission_applied"]) & read_only)


def test_v9_span_is_read_only_even_before_aviary_marked_it(state):
    state.data_store["design_params_applied"] = {"Aircraft.Wing.AREA": 122.8287, SPAN: 33.9127,
                                                 "Aircraft.Engine.SCALED_SLS_THRUST": 34000.0}
    settable, derived = design_json.applied_split(state.data_store)
    assert settable == {"Aircraft.Wing.AREA": 122.8287} and set(derived) == {SPAN, "Aircraft.Engine.SCALED_SLS_THRUST"}
    assert SPAN not in dp._reapply_source("no-such-session")


def test_v9_the_stale_geometry_refusal_lists_settable_parameters_only(state, monkeypatch):
    """Mission-authority mode only (not the study's default), but its refusal also listed every applied key."""
    from src.tools import knowledge_base as kbm
    from src.tools.duplicate_guard import _state
    from src.tools.geometry_guard import stale_geometry

    monkeypatch.setenv("AVION_DESIGN_AUTHORITY", "mission")
    kbm.configure_run(None, 0, 1, None)
    _state()["geometry_epoch"]["tigl-1"] = 0
    state.data_store["design_params_applied"] = {"Aircraft.Wing.ASPECT_RATIO": 11.5, "Aircraft.Wing.AREA": 124.6, SPAN: 37.85}
    refusal = stale_geometry("generate_volume_mesh", {"session_id": "tigl-1"})
    assert refusal["error_code"] == "STALE_GEOMETRY"
    assert refusal["design_parameters"] == {"Aircraft.Wing.ASPECT_RATIO": 11.5, "Aircraft.Wing.AREA": 124.6}
