"""Tests for data-plane middleware (src/tools/data_plane.py).

Focused on the 2026-05-11 fix that extends payload interception from
base64-only to also catch large lists and large dict payloads
(pycycle list_variables, aviary get_trajectory).
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools.data_plane import (
    _is_large_structured_payload,
    _summarize_payload,
    init_data_plane,
    intercept_response,
    resolve_request,
)


@pytest.fixture
def fresh_state():
    state = DesignState()
    init_data_plane(state, tool_server_map={})
    return state


class TestLargePayloadDetection:
    def test_long_list_triggers(self):
        assert _is_large_structured_payload(list(range(40))) is True

    def test_short_list_does_not_trigger(self):
        assert _is_large_structured_payload([1, 2, 3]) is False

    def test_at_threshold_does_not_trigger(self):
        # 30 items exactly — must be > 30 to trigger
        assert _is_large_structured_payload(list(range(30))) is False

    def test_big_dict_triggers(self):
        big = {f"k{i}": "x" * 50 for i in range(50)}
        assert _is_large_structured_payload(big) is True

    def test_small_dict_does_not_trigger(self):
        assert _is_large_structured_payload({"a": 1, "b": 2}) is False

    def test_non_structured_does_not_trigger(self):
        assert _is_large_structured_payload("a short string") is False
        assert _is_large_structured_payload(42) is False
        assert _is_large_structured_payload(None) is False


class TestSummarizePayload:
    def test_list_summary_keeps_preview_and_ref(self):
        items = [{"id": i, "name": f"item-{i}"} for i in range(100)]
        summary = _summarize_payload(items, "tool__field")
        assert summary["_intercepted"] is True
        assert summary["ref"] == "tool__field"
        assert summary["kind"] == "list"
        assert summary["total_count"] == 100
        assert summary["preview"] == items[:5]
        assert "Pass" in summary["note"]

    def test_dict_summary_keeps_keys_and_size(self):
        big = {f"k{i}": "v" * 100 for i in range(40)}
        summary = _summarize_payload(big, "tool__field")
        assert summary["_intercepted"] is True
        assert summary["ref"] == "tool__field"
        assert summary["kind"] == "dict"
        assert summary["total_keys"] == 40
        assert len(summary["keys_preview"]) == 10
        assert summary["size_bytes"] > 2048


class TestInterceptResponse:
    def test_pycycle_list_variables_intercepted(self, fresh_state):
        """The exact shape that bloated context to 377K tokens in run #1."""
        big_response = {
            "variables": [
                {"name": f"var_{i}", "value": float(i), "units": "kg"}
                for i in range(200)
            ]
        }
        out = intercept_response("list_variables", big_response)
        assert isinstance(out, dict)
        # Original "variables" key replaced with summary
        assert "variables" in out
        summary = out["variables"]
        assert summary["_intercepted"] is True
        assert summary["ref"] == "list_variables__variables"
        assert summary["total_count"] == 200
        # Full payload retained in store for downstream refs
        stored = fresh_state.data_store["list_variables__variables"]
        assert len(stored) == 200

    def test_aviary_get_trajectory_intercepted(self, fresh_state):
        """60-point timeseries arrays as returned by get_trajectory."""
        trajectory = {
            "trajectory": {
                "time": [float(i) for i in range(60)],
                "throttle": [0.3 + 0.001 * i for i in range(60)],
                "drag_N": [25000.0 + 50.0 * i for i in range(60)],
                "distance_nmi": [float(i * 40) for i in range(60)],
                "phase_labels": (["climb"] * 20 + ["cruise"] * 20 + ["descent"] * 20),
                "num_points": 60,
            }
        }
        out = intercept_response("get_trajectory", trajectory)
        # Nested dict > 2KB → intercept
        traj_field = out["trajectory"]
        assert traj_field["_intercepted"] is True
        assert traj_field["kind"] == "dict"
        assert traj_field["ref"] == "get_trajectory__trajectory"
        assert fresh_state.data_store["get_trajectory__trajectory"] == trajectory["trajectory"]

    def test_small_response_passthrough(self, fresh_state):
        small = {"session_id": "abc-123", "status": "ok"}
        out = intercept_response("create_session", small)
        assert out == small
        # Nothing stored
        assert "create_session__session_id" not in fresh_state.data_store

    def test_json_string_response_intercepted(self, fresh_state):
        """Some MCPs return JSON-encoded strings, not dicts."""
        big_response = json.dumps(
            {"variables": [{"n": i} for i in range(100)]}
        )
        out = intercept_response("list_variables", big_response)
        decoded = json.loads(out)
        assert decoded["variables"]["_intercepted"] is True
        assert decoded["variables"]["total_count"] == 100

    def test_binary_payload_still_intercepted(self, fresh_state):
        """Pre-existing binary interception must continue to work."""
        mesh = "A" * 5000  # base64-shaped, > size threshold
        out = intercept_response(
            "generate_volume_mesh",
            {"format": "su2", "mesh_base64": mesh, "statistics": {"cells": 350000}},
        )
        assert out["mesh_base64"]["ref"] == "generate_volume_mesh__mesh_base64"
        assert out["mesh_base64"]["size_bytes"] == 5000
        # statistics is small, passes through
        assert out["statistics"] == {"cells": 350000}

    def test_read_history_csv_passes_through_no_interception_PLACEHOLDER(self, fresh_state):  # noqa
        """Placeholder name to keep below test in this file."""
        pass


