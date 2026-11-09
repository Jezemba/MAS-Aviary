"""scripts/check_coupling.py and the tool call log it reads. No server, no model: files only."""

from __future__ import annotations

import json

import pytest

from scripts.check_coupling import FAIL, NOT_EXERCISED, PASS, check_sweep
from src.logging import step_log
from src.tools import call_watchdog


def _row(tool, args, result):
    return {"time": 0.0, "agent": "a", "tool": tool, "args": args, "result": result, "error": None}


def _flight():
    return [
        _row("open_cpacs", {"source": "/start.xml"}, {"session_id": "t"}),
        _row("set_high_level_parameters", {"session_id": "t", "component_uid": "Wing1", "updates": {"area": 130.0}},
             {"geometry_morph": {"after": {"reference_area": 130.0, "aspect_ratio": 10.0, "sweep_deg": 21.65, "taper_ratio": 0.246}}}),
        _row("generate_volume_mesh", {"session_id": "t"}, {"statistics": {}}),
        _row("export_cpacs", {"session_id": "t"}, {"cpacs_file_path": "/x/morph.xml"}),
        _row("create_su2_session", {}, {"session_id": "s", "references": {"REF_AREA": 130.0}, "drag_floor": {"aspect_ratio": 10.0}}),
        _row("set_mesh", {"session_id": "s"}, {"mesh_path": "m"}),
        _row("run_su2_solver", {"session_id": "s"}, {"CL": 0.55, "CD": 0.02, "ref_area": 130.0}),
        _row("estimate_mass", {"cpacs_file_path": "/x/morph.xml"}, {"mass_breakdown": {"components": {"mWing_kg": 9000.0}}}),
        _row("set_inputs", {"session_id": "c", "values": {"Fn_DES": 8100.0}}, {"updated": ["Fn_DES"]}),
        _row("set_aircraft_parameters", {"session_id": "av", "parameters": {
            "Aircraft.Wing.AREA": 130.0, "Aircraft.Wing.ASPECT_RATIO": 10.0, "Aircraft.Wing.SWEEP": 21.65,
            "Aircraft.Wing.TAPER_RATIO": 0.246, "Aircraft.Wing.MASS": 9000.0, "Mission.Design.LIFT_COEFFICIENT": 0.55,
            "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR": 1.1, "Aircraft.Engine.SCALE_FACTOR": 1.38}}, {"valid": True}),
        _row("run_simulation", {"session_id": "av"}, {"summary": {"fuel_burned_kg": 21000.0},
                                                       "engine": {"deck": "/nowhere/d.deck", "external_deck": True, "scale_factor": 1.38}}),
    ]


