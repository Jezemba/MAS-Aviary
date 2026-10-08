"""B148: the wing measured by get_high_level_parameters is the geometry (2026-10-08).

tigl-mcp B145 made get_high_level_parameters measure the geometry, and agents switched to it from
get_wing_summary. The data plane only captured the wing from get_wing_summary and
set_high_level_parameters, so v2b147 captured no wing: no target CL (B146 never fired, SU2 flew
2 deg, CL 0.24, link 0 infeasible) and no aspect ratio for SU2's drag floor.
Responses below are what the live tigl server returned for D150 on aipexws5.

Stubbed: no MCP server, no model, no .env.
"""

import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

NOTE = "Measured from the current geometry. ... sweep is the measured quarter-chord sweep ..."
WING1 = {"component_uid": "Wing1", "measured": True, "note": NOTE, "parameters": {
    "area": 122.782, "aspect_ratio": 9.367, "span": 33.913, "sweep": 21.649, "taper_ratio": 0.246}}
WING3V = {"component_uid": "Wing3V", "measured": True, "note": NOTE, "parameters": {
    "area": 21.5, "aspect_ratio": 1.607, "span": 5.878, "sweep": 37.2, "taper_ratio": 0.349}}
WING2H = {"component_uid": "Wing2H", "measured": True, "note": NOTE, "parameters": {
    "area": 31.118, "aspect_ratio": 4.981, "span": 12.45, "sweep": 28.0, "taper_ratio": 0.33}}
FUSELAGE1 = {"component_uid": "Fuselage1", "measured": True, "parameters": {
    "length": 37.684, "max_width": 3.96, "max_height": 4.149, "diameter": 4.149}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AVION_DESIGN_AUTHORITY", raising=False)
    monkeypatch.setattr(dp, "_design_state", DesignState())
    dp._call_ctx.__dict__.clear()
    yield dp._design_state


def _get(payload):
    dp.intercept_response("get_high_level_parameters", json.dumps(payload))


def test_the_main_wing_is_captured_with_its_measured_planform(fresh):
    _get(WING1)
    w = fresh.data_store["geometry_wing"]
    assert w["Aircraft.Wing.AREA"] == pytest.approx(122.782)
    assert w["Aircraft.Wing.ASPECT_RATIO"] == pytest.approx(9.367)
    assert w["Aircraft.Wing.SWEEP"] == pytest.approx(21.649)
    assert w["Aircraft.Wing.TAPER_RATIO"] == pytest.approx(0.246)


@pytest.mark.parametrize("tail", [WING3V, WING2H])
def test_a_tail_is_not_the_wing_even_before_the_component_list_is_known(fresh, tail):
    """v2b147 round 1 measured Wing3V right after Wing1."""
    _get(WING1)
    _get(tail)
    assert fresh.data_store["geometry_wing"]["Aircraft.Wing.AREA"] == pytest.approx(122.782)


def test_an_unmeasured_response_is_not_the_geometry(fresh):
    _get({"component_uid": "Wing1", "parameters": {"area": 140.0, "aspect_ratio": 10.0}})
    assert "geometry_wing" not in fresh.data_store


def test_the_fuselage_is_captured(fresh):
    _get(FUSELAGE1)
    f = fresh.data_store["geometry_fuselage"]
    assert (f["Aircraft.Fuselage.LENGTH"], f["Aircraft.Fuselage.MAX_HEIGHT"], f["Aircraft.Fuselage.MAX_WIDTH"]) == \
        pytest.approx((37.684, 4.149, 3.96))


def test_the_captured_wing_now_trims_su2_and_feeds_its_drag_floor(fresh, tmp_path):
    """The chain that broke in v2b147: wing captured -> target CL + aspect ratio on configure_from_cpacs."""
    cpacs = tmp_path / "plane.xml"
    cpacs.write_text("<cpacs><analyses><massBreakdown><designMasses><mTOM><mass>78126.0</mass></mTOM>"
                     "</designMasses></massBreakdown></analyses></cpacs>")
    _get(WING1)
    resolved = dp.resolve_request("configure_from_cpacs", {"session_id": "s", "cpacs_file_path": str(cpacs)})
    assert 0.5 < resolved["target_cl"] < 0.6
    assert resolved["aspect_ratio"] == pytest.approx(9.367)


def test_su2_gets_the_measured_full_wing_as_its_reference_area(fresh):
    _get(WING1)
    resolved = dp.resolve_request("configure_from_cpacs", {"session_id": "s"})
    assert resolved["ref_area"] == pytest.approx(122.782)


# ---- design.json design_space: the prompt table's variables (2026-10-08) -------------------------

def test_design_space_holds_measured_and_set_geometry_and_the_engine(fresh):
    from src.tools.design_json import build_design

    _get(WING1)
    _get(WING2H)
    _get(FUSELAGE1)
    # A reshape: the set key is recorded apart from the measured result.
    dp._call_ctx.morph = {"uid": "Wing1", "updates": {"sweep": 25.0}}
    dp.intercept_response("set_high_level_parameters", json.dumps({
        "component_uid": "Wing1", "new_parameters": {}, "warnings": [],
        "geometry_morph": {"rebuilt": True, "after": {"reference_area": 122.69, "aspect_ratio": 9.519,
                                                      "span": 34.17, "sweep_deg": 21.97, "taper_ratio": 0.246}}}))
    # pycycle: get_design_inputs gives current values; set_inputs records what it confirmed.
    dp.intercept_response("get_design_inputs", json.dumps({"design_inputs": [
        {"name": "splitter.BPR", "current_value": 5.105}, {"name": "T4_MAX", "current_value": 2857.0},
        {"name": "fc.alt", "current_value": 35000.0}]}))
    dp._call_ctx.cycle_values = {"splitter.BPR": 6.5, "fan.PR": 1.6, "fc.alt": 33000.0}
    dp.intercept_response("set_inputs", json.dumps({"updated": ["splitter.BPR", "fan.PR", "fc.alt"], "skipped": []}))
    space = build_design(fresh.data_store)["design_space"]
    w1 = space["geometry"]["Wing1"]
    assert w1["sweep_set"] == 25.0 and w1["sweep_quarter_chord_deg"] == pytest.approx(21.97)
    assert w1["area_m2"] == pytest.approx(122.69)
    assert space["geometry"]["Wing2H"]["area_m2"] == pytest.approx(31.118)
    assert space["geometry"]["Fuselage1"] == {"length_m": pytest.approx(37.684), "diameter_m": pytest.approx(4.149)}
    assert space["engine"] == {"splitter.BPR": 6.5, "T4_MAX": 2857.0, "fan.PR": 1.6}   # fc.alt is not a design input
