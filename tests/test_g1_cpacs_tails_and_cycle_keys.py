"""G1 (Jessica 2026-11-18): aviary flies the measured CPACS tails; F12 follow-up: unknown pyCycle keys are refused.

G1: the tails mass-mcp measured and sized with (estimate_mass mass_breakdown.sizing_report.tails, mass-mcp 5b7368c,
B304: D150 Wing2H 31.12 m^2, Wing3V 21.50 m^2) are put on every set_aircraft_parameters call, a caller cannot set
them to another value (TAIL_FROM_GEOMETRY), and run_simulation is refused unless aviary holds them.
No network, no API, no MCP servers.
"""
import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

# mass-mcp 5b7368c surface_summary of the D150 baseline (B304 report values; AR/taper/t-c as measured there).
D150_TAILS = {
    "horizontal": {"orientation": "horizontal", "area_m2": 31.1184, "span_m": 12.45, "aspect_ratio": 4.981,
                   "taper_ratio": 0.330, "sweep_quarter_chord_deg": 28.00, "tc_avg": 0.100, "uid": "Wing2H",
                   "source": "tigl_chord_points"},
    "vertical": {"orientation": "vertical", "area_m2": 21.50, "span_m": 5.67, "aspect_ratio": 1.49,
                 "taper_ratio": 0.349, "sweep_quarter_chord_deg": 37.20, "tc_avg": 0.110, "uid": "Wing3V",
                 "source": "tigl_chord_points"},
}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for var in ("AVION_CPACS_TAILS", "AVION_REQUIRE_COUPLED_INPUTS"):
        monkeypatch.delenv(var, raising=False)
    state = DesignState()
    monkeypatch.setattr(dp, "_design_state", state)
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_tool_server_map", {"set_aircraft_parameters": "aviary", "set_inputs": "pycycle",
                                                 "estimate_mass": "mass", "run_simulation": "aviary"})
    for name in ("refusal", "engine_pending"):
        if hasattr(dp._call_ctx, name):
            delattr(dp._call_ctx, name)
    yield state


def _estimate(tails=D150_TAILS):
    report = {"driver": {"converged": True}}
    if tails is not None:
        report["tails"] = tails
    return {"success": True, "cpacs_file_path": "/x/D150.xml", "mass_breakdown": {
        "components": {"mWing_kg": 7833.0, "mWing_source": "flops"}, "mTOM_kg": 78126.0,
        "mTOM_sized_kg": 88066.0, "sizing_converged": True, "sizing_report": report}}


def _set(params):
    dp.take_refusal()
    out = dp.resolve_request("set_aircraft_parameters", {"session_id": "av", "parameters": dict(params)})
    return out, dp.take_refusal()


# ---- G1 ---------------------------------------------------------------------------------------------------

def test_g1_the_measured_tails_are_captured_with_a_usable_estimate(fresh):
    data = _estimate()
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", data)
    tails = fresh.data_store["geometry_tails"]
    assert data["success"] is True
    assert tails["Aircraft.HorizontalTail.AREA"] == pytest.approx(31.1184)
    assert tails["Aircraft.VerticalTail.AREA"] == pytest.approx(21.50)
    assert tails["Aircraft.VerticalTail.ASPECT_RATIO"] == pytest.approx(1.49)
    assert tails["Aircraft.HorizontalTail.THICKNESS_TO_CHORD"] == pytest.approx(0.100)
    assert set(dp.TAIL_KEYS) <= set(tails)
    assert "Wing2H" in tails["_source"] and "Wing3V" in tails["_source"]


def test_g1_every_set_call_carries_the_measured_tails_not_the_bench(fresh):
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    out, refusal = _set({})
    assert refusal is None
    params = out["parameters"]
    assert params["Aircraft.HorizontalTail.AREA"] == pytest.approx(31.1184)      # not the bench 355 ft^2 = 32.98 m^2
    assert params["Aircraft.VerticalTail.AREA"] == pytest.approx(21.50)          # not the bench 284 ft^2 = 26.38 m^2
    assert all(k in params for k in dp.TAIL_KEYS)