def _write(root, link, rows, *, design=None, result=None, combo="mdo_f25_sequential_staged_pipeline", attempt=1, trace=None):
    d = root / f"repeat_{link:03d}" / combo
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"attempt": attempt, "chain_link": link, "org_structure": "sequential", **(result or {})}))
    (d / "design.json").write_text(json.dumps({"aero": {"cd_cruise": 0.02}, **(design or {})}))
    (d / f"tool_calls_attempt{attempt}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    if trace:
        (d / "trace.json").write_text(json.dumps(trace))


def _status(root, name, link=0):
    combo = next(iter(check_sweep(root).values()))
    return {c[0]: c[1] for c in combo[link]}[name]


def test_a_coupled_link_passes_every_check(tmp_path):
    _write(tmp_path, 0, _flight())
    checks = next(iter(check_sweep(tmp_path).values()))[0]
    assert [c for c in checks if c[1] == FAIL] == []
    assert sum(c[1] == PASS for c in checks) == 8


def test_b253_the_old_reference_area_fails(tmp_path):
    rows = _flight()
    rows[4]["result"]["references"]["REF_AREA"] = 125.86
    _write(tmp_path, 0, rows)
    assert _status(tmp_path, "geometry -> SU2 references") == FAIL


def test_a_solve_on_the_mesh_from_before_the_reshape_fails(tmp_path):
    rows = _flight()
    rows[1], rows[2] = rows[2], rows[1]
    _write(tmp_path, 0, rows)
    assert _status(tmp_path, "geometry -> SU2 mesh") == FAIL


def test_a_wing_not_flown_as_measured_fails(tmp_path):
    rows = _flight()
    rows[9]["args"]["parameters"]["Aircraft.Wing.SWEEP"] = 14.35
    _write(tmp_path, 0, rows)
    assert _status(tmp_path, "geometry -> aviary") == FAIL


def test_wrong_aero_mass_or_stale_mass_fails(tmp_path):
    rows = _flight()
    rows[9]["args"]["parameters"]["Mission.Design.LIFT_COEFFICIENT"] = 0.60
    rows[9]["args"]["parameters"]["Aircraft.Wing.MASS"] = 8000.0
    rows[7]["args"]["cpacs_file_path"] = "/baseline.xml"
    _write(tmp_path, 0, rows)
    assert _status(tmp_path, "SU2 -> aviary") == FAIL
    assert _status(tmp_path, "mass -> aviary") == FAIL
    assert _status(tmp_path, "geometry -> mass") == FAIL


def test_the_tool_default_thrust_fails(tmp_path):
    rows = _flight()
    rows[8]["args"]["values"]["Fn_DES"] = 5900.0
    _write(tmp_path, 0, rows)
    assert _status(tmp_path, "mass -> pycycle") == FAIL


def test_b254_a_deck_designed_at_the_wrong_flight_state_fails(tmp_path):
    deck = tmp_path / "d.deck"
    (tmp_path / "d.json").write_text(json.dumps({"design_inputs": {"fc.MN": {"value": 0.8}, "fc.alt": {"value": 35000.0}},
                                                 "design": {"TSFC": 0.652}}))
    rows = _flight()
    rows[10]["result"]["engine"]["deck"] = str(deck)
    root = tmp_path / "sweep"
    _write(root, 0, rows, design={"propulsion": {"sfc_cruise": 0.643}})
    assert _status(root, "pycycle -> aviary") == FAIL


def test_a_mission_without_the_designed_engine_fails(tmp_path):
    rows = _flight()
    rows[10]["result"]["engine"] = {"external_deck": False}
    _write(tmp_path, 0, rows)
    assert _status(tmp_path, "pycycle -> aviary") == FAIL


def test_an_untaken_path_is_not_called_a_pass(tmp_path):
    rows = [r for r in _flight() if r["tool"] in ("open_cpacs", "set_aircraft_parameters", "run_simulation")]
    _write(tmp_path, 0, rows)
    assert _status(tmp_path, "geometry -> SU2 mesh") == NOT_EXERCISED
    assert _status(tmp_path, "SU2 -> aviary") == NOT_EXERCISED
    assert _status(tmp_path, "mass -> aviary") == NOT_EXERCISED


def test_a_link_with_no_mission_says_so(tmp_path):
    _write(tmp_path, 0, _flight()[:3])
    assert _status(tmp_path, "mission") == NOT_EXERCISED


def test_a_run_without_the_log_fails_loudly(tmp_path):
    _write(tmp_path, 0, [])
    (tmp_path / "repeat_000" / "mdo_f25_sequential_staged_pipeline" / "tool_calls_attempt1.jsonl").unlink()
    assert _status(tmp_path, "tool call log") == FAIL


REC = {"kind": "recommendation", "verdict": "CONTINUE", "recommendation_to": "geometry_engineer",
       "recommended_change": "Wing1 aspect_ratio 9.36 -> 10.0"}


def _prompted(agent, text):
    return {agent: [{"step_number": 1, "model_input_messages": [{"content": text}]}]}


def _two_links(tmp_path, trace, org="sequential", recorded=REC):
    first = _flight()
    _write(tmp_path, 0, first, design={"coordination_signals": [recorded], "design_space": {"engine": {"fan.PR": 1.6}}},
           result={"chain_cpacs_out": "/x/end.xml", "org_structure": org})
    second = _flight()
    second[0]["args"]["source"] = "/x/end.xml"
    second[8]["args"]["values"]["fan.PR"] = 1.6
    _write(tmp_path, 1, second, result={"org_structure": org}, trace=trace)


def test_the_recommendation_reaches_the_role_it_names(tmp_path):
    _two_links(tmp_path, _prompted("geometry_engineer", "RECOMMENDED_CHANGE: Wing1 aspect_ratio 9.36 ->\n 10.0"))
    assert _status(tmp_path, "recommendation -> next link", 1) == PASS
    assert _status(tmp_path, "link -> link geometry file", 1) == PASS
    assert _status(tmp_path, "link -> link engine", 1) == PASS


def test_a_recommendation_in_the_wrong_agents_prompt_fails_in_sequential(tmp_path):
    _two_links(tmp_path, _prompted("mission_architect", "Wing1 aspect_ratio 9.36 -> 10.0"))
    assert _status(tmp_path, "recommendation -> next link", 1) == FAIL


def test_a_lost_recommendation_fails(tmp_path):
    _two_links(tmp_path, _prompted("geometry_engineer", "nothing about it"))
    assert _status(tmp_path, "recommendation -> next link", 1) == FAIL


def test_delivery_to_the_orchestrator_passes_what_it_does_next_is_not_checked(tmp_path):
    _two_links(tmp_path, _prompted("orchestrator", "Wing1 aspect_ratio 9.36 -> 10.0"), org="orchestrated")
    assert _status(tmp_path, "recommendation -> next link", 1) == PASS


def test_a_complete_verdict_has_nothing_to_deliver(tmp_path):
    _two_links(tmp_path, _prompted("geometry_engineer", "x"), recorded=dict(REC, verdict="COMPLETE"))
    assert _status(tmp_path, "recommendation -> next link", 1) == NOT_EXERCISED


def test_a_wrong_start_file_or_lost_engine_fails(tmp_path):
    _two_links(tmp_path, _prompted("geometry_engineer", "Wing1 aspect_ratio 9.36 -> 10.0"))
    log = tmp_path / "repeat_001" / "mdo_f25_sequential_staged_pipeline" / "tool_calls_attempt1.jsonl"
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    rows[0]["args"]["source"] = "/start.xml"
    rows[8]["args"]["values"].pop("fan.PR")
    log.write_text("\n".join(json.dumps(r) for r in rows))
    assert _status(tmp_path, "link -> link geometry file", 1) == FAIL
    assert _status(tmp_path, "link -> link engine", 1) == FAIL


# -- the log itself ------------------------------------------------------------------------------------

@pytest.fixture
def step_log_on(tmp_path):
    step_log.configure(tmp_path / "step_log_attempt1.jsonl")
    yield tmp_path / "tool_calls_attempt1.jsonl"
    step_log.configure(None)


def test_every_call_through_the_watchdog_is_logged_as_received(step_log_on):
    call_watchdog.call("run_simulation", lambda **kw: json.dumps({"summary": {"fuel_burned_kg": 21000.0}}), session_id="av")
    row = json.loads(step_log_on.read_text())
    assert row["tool"] == "run_simulation" and row["args"] == {"session_id": "av"}
    assert row["result"]["summary"]["fuel_burned_kg"] == 21000.0


def test_a_mesh_payload_is_not_written_out(step_log_on):
    call_watchdog.call("generate_volume_mesh", lambda **kw: json.dumps({"mesh_base64": "A" * 50000, "statistics": {"element_count": 7}}),
                       session_id="t")
    assert step_log_on.stat().st_size < 2000
    assert json.loads(step_log_on.read_text())["result"]["statistics"]["element_count"] == 7


def test_a_failed_call_is_logged_and_still_raises(step_log_on):
    def boom(**_):
        raise RuntimeError("no such session")

    with pytest.raises(RuntimeError):
        call_watchdog.call("run_cycle", boom, session_id="c")
    assert "no such session" in json.loads(step_log_on.read_text())["error"]


def test_with_the_log_off_nothing_is_written(tmp_path):
    step_log.configure(None)
    assert call_watchdog.call("run_cycle", lambda **kw: 1, session_id="c") == 1
    assert list(tmp_path.iterdir()) == []
