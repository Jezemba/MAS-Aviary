"""Typed discipline-variable coupling — the data model for the MDO pipeline.

Each discipline MCP produces named, typed OUTPUT variables; downstream disciplines
CONSUME them as explicit inputs. This module is the single source of truth for:

  1. CANONICAL_VARS  — the schema (name, producer, unit, consumers, description).
  2. The named, DOCUMENTED transforms that map a physical output to the specific
     input a consumer needs (e.g. SU2 inviscid CD -> aviary's drag-scale factor).
     These replace the formulas that were previously buried inside the data-plane
     middleware, so the coupling is explicit and reviewable, not hidden.
  3. read/write helpers over a typed registry kept on the DesignState.

Design intent (per Jessica, 2026-07-28): discipline outputs are first-class typed
variables fed as inputs; the COORDINATION STRUCTURE decides how a produced var
reaches a consumer (sequential hand-off / networked blackboard / graph route /
orchestrated dispatch) — that routing is the experiment's variable and is NOT
hardcoded here. The middleware's scrape+inject path becomes a flagged FALLBACK.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Registry lives on DesignState.data_store under this key (a flat name->value dict).
REGISTRY_KEY = "analysis_vars"


@dataclass(frozen=True)
class VarSpec:
    name: str            # canonical dotted name, e.g. "aero.cd_cruise"
    producer: str        # discipline that writes it
    unit: str
    consumers: tuple[str, ...]   # disciplines that read it
    description: str


# The canonical cross-discipline variable schema. Add here, not ad hoc.
CANONICAL_VARS: dict[str, VarSpec] = {
    "aero.cl_cruise": VarSpec("aero.cl_cruise", "su2", "-", (),
                              "SU2 cruise lift coefficient of the trimmed solve (recorded; not used for drag)."),
    "aero.cd_cruise": VarSpec("aero.cd_cruise", "su2", "-", (),
                              "SU2 cruise inviscid drag coefficient (recorded; aviary flies its FLOPS drag)."),
    "aero.l_over_d": VarSpec("aero.l_over_d", "su2", "-", (),
                             "SU2 cruise lift-to-drag ratio (diagnostic)."),
    "mass.wing_kg": VarSpec("mass.wing_kg", "mass", "kg", ("aviary",),
                            "mass-mcp structural wing mass."),
    "aero.target_cl": VarSpec("aero.target_cl", "mass", "-", ("su2",),
                              "Cruise lift coefficient the aircraft must make, W/(q*S) -- an INPUT "
                              "to the aero solve, not something aero may choose (B111)."),
    "mass.mtom_kg": VarSpec("mass.mtom_kg", "mass", "kg", ("pycycle",),
                            "mass-mcp maximum take-off mass (sizes engine thrust)."),
    "prop.sfc_cruise": VarSpec("prop.sfc_cruise", "pycycle", "lb/hr/lbf", ("aviary",),
                               "pycycle design-point TSFC. Coupled through the engine deck (B3): aviary flies "
                               "the off-design table of this cycle, not a single TSFC value."),
    "prop.fn_lbf": VarSpec("prop.fn_lbf", "pycycle", "lbf", (),
                           "pycycle net thrust at design point (diagnostic)."),
    # Geometry reference data -- SU2's reference area and length (the friction build-up that also read the wetted
    # areas was removed with the SU2 drag coupling, drag model decision of 2026-11-18).
    "geom.mac_length": VarSpec("geom.mac_length", "tigl", "m", ("su2",),
                               "mean aerodynamic chord -- SU2 reference length."),
    "geom.reference_area": VarSpec("geom.reference_area", "tigl", "m^2", ("su2",),
                                   "wing reference (planform) area -- SU2 CL/CD normalisation."),
    "aviary.cruise_l_over_d": VarSpec("aviary.cruise_l_over_d", "aviary", "-", ("pycycle",),
                                      "aviary's FLOPS lift-to-drag of the current design at the cruise point "
                                      "(Mach, altitude, CL = W/(qS) for the trim mass); sizes Fn_DES."),
}

# ---------------------------------------------------------------------------
# Named, documented transforms.
#
# Drag model decision (Jessica, 2026-11-18): drag is aviary's FLOPS model only. The SU2-to-aviary drag factor,
# the skin-friction build-up and the SU2-CL design-CL injection were REMOVED (not bypassed): SU2 Euler CD on the
# study mesh is numerical (negative at M0.5; dCD/dCL^2 the same at AR 9.4 and 13). SU2 still solves every design,
# trimmed, and its CL/CD are recorded only.
# ---------------------------------------------------------------------------


def isa_density_temperature(altitude_ft: float) -> tuple[float, float]:
    """ISA atmosphere density (kg/m^3) and temperature (K) — troposphere + lower strat."""
    import math
    h = float(altitude_ft) * 0.3048
    if h <= 11000.0:
        T = 288.15 - 0.0065 * h
        p = 101325.0 * (T / 288.15) ** 5.25588
    else:
        T = 216.65
        p = 22632.06 * math.exp(-9.80665 * 0.0289644 * (h - 11000.0) / (8.31447 * 216.65))
    rho = p / (287.058 * T)
    return rho, T


def cruise_lift_coefficient(mass_kg: float, mach: float, altitude_ft: float,
                            wing_area_m2: float) -> float | None:
    """The CL the aircraft must make to hold altitude: CL = W / (q S) (B111).

    Lift equals weight in level flight, so a cruise CL is decided by mass, the flight state and the
    wing -- never by the aero solver. geoauth_all8_7960 had it backwards: SU2 flew at a fixed 2 deg,
    produced CL 0.05-0.22 where this aircraft needs ~0.55, and that number was then handed to aviary
    as the design cruise CL. Returns None if any input is missing or unusable.
    """
    import math

    try:
        mass_kg, mach = float(mass_kg), float(mach)
        area = float(wing_area_m2)
        rho, temperature = isa_density_temperature(float(altitude_ft))
    except (TypeError, ValueError):
        return None
    if mass_kg <= 0 or mach <= 0 or area <= 0:
        return None
    speed = mach * math.sqrt(1.4 * 287.058 * temperature)
    q = 0.5 * rho * speed * speed
    if q <= 0:
        return None
    cl = (mass_kg * 9.80665) / (q * area)
    # A transport in cruise sits near 0.5. Outside this band the inputs are wrong (a semi-span area,
    # a mass in the wrong units), and trimming to a nonsense target is worse than not trimming.
    return cl if 0.05 <= cl <= 1.2 else None


# Engine sized to the design's cruise drag: Fn_DES = mass * g / (L/D) / engines / N-per-lbf * climb margin.
# Since the drag model decision of 2026-11-18 L/D is aviary's FLOPS lift-to-drag at the cruise point of the current
# design (aviary-mcp cruise_point), not SU2's. The old FN_DES_PER_KG_LBF 0.0811 = 9.81 / 17 / 2 / 4.448 * 1.25 was
# this formula at an assumed L/D of 17; it is gone (no assumed L/D).
_G = 9.81
_N_PER_LBF = 4.448
_N_ENGINES = 2
_CLIMB_THRUST_MARGIN = 1.25
# aviary bench engine (models/engines/turbofan_28k.deck, SCALE_FACTOR 1.0): maximum net thrust
# (gross - ram drag, throttle 50) at the mission cruise point M0.78 / 33,000 ft, interpolated from
# the deck rows M0.75/0.79 x 30,000/35,000 ft (6714.4, 6808.3, 5314.1, 5387.9 lbf).
BENCH_ENGINE_CRUISE_FN_LBF = 5936.0


def fn_des_from_lift_to_drag(mass_kg: float, lift_to_drag: float) -> float:
    """pycycle ``Fn_DES`` (lbf per engine) = mass * g / (L/D) / engines / 4.448 * 1.25.

    No clamp (B281, audit F2: was [2,000, 25,000] lbf). Only a physically impossible value fails: a non-positive
    mass or L/D, or a non-finite result.
    """
    mass, ld = float(mass_kg), float(lift_to_drag)
    if not (mass > 0.0 and ld > 0.0 and math.isfinite(mass) and math.isfinite(ld)):
        raise ValueError(f"design thrust from mass {mass_kg!r} kg and L/D {lift_to_drag!r}")
    return mass * _G / ld / _N_ENGINES / _N_PER_LBF * _CLIMB_THRUST_MARGIN


def fn_to_aviary_engine_scale(fn_lbf: float, reference_fn_lbf: float = BENCH_ENGINE_CRUISE_FN_LBF) -> float:
    """aviary ``Aircraft.Engine.SCALE_FACTOR`` that gives the flown engine pycycle's design thrust.

    The reference is the thrust the flown deck represents at SCALE_FACTOR 1.0: the bench deck's max
    cruise thrust (B170), or, with the designed engine's deck (B3), the Fn_DES it was generated at.
    """
    return float(fn_lbf) / float(reference_fn_lbf)


# ---------------------------------------------------------------------------
# Typed registry helpers (operate on a DesignState-like object with .data_store).
# ---------------------------------------------------------------------------

def registry(design_state) -> dict:
    if design_state is None:
        return {}
    return design_state.data_store.setdefault(REGISTRY_KEY, {})


def put_var(design_state, name: str, value, source_tool: str | None = None) -> None:
    """Write a typed discipline output into the registry (validated against schema)."""
    if design_state is None or name not in CANONICAL_VARS or value is None:
        return
    try:
        design_state.data_store.setdefault(REGISTRY_KEY, {})[name] = float(value)
        design_state.data_store.setdefault(REGISTRY_KEY + "__src", {})[name] = source_tool or CANONICAL_VARS[name].producer
    except (TypeError, ValueError):
        return


def get_var(design_state, name: str):
    return registry(design_state).get(name) if design_state is not None else None


def has_vars(design_state, names) -> bool:
    r = registry(design_state)
    return all(r.get(n) is not None for n in names)
