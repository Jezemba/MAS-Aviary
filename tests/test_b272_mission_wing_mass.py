"""B272: the mission flies mass-mcp's current wing mass even when the agent's last set_aircraft_parameters was refused.

l20 networked_graph_routed link 6 (2026-11-12): reshaped wing, mass-mcp 9,235 kg; the agent's set call also named the
derived Aircraft.Wing.SPAN, aviary refused the whole call (READ_ONLY_PARAMETER) and the mission flew 9,008 kg.
"""
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState


class _Setter:
    def __init__(self):
        self.calls = []

    def forward(self, **kw):
        self.calls.append(kw)
        return "{}"


@pytest.fixture
def state(monkeypatch):
    s = DesignState()
    s.sessions["aviary"] = "av1"
    s.data_store["design_params_applied"] = {"Aircraft.Wing.AREA": 130.0, "Aircraft.Wing.MASS": 9008.73,
                                             "Aircraft.Wing.SPAN": 38.67}
    s.data_store["design_params_derived"] = ["Aircraft.Wing.SPAN"]
    monkeypatch.setattr(dp, "_design_state", s)
    setter = _Setter()
    monkeypatch.setattr(dp, "_registered_tools", {"set_aircraft_parameters": setter})
    return s, setter


def test_a_new_wing_mass_is_applied_before_the_mission(state):
    s, setter = state
    s.data_store["mass_wing_kg"] = 9235.49
    dp._reapply_current_wing_mass({"session_id": "av1"})
    assert len(setter.calls) == 1
    call = setter.calls[0]
    assert call["session_id"] == "av1"
    assert "Aircraft.Wing.SPAN" not in call["parameters"]          # a derived name would get the call refused
    assert call["parameters"]["Aircraft.Wing.AREA"] == 130.0


def test_nothing_to_do_when_aviary_holds_the_current_mass(state):
    s, setter = state
    s.data_store["mass_wing_kg"] = 9008.73
    dp._reapply_current_wing_mass({"session_id": "av1"})
    assert setter.calls == []


def test_nothing_to_do_before_mass_mcp_ran(state):
    _, setter = state
    dp._reapply_current_wing_mass({"session_id": "av1"})
    assert setter.calls == []


def test_it_runs_on_every_mission():
    src = (Path(__file__).resolve().parents[1] / "src" / "tools" / "data_plane.py").read_text()
    block = src.split('if tool_name == "run_simulation" and _design_state and mcp_name == "aviary":')[1].split("\n    if ")[0]
    assert "_reapply_current_wing_mass(resolved)" in block
