"""Typed coupling: the named transforms must reproduce the former middleware formulas
byte-for-byte (behavior-preserving extraction), and the registry must enforce the schema."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools import coupling as C  # noqa: E402


class _DS:
    def __init__(self):
        self.data_store = {}


def test_the_su2_drag_transforms_were_removed():
    # Drag model decision (Jessica, 2026-11-18): aviary's FLOPS drag only. The friction build-up, the SU2 drag factor
    # and the assumed-L/D thrust were removed with the coupling (their tests went with them).
    for name in ("reynolds_number", "skin_friction_cd", "aero_cd_to_aviary_drag_factor", "mtom_to_pycycle_fn_des",
                 "fn_des_from_drag", "total_cruise_cd"):
        assert not hasattr(C, name), name


def test_fn_des_from_aviarys_lift_to_drag():
    # B306: Fn_DES = mass * g / (L/D) / 2 / 4.448 * 1.25, no [2,000, 25,000] lbf clamp.
    assert C.fn_des_from_lift_to_drag(73000.0, 17.0) == 73000.0 * 9.81 / 17.0 / 2 / 4.448 * 1.25
    assert C.fn_des_from_lift_to_drag(500000.0, 10.0) > 25000.0


def test_registry_enforces_schema_and_roundtrips():
    ds = _DS()
    C.put_var(ds, "aero.cd_cruise", 0.0138, "run_su2_solver")
    C.put_var(ds, "not.a.real.var", 1.23)   # rejected — not in schema
    C.put_var(ds, "aero.cl_cruise", None)     # rejected — None
    assert C.get_var(ds, "aero.cd_cruise") == 0.0138
    assert C.get_var(ds, "not.a.real.var") is None
    assert C.has_vars(ds, ["aero.cd_cruise"]) is True
    assert C.has_vars(ds, ["aero.cd_cruise", "mass.wing_kg"]) is False
