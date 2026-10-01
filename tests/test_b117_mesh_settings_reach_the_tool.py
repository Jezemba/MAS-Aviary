"""B117: the canonical mesh settings must reach generate_volume_mesh.

`far_field_distance` is the only mesh setting with a prompt placeholder (<<FAR_FIELD_DISTANCE>>);
`mesh_size_min` and `mesh_size_max` have none, so an agent is never told them and tigl falls back
to its calibrated defaults (~0.663 / 17.7 -> ~670k cells). That is the resolution the B116 study
rejected -- CD -0.0124 against an induced drag of +0.0007 -- so the config said one thing and every
run did another. Measured: fullphys_seqstaged link 1 meshed 673,368 cells and reproduced the
pre-fix fuel to within 0.4 kg.
"""
import pytest

import src.tools.data_plane as dp
from src.config.canonical import load_canonical
from src.coordination.design_state import DesignState


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AVION_COARSE_MESH", raising=False)
    monkeypatch.setattr(dp, "_design_state", DesignState())
    yield


def _canonical_geometry():
    return load_canonical().get("geometry", {})


def test_the_canonical_mesh_reaches_the_mesher():
    geometry = _canonical_geometry()
    resolved = dp.resolve_request("generate_volume_mesh", {"session_id": "t"})
    assert resolved["far_field_distance"] == geometry["far_field_distance"]
    assert resolved["mesh_size_min"] == geometry["mesh_size_min"]
    assert resolved["mesh_size_max"] == geometry["mesh_size_max"]


def test_the_injected_values_are_the_knee_the_study_chose():
    """Guards the config itself: these are the B116 measurements, not arbitrary numbers."""
    geometry = _canonical_geometry()
    assert geometry["mesh_size_min"] == 0.33, "L1 of the B116 study: 2.32M cells, CD +0.00070"
    assert geometry["mesh_size_max"] == 12.0
    assert geometry["far_field_distance"] == 10.0


def test_a_value_the_caller_chose_is_never_overridden():
    resolved = dp.resolve_request("generate_volume_mesh",
                                  {"session_id": "t", "mesh_size_min": 0.9,
                                   "far_field_distance": 4.0, "mesh_size_max": 30.0})
    assert resolved["mesh_size_min"] == 0.9
    assert resolved["far_field_distance"] == 4.0
    assert resolved["mesh_size_max"] == 30.0


def test_the_coarse_override_still_wins(monkeypatch):
    """AVION_COARSE_MESH is the deliberate fast path; it must not be quietly re-refined."""
    monkeypatch.setenv("AVION_COARSE_MESH", "1")
    resolved = dp.resolve_request("generate_volume_mesh", {"session_id": "t"})
    assert resolved["far_field_distance"] == 5.0
    assert resolved["mesh_size_max"] == 25.0


def test_other_tools_are_untouched():
    resolved = dp.resolve_request("export_component_mesh", {"session_id": "t"})
    assert "mesh_size_min" not in resolved


def test_a_broken_config_does_not_block_meshing(monkeypatch):
    monkeypatch.setattr("src.config.canonical.load_canonical",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad yaml")))
    resolved = dp.resolve_request("generate_volume_mesh", {"session_id": "t"})
    assert resolved["session_id"] == "t"       # the call still goes through
