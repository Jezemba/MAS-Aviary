"""The design space the optimizer baselines search: by default ("agents") every variable the agents could set --
the prompt table's 'Set' rows (sequential.md / orchestrated.md / networked.md) that the framework neither replaces
(aviary geometry from tigl, CL / drag factor / wing mass scaler / engine scale from the coupled results) nor pins
(mission, mass method): 16 variables. The search box of each is the range the agents actually used; the six tail
variables were never changed by any agent, so they are held at the baseline and 10 are searched (main wing and
fuselage through tigl, five engine-cycle inputs through pycycle). "geometry" is the five tigl variables alone.
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


# BOUNDS (Jessica 2026-10-27): the minimum and maximum of every value the agents set in the 20-link study.
# agent_bounds.json holds them with where each extreme came from, searched over the measured geometry after every
# successful tigl morph, every successful pycycle set_inputs value and every finished link's end-state design
# (160 links; script: MAS-Aviary analysis/20link-metrics, analysis/link20/agent_bounds.py). The agents themselves
# had no hard bounds: the prompt table's ranges are advice and aviary's range check only warns.
# Note the wing extremes all come from networked graph-routed link 20's morphs (area 20.8-304.9 m^2, AR 2.5-36.3,
# sweep -77 deg) and T4 5531.67 from one orchestrated graph-routed call; link end states alone give area 60-136,
# AR 8.5-21.1, sweep -33.5-23.6, T4 1550-3240.
#
# DEFAULTS are the agents' starting design, the chain anchor, in the quantities tigl's set_high_level_parameters
# sets: sweep is the QUARTER-CHORD sweep (tigl-mcp morph.py / planform.py), measured on the baseline CPACS -- not
# the table's 27.33 / 32.21 / 40.40, which are leading-edge values and would morph the aircraft before the first
# evaluation. The fuselage diameter is max(height, width) = max(4.15, 3.96) (morph.py).
GEOMETRY = (
    Variable("wing_area", "tigl", "Wing1", "area", 122.78, 20.84, 304.93, "m^2"),
    Variable("wing_aspect_ratio", "tigl", "Wing1", "aspect_ratio", 9.37, 2.511, 36.282, "-"),
    Variable("wing_sweep", "tigl", "Wing1", "sweep", 21.65, -77.06, 23.62, "deg"),
    Variable("fuselage_length", "tigl", "Fuselage1", "length", 37.68, 37.218, 38.147, "m"),
    Variable("fuselage_diameter", "tigl", "Fuselage1", "diameter", 4.15, 3.649, 4.638, "m"),
)

# No agent changed a tail (min = max over 91 values), so the tails stay at the baseline: not searched, and the
# evaluator never morphs them (the baseline CPACS already has these values).
TAILS_FIXED = (
    Variable("htail_area", "tigl", "Wing2H", "area", 31.1184, 31.1184, 31.1184, "m^2"),
    Variable("htail_aspect_ratio", "tigl", "Wing2H", "aspect_ratio", 4.981, 4.981, 4.981, "-"),
    Variable("htail_sweep", "tigl", "Wing2H", "sweep", 28.0, 28.0, 28.0, "deg"),
    Variable("vtail_area", "tigl", "Wing3V", "area", 21.5, 21.5, 21.5, "m^2"),
    Variable("vtail_aspect_ratio", "tigl", "Wing3V", "aspect_ratio", 1.6069, 1.6069, 1.6069, "-"),
    Variable("vtail_sweep", "tigl", "Wing3V", "sweep", 37.2, 37.2, 37.2, "deg"),
)

ENGINE = (
    Variable("bypass_ratio", "pycycle", "splitter.BPR", "", 5.105, 3.5, 10.0, "-"),
    Variable("turbine_inlet_temperature", "pycycle", "T4_MAX", "", 2857.0, 1500.0, 5531.67, "degR"),
    Variable("fan_pressure_ratio", "pycycle", "fan.PR", "", 1.685, 1.2, 1.8, "-"),
    Variable("lpc_pressure_ratio", "pycycle", "lpc.PR", "", 1.935, 1.5, 2.8, "-"),
    Variable("hpc_pressure_ratio", "pycycle", "hpc.PR", "", 9.369, 1.8, 14.0, "-"),
)

# "agents": every variable the agents could set that any agent actually moved (the table's 'Set' rows that the
# framework does not replace or pin; the tails were settable but never moved, so they are held -- TAILS_FIXED;
# component efficiencies and optimizer_max_iter stay fixed, Jessica 2026-10-27).
SETS = {"geometry": GEOMETRY, "geometry+engine": GEOMETRY + ENGINE, "agents": GEOMETRY + ENGINE}

# The mission's requirements (the prompts' TOP-LEVEL REQUIREMENTS): "Objective: minimize fuel burn <= 15,000 kg"
# (the objective, with its target) and "Constraint: GTOW <= 90,000 kg" (the one inequality constraint).
FUEL_TARGET_KG = 15000.0
GTOW_LIMIT_KG = 90000.0


def variables(which: str = "agents") -> tuple[Variable, ...]:
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
