"""The iterative_feedback handler must see phase status too (B43).

The phase status added for B40/B42 lived in OrchestratedStrategy's context
builders. This handler builds its OWN worker context and holds no reference to
the strategy, so the status never reached it.

Measured 2026-08-12 in a single sweep -- same model, same code, the ONLY
difference being whether the status reached the prompt:

    orchestrated + staged_pipeline     status arrives   0/7 -> 2/2 coupled
    orchestrated + iterative_feedback  status absent    0/8 -> 0/2

and iterative_feedback kept flying the mission BEFORE the aero existed
(run_simulation before run_su2_solver in every observed run).

INFORMATION ONLY. Working out the stage order is what an orchestrated structure
is measured on, so this must not constrain the choice -- and the handler is
shared, with sequential+iterative_feedback already coupling 6/8 without it.
"""

from __future__ import annotations

import pytest

from src.coordination.iterative_feedback_handler import IterativeFeedbackHandler

PHASES = {
    "geometry_setup": ["open_cpacs", "generate_volume_mesh"],
    "aerodynamic_analysis": ["create_su2_session", "run_su2_solver"],
    "mission_setup": ["create_session", "configure_mission"],
}


def _handler(seen=()):
    h = IterativeFeedbackHandler({"_required_tool_phases": PHASES})
    h._tools_seen = set(seen)
    return h


class TestStatusReflectsWhatRan:
    def test_nothing_run_yet(self):
        s = _handler()._format_phase_status()
        assert "run so far: none" in s
        assert "not yet run: geometry_setup" in s

    def test_completed_phase_is_listed(self):
        s = _handler(["open_cpacs", "generate_volume_mesh"])._format_phase_status()
        assert "geometry_setup" in s
        assert "not yet run: aerodynamic_analysis" in s

    def test_partial_phase_does_not_count(self):
        """One of two tools is not the phase."""
        s = _handler(["open_cpacs"])._format_phase_status()
        assert "run so far: none" in s

    def test_all_done(self):
        s = _handler([t for ts in PHASES.values() for t in ts])._format_phase_status()
        assert "all phases have run" in s

    def test_dependency_order_is_explained(self):
        """The ordering failure this addresses was the mission running first."""
        assert "dependency order" in _handler()._format_phase_status()


class TestItStaysOptional:
    def test_no_declared_phases_means_no_line(self):
        """sequential+iterative_feedback couples 6/8 without this; a config that
        declares no phases must be completely unaffected."""
        h = IterativeFeedbackHandler({})
        assert h._format_phase_status() == ""

    def test_absent_config_key_is_safe(self):
        h = IterativeFeedbackHandler({"max_retries": 3})
        assert h._format_phase_status() == ""


class TestConfigPlumbing:
    def test_phases_reach_the_handler_through_the_coordinator(self):
        """`ifb_config` is the `iterative_feedback` SUB-dict, so a key set on
        coord_config does NOT reach it. Missing this would have reproduced B43
        exactly -- the status rendering as nothing, silently."""
        import inspect

        from src.coordination import coordinator

        src = inspect.getsource(coordinator)
        assert 'ifb_config["_required_tool_phases"]' in src
