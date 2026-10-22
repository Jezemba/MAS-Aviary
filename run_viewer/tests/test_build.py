"""Unit + integration tests for the run viewer extractor, on a small synthetic run."""

import json
import os

import pytest

from run_viewer.extract import build, snapshot

T0 = 1_790_000_000.0


def _step(agent, k, t, tools=(), obs=None, out="text", prompt_tokens=100, output_tokens=10):
    return {"seq": k, "kind": "step", "agent": agent, "agent_step": k, "start_time": t, "end_time": t + 5,
            "generations": [{"attempt": 0, "thinking_enabled": False,
                             "prompt": {"messages": [], "prompt_tokens": prompt_tokens},
                             "visible": out, "output_tokens": output_tokens}],
            "tool_calls": [{"name": n, "arguments": a} for n, a in tools],
            "observations": json.dumps(obs) if obs is not None else None, "error": None,
            "is_final_answer": False}


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        if path.endswith(".jsonl"):
            f.write("\n".join(json.dumps(r) for r in obj) + "\n")
        else:
            json.dump(obj, f)


def _design(area, cd, sid="sid-1"):
    return {"mission": {"range_nmi": 2500, "num_passengers": 239, "cruise_mach": 0.78, "cruise_altitude_ft": 33000},
            "design_space": {"engine": {"T4_MAX": 2857.0, "splitter.BPR": 5.1}},
            "aero": {"cl_cruise": 0.55, "cd_cruise": cd}, "propulsion": {"sfc_cruise": 0.64},
            "mission_applied": {"Aircraft.Wing.AREA": area}, "sessions": {"aviary": sid},
            "coordination_signals": []}


def _result(fuel, flown=True, messages=None):
    return {"name": "mdo_f25_networked_graph_routed", "status": "success", "duration_seconds": 600,
            "total_turns": 2, "total_tokens": 4,
            "token_breakdown": {"model_id": "test-model", "total_tokens": 4},
            "eval_classification": {"fuel_burned_kg": fuel if flown else None, "gtow_kg": 1.0, "converged": False},
            "fuel_measured_kg": fuel if flown else None,
            "org_theory_metrics": {"misroute_rate": 0.5, "states_visited": ["a", "b"], "warnings": ["x"],
                                   "convergence_classification": "healthy_diversity"},
            "cross_strategy_metrics": {"coordination_overhead": 1.0},
            "messages": messages or []}


@pytest.fixture
def run_dir(tmp_path):
    root = tmp_path / "run"
    combo = "mdo_f25_networked_graph_routed"
    _write(str(root / "chain_anchor.json"), {"n_links": 2, "mode": "cumulative_chain", "base_seed": 42,
                                             "anchor_params": {"Aircraft.Wing.AREA": 120.0}})
    l1 = root / "repeat_000" / combo
    _write(str(l1 / "design.json"), _design(121.0, 0.022))
    _write(str(l1 / "result.json"), _result(20000.0, messages=[
        {"agent_name": "geometry_engineer", "turn_number": 1, "timestamp": T0 + 10, "duration_seconds": 10,
         "metadata": {"graph_route": {"node": "geometry_engineer", "next": "aero"}}},
        {"agent_name": "geometry_engineer", "turn_number": 2, "timestamp": T0 + 30, "duration_seconds": 10,
         "metadata": {}}]))
    _write(str(l1 / "knowledge_base.jsonl"), [
        {"seq": 1, "time_utc": "2026-09-21T14:13:25Z", "agent": "a", "tool": "run_simulation", "server": "aviary",
         "status": "success", "outputs": {"flown": True, "summary": {"fuel_burned_kg": 20000.0, "gtow_kg": 9.0}}},
        {"seq": 2, "time_utc": "2026-09-21T14:13:26Z", "agent": "a", "tool": "set_inputs", "server": "pycycle",
         "status": "refused", "outputs": {"error_code": "MISSING_DATA"}}])
    _write(str(l1 / "step_log_attempt1.jsonl"), [
        _step("peer_b", 2, T0 + 3, [("claim_todo", {"todo_name": "AERO"})], obs={"success": False}),
        _step("peer_a", 1, T0 + 1, [("claim_todo", {"todo_name": "GEOM"})], obs={"success": True}),
        _step("peer_a", 3, T0 + 6, [("mark_todo_failed", {"todo_name": "GEOM", "reason": "mesh broke"})],
              obs={"success": True}),
        {"seq": 9, "kind": "summary", "agent": "framework"},
    ])
    l2 = root / "repeat_001" / combo
    _write(str(l2 / "design.json"), _design(119.0, 0.021))
    _write(str(l2 / "result.json"), _result(None, flown=False))
    _write(str(l2 / "carry_over.json"), {"recommendation": {"agent": "mdo_integrator",
                                                            "recommendation_to": "geometry_engineer",
                                                            "verdict": "RETRY",
                                                            "recommended_change": "reduce area"}})
    _write(str(l2 / "step_log_attempt1.jsonl"), [_step("peer_a", 1, T0 + 100)])
    _write(str(l2 / "step_log_attempt2.jsonl"), [_step("peer_a", 1, T0 + 200), _step("peer_a", 2, T0 + 210)])
    return root, combo


