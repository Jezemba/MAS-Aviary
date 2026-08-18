"""Each handler asks for a DIFFERENT verdict vocabulary.

Measured 2026-08-17: the ledger recorded a verdict for graph_routed only, and a
raw search said the other traces "contained no verdict at all". Both were wrong in
the same way -- the search used the graph's alternation too. The integrator in
sequential_staged_pipeline had plainly said:

    "MDO assessment: Constraints failed: fuel_burned_kg (margin -5380 kg).
     Optimality gap: 4.67% (GOOD). ... Verdict: CONTINUE."

CONTINUE is exactly what that handler's own `verdict_patterns` config asks for
(CONTINUE / RETRY / COMPLETE / CONVERGED); the graph asks for
PASSED / MINOR_ISSUES / MAJOR_ISSUES.

Re-parsing tonight's 12 traces recovered 8 verdicts, up from 2.
"""

import pytest

from src.logging.design_ledger import _integrator_verdict, _VERDICT_NORMAL, build_record


def _tr(text):
    return {"w": {"steps": [{"model_output": text}]}}


class TestBothVocabularies:
    @pytest.mark.parametrize("raw,expected", [
        ("VERDICT: PASSED", "PASSED"),
        ("VERDICT: MINOR_ISSUES", "MINOR_ISSUES"),
        ("VERDICT: MAJOR_ISSUES", "MAJOR_ISSUES"),
        ("Verdict: CONTINUE", "CONTINUE"),
        ("Verdict: RETRY", "RETRY"),
        ("verdict: complete", "COMPLETE"),
        ("'VERDICT': 'CONVERGED'", "CONVERGED"),
    ])
    def test_every_declared_token_is_recognised(self, raw, expected):
        assert _integrator_verdict(_tr(raw)) == expected

    def test_the_exact_text_that_was_being_missed(self):
        """Verbatim from the sequential_staged_pipeline integrator."""
        text = ("MDO assessment: Constraints failed: fuel_burned_kg (margin -5380 kg). "
                "Optimality gap: 4.67% (GOOD). Verdict: CONTINUE. Recommendation: ...")
        assert _integrator_verdict(_tr(text)) == "CONTINUE"


class TestNormalisationForCrossComboComparison:
    @pytest.mark.parametrize("token,cls", [
        ("PASSED", "pass"), ("COMPLETE", "pass"), ("CONVERGED", "pass"),
        ("MINOR_ISSUES", "continue"), ("CONTINUE", "continue"),
        ("MAJOR_ISSUES", "issues"), ("RETRY", "issues"),
    ])
    def test_tokens_map_onto_one_scale(self, token, cls):
        assert _VERDICT_NORMAL[token] == cls

    def test_the_raw_token_is_kept_too(self):
        """CONTINUE and MINOR_ISSUES route alike but are not the same statement,
        and the paper should quote what the agent actually said."""
        rec = build_record({}, _tr("Verdict: CONTINUE"))
        assert rec["objective"]["integrator_verdict"] == "CONTINUE"
        assert rec["objective"]["integrator_verdict_class"] == "continue"


class TestAbsenceStaysDistinct:
    def test_no_verdict_is_None_not_a_failing_verdict(self):
        """Never reaching the assessment is a different outcome from assessing
        badly -- orchestrated_iterative_feedback and networked_graph_routed
        genuinely never got there."""
        rec = build_record({}, _tr("no assessment happened here"))
        assert rec["objective"]["integrator_verdict"] is None
        assert rec["objective"]["integrator_verdict_class"] is None

    def test_the_last_verdict_wins(self):
        tr = {"w": {"steps": [{"model_output": "Verdict: RETRY"},
                              {"model_output": "Verdict: COMPLETE"}]}}
        assert _integrator_verdict(tr) == "COMPLETE"
