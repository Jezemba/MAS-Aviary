"""Aero coupled/uncoupled DETECTION in the design ledger (.claude/BUGS.md B1).

B1 was reported as "only sequential+iterative_feedback couples aero->mission
(7 of 8 combos fail)". The staged-pipeline evidence contradicts that: in
logs/stat_results/deadline_seq_staged/repeat_000 the stage order was already
geometry -> aero -> structures -> propulsion -> mission -> simulation, SU2
converged (CL 0.09221, CD 0.0018375), and the mission stage's own output lists

    FRAMEWORK-INJECTED (Phase H/K, not authored by me):
      Mission.Design.LIFT_COEFFICIENT            = 0.09220510  (SU2 CL_CRUISE)
      Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR = 0.91814506

i.e. the run WAS coupled — yet the ledger recorded "uncoupled_default_drag".

Cause: _aero_coupled inferred coupling from the flown cruise_cd_avg against a
threshold (0.018) calibrated for the OLD "+0.005" drag transform. The
physics-based build-up (2026-07-28) makes a coupled run fly ~0.020-0.030, so
coupled runs land ABOVE the threshold and read as uncoupled.

Fix: read the authoritative aero_coupling_status the injector itself writes.
"""

import pytest

from src.coordination.design_state import DesignState
from src.logging.design_ledger import _aero_coupled
from src.tools.data_plane import init_data_plane

TOOL_SERVER_MAP = {"set_aircraft_parameters": "aviary"}

# cruise_cd_avg actually flown by the coupled staged link 0.
COUPLED_RUN_TRACES = {
    "simulation_executor": {
        "steps": [{"observations": '{"cruise_cd_avg": 0.0299, "fuel_burned_kg": 18373.05}'}]
    }
}


def _install_state(**data_store):
    ds = DesignState()
    ds.data_store.update(data_store)
    init_data_plane(ds, TOOL_SERVER_MAP)
    return ds


@pytest.fixture(autouse=True)
def _clean():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


class TestAuthoritativeDetection:
    def test_injected_run_reads_as_coupled_despite_high_cd(self):
        """THE regression: a genuinely coupled run flying cd 0.0299 (> the old
        0.018 threshold) must be reported coupled."""
        _install_state(
            aero_coupling_status="injected",
            aero_injected_cl=0.0922051,
            aero_injected_cd=0.0018375,
            aero_injected_drag_factor=0.91814506,
            aero_coupling_source="typed_registry",
        )
        out = _aero_coupled(COUPLED_RUN_TRACES)
        assert out["coupled"] is True
        assert out["status"] == "coupled"
        assert out["detection"] == "data_plane"
        assert out["injected_drag_factor"] == pytest.approx(0.91814506)
        assert out["aero_coupling_source"] == "typed_registry"
        # the heuristic value is still reported, just not used to decide
        assert out["cruise_cd_avg"] == pytest.approx(0.0299)

    def test_missing_aero_reads_as_uncoupled(self):
        _install_state(aero_coupling_status="MISSING_no_su2_aero")
        out = _aero_coupled(COUPLED_RUN_TRACES)
        assert out["coupled"] is False
        assert out["status"] == "uncoupled_default_drag"
        assert out["detection"] == "data_plane"

    def test_legacy_fallback_source_is_surfaced(self):
        _install_state(
            aero_coupling_status="injected",
            aero_coupling_source="legacy_fallback",
        )
        out = _aero_coupled({})
        assert out["coupled"] is True
        assert out["aero_coupling_source"] == "legacy_fallback"


class TestHeuristicFallback:
    def test_falls_back_when_no_data_plane_status(self):
        _install_state()  # no aero_coupling_status at all
        out = _aero_coupled(COUPLED_RUN_TRACES)
        assert out["detection"] == "cd_threshold_fallback"
        assert "detection_warning" in out
        # documents the mis-calibration: coupled run still reads uncoupled here
        assert out["coupled"] is False

    def test_fallback_low_cd_reads_coupled(self):
        _install_state()
        out = _aero_coupled(
            {"sim": {"steps": [{"observations": '{"cruise_cd_avg": 0.0141}'}]}}
        )
        assert out["coupled"] is True
        assert out["detection"] == "cd_threshold_fallback"

    def test_fallback_unknown_when_no_cd(self):
        _install_state()
        out = _aero_coupled({})
        assert out["status"] == "unknown"
        assert out["coupled"] is False


class TestRetryCounting:
    def test_uncoupled_advisories_are_counted(self):
        _install_state(aero_coupling_status="injected")
        traces = {
            "mission_architect": {
                "steps": [
                    {"observations": '{"error_code": "UNCOUPLED_MISSION"}'},
                    {"observations": '{"error_code": "UNCOUPLED_MISSION"}'},
                    {"observations": '{"cruise_cd_avg": 0.0299}'},
                ]
            }
        }
        out = _aero_coupled(traces)
        assert out["coupling_retries"] == 2
        assert out["coupled"] is True


class TestNeverRaises:
    def test_bad_traces_do_not_raise(self):
        _install_state(aero_coupling_status="injected")
        for bad in ({}, None, {"a": "not-a-dict"}, {"a": {"steps": None}}):
            assert isinstance(_aero_coupled(bad), dict)
