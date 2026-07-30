"""Typed coupling: the named transforms must reproduce the former middleware formulas
byte-for-byte (behavior-preserving extraction), and the registry must enforce the schema."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools import coupling as C  # noqa: E402


class _DS:
    def __init__(self):
        self.data_store = {}


def test_reynolds_number_at_cruise():
    # M0.78, 33 kft, MAC 4 m -> ~2.6e7 (a jet transport cruise Reynolds)
    re = C.reynolds_number(0.78, 33000, 4.0)
    assert 1.5e7 < re < 4.0e7


def test_skin_friction_is_physical():
    # Schlichting Cf ~0.0026 at Re 2.6e7; CD0 with Swet/Sref~6 -> ~0.018-0.022 (real parasite drag)
    cd0 = C.skin_friction_cd(2.6e7, 6.0)
    assert 0.015 < cd0 < 0.024
    # more wetted area (or lower Re) => more friction drag
    assert C.skin_friction_cd(2.6e7, 7.0) > C.skin_friction_cd(2.6e7, 6.0)


def test_aero_transform_physics_buildup():
    # CD_total = SU2 pressure CD + real skin friction; NOT the old +0.005 fudge.
    re = C.reynolds_number(0.78, 33000, 4.0)
    friction = C.skin_friction_cd(re, 6.0)
    cd_inviscid, cl, ar = 0.001, 0.42, 11.0
    cd_total = cd_inviscid + friction
    ar_eff = max(ar, 8.0)
    expected = max(0.5, min(cd_total / (0.022 + cl * cl / (3.14159 * ar_eff * 0.85)), 2.0))
    assert abs(C.aero_cd_to_aviary_drag_factor(cd_inviscid, cl, ar, reynolds=re, swet_sref=6.0) - expected) < 1e-9


def test_aero_transform_no_longer_clamps_near_zero_cd():
    # The morphed near-zero SU2 CD used to clamp to the 0.5 floor (fake). With the friction
    # build-up it now lands in-band (physically sensible), so the drag SIGNAL survives.
    re = C.reynolds_number(0.78, 33000, 4.0)
    f = C.aero_cd_to_aviary_drag_factor(-6.87e-05, 0.193, 12.4, reynolds=re, swet_sref=6.0)
    assert 0.6 < f < 1.2


def test_aero_transform_still_clamps_extremes():
    assert C.aero_cd_to_aviary_drag_factor(1.0, 0.4, 11.0) == 2.0   # huge CD clamps high


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
