"""Bugs found by the 2-link study, 2026-11-09. Stubbed tools only: no server, no model."""
from __future__ import annotations

import json
import os

import pytest

from scripts.check_coupling import FAIL, PASS, check_sweep
from src.coordination.design_state import DesignState
from src.tools import coupling, data_plane as dp

# -- B258: an agent's export cannot replace a recorded result -----------------------------------------------

RESULT = "/home/x/Avion/MAS-Aviary/logs/stat_results/l2/repeat_000/mdo_f25_sequential_iterative_feedback/geometry_end.xml"


def test_an_export_onto_the_previous_links_geometry_is_redirected():
    dp.init_data_plane(DesignState(), {})
    out = dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": RESULT})
    assert out["output_path"] != RESULT and "stat_results" not in out["output_path"]
    assert os.path.basename(out["output_path"]) == "geometry_end.xml"


def test_any_geometry_end_file_is_protected_wherever_it_is(tmp_path):
    dp.init_data_plane(DesignState(), {})
    target = str(tmp_path / "geometry_end.xml")
    assert dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": target})["output_path"] != target


def test_an_ordinary_export_is_left_alone(tmp_path):
    dp.init_data_plane(DesignState(), {})
    target = str(tmp_path / "D150_simple_modified.xml")
    assert dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": target})["output_path"] == target


# -- B259: the engine deck survives the agent closing its pycycle session -----------------------------------

SERVERS = {"set_inputs": "pycycle", "run_cycle": "pycycle", "close_cycle_model": "pycycle",
           "generate_engine_deck": "pycycle", "set_engine_deck": "aviary",
           "set_aircraft_parameters": "aviary", "run_simulation": "aviary", "configure_mission": "aviary"}
DECK = {"success": True, "cached": False, "deck_file": "/decks/deck_7815.deck", "design_key": "7815",
        "reference_fn_des_lbf": 5900.0, "design_tsfc": 0.6432, "sls_net_thrust_lbf": 29088.2, "envelope_holes": []}


class _Stub:
    def __init__(self, name, reply=None):
        self.name, self.calls, self.reply = name, [], reply or {"success": True}

    def forward(self, **kwargs):
        resolved = dp.resolve_request(self.name, kwargs)
        self.calls.append(resolved)
        if callable(self.reply):
            return self.reply(resolved)
        return json.dumps(self.reply)


@pytest.fixture
def plane(monkeypatch):
    monkeypatch.delenv("AVION_ENGINE_DECK", raising=False)
    state = DesignState()
    state.sessions["pycycle"] = "cyc1"
    state.sessions["aviary"] = "av1"
    dp.init_data_plane(state, SERVERS)
    monkeypatch.setattr(dp, "_registered_tools", {})
    open_sessions = {"cyc1"}

    def generate(resolved):
        if resolved["session_id"] not in open_sessions:
            return json.dumps({"error": {"type": "KeyError", "message": f"Session '{resolved['session_id']}' not found"}})
        return json.dumps(DECK)

    stubs = {n: _Stub(n) for n in ("set_inputs", "set_aircraft_parameters", "set_engine_deck", "configure_mission")}
    stubs["generate_engine_deck"] = _Stub("generate_engine_deck", generate)
    dp.register_tools(list(stubs.values()))
    yield state, stubs, open_sessions
    dp.init_data_plane(DesignState(), {})


def _sized_engine(state):
    state.data_store["mass_mtom_kg"] = 78126.0
    coupling.put_var(state, "mass.mtom_kg", 78126.0, source_tool="estimate_mass")
    coupling.put_var(state, "mass.wing_kg", 8735.0, source_tool="estimate_mass")
    coupling.put_var(state, "aero.cl_cruise", 0.5588, source_tool="run_su2_solver")
    coupling.put_var(state, "aero.cd_cruise", 0.0229, source_tool="run_su2_solver")
    dp.resolve_request("run_cycle", {"session_id": "cyc1"})
    dp._capture_cycle_outputs("run_cycle", {"success": True, "converged": True,
                                            "outputs": {"perf.Fn": [8145.0], "perf.TSFC": [0.6432]}})


def _close(state, open_sessions):
    """What close_cycle_model does: the framework's pre-call, the server closing it, the bookkeeping after."""
    dp.resolve_request("close_cycle_model", {"session_id": "cyc1"})
    open_sessions.discard("cyc1")
    dp.note_session_closed("close_cycle_model", {"session_id": "cyc1"}, json.dumps({"success": True}))


def test_closing_the_engines_session_builds_its_deck_first(plane):
    """ws6: closed after run_cycle, then 8 x "Session not found" and a link that flew nothing."""
    state, stubs, open_sessions = plane
    _sized_engine(state)
    _close(state, open_sessions)
    assert stubs["generate_engine_deck"].calls[-1]["session_id"] == "cyc1"
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert dp.engine_deck_current("av1")
    assert not state.data_store.get("engine_deck_error")
    assert stubs["set_engine_deck"].calls[-1]["deck_file"] == DECK["deck_file"]


