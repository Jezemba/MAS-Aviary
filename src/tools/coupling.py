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
    "aero.cl_cruise": VarSpec("aero.cl_cruise", "su2", "-", ("aviary",),
                              "SU2 cruise lift coefficient (converged, AoA=2 deg)."),
    "aero.cd_cruise": VarSpec("aero.cd_cruise", "su2", "-", ("aviary",),
                              "SU2 cruise inviscid drag coefficient (converged)."),
    "aero.l_over_d": VarSpec("aero.l_over_d", "su2", "-", (),
                             "SU2 cruise lift-to-drag ratio (diagnostic)."),
    "mass.wing_kg": VarSpec("mass.wing_kg", "mass", "kg", ("aviary",),
                            "mass-mcp structural wing mass."),
    "mass.mtom_kg": VarSpec("mass.mtom_kg", "mass", "kg", ("pycycle",),
                            "mass-mcp maximum take-off mass (sizes engine thrust)."),
    "prop.sfc_cruise": VarSpec("prop.sfc_cruise", "pycycle", "lb/hr/lbf", ("aviary",),
                               "pycycle cruise TSFC (diagnostic; aviary uses its deck)."),
    "prop.fn_lbf": VarSpec("prop.fn_lbf", "pycycle", "lbf", (),
                           "pycycle net thrust at design point (diagnostic)."),
    # Geometry reference data — feeds the physics-based drag build-up (Reynolds + Swet/Sref).
    "geom.mac_length": VarSpec("geom.mac_length", "tigl", "m", ("su2",),
                               "mean aerodynamic chord — reference length for Reynolds."),
    "geom.wing_wetted_area": VarSpec("geom.wing_wetted_area", "tigl", "m^2", ("su2",),
                                     "wing wetted area — skin-friction drag build-up."),
    "geom.fuselage_wetted_area": VarSpec("geom.fuselage_wetted_area", "tigl", "m^2", ("su2",),
                                         "fuselage wetted area — skin-friction drag build-up."),
    "geom.reference_area": VarSpec("geom.reference_area", "tigl", "m^2", ("su2",),
                                   "wing reference (planform) area — drag/CL normalization."),
}

# ---------------------------------------------------------------------------
# Named, documented transforms (extracted verbatim from the former middleware
# formulas so behavior is unchanged; now explicit and unit-tested).
# ---------------------------------------------------------------------------

# aviary height_energy A320-class bench references.
AVIARY_BENCH_WING_MASS_KG = 5998.0     # MASS_SCALER = 1.0 corresponds to this wing.
FN_DES_PER_KG_LBF = 0.0811             # engine thrust per kg MTOM.
_AVIARY_CD0 = 0.022                    # aviary FLOPS baseline zero-lift CD.
_OSWALD = 0.85                         # span efficiency in the aviary induced-drag est.
# Full-aircraft wetted-area ratio Swet/Sref — nominal transport value, used as a fallback
# when the geometry-derived ratio isn't available. (Raymer: ~6 for a jet transport.)
_NOMINAL_SWET_SREF = 6.0
_WING_FORM_FACTOR = 1.25               # thickness/sweep form-factor on the friction drag.
# Default cruise Reynolds (M0.78, 33 kft, MAC~4 m) if flight state isn't supplied.
_NOMINAL_REYNOLDS = 26.0e6


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


def sutherland_viscosity(temperature_k: float) -> float:
    """Sutherland's law dynamic viscosity (Pa·s) of air."""
    T = float(temperature_k)
    return 1.716e-5 * (T / 273.15) ** 1.5 * (273.15 + 110.4) / (T + 110.4)


def reynolds_number(mach: float, altitude_ft: float, length_m: float) -> float:
    """Cruise Reynolds number from flight state + a reference length (MAC)."""
    import math
    rho, T = isa_density_temperature(altitude_ft)
    a = math.sqrt(1.4 * 287.058 * T)          # speed of sound
    v = float(mach) * a
    mu = sutherland_viscosity(T)
    return rho * v * float(length_m) / mu


def skin_friction_cd(reynolds: float, swet_sref: float, form_factor: float = _WING_FORM_FACTOR) -> float:
    """Turbulent flat-plate skin-friction drag coefficient (Schlichting) scaled by the
    wetted-area ratio and a form factor. This is the PARASITE/friction drag that an
    inviscid Euler solve physically cannot produce — real physics, not a fudge constant.
        Cf = 0.455 / (log10 Re)^2.58 ;  CD0_friction = Cf * (Swet/Sref) * FF
    """
    import math
    re = max(float(reynolds), 1.0e5)
    cf = 0.455 / (math.log10(re)) ** 2.58
    return cf * float(swet_sref) * float(form_factor)


def aero_cd_to_aviary_drag_factor(cd_inviscid: float, cl: float, aspect_ratio: float,
                                  reynolds: float | None = None,
                                  swet_sref: float | None = None) -> float:
    """Map SU2 aero to aviary's ``Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR`` via a
    PHYSICS-BASED drag build-up (Option A′):

        CD_total = cd_inviscid (SU2: real pressure/induced/wave drag)
                   + skin_friction_cd(Re, Swet/Sref)   (real viscous friction Euler misses)

    then factor = CD_total / aviary's FLOPS default drag at this CL. Re and Swet/Sref
    come from the actual flight state + morphed geometry (design-responsive); they fall
    back to nominal transport values if unavailable. Replaces the old fixed +0.005 fudge,
    which underestimated friction (the dominant drag) and produced fake near-zero drag.
    """
    ar_eff = max(float(aspect_ratio) if aspect_ratio is not None else 11.0, 8.0)
    re = reynolds if reynolds else _NOMINAL_REYNOLDS
    swr = swet_sref if swet_sref else _NOMINAL_SWET_SREF
    cd_total = float(cd_inviscid) + skin_friction_cd(re, swr)
    cd_aviary_default = _AVIARY_CD0 + (float(cl) ** 2) / (3.14159 * ar_eff * _OSWALD)
    factor = cd_total / cd_aviary_default
    return max(0.5, min(factor, 2.0))


def wing_mass_to_aviary_scaler(wing_kg: float) -> float:
    """Map mass-mcp wing mass to aviary's ``Aircraft.Wing.MASS_SCALER``."""
    return max(0.5, min(float(wing_kg) / AVIARY_BENCH_WING_MASS_KG, 2.0))


def mtom_to_pycycle_fn_des(mtom_kg: float) -> float:
    """Map mass-mcp MTOM to pycycle's ``Fn_DES`` (design thrust, lbf)."""
    return max(2000.0, min(float(mtom_kg) * FN_DES_PER_KG_LBF, 25000.0))


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
