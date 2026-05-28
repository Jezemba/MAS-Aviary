"""Live test for SU2 CFD on a freshly generated F25 mesh.

Validates that the SU2 config the `aerodynamics_analyst` stage_prompt
tells workers to use ACTUALLY runs to convergence in reasonable
wall-clock time and extracts non-null CL/CD.

This was added after v17 of mdo_f25_orchestrated_staged_pipeline got
stuck iterating on `run_su2_solver` for 3 attempts: the orchestrated
worker guessed `SOLVER=RANS` (without KIND_TURB_MODEL / REYNOLDS_NUMBER)
on an Euler-class no-BL mesh, then ran out of context before fixing
itself. The fix is to pin the working Euler config from sequential
staged_pipeline (wandb u77aj8bg, 206 s solve) into the stage_prompt
so workers can't drift into RANS guesses.

Run with:
    pytest tests/test_su2_solver_live.py -m live_mcp -v -s

Requires:
    tigl-mcp on http://127.0.0.1:8500/mcp
    su2-mcp  on http://127.0.0.1:8200/mcp
    /home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml

This is the fast-iteration loop for SU2 config tuning: change the
config here, run the test, see the wall-clock + final residual.
Once happy, propagate the same config into
config/mdo_f25_staged_pipeline.yaml's `aerodynamics_analyst`
stage_prompt.
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


# Mesh params — keep in sync with tests/test_mesh_generation_live.py
# and config/mdo_f25_staged_pipeline.yaml's geometry stage_prompt.
# This produces a ~30 MB SU2 mesh in ~18 s with no boundary layer
# (Euler-grade), and that is exactly what the working sequential
# run (wandb u77aj8bg) used.
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

# SU2 Euler config — copied verbatim from the working sequential run
# (wandb u77aj8bg, /tmp/pipeline_mdo_f25_seq_staged_v2.log lines
# 360-388). Converged to rms_density < -8 in 77 inner iterations
# (~206 s on this box). Markers `aircraft` / `farfield` are what
# tigl-mcp's gmsh writes into the .su2 file — data_plane.py's
# _fix_su2_markers also auto-corrects guesses to these names.
_F25_CRUISE_EULER_CONFIG = {
    # ── Problem definition ────────────────────────────────────────────
    "SOLVER": "EULER",
    "MATH_PROBLEM": "DIRECT",
    "KIND_TURB_MODEL": "SA",
    "RESTART_SOL": "NO",
    # ── Freestream (FL330, Mach 0.78 cruise) ──────────────────────────
    "MACH_NUMBER": 0.78,
    "AOA": 2.0,
    "REYNOLDS_NUMBER": 30000000,
    "FREESTREAM_PRESSURE": 26500.0,
    "FREESTREAM_TEMPERATURE": 233.0,
    # ── Reference geometry (F25-class wing) ───────────────────────────
    "REF_DIMENSIONALIZATION": "DIMENSIONAL",
    "REF_AREA": 61.39076274891849,
    "REF_LENGTH": 4.192320937872386,
    "REF_ORIGIN_MOMENT_X": 15.280145964476011,
    "REF_ORIGIN_MOMENT_Y": 6.54558738384396,
    "REF_ORIGIN_MOMENT_Z": -0.7921210525374252,
    # ── Numerics ──────────────────────────────────────────────────────
    "CONV_NUM_METHOD_FLOW": "JST",
    "JST_SENSOR_COEFF": "( 0.5, 0.02 )",
    "NUM_METHOD_GRAD": "WEIGHTED_LEAST_SQUARES",
    "TIME_DISCRE_FLOW": "EULER_IMPLICIT",
    "CFL_NUMBER": 1000,
    "CFL_ADAPT": "YES",
    "CFL_ADAPT_PARAM": "( 0.1, 2.0, 10.0, 1e10 )",
    "MGCYCLE": "W_CYCLE",
    "MGLEVEL": 3,
    "LINEAR_SOLVER": "FGMRES",
    "LINEAR_SOLVER_PREC": "ILU",
    "LINEAR_SOLVER_ITER": 10,
    "LINEAR_SOLVER_ERROR": 1e-10,
    # ── Convergence ───────────────────────────────────────────────────
    "ITER": 200,
    "CONV_FIELD": "RMS_DENSITY",
    "CONV_RESIDUAL_MINVAL": -8,
    "CONV_STARTITER": 10,
    # ── I/O ───────────────────────────────────────────────────────────
    "HISTORY_OUTPUT": "( ITER, RMS_RES, AERO_COEFF )",
    "SCREEN_OUTPUT": "( INNER_ITER, RMS_DENSITY, LIFT, DRAG )",
    "OUTPUT_FILES": "( RESTART, PARAVIEW, SURFACE_CSV )",
    # ── Markers (these match what gmsh writes for the F25 mesh) ───────
    "MARKER_EULER": "( aircraft )",
    "MARKER_FAR": "( farfield )",
    "MARKER_PLOTTING": "( aircraft )",
    "MARKER_MONITORING": "( aircraft )",
}

_CPACS_PATH = (
    "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
)

# Sequential's working run hit 206 s on the solver alone, plus ~18 s
# mesh + ~15 s config plumbing. Budget 420 s to leave headroom for
# slower box load without giving infinite hangs a free pass.
_TOTAL_TIME_BUDGET_SECONDS = 420.0


def _check_mcp_alive(port: int) -> bool:
    """Cheap proof-of-life check — 406 / 405 means the streamable-http
    server is up but rejecting the bare GET, which is expected."""
    import urllib.request

    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/mcp", timeout=2)
    except Exception as e:
        msg = str(e).lower()
        if "406" in msg or "405" in msg or "method" in msg:
            return True
        return False
    return True


def _load_tigl_and_su2_tools():
    """Load tigl-mcp + su2-mcp tools directly through MCPConnector.
    Returns {name: tool}. Data-plane middleware is wired automatically
    by load_tools_for_agent (mesh marker capture, ref resolution,
    session injection, CL/CD capture from read_history_csv)."""
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
                MCPServerConfig(
                    name="su2",
                    url="http://127.0.0.1:8200/mcp",
                    transport="streamable-http",
                ),
            ],
        ),
    )
    tools = load_tools_for_agent([], cfg)
    return {t.name: t for t in tools}


def _unwrap(response):
    """Tool responses come back as either a JSON string or a dict
    depending on which path through the middleware they take. Always
    return a dict for assertions."""
    import json

    if isinstance(response, str):
        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            return {"_raw": response}
    if isinstance(response, dict):
        return response
    return {"_value": response}


def test_su2_euler_cruise_converges_with_pinned_config():
    """End-to-end check: open CPACS → generate mesh → SU2 Euler solve
    → read history.csv → assert CL/CD extracted.

    If this fails, the orchestrated_staged_pipeline aerodynamics
    stage will fail too — TUNE THE CONFIG above until it passes,
    then propagate to config/mdo_f25_staged_pipeline.yaml's
    aerodynamics_analyst stage_prompt.
    """
    if not _check_mcp_alive(8500):
        pytest.skip("tigl-mcp not reachable at 127.0.0.1:8500")
    if not _check_mcp_alive(8200):
        pytest.skip("su2-mcp not reachable at 127.0.0.1:8200")
    if not os.path.isfile(_CPACS_PATH):
        pytest.skip(f"CPACS fixture missing: {_CPACS_PATH}")

    tools = _load_tigl_and_su2_tools()
    for need in (
        "open_cpacs",
        "generate_volume_mesh",
        "create_su2_session",
        "set_mesh",
        "update_config_entries",
        "run_su2_solver",
        "read_history_csv",
    ):
        assert need in tools, f"missing required tool: {need}"

    overall_start = time.monotonic()

    # ── Step 1: open CPACS ────────────────────────────────────────
    open_resp = _unwrap(tools["open_cpacs"].forward(
        source_type="path",
        source=_CPACS_PATH,
    ))
    # tigl session id is captured into design_state by the middleware;
    # we don't actually need the value here, but assert no error.
    assert "error" not in open_resp, f"open_cpacs failed: {open_resp}"

    # ── Step 2: generate the volume mesh ──────────────────────────
    mesh_start = time.monotonic()
    mesh_resp = _unwrap(tools["generate_volume_mesh"].forward(
        **_FAST_MESH_PARAMS,
    ))
    mesh_elapsed = time.monotonic() - mesh_start
    assert "error" not in mesh_resp, f"generate_volume_mesh failed: {mesh_resp}"
    # mesh_base64 is intercepted into a ref by the middleware. Either
    # form is acceptable — we pass the ref string into set_mesh below
    # and resolve_request swaps it for the real bytes.
    mesh_b64_field = mesh_resp.get("mesh_base64")
    assert mesh_b64_field, f"no mesh_base64 in mesh response: {mesh_resp}"

    # ── Step 3: create SU2 session ────────────────────────────────
    su2_resp = _unwrap(tools["create_su2_session"].forward(
        base_name="f25_cruise_test",
    ))
    assert "error" not in su2_resp, f"create_su2_session failed: {su2_resp}"
    su2_sid = su2_resp.get("session_id")
    assert su2_sid, f"no session_id in create_su2_session response: {su2_resp}"

    # ── Step 4: attach mesh to the SU2 session ────────────────────
    # The ref string "generate_volume_mesh__mesh_base64" matches what
    # the data-plane middleware stored. resolve_request will swap it
    # for the actual base64 payload before the call leaves.
    set_mesh_resp = _unwrap(tools["set_mesh"].forward(
        session_id=su2_sid,
        mesh_base64="generate_volume_mesh__mesh_base64",
        mesh_file_name="mesh.su2",
        update_config=True,
    ))
    assert "error" not in set_mesh_resp, f"set_mesh failed: {set_mesh_resp}"

    # ── Step 5: write the F25 Euler cruise config ─────────────────
    upd_resp = _unwrap(tools["update_config_entries"].forward(
        session_id=su2_sid,
        updates=_F25_CRUISE_EULER_CONFIG,
        create_if_missing=True,
    ))
    assert "error" not in upd_resp, f"update_config_entries failed: {upd_resp}"
    missing = upd_resp.get("missing_required") or []
    assert not missing, (
        f"SU2 reports missing required keys after our config: {missing}. "
        f"Add them to _F25_CRUISE_EULER_CONFIG above."
    )

    # ── Step 6: run the solver ────────────────────────────────────
    solve_start = time.monotonic()
    run_resp = _unwrap(tools["run_su2_solver"].forward(
        session_id=su2_sid,
        solver="SU2_CFD",
        max_runtime_seconds=300,
        capture_log_lines=100,
    ))
    solve_elapsed = time.monotonic() - solve_start
    assert run_resp.get("success") is True, (
        f"run_su2_solver did NOT report success. exit_code="
        f"{run_resp.get('exit_code')}, log_tail={run_resp.get('log_tail', '')[:600]}"
    )

    # ── Step 7: pull CL/CD from history.csv ───────────────────────
    hist_resp = _unwrap(tools["read_history_csv"].forward(
        session_id=su2_sid,
        relative_path="history.csv",
        max_rows=1000,
        skip_rows=0,
    ))
    rows = hist_resp.get("rows")
    assert isinstance(rows, list) and rows, (
        f"history.csv had no rows: {hist_resp}"
    )

    # Match either the clean key ("CL") or SU2's quoted variant.
    def _pick(d: dict, name: str):
        if name in d:
            return d[name]
        for k, v in d.items():
            if isinstance(k, str) and k.strip().strip('"').strip("'").strip() == name:
                return v
        return None

    last = rows[-1]
    cl = _pick(last, "CL")
    cd = _pick(last, "CD")
    assert cl is not None and cd is not None, (
        f"CL/CD missing from last row of history.csv. Last row keys: "
        f"{list(last.keys())[:8]}"
    )
    cl_f = float(cl)
    cd_f = float(cd)

    # ── Sanity envelopes for F25 cruise (Mach 0.78, AoA 2°) ───────
    # Sequential u77aj8bg converged at CL≈0.187, CD≈0.0128. Allow a
    # wide bracket so we catch obvious garbage (NaN, negatives,
    # order-of-magnitude wrong) but don't false-fail on minor mesh
    # variation.
    assert 0.05 < cl_f < 0.6, f"CL={cl_f} outside [0.05, 0.6] envelope"
    assert 0.002 < cd_f < 0.05, f"CD={cd_f} outside [0.002, 0.05] envelope"

    # ── Time budget ───────────────────────────────────────────────
    total_elapsed = time.monotonic() - overall_start
    assert total_elapsed < _TOTAL_TIME_BUDGET_SECONDS, (
        f"Total time {total_elapsed:.1f}s exceeds budget "
        f"{_TOTAL_TIME_BUDGET_SECONDS}s. Sequential reference ~240 s."
    )

    print(
        f"\nSU2 Euler cruise converged: CL={cl_f:.4f}, CD={cd_f:.4f}, "
        f"L/D={cl_f / cd_f:.2f}\n"
        f"  mesh:  {mesh_elapsed:.1f}s\n"
        f"  solve: {solve_elapsed:.1f}s\n"
        f"  total: {total_elapsed:.1f}s"
    )
