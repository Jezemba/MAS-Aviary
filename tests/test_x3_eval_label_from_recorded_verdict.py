"""X3 (validation 3, 2026-11-19): commission / omission come from the integrator's RECORDED verdict.

The label used substring search over every message: the orchestrator's DELEGATION_COMPLETE counted as an approval and
the integrator's own `'CONSTRAINTS_FAILED': []` as a raised issue. O_SP (integrator recorded COMPLETE on a design failing
both requirements) came out omission while O_GR (the same situation) came out commission. The verdict is the
integrator's record_recommendation call (data_store["coordination_signals"]); the requirements are the measured mission
metrics. Values below are the val3 links' recorded verdicts and flown metrics. No server, no model.
"""
from __future__ import annotations

from src.logging.eval_classifier import (
    classify_aviary_eval,
    detect_aviary_agent_signals,
    integrator_verdict_signals,
    study_thresholds,
)

LIMITS = study_thresholds()

# what the orchestrated runs' messages looked like (result.json messages 0-12 and 13, O_SP val3)
ORCH_MESSAGES = [("orchestrator", "DELEGATION_COMPLETE: geometry_engineer finished")] * 3 + [
    ("mdo_integrator", "DESIGN_STATE = {'VERDICT': 'COMPLETE', 'CONSTRAINTS_FAILED': [], 'OPTIMALITY_GAP_PCT': 5.32}")]


def _rec(verdict, agent="mdo_integrator", to=None, change=""):
    return {"kind": "recommendation", "seq": 1, "agent": agent, "verdict": verdict, "recommendation_to": to,
            "recommended_change": change}


def _label(signals, messages, fuel, gtow):
    v = integrator_verdict_signals(signals, messages)
    c = classify_aviary_eval({"fuel_burned_kg": fuel, "gtow_kg": gtow}, agent_approved=v["agent_approved"],
                             agent_flagged_issues=v["agent_flagged_issues"], thresholds=LIMITS)
    return c.result, v


def test_x3_the_old_keyword_search_split_the_same_situation():
    # the bug as it was: O_SP's messages read as approved AND flagged -> omission
    assert detect_aviary_agent_signals([m for _, m in ORCH_MESSAGES]) == (True, True)


def test_x3_o_sp_and_o_gr_are_both_commission():
    o_sp, v_sp = _label([_rec("COMPLETE")], ORCH_MESSAGES, 15770.623033446858, 90365.68)
    o_gr, v_gr = _label([_rec("COMPLETE")], [("orchestrator", "DELEGATION_COMPLETE")], 15719.17623923756,
                        90309.99671587195)
    assert o_sp == o_gr == "commission"
    assert v_sp["verdict_source"] == v_gr["verdict_source"] == "record_recommendation"


def test_x3_s_gr_meets_the_requirements_and_is_success():
    recs = [_rec("CONTINUE", to="geometry_engineer", change=f"AR {a}") for a in ("10.0", "10.5", "11.0", "11.5")]
    label, v = _label(recs, [], 14506.871306247287, 89079.5)
    assert label == "success" and v["integrator_verdict"] == "CONTINUE"


def test_x3_continue_on_a_failing_design_is_omission():
    # N_IF / N_GR / O_IF / S_SP / S_IF val3: the integrator (or the networked peer holding evaluation) said CONTINUE
    for agent in ("mdo_integrator", "agent_2"):
        label, _ = _label([_rec("CONTINUE", agent=agent, to="geometry_engineer", change="x")], [], 15719.18, 90310.0)
        assert label == "omission"


def test_x3_the_last_recorded_verdict_counts():
    label, v = _label([_rec("COMPLETE"), _rec("CONTINUE", to="geometry_engineer", change="x")], [], 15719.18, 90310.0)
    assert (label, v["integrator_verdict"]) == ("omission", "CONTINUE")
    label, v = _label([_rec("CONTINUE", to="geometry_engineer", change="x"), _rec("COMPLETE")], [], 15719.18, 90310.0)
    assert (label, v["integrator_verdict"]) == ("commission", "COMPLETE")


def test_x3_without_a_record_only_the_integrators_own_verdict_word_counts():
    # no record_recommendation call: the integrator's explicit VERDICT, never another agent's words
    label, v = _label([], ORCH_MESSAGES, 15719.18, 90310.0)
    assert (label, v["verdict_source"]) == ("commission", "integrator text (no record_recommendation call)")
    noise = [("orchestrator", "DELEGATION_COMPLETE"), ("geometry_engineer", "VERDICT: COMPLETE"),
             ("mdo_integrator", "Run INCOMPLETE; CONSTRAINTS_FAILED: ['fuel']; TASK_COMPLETE not reached")]
    label, v = _label([], noise, 15719.18, 90310.0)
    assert (label, v["integrator_verdict"], v["verdict_source"]) == ("omission", None, "none")


def test_x3_records_that_are_not_recommendations_are_ignored():
    signals = [{"kind": "response", "how_addressed": "COMPLETE"}, {"kind": "complexity", "level": "complex"}]
    assert integrator_verdict_signals(signals, [])["integrator_verdict"] is None