def test_a_session_closed_with_no_deck_says_what_to_do(plane):
    state, stubs, open_sessions = plane
    _sized_engine(state)
    open_sessions.discard("cyc1")
    dp.note_session_closed("close_cycle_model", {"session_id": "cyc1"}, json.dumps({"success": True}))
    dp.resolve_request("run_simulation", {"session_id": "av1"})
    assert not dp.engine_deck_current("av1")
    error = state.data_store["engine_deck_error"]
    assert "was closed" in error and "run_cycle" in error


def test_closing_an_engine_not_sized_on_this_design_builds_no_deck(plane):
    state, stubs, _ = plane
    _sized_engine(state)
    coupling.put_var(state, "aero.cd_cruise", 0.0250, source_tool="run_su2_solver")  # the drag changed since
    dp.resolve_request("close_cycle_model", {"session_id": "cyc1"})
    assert stubs["generate_engine_deck"].calls == []


# -- B260: the checker's false FAILs ---------------------------------------------------------------------------

def _row(tool, args, result):
    return {"time": 0.0, "agent": "a", "tool": tool, "args": args, "result": result, "error": None}


def _link(root, n, rows, design=None, result=None):
    d = root / f"repeat_{n:03d}" / "mdo_f25_sequential_iterative_feedback"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"attempt": 1, "chain_link": n, "org_structure": "sequential", **(result or {})}))
    (d / "design.json").write_text(json.dumps(design or {}))
    (d / "tool_calls_attempt1.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


WING = {"reference_area": 122.83, "aspect_ratio": 9.363, "sweep_deg": 21.65, "taper_ratio": 0.246}


def test_the_second_links_start_is_its_first_reshapes_before(tmp_path):
    first = [_row("get_high_level_parameters", {"component_uid": "Wing1"},
                  {"parameters": {"area": 122.83, "aspect_ratio": 9.363, "sweep": 21.65}})]
    second = [_row("set_high_level_parameters", {"component_uid": "Wing1", "updates": {"aspect_ratio": 10.0}},
                   {"geometry_morph": {"before": WING, "after": dict(WING, aspect_ratio=10.0)}})]
    _link(tmp_path, 0, first, design={"design_space": {"engine": {"T4_MAX": 2857.0}}})
    _link(tmp_path, 1, second + [_row("set_inputs", {"values": {"T4_MAX": "2857"}}, {"updated": ["T4_MAX"]})])
    checks = {c[0]: c[1] for c in next(iter(check_sweep(tmp_path).values()))[1]}
    assert checks["link -> link wing"] == PASS
    assert checks["link -> link engine"] == PASS


def test_mass_reading_the_frameworks_copy_of_the_export_passes(tmp_path):
    rows = [
        _row("set_high_level_parameters", {"component_uid": "Wing1"}, {"geometry_morph": {"before": WING, "after": WING}}),
        _row("export_cpacs", {}, {"cpacs_file_path": "/r/repeat_000/c/geometry_end.xml"}),
        _row("estimate_mass", {"cpacs_file_path": "/tmp/avion_mass_ab12/geometry_end.xml"},
             {"mass_breakdown": {"components": {"mWing_kg": 8892.0}}}),
        _row("set_aircraft_parameters", {"parameters": {"Aircraft.Wing.MASS": 8892.0}}, {"valid": True}),
        _row("run_simulation", {}, {"summary": {"fuel_burned_kg": 20968.0}, "engine": {"deck": "/d.deck", "external_deck": True}}),
    ]
    _link(tmp_path, 0, rows)
    checks = {c[0]: c[1] for c in next(iter(check_sweep(tmp_path).values()))[0]}
    assert checks["geometry -> mass"] == PASS


def test_mass_reading_some_other_file_still_fails(tmp_path):
    rows = [
        _row("set_high_level_parameters", {"component_uid": "Wing1"}, {"geometry_morph": {"before": WING, "after": WING}}),
        _row("export_cpacs", {}, {"cpacs_file_path": "/x/morph.xml"}),
        _row("estimate_mass", {"cpacs_file_path": "/tmp/avion_mass_ab12/D150_simple.xml"},
             {"mass_breakdown": {"components": {"mWing_kg": 8892.0}}}),
        _row("run_simulation", {}, {"summary": {"fuel_burned_kg": 20968.0}, "engine": {"deck": "/d.deck", "external_deck": True}}),
    ]
    _link(tmp_path, 0, rows)
    checks = {c[0]: c[1] for c in next(iter(check_sweep(tmp_path).values()))[0]}
    assert checks["geometry -> mass"] == FAIL