def test_g1_a_caller_cannot_set_a_tail_to_another_value(fresh):
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    _, refusal = _set({"Aircraft.HorizontalTail.AREA": 32.98})
    assert refusal["error_code"] == "TAIL_FROM_GEOMETRY"
    assert "Aircraft.HorizontalTail.AREA" in refusal["error"] and "Wing2H" in refusal["error"]
    assert refusal["framework_values"]["Aircraft.HorizontalTail.AREA"] == pytest.approx(31.1184)
    # The framework's own value passes (e.g. a re-apply that names it).
    _, refusal = _set({"Aircraft.HorizontalTail.AREA": 31.1184})
    assert refusal is None


def test_g1_without_a_measurement_a_tail_cannot_be_set_and_none_is_injected(fresh):
    out, refusal = _set({})
    assert refusal is None and not any(k in out["parameters"] for k in dp.TAIL_KEYS)
    _, refusal = _set({"Aircraft.VerticalTail.AREA": 26.38})
    assert refusal["error_code"] == "TAIL_FROM_GEOMETRY" and "no usable mass estimate" in refusal["error"]


def test_g1_the_mission_is_refused_until_aviary_holds_the_measured_tails(fresh):
    refusal = dp.mission_input_refusal("run_simulation", {"session_id": "av"})
    assert refusal["error_code"] == "MISSION_INPUTS_NOT_APPLIED" and "measured tails" in refusal["error"]
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    refusal = dp.mission_input_refusal("run_simulation", {"session_id": "av"})
    assert "aviary does not hold the measured tails" in refusal["error"]
    out, _ = _set({})
    dp._capture_applied_design("set_aircraft_parameters", {"success": True, "applied": [
        {"name": k, "old_value": None, "new_value": v} for k, v in out["parameters"].items()]})
    assert dp.mission_input_refusal("run_simulation", {"session_id": "av"}) is None


def test_g1_a_refused_set_does_not_count_as_holding_the_tails(fresh):
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    dp._capture_applied_design("set_aircraft_parameters", {"success": False, "error_code": "UNKNOWN_PARAMETER",
                                                           "error": "Unknown parameter: 'Aircraft.HorizontalTail.AREA'"})
    assert "measured tails" in dp.mission_input_refusal("run_simulation", {"session_id": "av"})["error"]


def test_g1_an_estimate_without_tails_is_recorded_and_refuses_the_mission(fresh):
    data = _estimate(tails=None)
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", data)
    assert "geometry_tails" not in fresh.data_store
    assert "no measured tails" in fresh.data_store["tail_geometry_error"]
    assert "no measured tails" in dp.mission_input_refusal("run_simulation", {"session_id": "av"})["error"]


def test_g1_an_unusable_estimate_withdraws_the_tails(fresh):
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    bad = _estimate()
    bad["mass_breakdown"]["sizing_converged"] = False
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", bad)
    assert "geometry_tails" not in fresh.data_store


def test_g1_the_framework_reapply_does_not_resend_old_tails(fresh, monkeypatch):
    """B272 / B187 re-apply from design_params_applied: the old tails are dropped, the measured ones injected."""
    sent = []

    class _Setter:
        def forward(self, session_id, parameters):
            sent.append(dict(parameters))
            return {"success": True, "applied": []}

    monkeypatch.setattr(dp, "_registered_tools", {"set_aircraft_parameters": _Setter()})
    fresh.data_store["design_params_applied"] = {"Aircraft.HorizontalTail.AREA": 30.0, "Aircraft.Wing.MASS": 7000.0}
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    fresh.data_store["_coupled_version"] = 2
    dp._reapply_current_wing_mass({"session_id": "av"})
    assert sent and "Aircraft.HorizontalTail.AREA" not in sent[0] and sent[0]["Aircraft.Wing.MASS"] == 7000.0


