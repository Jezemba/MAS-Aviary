"""Tests for negation-aware completion-signal detection (.claude/BUGS.md B2).

The phantom-link bug: a bare ``"TASK_COMPLETE" in text`` scan matched the
integrator's NEGATED verdict ("Not TASK_COMPLETE: ...") after the cross-link
feedback forwarded that assessment into the next link's task prompt. The
StagedPipelineHandler then skipped all 7 stages, so links 1..N ran in 0.17 s
with 0 tokens and re-reported the previous link's fuel.

The verbatim text below is lifted from the real failing run,
``logs/stat_results/deadline_seq_staged/repeat_001/.../result.json``.
"""

import pytest

from src.coordination.completion_signal import (
    signals_blackboard_done,
    signals_completion,
    strip_forwarded_feedback,
)

# ---------------------------------------------------------------------------
# The exact phrase that caused the phantom-link bug.
# ---------------------------------------------------------------------------

REAL_INTEGRATOR_VERDICT = (
    "  VERDICT: CONTINUE\n\n"
    "Not TASK_COMPLETE: the simulation converged, but fuel_burned_kg violates "
    "its 15,000 kg limit by 3,373 kg and six further constraints are "
    "unverified.\n"
)

REAL_LINK1_TASK = (
    "IMPORTANT — A session has already been created with mission configured.\n"
    "  session_id = a5fec64f-dca5-4335-81d4-5b8e2e83dd32\n"
    "\n=== FEEDBACK FROM THE PREVIOUS DESIGN ITERATION (chain link) ===\n"
    "Previous iteration (link 1) — quick metrics:\n"
    "  fuel_burned_kg = 18373.1 (constraint <=15000: FAIL)\n"
    + REAL_INTEGRATOR_VERDICT
    + "\nThe STARTING parameters above are that previous design.\n"
    "================================================================\n"
    "Design a DLR-F25 class aircraft for minimum fuel burn."
)


class TestNegationAwareness:
    def test_negated_verdict_is_not_completion(self):
        """THE regression: 'Not TASK_COMPLETE' must not read as done."""
        assert signals_completion(REAL_INTEGRATOR_VERDICT) is False

    def test_real_link1_task_is_not_completion(self):
        """The full forwarded link-1 task must not short-circuit the pipeline."""
        assert signals_completion(REAL_LINK1_TASK) is False

    @pytest.mark.parametrize(
        "text",
        [
            "Not TASK_COMPLETE: fuel over limit",
            "This is NOT TASK_COMPLETE yet.",
            "The design is not TASK_COMPLETE.",
            "I cannot report TASK_COMPLETE — constraints failed.",
            "Do not emit TASK_COMPLETE until all constraints pass.",
            "Without TASK_COMPLETE the chain continues.",
            "Never write TASK_COMPLETE prematurely.",
            "Constraints failed, so TASK_COMPLETE is withheld.",
        ],
    )
    def test_negated_forms(self, text):
        assert signals_completion(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "TASK_COMPLETE",
            "All constraints satisfied. TASK_COMPLETE",
            "Fuel 12,400 kg within limit; gtow within limit.\nTASK_COMPLETE\n",
            "VERDICT: DONE. TASK_COMPLETE.",
        ],
    )
    def test_affirmative_forms(self, text):
        assert signals_completion(text) is True

    def test_negation_does_not_leak_across_clauses(self):
        """A negation in an EARLIER sentence must not suppress a later genuine
        signal — clause scanning stops at sentence/line breaks."""
        text = (
            "Link 1 was not finished. All constraints now pass.\n"
            "TASK_COMPLETE"
        )
        assert signals_completion(text) is True

    def test_negation_in_same_clause_suppresses(self):
        text = "All constraints pass but this is not TASK_COMPLETE"
        assert signals_completion(text) is False


class TestStandaloneToken:
    def test_longer_identifier_does_not_match(self):
        assert signals_completion("TASK_COMPLETED_AT = 12:04") is False
        assert signals_completion("field XTASK_COMPLETE set") is False

    def test_punctuation_delimited_matches(self):
        assert signals_completion("(TASK_COMPLETE)") is True
        assert signals_completion("status=TASK_COMPLETE;") is True


