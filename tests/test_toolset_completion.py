"""A worker must arrive able to do the job it is given.

`create_agent` validated that requested tools EXIST but never that the set could
perform the role, so a mis-provisioned worker failed two steps later as a missing
artifact, far from the cause. Measured 2026-08-12:

  B33  the aero worker was created with
       ['create_su2_session', 'set_mesh', 'run_su2_solver'] -- no way to
       configure the solver. Responses named configure_from_cpacs five times and
       the worker returned final_answer each time. Zero fuel.
  B37  the geometry worker was created without generate_volume_mesh, which its
       own graph state declares in TOOLS. It tried 10 times, got
       "Unknown tool generate_volume_mesh, should be one of: open_cpacs, ...",
       and gave up. No mesh -> no solve -> no aero.

Completion rather than rejection is deliberate: an error asks the caller to
compose a different toolset, and the measured behaviour on a missing capability
is abandonment, not correction. Supplying it matches what the data plane already
does for session config and mesh payloads.
"""

from __future__ import annotations

import pytest

from src.tools.orchestrator_tools import _complete_toolset

AVAILABLE = {
    t: object()
    for t in (
        "open_cpacs", "generate_volume_mesh", "set_high_level_parameters",
        "get_wing_summary", "create_su2_session", "set_mesh",
        "configure_from_cpacs", "run_su2_solver", "read_history_csv",
        "estimate_mass", "create_cycle_model", "run_cycle",
        "set_aircraft_parameters", "run_simulation", "get_results",
    )
}


class TestTheObservedFailures:
    def test_b33_aero_worker_gains_configure_from_cpacs(self):
        tools, added, disc = _complete_toolset(
            "aerodynamics_analyst", "Run SU2 for cruise aero",
            ["create_su2_session", "set_mesh", "run_su2_solver"], AVAILABLE,
        )
        assert "configure_from_cpacs" in tools
        assert "configure_from_cpacs" in added
        assert disc == "aero"

    def test_b33_aero_worker_also_gains_the_capture_tool(self):
        """Without read_history_csv the coefficients may never be captured."""
        tools, _, _ = _complete_toolset(
            "aero_worker", "SU2 drag analysis",
            ["create_su2_session", "run_su2_solver"], AVAILABLE,
        )
        assert "read_history_csv" in tools

    def test_b37_geometry_worker_gains_generate_volume_mesh(self):
        tools, added, disc = _complete_toolset(
            "geometry_engineer", "Open CPACS and morph the wing",
            ["open_cpacs", "set_high_level_parameters"], AVAILABLE,
        )
        assert "generate_volume_mesh" in added
        assert disc == "geometry"


class TestOtherDisciplines:
    def test_mission_worker_gains_run_simulation(self):
        tools, added, _ = _complete_toolset(
            "mission_analyst", "Fly the mission and report fuel",
            ["set_aircraft_parameters"], AVAILABLE,
        )
        assert {"run_simulation", "get_results"} <= set(tools)

    def test_propulsion_worker_gains_run_cycle(self):
        tools, _, _ = _complete_toolset(
            "propulsion_analyst", "size the engine", ["create_cycle_model"], AVAILABLE
        )
        assert "run_cycle" in tools


class TestItDoesNotOverreach:
    def test_a_complete_toolset_is_untouched(self):
        original = ["create_su2_session", "set_mesh", "configure_from_cpacs",
                    "run_su2_solver", "read_history_csv"]
        tools, added, _ = _complete_toolset("aero", "su2", list(original), AVAILABLE)
        assert added == []
        assert tools == original

    def test_a_narrow_helper_is_not_promoted(self):
        """Keyword match alone must not turn a reader into a full discipline
        worker -- it also has to already carry one of that discipline's tools."""
        tools, added, disc = _complete_toolset(
            "aero_reporter", "summarise the aerodynamics results for the report",
            ["get_wing_summary"], AVAILABLE,
        )
        assert added == []
        assert disc is None

    def test_unrelated_agent_untouched(self):
        tools, added, disc = _complete_toolset(
            "note_taker", "record decisions", ["get_wing_summary"], AVAILABLE
        )
        assert added == [] and disc is None

    def test_missing_tools_are_not_invented(self):
        """Only tools the orchestrator actually has may be added."""
        tools, added, _ = _complete_toolset(
            "geometry_engineer", "morph the wing", ["open_cpacs"],
            {"open_cpacs": object()},
        )
        assert added == []
        assert tools == ["open_cpacs"]
