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


class TestTheBudgetIsStated:
    def test_plenty_left_states_the_budget_plainly(self):
        msg = _handler(6)._format_budget_warning()
        assert "6 of 8 passes remaining" in msg

    def test_no_resource_manager_is_silent_not_a_crash(self):
        assert _handler(None)._format_budget_warning() == ""


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
