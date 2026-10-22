"""Parse the integrator's free-text recommendations into per-parameter recommended changes.

Each recommendation is split into clauses ("A and B", "A, B", "A or B", "... by lowering BPR to 5"), and each clause
that names exactly one of the 16 plotted design parameters becomes one item:

    kind = "value"      a target value                  "Wing1 aspect_ratio 11.01 -> 12.0"
           "range"      a target range (lo, hi)         "SCALE_FACTOR from 1.87 to 1.5-1.6"
           "bound"      a target bound                  "SCALE_FACTOR from 1.97 to <=1.5"
           "pct"        a relative change in %          "Increase wing area by 5%"
           "direction"  up or down, no number           "Increase wing area"

A clause naming none of the parameters (CD, mission, optimizer, materials, ...) or two at once (ambiguous) is dropped
and listed in the audit table with the reason. Numbers are taken as written: the model's unit labels are not
converted (it sometimes writes T4 in K; the design records it in degR).
"""

from __future__ import annotations

import re

PARAM_PATTERNS = [
    ("Aircraft.Wing.AREA", r"\barea\b|area_m2"),
    ("Aircraft.Wing.ASPECT_RATIO", r"aspect[ _]?ratio"),
    ("Aircraft.Wing.SWEEP", r"sweep"),
    ("Aircraft.Wing.TAPER_RATIO", r"taper"),
    ("Aircraft.Fuselage.LENGTH", r"fuselage[ ._]?length"),
    ("Aircraft.Fuselage.MAX_HEIGHT", r"fuselage[ ._]?(?:max_)?height"),
    ("Aircraft.Fuselage.MAX_WIDTH", r"fuselage[ ._]?(?:max_)?width"),
    ("Aircraft.Engine.SCALE_FACTOR", r"scale[ _]?factor"),
    ("engine.T4_MAX", r"\bt4"),
    ("engine.splitter.BPR", r"\bbpr\b|bypass"),
    ("engine.fan.PR", r"fan[ ._]?pr\b|fan pressure"),
    ("engine.lpc.PR", r"\blpc"),
    ("engine.hpc.PR", r"\bhpc"),
    ("Mission.Design.LIFT_COEFFICIENT", r"lift[ _]coefficient|\bcl\b"),
    ("Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", r"subsonic_drag|drag[ _]coeff\w*[ _]factor"),
    ("Aircraft.Wing.MASS_SCALER", r"mass[ _]?scaler"),
]

UP = r"\b(increase|increasing|raise|higher|enlarge|extend|grow)\b"
DOWN = r"\b(reduce|reducing|decrease|decreasing|lower|lowering|cut|shrink|smaller)\b"
NUM = r"~?(\d+(?:\.\d+)?)"
# A purpose clause ("... to reduce fuel burn") is not part of the change itself.
PURPOSE = (r"\s+(?:in order |so as )?to\s+(?=(?:reduce|lower|improve|decrease|increase|enhance|resolve|meet|align|"
           r"achieve|allow|help|bridge|accommodate|enable|maintain|minimi|maximi|keep|address|bring|match|stay|"
           r"ensure|avoid|compensate|offset|support|balance|recalculate|update|obtain|retrieve|validate|confirm|"
           r"close|decrease)\b)")


def _clean(text: str) -> str:
    t = text.replace("²", "").replace("°", "")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)                                   # 8,900 -> 8900
    t = re.sub(r"between\s+" + NUM + r"\s+and\s+" + NUM, r"\1–\2", t, flags=re.I)  # keep the range in one clause
    return t


def clauses(text: str) -> list[str]:
    parts = re.split(r",\s*|;\s*|\s+and/or\s+|\s+and\s+|\s+or\s+|\s+by\s+(?=[a-z])", _clean(text), flags=re.I)
    return [re.split(PURPOSE, p, maxsplit=1, flags=re.I)[0].strip() for p in parts if p.strip()]


def _strip_identifiers(clause: str) -> str:
    """Drop tokens like Wing1, C4, SU2, T4_MAX so their digits are not read as values."""
    return re.sub(r"\b[A-Za-z_][A-Za-z0-9_.]*\d[A-Za-z0-9_.]*\b", " ", clause)


