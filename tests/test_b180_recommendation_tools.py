"""B180: the recommendation, the responses and the complexity request are typed tool calls, not text keys.

The framework kept losing the integrator's recommendation to spelling, markdown and prose (B172, B174, B177,
B178). Tool-call arguments are JSON against a schema; consumers read those records first, text only as the
fallback for an agent that never called the tool.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import recommendation_tools as rt
from src.tools.agent_context import agent_scope
from src.tools.data_plane import get_design_state, init_data_plane


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), {})
    yield
    init_data_plane(DesignState(), {})


def test_the_integrator_records_a_typed_recommendation():
    with agent_scope("mdo_integrator"):
        out = json.loads(rt.RecordRecommendation().forward("continue", "Aerodynamics Analyst",
                                                           "Wing1 aspect_ratio 9.4 -> 10.5"))
    assert out["recorded"] and out["verdict"] == "CONTINUE" and out["recommendation_to"] == "aerodynamics_analyst"
    rec = rt.latest_recommendation()
    assert rec["agent"] == "mdo_integrator" and rec["recommended_change"] == "Wing1 aspect_ratio 9.4 -> 10.5"


def test_bad_arguments_are_refused_with_the_reason():
    with agent_scope("mdo_integrator"):
        assert "verdict must be one of" in rt.RecordRecommendation().forward("MAYBE", "geometry_engineer", "x")
        assert "recommendation_to must be one of" in rt.RecordRecommendation().forward("CONTINUE", "the wing team", "x")
        assert "recommended_change is empty" in rt.RecordRecommendation().forward("RETRY", "geometry_engineer", "")
        assert json.loads(rt.RecordRecommendation().forward("COMPLETE"))["recorded"]
    with agent_scope("geometry_engineer"):
        assert "Only the mdo_integrator" in rt.RecordRecommendation().forward("CONTINUE", "geometry_engineer", "x")
    assert len(rt.signals("recommendation")) == 1


def test_responses_and_complexity_are_attributed_and_ordered():
    with agent_scope("geometry_engineer"):
        rt.RespondToRecommendation().forward("aspect ratio 9.4 -> 10.5")
        rt.RequestComplexity().forward("complex", "the wing changes")
        assert "level must be one of" in rt.RequestComplexity().forward("huge", "x")
    with agent_scope("aerodynamics_analyst"):
        rt.RespondToRecommendation().forward("re-solved at the new geometry")
    assert [(r["agent"], r["how_addressed"]) for r in rt.responses_since(0)] == [
        ("geometry_engineer", "aspect ratio 9.4 -> 10.5"), ("aerodynamics_analyst", "re-solved at the new geometry")]
    assert rt.complexity_requests_since(0)[0]["level"] == "complex"


def test_design_json_carries_the_signals_and_the_carry_over_takes_the_recommendation(tmp_path):
    import scripts.stat_batch_runner as sbr
    from src.tools.design_json import build_design

    with agent_scope("mdo_integrator"):
        rt.RecordRecommendation().forward("CONTINUE", "propulsion_analyst", "splitter.BPR 5.1 -> 6.0")
    state = get_design_state()
    prev = tmp_path / "repeat_000" / "combo"
    prev.mkdir(parents=True)
    (prev / "design.json").write_text(json.dumps(build_design(state.data_store, state.sessions)))
    link = tmp_path / "repeat_001" / "combo"
    sbr.write_carry_over(link, 1, "prose with no fields", None, {}, None)
    carry = json.loads((link / "carry_over.json").read_text())
    assert carry["recommendation"]["recommended_change"] == "splitter.BPR 5.1 -> 6.0"


def test_every_agent_sees_the_recorded_recommendation_even_when_the_text_has_no_fields():
    from src.coordination import prompt_assembly

    carry = {"previous_integrator_design_state": "## MDO assessment\nThe engine is too small, raise the BPR.",
             "recommendation": {"kind": "recommendation", "seq": 3, "verdict": "CONTINUE",
                                "recommendation_to": "propulsion_analyst",
                                "recommended_change": "splitter.BPR 5.1 -> 6.0"}}
    with agent_scope("propulsion_analyst"):
        rt.RespondToRecommendation().forward("BPR set to 6.0")
    out = prompt_assembly.verbatim_stage_input("ROLE: MISSION", [("propulsion_analyst", "DESIGN_STATE: ok", None)],
                                               carry, "mission_architect")
    assert ("RECOMMENDATION from the previous iteration (mdo_integrator, recorded with record_recommendation):\n"
            "  RECOMMENDATION_TO: propulsion_analyst\n  RECOMMENDED_CHANGE: splitter.BPR 5.1 -> 6.0") in out
    assert "RECOMMENDATION_IMPLEMENTED (reported by propulsion_analyst): BPR set to 6.0" in out


def test_the_graph_routes_on_the_recorded_recommendation():
    from src.coordination import graph_router as gr

    recorded = {"verdict": "CONTINUE", "recommendation_to": "aerodynamics_analyst",
                "recommended_change": "Wing1 aspect_ratio 9.4 -> 10.5"}
    d = gr.route_after_review("prose only, no fields", gr.SIMPLE, recorded)
    assert (d.next_node, d.level) == (gr.GEOMETRY, gr.COMPLEX)
    assert gr.route_after_review("VERDICT: CONTINUE", gr.SIMPLE, {"verdict": "COMPLETE"}).next_node == gr.COMPLETE
    ok = [{"tool": "set_aircraft_parameters", "status": "success", "outputs": {"valid": True}}]
    d = gr.route_after_node(gr.MISSION, ok, "no field in the text", gr.SIMPLE, 0, requested_level=gr.MODERATE)
    assert (d.next_node, d.level) == (gr.AERO, gr.MODERATE)


def test_the_integrator_gate_reminds_twice_then_lets_it_finish(monkeypatch):
    from types import SimpleNamespace

    from src.tools import artifact_checks as ac

    agent = SimpleNamespace(name="mdo_integrator", tools={"record_recommendation": 1})
    monkeypatch.delenv("MAS_REQUIRE_RECORDED_RECOMMENDATION", raising=False)      # on by default
    for n in (1, 2):
        with pytest.raises(ac.MissingRecordedRecommendation, match=f"reminder {n} of 2"):
            ac.recommendation_recorded_before_finish("x", None, agent)
    assert ac.recommendation_recorded_before_finish("x", None, agent) is True       # then it may finish
    assert get_design_state().data_store["finished_without_recorded_recommendation"] == ["mdo_integrator"]
    # Recording it satisfies the gate; and it can be switched off.
    agent2 = SimpleNamespace(name="mdo_integrator", tools={"record_recommendation": 1})
    with agent_scope("mdo_integrator"):
        rt.RecordRecommendation().forward("COMPLETE")
    assert ac.recommendation_recorded_before_finish("x", None, agent2) is True
    monkeypatch.setenv("MAS_REQUIRE_RECORDED_RECOMMENDATION", "0")
    agent3 = SimpleNamespace(name="mdo_integrator", tools={"record_recommendation": 1}, _avion_recommendation_since=99)
    assert ac.recommendation_recorded_before_finish("x", None, agent3) is True


def test_the_addressed_role_must_answer_the_recommendation(monkeypatch):
    from types import SimpleNamespace

    from src.coordination import prompt_assembly
    from src.tools import artifact_checks as ac

    monkeypatch.delenv("MAS_REQUIRE_RECORDED_RECOMMENDATION", raising=False)
    carry = {"recommendation": {"verdict": "CONTINUE", "recommendation_to": "propulsion_analyst",
                                "recommended_change": "splitter.BPR 5.1 -> 6.0"}}
    prompt_assembly.verbatim_stage_input("ROLE: X", [], carry, "geometry_engineer")       # shows it -> active
    prop = SimpleNamespace(name="propulsion_analyst", tools={"respond_to_recommendation": 1})
    geo = SimpleNamespace(name="geometry_engineer", tools={"respond_to_recommendation": 1})
    assert ac.recommendation_response_before_finish("x", None, geo) is True              # not addressed to it
    with pytest.raises(ac.MissingRecommendationResponse, match="addressed to your role"):
        ac.recommendation_response_before_finish("x", None, prop)
    with agent_scope("propulsion_analyst"):
        rt.RespondToRecommendation().forward("this is a geometry change; handed to geometry")
    assert ac.recommendation_response_before_finish("x", None, prop) is True


def test_her_prompts_ask_for_the_tool_not_the_text_fields():
    """Jessica, 2026-10-12: the old text-field way is removed from the prompts; the tool replaces it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("sequential.md", "orchestrated.md", "networked.md", "config/mdo_f25_sequential_agents.yaml"):
        text = (root / name).read_text()
        assert "RECOMMENDATION_TO: <" not in text and "RECOMMENDED_CHANGE: <" not in text, name
        assert "with the record_recommendation tool." in text, name
        assert text.count("VERDICT: <COMPLETE | CONTINUE | RETRY>") >= 1, name


