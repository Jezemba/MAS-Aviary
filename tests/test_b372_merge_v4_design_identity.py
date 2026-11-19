"""Merge check (B372 x B364): V4's anchor seed and fuselage measurements never change the design identity.

B364 (V4) seeds the link-1 geometry record from the measured D150 anchor, captures get_fuselage_summary and measures
Fuselage1 on the live tigl session before set_aircraft_parameters. B372 (V5) makes the design fingerprint the geometry's
content. Seeding and measuring are records, not geometry changes, so an unchanged geometry keeps its fingerprint and
its results stay current; re-applying the measured fuselage (V3) -- answered by tigl-mcp 043ba39 with a ~1e-6 rescale
and rebuilt true, or by tigl-mcp's V5 follow-up with no_op / rebuilt false -- is the same design too.

Runs a whole link through the real middleware (the B361 harness). No network, no API, no MCP servers.
"""
from __future__ import annotations

import json

import src.tools.data_plane as dp
import tests.test_b361_end_to_end_threaded_link as h
from src.tools import coupling_contract as cc
from src.tools import duplicate_guard as dg

link = h.link        # the harness fixture

FUSELAGE = {"length": 37.644560994515366, "width": 3.951781358688922, "height": 4.143709313380411,
            "surface_area": 412.5}


def _fuselage_morph(sid, after, rebuilt=True, no_op=False):
    reply = {"component_uid": "Fuselage1", "new_parameters": {}, "warnings": [],
             "geometry_morph": {"rebuilt": rebuilt, "no_op": no_op, "before": dict(FUSELAGE), "after": after}}
    dg.observe("set_high_level_parameters", {"session_id": sid, "component_uid": "Fuselage1",
                                             "updates": {"length": 37.6446, "diameter": 4.1437}},
               json.dumps(reply), "success", None)


def test_anchor_seed_measurements_and_fuselage_re_apply_keep_the_design(link):
    dp.seed_anchor_geometry()                                   # the runner, link 1 (B364)
    seeded = dict(dp._design_state.data_store["geometry_fuselage"])
    assert seeded["_source"] == dp.ANCHOR_SOURCE
    h.run_as("geometry", h.geometry, link)                      # opens, measures Wing1 + Fuselage1, exports, meshes
    design = cc.current_design_fingerprint()
    assert dp._design_state.data_store["geometry_fuselage"]["_source"] == "get_high_level_parameters"
    for key in ("Aircraft.Fuselage.LENGTH", "Aircraft.Fuselage.MAX_HEIGHT", "Aircraft.Fuselage.MAX_WIDTH"):
        assert dp._design_state.data_store["geometry_fuselage"][key] == seeded[key], key   # anchor == measurement
    h.run_as("aero", h.aero, link)
    h.run_as("structures", h.structures, link)
    h.run_as("propulsion", h.propulsion, link)
    assert cc.current_design_fingerprint() == design and dp.engine_is_current()
    sid = dp._design_state.sessions["tigl"]
    # More measurements of the same geometry (an agent, and the framework's V4 measurement before a set).
    h.call(link, "get_high_level_parameters", session_id=sid, component_uid="Fuselage1")
    dp._auto_measure_fuselage()
    # V3: an agent re-applies the measured starting fuselage. tigl-mcp 043ba39: rebuilt, ~1e-6 rescale.
    _fuselage_morph(sid, {k: v * (1 + 1.2e-6) for k, v in FUSELAGE.items()})
    # tigl-mcp V5 follow-up: a reported no-op, nothing written.
    _fuselage_morph(sid, dict(FUSELAGE), rebuilt=False, no_op=True)
    assert cc.current_design_fingerprint() == design
    for required in (cc.AERO_SOLVE, cc.WING_MASS, cc.MTOM):
        assert cc.status(required) == "ok", required
    assert dp.engine_is_current()
    # The mission flies on the results already produced: no refusal, no re-run.
    h.run_as("mission", h.mission, link)
    h.assert_engine_sized_and_mission_flown(link)
    assert len(link.sent("estimate_mass")) == 1 and len(link.sent("run_su2_solver")) == 1


def test_a_real_fuselage_change_is_still_a_new_design(link):
    dp.seed_anchor_geometry()
    h.run_as("geometry", h.geometry, link)
    h.run_as("structures", h.structures, link)
    design = cc.current_design_fingerprint()
    _fuselage_morph(dp._design_state.sessions["tigl"], dict(FUSELAGE, length=FUSELAGE["length"] * 1.01))
    assert cc.current_design_fingerprint() != design
    assert cc.status(cc.WING_MASS) == "stale"
