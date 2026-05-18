"""Phase G live regression: agent-driven volume meshes must be CFD-correct.

Before Phase G, tigl-mcp's ``generate_volume_mesh`` embedded the wing
surface as a 2D shell inside the fluid box, so SU2 ran on a mesh with
fluid cells *inside* the wing solid and produced physically impossible
forces (negative drag). The fix lives in
``tigl-mcp/feat/cfd-ready-volume-mesh`` (commit ``30e3474``): the STL
is sewn into a watertight OCC solid and boolean-cut from the box.

This test exercises the full agent → tigl-mcp pipeline against the
restarted server, decodes the produced mesh, and verifies the contract
the unit test in tigl-mcp pins down: no fluid mesh node lies inside
the wing solid envelope.

Run on demand (costs Anthropic API tokens, requires tigl-mcp listening
on port 8500):

    pytest tests/test_phase_g_volume_mesh_regressions.py \\
        -m live_mcp_llm -v --no-cov
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

try:
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
    pass


# Reuse the test harness from test_run3_regressions.py
from tests.test_run3_regressions import _collect_tool_calls, _make_agent  # noqa: E402


def _decode_su2_mesh_from_observation(observation: str) -> bytes:
    """Decode the mesh from an agent tool observation.

    The data plane intercepts the base64 mesh and replaces it with
    ``{"ref": "generate_volume_mesh__mesh_base64", "size_bytes": ...}``
    in the agent's view. The original base64 string lives in the
    DesignState data store under that ref key.
    """
    import json
    import re

    from src.tools.data_plane import get_design_state

    # Path 1: data-plane intercepted — pull base64 string from data_store.
    match = re.search(
        r'"ref"\s*:\s*"(generate_volume_mesh__mesh_base64[^"]*)"', observation
    )
    if match:
        ds = get_design_state()
        if ds is None:
            raise AssertionError(
                "Observation referenced data-store key but data plane not"
                " initialized"
            )
        mesh_b64 = ds.data_store.get(match.group(1))
        if not isinstance(mesh_b64, str):
            raise AssertionError(
                f"data_store[{match.group(1)!r}] is "
                f"{type(mesh_b64).__name__}, expected str"
            )
        return base64.b64decode(mesh_b64)

    # Path 2: raw base64 inline in the observation (no data plane).
    try:
        data = json.loads(observation)
        mesh_b64 = data.get("mesh_base64")
        if isinstance(mesh_b64, str):
            return base64.b64decode(mesh_b64)
    except Exception:  # noqa: BLE001
        pass

    match = re.search(r'"mesh_base64"\s*:\s*"([^"]+)"', observation)
    if match:
        return base64.b64decode(match.group(1))

    raise AssertionError(
        "generate_volume_mesh observation did not include a recognizable "
        "mesh_base64 field or data-plane ref: " + observation[:200]
    )


def _parse_su2(mesh_bytes: bytes) -> dict:
    """Parse SU2 ASCII mesh — return point coordinates and marker nodes."""
    text = mesh_bytes.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    n_lines = len(lines)

    points: list[tuple[float, float, float]] = []
    marker_nodes: dict[str, set[int]] = {}

    i = 0
    while i < n_lines:
        line = lines[i].strip()
        if line.startswith("NPOIN"):
            npoint = int(line.split("=")[1])
            for j in range(npoint):
                parts = lines[i + 1 + j].split()
                points.append((float(parts[0]), float(parts[1]), float(parts[2])))
            i += 1 + npoint
            continue
        if line.startswith("MARKER_TAG"):
            tag = line.split("=")[1].strip()
            i += 1
            nelem = int(lines[i].split("=")[1])
            ids: set[int] = set()
            for k in range(nelem):
                row = lines[i + 1 + k].split()
                etype = int(row[0])
                if etype == 5:    # triangle
                    ids.update(int(x) for x in row[1:4])
                elif etype == 9:  # quad
                    ids.update(int(x) for x in row[1:5])
            marker_nodes[tag] = ids
            i += 1 + nelem
            continue
        i += 1

    return {"points": points, "markers": marker_nodes}


@pytest.mark.live_mcp_llm
def test_geometry_agent_produces_cfd_correct_volume_mesh():
    """Drive a real Claude agent through generate_volume_mesh and verify
    the mesh has the topology a CFD solve actually needs.

    The Phase F live runs converged SU2 against meshes the agent produced
    here, only to discover the meshes were degenerate (fluid on both
    sides of the wing surface). This test catches that class of bug at
    the agent ↔ tigl-mcp boundary, BEFORE wasting a 10-15 min SU2 solve.
    """
    cpacs_path = (
        "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
    )
    agent = _make_agent(
        tool_names=[
            "open_cpacs",
            "get_configuration_summary",
            "get_wing_summary",
            "generate_volume_mesh",
            "close_cpacs",
        ],
        servers=["tigl"],
        max_steps=8,
        instructions=(
            "You are testing the volume-mesh tool. Open the CPACS file, "
            "inspect the wing once with get_wing_summary, then call "
            "generate_volume_mesh on the wing with "
            "surface_mesh_size=1.0, boundary_layer_enabled=False, "
            "far_field_distance=10.0. Then close_cpacs. Do NOT modify any "
            "geometry. Report the mesh stats."
        ),
    )
    agent.run(
        f"Open the CPACS file at {cpacs_path}. Get the wing summary. "
        "Then generate a coarse volume mesh of the wing (use "
        "surface_mesh_size=1.0, boundary_layer_enabled=false, "
        "far_field_distance=10.0). Then close the CPACS session."
    )

    calls = _collect_tool_calls(agent)
    mesh_calls = [
        c
        for c in calls
        if c["name"] == "generate_volume_mesh" and c.get("observation")
    ]
    assert mesh_calls, "Agent never called generate_volume_mesh"

    # Use the LAST successful mesh call (some calls may retry-error first).
    mesh_bytes = None
    for call in reversed(mesh_calls):
        try:
            mesh_bytes = _decode_su2_mesh_from_observation(call["observation"])
            break
        except AssertionError:
            continue
    assert mesh_bytes is not None, (
        "No generate_volume_mesh call returned a decodable mesh_base64 field. "
        f"Observations: {[c['observation'][:200] for c in mesh_calls]}"
    )

    parsed = _parse_su2(mesh_bytes)
    points = parsed["points"]
    markers = parsed["markers"]
    assert points, "Mesh has no NPOIN section"
    assert "aircraft" in markers, (
        f"Mesh missing 'aircraft' marker: {sorted(markers)}"
    )
    assert "farfield" in markers, (
        f"Mesh missing 'farfield' marker: {sorted(markers)}"
    )

    surface_ids = markers["aircraft"]
    surface_pts = [points[i] for i in surface_ids]
    assert len(surface_pts) > 50, (
        f"'aircraft' marker has only {len(surface_pts)} nodes — too coarse "
        "to validate the topology"
    )

    # The wing surface's centroid is the most reliably-inside-the-solid
    # location for any airfoil-like wing (mid-chord, mid-span,
    # mid-thickness). Test that no fluid (non-surface) node lies within
    # a generous sphere around it. For a swept wing the chordwise extent
    # is the smallest dimension; pick the radius from the thickness
    # rather than the chord/span so the sphere stays inside the wing
    # across the whole span.
    cx = sum(p[0] for p in surface_pts) / len(surface_pts)
    cy = sum(p[1] for p in surface_pts) / len(surface_pts)
    cz = sum(p[2] for p in surface_pts) / len(surface_pts)
    half_thickness = 0.5 * (max(p[2] for p in surface_pts) - min(p[2] for p in surface_pts))
    radius = 0.5 * half_thickness  # well inside the wing's thickness envelope

    interior_count = 0
    sample_offenders: list[tuple[float, float, float]] = []
    for idx, p in enumerate(points):
        if idx in surface_ids:
            continue
        dx, dy, dz = p[0] - cx, p[1] - cy, p[2] - cz
        if dx * dx + dy * dy + dz * dz <= radius * radius:
            interior_count += 1
            if len(sample_offenders) < 5:
                sample_offenders.append(p)

    assert interior_count == 0, (
        f"PHASE G REGRESSION: {interior_count} fluid mesh nodes lie inside "
        f"the sphere of radius {radius:.3f} around the wing-surface "
        f"centroid ({cx:.2f}, {cy:.2f}, {cz:.2f}). "
        f"Sample offenders: {sample_offenders}. "
        "This means tigl-mcp's generate_volume_mesh reverted to embedding "
        "the wing surface as a 2D shell rather than cutting it out of the "
        "fluid box. Re-check tigl-mcp branch and restart the server."
    )