def test_g1_design_json_records_the_tails_and_graph_preinit_seeds_them(fresh, monkeypatch):
    from src.coordination import graph_preinit
    from src.tools.design_json import build_design

    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    doc = json.loads(json.dumps(build_design(fresh.data_store), default=str))
    tails = doc["structures"]["tails"]
    assert tails["Aircraft.VerticalTail.AREA"] == pytest.approx(21.50) and "_source" not in tails
    assert "Wing3V" in doc["structures"]["tails_source"]
    monkeypatch.setattr(graph_preinit, "BASELINE_VERSION", graph_preinit.BASELINE_VERSION)
    new = DesignState()
    monkeypatch.setattr(dp, "_design_state", new)
    graph_preinit.seed_from_design(doc)
    assert new.data_store["geometry_tails"]["Aircraft.HorizontalTail.AREA"] == pytest.approx(31.1184)
    # A design.json of older code (no tails) seeds none and says so.
    old = dict(doc, structures={k: v for k, v in doc["structures"].items() if k != "tails"})
    newer = DesignState()
    monkeypatch.setattr(dp, "_design_state", newer)
    graph_preinit.seed_from_design(old)
    assert "geometry_tails" not in newer.data_store and "no measured tails" in newer.data_store["tail_geometry_error"]


def test_g1_the_graph_cache_version_moved_past_b306():
    from src.coordination import graph_preinit

    assert graph_preinit.BASELINE_VERSION not in ("b254", "b306")


def test_g1_switch_off_restores_the_bench_tails_explicitly(fresh, monkeypatch):
    monkeypatch.setenv("AVION_CPACS_TAILS", "0")
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    out, refusal = _set({})
    assert refusal is None and not any(k in out["parameters"] for k in dp.TAIL_KEYS)
    fresh.data_store["_coupled_version_applied"] = fresh.data_store.get("_coupled_version")   # aviary took the set
    assert dp.mission_input_refusal("run_simulation", {"session_id": "av"}) is None


# ---- F12 follow-up: unknown pyCycle keys -------------------------------------------------------------------

def _cycle(values):
    dp.take_refusal()
    out = dp.resolve_request("set_inputs", {"session_id": "pc", "values": values})
    return out, dp.take_refusal()


def test_f12_an_unknown_cycle_key_is_refused_with_the_accepted_keys(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_inject_phase_k_b_fn_des", lambda resolved: None)   # sizing tested elsewhere
    _, refusal = _cycle({"balance.lpt_PR": 7.5, "splitter.BPR": 6.0})
    assert refusal["error_code"] == "UNKNOWN_CYCLE_KEY"
    assert refusal["unknown_keys"] == ["balance.lpt_PR"]
    for key in ("splitter.BPR", "T4_MAX", "fan.PR", "lpc.PR", "hpc.PR", "T4_TAKEOFF", "T4_CLIMB", "fan.eff", "Fn_DES",
                "fc.alt", "fc.MN"):
        assert key in refusal["error"] and key in refusal["accepted_keys"]


def test_f12_the_design_table_keys_are_accepted(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_inject_phase_k_b_fn_des", lambda resolved: None)
    _, refusal = _cycle({"splitter.BPR": 6.0, "T4_MAX": 2900.0, "fan.PR": 1.6, "lpc.PR": 1.9, "hpc.PR": 10.0,
                         "T4_TAKEOFF": 3500.0, "T4_CLIMB": 3100.0, "fan.eff": 0.8948, "fc.alt": 33000.0,
                         "fc.MN": 0.78, "fc.dTs": 0.0})
    assert refusal is None


def test_f12_the_accepted_keys_are_every_pycycle_row_of_the_design_table():
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "config" / "mdo_f25_sequential_agents.yaml").read_text()
    rows = set(re.findall(r"pycycle:? `([A-Za-z0-9_.]+)`", text)) | set(re.findall(r"pycycle `([A-Za-z0-9_.]+)`", text))
    assert rows and rows <= set(dp.accepted_cycle_keys())


def test_f12_a_json_string_of_values_is_checked_too(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_inject_phase_k_b_fn_des", lambda resolved: None)
    _, refusal = _cycle(json.dumps({"nozz.Cv": 0.99}))
    assert refusal["error_code"] == "UNKNOWN_CYCLE_KEY" and refusal["unknown_keys"] == ["nozz.Cv"]