def _target(expr: str):
    """(kind, lo, hi) from the text after the arrow / 'to' / '='."""
    e = expr.strip()
    e = re.sub(r"^[^\d<>≤≥~]*?(?=(<=|>=|<|>|≤|≥|below|under|above|over|~)?\s*\d)", "", e, count=1, flags=re.I)
    m = re.match(r"(<=|<|≤|below|under)\s*" + NUM, e, flags=re.I)
    if m:
        return "bound", float(m.group(2)), float(m.group(2))
    m = re.match(r"(>=|>|≥|above|over)\s*" + NUM, e, flags=re.I)
    if m:
        return "bound", float(m.group(2)), float(m.group(2))
    m = re.match(NUM + r"\s*(?:–|-|to)\s*" + NUM, e)
    if m:
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        return "range", lo, hi
    m = re.match(NUM + r"\s*%", e)
    if m:
        return "pct", float(m.group(1)), float(m.group(1))
    m = re.match(NUM, e)
    if m:
        return "value", float(m.group(1)), float(m.group(1))
    return None


# Quantities that are not plotted rows; a clause naming one alongside a row is ambiguous ("CL/CD mismatch").
OTHER_QUANTITIES = r"\bcd\b|drag[ _]coefficient(?![ _]factor)"
# Clauses asking to check or study something, not to change it.
NOT_A_CHANGE = r"^\s*(evaluate|investigate|validate|conduct|reconcile)\b"


def parse_clause(clause: str) -> dict:
    if re.search(NOT_A_CHANGE, clause, flags=re.I):
        return {"clause": clause, "param": None, "reason": "not a change (evaluate/validate/...)"}
    hits = [col for col, pat in PARAM_PATTERNS if re.search(pat, clause, flags=re.I)]
    if hits and re.search(OTHER_QUANTITIES, clause, flags=re.I):
        hits.append("CD")
    if len(hits) != 1:
        return {"clause": clause, "param": None, "reason": "no parameter" if not hits else "ambiguous: " + ", ".join(hits)}
    item = {"clause": clause, "param": hits[0], "kind": None, "lo": None, "hi": None, "from": None, "direction": 0}
    if re.search(UP, clause, flags=re.I):
        item["direction"] = 1
    elif re.search(DOWN, clause, flags=re.I):
        item["direction"] = -1
    body = _strip_identifiers(clause)
    tgt = None
    m = re.search(r"(?:from\s+)?" + NUM + r"[^\d<>≤≥~%]*?(?:->|→|\bto\b)\s*(.*)$", body, flags=re.I)
    if m:
        item["from"] = float(m.group(1))
        tgt = _target(m.group(2))
    if tgt is None:
        if re.search(r"\bcurrent\b", body, flags=re.I) and not item["direction"]:
            return {**item, "reason": "states the current value, not a target"}
        m = re.search(r"(?:\bto\b|=|\bby\b)\s*(.*)$", body, flags=re.I) or re.search(
            r"((?:<=|<|≤|below|under)\s*\d.*)$", body, flags=re.I)
        if m:
            tgt = _target(m.group(1))
    if tgt:
        item["kind"], item["lo"], item["hi"] = tgt
        if item["kind"] == "pct" and item["direction"] == 0:
            return {**item, "kind": None, "reason": "percentage without a direction"}
    elif item["direction"]:
        item["kind"] = "direction"
    else:
        return {**item, "reason": "parameter named, no direction or value"}
    if item["kind"] in ("value", "range") and item["from"] is not None and item["direction"] == 0:
        mid = (item["lo"] + item["hi"]) / 2
        item["direction"] = 1 if mid > item["from"] else -1 if mid < item["from"] else 0
    item["reason"] = ""
    return item


def parse(text) -> list[dict]:
    if not isinstance(text, str) or not text.strip():
        return []
    return [parse_clause(c) for c in clauses(text)]


def recommended_change_pct(item: dict, current: float | None) -> float | None:
    """% change the item asks for relative to the parameter's value in the recommending link."""
    if item.get("kind") == "pct":
        return item["direction"] * item["lo"]
    if item.get("kind") in ("value", "range", "bound"):
        base = current if current not in (None, 0) and current == current else item.get("from")
        if not base:
            return None
        return 100.0 * ((item["lo"] + item["hi"]) / 2 - base) / abs(base)
    return None


def recommended_value(item: dict, current: float | None) -> float | None:
    if item.get("kind") in ("value", "range", "bound"):
        return (item["lo"] + item["hi"]) / 2
    if item.get("kind") == "pct" and current is not None and current == current:
        return current * (1 + item["direction"] * item["lo"] / 100.0)
    return None
