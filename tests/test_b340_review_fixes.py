"""B340 (independent review of integration/final-rerun-2026-11-18): engine sized on the CURRENT design, mass with the
carried engine, the last FLOWN link carries, pre-init seeds only its own geometry, aviary fingerprints per session,
router rule 4 on the Fn_DES basis, mass-mcp's error status.

The tool-order scenarios run every call the way the middleware does (resolve_request -> refusal -> coupling contract
-> stub server -> intercept_response) against small deterministic stub servers: aviary's cruise L/D and fingerprint
follow the wing and tail areas it HOLDS, mass-mcp's tails follow the geometry. No network, no API, no MCP servers.
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

SERVERS = {"set_aircraft_parameters": "aviary", "get_cruise_point": "aviary", "run_simulation": "aviary",
           "get_results": "aviary", "set_inputs": "pycycle", "run_cycle": "pycycle", "get_outputs": "pycycle",
           "estimate_mass": "mass", "run_su2_solver": "su2"}
WING = {"A": {"Aircraft.Wing.AREA": 122.83, "Aircraft.Wing.ASPECT_RATIO": 9.36},
        "B": {"Aircraft.Wing.AREA": 140.00, "Aircraft.Wing.ASPECT_RATIO": 10.50}}
HT = {"A": 31.12, "B": 36.00}          # the measured horizontal tail follows the geometry (mass-mcp B304)
TRIM_MASS = 90000.0


class World:
    """Stub aviary / pycycle / mass-mcp servers and the geometry the TiGL session holds."""

    def __init__(self, tmp_path):
        self.geometry = "A"
        self.held = {}                  # aviary session -> params it holds
        self.cycle_values = {}
        self.calls = []
        self.deck = tmp_path / "carried.deck"
        self.deck.write_text("deck")
        self.cpacs = tmp_path / "geom.xml"
        self.cpacs.write_text("<cpacs/>")

    # -- servers --------------------------------------------------------------------------------------------
    def _fp(self, sid):
        held = self.held.get(sid, {})
        return "fp-" + hashlib.md5(json.dumps(sorted(held.items()), default=str).encode()).hexdigest()[:8]

    def server(self, tool, kw):
        if tool == "set_aircraft_parameters":
            sid = kw["session_id"]
            self.held.setdefault(sid, {}).update(kw.get("parameters") or {})
            return {"success": True, "design_fingerprint": self._fp(sid),
                    "applied": [{"name": k, "old_value": None, "new_value": v}
                                for k, v in (kw.get("parameters") or {}).items()]}
        if tool == "get_cruise_point":
            held = self.held.get(kw["session_id"], {})
            ld = (15.0 + 0.05 * (held.get("Aircraft.Wing.AREA", 127.28) - 122.0)
                  - 0.02 * (held.get("Aircraft.HorizontalTail.AREA", 32.98) - 31.0))
            fp = self._fp(kw["session_id"])
            return {"success": True, "design_fingerprint": fp, "cruise_point": {
                "mach": kw["mach"], "altitude_ft": kw["altitude_ft"], "mass_kg": kw["mass_kg"], "cl": 0.6,
                "cd": 0.6 / ld, "lift_to_drag": ld, "engine_scale_used": kw.get("engine_scale"),
                "cd_depends_on_engine_scale": False, "design_fingerprint": fp}}
        if tool == "set_inputs":
            self.cycle_values.update(kw.get("values") or {})
            return {"updated": list((kw.get("values") or {}).keys()), "skipped": []}
        if tool == "run_cycle":
            return {"success": True, "converged": True,
                    "outputs": {"perf.Fn": [self.cycle_values.get("Fn_DES", 5900.0)], "perf.TSFC": [0.55]}}
        if tool == "estimate_mass":
            g = self.geometry
            tail = {"area_m2": HT[g], "aspect_ratio": 4.98, "taper_ratio": 0.33, "tc_avg": 0.10, "uid": "Wing2H"}
            fin = {"area_m2": 21.5, "aspect_ratio": 1.49, "taper_ratio": 0.35, "tc_avg": 0.11, "uid": "Wing3V"}
            return {"status": "success", "cpacs_file_path": kw.get("cpacs_file_path"), "mass_breakdown": {
                "components": {"mWing_kg": 7800.0 + (500.0 if g == "B" else 0.0)}, "mTOM_sized_kg": 88000.0,
                "sizing_converged": True, "sizing_report": {"driver": {"converged": True},
                                                            "tails": {"horizontal": tail, "vertical": fin}}}}
        return {"success": True}

    # -- the middleware's order ------------------------------------------------------------------------------
    def call(self, tool, **kw):
        dp.take_refusal()
        resolved = dp.resolve_request(tool, dict(kw))
        refusal = dp.take_refusal()
        if refusal:
            return refusal
        missing = cc.check(tool, resolved)
        if missing:
            return missing
        cc.note_call_start(tool)
        self.calls.append((tool, json.loads(json.dumps(resolved, default=str))))
        reply = self.server(tool, resolved)
        out = dp.intercept_response(tool, json.dumps(reply))
        return json.loads(out) if isinstance(out, str) else out

    def morph(self, to):
        """A TiGL morph + export: the measured wing is the new one (B363: the design is the geometry's content --
        tigl's measured before / after -- so the morph is observed as the middleware observes it)."""
        from src.tools import duplicate_guard as dg

        before = self.geometry
        self.geometry = to
        gs = dg._state()
        if "tigl-1" not in gs["geometry_identity"]:
            dg.observe("open_cpacs", {"source_type": "xml_string", "source": "<cpacs>A</cpacs>"},
                       json.dumps({"session_id": "tigl-1"}), "success", {"seq": 1})
        dg.observe("set_high_level_parameters", {"session_id": "tigl-1", "component_uid": "Wing1"},
                   json.dumps({"geometry_morph": {"rebuilt": True, "before": {"design": float(ord(before))},
                                                  "after": {"design": float(ord(to))}}}), "success", {"seq": 2})
        dp._design_state.data_store["geometry_wing"] = dict(WING[to])

    def fn_des_sent(self):
        return [r["values"]["Fn_DES"] for t, r in self.calls if t == "set_inputs" and "Fn_DES" in r.get("values", {})]


class _Tool:
    def __init__(self, world, name):
        self.world, self.name = world, name

    def forward(self, **kw):
        return json.dumps(self.world.call(self.name, **kw))


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("AVION_CPACS_TAILS", "1")
    state = DesignState()
    state.sessions.update({"aviary": "av", "pycycle": "pc"})
    dp.init_data_plane(state, SERVERS)
    kbm.configure_run(None, 0, 1, None)
    w = World(tmp_path)
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (TRIM_MASS, "carried sized mass (test)"))
    monkeypatch.setattr(dp, "carried_engine", lambda: (str(w.deck), 1.33, "carried engine (test)"))
    dp.register_tools([_Tool(w, n) for n in ("set_aircraft_parameters", "get_cruise_point", "set_inputs")])
    dp._call_ctx.__dict__.clear()
    state.data_store["geometry_wing"] = dict(WING["A"])
    yield w
    dp._call_ctx.__dict__.clear()
    dp.init_data_plane(DesignState(), {})


