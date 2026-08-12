"""The agent supplies only the CPACS path; the data plane supplies the rest.

Single-pass coupling worked because run_link.py LOADS the canonical SU2 config
programmatically. Agents received the same ~20-key dict as prompt text to
reproduce verbatim and drifted (PHYSICAL_PROBLEM, GREEN-GAUSS, IMPLICIT,
MARKER_BODY, MACH), so SU2 rejected 80-91% of their configs and the aero
coupling could not happen. The difference was transcription, not physics.

Two values must reach configure_from_cpacs without the agent retyping them:

  * the pinned flight state + numerics, so all combos solve the same cruise
    point and remain comparable;
  * the COMPUTED reference area/MAC. CPACS's <reference><area> is DECLARED and
    morphing does not update it — a real morphed export still said 122.4 while
    get_wing_summary computed 65.98 for that geometry — so the declared value
    would describe the BASELINE wing and reproduce the wrong-CL bug (B12).

Both are already captured into the typed registry from get_wing_summary.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools.data_plane import init_data_plane, intercept_response, resolve_request

TOOL_SERVER_MAP = {"configure_from_cpacs": "su2", "get_wing_summary": "tigl"}


@pytest.fixture(autouse=True)
def _state():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _capture_geometry(reference_area=65.985, mac_length=4.193):
    intercept_response(
        "get_wing_summary",
        json.dumps({"reference_area": reference_area, "mac_length": mac_length,
                    "wetted_area": 111.6, "span": 38.8}),
    )


class TestComputedReferencesInjected:
    def test_ref_area_and_length_come_from_captured_geometry(self):
        _capture_geometry()
        out = resolve_request("configure_from_cpacs", {"cpacs_file_path": "/tmp/f25.xml"})
        assert out["ref_area"] == pytest.approx(65.985)
        assert out["ref_length"] == pytest.approx(4.193)

    def test_references_track_a_morphed_design(self):
        """A new morph must move the reference the solver uses."""
        _capture_geometry(reference_area=122.4, mac_length=5.219)
        first = resolve_request("configure_from_cpacs", {"cpacs_file_path": "/tmp/a.xml"})
        _capture_geometry(reference_area=65.985, mac_length=4.193)
        second = resolve_request("configure_from_cpacs", {"cpacs_file_path": "/tmp/a.xml"})
        assert first["ref_area"] == pytest.approx(122.4)
        assert second["ref_area"] == pytest.approx(65.985)

    def test_agent_supplied_reference_is_respected(self):
        _capture_geometry()
        out = resolve_request(
            "configure_from_cpacs",
            {"cpacs_file_path": "/tmp/f25.xml", "ref_area": 200.0, "ref_length": 6.0},
        )
        assert out["ref_area"] == 200.0
        assert out["ref_length"] == 6.0

    def test_no_geometry_captured_means_no_injection(self):
        """Without a wing summary we do not invent a reference."""
        out = resolve_request("configure_from_cpacs", {"cpacs_file_path": "/tmp/f25.xml"})
        assert out.get("ref_area") is None


class TestCanonicalNumericsInjected:
    def test_overrides_filled_from_canonical(self):
        out = resolve_request("configure_from_cpacs", {"cpacs_file_path": "/tmp/f25.xml"})
        ov = out.get("overrides") or {}
        assert ov, "canonical su2_config should have been injected"
        # The exact values the agents kept getting wrong.
        assert str(ov.get("SOLVER")) == "EULER"
        assert str(ov.get("TIME_DISCRE_FLOW")) == "EULER_IMPLICIT"
        assert "PHYSICAL_PROBLEM" not in ov

    def test_agent_supplied_overrides_are_respected(self):
        out = resolve_request(
            "configure_from_cpacs",
            {"cpacs_file_path": "/tmp/f25.xml", "overrides": {"ITER": 7}},
        )
        assert out["overrides"] == {"ITER": 7}


class TestOtherToolsUnaffected:
    def test_unrelated_tool_untouched(self):
        _capture_geometry()
        out = resolve_request("run_su2_solver", {"session_id": "s"})
        assert "ref_area" not in out and "overrides" not in out

    def test_geometry_capture_still_populates_registry(self):
        _capture_geometry()
        ds = __import__("src.tools.data_plane", fromlist=["x"]).get_design_state()
        assert coupling.get_var(ds, "geom.reference_area") == pytest.approx(65.985)
