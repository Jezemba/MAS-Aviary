"""The mission stage may not finish without applying and validating the design (2026-10-09).

Sandbox 20261009-093133: the mission agent called read_design_json and configure_mission only, then
finished claiming VALIDATION_STATUS valid; run_simulation was refused GEOMETRY_NOT_APPLIED three times.
Stubbed: no MCP server, no model, no .env.
"""
import json
from types import SimpleNamespace

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import artifact_checks as ac


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    yield dp._design_state


def step(name, observation=""):
    return SimpleNamespace(tool_calls=[SimpleNamespace(name=name)], observations=observation)


def agent(*tool_names, name="mission_architect"):
    return SimpleNamespace(name=name, tools={t: object() for t in tool_names})


MISSION_TOOLS = ("get_design_space", "create_session", "set_aircraft_parameters", "configure_mission")
VALID = json.dumps({"success": True, "valid": True, "applied": []})
INVALID = json.dumps({"success": True, "valid": False, "violations": ["x"]})


def test_the_sandbox_failure_is_refused_with_the_missing_step(fresh):
    """read_design_json + configure_mission, then final_answer: exactly 20261009-093133."""
    memory = SimpleNamespace(steps=[step("read_design_json", "{}"), step("configure_mission", '{"success": true}')])
    with pytest.raises(ac.MissingValidatedParameters) as exc:
        ac.validated_parameters_before_finish("done", memory, agent(*MISSION_TOOLS))
    assert "set_aircraft_parameters" in str(exc.value) and "configure_mission" in str(exc.value)
    assert fresh.data_store["final_answer_refused_not_validated"] == 1


def test_a_valid_set_aircraft_parameters_lets_it_finish():
    memory = SimpleNamespace(steps=[step("configure_mission"), step("set_aircraft_parameters", VALID)])
    assert ac.validated_parameters_before_finish("done", memory, agent(*MISSION_TOOLS)) is True


def test_an_invalid_result_does_not_count():
    memory = SimpleNamespace(steps=[step("set_aircraft_parameters", INVALID)])
    with pytest.raises(ac.MissingValidatedParameters):
        ac.validated_parameters_before_finish("done", memory, agent(*MISSION_TOOLS))


def test_agents_that_did_no_mission_work_are_never_held():
    """Simulation / integrator / a networked peer that only read things."""
    sim = SimpleNamespace(steps=[step("run_simulation", "{}"), step("get_results", "{}")])
    assert ac.validated_parameters_before_finish("x", sim, agent("run_simulation", "get_results")) is True
    peer = SimpleNamespace(steps=[step("get_design_space", "{}")])
    assert ac.validated_parameters_before_finish("x", peer, agent(*MISSION_TOOLS)) is True


def test_the_refusals_are_bounded_and_the_bypass_is_recorded(fresh):
    a = agent(*MISSION_TOOLS)
    memory = SimpleNamespace(steps=[step("configure_mission")])
    for _ in range(ac.MAX_VALIDATION_REFUSALS):
        with pytest.raises(ac.MissingValidatedParameters):
            ac.validated_parameters_before_finish("done", memory, a)
    assert ac.validated_parameters_before_finish("done", memory, a) is True
    assert fresh.data_store["finished_without_validated_parameters"] == ["mission_architect"]


def test_every_agent_gets_both_gates():
    checks = ac.with_artifact_checks()
    assert ac.mesh_before_finish in checks and ac.validated_parameters_before_finish in checks
    assert ac.with_artifact_checks(checks) == checks          # no duplicates