def _expected(world):
    g = world.geometry
    ld = 15.0 + 0.05 * (WING[g]["Aircraft.Wing.AREA"] - 122.0) - 0.02 * (HT[g] - 31.0)
    return coupling.fn_des_from_lift_to_drag(TRIM_MASS, ld)


def _start_on_A(world):
    """Iteration state on geometry A: mass, engine sized and run, aviary set."""
    world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
    world.call("set_inputs", session_id="pc", values={})
    world.call("run_cycle", session_id="pc")
    world.call("set_aircraft_parameters", session_id="av", parameters={})
    assert dp.engine_is_current()


# ---- #1: the engine is sized on the current design, whatever the tool order --------------------------------

ORDERS = {
    "mass, set_inputs, run_cycle": ["mass", "inputs", "cycle"],
    "set_inputs, mass, run_cycle": ["inputs", "mass", "cycle"],
    "aviary set, mass, set_inputs, run_cycle": ["aviary", "mass", "inputs", "cycle"],
    "mass, aviary set, set_inputs, run_cycle": ["mass", "aviary", "inputs", "cycle"],
    "mass, run_cycle (auto-sized)": ["mass", "cycle"],
    "set_inputs, aviary set, mass, aviary set, run_cycle": ["inputs", "aviary", "mass", "aviary", "cycle"],
    "mass, set_inputs, run_cycle, get_outputs": ["mass", "inputs", "cycle", "outputs"],
}


