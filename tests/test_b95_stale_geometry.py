"""B95: no meshing the baseline once the design has moved on.

validate14, first networked link:

    2  agent_3  open_cpacs            success
    8  agent_3  generate_volume_mesh  failed (cell cap)

`set_high_level_parameters` (the morph) and `export_cpacs` never ran, so the mesh describes the
DLR-F25 baseline while the mission holder had already applied ASPECT_RATIO 11.5, AREA 124.6,
SWEEP 28.0 and fuselage 35.0 m. Geometry, aero and mass then describe one aircraft and the
mission describes another -- and because no morphed file is ever produced, `estimate_mass` falls
back to the baseline fixture in every run (B91).

Stubbed: no MCP server, no model, no .env.
"""

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm
from src.tools.geometry_guard import stale_geometry

SESSION = "tigl-session-1"
DESIGN = {"Aircraft.Wing.ASPECT_RATIO": 11.5, "Aircraft.Wing.AREA": 124.6,
          "Aircraft.Wing.SWEEP": 28.0, "Aircraft.Fuselage.LENGTH": 35.0}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {})
    kbm.configure_run(None, 0, 1, None)
    yield


def _open_session(epoch=0):
    """Put a tigl session in the guard state at the given geometry epoch."""
    from src.tools.duplicate_guard import _state

    gs = _state()
    gs["geometry_epoch"][SESSION] = epoch
    return gs


def _design_applied(params=None):
    dp._design_state.data_store["design_params_applied"] = dict(params or DESIGN)


# ---- the refusal ------------------------------------------------------------------------------

def test_meshing_an_unmorphed_session_is_refused_once_the_design_has_moved():
    _open_session(epoch=0)
    _design_applied()
    refusal = stale_geometry("generate_volume_mesh", {"session_id": SESSION})
    assert refusal is not None
    assert refusal["error_code"] == "STALE_GEOMETRY"
    assert "still the BASELINE" in refusal["error"]


def test_the_refusal_writes_the_exact_morph_call():
    """Not a description of the fix -- the call itself, with the real UID and the design's numbers.

    validate14 04:24 showed why this matters: the geometry holder went looking for the current
    values with get_high_level_parameters and got {} (B97), and our own procedure text told it that
    the morph needs span, root chord, tip chord and sweep TOGETHER. In fact ANY of area /
    aspect_ratio / sweep fires it (tigl-mcp `_wing_targets`), and the mission already holds all three.
    """
    _open_session(epoch=0)
    _design_applied()
    error = stale_geometry("generate_volume_mesh", {"session_id": SESSION})["error"]
    assert "ASPECT_RATIO=11.5" in error and "AREA=124.6" in error
    assert "set_high_level_parameters(session_id='tigl-session-1', component_uid='Wing1'" in error
    assert "'area': 124.6" in error and "'aspect_ratio': 11.5" in error and "'sweep': 28.0" in error
    assert "you do not need span, root chord and tip chord" in error
    assert "export_cpacs" in error


def test_the_call_falls_back_to_placeholders_when_the_design_has_no_wing_values():
    _open_session(epoch=0)
    _design_applied({"Aircraft.Fuselage.LENGTH": 35.0})
    error = stale_geometry("generate_volume_mesh", {"session_id": SESSION})["error"]
    assert "'area': <m^2>" in error


def test_exporting_an_unmorphed_session_is_refused_too():
    """Exporting the baseline is the same error one step later -- it is what mass sizes on."""
    _open_session(epoch=0)
    _design_applied()
    assert stale_geometry("export_cpacs", {"session_id": SESSION}) is not None


# ---- and the cases it must NOT touch -----------------------------------------------------------

def test_a_morphed_session_proceeds():
    _open_session(epoch=1)          # set_high_level_parameters has run
    _design_applied()
    assert stale_geometry("generate_volume_mesh", {"session_id": SESSION}) is None


def test_no_design_applied_means_the_baseline_IS_the_current_design():
    _open_session(epoch=0)
    assert stale_geometry("generate_volume_mesh", {"session_id": SESSION}) is None


def test_an_unknown_session_is_left_to_the_server():
    _design_applied()
    assert stale_geometry("generate_volume_mesh", {"session_id": "not-a-session"}) is None


def test_other_tools_are_untouched():
    _open_session(epoch=0)
    _design_applied()
    for tool in ("open_cpacs", "run_su2_solver", "estimate_mass", "set_aircraft_parameters"):
        assert stale_geometry(tool, {"session_id": SESSION}) is None


def test_the_guard_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AVION_REQUIRE_MORPH_BEFORE_MESH", "0")
    _open_session(epoch=0)
    _design_applied()
    assert stale_geometry("generate_volume_mesh", {"session_id": SESSION}) is None


