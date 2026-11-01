"""B3: the designed cycle becomes the mission's engine (pycycle engine deck flown by aviary).

Before: aviary burned its bench table whatever pycycle designed (2026-10-27: every engine step gave
19,019.6 kg). No servers: registered tools are stubs that go through resolve_request, as the real
middleware does (same pattern as test_b187_b188).
"""
from __future__ import annotations

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling, coupling_contract, data_plane as dp

SERVERS = {"set_inputs": "pycycle", "run_cycle": "pycycle", "get_outputs": "pycycle",
           "generate_engine_deck": "pycycle", "set_engine_deck": "aviary",
           "set_aircraft_parameters": "aviary", "run_simulation": "aviary", "configure_mission": "aviary"}

DECK = {"success": True, "cached": False, "deck_file": "/decks/deck_abc.deck", "design_key": "abc",
        "reference_fn_des_lbf": 5900.0, "size_normalised": True, "design_tsfc": 0.6522,
        "sls_net_thrust_lbf": 20266.9, "envelope_holes": []}


class _Stub:
    """A registered tool: records its calls and runs the data plane's pre-call injection on them."""

    def __init__(self, name, reply=None):
        self.name, self.calls, self.reply = name, [], reply or {"success": True}

    def forward(self, **kwargs):
        resolved = dp.resolve_request(self.name, kwargs)
        self.calls.append(resolved)
        return json.dumps(self.reply)


@pytest.fixture
def plane(monkeypatch):
    monkeypatch.delenv("AVION_ENGINE_DECK", raising=False)
    state = DesignState()
    state.sessions["pycycle"] = "cyc1"
    state.sessions["aviary"] = "av1"
    dp.init_data_plane(state, SERVERS)
    monkeypatch.setattr(dp, "_registered_tools", {})
    stubs = {n: _Stub(n) for n in ("set_inputs", "set_aircraft_parameters", "set_engine_deck")}
    stubs["generate_engine_deck"] = _Stub("generate_engine_deck", dict(DECK))
    dp.register_tools(list(stubs.values()))
    yield state, stubs
    dp.init_data_plane(DesignState(), {})


def _sized_engine(state, fn=10072.0):
    """Mass and aero on record, the cycle sized on them and run (as the B187 path does it)."""
    state.data_store["mass_mtom_kg"] = 78126.0
    coupling.put_var(state, "mass.mtom_kg", 78126.0, source_tool="estimate_mass")
    coupling.put_var(state, "mass.wing_kg", 7000.0, source_tool="estimate_mass")
    coupling.put_var(state, "aero.cl_cruise", 0.5587, source_tool="run_su2_solver")
    coupling.put_var(state, "aero.cd_cruise", 0.0223, source_tool="run_su2_solver")
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    dp._capture_cycle_outputs("run_cycle", {"success": True, "converged": True,
                                            "outputs": {"perf.Fn": [fn], "perf.TSFC": [0.6522]}})


def test_mission_gets_the_designed_engine_and_its_scale(plane):
    state, stubs = plane
    _sized_engine(state, fn=10072.0)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert stubs["generate_engine_deck"].calls[-1]["session_id"] == "cyc1"
    assert stubs["set_engine_deck"].calls[-1] == {"session_id": "av1", "deck_file": DECK["deck_file"]}
    assert dp.engine_deck_current("av1")
    # the re-applied design carries SCALE_FACTOR = Fn_DES / the deck's reference thrust, not / 5936
    scale = stubs["set_aircraft_parameters"].calls[-1]["parameters"]["Aircraft.Engine.SCALE_FACTOR"]
    assert scale == pytest.approx(10072.0 / 5900.0)
    assert state.data_store["engine_scale_basis"] == "engine deck abc"
    assert coupling_contract.status(coupling_contract.ENGINE_DECK, {"session_id": "av1"}) == "ok"


def test_unchanged_deck_is_not_attached_again(plane):
    state, stubs = plane
    _sized_engine(state)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    stubs["generate_engine_deck"].reply = dict(DECK, cached=True)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert len(stubs["generate_engine_deck"].calls) == 2          # asked each time (cheap when cached)
    assert len(stubs["set_engine_deck"].calls) == 1               # attached once
    assert len(stubs["set_aircraft_parameters"].calls) == 1       # scale already current


def test_new_cycle_deck_is_attached_and_rescaled(plane):
    state, stubs = plane
    _sized_engine(state)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    stubs["generate_engine_deck"].reply = dict(DECK, deck_file="/decks/deck_bpr8.deck", design_key="bpr8")
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert stubs["set_engine_deck"].calls[-1]["deck_file"] == "/decks/deck_bpr8.deck"
    assert len(stubs["set_aircraft_parameters"].calls) == 2       # the scale key includes the deck
    assert state.data_store["engine_scale_basis"] == "engine deck bpr8"