@pytest.mark.parametrize("order", list(ORDERS))
def test_b340_morph_then_propulsion_gives_the_same_fn_des_in_every_order(world, order):
    _start_on_A(world)
    world.morph("B")
    assert not dp.engine_is_current()                  # review #1: a morph makes the engine not current
    for step in ORDERS[order]:
        if step == "mass":
            world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
        elif step == "inputs":
            world.call("set_inputs", session_id="pc", values={})
        elif step == "aviary":
            world.call("set_aircraft_parameters", session_id="av", parameters={})
        elif step == "cycle":
            world.call("run_cycle", session_id="pc")
        elif step == "outputs":
            world.call("get_outputs", session_id="pc", names=["perf.Fn"])
    assert dp.engine_is_current(), order
    fn = coupling.get_var(dp._design_state, "prop.fn_lbf")
    assert fn == pytest.approx(_expected(world), rel=1e-12), order          # B's L/D, B's tails
    assert world.fn_des_sent()[-1] == pytest.approx(_expected(world), rel=1e-12)


def test_b340_the_old_wings_point_is_never_used_after_a_morph(world):
    """The review's order: morph -> estimate_mass -> set_inputs sized Fn_DES on A's L/D (aviary still held A)."""
    _start_on_A(world)
    fn_a = coupling.get_var(dp._design_state, "prop.fn_lbf")
    world.morph("B")
    world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
    world.call("set_inputs", session_id="pc", values={})
    assert world.fn_des_sent()[-1] != pytest.approx(fn_a)
    assert world.fn_des_sent()[-1] == pytest.approx(_expected(world))


def test_b340_a_new_mass_estimate_with_other_tails_resizes(world):
    _start_on_A(world)
    world.morph("B")
    world.call("set_inputs", session_id="pc", values={})          # sized before the new tails were measured
    first = world.fn_des_sent()[-1]
    world.call("estimate_mass", cpacs_file_path=str(world.cpacs))   # tails of B arrive
    assert not dp.cycle_is_sized("pc")
    world.call("run_cycle", session_id="pc")                         # auto re-sized on the point with B's tails
    assert first != pytest.approx(_expected(world)) and world.fn_des_sent()[-1] == pytest.approx(_expected(world))


# ---- #2 / #3: mass always with the carried engine -------------------------------------------------------------

def test_b340_mass_uses_the_carried_engine_before_and_after_propulsion(world):
    world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
    before = [r for t, r in world.calls if t == "estimate_mass"][-1]
    world.call("set_inputs", session_id="pc", values={})
    world.call("run_cycle", session_id="pc")
    dp._design_state.data_store["engine_deck"] = {"file": "/decks/this_iteration.deck", "cycle_key": "x"}
    world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
    after = [r for t, r in world.calls if t == "estimate_mass"][-1]
    for r in (before, after):
        assert r["engine_deck_file"] == str(world.deck) and r["engine_scale_factor"] == 1.33


def test_b340_no_deadlock_after_a_morph_with_a_stale_deck(world):
    _start_on_A(world)
    dp._design_state.data_store["engine_deck"] = {"file": "/decks/a.deck", "cycle_key": "old"}
    world.morph("B")
    reply = world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
    assert reply.get("error_code") is None and reply["status"] == "success"
    assert world.call("run_cycle", session_id="pc").get("error_code") is None
    assert dp.engine_is_current()


def test_b340_get_outputs_before_run_cycle_does_not_record_the_previous_thrust(world):
    _start_on_A(world)
    world.morph("B")
    world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
    world.call("set_inputs", session_id="pc", values={})            # re-sized; the session still holds A's run
    dp.intercept_response("get_outputs", json.dumps({"values": {"perf.Fn": [1234.0], "perf.TSFC": [0.5]}}))
    assert coupling.get_var(dp._design_state, "prop.fn_lbf") != 1234.0
    assert not dp.engine_is_current()


# ---- #6: aviary fingerprints -------------------------------------------------------------------------------

def test_b340_get_results_without_flown_fingerprint_is_not_taken_as_held(world):
    _start_on_A(world)
    point = dp._design_state.data_store["aviary_cruise_point"]
    dp._call_ctx.aviary_sid = "av"
    dp.intercept_response("get_results", json.dumps({"success": True, "design_fingerprint": "fp-flown-X"}))
    assert dp.aviary_held_fingerprint("av") is None
    assert "ambiguous" not in json.dumps(point) and "without flown_design_fingerprint" in \
        dp._design_state.data_store["aviary_fingerprint_ambiguous"]


