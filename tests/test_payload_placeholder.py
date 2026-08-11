"""Payload arguments must carry a stored ref, never a model-invented literal.

Measured 25 times across 13 runs of the 2026-08-04 sweep (.claude/BUGS.md B14).
The model wrote its own stand-in instead of the real ref:

    set_mesh(mesh_base64="<MESH_BASE64_FROM_GEOMETRY_STAGE>")
    set_mesh(mesh_base64="base64_mesh_data==")
    set_mesh(mesh_base64="SGVsbG8gd29ybGQ=")        # "Hello world"

su2-mcp answered "Incorrect padding" / "Invalid base64-encoded string", which
names the wrong problem, so the model could not self-correct and reissued the
same call — burning 1-2 steps each time at ~45 s/step on the local model.

The existing UNRESOLVED_REF guard (B11) missed these: it matches the
"<tool>__<field>" ref shape, not placeholders.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools.data_plane import init_data_plane, unresolved_ref_error

TOOL_SERVER_MAP = {"set_mesh": "su2", "generate_volume_mesh": "tigl"}
REAL_KEY = "generate_volume_mesh__mesh_base64"


@pytest.fixture(autouse=True)
def _state():
    ds = DesignState()
    ds.data_store[REAL_KEY] = "TkRJTUU9Mw==" * 200  # stand-in for a real payload
    init_data_plane(ds, TOOL_SERVER_MAP)
    yield ds
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


class TestPlaceholdersRejected:
    @pytest.mark.parametrize("bad", [
        "<MESH_BASE64_FROM_GEOMETRY_STAGE>",   # verbatim from the sweep
        "<MESH_BASE64 ref generate_volume_mesh>",
        "base64_mesh_data==",
        "SGVsbG8gd29ybGQ=",                    # "Hello world" — decodes fine, still wrong
        "YOUR_MESH_HERE",
        "...",
        "TODO",
    ])
    def test_invented_payloads_are_blocked(self, bad):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": bad})
        assert err is not None, f"{bad!r} should have been rejected"
        assert err["error_code"] == "UNRESOLVED_REF"

    def test_error_names_the_real_problem_and_the_real_key(self):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": "<MESH_BASE64_FROM_GEOMETRY_STAGE>"})
        assert "placeholder" in err["error"]
        assert REAL_KEY in err["error"]
        assert "do NOT" in err["error"]
        assert "NOT sent to the server" in err["error"]

    def test_short_payload_reports_its_length(self):
        err = unresolved_ref_error("set_mesh", {"mesh_base64": "SGVsbG8gd29ybGQ="})
        assert "too short" in err["error"]

    def test_typo_ref_still_caught_with_suggestion(self):
        """B11 behaviour must be preserved."""
        err = unresolved_ref_error("set_mesh", {"mesh_base64": "generated_volume_mesh__mesh_base64"})
        assert f"Did you mean '{REAL_KEY}'?" in err["error"]

    def test_create_su2_session_initial_mesh_is_covered(self):
        """`initial_mesh` was MISSED by the first hand-enumerated _PAYLOAD_ARGS,
        so a bad payload reached create_su2_session and produced su2-mcp's
        "Invalid base64-encoded string" during the 2026-08-04 rerun. The set is
        now derived from the live tool schemas."""
        err = unresolved_ref_error(
            "create_su2_session", {"initial_mesh": "<MESH_BASE64 ref from GEOMETRY_SETUP>"}
        )
        assert err is not None
        assert err["error_code"] == "UNRESOLVED_REF"
        assert REAL_KEY in err["error"]

    def test_mesh_file_name_is_NOT_treated_as_payload(self):
        """Names are not payloads — 'mesh.su2' must pass untouched."""
        assert unresolved_ref_error(
            "create_su2_session", {"mesh_file_name": "mesh.su2", "output_mesh_name": "out.su2"}
        ) is None


class TestLegitimateCallsPassThrough:
    def test_correct_ref_accepted(self):
        assert unresolved_ref_error("set_mesh", {"mesh_base64": REAL_KEY}) is None

    def test_genuine_large_payload_accepted(self):
        """A real inline base64 blob must not be mistaken for a placeholder."""
        blob = "TkRJTUU9Mw==" * 200
        assert unresolved_ref_error("set_mesh", {"mesh_base64": blob}) is None

    def test_non_payload_args_untouched(self):
        """Only payload-carrying arguments are policed — short strings are normal
        everywhere else."""
        for k, v in [("mesh_file_name", "mesh.su2"), ("session_id", "abc-123"),
                     ("solver", "SU2_CFD"), ("marker", "( aircraft )"),
                     ("relative_path", "history.csv")]:
            assert unresolved_ref_error("set_mesh", {k: v}) is None

    def test_dotted_aviary_param_untouched(self):
        assert unresolved_ref_error("x", {"p": "Aircraft.Wing.ASPECT_RATIO"}) is None

    def test_empty_store_means_no_gate(self):
        init_data_plane(DesignState(), TOOL_SERVER_MAP)
        assert unresolved_ref_error("set_mesh", {"mesh_base64": "<PLACEHOLDER>"}) is None


class TestMiddlewareBlocksBeforeServer:
    def test_placeholder_never_reaches_the_tool(self):
        from src.tools.type_coercion import apply_coercion_to_tools

        calls = {"n": 0}

        class _Tool:
            name = "set_mesh"
            inputs = {"mesh_base64": {"type": "string", "description": "mesh"}}

            def forward(self, **kwargs):
                calls["n"] += 1
                return "{}"

        tool = apply_coercion_to_tools([_Tool()])[0]
        out = json.loads(tool.forward(mesh_base64="<MESH_BASE64_FROM_GEOMETRY_STAGE>"))
        assert out["error_code"] == "UNRESOLVED_REF"
        assert calls["n"] == 0
