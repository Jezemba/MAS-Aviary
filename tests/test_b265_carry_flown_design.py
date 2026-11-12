"""B265: the next link starts from the design the last mission FLEW, not from what was touched after it.

networked_graph_routed, 2-link study 2026-11-10, link 1: the last mission flew 122.79 m^2 (23,787 kg); the
agent then reshaped to 150 m^2 and solved SU2 for it without flying. design.json held the 150 m^2 wing, its
aero and its engine scale, and link 2 was seeded with them while it opened the 122.79 m^2 geometry (B263).
"""
from __future__ import annotations

import json

import pytest

from scripts.check_coupling import FAIL, PASS, check_sweep
from src.coordination.design_state import DesignState
from src.tools import data_plane as dp
from src.tools import knowledge_base


@pytest.fixture
def link_dir(tmp_path, monkeypatch):
    d = tmp_path / "repeat_000" / "mdo_f25_networked_graph_routed"
    d.mkdir(parents=True)
    monkeypatch.setitem(knowledge_base._run_config, "path", str(d / "knowledge_base.jsonl"))
    state = DesignState()
    dp.init_data_plane(state, {"run_simulation": "aviary"})
    return d, state


def _fly(state, area, cd, fuel):
    state.data_store["design_space_components"] = {"Wing1": {"area_m2": area}}
    state.data_store["aero_cd_cruise"] = cd
    dp._snapshot_flown_geometry("run_simulation", {"summary": {"fuel_burned_kg": fuel}})
    dp._write_design_json()


def test_the_flown_design_is_kept_beside_design_json(link_dir):
    d, state = link_dir
    _fly(state, 122.79, 0.0262, 23787.2)
    state.data_store["design_space_components"] = {"Wing1": {"area_m2": 150.0}}   # after the flight
    state.data_store["aero_cd_cruise"] = 0.0338
    dp._write_design_json()
    flown = json.loads((d / "design_flown.json").read_text())
    final = json.loads((d / "design.json").read_text())
    assert flown["design_space"]["geometry"]["Wing1"]["area_m2"] == 122.79
    assert flown["aero"]["cd_cruise"] == 0.0262
    assert final["design_space"]["geometry"]["Wing1"]["area_m2"] == 150.0


def test_a_mission_that_did_not_fly_keeps_no_flown_design(link_dir):
    d, state = link_dir
    dp._snapshot_flown_geometry("run_simulation", {"summary": {"fuel_burned_kg": 0.0}})
    assert not (d / "design_flown.json").exists()


def test_the_runner_carries_the_flown_design_and_the_last_recommendation(link_dir):
    from scripts.stat_batch_runner import design_space_of, write_carry_over

    d, state = link_dir
    _fly(state, 122.79, 0.0262, 23787.2)
    state.data_store["design_space_components"] = {"Wing1": {"area_m2": 150.0}}
    state.data_store["coordination_signals"] = [{"kind": "recommendation", "recommended_change": "AR 10"}]
    dp._write_design_json()
    assert design_space_of(d)["geometry"]["Wing1"]["area_m2"] == 122.79
    nxt = d.parent.parent / "repeat_001" / d.name
    carry = json.loads(write_carry_over(nxt, 1, "text", None, {}, design_space_of(d)).read_text())
    assert carry["starting_design"]["geometry"]["Wing1"]["area_m2"] == 122.79
    assert carry["previous_design"]["aero"]["cd_cruise"] == 0.0262
    assert carry["recommendation"]["recommended_change"] == "AR 10"


def test_a_link_that_never_flew_falls_back_to_design_json(link_dir):
    from scripts.stat_batch_runner import design_space_of

    d, state = link_dir
    state.data_store["design_space_components"] = {"Wing1": {"area_m2": 130.0}}
    dp._write_design_json()
    assert design_space_of(d)["geometry"]["Wing1"]["area_m2"] == 130.0


def test_a_retry_sets_the_failed_attempts_flight_aside(link_dir):
    from scripts.stat_batch_runner import set_aside_design_json

    d, state = link_dir
    _fly(state, 122.79, 0.0262, 23787.2)
    set_aside_design_json(d, 2)
    assert not (d / "design_flown.json").exists()
    assert (d / "design_flown_attempt1.json").exists()


# -- the checker ---------------------------------------------------------------------------------------

def _row(tool, args, result):
    return {"time": 0.0, "agent": "a", "tool": tool, "args": args, "result": result, "error": None}


