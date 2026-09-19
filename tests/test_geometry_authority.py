"""Geometry is authoritative for the wing (Jessica, 2026-09-18).

The wing's area, aspect ratio and sweep are properties of the SHAPE; CFD and structural mass are
computed from the shape. So the CPACS geometry is the design and the mission consumes its numbers.

Why it had to be enforced -- horizon10 sequential, 13 links measured: only 3 flew a mission whose wing
matched the geometry that was built. 5 flew a different area (B103: iterative_feedback's mission held
AREA 130.1 for ten links while geometry ranged 117-200 m^2). 2 flew HALF the wing (B106: staged_pipeline's
mission copied get_wing_summary's semi-span 100.006 for a 200.01 m^2 wing). 3 never reshaped at all
(B105). The two best fuel figures of the run were both among the consistent three.

Stubbed: no MCP server, no model, no .env.
"""

import json
import os

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import geometry_guard as gg

BASELINE_SUMMARY = {"span": 33.91272350701785, "half_span": 16.956361753508926,
                    "reference_area": 61.39076274891849, "aspect_ratio": 18.73364597809467,
                    "mac_length": 4.192320937872386}
MORPHED_SUMMARY = {"span": 45.347003504972406, "reference_area": 100.0059979035337,
                   "aspect_ratio": 20.562273963443136, "mac_length": 5.104453955260532}
