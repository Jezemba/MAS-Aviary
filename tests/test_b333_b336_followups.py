"""B333-B336 (coordinator follow-ups 2026-11-18): fixed cruise condition for the cycle, the router's thrust rule is never
skipped silently, what aviary holds is per aviary session, cruise-point contract confirmed.
No network, no API, no MCP servers.
"""
import json

import pytest

import src.tools.data_plane as dp
from src.coordination import graph_router as gr
from src.coordination.design_state import DesignState


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for var in ("AVION_REQUIRE_COUPLED_INPUTS", "AVION_ENGINE_DECK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AVION_CPACS_TAILS", "0")      # G1 has its own tests; one test below turns it on
    state = DesignState()
    # Geometry authority puts the measured wing on every set (a caller's unmeasured wing key is dropped, B143).
    state.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.83, "Aircraft.Wing.ASPECT_RATIO": 9.36}
    monkeypatch.setattr(dp, "_design_state", state)
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", {"set_aircraft_parameters": "aviary", "set_inputs": "pycycle",
                                                 "create_session": "aviary", "run_simulation": "aviary"})
    dp._call_ctx.__dict__.clear()
    yield state
    dp._call_ctx.__dict__.clear()


# ---- B333: the cycle's flight condition is fixed ------------------------------------------------------------

def _cycle(values, monkeypatch):
    monkeypatch.setattr(dp, "_inject_phase_k_b_fn_des", lambda resolved: None)
    dp.take_refusal()
    out = dp.resolve_request("set_inputs", {"session_id": "pc", "values": values})
    return out, dp.take_refusal()


@pytest.mark.parametrize("key,value", [("fc.alt", 35000.0), ("fc.MN", 0.80), ("fc.dTs", 10.0)])
def test_b333_another_flight_condition_is_refused(monkeypatch, key, value):
    _, refusal = _cycle({key: value, "splitter.BPR": 6.0}, monkeypatch)
    assert refusal["error_code"] == "FLIGHT_CONDITION_FIXED" and key in refusal["error"]
    assert "0.78" in refusal["error"] and "33000" in refusal["error"]


def test_b333_the_studys_condition_is_put_on_every_call_and_equal_values_pass(monkeypatch):
    out, refusal = _cycle({"splitter.BPR": 6.0}, monkeypatch)
    assert refusal is None and out["values"]["fc.alt"] == 33000.0 and out["values"]["fc.MN"] == 0.78
    assert "fc.dTs" not in out["values"]            # checked, not injected (pyCycle's default is the table's 0)
    out, refusal = _cycle({"fc.alt": 33000, "fc.MN": 0.78, "fc.dTs": 0.0}, monkeypatch)
    assert refusal is None


def test_b333_no_canonical_condition_refuses_instead_of_flying_the_default(monkeypatch):
    from src.config import canonical

    monkeypatch.setattr(canonical, "load_canonical", lambda path=None: {"mission": {}})
    monkeypatch.setattr(dp, "_pin_cycle_fixed_inputs", lambda resolved: None)
    _, refusal = _cycle({"splitter.BPR": 6.0}, monkeypatch)
    assert refusal["error_code"] == "CANONICAL_CYCLE_SETTINGS_UNAVAILABLE"


# ---- B334: the router's thrust-vs-drag rule ------------------------------------------------------------------

ZERO = [{"tool": "run_simulation", "status": "success", "outputs": {"success": True, "summary": {"fuel_burned_kg": 0}}}]
TRIMMED = {"aero.cl_cruise": 0.5588, "aero.cd_cruise": 0.0229, "aero.target_cl": 0.559, "mass.mtom_kg": 78126.0}


def _zero():
    return [dict(r, outputs=dict(r["outputs"], success=False, converged=False)) for r in ZERO]


def test_b334_engine_thrust_without_aviary_ld_is_an_error_not_a_skip():
    d = gr.route_after_simulation(_zero(), dict(TRIMMED, **{"prop.fn_lbf": 8100.0}), None, gr.SIMPLE)
    assert (d.next_node, d.rule) == (gr.PROPULSION, "4")
    assert "cannot be checked" in d.reason and "not recorded" in d.reason


def test_b334_an_unusable_ld_is_named():
    d = gr.route_after_simulation(_zero(), dict(TRIMMED, **{"prop.fn_lbf": 8100.0,
                                                              "aviary.cruise_l_over_d": -3.0}), None, gr.SIMPLE)
    assert (d.next_node, d.rule) == (gr.PROPULSION, "4") and "not usable" in d.reason


def test_b334_with_ld_the_rule_is_evaluated_as_before():
    reg = dict(TRIMMED, **{"prop.fn_lbf": 8100.0, "aviary.cruise_l_over_d": 13.0})
    d = gr.route_after_simulation(_zero(), reg, None, gr.SIMPLE)
    assert (d.next_node, d.rule) == (gr.MISSION, "9") and "not evaluated" not in d.reason


def test_b334_no_engine_records_that_the_rule_was_not_evaluated():
    d = gr.route_after_simulation(_zero(), dict(TRIMMED), None, gr.SIMPLE)
    assert (d.next_node, d.rule) == (gr.MISSION, "9")
    assert "rule 4 not evaluated: no engine thrust (prop.fn_lbf)" in d.reason


