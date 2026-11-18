"""The main wing's planform read straight from a CPACS file, without a TiGL session (B274).

Why: the cruise-lift target, the aspect ratio for the induced-drag floor and the SU2 reference area all
need the wing of the file SU2 is about to solve. Until B274 they came only from a LIVE TiGL measurement
(a morph, get_wing_summary, get_high_level_parameters). When the geometry agent closed the TiGL session
before anyone measured the wing (l20_final O_SP 6 / 19, O_IF 4), there was none, so SU2 solved at a fixed
2 deg with the CPACS-declared reference area and no aspect ratio, and that untrimmed solve was flown.

What is computed, on the definitions tigl-mcp uses (tigl-mcp tools/morph.py `_measure`, planform.py):

* chord points: each section element's profile leading and trailing edge, carried through the element,
  section, positioning and wing transformations in TiGL's order (element, section, positioning, wing);
* ``reference_area``: the sum over segments of the area of the chord quadrilateral (inner LE, inner TE,
  outer TE, outer LE), unprojected, times 2 for a wing mirrored in the x-z plane (morph.py: wingGetReferenceArea(
  index, 0) * sym_factor);
* ``span``: the full span, twice the largest spanwise reach of the chord points for a mirrored wing;
* ``aspect_ratio``: span^2 / reference_area;
* ``sweep_quarter_chord_deg`` / ``taper_ratio``: planform.py's definitions (root c/4 -> tip c/4);
* ``mac_length``: the mean aerodynamic chord of the trapezoidal segments (span-weighted between the
  quarter-chord points). TiGL's wingGetMAC differs by about 1e-5 (relative); the friction drag uses this value
  for every unmorphed or exported design, so the same file always gives the same MAC.

The test ``tests/test_b274_cpacs_planform.py`` pins these against TiGL's own answers for the D150 baseline
and for morphed geometries of the 20-link study (computed on aipexws8 with tigl3).

Pure standard library: the data plane runs in the MAS-Aviary venv, which has no TiGL.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Any

Matrix = list[list[float]]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(node, name: str):
    if node is None:
        return None
    for c in node:
        if _local(c.tag) == name:
            return c
    return None


def _children(node, name: str) -> list:
    return [c for c in (node if node is not None else []) if _local(c.tag) == name]


def _num(node, default: float) -> float:
    try:
        return float(node.text) if node is not None and node.text is not None else default
    except ValueError:
        return default


def _xyz(node, default: float) -> tuple[float, float, float]:
    return tuple(_num(_child(node, k), default) for k in ("x", "y", "z"))  # type: ignore[return-value]


def _identity() -> Matrix:
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _mul(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _apply(m: Matrix, p) -> tuple[float, float, float]:
    x, y, z = p
    return tuple(m[i][0] * x + m[i][1] * y + m[i][2] * z + m[i][3] for i in range(3))  # type: ignore[return-value]


def _transformation(node) -> Matrix:
    """A CPACS <transformation>: scale, then rotate about x, y, z (degrees), then translate (TiGL's order)."""
    t = _child(node, "transformation")
    sx, sy, sz = _xyz(_child(t, "scaling"), 1.0)
    rx, ry, rz = (math.radians(v) for v in _xyz(_child(t, "rotation"), 0.0))
    tx, ty, tz = _xyz(_child(t, "translation"), 0.0)
    m = [[sx, 0, 0, 0], [0, sy, 0, 0], [0, 0, sz, 0], [0, 0, 0, 1.0]]
    cx, sxr = math.cos(rx), math.sin(rx)
    cy, syr = math.cos(ry), math.sin(ry)
    cz, szr = math.cos(rz), math.sin(rz)
    m = _mul([[1, 0, 0, 0], [0, cx, -sxr, 0], [0, sxr, cx, 0], [0, 0, 0, 1]], m)
    m = _mul([[cy, 0, syr, 0], [0, 1, 0, 0], [-syr, 0, cy, 0], [0, 0, 0, 1]], m)
    m = _mul([[cz, -szr, 0, 0], [szr, cz, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], m)
    return _mul([[1, 0, 0, tx], [0, 1, 0, ty], [0, 0, 1, tz], [0, 0, 0, 1]], m)


def _profile_chord(airfoil) -> tuple[tuple, tuple]:
    """Leading and trailing edge of a point-list profile: TE = midpoint of the first and last point,
    LE = the point farthest from the TE."""
    plist = _child(airfoil, "pointList")
    cols = []
    for k in ("x", "y", "z"):
        text = (_child(plist, k).text or "") if _child(plist, k) is not None else ""
        cols.append([float(v) for v in text.replace("\n", " ").split(";") if v.strip()])
    pts = list(zip(*cols))
    if len(pts) < 3:
        raise ValueError("profile has fewer than three points")
    te = tuple((a + b) / 2.0 for a, b in zip(pts[0], pts[-1]))
    le = max(pts, key=lambda p: math.dist(p, te))
    return le, te


def _positioning_points(wing) -> dict[str, tuple[float, float, float]]:
    """Each section's positioning translation (TiGL CCPACSPositioning: x += L sin(sweep),
    y += L cos(dihedral) cos(sweep), z += L sin(dihedral) cos(sweep), chained from fromSectionUID)."""
    raw = {}
    for pos in _children(_child(wing, "positionings"), "positioning"):
        to = (_child(pos, "toSectionUID").text or "").strip() if _child(pos, "toSectionUID") is not None else ""
        frm = _child(pos, "fromSectionUID")
        raw[to] = ((frm.text or "").strip() if frm is not None and frm.text else "",
                   _num(_child(pos, "length"), 0.0), math.radians(_num(_child(pos, "sweepAngle"), 0.0)),
                   math.radians(_num(_child(pos, "dihedralAngle"), 0.0)))
    out: dict[str, tuple[float, float, float]] = {}

    def point(uid: str, depth: int = 0) -> tuple[float, float, float]:
        if uid in out:
            return out[uid]
        if uid not in raw or depth > 100:
            return (0.0, 0.0, 0.0)
        frm, length, sweep, dihedral = raw[uid]
        base = point(frm, depth + 1) if frm else (0.0, 0.0, 0.0)
        out[uid] = (base[0] + length * math.sin(sweep),
                    base[1] + length * math.cos(dihedral) * math.cos(sweep),
                    base[2] + length * math.sin(dihedral) * math.cos(sweep))
        return out[uid]

    for uid in raw:
        point(uid)
    return out


def _quad_area(p0, p1, p2, p3) -> float:
    """Area of the quadrilateral p0-p1-p2-p3 (half the norm of the diagonals' cross product)."""
    d1 = [b - a for a, b in zip(p0, p2)]
    d2 = [b - a for a, b in zip(p1, p3)]
    cx = d1[1] * d2[2] - d1[2] * d2[1]
    cy = d1[2] * d2[0] - d1[0] * d2[2]
    cz = d1[0] * d2[1] - d1[1] * d2[0]
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def wing_chord_points(path, wing_uid: str | None = None) -> tuple[list[tuple[tuple, tuple]], bool]:
    """(LE, TE) per section boundary, root to tip, and whether the wing is mirrored in the x-z plane."""
    root = ET.parse(str(path)).getroot()
    wings = [w for w in root.iter() if _local(w.tag) == "wing" and w.get("uID")]
    if not wings:
        raise ValueError("no wing in the CPACS file")
    if wing_uid:
        wing = next((w for w in wings if w.get("uID") == wing_uid), None)
        if wing is None:
            raise ValueError(f"no wing {wing_uid!r} in the CPACS file")
    else:
        wing = next((w for w in wings if str(w.get("uID")).lower().startswith("wing")), wings[0])
    airfoils = {a.get("uID"): a for a in root.iter() if _local(a.tag) == "wingAirfoil"}
    wing_m = _transformation(wing)
    positions = _positioning_points(wing)
    elements: dict[str, Any] = {}
    for sec in _children(_child(wing, "sections"), "section"):
        sec_m = _mul(_transformation(sec), _identity())
        pos = positions.get(sec.get("uID"), (0.0, 0.0, 0.0))
        pos_m = [[1, 0, 0, pos[0]], [0, 1, 0, pos[1]], [0, 0, 1, pos[2]], [0, 0, 0, 1.0]]
        for el in _children(_child(sec, "elements"), "element"):
            uid_node = _child(el, "airfoilUID")
            airfoil = airfoils.get((uid_node.text or "").strip() if uid_node is not None else "")
            if airfoil is None:
                raise ValueError(f"element {el.get('uID')} names no known airfoil")
            le, te = _profile_chord(airfoil)
            m = _mul(wing_m, _mul(pos_m, _mul(sec_m, _transformation(el))))
            elements[el.get("uID")] = (_apply(m, le), _apply(m, te))
    segments = _children(_child(wing, "segments"), "segment")
    if not segments:
        raise ValueError("the wing has no segments")
    chain = []
    for i, seg in enumerate(segments):
        frm = (_child(seg, "fromElementUID").text or "").strip()
        to = (_child(seg, "toElementUID").text or "").strip()
        if frm not in elements or to not in elements:
            raise ValueError(f"segment {seg.get('uID')} names an unknown element")
        if i == 0:
            chain.append(elements[frm])
        chain.append(elements[to])
    return chain, str(wing.get("symmetry") or "").lower() == "x-z-plane"


def wing_planform(path, wing_uid: str | None = None) -> dict[str, float]:
    """The main wing's area, span, aspect ratio, c/4 sweep, taper and MAC from a CPACS file.

    Raises ValueError (or OSError / ParseError) when the file cannot be read as a wing.
    """
    sections, mirrored = wing_chord_points(path, wing_uid)
    factor = 2.0 if mirrored else 1.0
    area = 0.0
    mac_sum = mac_w = 0.0
    for (le0, te0), (le1, te1) in zip(sections, sections[1:]):
        a = _quad_area(le0, te0, te1, le1)
        c0, c1 = math.dist(le0, te0), math.dist(le1, te1)
        if c0 + c1 > 0:
            # Trapezoid panel: weight = mean chord x the panel span between the quarter-chord points (y-z plane).
            q0 = [p + 0.25 * (q - p) for p, q in zip(le0, te0)]
            q1 = [p + 0.25 * (q - p) for p, q in zip(le1, te1)]
            w = 0.5 * (c0 + c1) * math.hypot(q1[1] - q0[1], q1[2] - q0[2])
            mac_sum += w * (2.0 / 3.0) * (c0 * c0 + c0 * c1 + c1 * c1) / (c0 + c1)
            mac_w += w
        area += a
    if area <= 0:
        raise ValueError("the wing has no area")
    if mirrored:
        span = 2.0 * max(abs(p[1]) for le, te in sections for p in (le, te))
    else:
        span = max(abs(p[1] - sections[0][0][1]) for le, te in sections for p in (le, te))
    # tigl-mcp planform.py: root c/4 -> tip c/4, x aft, spanwise y (z for a fin).
    (root_le, root_te), (tip_le, tip_te) = sections[0], sections[-1]
    axis = 1 if abs(tip_le[1] - root_le[1]) >= abs(tip_le[2] - root_le[2]) else 2
    q0 = [a + 0.25 * (b - a) for a, b in zip(root_le, root_te)]
    q1 = [a + 0.25 * (b - a) for a, b in zip(tip_le, tip_te)]
    ds = abs(q1[axis] - q0[axis])
    sweep = math.degrees(math.atan((q1[0] - q0[0]) / ds)) if ds > 1e-9 else None
    root_chord, tip_chord = math.dist(root_le, root_te), math.dist(tip_le, tip_te)
    full_area = area * factor
    return {
        "reference_area": full_area,
        "span": span,
        "aspect_ratio": span * span / full_area,
        "sweep_quarter_chord_deg": sweep,
        "taper_ratio": tip_chord / root_chord if root_chord > 0 else None,
        "mac_length": mac_sum / mac_w if mac_w > 0 else None,
        "root_chord_m": root_chord,
        "tip_chord_m": tip_chord,
    }