def test_steps_sorted_by_time_and_summary_rows_dropped(run_dir):
    root, combo = run_dir
    rows = build._load_jsonl(str(root / "repeat_000" / combo / "step_log_attempt1.jsonl"))
    steps = build.build_steps(rows)
    assert [s["agent_step"] for s in steps] == [1, 2, 3]
    assert [s["i"] for s in steps] == [0, 1, 2]
    assert steps[0]["prompt_tokens"] == 100 and steps[0]["output_tokens"] == 10


def test_half_written_jsonl_line_is_skipped(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n{"a": 2')
    assert build._load_jsonl(str(p)) == [{"a": 1}]


def test_board_comm_and_counts_use_real_tool_names(run_dir):
    root, combo = run_dir
    steps = build.build_steps(build._load_jsonl(str(root / "repeat_000" / combo / "step_log_attempt1.jsonl")))
    comm = build.build_comm(steps, "networked")
    assert [(c["agent"], c["tool"], c["todo"], c["ok"]) for c in comm] == [
        ("peer_a", "claim_todo", "GEOM", True), ("peer_b", "claim_todo", "AERO", False),
        ("peer_a", "mark_todo_failed", "GEOM", True)]
    assert comm[2]["text"] == "mesh broke"
    m = build.step_metrics(steps, [], [], "networked", "graph_routed")
    assert m[-1]["claim_todo"] == 2 and m[-1]["mark_todo_failed"] == 1 and m[-1]["claim_conflicts"] == 1


def test_orchestrator_messages_have_direction():
    steps = build.build_steps([
        _step("structures_analyst", 1, T0, [("message_orchestrator", {"message": "need mesh"})]),
        _step("orchestrator", 2, T0 + 9, [("reply_to_agent", {"agent_name": "structures_analyst",
                                                              "message": "geometry first"})])])
    comm = build.build_comm(steps, "orchestrated")
    assert [(c["src"], c["dst"], c["text"]) for c in comm] == [
        ("structures_analyst", "orchestrator", "need mesh"), ("orchestrator", "structures_analyst", "geometry first")]


def test_graph_running_misroute_matches_repo_formula():
    turns = [{"agent": n, "start": T0 + i, "route": {"node": n}, "completion_met": True} for i, n in
             enumerate(["a", "b", "a", "c"])]
    steps = build.build_steps([_step("a", 1, T0 + 10)])
    m = build.step_metrics(steps, turns, [], "sequential", "graph_routed")[-1]
    # repo: revisits / transitions = 1 / 3
    assert m["states_visited"] == 3 and m["total_transitions"] == 3 and m["misroute_rate"] == round(1 / 3, 4)


def test_did_not_fly_is_null_not_zero(run_dir):
    root, combo = run_dir
    link = root / "repeat_001" / combo
    s = build.link_summary(build._load_json(str(link / "result.json")), None, [], [])
    assert s["flown"] is False and s["fuel_burned_kg"] is None and s["gtow_kg"] is None


def test_coordination_keeps_numbers_and_counts_lists(run_dir):
    root, combo = run_dir
    s = build.link_summary(build._load_json(str(root / "repeat_000" / combo / "result.json")), None, [], [])
    assert s["coordination"] == {"misroute_rate": 0.5, "states_visited_count": 2}


def test_build_run_end_to_end(run_dir, tmp_path):
    root, combo = run_dir
    out = tmp_path / "bundle"
    env = build.ImageEnv(None, None, None)
    run = build.RunBuilder(str(root), combo, str(out), None, env, "test__N_GR").build()
    assert run["label"] == "N_GR" and run["inputs"]["links_planned"] == 2
    assert run["inputs"]["model"] == "test-model"
    assert [lk["fuel_burned_kg"] for lk in run["links"]] == [20000.0, None]
    link1 = json.load(open(out / "links" / "001.json"))
    assert link1["step_metrics"][-1]["mcp_refusals"] == 1
    assert link1["params_start"]["geometry"][0]["value"] == 120.0  # chain anchor
    assert link1["images"]["trajectory"]["status"] == "aviary recorder not saved for this session"
    assert link1["images"]["su2"]["status"] == "no successful SU2 solve in this link"
    link2 = json.load(open(out / "links" / "002.json"))
    assert link2["attempt"] == 2 and len(link2["steps"]) == 2  # last attempt is the result
    assert link2["params_start"]["geometry"][0]["value"] == 121.0  # = link 1's end
    assert link2["params_end"]["geometry"][0]["value"] == 119.0
    assert link2["recommendation"]["to"] == "geometry_engineer"
    # A rebuild with unchanged files reuses the cache.
    mtime = os.path.getmtime(out / "links" / "002.json")
    build.RunBuilder(str(root), combo, str(out), None, env, "test__N_GR").build()
    assert os.path.getmtime(out / "links" / "002.json") == mtime


def test_live_link_without_result_is_running(run_dir, tmp_path):
    root, combo = run_dir
    os.remove(root / "repeat_001" / combo / "result.json")
    run = build.RunBuilder(str(root), combo, str(tmp_path / "b"), None, build.ImageEnv(None, None, None),
                           "t").build()
    assert run["links"][1]["status"] == "running" and run["links"][1]["complete"] is False


def test_snapshot_copies_only_from_sources(run_dir, tmp_path):
    root, combo = run_dir
    link = root / "repeat_000" / combo
    aviary = tmp_path / "aviary"
    _write(str(aviary / "aviary_sid-1_out" / "problem_history.db"), {"fake": 1})
    su2 = tmp_path / "su2_session_x"
    for f in ("surface_flow.csv", "config.cfg"):
        (su2.parent / su2.name).mkdir(exist_ok=True)
        (su2 / f).write_text("x")
    with open(link / "knowledge_base.jsonl", "a") as f:
        f.write(json.dumps({"seq": 7, "tool": "run_su2_solver", "status": "success",
                            "outputs": {"config_used": str(su2 / "config.cfg")}}) + "\n")
    found = snapshot.snapshot_link(str(link), str(tmp_path / "snap"), str(aviary), None)
    assert found["aviary"] == ["sid-1"] and found["su2"] == ["0007_su2_session_x"]
    assert (tmp_path / "snap" / "su2" / "0007_su2_session_x" / "surface_flow.csv").exists()
    assert (su2 / "surface_flow.csv").exists()  # source untouched
