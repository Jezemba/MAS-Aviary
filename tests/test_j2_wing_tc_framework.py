"""J2 decision (Jessica 2026-11-18): aviary flies the measured wing thickness-to-chord, framework-owned like the G1 tails.

mass-mcp's estimate_mass reports the chord-weighted mean t/c it sizes the wing with (mass_breakdown.sizing_report.
wing_stations.tc_avg; D150 0.1164, B303); aviary-mcp J2 (b82381b) accepts Aircraft.Wing.THICKNESS_TO_CHORD (bench 0.13).
Captured with a usable estimate, withdrawn with an unusable one, injected on every set, agents refused, required by
the mission, re-applied fresh, in design.json, seeded by graph pre-init, part of the cruise-point design basis.
No network, no API, no MCP servers.
"""
import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from tests.test_g1_cpacs_tails_and_cycle_keys import _estimate, _set, fresh  # noqa: F401 (fixture)

KEY = dp.WING_TC_KEY


def _applied(out):
    dp._capture_applied_design("set_aircraft_parameters", {"success": True, "applied": [
        {"name": k, "old_value": None, "new_value": v} for k, v in out["parameters"].items()]})


def test_j2_captured_with_a_usable_estimate_and_put_on_every_set(fresh):
    data = _estimate()
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", data)
    rec = fresh.data_store["geometry_wing_tc"]
    assert rec[KEY] == pytest.approx(0.1164) and "wing_stations.tc_avg" in rec["_source"]
    out, refusal = _set({})
    assert refusal is None and out["parameters"][KEY] == pytest.approx(0.1164)        # not the bench 0.13


def test_j2_an_agent_cannot_set_the_wing_tc(fresh):
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    _, refusal = _set({KEY: 0.13})
    assert refusal["error_code"] == "WING_TC_FROM_GEOMETRY" and KEY in refusal["error"]
    assert refusal["framework_values"][KEY] == pytest.approx(0.1164)
    _, refusal = _set({KEY: 0.1164})                       # the framework's own value (a re-apply) passes
    assert refusal is None


def test_j2_without_a_measurement_none_is_injected_and_an_agent_value_is_refused(fresh):
    out, refusal = _set({})
    assert refusal is None and KEY not in out["parameters"]
    _, refusal = _set({KEY: 0.11})
    assert refusal["error_code"] == "WING_TC_FROM_GEOMETRY" and "no usable mass estimate" in refusal["error"]


def test_j2_the_mission_is_refused_until_aviary_holds_the_measured_tc(fresh):
    assert "wing thickness-to-chord" in dp.mission_input_refusal("run_simulation", {"session_id": "av"})["error"]
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    assert "does not hold the measured wing thickness-to-chord" in \
        dp.mission_input_refusal("run_simulation", {"session_id": "av"})["error"]
    out, _ = _set({})
    _applied(out)
    assert dp.mission_input_refusal("run_simulation", {"session_id": "av"}) is None


@pytest.mark.parametrize("stations, why", [(None, "no measured wing stations"),
                                           ({"tc_avg": None}, "not a number"),
                                           ({"tc_avg": 0.9}, "outside")])
def test_j2_an_estimate_without_a_usable_tc_is_recorded_and_refuses_the_mission(fresh, stations, why):
    data = _estimate()
    if stations is None:
        data["mass_breakdown"]["sizing_report"].pop("wing_stations")
    else:
        data["mass_breakdown"]["sizing_report"]["wing_stations"] = stations
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", data)
    assert "geometry_wing_tc" not in fresh.data_store and why in fresh.data_store["wing_tc_error"]
    assert why in dp.mission_input_refusal("run_simulation", {"session_id": "av"})["error"]


@pytest.mark.parametrize("kind", ["unconverged", "status_error"])
def test_j2_an_unusable_estimate_withdraws_the_tc(fresh, kind):
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    bad = _estimate()
    if kind == "unconverged":
        bad["mass_breakdown"]["sizing_converged"] = False
    else:
        bad = {"status": "error", "error_message": "TiGL could not open the file"}
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", bad)
    assert "geometry_wing_tc" not in fresh.data_store


def test_j2_the_reapply_does_not_resend_an_old_tc(fresh, monkeypatch):
    sent = []

    class _Setter:
        def forward(self, session_id, parameters):
            sent.append(dict(parameters))
            return {"success": True, "applied": []}

    monkeypatch.setattr(dp, "_registered_tools", {"set_aircraft_parameters": _Setter()})
    fresh.data_store["design_params_applied"] = {KEY: 0.13, "Aircraft.Wing.MASS": 7000.0}
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    fresh.data_store["_coupled_version"] = 2
    dp._reapply_current_wing_mass({"session_id": "av"})
    assert sent and KEY not in sent[0] and sent[0]["Aircraft.Wing.MASS"] == 7000.0


def test_j2_design_json_records_it_and_graph_preinit_seeds_it(fresh, monkeypatch):
    from src.coordination import graph_preinit
    from src.tools.design_json import build_design

    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    doc = json.loads(json.dumps(build_design(fresh.data_store), default=str))
    st = doc["structures"]
    assert st["wing_thickness_to_chord"] == pytest.approx(0.1164)
    assert "wing_stations.tc_avg" in st["wing_thickness_to_chord_source"] and st["wing_thickness_to_chord_error"] is None
    new = DesignState()
    monkeypatch.setattr(dp, "_design_state", new)
    graph_preinit.seed_from_design(doc)
    assert new.data_store["geometry_wing_tc"][KEY] == pytest.approx(0.1164)
    old = dict(doc, structures={k: v for k, v in st.items() if k != "wing_thickness_to_chord"})
    newer = DesignState()
    monkeypatch.setattr(dp, "_design_state", newer)
    graph_preinit.seed_from_design(old)
    assert "geometry_wing_tc" not in newer.data_store and "no measured wing" in newer.data_store["wing_tc_error"]


def test_j2_the_tc_is_part_of_the_cruise_point_design_basis(fresh, monkeypatch):
    from src.tools import coupling_contract

    monkeypatch.setattr(coupling_contract, "current_design_fingerprint", lambda: "geom-1")
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    basis = dp.design_basis()
    assert basis[2] == pytest.approx(0.1164)
    other = _estimate()
    other["mass_breakdown"]["sizing_report"]["wing_stations"]["tc_avg"] = 0.105
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", other)
    assert dp.design_basis() != basis                    # a new t/c makes the stored cruise point not this design's


def test_j2_the_graph_cache_version_moved_past_g1():
    from src.coordination import graph_preinit

    assert graph_preinit.BASELINE_VERSION not in ("b254", "b306", "b306g1")


def test_j2_switch_off_flies_the_bench_value_explicitly(fresh, monkeypatch):
    monkeypatch.setenv("AVION_CPACS_WING_TC", "0")
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    out, refusal = _set({})
    assert refusal is None and KEY not in out["parameters"]
    _applied(out)
    assert dp.mission_input_refusal("run_simulation", {"session_id": "av"}) is None
    assert dp.design_basis()[2] is None
