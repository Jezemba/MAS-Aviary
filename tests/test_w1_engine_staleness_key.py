"""W1 (validation 2, 2026-11-19): the engine is current only for exactly what Fn_DES and the flown engine depend on.

N_IF it2 (ws8 final_val2 val2_net_iter repeat_001): the mass peer's first estimate_mass read the pre-morph file
(t/c 0.11644185460446387), the propulsion peer sized Fn_DES on aviary's cruise point of that design (b8b0ec61), the
re-estimate on the morphed file gave t/c 0.1164418741764862 -- equal to 6 decimals, the precision the staleness key
kept -- so the engine stayed "current" and the mission flew another aviary design (ae3749c9) on the old Fn_DES.

The replay below uses the recorded replies (tests/fixtures/w1_n_if_it2_records.json) through the middleware's order
(the B340 World: resolve_request -> refusal -> coupling contract -> stub server -> intercept_response). The stub aviary
answers with the recorded fingerprints and cruise points: its fingerprint follows the held wing t/c and NOT the wing
mass (aviary-mcp 36b3a53 design_fingerprint covers the inputs of its cruise aerodynamics only; the recorded
fingerprint_covers has no aircraft:wing:mass). No network, no API, no MCP servers.
"""
import hashlib
import json
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling
from src.tools import coupling_contract as cc
from src.tools import knowledge_base as kbm
from tests.test_b340_review_fixes import SERVERS, World, _Tool

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "w1_n_if_it2_records.json").read_text())
CALLS = {c["index"]: c for c in FIXTURE["calls"]}
TRIM_MASS = CALLS[13]["args"]["mass_kg"]                       # 90,298.63367457094 (carried sized mass)
CARRIED_SCALE = CALLS[13]["args"]["engine_scale"]              # 1.1957958109698965
TC_STALE = CALLS[5]["reply"]["mass_breakdown"]["sizing_report"]["wing_stations"]["tc_avg"]
TC_FLOWN = CALLS[15]["reply"]["mass_breakdown"]["sizing_report"]["wing_stations"]["tc_avg"]
FP_STALE = CALLS[13]["cruise_point"]["design_fingerprint"]     # b8b0ec61673d0d45
FP_FLOWN = CALLS[25]["flown_design_fingerprint"]               # ae3749c95f93e4bc
POINTS = {FP_STALE: CALLS[13]["cruise_point"], FP_FLOWN: FIXTURE["o_if_it2_flown_design_cruise_point"]}
WING_10 = {"Aircraft.Wing.AREA": CALLS[4]["geometry_morph"]["after"]["reference_area"],
           "Aircraft.Wing.ASPECT_RATIO": CALLS[4]["geometry_morph"]["after"]["aspect_ratio"]}
AERO_EXTRA = "Aircraft.Nacelle.AVG_DIAMETER"       # an aviary cruise-aero input the framework does not set
# #14's cycle design inputs (the carried engine); Fn_DES, the flight condition and the efficiencies are the framework's
CYCLE_INPUTS = {k: v for k, v in CALLS[14]["args"].items() if k in ("T4_MAX", "T4_TAKEOFF", "T4_CLIMB", "splitter.BPR",
                                                                   "fan.PR", "lpc.PR", "hpc.PR")}


class RecordedWorld(World):
    """aviary / mass-mcp answer as recorded in N_IF it2; pycycle as the B340 World."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.estimates = []                 # replies estimate_mass returns, in order
        self.cycle_fails = False

    def _fp(self, sid):
        held = self.held.get(sid, {})
        tc = held.get(dp.WING_TC_KEY)
        base = {TC_STALE: FP_STALE, TC_FLOWN: FP_FLOWN}.get(tc)
        if base is None or AERO_EXTRA in held:
            # any other aero input -> another design (aviary hashes every input of its cruise aero; not the wing mass)
            aero = {k: v for k, v in held.items() if k not in ("Aircraft.Wing.MASS", "Aircraft.Engine.SCALE_FACTOR")}
            return "fp-" + hashlib.md5(json.dumps(sorted(aero.items()), default=str).encode()).hexdigest()[:16]
        return base

    def server(self, tool, kw):
        if tool == "get_cruise_point":
            fp = self._fp(kw["session_id"])
            point = dict(POINTS.get(fp) or dict(POINTS[FP_FLOWN], lift_to_drag=17.0), design_fingerprint=fp,
                         mass_kg=kw["mass_kg"], engine_scale_used=kw.get("engine_scale"))
            return {"success": True, "design_fingerprint": fp, "cruise_point": point}
        if tool == "estimate_mass":
            reply = json.loads(json.dumps(self.estimates.pop(0)))
            reply["cpacs_file_path"] = kw.get("cpacs_file_path")
            return reply
        if tool == "set_inputs" and self.cycle_fails:
            return {"error": {"type": "UnknownVariable", "message": "'T4_MAXX' is not a variable"}}
        return super().server(tool, kw)


@pytest.fixture
def rworld(monkeypatch, tmp_path):
    monkeypatch.setenv("AVION_CPACS_TAILS", "1")
    monkeypatch.setenv("AVION_CPACS_WING_TC", "1")
    state = DesignState()
    state.sessions.update({"aviary": "av", "pycycle": "pc"})
    dp.init_data_plane(state, SERVERS)
    kbm.configure_run(None, 0, 1, None)
    w = RecordedWorld(tmp_path)
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (TRIM_MASS, "carried sized mass (N_IF it2 record)"))
    monkeypatch.setattr(dp, "carried_engine", lambda: (str(w.deck), CARRIED_SCALE, "carried engine (record)"))
    dp.register_tools([_Tool(w, n) for n in ("set_aircraft_parameters", "get_cruise_point", "set_inputs")])
    dp._call_ctx.__dict__.clear()
    state.data_store["geometry_wing"] = dict(WING_10)
    yield w
    dp._call_ctx.__dict__.clear()
    dp.init_data_plane(DesignState(), {})


def _estimate(world, index, **change):
    reply = json.loads(json.dumps(CALLS[index]["reply"]))
    if "wing_kg" in change:
        reply["mass_breakdown"]["components"]["mWing_kg"] = change["wing_kg"]
    if "tc" in change:
        reply["mass_breakdown"]["sizing_report"]["wing_stations"]["tc_avg"] = change["tc"]
    world.estimates.append(reply)
    return world.call("estimate_mass", cpacs_file_path=str(world.cpacs))


def _replay_until_sized(world):
    """#4 morph AR 9.36 -> 10, #5 estimate on the pre-morph copy, #12 aviary set, #13-#14 cruise point + sizing."""
    world.morph("B")
    dp._design_state.data_store["geometry_wing"] = dict(WING_10)
    assert _estimate(world, 5).get("error_code") is None
    world.call("set_aircraft_parameters", session_id="av", parameters={})
    world.call("set_inputs", session_id="pc", values=dict(CYCLE_INPUTS))


