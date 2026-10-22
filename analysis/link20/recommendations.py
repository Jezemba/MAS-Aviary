"""Group the integrator's carried-forward recommendations by target role and by design variable.

The recommendation a link hands to the next one is its LAST recorded recommendation (checked on every link:
carry_over.json of link k+1 equals the last `recommendation` signal in link k's design.json).

Design-variable grouping is a keyword map over `recommended_change` (free text written by the model). One
recommendation can name several variables ("reduce wing area and increase aspect ratio"), so it counts once
under each. Text that names none of them falls under "Other". The map is deliberately literal; review it
before trusting the counts.
"""

from __future__ import annotations

import re

ROLE_LABELS = {
    "geometry_engineer": "Geometry engineer",
    "aerodynamics_analyst": "Aerodynamics analyst",
    "structures_analyst": "Structures analyst",
    "propulsion_analyst": "Propulsion analyst",
    "mission_architect": "Mission architect",
    "simulation_executor": "Simulation executor",
    "mdo_integrator": "MDO integrator",
}
NO_RECOMMENDATION = "None recorded"

# (label, pattern) in display order; matched case-insensitively and independently of each other.
VARIABLE_PATTERNS = [
    ("Wing area", r"\barea\b|area_m2"),
    ("Wing aspect ratio", r"aspect[ _]ratio"),
    ("Wing sweep", r"sweep"),
    ("Wing taper ratio", r"taper"),
    ("Wing mass / mass scaler", r"wing mass|mass[ _]scaler"),
    ("Engine scale factor", r"scale[ _]factor"),
    ("Engine cycle (BPR, PRs, T4, SFC, thrust)", r"bypass|\bbpr\b|fan\.pr|hpc\.pr|lpc\.pr|compressor|t4_max|\bsfc\b"
                                                 r"|engine (cycle|thrust)|thrust settings"),
    ("Aero coefficients (CL, CD)", r"\bcl\b|\bcd\b|lift[ _]coefficient|drag[ _]coeff"),
    ("Aircraft mass (OEM, MTOM)", r"\boem\b|\bmtom\b|structural mass"),
    ("Mission (range, Mach, trajectory, reserves)", r"mission (range|parameters|constraints|fuel)|range to|cruise mach"
                                                    r"|mach number|trajectory|flight profile|reserve"),
    ("Optimizer settings", r"optimizer"),
    ("Run or validate only", r"^\s*(run|execute|re-run|validate|investigate|conduct|reconcile|evaluate|finalize)\b"),
]
OTHER = "Other"


def role_label(role) -> str:
    if not isinstance(role, str) or not role:
        return NO_RECOMMENDATION
    return ROLE_LABELS.get(role, role)


def variable_labels(change) -> list[str]:
    if not isinstance(change, str) or not change.strip():
        return [NO_RECOMMENDATION]
    hits = [label for label, pat in VARIABLE_PATTERNS if re.search(pat, change, flags=re.IGNORECASE)]
    return hits or [OTHER]