def test_failed_deck_refuses_the_mission_with_the_reason(plane):
    state, stubs = plane
    _sized_engine(state)
    stubs["generate_engine_deck"].reply = {"error": {"type": "DesignNotConverged", "message": "LPT PR 9.1"}}
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert stubs["set_engine_deck"].calls == []
    assert not dp.engine_deck_current("av1")
    assert coupling_contract.status(coupling_contract.ENGINE_DECK, {"session_id": "av1"}) == "deck_missing"
    refusal = coupling_contract.check("run_simulation", {"session_id": "av1"})
    assert refusal["error_code"] == coupling_contract.ERROR_CODE
    assert "DesignNotConverged" in refusal["error"] and "LPT PR 9.1" in refusal["error"]
    assert any(m["state"] == "deck_missing" for m in refusal["missing"])


def test_aviary_refusing_the_deck_is_recorded(plane):
    state, stubs = plane
    _sized_engine(state)
    stubs["set_engine_deck"].reply = {"success": False, "error": {"message": "deck does not cover"}}
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert not dp.engine_deck_current("av1")
    assert "deck does not cover" in state.data_store["engine_deck_error"]


def test_bench_engine_switch(plane, monkeypatch):
    state, stubs = plane
    monkeypatch.setenv("AVION_ENGINE_DECK", "0")
    _sized_engine(state, fn=10072.0)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert stubs["generate_engine_deck"].calls == []
    scale = stubs["set_aircraft_parameters"].calls[-1]["parameters"]["Aircraft.Engine.SCALE_FACTOR"]
    assert scale == pytest.approx(10072.0 / coupling.BENCH_ENGINE_CRUISE_FN_LBF)
    assert coupling_contract.status(coupling_contract.ENGINE_DECK, {"session_id": "av1"}) == "ok"
    dp._capture_mission_engine("run_simulation", {"engine": {"deck": "bench turbofan_28k.deck",
                                                             "external_deck": False}})
    assert coupling_contract.coupling_status()["propulsion_coupled"] is False


def test_no_deck_for_an_engine_not_sized_on_this_design(plane):
    state, stubs = plane
    dp.resolve_request("run_simulation", {"session_id": "av1"})   # no mass, no cycle
    assert stubs["generate_engine_deck"].calls == []


def test_propulsion_coupled_comes_from_what_the_mission_flew(plane):
    state, stubs = plane
    _sized_engine(state)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    dp._capture_mission_engine("run_simulation", {"engine": {"deck": "/some/other.deck", "external_deck": True}})
    assert coupling_contract.coupling_status()["propulsion_coupled"] is False
    dp._capture_mission_engine("run_simulation", {"engine": {"deck": DECK["deck_file"], "external_deck": True,
                                                             "scale_factor": 1.7071}})
    status = coupling_contract.coupling_status()
    assert status["propulsion_coupled"] is True
    assert status["mission_engine"]["deck_key"] == "abc" and status["engine_deck_status"] == "ok"


def test_deck_tools_are_registered_for_the_framework_but_not_given_to_agents(monkeypatch):
    from src.tools import tool_loader

    class _T:
        def __init__(self, name):
            self.name = name          # no forward/inputs: the middleware wrap skips it

    registered = {}
    monkeypatch.setattr(dp, "_registered_tools", registered)
    all_tools = [_T("run_cycle"), _T("generate_engine_deck"), _T("set_engine_deck"), _T("run_simulation")]
    selected = tool_loader._register_framework_tools(all_tools, [all_tools[0], all_tools[3]])
    assert [t.name for t in selected] == ["run_cycle", "run_simulation"]
    assert {"generate_engine_deck", "set_engine_deck"} <= set(registered)
    selected = tool_loader._register_framework_tools(all_tools, list(all_tools))   # a "pycycle.*" toolset
    assert [t.name for t in selected] == ["run_cycle", "run_simulation"]


def test_derived_span_is_not_sent_back_on_the_engine_reapply(plane):
    """Smoke test 2026-11-01: aviary reports SPAN as applied with derived: True; re-sending it made aviary refuse
    the whole re-apply (READ_ONLY_PARAMETER), so the mission flew the previous engine scale."""
    state, stubs = plane
    _sized_engine(state)
    dp._capture_applied_design("set_aircraft_parameters", {"applied": [
        {"name": "Aircraft.Wing.AREA", "new_value": 125.8},
        {"name": "Aircraft.Wing.SPAN", "new_value": 35.05, "units": "m", "derived": True}]})
    assert state.data_store["design_params_derived"] == ["Aircraft.Wing.SPAN"]       # kept, marked derived (B95)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    sent = stubs["set_aircraft_parameters"].calls[-1]["parameters"]
    assert "Aircraft.Wing.SPAN" not in sent
    assert sent["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(10072.0 / 5900.0)


def test_coupled_is_judged_when_the_mission_flew(plane):
    state, stubs = plane
    _sized_engine(state)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    dp._capture_mission_engine("run_simulation", {"engine": {"deck": DECK["deck_file"], "external_deck": True}})
    coupling.put_var(state, "mass.mtom_kg", 80000.0, source_tool="estimate_mass")   # design moves on afterwards
    state.data_store["mass_mtom_kg"] = 80000.0
    assert not dp.engine_is_current()
    assert coupling_contract.coupling_status()["propulsion_coupled"] is True        # it was current when flown
    dp._capture_mission_engine("run_simulation", {"engine": {"deck": DECK["deck_file"], "external_deck": True}})
    assert coupling_contract.coupling_status()["propulsion_coupled"] is False       # flown on a stale engine
