"""Canonical baseline parameter injection.

Single source of truth for every discipline's tool/solver inputs lives in
``config/mdo_f25_canonical_baseline.yaml``. Combo prompt configs reference the
canonical values via ``<<PLACEHOLDER>>`` tokens; ``substitute_text`` replaces
them at load time so the ONLY thing that differs between the 8 combos is the
coordination structure.

Placeholder delimiter is ``<<NAME>>`` (not ``{{ }}``) to avoid clashing with the
dict/brace literals the rendered SU2 config contains.

Rendered placeholders:
  <<WING_UID>>           -> Wing1
  <<FUSELAGE_UID>>       -> Fuselage1
  <<FAR_FIELD_DISTANCE>> -> 10.0
  <<SU2_CONFIG>>         -> {"AOA": 2.0, "CFL_ADAPT": "YES", ...}  (full dict literal)
  <<MASS_PARAMS>>        -> wing_mass_method="flops", design_load_factor=2.5, material="aluminum"
  <<PROP_DESIGN_POINT>>  -> design_mach=0.785, design_altitude_ft=35000, ...
  <<MISSION_PARAMS>>     -> cruise_mach=0.785, cruise_altitude_ft=35000, range_nmi=1500, optimizer_max_iter=200
"""
from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

import yaml

_CANONICAL_PATH = Path(__file__).resolve().parents[2] / "config" / "mdo_f25_canonical_baseline.yaml"
_PLACEHOLDER_RE = re.compile(r"<<([A-Z0-9_]+)>>")


@functools.lru_cache(maxsize=1)
def load_canonical(path: str | None = None) -> dict[str, Any]:
    """Load and cache the canonical baseline YAML."""
    p = Path(path) if path else _CANONICAL_PATH
    with open(p, "r") as f:
        return yaml.safe_load(f) or {}


def _fmt_scalar(v: Any) -> str:
    """Render a scalar the way it should appear in a prompt (dict/kwarg literal)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)


def _render_su2_config(su2: dict[str, Any]) -> str:
    """Render the SU2 numerics as a deterministic dict literal (sorted keys).

    ``ref_from_upstream`` is a directive, not an SU2 key, so it is excluded.
    """
    keys = sorted(k for k in su2 if k != "ref_from_upstream")
    inner = ", ".join(f'"{k}": {_fmt_scalar(su2[k])}' for k in keys)
    return "{" + inner + "}"


def _render_kwargs(d: dict[str, Any], keys: list[str]) -> str:
    """Render selected keys as ``k=v, k=v`` in the given (deterministic) order."""
    parts = []
    for k in keys:
        if k in d and d[k] is not None:
            parts.append(f"{k}={_fmt_scalar(d[k])}")
    return ", ".join(parts)


@functools.lru_cache(maxsize=1)
def render_snippets(path: str | None = None) -> dict[str, str]:
    """Build the {PLACEHOLDER_NAME: replacement_text} map from the canonical file."""
    c = load_canonical(path)
    geo = c.get("geometry", {})
    mass = c.get("mass", {})
    prop = c.get("propulsion", {})
    mission = c.get("mission", {})
    return {
        # geometry
        "WING_UID": str(geo.get("wing_component_uid", "Wing1")),
        "FUSELAGE_UID": str(geo.get("fuselage_component_uid", "Fuselage1")),
        "FAR_FIELD_DISTANCE": str(geo.get("far_field_distance", 10.0)),
        "SU2_CONFIG": _render_su2_config(c.get("su2_config", {})),
        # mass (granular)
        "WING_MASS_METHOD": str(mass.get("wing_mass_method", "flops")),
        "MATERIAL": str(mass.get("material", "aluminum")),
        "DESIGN_LOAD_FACTOR": str(mass.get("design_load_factor", 2.5)),
        # propulsion (granular)
        "DESIGN_MACH": str(prop.get("design_mach", 0.785)),
        "DESIGN_ALTITUDE_FT": str(prop.get("design_altitude_ft", 35000)),
        "T4_MAX_DEGR": str(prop.get("T4_MAX_degR", 2857)),
        # Kelvin form for prompt set_inputs examples that use burner.T4 in K.
        "BURNER_T4_K": str(round(prop.get("T4_MAX_degR", 2857) * 5.0 / 9.0)),
        "FN_DES_LBF": str(prop.get("Fn_DES_lbf", 5900.0)),
        "BPR": str(prop.get("BPR", 5.105)),
        # mission (granular)
        "CRUISE_MACH": str(mission.get("cruise_mach", 0.78)),
        "CRUISE_ALTITUDE_FT": str(mission.get("cruise_altitude_ft", 33000)),
        "RANGE_NMI": str(mission.get("range_nmi", 2500)),
        "NUM_PASSENGERS": str(mission.get("num_passengers", 239)),
        "OPTIMIZER_MAX_ITER": str(mission.get("optimizer_max_iter", 200)),
        "MASS_PARAMS": _render_kwargs(
            mass, ["wing_mass_method", "design_load_factor", "material"]
        ),
        "PROP_DESIGN_POINT": _render_kwargs(
            prop,
            ["design_mach", "design_altitude_ft", "T4_MAX_degR", "Fn_DES_lbf", "BPR"],
        ),
        "MISSION_PARAMS": _render_kwargs(
            mission,
            ["cruise_mach", "cruise_altitude_ft", "range_nmi", "optimizer_max_iter"],
        ),
    }


def substitute_text(text: str, path: str | None = None) -> str:
    """Replace every ``<<NAME>>`` in text with its canonical rendering.

    Unknown placeholders are left untouched (and are surfaced by the identity
    test, which fails on any residual ``<<...>>``).
    """
    if "<<" not in text:
        return text
    snippets = render_snippets(path)
    return _PLACEHOLDER_RE.sub(lambda m: snippets.get(m.group(1), m.group(0)), text)


def substitute_obj(obj: Any, path: str | None = None) -> Any:
    """Recursively substitute placeholders in every string within a parsed config."""
    if isinstance(obj, str):
        return substitute_text(obj, path)
    if isinstance(obj, dict):
        return {k: substitute_obj(v, path) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute_obj(v, path) for v in obj]
    return obj
