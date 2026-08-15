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

    def test_non_su2_metadata_is_never_injected(self):
        """The canonical su2_config carries `ref_from_upstream`, a DIRECTIVE
        meaning "REF comes from upstream geometry" — not an SU2 option. Reading
        the raw dict wrote REF_FROM_UPSTREAM= True into the config and SU2
        rejected the whole file, which is exactly the failure this tool exists
        to eliminate. render_snippets already excludes it."""
        out = resolve_request("configure_from_cpacs", {"cpacs_file_path": "/tmp/f25.xml"})
        ov = out.get("overrides") or {}
        assert "ref_from_upstream" not in ov
        assert "REF_FROM_UPSTREAM" not in {str(k).upper() for k in ov}

    def test_every_injected_key_is_su2_shaped(self):
        """SU2 options are uppercase; anything else is our own metadata."""
        import re

        out = resolve_request("configure_from_cpacs", {"cpacs_file_path": "/tmp/f25.xml"})
        for k in (out.get("overrides") or {}):
            assert re.match(r"^[A-Z][A-Z0-9_]*$", str(k)), f"non-SU2 key injected: {k}"

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


class TestSessionCreationGetsTheSameInjections:
    """`create_su2_session` applies configuration at creation (su2-mcp 70e75cc),
    so it must receive the SAME injections as `configure_from_cpacs`.

    Wiring only `cpacs_file_path` through was not enough. Observed live
    (remaining5b run 1/10): the session auto-configured with geometry references
    and markers but no flight numerics, and SU2 answered

        "Config file is missing the CONV_NUM_METHOD_FLOW option."

    The response named configure_from_cpacs as the remedy and the caller
    returned final_answer instead -- the tool-switch failure measured in B33. A
    session that arrives half-configured is still a session that cannot solve.
    """

    def test_canonical_numerics_are_injected(self):
        out = resolve_request("create_su2_session", {"base_name": "f25"})
        ov = out.get("overrides") or {}
        assert ov, "create_su2_session must receive the canonical su2_config"
        for required in ("CONV_NUM_METHOD_FLOW", "TIME_DISCRE_FLOW", "CFL_NUMBER", "ITER"):
            assert required in ov, f"{required} missing -- SU2 rejects the config without it"

    def test_the_exact_option_su2_complained_about(self):
        ov = resolve_request("create_su2_session", {"base_name": "f25"}).get("overrides") or {}
        assert ov.get("CONV_NUM_METHOD_FLOW")

    def test_computed_references_are_injected(self):
        _capture_geometry(reference_area=58.505, mac_length=3.282)
        out = resolve_request("create_su2_session", {"base_name": "f25"})
        assert out["ref_area"] == pytest.approx(58.505)
        assert out["ref_length"] == pytest.approx(3.282)

    def test_caller_supplied_values_still_win(self):
        _capture_geometry()
        out = resolve_request(
            "create_su2_session", {"base_name": "f25", "ref_area": 200.0, "overrides": {"ITER": 7}}
        )
        assert out["ref_area"] == 200.0
        assert out["overrides"] == {"ITER": 7}

    def test_no_su2_metadata_leaks_in(self):
        """`ref_from_upstream` is a directive, not an SU2 option."""
        ov = resolve_request("create_su2_session", {"base_name": "f25"}).get("overrides") or {}
        assert "ref_from_upstream" not in ov
        assert "REF_FROM_UPSTREAM" not in {str(k).upper() for k in ov}


class TestMeshIsInjectedIntoSessionCreation:
    """A session that arrives configured but MESH-LESS still cannot solve.

    B36, measured in remaining5c run 4/10: the auto-config was accepted (zero
    config_errors) and SU2 then failed with

        "The SU2 mesh file named wing_mesh.su2 was not found."

    The caller named a mesh in create_su2_session and never called set_mesh, so
    nothing was stored under that name. Third instalment of one lesson -- a
    caller that must make N calls in order will make fewer than N -- so the mesh
    is supplied the same way the CPACS path and the numerics now are.
    """

    def _store_mesh(self, key="generate_volume_mesh__mesh_base64", size=4000):
        from src.tools.data_plane import get_design_state

        get_design_state().data_store[key] = "TkRJTUU9Mw==" * (size // 12)

    def test_mesh_payload_is_injected(self):
        self._store_mesh()
        out = resolve_request("create_su2_session", {"base_name": "f25"})
        assert out.get("initial_mesh"), "the session must arrive with its mesh"
        assert len(out["initial_mesh"]) > 512

    def test_filename_is_pinned_so_config_and_mesh_agree(self):
        """The observed failure was a caller-invented name (wing_mesh.su2) that
        nothing was ever stored under."""
        self._store_mesh()
        out = resolve_request("create_su2_session", {"base_name": "f25"})
        assert out["mesh_file_name"] == "mesh.su2"

    def test_export_component_mesh_is_also_accepted(self):
        self._store_mesh(key="export_component_mesh__mesh_base64")
        out = resolve_request("create_su2_session", {"base_name": "f25"})
        assert out.get("initial_mesh")

    def test_caller_supplied_mesh_wins(self):
        self._store_mesh()
        out = resolve_request(
            "create_su2_session", {"base_name": "f25", "initial_mesh": "CALLER"}
        )
        assert out["initial_mesh"] == "CALLER"

    def test_no_mesh_yet_means_no_injection(self):
        """Before generate_volume_mesh runs there is nothing to supply, and we
        must not invent one."""
        out = resolve_request("create_su2_session", {"base_name": "f25"})
        assert not out.get("initial_mesh")

    def test_a_too_small_payload_is_not_treated_as_a_mesh(self):
        from src.tools.data_plane import get_design_state

        get_design_state().data_store["generate_volume_mesh__mesh_base64"] = "tiny"
        out = resolve_request("create_su2_session", {"base_name": "f25"})
        assert not out.get("initial_mesh")

    def test_other_tools_unaffected(self):
        self._store_mesh()
        out = resolve_request("run_su2_solver", {"session_id": "s"})
        assert "initial_mesh" not in out
