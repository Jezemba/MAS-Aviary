"""Unit tests for analysis/link20/extract.py on synthetic link folders (no MCP, no model, no network)."""

import json

from analysis.link20 import extract as ex


def _write_link(d, kb, result=None, design=None, steps=None, carry=None, attempt=1):
    d.mkdir(parents=True)
    (d / "knowledge_base.jsonl").write_text("\n".join(json.dumps(e) for e in kb))
    if result is not None:
        (d / "result.json").write_text(json.dumps(result))
    (d / "design.json").write_text(json.dumps(design or {}))
    if carry is not None:
        (d / "carry_over.json").write_text(json.dumps(carry))
    (d / f"step_log_attempt{attempt}.jsonl").write_text("\n".join(json.dumps(s) for s in steps or []))


def _sim(seq, fuel, status="success", gtow=80000.0):
    return {"seq": seq, "agent": "a", "tool": "run_simulation", "status": status,
            "inputs": {}, "outputs": {"summary": {"fuel_burned_kg": fuel, "gtow_kg": gtow, "wing_mass_kg": 9000.0}}}


def test_link_fuel_is_last_successful_mission(tmp_path):
    d = tmp_path / "repeat_000" / "mdo_f25_sequential_staged_pipeline"
    _write_link(d, [_sim(1, 20000.0), _sim(2, 999.0, status="refused"), _sim(3, 18000.0, gtow=85000.0)], result={})
    row = ex.extract_link("t", d.name, 1, d, {"completed": {"r000_" + d.name: {}}})
    assert row["flew"] and row["fuel_kg"] == 18000.0 and row["gtow_kg"] == 85000.0
    assert row["first_fuel_kg"] == 20000.0 and row["n_missions_flown"] == 2
    assert row["run_simulation_refused"] == 1
    assert (row["structure"], row["handler"], row["run_status"]) == ("sequential", "staged_pipeline", "completed")


def test_zero_or_missing_fuel_did_not_fly(tmp_path):
    d = tmp_path / "repeat_000" / "mdo_f25_networked_iterative_feedback"
    _write_link(d, [_sim(1, 0.0)], result={})
    row = ex.extract_link("t", d.name, 1, d, {})
    assert row["flew"] is False and row["fuel_kg"] is None and row["gtow_kg"] is None
    assert row["run_status"] == "in_progress"
    d2 = tmp_path / "repeat_001" / "mdo_f25_networked_iterative_feedback"
    _write_link(d2, [_sim(1, 0.0, status="failed")], result=None)
    row2 = ex.extract_link("t", d2.name, 2, d2, {"failed": {"r001_" + d2.name: {"error": "zero fuel", "attempts": 3}}})
    assert row2["flew"] is False and row2["first_fuel_kg"] is None
    assert row2["run_status"] == "failed" and row2["attempt"] == 3 and row2["has_result"] is False


def test_b187_detected_from_thrust_and_scale():
    kb = [{"seq": 1, "tool": "run_cycle", "status": "success", "outputs": {"outputs": {"perf.Fn": 5899.99999}}},
          {"seq": 2, "tool": "run_cycle", "status": "success", "outputs": {"outputs": {"perf.Fn": 8118.2}}},
          {"seq": 3, "tool": "set_aircraft_parameters", "status": "success",
           "inputs": {"parameters": {"Aircraft.Engine.SCALE_FACTOR": 0.9939353}}},
          {"seq": 4, "tool": "set_aircraft_parameters", "status": "success",
           "inputs": {"parameters": {"Aircraft.Engine.SCALE_FACTOR": 1.3676}}}]
    hits = ex.b187_hits(kb)
    assert len(hits) == 2 and "seq 1" in hits[0] and "seq 3" in hits[1]


def test_b214_only_fixture_paths():
    kb = [{"seq": 1, "tool": "export_cpacs", "inputs": {"output_path": "/x/MAS-Aviary/tests/fixtures/D150.xml"}},
          {"seq": 2, "tool": "export_cpacs", "inputs": {"output_path": "/x/mass-mcp/generated/D150.xml"}}]
    assert ex.b214_hits(kb) == ["export_cpacs -> /x/MAS-Aviary/tests/fixtures/D150.xml (seq 1)"]