def test_b340_flown_and_held_fingerprints_are_kept_apart_per_session(world):
    dp._call_ctx.aviary_sid = "av"
    dp.intercept_response("get_results", json.dumps({"success": True, "design_fingerprint": "held-Y",
                                                     "flown_design_fingerprint": "flown-X"}))
    store = dp._design_state.data_store
    assert dp.aviary_held_fingerprint("av") == "held-Y"
    assert store["aviary_flown_fingerprint_by_session"]["av"] == "flown-X"
    dp._call_ctx.aviary_sid = "other"
    dp.intercept_response("set_aircraft_parameters", json.dumps({"success": True, "design_fingerprint": "Z",
                                                                 "applied": []}))
    assert dp.aviary_held_fingerprint("other") == "Z" and dp.aviary_held_fingerprint("av") == "held-Y"


# ---- #10: router rule 4 on the Fn_DES basis ---------------------------------------------------------------------

def test_b340_rule_4_uses_the_trim_mass_and_the_attached_decks_reference(world, monkeypatch):
    from src.coordination import graph_router as gr

    _start_on_A(world)
    reg = dict(coupling.registry(dp._design_state), **{"aero.cl_cruise": 0.56, "aero.cd_cruise": 0.02,
                                                       "aero.target_cl": 0.56, "mass.mtom_kg": 99000.0})
    zero = [{"tool": "run_simulation", "status": "error", "outputs": {"success": False, "converged": False}}]
    d = gr.route_after_simulation(zero, reg, None, gr.SIMPLE)
    assert d.rule != "4"           # Fn_DES = needed at the trim mass; this link's 99 t estimate does not enter
    store = dp._design_state.data_store
    store["engine_deck_attached"] = {"av": "/decks/d.deck"}
    store["engine_deck_references"] = {"/decks/d.deck": {"reference_fn_des_lbf": 1000.0, "key": "d"}}
    d = gr.route_after_simulation(zero, reg, None, gr.SIMPLE)
    assert d.rule == "6" and "engine deck d" in d.reason          # scale against the attached deck's 1,000 lbf


# ---- #11: mass-mcp's error status ---------------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    {"status": "error", "error_message": "Iteration limit", "mass_breakdown": {
        "components": {"mWing_kg": 7800.0}, "mTOM_sized_kg": 88000.0, "sizing_converged": True}},
    {"status": "success", "mass_breakdown": "not a dict"},
])
def test_b340_a_failed_mass_reply_withdraws_the_previous_mass(world, reply):
    world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
    store = dp._design_state.data_store
    assert store.get("mass_wing_kg") and store.get("geometry_tails")
    dp.intercept_response("estimate_mass", json.dumps(reply))
    assert "mass_wing_kg" not in store and "geometry_tails" not in store
    assert coupling.get_var(dp._design_state, "mass.wing_kg") is None
    assert "estimate_mass failed" in store["mass_estimate_error"]


# ---- #4: the last flown link carries mass and engine --------------------------------------------------------------

FLOWN = {"structures": {"mtom_kg": 88000.0, "sizing_converged": True},
         "engine_deck": {"file": "/decks/flown.deck"}, "mission_applied": {"Aircraft.Engine.SCALE_FACTOR": 1.31},
         "aero": {"cl_cruise": 0.56, "cd_cruise": 0.02}}


def test_b340_trim_mass_and_engine_come_from_the_last_flown_link(monkeypatch):
    from src.coordination import prompt_assembly

    unflown = {"structures": {"mtom_kg": None, "sizing_converged": False}, "mission_applied": {}}
    monkeypatch.setattr(prompt_assembly, "read_carry_over",
                        lambda: {"previous_design": unflown, "last_flown_design": FLOWN})
    assert dp.trim_mass_basis() == (88000.0, "mass-mcp sized mass carried from the last flown iteration")
    assert dp.carried_engine()[:2] == ("/decks/flown.deck", 1.31)
    monkeypatch.setattr(prompt_assembly, "read_carry_over",
                        lambda: {"previous_design": unflown, "last_flown_design": None})
    from src.config import canonical

    real = canonical.load_canonical()
    monkeypatch.setattr(canonical, "load_canonical", lambda path=None: {**real, "baseline": {
        "sized_mtom_kg": 87500.0, "engine_deck_file": "/decks/base.deck", "engine_scale_factor": 1.2}})
    assert dp.trim_mass_basis()[0] == 87500.0 and dp.carried_engine()[:2] == ("/decks/base.deck", 1.2)


