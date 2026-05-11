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
