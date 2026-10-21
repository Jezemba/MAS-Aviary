"""B187 (pycycle ran unsized and B170 shrank aviary's engine to 0.994) and B188 (an identical failed mission
was repeated 37 times). Live on aipexws5 networked graph-routed links 2-7 and aipexws8 link 1, 2026-10-21.
No servers: the registered tools are stubs that go through resolve_request, as the real middleware does."""
from __future__ import annotations

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling, coupling_contract, data_plane as dp, mission_guard

SERVERS = {"set_inputs": "pycycle", "run_cycle": "pycycle", "get_outputs": "pycycle",
           "set_aircraft_parameters": "aviary", "run_simulation": "aviary", "configure_mission": "aviary"}


class _Stub:
    """A registered tool: records its calls and runs the data plane's pre-call injection on them."""

    def __init__(self, name):
        self.name, self.calls = name, []

    def forward(self, **kwargs):
        resolved = dp.resolve_request(self.name, kwargs)
        self.calls.append(resolved)
        return json.dumps({"success": True})


@pytest.fixture
def plane(monkeypatch):
    state = DesignState()
    state.sessions["pycycle"] = "cyc1"
    state.sessions["aviary"] = "av1"
    dp.init_data_plane(state, SERVERS)
    monkeypatch.setattr(dp, "_registered_tools", {})
    stubs = {n: _Stub(n) for n in ("set_inputs", "set_aircraft_parameters")}
    dp.register_tools(list(stubs.values()))
    yield state, stubs
    dp.init_data_plane(DesignState(), {})


def _design(state, mtom=78126.0, cl=0.5587, cd=0.0223):
    state.data_store["mass_mtom_kg"] = mtom
    coupling.put_var(state, "mass.mtom_kg", mtom, source_tool="estimate_mass")
    coupling.put_var(state, "aero.cl_cruise", cl, source_tool="run_su2_solver")
    coupling.put_var(state, "aero.cd_cruise", cd, source_tool="run_su2_solver")


def _cycle(fn=8041.0, sfc=0.55):
    return {"success": True, "converged": True, "outputs": {"perf.Fn": [fn], "perf.TSFC": [sfc]}}


def test_run_cycle_on_an_unsized_session_is_sized_first(plane):
    state, stubs = plane
    _design(state)
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    sized = stubs["set_inputs"].calls[-1]["values"]
    assert sized["Fn_DES"] > 7000 and sized["fc.alt"] == 33000 and sized["fc.MN"] == 0.78
    assert dp.cycle_is_sized("cyc1")
    stubs["set_inputs"].calls.clear()
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    assert stubs["set_inputs"].calls == []            # already sized on this design: not again


def test_without_mass_nothing_is_sized_and_the_contract_refuses_run_cycle(plane):
    state, stubs = plane
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    assert stubs["set_inputs"].calls == []
    assert coupling_contract.check("run_cycle", {"session_id": "cyc1"})["error_code"] == coupling_contract.ERROR_CODE


def test_an_unsized_cycle_is_not_captured_and_never_shrinks_the_engine(plane, monkeypatch):
    state, stubs = plane
    _design(state)
    monkeypatch.setattr(dp, "_auto_size_cycle", lambda resolved: None)   # the sizing could not run
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    dp._capture_cycle_outputs("run_cycle", _cycle(fn=5900.0))           # pycycle's defaults
    assert coupling.get_var(state, "prop.fn_lbf") is None
    params = dp.resolve_request("set_aircraft_parameters", {"session_id": "av1", "parameters": {}})["parameters"]
    assert "Aircraft.Engine.SCALE_FACTOR" not in params                # no 0.994
    refusal = coupling_contract.check("run_simulation", {"session_id": "av1"})
    assert refusal is not None and "engine cycle run" in json.dumps(refusal)


def test_a_sized_cycle_feeds_the_engine_and_goes_stale_when_mass_changes(plane):
    state, stubs = plane
    _design(state)
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    dp._capture_cycle_outputs("run_cycle", _cycle(fn=8041.0))
    params = dp.resolve_request("set_aircraft_parameters", {"session_id": "av1", "parameters": {}})["parameters"]
    assert params["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(8041.0 / coupling.BENCH_ENGINE_CRUISE_FN_LBF)
    assert coupling_contract.status(coupling_contract.CYCLE) == "ok"
    _design(state, mtom=85000.0)                                         # a new mass result
    assert coupling_contract.status(coupling_contract.CYCLE) == "engine_stale"
    params = dp.resolve_request("set_aircraft_parameters", {"session_id": "av1", "parameters": {}})["parameters"]
    assert "Aircraft.Engine.SCALE_FACTOR" not in params                 # the old engine is not this design's


def test_run_simulation_reapplies_the_current_engine_after_a_cycle_rerun(plane):
    state, stubs = plane
    _design(state)
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    dp._capture_cycle_outputs("run_cycle", _cycle(fn=8041.0))
    dp.resolve_request("set_aircraft_parameters", {"session_id": "av1", "parameters": {"Aircraft.Wing.AREA": 125.0}})
    state.data_store["design_params_applied"] = {"Aircraft.Wing.AREA": 125.0}
    stubs["set_aircraft_parameters"].calls.clear()
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert stubs["set_aircraft_parameters"].calls == []                 # aviary already has this engine
    _design(state, mtom=85000.0)
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})               # re-sized on the new mass
    dp._capture_cycle_outputs("run_cycle", _cycle(fn=8900.0))
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    applied = stubs["set_aircraft_parameters"].calls[-1]["parameters"]
    assert applied["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(8900.0 / coupling.BENCH_ENGINE_CRUISE_FN_LBF)


def test_graph_preinit_seed_counts_as_sized(plane):
    from src.coordination import graph_preinit

    state, _ = plane
    graph_preinit.seed_from_design({"aero": {"cl_cruise": 0.5587, "cd_cruise": 0.0223},
                                    "structures": {"wing_mass_kg": 9000.0, "mtom_kg": 78126.0},
                                    "propulsion": {"sfc_cruise": 0.55, "fn_lbf": 8041.0, "cycle_converged": True}})
    assert dp.engine_is_current() and dp.cycle_is_sized("cyc1")


# -- B188 ---------------------------------------------------------------------------------------------

ZERO = json.dumps({"success": False, "error_code": "ZERO_FUEL", "summary": {"fuel_burned_kg": 2.2e-06}})
FLOWN = json.dumps({"success": True, "summary": {"fuel_burned_kg": 21000.0}})


def test_an_unchanged_failed_mission_is_refused_until_something_is_set(plane):
    mission_guard.observe("run_simulation", {"session_id": "av1"}, ZERO, "failed")
    refusal = mission_guard.check("run_simulation", {"session_id": "av1"})
    assert refusal["error_code"] == mission_guard.ERROR_CODE and "ZERO_FUEL" in refusal["error"]
    mission_guard.observe("set_aircraft_parameters", {"session_id": "av1"}, "{}", "success")
    assert mission_guard.check("run_simulation", {"session_id": "av1"}) is None
    mission_guard.observe("run_simulation", {"session_id": "av1"}, FLOWN, "success")
    assert mission_guard.check("run_simulation", {"session_id": "av1"}) is None


def test_another_session_or_a_flown_mission_is_never_refused(plane):
    mission_guard.observe("run_simulation", {"session_id": "av1"}, ZERO, "failed")
    assert mission_guard.check("run_simulation", {"session_id": "av2"}) is None
    mission_guard.observe("run_simulation", {"session_id": "av3"}, FLOWN, "success")
    assert mission_guard.check("run_simulation", {"session_id": "av3"}) is None
