"""Typed coupling: the named transforms must reproduce the former middleware formulas
byte-for-byte (behavior-preserving extraction), and the registry must enforce the schema."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools import coupling as C  # noqa: E402


class _DS:
    def __init__(self):
        self.data_store = {}


def test_aero_transform_matches_old_formula():
    cd, cl, ar = 0.0138, 0.42, 11.0
    ar_eff = max(ar, 8.0)
    expected = (cd + 0.0050) / (0.022 + cl * cl / (3.14159 * ar_eff * 0.85))
    expected = max(0.5, min(expected, 2.0))
    assert C.aero_cd_to_aviary_drag_factor(cd, cl, ar) == expected


def test_aero_transform_clamps():
    assert C.aero_cd_to_aviary_drag_factor(1.0, 0.4, 11.0) == 2.0   # huge CD clamps high
    assert C.aero_cd_to_aviary_drag_factor(0.0001, 0.9, 11.0) == 0.5  # tiny CD clamps low
    # low AR is floored at 8.0 (same as old ar_eff = max(ar, 8.0))
    assert C.aero_cd_to_aviary_drag_factor(0.0138, 0.42, 5.0) == C.aero_cd_to_aviary_drag_factor(0.0138, 0.42, 8.0)


def test_wing_mass_scaler_matches():
    assert C.wing_mass_to_aviary_scaler(7677.0) == max(0.5, min(7677.0 / 5998.0, 2.0))
    assert C.wing_mass_to_aviary_scaler(20000.0) == 2.0   # clamp
    assert C.wing_mass_to_aviary_scaler(1000.0) == 0.5    # clamp


def test_fn_des_matches():
    assert C.mtom_to_pycycle_fn_des(73000.0) == max(2000.0, min(73000.0 * 0.0811, 25000.0))
    assert C.mtom_to_pycycle_fn_des(500000.0) == 25000.0  # clamp


def test_registry_enforces_schema_and_roundtrips():
    ds = _DS()
    C.put_var(ds, "aero.cd_cruise", 0.0138, "run_su2_solver")
    C.put_var(ds, "not.a.real.var", 1.23)   # rejected — not in schema
    C.put_var(ds, "aero.cl_cruise", None)     # rejected — None
    assert C.get_var(ds, "aero.cd_cruise") == 0.0138
    assert C.get_var(ds, "not.a.real.var") is None
    assert C.has_vars(ds, ["aero.cd_cruise"]) is True
    assert C.has_vars(ds, ["aero.cd_cruise", "mass.wing_kg"]) is False
