"""The design space the optimizer baselines search: the variables the agents can set (Jessica's prompt table,
sequential.md 'Set' rows), with their defaults and bounds.

Variables whose range the table leaves open ('not set': the tails, LPC PR) are held at their defaults. The
engine-cycle variables are available but off by default: in this framework pycycle's SFC does not reach the
mission's fuel burn (B3), so they cannot change the objective and would only spend evaluations.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Variable:
    name: str           # short id used in logs and plots
    tool: str           # "tigl" (set_high_level_parameters) or "pycycle" (set_inputs)
    target: str         # tigl component uid, or the pycycle input name
    key: str            # tigl updates key ("" for pycycle)
    default: float
    lower: float
    upper: float
    unit: str


GEOMETRY = (
    Variable("wing_area", "tigl", "Wing1", "area", 122.78, 100.0, 160.0, "m^2"),
    # The table's range is 7-17, 'reliable 9 to 11.5': above ~12 aviary's tables fail (best_practices).
    Variable("wing_aspect_ratio", "tigl", "Wing1", "aspect_ratio", 9.37, 9.0, 11.5, "-"),
    Variable("wing_sweep", "tigl", "Wing1", "sweep", 27.33, 15.0, 40.0, "deg"),
    Variable("fuselage_length", "tigl", "Fuselage1", "length", 37.68, 28.0, 50.0, "m"),
    Variable("fuselage_diameter", "tigl", "Fuselage1", "diameter", 4.15, 3.0, 5.5, "m"),
)

ENGINE = (
    # The table gives one-sided ranges here ('up to 10', '2857 and above', 'up to 30'); the other side is the
    # default, or a value chosen here and stated (T4_MAX upper 3200 degR).
    Variable("bypass_ratio", "pycycle", "splitter.BPR", "", 5.105, 5.105, 10.0, "-"),
    Variable("turbine_inlet_temperature", "pycycle", "T4_MAX", "", 2857.0, 2857.0, 3200.0, "degR"),
    Variable("fan_pressure_ratio", "pycycle", "fan.PR", "", 1.685, 1.4, 1.7, "-"),
    Variable("hpc_pressure_ratio", "pycycle", "hpc.PR", "", 9.369, 9.369, 30.0, "-"),
)

SETS = {"geometry": GEOMETRY, "geometry+engine": GEOMETRY + ENGINE}

# The mission's limits (the prompts' TOP-LEVEL REQUIREMENTS).
FUEL_TARGET_KG = 15000.0
GTOW_LIMIT_KG = 90000.0


def variables(which: str = "geometry") -> tuple[Variable, ...]:
    if which not in SETS:
        raise ValueError(f"unknown variable set {which!r}; choose from {sorted(SETS)}")
    return SETS[which]


def to_unit(x: list[float], vs: tuple[Variable, ...]) -> list[float]:
    """Physical values -> [0, 1] per variable (the optimizers work in the unit box)."""
    return [(xi - v.lower) / (v.upper - v.lower) for xi, v in zip(x, vs)]


def from_unit(u: list[float], vs: tuple[Variable, ...]) -> list[float]:
    """[0, 1] -> physical values, clipped to the bounds."""
    return [v.lower + min(1.0, max(0.0, ui)) * (v.upper - v.lower) for ui, v in zip(u, vs)]


def defaults(vs: tuple[Variable, ...]) -> list[float]:
    return [v.default for v in vs]