def test_backward_moves_ignore_orchestrator():
    route = ["orchestrator", "geometry_engineer", "mission_architect", "simulation_executor",
             "mdo_integrator", "structures_analyst", "mission_architect", "geometry_engineer"]
    assert ex.backward_moves(route) == 2


def test_counted_attempt_is_highest_step_log_and_steps_exclude_summaries(tmp_path):
    d = tmp_path / "repeat_002" / "mdo_f25_orchestrated_iterative_feedback"
    steps = [{"kind": "step", "agent": "orchestrator", "generations": [{"output_tokens": 10}],
              "tool_calls": [{"name": "assign_task"}, {"name": "record_recommendation"}]},
             {"kind": "summary", "agent": "unknown", "generations": [{"output_tokens": 500}]}]
    _write_link(d, [], result={"attempt": 2}, steps=steps, attempt=2,
                design={"coordination_signals": [
                    {"kind": "recommendation", "verdict": "continue", "recommendation_to": "geometry_engineer"},
                    {"kind": "response"}]},
                carry={"recommendation": {"recommendation_to": "mission_architect", "recommended_change": "x"}})
    (d / "step_log_attempt1.jsonl").write_text(json.dumps({"kind": "step", "agent": "z", "generations": []}))
    row = ex.extract_link("t", d.name, 3, d, {})
    assert row["step_log_attempts"] == 2 and row["agent_steps"] == 1 and row["output_tokens"] == 10
    assert row["n_assign_task"] == 1 and row["n_record_recommendation"] == 1
    assert row["verdict_continue"] == 1 and row["n_responses"] == 1 and row["last_rec_to"] == "geometry_engineer"
    assert row["inherited_rec_to"] == "mission_architect"


def test_b187_split_into_cycle_and_inherited(tmp_path):
    scale = {"seq": 9, "tool": "set_aircraft_parameters", "status": "success",
             "inputs": {"parameters": {"Aircraft.Engine.SCALE_FACTOR": 0.99393531}}}
    cycle = {"seq": 7, "tool": "run_cycle", "status": "success", "outputs": {"outputs": {"perf.Fn": 5900.0}}}
    d1 = tmp_path / "repeat_000" / "mdo_f25_sequential_graph_routed"
    _write_link(d1, [scale], result={})
    r1 = ex.extract_link("t", d1.name, 1, d1, {})
    assert r1["b187"] and r1["b187_inherited_only"] and not r1["b187_cycle"]
    d2 = tmp_path / "repeat_001" / "mdo_f25_sequential_graph_routed"
    _write_link(d2, [cycle, scale], result={})
    r2 = ex.extract_link("t", d2.name, 2, d2, {})
    assert r2["b187_cycle"] and not r2["b187_inherited_only"]


def test_graph_route_from_message_metadata(tmp_path):
    d = tmp_path / "repeat_000" / "mdo_f25_sequential_graph_routed"
    turns = [{"node": "mission_architect", "level": "simple", "next_level": "simple"},
             {"node": "simulation_executor", "level": "simple", "next_level": "moderate"},
             {"node": "structures_analyst", "level": "moderate", "next_level": "complex"},
             {"node": "mission_architect", "level": "complex", "next_level": "complex"}]
    msgs = [{"metadata": {"graph_route": t}} for t in turns]
    msgs[-1]["metadata"]["graph_stop_reason"] = "budget of 25 role runs at complex spent"
    _write_link(d, [], result={"messages": msgs})
    row = ex.extract_link("t", d.name, 1, d, {})
    assert row["route_length"] == 4 and row["backward_moves"] == 1
    assert row["graph_level"] == "complex" and row["graph_level_changes"] == 2
    assert row["graph_stop_reason"].startswith("budget")


def test_claims_granted_and_refused_from_observations(tmp_path):
    d = tmp_path / "repeat_000" / "mdo_f25_networked_iterative_feedback"
    steps = [{"kind": "step", "agent": "agent_1", "tool_calls": [{"name": "claim_todo"}],
              "observations": '{"success": true, "todo_name": "aero", "attempted_by": "agent_1"}'},
             {"kind": "step", "agent": "agent_2", "tool_calls": [{"name": "claim_todo"}],
              "observations": '{"success": false, "todo_name": "aero", "attempted_by": "agent_2"}'}]
    _write_link(d, [], result={}, steps=steps)
    row = ex.extract_link("t", d.name, 1, d, {})
    assert (row["n_claim_granted"], row["n_claim_refused"]) == (1, 1)