class TestResolveRequestRefFormats:
    """The data plane stores large payloads under a key and hands the LLM
    ``{"ref": "key", "size_bytes": N}``. When the LLM then forwards that
    ref into a downstream tool, it picks ONE of several reasonable
    formats based on how it interpreted the dict. resolve_request must
    handle all of them — or set_mesh / set_inputs etc. receive a literal
    string and the underlying MCP server fails with a base64-decode
    error ("Incorrect padding" — observed in Run #8 2026-05-16).

    Every format below is one a real Claude run has produced at temp=0.
    They differ only in how the LLM serialized the dict the data plane
    handed it. The middleware should treat them all as the same ref.
    """

    @pytest.fixture
    def stored_payload(self):
        """Pre-populate DesignState.data_store with a fake mesh payload."""
        ds = DesignState()
        ds.data_store["generate_volume_mesh__mesh_base64"] = "AAAA" * 1000  # 4 KB stand-in
        init_data_plane(ds, {"set_mesh": "su2"})
        return ds

    def test_dict_form_resolves(self, stored_payload):
        """{"ref": "key"} — the canonical format the data plane emits."""
        out = resolve_request(
            "set_mesh",
            {"mesh_base64": {"ref": "generate_volume_mesh__mesh_base64"}},
        )
        assert out["mesh_base64"] == "AAAA" * 1000

    def test_dict_form_with_extra_keys_resolves(self, stored_payload):
        """The summary dict carries ref + size_bytes. Both should work."""
        out = resolve_request(
            "set_mesh",
            {"mesh_base64": {
                "ref": "generate_volume_mesh__mesh_base64",
                "size_bytes": 4000,
            }},
        )
        assert out["mesh_base64"] == "AAAA" * 1000

    def test_bare_string_key_resolves(self, stored_payload):
        """Just the key as a plain string — agent often picks this shape."""
        out = resolve_request(
            "set_mesh",
            {"mesh_base64": "generate_volume_mesh__mesh_base64"},
        )
        assert out["mesh_base64"] == "AAAA" * 1000

    def test_ref_prefix_string_resolves(self, stored_payload):
        """LLM serialized the dict as a `ref:key` string (the Run #8 bug).

        This is what burned Run #8: the agent received
        {"ref": "generate_volume_mesh__mesh_base64"} from the previous
        step, then forwarded it to set_mesh as the STRING
        `"ref:generate_volume_mesh__mesh_base64"` instead of the dict.
        The middleware must recognize this as a ref-to-key and resolve.
        """
        out = resolve_request(
            "set_mesh",
            {"mesh_base64": "ref:generate_volume_mesh__mesh_base64"},
        )
        assert out["mesh_base64"] == "AAAA" * 1000

    def test_ref_prefix_with_whitespace_resolves(self, stored_payload):
        """Same as above but with whitespace variants the LLM might produce."""
        for value in ("ref: generate_volume_mesh__mesh_base64",
                      "ref:  generate_volume_mesh__mesh_base64",
                      " ref:generate_volume_mesh__mesh_base64 "):
            out = resolve_request("set_mesh", {"mesh_base64": value})
            assert out["mesh_base64"] == "AAAA" * 1000, (
                f"ref-prefix variant did not resolve: {value!r}"
            )

    def test_json_string_dict_resolves(self, stored_payload):
        """LLM might forward the dict as a JSON-string (mcpadapt anyOf
        collapse). resolve_request should handle this too."""
        out = resolve_request(
            "set_mesh",
            {"mesh_base64": '{"ref": "generate_volume_mesh__mesh_base64"}'},
        )
        assert out["mesh_base64"] == "AAAA" * 1000

    def test_unknown_string_passes_through(self, stored_payload):
        """A string that is NOT a known ref must not be munged."""
        out = resolve_request(
            "set_mesh",
            {"mesh_base64": "this_is_not_a_ref"},
        )
        assert out["mesh_base64"] == "this_is_not_a_ref"

    def test_unknown_ref_prefix_passes_through(self, stored_payload):
        """A `ref:` prefix pointing at an unknown key must NOT be silently
        swapped to something else — let the server error so we notice."""
        out = resolve_request(
            "set_mesh",
            {"mesh_base64": "ref:nonexistent_key"},
        )
        # Stays as-is; the underlying server's base64 decode will fail
        # loudly which is the right outcome here.
        assert out["mesh_base64"] == "ref:nonexistent_key"