# ---- the capture that feeds it -----------------------------------------------------------------

def test_applied_design_parameters_are_captured_from_set_aircraft_parameters():
    payload = {"success": True, "applied": [
        {"name": "Aircraft.Wing.ASPECT_RATIO", "old_value": 12.4177, "new_value": 11.5},
        {"name": "Aircraft.Wing.AREA", "old_value": 126.3327, "new_value": 124.6},
        {"name": "Aircraft.Wing.SPAN", "new_value": 37.8537, "derived": True}]}
    dp._capture_applied_design("set_aircraft_parameters", payload)
    captured = dp._design_state.data_store["design_params_applied"]
    assert captured["Aircraft.Wing.ASPECT_RATIO"] == 11.5
    assert captured["Aircraft.Wing.SPAN"] == 37.8537          # derived values count too


def test_later_parameter_calls_merge_rather_than_replace():
    dp._capture_applied_design("set_aircraft_parameters", {"applied": [
        {"name": "Aircraft.Wing.AREA", "new_value": 130.0}]})
    dp._capture_applied_design("set_aircraft_parameters", {"applied": [
        {"name": "Aircraft.Wing.SWEEP", "new_value": 25.0}]})
    captured = dp._design_state.data_store["design_params_applied"]
    assert captured == {"Aircraft.Wing.AREA": 130.0, "Aircraft.Wing.SWEEP": 25.0}


def test_a_result_with_no_applied_list_is_ignored():
    dp._capture_applied_design("set_aircraft_parameters", {"success": False, "error": "nope"})
    assert "design_params_applied" not in dp._design_state.data_store


# ---- B91: structures must size the design, not the fixture -------------------------------------
#
# validate15 05:13-05:16 is the cleanest statement of it: the wing had just been morphed
# (span 33.91 -> 45.40 m, aspect ratio 9.37 -> 15.89, rebuilt: true) and estimate_mass ran three
# minutes later on mass-mcp/tests/fixtures/D150_simple.xml. The right geometry was in the tigl
# session; the mass model read the wrong file off disk, because nobody had called export_cpacs.

from src.tools.geometry_guard import mass_on_baseline

FIXTURE = "/home/jezemba/Avion/mass-mcp/tests/fixtures/D150_simple.xml"


def _morphed_session():
    from src.tools.duplicate_guard import _state
    _state()["geometry_epoch"][SESSION] = 1


def test_sizing_the_fixture_is_refused_once_a_design_exists():
    _open_session(epoch=0)
    _design_applied()
    refusal = mass_on_baseline("estimate_mass", {"cpacs_file_path": FIXTURE})
    assert refusal is not None
    assert refusal["error_code"] == "MASS_ON_BASELINE"
    assert "MASS_SCALER" in refusal["error"]


def test_a_morphed_but_unexported_geometry_is_told_to_export():
    """The validate15 case exactly: geometry deformed in the session, never written out."""
    _morphed_session()
    _design_applied()
    error = mass_on_baseline("estimate_mass", {"cpacs_file_path": FIXTURE})["error"]
    assert "HAS been morphed" in error
    assert "export_cpacs(session_id=" in error


def test_an_unmorphed_geometry_is_told_to_morph_first():
    _open_session(epoch=0)
    _design_applied()
    error = mass_on_baseline("estimate_mass", {"cpacs_file_path": FIXTURE})["error"]
    assert "has not been morphed yet" in error
    assert "set_high_level_parameters(component_uid='Wing1'" in error


def test_with_no_design_applied_the_fixture_is_the_current_design():
    _open_session(epoch=0)
    assert mass_on_baseline("estimate_mass", {"cpacs_file_path": FIXTURE}) is None


def test_a_morphed_export_on_disk_proceeds(tmp_path):
    exported = tmp_path / "morphed_design.xml"
    exported.write_text("<cpacs/>")
    _morphed_session()
    _design_applied()
    dp._design_state.data_store["morphed_cpacs_path"] = str(exported)
    assert mass_on_baseline("estimate_mass", {"cpacs_file_path": str(exported)}) is None


def test_a_deliberate_non_fixture_path_is_left_alone():
    _morphed_session()
    _design_applied()
    assert mass_on_baseline("estimate_mass", {"cpacs_file_path": "/tmp/some_other_design.xml"}) is None


def test_other_tools_are_untouched_by_the_mass_guard():
    _open_session(epoch=0)
    _design_applied()
    assert mass_on_baseline("generate_volume_mesh", {"cpacs_file_path": FIXTURE}) is None


def test_the_mass_guard_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AVION_REQUIRE_MORPHED_MASS", "0")
    _open_session(epoch=0)
    _design_applied()
    assert mass_on_baseline("estimate_mass", {"cpacs_file_path": FIXTURE}) is None
