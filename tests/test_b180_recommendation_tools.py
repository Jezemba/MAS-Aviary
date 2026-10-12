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


def test_the_reminder_gate_is_off_by_default(monkeypatch):
    from types import SimpleNamespace

    from src.tools import artifact_checks as ac

    agent = SimpleNamespace(name="mdo_integrator", tools={"record_recommendation": 1})
    monkeypatch.delenv("MAS_REQUIRE_RECORDED_RECOMMENDATION", raising=False)
    assert ac.recommendation_recorded_before_finish("x", None, agent) is True
    monkeypatch.setenv("MAS_REQUIRE_RECORDED_RECOMMENDATION", "1")
    with pytest.raises(ac.MissingRecordedRecommendation):
        ac.recommendation_recorded_before_finish("x", None, agent)
    assert ac.recommendation_recorded_before_finish("x", None, agent) is True    # reminded once only
