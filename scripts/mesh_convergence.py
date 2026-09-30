#!/usr/bin/env python
"""Mesh convergence study for the F25 baseline (B116, closing out B6/B45).

Finds the mesh that is fine enough to give real drag and still coarse enough to validate
quickly. For each level it meshes the baseline, solves at a FIXED angle (so levels are
comparable), and records cells, wall time, CL and CD.

The number to watch is CD. At CL ~0.13 the induced drag is CL^2/(pi*AR*e) ~ +0.0007, so a
converged level should land in the +0.001..0.005 band; -0.0124 at 736k cells is the error
being ~18x the quantity. Levels run coarse-first so a runaway is caught before it is paid for.

    python scripts/mesh_convergence.py            # the whole ladder
    python scripts/mesh_convergence.py --levels 0 1
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CPACS = "/home/jezemba/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
PY = "/home/jezemba/Avion/.venv/bin/python"
OUT = REPO / "logs" / "mesh_convergence"

# (name, far_field_distance, mesh_size_min, mesh_size_max)
#
# tigl-mcp refuses anything over 3,000,000 cells (B69: meshing blocks that server for every
# caller), and its own advice is that far-field distance is CUBIC in cost while what drag needs is
# resolution ON the aircraft. So the ladder holds the domain roughly fixed and refines the surface,
# which is where the pressure integral that becomes CD is computed.
#
# L0 is the calibrated default, the one this week's acceptance solve used (736k cells, CD -0.0124).
# The canonical preset's pinned far_field 5.0 / mesh_size_max 2.0 (B45) is not in the ladder because
# it cannot run at all: it estimates ~6.9M cells and is refused.
LEVELS = [
    ("L0_default", 10.0, None, None),
    ("L1_surface_x2", 10.0, 0.33, 12.0),
    ("L2_surface_x4", 12.0, 0.17, 8.0),
    ("L3_surface_x6", 12.0, 0.11, 6.0),
]


def mcp(tool: str, args: dict, timeout: int = 5400) -> dict:
    """One MCP call through the project's own client, so the data plane behaves as in a run."""
    proc = subprocess.run(
        [PY, "scripts/mcp_call.py", tool, json.dumps(args)],
        cwd=REPO, capture_output=True, text=True, timeout=timeout,
    )
    text = proc.stdout
    if "{" not in text:
        raise RuntimeError(f"{tool}: no JSON in reply: {text[-400:]}")
    return json.loads(text[text.index("{"):])


def last_history_row(workdir: str) -> dict:
    path = Path(workdir) / "history.csv"
    rows = list(csv.reader(path.open()))
    head = [c.strip().strip('"') for c in rows[0]]
    return dict(zip(head, (float(v) for v in rows[-1])))


def run_level(name: str, far_field: float, size_min: float | None, size_max: float | None) -> dict:
    t0 = time.time()
    mcp("open_cpacs", {"source_type": "path", "source": CPACS})

    mesh_args: dict = {"far_field_distance": far_field, "output_format": "su2"}
    if size_min is not None:
        mesh_args["mesh_size_min"] = size_min
    if size_max is not None:
        mesh_args["mesh_size_max"] = size_max
    mesh = mcp("generate_volume_mesh", mesh_args)
    t_mesh = time.time() - t0
    stats = mesh.get("statistics") or {}

    session = mcp("create_su2_session", {
        "cpacs_file_path": CPACS,
        "initial_mesh": "generate_volume_mesh__mesh_base64",
        "mesh_file_name": "mesh.su2",
    })
    workdir = session["workdir"]
    mcp("configure_from_cpacs", {"cpacs_file_path": CPACS})     # fixed AOA: levels stay comparable

    t1 = time.time()
    solve = mcp("run_su2_solver", {"max_runtime_seconds": 5000, "capture_log_lines": 20})
    t_solve = time.time() - t1

    row = {
        "level": name, "far_field": far_field,
        "mesh_size_min": size_min, "mesh_size_max": size_max,
        "nodes": stats.get("node_count"), "cells": stats.get("element_count"),
        "mesh_seconds": round(t_mesh, 1), "solve_seconds": round(t_solve, 1),
        "solver_ok": solve.get("success"), "workdir": workdir,
    }
    coeffs = solve.get("final_coefficients") or {}
    if coeffs:
        row["CL"], row["CD"] = coeffs.get("CL"), coeffs.get("CD")
    else:
        try:
            last = last_history_row(workdir)
            row["CL"], row["CD"] = last.get("CL"), last.get("CD")
        except Exception:
            row["CL"] = row["CD"] = None
    try:
        row["rms_rho"] = last_history_row(workdir).get("rms[Rho]")
    except Exception:
        row["rms_rho"] = None
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", nargs="*", type=int, help="indices into LEVELS (default: all)")
    args = ap.parse_args()
    chosen = [LEVELS[i] for i in args.levels] if args.levels else LEVELS

    OUT.mkdir(parents=True, exist_ok=True)
    results_path = OUT / "results.jsonl"
    print(f"{'level':22s} {'cells':>9s} {'mesh s':>7s} {'solve s':>8s} {'CL':>8s} {'CD':>10s}  verdict")
    for name, far_field, size_min, size_max in chosen:
        subprocess.run([PY, "scripts/mcp_call.py", "RESET"], cwd=REPO, capture_output=True)
        try:
            row = run_level(name, far_field, size_min, size_max)
        except Exception as exc:                 # a level that cannot be built is a result too
            row = {"level": name, "far_field": far_field, "mesh_size_min": size_min,
                   "mesh_size_max": size_max, "error": f"{exc.__class__.__name__}: {exc}"}
        with results_path.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        if "error" in row:
            print(f"{name:22s} {'-':>9s} {'-':>7s} {'-':>8s} {'-':>8s} {'-':>10s}  FAILED: {row['error'][:60]}")
            continue
        cd = row.get("CD")
        verdict = "no CD" if cd is None else ("NEGATIVE - not converged in mesh" if cd <= 0
                                              else "positive" + (" and plausible" if 0.0005 <= cd <= 0.01 else ""))
        print(f"{name:22s} {str(row['cells']):>9s} {row['mesh_seconds']:>7.0f} "
              f"{row['solve_seconds']:>8.0f} {row['CL']:>8.4f} {cd:>10.5f}  {verdict}")
        sys.stdout.flush()
    print(f"\nrows appended to {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
