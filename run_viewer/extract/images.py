"""Render the per-link images: aircraft from a TiGL STL, SU2 surface pressure from surface_flow.csv.

Axes and colour ranges are FIXED across links so an N vs N-1 pair is comparable by eye.
"""

from __future__ import annotations

import csv
import math
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402

GAMMA = 1.4
R_AIR = 287.058
# Fixed frames (m); the DLR-F25/D150 family sits well inside these.
TOP_XLIM, TOP_YLIM = (-2.0, 48.0), (-26.0, 26.0)
FRONT_YLIM, FRONT_ZLIM = (-26.0, 26.0), (-5.0, 10.0)
CP_RANGE = (-1.2, 1.0)


def read_stl(path: str) -> np.ndarray:
    """Triangles (n, 3, 3) from an ASCII or binary STL."""
    with open(path, "rb") as f:
        head = f.read(5)
    if head == b"solid":
        verts = [list(map(float, line.split()[1:4]))
                 for line in open(path, errors="replace") if line.strip().startswith("vertex")]
        if verts:
            return np.asarray(verts).reshape(-1, 3, 3)
    data = np.fromfile(path, dtype=np.uint8)
    n = int.from_bytes(data[80:84].tobytes(), "little")
    rec = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    return np.frombuffer(data[84:84 + n * rec.itemsize].tobytes(), dtype=rec)["v"].astype(float)


def render_aircraft(stl_path: str, png_path: str, title: str = "") -> dict:
    """Top and front view of the aircraft; returns the measured extents."""
    tris = read_stl(stl_path)
    fig, (ax_top, ax_front) = plt.subplots(2, 1, figsize=(5, 6.2), height_ratios=[3.2, 1.2])
    for ax, (i, j), (xl, yl) in ((ax_top, (0, 1), (TOP_XLIM, TOP_YLIM)),
                                 (ax_front, (1, 2), (FRONT_YLIM, FRONT_ZLIM))):
        polys = tris[:, :, [i, j]]
        ax.add_collection(PolyCollection(polys, facecolor="#9aa7b8", edgecolor="#55657a",
                                         linewidths=0.05, alpha=0.9))
        ax.set_xlim(*xl)
        ax.set_ylim(*yl)
        ax.set_aspect("equal")
        ax.grid(alpha=0.25)
    ax_top.set_title(title or "Aircraft (TiGL)", fontsize=9)
    ax_top.set_xlabel("x [m]", fontsize=7)
    ax_top.set_ylabel("y [m]", fontsize=7)
    ax_front.set_xlabel("y [m]", fontsize=7)
    ax_front.set_ylabel("z [m]", fontsize=7)
    for ax in (ax_top, ax_front):
        ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(png_path, dpi=130)
    plt.close(fig)
    lo, hi = tris.reshape(-1, 3).min(0), tris.reshape(-1, 3).max(0)
    return {"length_m": float(hi[0] - lo[0]), "span_m": float(hi[1] - lo[1]),
            "height_m": float(hi[2] - lo[2]), "triangles": int(len(tris))}


def read_freestream(config_path: str) -> dict:
    """MACH_NUMBER, FREESTREAM_PRESSURE, FREESTREAM_TEMPERATURE from an SU2 config."""
    want = {"MACH_NUMBER", "FREESTREAM_PRESSURE", "FREESTREAM_TEMPERATURE"}
    out = {}
    for line in open(config_path):
        m = re.match(r"\s*([A-Z_]+)\s*=\s*([-+0-9.eE]+)\s*$", line)
        if m and m.group(1) in want:
            out[m.group(1)] = float(m.group(2))
    missing = want - set(out)
    if missing:
        raise ValueError(f"{config_path}: no {sorted(missing)}")
    return out


def surface_cp(csv_path: str, config_path: str) -> tuple[np.ndarray, np.ndarray]:
    """Points (n, 3) and pressure coefficient (n,) from SU2's conservative surface output."""
    fs = read_freestream(config_path)
    rows = list(csv.DictReader(open(csv_path), skipinitialspace=True))
    get = lambda k: np.array([float(r[k]) for r in rows])  # noqa: E731
    xyz = np.stack([get("x"), get("y"), get("z")], axis=1)
    rho, energy = get("Density"), get("Energy")
    mom2 = get("Momentum_x") ** 2 + get("Momentum_y") ** 2 + get("Momentum_z") ** 2
    p = (GAMMA - 1.0) * (energy - 0.5 * mom2 / rho)
    p_inf, t_inf = fs["FREESTREAM_PRESSURE"], fs["FREESTREAM_TEMPERATURE"]
    rho_inf = p_inf / (R_AIR * t_inf)
    v_inf = fs["MACH_NUMBER"] * math.sqrt(GAMMA * R_AIR * t_inf)
    return xyz, (p - p_inf) / (0.5 * rho_inf * v_inf ** 2)


def render_su2(csv_path: str, config_path: str, png_path: str, title: str = "") -> dict:
    """Upper-surface Cp seen from above (points drawn low-z first, so the top skin is visible)."""
    xyz, cp = surface_cp(csv_path, config_path)
    order = np.argsort(xyz[:, 2])
    fig, ax = plt.subplots(figsize=(5, 4.6))
    sc = ax.scatter(xyz[order, 0], xyz[order, 1], c=cp[order], s=3, cmap="coolwarm_r",
                    vmin=CP_RANGE[0], vmax=CP_RANGE[1], linewidths=0)
    # The SU2 mesh is a half model on y >= 0 when it has no negative-y points.
    half = bool((xyz[:, 1] >= -1e-6).all())
    ax.set_xlim(*TOP_XLIM)
    ax.set_ylim(-2.0 if half else TOP_YLIM[0], TOP_YLIM[1])
    ax.set_aspect("equal")
    ax.grid(alpha=0.25)
    ax.set_title(title or "SU2 surface Cp (top view)", fontsize=9)
    ax.set_xlabel("x [m]", fontsize=7)
    ax.set_ylabel("y [m]", fontsize=7)
    ax.tick_params(labelsize=6)
    cb = fig.colorbar(sc, ax=ax, shrink=0.8)
    cb.set_label("Cp", fontsize=7)
    cb.ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(png_path, dpi=130)
    plt.close(fig)
    return {"points": int(len(cp)), "cp_min": float(cp.min()), "cp_max": float(cp.max()),
            "half_model": half}
