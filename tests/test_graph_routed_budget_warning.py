"""graph_routed must tell the agent how much budget is left, and warn it to converge.

Measured (sweep_final5 run 6, orchestrated_graph_routed): `passes_remaining` was
tracked correctly for the entire run and reached the agent ZERO times. The only
channel was `_build_mental_model`, gated on `internal_representations`, which was
`enabled: false`. The run spent its budget exploring and was killed by the
120-minute wall clock with no ledger row -- after 14 SU2 solves and 67 mission
runs, all discarded.

iterative_feedback has had a second-to-last-attempt warning since early on
(iterative_feedback_handler.py:566). graph_routed had no equivalent.
"""

import pytest

from src.coordination.graph_routed_handler import GraphRoutedHandler


class _RS:
    def __init__(self, remaining, total=8):
        self.passes_remaining = remaining
        self.passes_max = total


class _RM:
    def __init__(self, remaining, total=8):
        self.state = _RS(remaining, total)


def _handler(remaining, total=8):
    h = GraphRoutedHandler({})
    h._resource_mgr = _RM(remaining, total) if remaining is not None else None
    return h


class TestItIsSilentUntilTheThreshold:
    """No running countdown. Jessica, 2026-08-16: a per-turn "N of M passes
    remaining" line is a continuous pacing signal, and ONLY graph_routed has a
    ResourceManager -- broadcasting it every turn hands three of the eight
    combinations a coordination input the other five never receive, which
    contaminates the comparison the experiment exists to make.

    A threshold warning keeps rough parity: iterative_feedback has had a
    second-to-last-ATTEMPT notice all along, so "you are near the end" is a
    signal both handlers give.
    """

    @pytest.mark.parametrize("left", [25, 10, 6, 4, 3])
    def test_comfortable_budget_says_nothing_at_all(self, left):
        assert _handler(left, 25)._format_budget_warning() == ""

    def test_no_resource_manager_is_silent_not_a_crash(self):
        assert _handler(None)._format_budget_warning() == ""

    def test_the_count_appears_only_once_warning(self):
        """The number is fine INSIDE the warning -- it is the per-turn drip that
        is the confound, not the figure itself."""
        assert "2 of 25" in _handler(2, 25)._format_budget_warning()


class TestItWarnsBeforeTheBudgetIsGone:
    def test_second_to_last_pass_says_converge(self):
        msg = _handler(2)._format_budget_warning()
        assert "second-to-last" in msg
        assert "report the best design" in msg

    def test_last_pass_says_stop_starting_new_work(self):
        msg = _handler(1)._format_budget_warning()
        assert "LAST pass" in msg
        assert "Do not" in msg and "new design iteration" in msg

    def test_exhausted_forces_a_final_report(self):
        msg = _handler(0)._format_budget_warning()
        assert "FINAL output" in msg
        assert "do not start" in msg.lower()


class TestItDoesNotDependOnADisplayFlag:
    """The warning must fire even when internal_representations is off.

    That flag gates the mental model, which is where passes_remaining used to
    live exclusively -- and being off is exactly why the agent never saw it.
    A run-ending warning cannot be gated on a display setting.
    """

    def test_warning_is_produced_with_representations_disabled(self):
        h = GraphRoutedHandler({"internal_representations": {"enabled": False}})
        h._resource_mgr = _RM(1)
        assert h._internal_representations is False
        assert "LAST pass" in h._format_budget_warning()
