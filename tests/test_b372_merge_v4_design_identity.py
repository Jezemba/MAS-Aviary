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


# -- tigl-mcp fix/final-rerun b5ae37a (B373): the within-tolerance morph reply, through the real middleware ------------

_FIX = json.loads((h.Path(__file__).parent / "fixtures" / "v1a_validation1_records.json").read_text())
_WING_BEFORE = next(c for c in _FIX["links"]["val_net_graph/repeat_000"]["tool_calls"]
                    if c["tool"] == "set_high_level_parameters")["result"]["geometry_morph"]["before"]


def _no_op_reply(uid, before, tolerance, kind):
    """tigl-mcp b5ae37a set_high_level_parameters for a request within tolerance (parameters.py / morph.py)."""
    return {"component_uid": uid, "new_parameters": {}, "warnings": [],
            "geometry_morph": {"rebuilt": False, "before": before, "after": before, "no_op": True,
                               "note": f"no change: within tolerance ({kind}); the CPACS file and the geometry are "
                                       "untouched.", "tolerance": tolerance}}


def test_tigl_b373_no_op_replies_keep_the_design_and_nothing_goes_stale(link, monkeypatch):
    from src.tools.type_coercion import apply_coercion_to_tools

    replies = {"Wing1": _no_op_reply("Wing1", dict(_WING_BEFORE),
                                     {"area_aspect_ratio_relative": 1e-4, "sweep_deg": 0.01}, "wing"),
               "Wing2H": _no_op_reply("Wing2H", dict(_WING_BEFORE, reference_area=31.118412165407747),
                                      {"area_aspect_ratio_relative": 1e-4, "sweep_deg": 0.01}, "tail"),
               "Fuselage1": _no_op_reply("Fuselage1", dict(FUSELAGE),
                                         {"relative": 1e-4, "held_within_tolerance": ["length", "diameter"]},
                                         "fuselage")}
    answer = link._answer

    def answer_with_morph(tool, kw):
        if tool == "set_high_level_parameters":
            return json.loads(json.dumps(replies[kw["component_uid"]]))
        return answer(tool, kw)

    monkeypatch.setattr(link, "_answer", answer_with_morph)
    monkeypatch.setitem(h.DECLARED, "set_high_level_parameters", ["session_id", "component_uid", "updates"])
    dp._tool_server_map["set_high_level_parameters"] = "tigl"
    link.tools.update({t.name: t for t in apply_coercion_to_tools(
        [h.FakeMCPTool(link, "set_high_level_parameters")])})

    dp.seed_anchor_geometry()
    for agent, stage in (("geometry", h.geometry), ("aero", h.aero), ("structures", h.structures),
                         ("propulsion", h.propulsion)):
        h.run_as(agent, stage, link)
    design = cc.current_design_fingerprint()
    store = dp._design_state.data_store
    wing_before, fus_before = dict(store["geometry_wing"]), dict(store["geometry_fuselage"])
    sid = dp._design_state.sessions["tigl"]
    epoch = dg._state()["geometry_epoch"].get(sid, 0)
    updates = {"Wing1": {"area": 122.8287, "aspect_ratio": 9.3632, "sweep": 21.65},
               "Wing2H": {"area": 31.1184}, "Fuselage1": {"length": 37.6446, "diameter": 4.1437}}
    for uid, up in updates.items():
        out = h.call(link, "set_high_level_parameters", session_id=sid, component_uid=uid, updates=up)
        assert out["geometry_morph"]["no_op"] is True and "error_code" not in out, out
    # The design did not change: fingerprint, epoch, records, staleness, export state.
    assert cc.current_design_fingerprint() == design
    assert dg._state()["geometry_epoch"].get(sid, 0) == epoch
    assert store["geometry_wing"] == wing_before and store["geometry_fuselage"] == fus_before
    assert not store.get("morph_unexported")
    for required in (cc.AERO_SOLVE, cc.WING_MASS, cc.MTOM):
        assert cc.status(required) == "ok", required
    assert dp.engine_is_current()
    # Nothing is refused: the export, SU2 and mass are not demanded again, and the mission flies.
    h.run_as("mission", h.mission, link)
    h.assert_engine_sized_and_mission_flown(link)
    assert len(link.sent("estimate_mass")) == 1 and len(link.sent("run_su2_solver")) == 1
    refused = [e for e in kbm_entries() if e["status"] == "refused"]
    assert not refused, refused


def kbm_entries():
    from src.tools import knowledge_base as kbm

    return list(kbm.get_kb().entries)