class TestRunHistoryPassthroughOriginal:
    def test_read_history_csv_passes_through_no_interception(self, fresh_state):
        """``read_history_csv`` is in _PASSTHROUGH_TOOLS — its rows ARE
        the analytical content the agent reasons about. Intercepting
        them hides the final residuals and makes the agent under-count
        the convergence (Run #7 2026-05-12 regression).

        Even with 117 rows (well above the list-intercept threshold of
        30), the response should be returned as-is so the agent can
        read the final iteration's rms value.
        """
        history = [
            {"Inner_Iter": i, "rms[Rho]": -1.0 - 0.06 * i}
            for i in range(117)
        ]
        response = {"columns": ["Inner_Iter", "rms[Rho]"],
                    "rows": history, "total_rows": 117}
        out = intercept_response("read_history_csv", response)

        # rows pass through unchanged — agent sees full trace
        assert out == response, (
            "read_history_csv response was intercepted; agent will only "
            "see the preview rows and miss the converged tail."
        )
        # Nothing got stored under a ref either
        assert "read_history_csv__rows" not in fresh_state.data_store


class TestPhaseHAeroInjection:
    """Phase H — data-plane captures SU2 CL/CD off read_history_csv
    and merges them into the next set_aircraft_parameters call.

    The LLM-driven prompt path consistently dropped these keys (Runs
    #13–#15, 2026-05-19), so the middleware closes the loop. These
    tests pin down the contract: the agent doesn't need to know.
    """

    def test_capture_aero_from_read_history_csv(self, fresh_state):
        """Final-row CL/CD on a read_history_csv response is stashed
        in data_store under fixed keys."""
        response = {
            "columns": ["Inner_Iter", "rms[Rho]", "CL", "CD"],
            "rows": [
                {"Inner_Iter": 0, "rms[Rho]": -1.0, "CL": 0.05, "CD": 0.01},
                {"Inner_Iter": 100, "rms[Rho]": -8.0,
                 "CL": 0.187, "CD": 0.0128},
            ],
            "total_rows": 2,
        }
        intercept_response("read_history_csv", response)

        assert fresh_state.data_store["aero_cl_cruise"] == pytest.approx(0.187)
        assert fresh_state.data_store["aero_cd_cruise"] == pytest.approx(0.0128)

    def test_capture_handles_whitespace_padded_keys(self, fresh_state):
        """SU2 raw history CSVs ship column headers like 'CL      '
        with surrounding whitespace; the capture must still find them.
        """
        response = {
            "rows": [
                {"Inner_Iter": 50,
                 "      CL      ": 0.42,
                 "      CD      ": 0.018},
            ],
        }
        intercept_response("read_history_csv", response)
        assert fresh_state.data_store["aero_cl_cruise"] == pytest.approx(0.42)
        assert fresh_state.data_store["aero_cd_cruise"] == pytest.approx(0.018)

    def test_capture_handles_quoted_su2_column_headers(self, fresh_state):
        """SU2's actual history.csv writes column headers with literal
        double quotes inside each CSV cell: '       "CL"       '.
        csv.DictReader preserves them; Phase H v4 (Run #16) hit this
        and the capture silently no-op'd. Strip whitespace AND
        embedded quote chars before matching.
        """
        response = {
            "rows": [
                {"Time_Iter": 0.0,
                 "       \"CL\"       ": 0.187,
                 "       \"CD\"       ": 0.0128},
            ],
        }
        intercept_response("read_history_csv", response)
        assert fresh_state.data_store["aero_cl_cruise"] == pytest.approx(0.187)
        assert fresh_state.data_store["aero_cd_cruise"] == pytest.approx(0.0128)

    def test_inject_into_set_aircraft_parameters(self, fresh_state):
        """When the agent submits the 8-key parameters dict without the
        Phase H keys, resolve_request adds Mission.Design.LIFT_COEFFICIENT
        and Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR using the
        previously captured aero coefficients."""
        fresh_state.data_store["aero_cl_cruise"] = 0.187
        fresh_state.data_store["aero_cd_cruise"] = 0.0128

        agent_params = {
            "Aircraft.Wing.AREA": 130.1,
            "Aircraft.Wing.ASPECT_RATIO": 11.0,
            "Aircraft.Engine.SCALE_FACTOR": 1.3,
        }
        out = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s", "parameters": agent_params},
        )
        params = out["parameters"]
        assert params["Mission.Design.LIFT_COEFFICIENT"] == pytest.approx(0.187)
        # CD_realistic = 0.0128 + 0.005 = 0.0178
        # CD_av_default = 0.022 + 0.187**2 / (pi * 11 * 0.85) ≈ 0.02319
        # scale ≈ 0.0178 / 0.02319 ≈ 0.768
        assert params["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] == \
            pytest.approx(0.768, abs=0.01)

    def test_inject_does_not_overwrite_explicit_agent_values(
        self, fresh_state,
    ):
        """If the agent did set the Phase H keys (e.g. in a future run
        where the prompt finally landed), preserve their values rather
        than overwriting with the middleware's calculation.
        """
        fresh_state.data_store["aero_cl_cruise"] = 0.187
        fresh_state.data_store["aero_cd_cruise"] = 0.0128

        agent_params = {
            "Aircraft.Wing.AREA": 130.1,
            "Mission.Design.LIFT_COEFFICIENT": 0.40,
            "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR": 1.0,
        }
        out = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s", "parameters": agent_params},
        )
        params = out["parameters"]
        assert params["Mission.Design.LIFT_COEFFICIENT"] == 0.40
        assert params["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] == 1.0

    def test_inject_noop_when_no_aero_captured(self, fresh_state):
        """If aero never produced CL/CD (e.g. UPSTREAM_ERROR cascaded),
        the middleware is a no-op so aviary falls back to its internal
        FLOPS aero."""
        out = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s", "parameters": {"Aircraft.Wing.AREA": 130.1}},
        )
        assert "Mission.Design.LIFT_COEFFICIENT" not in out["parameters"]
        assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in out["parameters"]

    # Phase J middleware reverted 2026-05-23 after sensitivity
    # validation (tests/test_phase_hj_coupling_validation.py) showed
    # SUBSONIC_FUEL_FLOW_SCALER has no effect on the FwFm bench's
    # tabular engine deck. The value lands in aviary's applied list
    # but doesn't reach the trajectory simulation, so injecting it
    # produced a silent no-op.
    #
    # Reinstate only if/when the pipeline switches to a GASP analytic
    # engine model OR replaces the engine deck file at runtime — both
    # of which the test will catch with a direct sensitivity sweep.

    def test_inject_coerces_json_string_parameters(self, fresh_state):
        """mcpadapt's anyOf gap sometimes lands `parameters` as a JSON
        string. Inject still works."""
        fresh_state.data_store["aero_cl_cruise"] = 0.187
        fresh_state.data_store["aero_cd_cruise"] = 0.0128

        out = resolve_request(
            "set_aircraft_parameters",
            {"session_id": "s",
             "parameters": json.dumps({"Aircraft.Wing.AREA": 130.1})},
        )
        params = out["parameters"]
        # Should be coerced to a dict now
        assert isinstance(params, dict)
        assert "Mission.Design.LIFT_COEFFICIENT" in params
