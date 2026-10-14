"""B184: the SU2 references (geom.reference_area / geom.mac_length) are the MAIN wing's.

v2gemma4 links 1-2 (aipexws8, 2026-10-12): the geometry agent re-set Wing1, Wing2H, Wing3V and Fuselage1 in
that order. Every morph refreshed the references (B99), so the vertical tail's won: SU2 was injected
ref_area 21.499 / ref_length 3.9465 for a 120.26 m^2 wing, returned CL 5.4-6.4 / CD 1.3-4.7, and every
run_simulation was refused AERO_NONPHYSICAL (6 runner attempts lost). get_wing_summary(Wing3V) did the same
in link 4 (43.0 m^2).

Values below are the tool results from that run. Stubbed: no MCP server, no model, no .env.
"""

import types

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_call_ctx", types.SimpleNamespace())
    yield


def _morph(uid, full_area, mac):
    return {"component_uid": uid, "geometry_morph": {
        "rebuilt": True, "after": {"reference_area": full_area, "mac_length": mac}}}


WING1 = ("Wing1", 120.26127505545264, 4.150823684513553)
WING2H = ("Wing2H", 31.1, 2.6)
WING3V = ("Wing3V", 21.49936662844478, 3.946520164717242)


def _ref():
    return (coupling.get_var(dp._design_state, "geom.reference_area"),
            coupling.get_var(dp._design_state, "geom.mac_length"))


def test_tail_morphs_after_the_wing_do_not_replace_the_wing_references():
    for uid, area, mac in (WING1, WING2H, WING3V):
        dp._call_ctx.morph = {"uid": uid}
        dp._capture_geometry_ref("set_high_level_parameters", _morph(uid, area, mac))
    area, mac = _ref()
    assert 2.0 * area == pytest.approx(120.2613, abs=1e-3)      # injected ref_area is 2x the semi value
    assert mac == pytest.approx(4.1508, abs=1e-4)


def test_a_tail_morph_alone_records_nothing():
    dp._call_ctx.morph = {"uid": "Wing3V"}
    dp._capture_geometry_ref("set_high_level_parameters", _morph(*WING3V))
    assert _ref() == (None, None)


def test_the_response_uid_is_used_when_the_call_context_is_missing():
    dp._capture_geometry_ref("set_high_level_parameters", _morph(*WING3V))
    assert _ref() == (None, None)


def test_get_wing_summary_of_the_tail_does_not_replace_the_wing():
    dp._call_ctx.wing_uid = "Wing1"
    dp._capture_geometry_ref("get_wing_summary", {"reference_area": 60.13063752772632,
                                                   "mac_length": 4.150823684513553, "wetted_area": 107.37})
    dp._call_ctx.wing_uid = "Wing3V"
    dp._capture_geometry_ref("get_wing_summary", {"reference_area": 21.49936662844478,
                                                   "mac_length": 3.946520164717242, "wetted_area": 45.31})
    area, mac = _ref()
    assert area == pytest.approx(60.1306, abs=1e-3)
    assert mac == pytest.approx(4.1508, abs=1e-4)
    assert coupling.get_var(dp._design_state, "geom.wing_wetted_area") == pytest.approx(107.37)