def test_w1_replay_reproduces_the_recorded_sizing(rworld):
    _replay_until_sized(rworld)
    assert dp._design_state.data_store["aviary_cruise_point"]["design_fingerprint"] == FP_STALE
    assert rworld.fn_des_sent()[-1] == CALLS[14]["args"]["Fn_DES"]                 # 6,775.256804699464, as recorded


def test_w1_n_if_it2_the_engine_flown_is_sized_on_the_flown_design(rworld):
    _replay_until_sized(rworld)
    _estimate(rworld, 15)                                                           # #15: t/c of the morphed file
    assert not dp.engine_is_current() and not dp.cycle_is_sized("pc")               # was: still "current"
    rworld.call("run_cycle", session_id="pc")                                       # #16: re-sized before it runs
    rworld.call("set_aircraft_parameters", session_id="av", parameters={})          # #24: the agent's set
    store = dp._design_state.data_store
    assert dp.aviary_held_fingerprint("av") == FP_FLOWN                              # what #25 flew
    assert store["aviary_cruise_point"]["design_fingerprint"] == FP_FLOWN            # cruise point = flown design
    assert dp.engine_is_current() and cc.status(cc.CYCLE) == "ok"
    want = coupling.fn_des_from_lift_to_drag(TRIM_MASS, POINTS[FP_FLOWN]["lift_to_drag"])
    assert rworld.fn_des_sent()[-1] == want
    assert coupling.get_var(dp._design_state, "prop.fn_lbf") == want
    # O_IF it2 sized the same wing on the flown design: 6,775.256872272325 lbf (its mass basis differs by 3e-11)
    assert want == pytest.approx(FIXTURE["o_if_it2_fn_des"], rel=1e-12)
    assert want != pytest.approx(CALLS[14]["args"]["Fn_DES"], rel=1e-12, abs=0)     # not the stale 6,775.2568047


def test_w1_the_t_c_change_after_the_run_makes_the_engine_stale_until_run_cycle(rworld):
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()
    _estimate(rworld, 15)
    assert not dp.engine_is_current() and cc.status(cc.CYCLE) == "engine_stale"
    refusal = cc.check("run_simulation", {"session_id": "av"})
    assert refusal and "cycle inputs were set again" in refusal["error"]
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()
    assert dp._design_state.data_store["aviary_cruise_point"]["design_fingerprint"] == FP_FLOWN


def test_w1_tails_t_c_and_mass_are_exact_in_the_key(rworld, monkeypatch):
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    store = dp._design_state.data_store
    basis = dp.design_basis()
    tails = dict(store["geometry_tails"])
    store["geometry_tails"] = dict(tails, **{"Aircraft.VerticalTail.AREA": tails["Aircraft.VerticalTail.AREA"] + 1e-9})
    assert dp.design_basis() != basis and not dp.engine_is_current()
    store["geometry_tails"] = tails
    assert dp.design_basis() == basis and dp.engine_is_current()
    tc = dict(store["geometry_wing_tc"])
    store["geometry_wing_tc"] = dict(tc, **{dp.WING_TC_KEY: TC_FLOWN})
    assert round(TC_FLOWN, 6) == round(TC_STALE, 6)                                  # what the old key compared
    assert dp.design_basis() != basis and not dp.engine_is_current()
    store["geometry_wing_tc"] = tc
    key = dp._cycle_sizing_key()
    assert key[0] == TRIM_MASS and key[1] == CALLS[13]["cruise_point"]["lift_to_drag"]   # unrounded
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (TRIM_MASS + 1e-9, "test"))
    assert not dp.engine_is_current()                                                # 1e-9 kg: another Fn_DES


