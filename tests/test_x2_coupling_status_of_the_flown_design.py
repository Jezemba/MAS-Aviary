"""X2 (validation 3, 2026-11-19): result.json's coupling status is the COUNTED mission's, not the end-of-link design's.

S_GR (val2 and val3): mission #84 flew AR 11.0 fully coupled (check_coupling 13/13 PASS); the geometry engineer then
reshaped to AR 11.5 (#89) and exported (#90), and the link ended on its role-run budget. coupling_status() judged the
unflown AR-11.5 design: aero/mass "stale", "propulsion ran: False", mission_coupled False, "[B84] WARNING", ledger
scoreable False, dropped from fuel ranking by analyze_sweep. Now the status is recorded when a converged mission flies;
the end-of-link status and the existence of an unflown later design are recorded beside it. Stubbed tools only.
"""
from __future__ import annotations

import json

from src.tools import coupling_contract as cc
import src.tools.data_plane as dp
from tests.test_b84_required_coupled_inputs import _everything_ran, _morph_the_geometry, fresh  # noqa: F401


def _fly(fuel=14506.871306247287, converged=True):
    dp.intercept_response("run_simulation", json.dumps(
        {"success": True, "converged": converged, "summary": {"fuel_burned_kg": fuel, "gtow_kg": 89079.5}}))


def test_x2_before_any_mission_the_end_of_link_status_is_reported_as_before():
    _everything_ran()
    s = cc.coupling_status()
    assert s["judged_on"].startswith("end_of_link") and s["mission_coupled"] is True
    assert s["unflown_later_design"] is None and s["end_of_link"] is None


def test_x2_a_reshape_after_the_last_mission_does_not_unscore_the_flown_design():
    _everything_ran()
    _fly()
    _morph_the_geometry()                       # S_GR #89: reshaped after the last mission, never flown
    s = cc.coupling_status()
    assert s["judged_on"] == "counted_mission"
    assert s["mission_coupled"] is True and s["aero_status"] == "ok" and s["mass_status"] == "ok"
    assert s["propulsion_ran"] is True
    # recorded, not a failure
    assert s["unflown_later_design"] is True
    assert s["end_of_link"]["aero_status"] == "stale" and s["end_of_link"]["mission_coupled"] is False
    assert s["flown_design_fingerprint"] != s["end_of_link"]["design_fingerprint"]


def test_x2_no_later_design_means_end_and_flown_agree():
    _everything_ran()
    _fly()
    s = cc.coupling_status()
    assert s["unflown_later_design"] is False and s["end_of_link"]["mission_coupled"] is True


def test_x2_the_last_converged_mission_is_the_one_judged():
    _everything_ran()
    _fly()
    _morph_the_geometry()
    _fly(fuel=15100.0)                          # flown on the reshaped wing with the earlier aero: not coupled
    s = cc.coupling_status()
    assert s["mission_coupled"] is False and s["aero_status"] == "stale" and s["unflown_later_design"] is False


def test_x2_a_mission_that_did_not_converge_is_not_counted():
    _everything_ran()
    _fly()
    _morph_the_geometry()
    _fly(fuel=15100.0, converged=False)         # B306: not a flown result
    s = cc.coupling_status()
    assert s["mission_coupled"] is True and s["unflown_later_design"] is True