def test_b334_graph_preinit_seeds_the_ld_the_engine_was_sized_on(fresh, monkeypatch):
    from src.coordination import graph_preinit
    from src.tools import coupling

    monkeypatch.setattr(dp, "_cycle_sizing_key", lambda: ["k"])
    graph_preinit.seed_from_design({"propulsion": {"sfc_cruise": 0.55, "fn_lbf": 8100.0},
                                    "aviary_cruise_point": {"lift_to_drag": 17.2}})
    assert coupling.get_var(fresh, "aviary.cruise_l_over_d") == 17.2


# ---- B335: what aviary holds is per aviary session ------------------------------------------------------------

def _accepted_set(sid, params):
    """A set_aircraft_parameters on session `sid` through the pre-hooks, accepted by aviary."""
    dp.take_refusal()
    out = dp.resolve_request("set_aircraft_parameters", {"session_id": sid, "parameters": dict(params)})
    assert dp.take_refusal() is None
    dp._capture_applied_design("set_aircraft_parameters", {"success": True, "applied": [
        {"name": k, "old_value": None, "new_value": v} for k, v in out["parameters"].items()]
        + [{"name": "Aircraft.Wing.SPAN", "old_value": None, "new_value": 34.0, "derived": True}]})
    return out


def test_b335_a_new_session_holds_nothing_and_the_mission_on_it_is_refused(fresh):
    fresh.sessions["aviary"] = "a"
    fresh.data_store["_coupled_version"] = 3
    _accepted_set("a", {"Aircraft.Wing.ASPECT_RATIO": 9.36})
    assert dp.mission_input_refusal("run_simulation", {"session_id": "a"}) is None
    held, version = dp.aviary_holds("b")
    assert held == {} and version is None
    refusal = dp.mission_input_refusal("run_simulation", {"session_id": "b"})
    assert refusal["error_code"] == "MISSION_INPUTS_NOT_APPLIED" and "session b" in refusal["error"]


def test_b335_switching_sessions_mirrors_what_the_new_session_holds(fresh):
    fresh.sessions["aviary"] = "a"
    _accepted_set("a", {"Aircraft.Wing.ASPECT_RATIO": 9.36})
    assert fresh.data_store["design_params_applied"]["Aircraft.Wing.ASPECT_RATIO"] == 9.36
    dp._capture_session("create_session", {"session_id": "b"})
    assert fresh.sessions["aviary"] == "b"
    assert fresh.data_store["design_params_applied"] == {} and fresh.data_store["design_params_applied_session"] == "b"


def test_b335_the_reapply_gives_a_new_session_the_runs_design_without_derived_keys(fresh, monkeypatch):
    sent = []

    class _Setter:
        def forward(self, session_id, parameters):
            sent.append((session_id, dict(parameters)))
            return {"success": True, "applied": []}

    monkeypatch.setattr(dp, "_registered_tools", {"set_aircraft_parameters": _Setter()})
    fresh.sessions["aviary"] = "a"
    fresh.data_store["mass_wing_kg"] = 7833.0
    _accepted_set("a", {"Aircraft.Wing.ASPECT_RATIO": 9.36, "Aircraft.Wing.MASS": 7833.0})
    dp._reapply_current_wing_mass({"session_id": "a"})
    assert sent == []                                       # session a holds the mass and the results
    dp._reapply_current_wing_mass({"session_id": "b"})      # session b holds nothing
    assert sent and sent[0][0] == "b"
    assert sent[0][1]["Aircraft.Wing.ASPECT_RATIO"] == 9.36 and "Aircraft.Wing.SPAN" not in sent[0][1]
    assert dp.aviary_holds("b")[1] == dp.aviary_holds("a")[1]


def test_b335_tails_are_checked_on_the_missions_session(fresh, monkeypatch):
    monkeypatch.setenv("AVION_CPACS_TAILS", "1")
    fresh.data_store["geometry_tails"] = {k: 1.0 + i for i, k in enumerate(dp.TAIL_KEYS)}
    _accepted_set("a", {})
    assert dp.mission_input_refusal("run_simulation", {"session_id": "a"}) is None
    refusal = dp.mission_input_refusal("run_simulation", {"session_id": "b"})
    assert "aviary does not hold the measured tails" in refusal["error"]


def test_b335_the_b113_restore_sends_no_derived_key(fresh, monkeypatch):
    sent = []

    class _Tool:
        def __init__(self, name):
            self.name = name

        def forward(self, **kwargs):
            sent.append((self.name, kwargs))
            return {"success": True, "applied": []}

    monkeypatch.setattr(dp, "_registered_tools", {"configure_mission": _Tool("configure_mission"),
                                                  "set_aircraft_parameters": _Tool("set_aircraft_parameters")})
    _accepted_set("a", {"Aircraft.Wing.ASPECT_RATIO": 9.36})
    dp._restore_aviary_session("b")
    sets = [kw for name, kw in sent if name == "set_aircraft_parameters"]
    assert sets and "Aircraft.Wing.SPAN" not in sets[0]["parameters"] and sets[0]["session_id"] == "b"


# ---- B336: cruise-point contract confirmed ---------------------------------------------------------------------

def test_b336_the_cruise_point_docstring_names_the_confirmed_contract():
    doc = dp._capture_cruise_point.__doc__
    assert "to be confirmed" not in doc and "3c4e6b6" in doc and "get_cruise_point" in doc