def test_a_networked_peer_holds_its_role_through_the_board(monkeypatch):
    """Networked peers are agent_N: the peer holding the evaluation TODO is the integrator, the one holding
    propulsion is the propulsion analyst (record permission and both gates follow the board)."""
    from types import SimpleNamespace

    from src.tools import artifact_checks as ac
    from src.tools import work_claims

    held = {"agent_3": ["evaluation"], "agent_1": ["propulsion"]}
    monkeypatch.setattr(work_claims, "_held_by", lambda a: held.get(a, []))
    monkeypatch.delenv("MAS_REQUIRE_RECORDED_RECOMMENDATION", raising=False)
    assert rt.roles_of("agent_3") == {"mdo_integrator"} and rt.roles_of("agent_1") == {"propulsion_analyst"}
    with agent_scope("agent_1"):
        assert "Only the mdo_integrator" in rt.RecordRecommendation().forward("CONTINUE", "geometry_engineer", "x")
    peer3 = SimpleNamespace(name="agent_3", tools={"record_recommendation": 1})
    with pytest.raises(ac.MissingRecordedRecommendation):
        ac.recommendation_recorded_before_finish("x", None, peer3)
    with agent_scope("agent_3"):
        assert json.loads(rt.RecordRecommendation().forward("CONTINUE", "propulsion_analyst", "BPR 5.1 -> 6.0"))["recorded"]
    rt.activate("propulsion_analyst", "BPR 5.1 -> 6.0")
    peer1 = SimpleNamespace(name="agent_1", tools={"respond_to_recommendation": 1})
    with pytest.raises(ac.MissingRecommendationResponse):
        ac.recommendation_response_before_finish("x", None, peer1)


def test_a_repeated_record_is_refused_and_every_result_says_what_comes_next():
    """Live sandbox 2026-10-14: Qwen re-called record_recommendation 16 times when the result only said
    recorded; it must be told to give its final answer, and a repeat must not be stored again."""
    with agent_scope("mdo_integrator"):
        first = json.loads(rt.RecordRecommendation().forward("RETRY", "propulsion_analyst", "Fn_DES 8041 -> 9000"))
        again = json.loads(rt.RecordRecommendation().forward("RETRY", "propulsion_analyst", "Fn_DES 8041 -> 9000"))
        changed = json.loads(rt.RecordRecommendation().forward("RETRY", "propulsion_analyst", "Fn_DES 8041 -> 9500"))
    assert "final_answer" in first["next"]
    assert not again["recorded"] and "final_answer" in again["error"]
    assert changed["recorded"]
    assert len(rt.signals("recommendation")) == 2
    with agent_scope("propulsion_analyst"):
        r1 = json.loads(rt.RespondToRecommendation().forward("Fn_DES raised to 9500"))
        r2 = json.loads(rt.RespondToRecommendation().forward("Fn_DES raised to 9500"))
    assert "final_answer" in r1["next"] and not r2["recorded"]
