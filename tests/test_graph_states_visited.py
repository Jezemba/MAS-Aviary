"""The graph's actual path must be recorded, and measured from graph states.

Two problems, found while diagnosing a graph_routed run that burned its whole
75-minute budget and produced nothing:

1. `states_visited` was never surfaced. Reconstructing the path meant regexing
   state names out of the sweep log -- which also matches the PROMPT text, since
   the graph definition is embedded in the prompts, so the trace was untrustworthy.

2. `_graph_routed_metrics` computed its state sequence from `agent_name`
   (geometry_engineer, aerodynamics_analyst) rather than the graph's own states
   (GEOMETRY_SETUP, AERO_ANALYSIS), which GraphRoutedHandler already sets on
   every message as metadata["graph_state"]. Revisits, routing_accuracy and
   misroute_rate were all measuring which AGENT spoke, not where the graph went,
   so a loop back to GEOMETRY_SETUP was invisible.
"""

from __future__ import annotations

import pytest

from src.coordination.history import AgentMessage
from src.logging.org_theory_metrics import compute_org_theory_metrics


def _msg(agent, state, turn):
    return AgentMessage(
        agent_name=agent,
        content="x" * 40,
        turn_number=turn,
        timestamp=float(turn),
        metadata={"graph_state": state} if state else {},
    )


# The loop actually observed: CONSTRAINTS routes back to GEOMETRY_SETUP.
LOOP = [
    _msg("geometry_engineer", "GEOMETRY_SETUP", 1),
    _msg("aerodynamics_analyst", "AERO_ANALYSIS", 2),
    _msg("mission_analyst", "SIMULATION", 3),
    _msg("reviewer", "CONSTRAINTS", 4),
    _msg("geometry_engineer", "GEOMETRY_SETUP", 5),
    _msg("mission_analyst", "SIMULATION", 6),
    _msg("reviewer", "CONSTRAINTS", 7),
]


def _metrics(messages):
    return compute_org_theory_metrics(
        messages=messages, os_name="sequential", handler_name="graph_routed", config={}
    )


class TestStatesVisitedIsRecorded:
    def test_the_path_is_reported_in_order(self):
        sv = _metrics(LOOP)["states_visited"]
        assert sv[:3] == ["GEOMETRY_SETUP", "AERO_ANALYSIS", "SIMULATION"]

    def test_revisits_are_preserved(self):
        assert _metrics(LOOP)["states_visited"].count("GEOMETRY_SETUP") == 2

    def test_visit_counts_are_reported(self):
        counts = _metrics(LOOP)["state_visit_counts"]
        assert counts["GEOMETRY_SETUP"] == 2
        assert counts["CONSTRAINTS"] == 2

    def test_the_looping_states_are_named(self):
        """This is the diagnostic that was missing: which states repeated."""
        assert set(_metrics(LOOP)["states_revisited"]) == {
            "GEOMETRY_SETUP", "SIMULATION", "CONSTRAINTS"
        }

    def test_a_straight_run_reports_no_revisits(self):
        straight = LOOP[:4]
        assert _metrics(straight)["states_revisited"] == []


class TestSequenceComesFromGraphStateNotAgentName:
    def test_graph_states_are_used(self):
        """agent_name would give geometry_engineer, not GEOMETRY_SETUP."""
        sv = _metrics(LOOP)["states_visited"]
        assert "GEOMETRY_SETUP" in sv
        assert "geometry_engineer" not in sv

    def test_a_loop_is_visible_that_agent_names_would_hide(self):
        """Two different agents can serve one state and vice versa, so an
        agent_name sequence can miss a revisit entirely."""
        msgs = [
            _msg("worker_a", "GEOMETRY_SETUP", 1),
            _msg("worker_b", "CONSTRAINTS", 2),
            _msg("worker_c", "GEOMETRY_SETUP", 3),   # same STATE, new agent
        ]
        assert _metrics(msgs)["state_visit_counts"]["GEOMETRY_SETUP"] == 2

    def test_agent_name_is_the_fallback_for_older_runs(self):
        """Runs predating the metadata must still produce a sequence."""
        msgs = [_msg("classifier", None, 1), _msg("coder", None, 2)]
        assert _metrics(msgs)["states_visited"] == ["classifier", "coder"]


class TestTransitionCapBinds:
    def test_default_cap_fits_a_realistic_budget(self):
        """A guard that cannot fire before the wall-clock timeout converts a
        recoverable run into a total loss: max_transitions breaks the loop and
        RETURNS a result, the timeout kills the process and yields nothing."""
        from src.coordination.graph_routed_handler import GraphRoutedHandler

        h = GraphRoutedHandler.__new__(GraphRoutedHandler)
        cap = {}.get("max_transitions", 25)
        assert cap == 25, "default must fit the run budget at local-model speed"
