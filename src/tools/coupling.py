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
}

# ---------------------------------------------------------------------------
# Named, documented transforms (extracted verbatim from the former middleware
# formulas so behavior is unchanged; now explicit and unit-tested).
# ---------------------------------------------------------------------------

# aviary height_energy A320-class bench references.
AVIARY_BENCH_WING_MASS_KG = 5998.0     # MASS_SCALER = 1.0 corresponds to this wing.
FN_DES_PER_KG_LBF = 0.0811             # engine thrust per kg MTOM.
_SKIN_FRICTION_INCREMENT = 0.0050      # inviscid SU2 CD -> viscous-corrected CD.
_AVIARY_CD0 = 0.022                    # aviary FLOPS baseline zero-lift CD.
_OSWALD = 0.85                         # span efficiency in the aviary induced-drag est.


def aero_cd_to_aviary_drag_factor(cd_inviscid: float, cl: float, aspect_ratio: float) -> float:
    """Map SU2's inviscid cruise CD to aviary's ``Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR``.

    aviary scales its own FLOPS drag polar by this factor. We compare a viscous-
    corrected SU2 CD against aviary's baseline drag at the same CL and take the ratio,
    clamped to a sane band. This is the SAME calculation the middleware did inline;
    it now lives here, named and documented.
    """
    ar_eff = max(float(aspect_ratio) if aspect_ratio is not None else 11.0, 8.0)
    cd_corrected = float(cd_inviscid) + _SKIN_FRICTION_INCREMENT
    cd_aviary_default = _AVIARY_CD0 + (float(cl) ** 2) / (3.14159 * ar_eff * _OSWALD)
    factor = cd_corrected / cd_aviary_default
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