MORPH_RESULT = {"component_uid": "Wing1", "new_parameters": {}, "warnings": [],
                "geometry_morph": {"rebuilt": True,
                                   "before": {"span": 33.91, "reference_area": 122.78152549783698,
                                              "aspect_ratio": 9.366822989047336},
                                   "after": {"span": 45.347003504972406, "reference_area": 200.0119958070674,
                                             "aspect_ratio": 10.281136981721568, "mac_length": 5.104}}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AVION_DESIGN_AUTHORITY", raising=False)
    monkeypatch.setattr(dp, "_design_state", DesignState())
    dp._call_ctx.__dict__.clear()
    yield
    dp._call_ctx.__dict__.clear()


def _summary(data, uid="Wing1"):
    dp.resolve_request("get_wing_summary", {"session_id": "t", "wing_uid": uid})
    dp.intercept_response("get_wing_summary", json.dumps(data))


def _morph(result=MORPH_RESULT, updates=None, uid="Wing1"):
    dp.resolve_request("set_high_level_parameters",
                       {"session_id": "t", "component_uid": uid,
                        "updates": updates or {"span": 45.0, "root_chord": 7.0, "tip_chord": 1.9, "sweep": 25.0}})
    dp.intercept_response("set_high_level_parameters", json.dumps(result))


def _wing():
    return dp._design_state.data_store.get("geometry_wing") or {}


# ---- the geometry's wing is captured in FULL-wing terms -------------------------------------------

def test_the_baseline_summary_is_converted_from_semi_span():
    """B106: 61.39 is the half-wing figure and 18.73 the aspect ratio computed from it."""
    _summary(BASELINE_SUMMARY)
    assert _wing()["Aircraft.Wing.AREA"] == pytest.approx(122.7815, abs=1e-3)
    assert _wing()["Aircraft.Wing.ASPECT_RATIO"] == pytest.approx(9.3668, abs=1e-3)


def test_the_morphed_summary_gives_the_morphs_own_full_area():
    _summary(MORPHED_SUMMARY)
    assert _wing()["Aircraft.Wing.AREA"] == pytest.approx(200.012, abs=1e-2)
    assert _wing()["Aircraft.Wing.ASPECT_RATIO"] == pytest.approx(10.2811, abs=1e-3)


def test_a_rebuilt_morph_is_captured_with_its_sweep():
    _morph()
    assert _wing()["Aircraft.Wing.AREA"] == pytest.approx(200.012, abs=1e-2)
    assert _wing()["Aircraft.Wing.ASPECT_RATIO"] == pytest.approx(10.2811, abs=1e-3)
    assert _wing()["Aircraft.Wing.SWEEP"] == 25.0


def test_an_ignored_sweep_key_does_not_become_the_missions_sweep():
    """B105: the morph reads `sweep`, not `sweep_deg`."""
    _morph(updates={"area": 200.0, "sweep_deg": 20.0})
    assert "Aircraft.Wing.SWEEP" not in _wing()


def test_a_morph_that_did_not_rebuild_changes_nothing():
    _summary(BASELINE_SUMMARY)
    _morph(result={"component_uid": "Wing1", "new_parameters": {"sweep_deg": 20.0}, "warnings": []})
    assert _wing()["Aircraft.Wing.AREA"] == pytest.approx(122.7815, abs=1e-3)


def test_a_tail_surface_is_not_the_wing():
    _summary({"span": 12.4, "reference_area": 15.0}, uid="Wing2H")
    assert _wing() == {}


# ---- the mission receives the geometry's wing ----------------------------------------------------

def _set_params(params):
    return dp.resolve_request("set_aircraft_parameters", {"session_id": "a", "parameters": dict(params)})


def test_the_mission_gets_the_geometrys_wing_whatever_it_asked_for():
    """The staged_pipeline case: the agent passed 100.006 (half the wing); the mission gets 200.01."""
    _morph()
    resolved = _set_params({"Aircraft.Wing.AREA": 100.006, "Aircraft.Wing.ASPECT_RATIO": 11.0,
                            "Aircraft.Engine.SCALE_FACTOR": 1.3})
    p = resolved["parameters"]
    assert p["Aircraft.Wing.AREA"] == pytest.approx(200.012, abs=1e-2)
    assert p["Aircraft.Wing.ASPECT_RATIO"] == pytest.approx(10.2811, abs=1e-3)
    assert p["Aircraft.Wing.SWEEP"] == 25.0
    assert p["Aircraft.Engine.SCALE_FACTOR"] == 1.3          # non-geometric: the mission's own


def test_the_mission_is_told_what_was_replaced():
    _morph()
    _set_params({"Aircraft.Wing.AREA": 100.006})
    out = json.loads(dp.intercept_response("set_aircraft_parameters",
                                           json.dumps({"success": True, "applied": []})))
    ga = out["geometry_authority"]
    assert ga["overridden"]["Aircraft.Wing.AREA"]["asked"] == 100.006
    assert "set_high_level_parameters" in ga["note"]


def test_nothing_is_overridden_before_the_geometry_has_been_read():
    resolved = _set_params({"Aircraft.Wing.AREA": 130.1})
    assert resolved["parameters"]["Aircraft.Wing.AREA"] == 130.1


def test_mission_authority_mode_leaves_the_mission_alone(monkeypatch):
    monkeypatch.setenv("AVION_DESIGN_AUTHORITY", "mission")
    _morph()
    resolved = _set_params({"Aircraft.Wing.AREA": 130.1})
    assert resolved["parameters"]["Aircraft.Wing.AREA"] == 130.1


# ---- and may only fly it --------------------------------------------------------------------------

def _applied(area, ar):
    dp._design_state.data_store["design_params_applied"] = {
        "Aircraft.Wing.AREA": area, "Aircraft.Wing.ASPECT_RATIO": ar}


def test_flying_a_different_wing_is_refused():
    """iterative_feedback: mission AREA 130.1 against a 200.01 m^2 geometry."""
    _morph()
    _applied(130.1, 11.0)
    refusal = gg.mission_off_geometry("run_simulation", {"session_id": "a"})
    assert refusal["error_code"] == "GEOMETRY_NOT_APPLIED"
    assert "set_aircraft_parameters" in refusal["error"]


def test_flying_half_the_wing_is_refused():
    _morph()
    _applied(100.006, 10.2811)
    assert gg.mission_off_geometry("run_simulation", {}) is not None


def test_flying_the_geometrys_wing_proceeds():
    _morph()
    _applied(200.012, 10.2811)
    assert gg.mission_off_geometry("run_simulation", {}) is None


def test_a_mission_that_never_set_its_wing_this_link_is_refused():
    _morph()
    assert gg.mission_off_geometry("run_simulation", {}) is not None


def test_no_geometry_read_means_nothing_to_compare():
    _applied(130.1, 11.0)
    assert gg.mission_off_geometry("run_simulation", {}) is None


def test_the_match_rule_stands_down_under_mission_authority(monkeypatch):
    monkeypatch.setenv("AVION_DESIGN_AUTHORITY", "mission")
    _morph()
    _applied(130.1, 11.0)
    assert gg.mission_off_geometry("run_simulation", {}) is None


def test_stale_geometry_stands_down_under_geometry_authority():
    dp._design_state.data_store["design_params_applied"] = {"Aircraft.Wing.AREA": 124.6}
    assert gg.stale_geometry("generate_volume_mesh", {"session_id": "t"}) is None


# ---- mass and SU2 size the geometry that is open, not the baseline fixture -----------------------

def test_an_explicit_baseline_path_is_pointed_at_the_open_geometry(tmp_path):
    open_geo = tmp_path / "geometry_end.xml"
    open_geo.write_text("<cpacs/>")
    dp._design_state.cpacs_file_path = str(open_geo)
    fixture = "/home/jezemba/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
    if not os.path.isfile(fixture):
        pytest.skip("fixture not present on this machine")
    for tool in ("create_su2_session", "configure_from_cpacs"):
        resolved = dp.resolve_request(tool, {"cpacs_file_path": fixture})
        assert resolved["cpacs_file_path"] == str(open_geo), tool
    # estimate_mass is pointed at the open geometry too, then G1 hands it a per-run WORKING COPY of
    # that file so write-backs never touch the source -- so compare the file it copied, not the path.
    resolved = dp.resolve_request("estimate_mass", {"cpacs_file_path": fixture})
    assert os.path.basename(resolved["cpacs_file_path"]) == "geometry_end.xml"
    assert "tests/fixtures" not in resolved["cpacs_file_path"]


# ---- the chain carries the geometry forward -------------------------------------------------------

def test_the_next_link_opens_the_previous_links_geometry(tmp_path):
    from scripts.stat_batch_runner import _D150_FIXTURE, base_task_for, carry_geometry_forward

    exported = tmp_path / "morphed.xml"
    exported.write_text("<cpacs>morphed</cpacs>")
    link_dir = tmp_path / "repeat_000" / "combo"
    carried = carry_geometry_forward({"morphed_cpacs_path": str(exported)}, link_dir)
    assert carried and open(carried).read() == "<cpacs>morphed</cpacs>"
    task = base_task_for("mdo_f25_sequential_staged_pipeline", carried)
    assert carried in task and str(_D150_FIXTURE) not in task


def test_without_an_export_the_chain_keeps_its_geometry(tmp_path):
    from scripts.stat_batch_runner import carry_geometry_forward

    assert carry_geometry_forward({}, tmp_path) is None
    assert carry_geometry_forward({"morphed_cpacs_path": "/no/such/file.xml"}, tmp_path) is None


# ---- the carried geometry path must work from ANOTHER process's working directory ----------------
#
# geoauth_all8_7960 link 2: the runner handed the next link 'logs/stat_results/.../geometry_end.xml',
# a path relative to the RUNNER's working directory. The file is opened by the tigl MCP server, which
# runs elsewhere, so tigl answered "File not found", every geometry call after it failed with
# "Unknown session_id", and the link was recorded as failed with zero fuel. The earlier carry-forward
# test wrote to an absolute tmp_path and could not have seen it.

def test_a_relative_link_directory_still_yields_an_absolute_path(tmp_path, monkeypatch):
    from scripts.stat_batch_runner import carry_geometry_forward

    monkeypatch.chdir(tmp_path)
    exported = tmp_path / "morphed.xml"
    exported.write_text("<cpacs/>")
    carried = carry_geometry_forward({"morphed_cpacs_path": str(exported)},
                                     os.path.join("logs", "stat_results", "x", "repeat_000", "combo"))
    assert carried is not None and os.path.isabs(carried)


def test_the_task_names_an_absolute_path_even_when_given_a_relative_one(tmp_path, monkeypatch):
    """A resumed run reads the relative path the earlier version stored in result.json."""
    from scripts.stat_batch_runner import base_task_for

    monkeypatch.chdir(tmp_path)
    rel = os.path.join("logs", "geometry_end.xml")
    os.makedirs("logs")
    open(rel, "w").write("<cpacs/>")
    task = base_task_for("mdo_f25_sequential_staged_pipeline", rel)
    assert str(tmp_path / "logs" / "geometry_end.xml") in task
    assert f"at {rel}." not in task
