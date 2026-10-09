"""B151: an agent cannot rewrite the solve's convergence / iteration / trim settings (2026-10-08).

v2b149's aero agent, after the trimmed solve timed out, sent MAX_ITER 15000, RESIDUAL_REDUCTION 6,
NUM_METHOD Jacobian, TIME_ITER 1000, INNER_ITER 10; su2-mcp turned two of them into broken settings and
the next solve died in 1.6 s. Stubbed: no MCP server, no model, no .env.
"""
import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    yield dp._design_state


def test_the_v2b149_update_keeps_the_framework_settings():
    resolved = dp.resolve_request("update_config_entries", {"session_id": "s", "updates": {
        "MAX_ITER": "15000", "CONV_CRITERION": "RESIDUAL", "RESIDUAL_REDUCTION": "6", "NUM_METHOD": "Jacobian",
        "JACOBIAN_UPDATE": "TRUE", "TIME_ITER": "1000", "INNER_ITER": "10", "CFL_ADAPT_PARAM": "0.9"}})
    left = resolved["updates"]
    for key in ("MAX_ITER", "RESIDUAL_REDUCTION", "TIME_ITER", "INNER_ITER"):
        assert key not in left
    assert left["CFL_ADAPT_PARAM"] == "0.9"           # numerics the framework does not fix still pass
    out = json.loads(dp.intercept_response("update_config_entries", json.dumps({"updated_keys": list(left)})))
    assert set(out["framework_kept"]["ignored"]) == {"MAX_ITER", "RESIDUAL_REDUCTION", "TIME_ITER", "INNER_ITER"}


@pytest.mark.parametrize("key", ["FIXED_CL_MODE", "TARGET_CL", "DCL_DALPHA", "AOA", "MACH_NUMBER", "ITER"])
def test_the_trim_and_flight_state_cannot_be_changed(key):
    resolved = dp.resolve_request("update_config_entries", {"session_id": "s", "updates": {key: "1"}})
    assert key not in resolved["updates"]


def test_the_runtime_floor_leaves_room_for_a_trimmed_solve():
    resolved = dp.resolve_request("run_su2_solver", {"session_id": "s", "max_runtime_seconds": 600})
    assert resolved["max_runtime_seconds"] >= 3600
