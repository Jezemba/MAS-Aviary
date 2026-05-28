"""Live test for TiGL mesh generation on the F25 fixture.

Validates that the parameter set baked into
`config/mdo_f25_staged_pipeline.yaml` (geometry stage) actually
produces a mesh in reasonable wall-clock time. This was added after
v15/v16 of mdo_f25_orchestrated_staged_pipeline got stuck for 4-10
minutes inside `generate_volume_mesh` — the symptom looked like
infinite hang but was actually just a 5x-too-large domain.

Run with:
    pytest tests/test_mesh_generation_live.py -m live_mcp -v

Requires:
    tigl-mcp running on http://127.0.0.1:8500/mcp
    /home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml

This is the fast-iteration loop for tuning mesh params: change the
params here, run the test, see the wall-clock. Once happy, propagate
the same params into the stage_prompt.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.live_mcp

try:
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
    pass


# These mesh params match what `config/mdo_f25_staged_pipeline.yaml`
# tells the geometry_engineer worker to use. Keep them in sync — if
# this test passes but a pipeline run hangs, look for divergence.
_FAST_MESH_PARAMS = {
    "component_uid": "Wing1",
    "surface_mesh_size": 1.0,
    "mesh_size_min": 0.3,
    "mesh_size_max": 8.0,
    "far_field_distance": 10.0,
    "boundary_layer_enabled": False,
    "boundary_layer_thickness": 0.01,
    "boundary_layer_layers": 5,
    "boundary_layer_growth": 1.2,
    "output_format": "su2",
}

_CPACS_PATH = (
    "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
)

_MESH_TIME_BUDGET_SECONDS = 60.0  # sequential's working run: 17.9 s


def _check_tigl_alive() -> bool:
    """Cheap proof-of-life check for tigl-mcp."""
    import urllib.request

    try:
        urllib.request.urlopen("http://127.0.0.1:8500/mcp", timeout=2)
    except Exception as e:
        # 406 / 405 are expected — streamable-http rejects unauthenticated
        # GETs but the server IS up. Bare connect-refused / timeout means
        # the server is actually down.
        if "406" in str(e) or "405" in str(e) or "method" in str(e).lower():
            return True
        return False
    return True


def _load_tigl_tools():
    """Load just the tigl-mcp tools via the MCPConnector. Returns a
    {name: tool} dict so we can call open_cpacs and generate_volume_mesh
    directly without going through an LLM."""
    from src.config.loader import AppConfig, MCPConfig, MCPServerConfig
    from src.tools.tool_loader import load_tools_for_agent

    cfg = AppConfig(
        mcp=MCPConfig(
            mode="real",
            servers=[
                MCPServerConfig(
                    name="tigl",
                    url="http://127.0.0.1:8500/mcp",
                    transport="streamable-http",
                ),
            ],
        ),
    )
    tools = load_tools_for_agent([], cfg)
    return {t.name: t for t in tools}


def test_mesh_generation_completes_within_budget():
    """The pinned mesh params must produce a mesh in under
    _MESH_TIME_BUDGET_SECONDS. If this test exceeds the budget, the
    pipeline's geometry stage will hang for users too — TUNE THE PARAMS
    DOWN (reduce far_field_distance, increase mesh_size_min/max,
    or pick a smaller component_uid) until the test passes, then
    propagate to config/mdo_f25_staged_pipeline.yaml's geometry stage."""
    if not _check_tigl_alive():
        pytest.skip("tigl-mcp not reachable at 127.0.0.1:8500")
    if not os.path.isfile(_CPACS_PATH):
        pytest.skip(f"CPACS fixture missing: {_CPACS_PATH}")

    tools = _load_tigl_tools()
    assert "open_cpacs" in tools, "tigl-mcp did not expose open_cpacs"
    assert "generate_volume_mesh" in tools

    # 1. Open the CPACS file.
    open_resp = tools["open_cpacs"].forward(
        source_type="path",
        source=_CPACS_PATH,
    )
    # Resp is either a dict (raw) or a wrapped object — extract session_id.
    if isinstance(open_resp, dict):
        session_id = open_resp.get("session_id")
    else:
        # The data-plane middleware may have replaced the response with
        # a ref or stored object. The session_id should still be in the
        # captured DesignState.sessions["tigl"].
        from src.tools.data_plane import get_design_state

        ds = get_design_state()
        session_id = ds.sessions.get("tigl") if ds else None
    assert session_id, (
        f"open_cpacs did not return a session_id. Response: {open_resp}"
    )

    # 2. Generate the volume mesh with the pinned params, timed.
    start = time.monotonic()
    mesh_resp = tools["generate_volume_mesh"].forward(
        session_id=session_id,
        **_FAST_MESH_PARAMS,
    )
    elapsed = time.monotonic() - start

    assert elapsed < _MESH_TIME_BUDGET_SECONDS, (
        f"generate_volume_mesh took {elapsed:.1f}s — exceeds the budget "
        f"of {_MESH_TIME_BUDGET_SECONDS}s. Tune the params down. The "
        f"sequential staged_pipeline working run hit ~18 s with these "
        f"same params."
    )

    # 3. Sanity-check the mesh result has the expected shape.
    if isinstance(mesh_resp, dict):
        # Either the actual response or the data-plane intercept ref.
        assert (
            "mesh_base64" in mesh_resp
            or "ref" in str(mesh_resp)
            or "statistics" in mesh_resp
        ), f"Unexpected mesh response shape: {mesh_resp}"

    print(f"\nMesh generated in {elapsed:.1f}s with pinned params.")
