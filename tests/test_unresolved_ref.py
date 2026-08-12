"""A ref-shaped argument that doesn't resolve must fail fast and helpfully.

Observed live in sweep run 1/16 (2026-08-03). The agent passed

    set_mesh(mesh_base64="generated_volume_mesh__mesh_base64", ...)

one character off the real key ``generate_volume_mesh__mesh_base64``.
resolve_request deliberately does NOT guess at an unrecognised ref (never
silently swap in the wrong payload), so the literal string was forwarded to
su2-mcp, which tried to use it as DATA:

    "Failed to set mesh: Invalid base64-encoded string: number of data
     characters (29) cannot be 1 more than a multiple of 4"

That error names the wrong problem, so the agent could not self-correct and
reissued the identical call. The fix keeps the no-guessing policy but reports
the real available keys (and the near-miss) before the call leaves the framework.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools.data_plane import init_data_plane, unresolved_ref_error

TOOL_SERVER_MAP = {"set_mesh": "su2", "generate_volume_mesh": "tigl"}

REAL_KEY = "generate_volume_mesh__mesh_base64"
TYPO_KEY = "generated_volume_mesh__mesh_base64"


@pytest.fixture(autouse=True)
def _state():
    ds = DesignState()
    ds.data_store[REAL_KEY] = "TkRJTUU9Mw==" * 200  # realistic payload size
    ds.data_store["export_component_mesh__mesh_base64"] = "c29saWQg" * 200
    init_data_plane(ds, TOOL_SERVER_MAP)
    yield ds
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


class TestTypoIsCaught:
    def test_the_live_failure_is_reported(self):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": TYPO_KEY})
        assert err is not None
        assert err["error_code"] == "UNRESOLVED_REF"
        assert TYPO_KEY in err["error"]

    def test_near_miss_is_suggested(self):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": TYPO_KEY})
        assert f"Did you mean '{REAL_KEY}'?" in err["error"]

    def test_available_keys_are_listed(self):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": TYPO_KEY})
        assert REAL_KEY in err["error"]
        assert "export_component_mesh__mesh_base64" in err["error"]

    def test_states_the_call_was_not_sent(self):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": TYPO_KEY})
        assert "NOT sent to the server" in err["error"]


class TestValidCallsPassThrough:
    def test_correct_ref_is_not_flagged(self):
        assert unresolved_ref_error("set_mesh", {"mesh_base64": REAL_KEY}) is None

    def test_ordinary_strings_are_not_flagged(self):
        for value in ("mesh.su2", "/tmp/a/b.xml", "aircraft", "( aircraft )", "EULER"):
            assert unresolved_ref_error("set_mesh", {"x": value}) is None

    def test_uuid_session_id_not_flagged(self):
        sid = "e3b511c0-7a28-48b3-9bed-68882c614c10"
        assert unresolved_ref_error("set_mesh", {"session_id": sid}) is None

    def test_non_string_values_ignored(self):
        assert unresolved_ref_error("set_mesh", {"a": 5, "b": None, "c": {"ref": "x"}}) is None

    def test_dotted_param_names_not_flagged(self):
        """Aviary params like Aircraft.Wing.AREA must never look like refs."""
        assert unresolved_ref_error("x", {"p": "Aircraft.Wing.ASPECT_RATIO"}) is None


class TestEdgeCases:
    def test_no_state_returns_none(self):
        init_data_plane(None, TOOL_SERVER_MAP)
        assert unresolved_ref_error("set_mesh", {"mesh_base64": TYPO_KEY}) is None

    def test_empty_store_returns_none(self):
        """Before anything is stored, a ref-shaped string can't be judged."""
        init_data_plane(DesignState(), TOOL_SERVER_MAP)
        assert unresolved_ref_error("set_mesh", {"mesh_base64": TYPO_KEY}) is None

    def test_unrelated_ref_shape_still_reports_available(self):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": "totally__unknown"})
        assert err is not None
        assert REAL_KEY in err["error"]


class TestMiddlewareBlocksTheCall:
    def test_wrapper_returns_error_without_calling_tool(self):
        from src.tools.type_coercion import apply_coercion_to_tools

        called = {"n": 0}

        class _Tool:
            name = "set_mesh"
            inputs = {"mesh_base64": {"type": "string", "description": "mesh"}}

            def forward(self, **kwargs):
                called["n"] += 1
                return "{}"

        tool = apply_coercion_to_tools([_Tool()])[0]
        out = json.loads(tool.forward(mesh_base64=TYPO_KEY))
        assert out["error_code"] == "UNRESOLVED_REF"
        assert called["n"] == 0, "the bad call must NOT reach the server"