def test_w1_a_wing_mass_change_alone_does_not_resize(rworld):
    """aviary's cruise L/D does not depend on the wing mass (its fingerprint does not cover it): no re-sizing."""
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    sent = len(rworld.fn_des_sent())
    _estimate(rworld, 5, wing_kg=8321.938322771759)                                 # same t/c and tails, new wing
    assert dp.engine_is_current()
    rworld.call("set_aircraft_parameters", session_id="av", parameters={})          # the new wing mass reaches aviary
    assert dp.aviary_held_fingerprint("av") == FP_STALE and dp.engine_is_current()
    rworld.call("run_cycle", session_id="pc")
    assert len(rworld.fn_des_sent()) == sent                                         # no re-sizing set_inputs


def test_w1_an_agent_set_that_changes_aviarys_cruise_aero_makes_the_engine_stale(rworld):
    """Held-fingerprint check: an aero input the framework does not apply (not in design_basis) still counts."""
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()
    rworld.call("set_aircraft_parameters", session_id="av", parameters={AERO_EXTRA: 2.2})
    held = dp.aviary_held_fingerprint("av")
    assert held not in (FP_STALE, FP_FLOWN)
    assert not dp.engine_is_current() and cc.status(cc.CYCLE) == "engine_stale"
    rworld.call("run_cycle", session_id="pc")                                       # re-sized on the held design
    store = dp._design_state.data_store
    assert dp.engine_is_current() and store["aviary_cruise_point"]["design_fingerprint"] == held
    assert rworld.fn_des_sent()[-1] == coupling.fn_des_from_lift_to_drag(TRIM_MASS, 17.0)


def test_w1_cycle_inputs_set_after_the_run_make_the_engine_stale(rworld):
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()
    same = {k: v for k, v in CALLS[14]["args"].items() if k in ("T4_MAX", "splitter.BPR")}
    rworld.call("set_inputs", session_id="pc", values=same)                         # the same values: still current
    assert dp.engine_is_current()
    rworld.call("set_inputs", session_id="pc", values={"T4_MAX": 2900.0})           # the deck would be another engine
    assert not dp.engine_is_current() and cc.status(cc.CYCLE) == "engine_stale"
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()
    assert dp._design_state.data_store["prop_cycle_inputs"]["T4_MAX"] == 2900.0


def test_w1_a_failed_set_inputs_leaves_the_session_inputs_unknown_until_a_run(rworld):
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    rworld.cycle_fails = True
    reply = rworld.call("set_inputs", session_id="pc", values={"T4_MAX": 2857.0})
    assert reply.get("error")
    assert not dp.engine_is_current()
    rworld.cycle_fails = False
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()


def test_w1_a_framework_refusal_of_set_inputs_changes_nothing(rworld):
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    reply = rworld.call("set_inputs", session_id="pc", values={"fc.MN": 0.80})      # FLIGHT_CONDITION_FIXED
    assert reply.get("error_code") == "FLIGHT_CONDITION_FIXED"
    assert dp.engine_is_current()


def test_w1_an_input_never_sent_before_the_run_counts_as_a_change(rworld):
    """The framework knows a session's inputs only from the set_inputs it saw; a first value for an input set after the
    run may differ from the session's default, so the engine is re-run (refuse, never guess)."""
    rworld.morph("B")
    dp._design_state.data_store["geometry_wing"] = dict(WING_10)
    _estimate(rworld, 5)
    rworld.call("set_inputs", session_id="pc", values={})            # sized on pycycle's own cycle defaults
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()
    rworld.call("set_inputs", session_id="pc", values={"T4_MAX": 2857.0})
    assert not dp.engine_is_current()
    rworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()


def test_w1_scale_dependent_drag_is_recorded_and_compares_the_basis_only(rworld):
    """H5: with drag that depends on the engine scale the point (carried scale) is never the held design (flown scale);
    the held check would refuse every mission, so it is skipped -- and that is recorded. Not this study's aviary."""
    _replay_until_sized(rworld)
    rworld.call("run_cycle", session_id="pc")
    store = dp._design_state.data_store
    assert "cruise_point_scale_dependent" not in store                       # recorded points: depends false
    dp._call_ctx.aviary_sid = "av"
    dp._cruise_basis.set(dp.design_basis())
    point = dict(store["aviary_cruise_point"], cd_depends_on_engine_scale=True)
    point.pop("_design_basis"), point.pop("_session")
    dp.intercept_response("get_cruise_point", json.dumps({"success": True, "cruise_point": point}))
    dp._cruise_basis.set(None)
    assert "cd_depends_on_engine_scale True" in store["cruise_point_scale_dependent"]
    store.setdefault("aviary_held_fingerprint_by_session", {})["av"] = "fp-at-the-flown-scale"
    assert dp._cycle_sizing_key() is not None                                  # basis compared, held not
