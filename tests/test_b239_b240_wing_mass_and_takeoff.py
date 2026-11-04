"""B240: the mission flies mass-mcp's wing (Aircraft.Wing.MASS, not MASS_SCALER); B239: the climb starts at V2 for
this aircraft's take-off mass (mass-mcp MTOM passed to aviary as configure_mission's takeoff_mass_kg).

Measured on the B3 baseline (2026-11-03): MASS_SCALER 1.50 flew a 12,400 kg wing where mass-mcp said 8,995 kg, and the
climb started at the stall speed; the mission did not converge. No servers: registered tools are stubs that go through
resolve_request, as the real middleware does.
"""
from __future__ import annotations

import json

import pytest

from src.coordination.design_state import DesignState
from src.tools import coupling, coupling_contract, data_plane as dp

SERVERS = {"configure_mission": "aviary", "set_aircraft_parameters": "aviary", "run_simulation": "aviary",
           "set_inputs": "pycycle", "run_cycle": "pycycle"}


class _Stub:
    def __init__(self, name, reply=None):
        self.name, self.calls, self.reply = name, [], reply or {"success": True}

    def forward(self, **kwargs):
        resolved = dp.resolve_request(self.name, kwargs)
        self.calls.append(resolved)
        return json.dumps(self.reply)


@pytest.fixture
def plane(monkeypatch):
    state = DesignState()
    state.sessions["aviary"] = "av1"
    dp.init_data_plane(state, SERVERS)
    monkeypatch.setattr(dp, "_registered_tools", {})
    stubs = {n: _Stub(n) for n in ("configure_mission", "set_aircraft_parameters")}
    dp.register_tools(list(stubs.values()))
    yield state, stubs
    dp.init_data_plane(DesignState(), {})


def _mass_ran(state, mtom=92210.7, wing=8994.6):
    state.data_store["mass_mtom_kg"] = mtom
    state.data_store["mass_wing_kg"] = wing
    coupling.put_var(state, "mass.mtom_kg", mtom, source_tool="estimate_mass")
    coupling.put_var(state, "mass.wing_kg", wing, source_tool="estimate_mass")


def test_wing_mass_is_carried_as_the_mass_itself(plane):
    state, _ = plane
    _mass_ran(state)
    params = dp.resolve_request("set_aircraft_parameters",
                                {"session_id": "av1", "parameters": {"Aircraft.Wing.MASS_SCALER": 1.5}})["parameters"]
    assert params["Aircraft.Wing.MASS"] == pytest.approx(8994.6)
    assert "Aircraft.Wing.MASS_SCALER" not in params
    assert state.data_store["_coupled_override_last"]["Aircraft.Wing.MASS_SCALER"]["asked"] == 1.5


def test_contract_names_the_wing_mass_parameter():
    assert coupling_contract.WING_MASS.parameter == "Aircraft.Wing.MASS"
    assert not hasattr(coupling, "wing_mass_to_aviary_scaler")     # the 5,998 kg transform is gone


def test_agent_configure_mission_gets_the_takeoff_mass(plane):
    state, _ = plane
    _mass_ran(state)
    out = dp.resolve_request("configure_mission", {"session_id": "av1", "range_nmi": 2500,
                                                   "takeoff_mass_kg": 70000.0})
    assert out["takeoff_mass_kg"] == pytest.approx(92210.7)          # framework value wins (B143)
    assert out["range_nmi"] == 2500


def test_mission_gets_the_takeoff_mass_once_per_mtom(plane):
    state, stubs = plane
    _mass_ran(state)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    sent = stubs["configure_mission"].calls[-1]
    assert sent["session_id"] == "av1" and sent["takeoff_mass_kg"] == pytest.approx(92210.7)
    assert sent["range_nmi"] == 2500                                   # the canonical mission stays as configured
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert len(stubs["configure_mission"].calls) == 1                # unchanged MTOM: not re-sent
    _mass_ran(state, mtom=95000.0)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert stubs["configure_mission"].calls[-1]["takeoff_mass_kg"] == pytest.approx(95000.0)


def test_no_takeoff_mass_without_a_mass_result(plane):
    state, stubs = plane
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert stubs["configure_mission"].calls == []


def test_missing_configure_mission_is_recorded_not_raised(plane, monkeypatch):
    state, _ = plane
    monkeypatch.setattr(dp, "_registered_tools", {})
    _mass_ran(state)
    dp.resolve_request("run_simulation", {"session_id": "av1"})        # must not raise
    assert state.data_store["takeoff_mass_error"] == "configure_mission is not loaded"


def test_aviary_refusal_of_the_takeoff_mass_is_recorded(plane):
    state, stubs = plane
    stubs["configure_mission"].reply = {"success": False, "error_code": "OUT_OF_BOUNDS",
                                        "error": "takeoff_mass_kg must be between 20000 and 200000, got 250000.0."}
    _mass_ran(state, mtom=250000.0)
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert "OUT_OF_BOUNDS" in state.data_store["takeoff_mass_error"]


def test_read_only_wing_mass_routes_to_the_mass_discipline_not_geometry():
    from src.coordination.graph_router import MASS, PARAM_PRODUCER

    def producer(text):
        return next((node for pattern, node in PARAM_PRODUCER if pattern.search(text)), None)

    assert producer("'Aircraft.Wing.MASS' is set by the framework") == MASS
    assert producer("'Aircraft.Wing.MASS_SCALER' is set by the framework") == MASS