class TestForwardedFeedbackStripping:
    def test_block_is_removed(self):
        stripped = strip_forwarded_feedback(REAL_LINK1_TASK)
        assert "FEEDBACK FROM THE PREVIOUS" not in stripped
        assert "18373.1" not in stripped
        assert "Design a DLR-F25 class aircraft" in stripped
        assert "session_id = a5fec64f" in stripped

    def test_no_block_is_identity(self):
        text = "Design a DLR-F25 class aircraft. TASK_COMPLETE"
        assert strip_forwarded_feedback(text) == text

    def test_affirmative_signal_inside_feedback_is_ignored(self):
        """Even a genuine 'TASK_COMPLETE' quoted from the PREVIOUS link must
        not complete THIS link."""
        task = (
            "=== FEEDBACK FROM THE PREVIOUS DESIGN ITERATION (chain link) ===\n"
            "All constraints satisfied. TASK_COMPLETE\n"
            "================================================================\n"
            "Now improve the design further."
        )
        assert signals_completion(task) is False

    def test_strip_can_be_disabled(self):
        task = (
            "=== FEEDBACK FROM THE PREVIOUS DESIGN ITERATION (chain link) ===\n"
            "All constraints satisfied. TASK_COMPLETE\n"
            "================================================================\n"
        )
        assert signals_completion(task, strip_feedback=False) is True


class TestBlackboardMarker:
    def test_marker_detected(self):
        assert signals_blackboard_done("[STATUS] task_complete") is True
        assert signals_blackboard_done("[status] TASK_COMPLETE") is True

    def test_marker_inside_feedback_ignored(self):
        task = (
            "=== FEEDBACK FROM THE PREVIOUS DESIGN ITERATION (chain link) ===\n"
            "[STATUS] task_complete\n"
            "================================================================\n"
            "Keep optimizing."
        )
        assert signals_blackboard_done(task) is False

    def test_absent_marker(self):
        assert signals_blackboard_done("nothing here") is False


class TestEdgeCases:
    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_empty_inputs(self, text):
        assert signals_completion(text) is False
        assert signals_blackboard_done(text) is False

    def test_empty_keyword_never_matches(self):
        assert signals_completion("TASK_COMPLETE", keyword="") is False

    def test_custom_keyword(self):
        assert signals_completion("ALL_DONE", keyword="ALL_DONE") is True
        assert signals_completion("not ALL_DONE", keyword="ALL_DONE") is False


class TestStagedHandlerIntegration:
    """The handler method that actually regressed."""

    def _handler(self):
        from src.coordination.staged_pipeline_handler import StagedPipelineHandler

        return StagedPipelineHandler({"pipeline": "mdo_f25"})

    def test_forwarded_negated_verdict_does_not_complete_pipeline(self):
        h = self._handler()
        assert h._is_task_complete_in_context(REAL_LINK1_TASK, []) is False

    def test_genuine_prior_stage_signal_still_completes(self):
        from src.coordination.completion_criteria import CompletionResult

        h = self._handler()
        prior = [
            (
                "mdo_integrator",
                "All constraints satisfied. TASK_COMPLETE",
                CompletionResult(met=True, reason="done", evidence=""),
            )
        ]
        assert h._is_task_complete_in_context("continue the design", prior) is True

    def test_negated_prior_stage_signal_does_not_complete(self):
        from src.coordination.completion_criteria import CompletionResult

        h = self._handler()
        prior = [
            (
                "mdo_integrator",
                REAL_INTEGRATOR_VERDICT,
                CompletionResult(met=False, reason="continue", evidence=""),
            )
        ]
        assert h._is_task_complete_in_context("continue the design", prior) is False

    def test_blackboard_marker_still_completes(self):
        h = self._handler()
        assert h._is_task_complete_in_context("[STATUS] task_complete", []) is True