def _link(root, link, rows, carry=None):
    d = root / f"repeat_{link:03d}" / "mdo_f25_sequential_staged_pipeline"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"attempt": 1, "chain_link": link, "org_structure": "sequential"}))
    (d / "design.json").write_text(json.dumps({"aero": {"cd_cruise": 0.02}}))
    (d / "tool_calls_attempt1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    if carry is not None:
        (d / "carry_over.json").write_text(json.dumps(carry))


def _status(root, name, link):
    return {c[0]: c[1] for c in next(iter(check_sweep(root).values()))[link]}[name]


def test_checker_the_next_link_starting_from_an_unflown_area_fails(tmp_path):
    flown = [_row("set_aircraft_parameters", {"parameters": {"Aircraft.Wing.AREA": 122.788}}, {"valid": True}),
             _row("run_simulation", {}, {"summary": {"fuel_burned_kg": 23787.2}}),
             _row("set_aircraft_parameters", {"parameters": {"Aircraft.Wing.AREA": 150.0}}, {"valid": True})]
    _link(tmp_path, 0, flown)
    _link(tmp_path, 1, flown[:2], carry={"aviary_session_start": {"Aircraft.Wing.AREA": 150.0008}})
    assert _status(tmp_path, "link -> link aviary inputs", 1) == FAIL
    (tmp_path / "repeat_001" / "mdo_f25_sequential_staged_pipeline" / "carry_over.json").write_text(
        json.dumps({"aviary_session_start": {"Aircraft.Wing.AREA": 122.788}}))
    assert _status(tmp_path, "link -> link aviary inputs", 1) == PASS


def test_checker_mass_reading_the_closed_sessions_autoexport_passes(tmp_path):
    rows = [
        _row("open_cpacs", {"source": "/start.xml"}, {"session_id": "e442"}),
        _row("set_high_level_parameters", {"session_id": "e442", "component_uid": "Wing1", "updates": {"area": 115}},
             {"geometry_morph": {"after": {"reference_area": 115.0, "aspect_ratio": 9.0, "sweep_deg": 25.0,
                                           "taper_ratio": 0.246}}}),
        _row("export_cpacs", {"session_id": "e442"}, {"cpacs_file_path": "/x/D150_simple_modified.xml"}),
        _row("close_cpacs", {"session_id": "e442"}, {"cpacs_file_path": "/tmp/cpacs_autoexport_e442.xml"}),
        _row("estimate_mass", {"cpacs_file_path": "/t/avion_mass_1/cpacs_autoexport_e442.xml"},
             {"mass_breakdown": {"components": {"mWing_kg": 8160.5}}}),
        _row("set_aircraft_parameters", {"parameters": {"Aircraft.Wing.MASS": 8160.5}}, {"valid": True}),
        _row("run_simulation", {}, {"summary": {"fuel_burned_kg": 28102.6}}),
    ]
    _link(tmp_path, 0, rows)
    assert _status(tmp_path, "geometry -> mass", 0) == PASS
    rows[3]["args"]["session_id"] = "other"                  # a different session's auto-export
    (tmp_path / "repeat_000" / "mdo_f25_sequential_staged_pipeline" / "tool_calls_attempt1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows))
    assert _status(tmp_path, "geometry -> mass", 0) == FAIL


def test_checker_takes_the_last_verdict_from_design_json_not_the_flown_record(tmp_path):
    """l2c orch_graph 2026-11-10: the flown record ends at a CONTINUE to structures_analyst; the link's last
    word (design.json) is COMPLETE. The runner carries COMPLETE, so the checker must grade COMPLETE."""
    rec = {"kind": "recommendation", "agent": "mdo_integrator", "verdict": "CONTINUE",
           "recommendation_to": "structures_analyst", "recommended_change": "Adjust wing taper ratio"}
    done = {"kind": "recommendation", "agent": "mdo_integrator", "verdict": "COMPLETE",
            "recommendation_to": None, "recommended_change": ""}
    rows = [_row("run_simulation", {}, {"summary": {"fuel_burned_kg": 21698.6}})]
    _link(tmp_path, 0, rows)
    _link(tmp_path, 1, rows)
    d0 = tmp_path / "repeat_000" / "mdo_f25_sequential_staged_pipeline"
    (d0 / "design_flown.json").write_text(json.dumps({"coordination_signals": [rec]}))
    (d0 / "design.json").write_text(json.dumps({"coordination_signals": [rec, done]}))
    assert _status(tmp_path, "recommendation -> next link", 1) != FAIL
    from scripts.check_coupling import Link
    assert Link(d0).design["coordination_signals"][-1]["verdict"] == "COMPLETE"


def test_checker_skips_an_mcp_error_text_as_a_measurement(tmp_path):
    """l2c orch_iter 2026-11-10: attempt 2's first call hit a stale session; its error text was taken as the
    starting wing (all None) and link -> link wing failed although the next call measured the carried wing."""
    wing = {"area": 122.788, "aspect_ratio": 9.3693, "sweep": 27.3267, "taper_ratio": 0.2462}
    end = [_row("set_high_level_parameters", {"component_uid": "Wing1", "updates": {"area": 122.788}},
                {"geometry_morph": {"after": {"reference_area": 122.788, "aspect_ratio": 9.3693, "sweep_deg": 27.3267,
                                              "taper_ratio": 0.2462}}}),
           _row("run_simulation", {}, {"summary": {"fuel_burned_kg": 23885.5}})]
    start = [_row("get_high_level_parameters", {"component_uid": "Wing1"},
                  "Error calling tool 'get_high_level_parameters': Unknown session_id 'b3b3'"),
             _row("get_high_level_parameters", {"component_uid": "Wing1"}, {"parameters": wing}),
             _row("run_simulation", {}, {"summary": {"fuel_burned_kg": 27909.5}})]
    _link(tmp_path, 0, end)
    _link(tmp_path, 1, start)
    assert _status(tmp_path, "link -> link wing", 1) == PASS


def test_checker_a_mass_reply_with_status_error_is_not_a_mass(tmp_path):
    """l20 orch_graph link 2: the 4th estimate_mass (num_engines 4) failed with status "error" and all masses 0.0."""
    good = {"status": "success", "mass_breakdown": {"components": {"mWing_kg": 8735.3}}}
    bad = {"status": "error", "mass_breakdown": {"components": {"mWing_kg": 0.0}}}
    rows = [_row("open_cpacs", {"source": "/start.xml"}, {"session_id": "e442"}),
            _row("estimate_mass", {"cpacs_file_path": "/start.xml"}, good),
            _row("estimate_mass", {"cpacs_file_path": "/start.xml", "num_engines": 4}, bad),
            _row("set_aircraft_parameters", {"parameters": {"Aircraft.Wing.MASS": 8735.3}}, {"valid": True}),
            _row("run_simulation", {}, {"summary": {"fuel_burned_kg": 23192.0}})]
    _link(tmp_path, 0, rows)
    assert _status(tmp_path, "mass -> aviary", 0) == PASS