def test_b340_the_runner_carries_the_last_flown_link(tmp_path):
    from scripts import stat_batch_runner as sbr

    flown_dir = tmp_path / "repeat_000" / "c"
    unflown_dir = tmp_path / "repeat_001" / "c"
    for d in (flown_dir, unflown_dir):
        d.mkdir(parents=True)
    (flown_dir / "design_flown.json").write_text(json.dumps(FLOWN))
    (unflown_dir / "design.json").write_text(json.dumps({"structures": {"sizing_converged": False}}))
    assert sbr.last_flown_of(flown_dir) == FLOWN and sbr.last_flown_of(unflown_dir) is None
    (unflown_dir / "design_flown.json").write_text(json.dumps({"structures": {"sizing_converged": False}}))
    assert sbr.last_flown_of(unflown_dir) is None            # flew, but cannot carry: not chosen
    link = tmp_path / "repeat_002" / "c"
    path = sbr.write_carry_over(link, 2, "text", str(tmp_path / "g2.xml"), {}, None, previous_dir=str(unflown_dir),
                                flown_dir=str(flown_dir), flown_cpacs=str(tmp_path / "g1.xml"))
    carry = json.loads(Path(path).read_text())
    assert carry["last_flown_design"] == FLOWN and carry["last_flown_geometry"] == str(tmp_path / "g1.xml")
    assert carry["previous_design"]["structures"]["sizing_converged"] is False
    path = sbr.write_carry_over(link, 2, "text", None, {}, None, previous_dir="", flown_dir="")
    carry = json.loads(Path(path).read_text())
    assert carry["last_flown_design"] is None


# ---- #5: graph pre-init seeds only its own geometry -----------------------------------------------------------

def test_b340_preinit_seeds_the_flown_design_only_on_its_geometry(tmp_path):
    from src.coordination import graph_preinit

    g1, g2 = tmp_path / "g1.xml", tmp_path / "g2.xml"
    g1.write_text("<cpacs>G1</cpacs>")
    g2.write_text("<cpacs>G2</cpacs>")
    carry = {"last_flown_design": FLOWN, "last_flown_geometry": str(g1), "previous_design": {"aero": {}}}
    design, note = graph_preinit._seed_source(carry, str(g1))
    assert design == FLOWN
    design, note = graph_preinit._seed_source(carry, str(g2))
    assert design is None and "not this geometry's" in note
    design, note = graph_preinit._seed_source(dict(carry, last_flown_design=None), str(g1))
    assert design is None and "no link has flown" in note


# ---- scenario: a full link through the main tool orders of the three structures ---------------------------------

STRUCTURES = {
    # sequential pipeline: geometry -> aero -> structures -> propulsion -> mission
    "sequential": ["morph", "su2", "mass", "inputs", "cycle", "aviary"],
    # orchestrated: the orchestrator re-runs propulsion after a refused mission and a second geometry pass
    "orchestrated": ["morph", "su2", "mass", "aviary", "inputs", "cycle", "aviary", "inputs", "cycle"],
    # networked: peers in any order -- propulsion first, a mission attempt, mass late, aviary set in between
    "networked": ["morph", "inputs", "aviary", "su2", "cycle", "mass", "aviary", "cycle"],
}


@pytest.mark.parametrize("structure", list(STRUCTURES))
def test_b340_a_full_link_flies_the_same_engine_in_every_structure(world, structure, monkeypatch):
    def attach_deck(resolved):        # what _ensure_engine_deck does: the current cycle's deck on the session
        store = dp._design_state.data_store
        store["engine_deck"] = {"file": "/decks/now.deck", "key": "now", "reference_fn_des_lbf": 5900.0,
                                "cycle_key": store.get("prop_cycle_key")}
        store.setdefault("engine_deck_attached", {})["av"] = "/decks/now.deck"

    monkeypatch.setattr(dp, "_ensure_engine_deck", attach_deck)
    monkeypatch.setattr(dp, "_ensure_takeoff_mass", lambda resolved: None)
    _start_on_A(world)
    for step in STRUCTURES[structure]:
        if step == "morph":
            world.morph("B")
        elif step == "su2":           # a trimmed, converged SU2 solve of this geometry (recorded only)
            store = dp._design_state.data_store
            store.update({"aero_solve_trimmed": True, "aero_solve_converged": True, "aero_solve_target_cl": 0.56})
            coupling.put_var(dp._design_state, "aero.cl_cruise", 0.56, source_tool="run_su2_solver")
            coupling.put_var(dp._design_state, "aero.cd_cruise", 0.021, source_tool="run_su2_solver")
            cc.note_capture("su2")
        elif step == "mass":
            world.call("estimate_mass", cpacs_file_path=str(world.cpacs))
        elif step == "inputs":
            world.call("set_inputs", session_id="pc", values={})
        elif step == "cycle":
            world.call("run_cycle", session_id="pc")
        elif step == "aviary":
            world.call("set_aircraft_parameters", session_id="av", parameters={})
    dp.resolve_request("run_simulation", {"session_id": "av"})       # pre-mission re-applies (B187 / B272)
    assert dp.engine_is_current()
    assert coupling.get_var(dp._design_state, "prop.fn_lbf") == pytest.approx(_expected(world), rel=1e-12)
    held = world.held["av"]
    assert held["Aircraft.HorizontalTail.AREA"] == HT["B"] and held["Aircraft.Wing.AREA"] == WING["B"]["Aircraft.Wing.AREA"]
    assert held["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(_expected(world) / 5900.0)
    masses = [r for t, r in world.calls if t == "estimate_mass"]
    assert all(r["engine_deck_file"] == str(world.deck) and r["engine_scale_factor"] == 1.33 for r in masses)


def test_b340_an_error_reply_still_names_the_held_design(world):
    """aviary-mcp B339: a set whose validation crashed is applied server-side and its reply names the held design."""
    dp._call_ctx.aviary_sid = "av"
    dp.intercept_response("set_aircraft_parameters", json.dumps({
        "success": False, "error_code": "VALIDATION_ERROR", "applied_on_server": True, "design_fingerprint": "fp-Y"}))
    assert dp.aviary_held_fingerprint("av") == "fp-Y"
    dp.intercept_response("get_results", json.dumps({"success": False, "error_code": "LAST_RUN_FAILED",
                                                     "design_fingerprint": "fp-Y2"}))
    assert dp.aviary_held_fingerprint("av") == "fp-Y2"


# ---- B344: the seeded aero says what it is ---------------------------------------------------------------------

def test_b344_seeded_aero_is_recorded_as_seeded_with_its_own_trim(world, monkeypatch):
    from src.coordination import graph_preinit
    from src.tools.design_json import build_design

    design = {"aero": {"cl_cruise": 0.55, "cd_cruise": 0.02, "solve_target_cl": 0.5512, "solve_trimmed": True,
                       "solve_converged": True, "solve_trim_basis": {"mass_kg": 89100.0, "wing_area_m2": 122.8}}}
    graph_preinit.seed_from_design(design)
    aero = build_design(dp._design_state.data_store)["aero"]
    prov = aero["provenance"]
    assert prov["solved_on_this_link"] is False and prov["trim_mass_kg"] == 89100.0
    assert prov["trim_target_cl"] == 0.5512 and prov["this_link_trim_mass_kg"] == TRIM_MASS
    assert "SEEDED from the last flown link" in prov["note"] and "89100.0 kg" in prov["note"] \
        and "90000.0 kg" in prov["note"]
    kb = kbm.get_kb()
    entry = [e for e in kb.entries if e["tool"] == "graph_preinit_seed_aero"][-1]
    assert "not solved on this link" in entry["note"]


def test_b344_a_solve_on_this_link_replaces_the_seeded_provenance(world):
    from src.coordination import graph_preinit
    from src.tools.design_json import build_design

    graph_preinit.seed_from_design({"aero": {"cl_cruise": 0.55, "cd_cruise": 0.02, "solve_target_cl": 0.55}})
    store = dp._design_state.data_store
    store["aero_target_cl_basis"] = {"mass_kg": TRIM_MASS, "mass_source": "t", "wing_area_m2": 122.8}
    dp.note_su2_trim("create_su2_session", {}, {"session_id": "su2-1", "configured": True,
                                                "trim": {"target_cl": 0.5601}})
    dp._call_ctx.su2_session = "su2-1"
    dp._call_ctx.su2_config_override = None
    dp._note_solve_trim()
    aero = build_design(store)["aero"]
    assert aero["provenance"]["solved_on_this_link"] is True
    assert aero["solve_trim_basis"]["mass_kg"] == TRIM_MASS and aero["solve_target_cl"] == 0.5601
