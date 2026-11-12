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


# -- B261: a later link starts with the previous link's engine ---------------------------------------------

def test_the_previous_links_engine_fills_what_set_inputs_leaves_out(monkeypatch):
    """Orchestrated link 2 sent only Fn_DES and the Mach and flew the baseline engine instead of BPR 10."""
    from src.coordination import prompt_assembly

    dp.init_data_plane(DesignState(), {"set_inputs": "pycycle"})
    carried = {"T4_MAX": 2857.0, "splitter.BPR": 10.0, "fan.PR": 1.5, "lpc.PR": 1.8, "hpc.PR": 12.0}
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: {"starting_design": {"engine": carried}})
    out = dp.resolve_request("set_inputs", {"session_id": "c1", "values": {"Fn_DES": 8144.6, "fc.MN": 0.78, "fan.PR": 1.6}})
    assert out["values"]["splitter.BPR"] == 10.0 and out["values"]["hpc.PR"] == 12.0
    assert out["values"]["fan.PR"] == 1.6          # what the agent sends wins


def test_an_input_already_set_in_this_link_is_not_overwritten(monkeypatch):
    from src.coordination import prompt_assembly

    state = DesignState()
    state.data_store["design_space_engine"] = {"splitter.BPR": 7.0}
    state.data_store["engine_inputs_set"] = {"splitter.BPR": True}       # B270: set by set_inputs in this link
    dp.init_data_plane(state, {"set_inputs": "pycycle"})
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: {"starting_design": {"engine": {"splitter.BPR": 10.0}}})
    out = dp.resolve_request("set_inputs", {"session_id": "c1", "values": {"Fn_DES": 8144.6}})
    assert "splitter.BPR" not in out["values"]


def test_b270_defaults_read_with_get_design_inputs_do_not_block_the_carried_engine(monkeypatch):
    """l20 orchestrated_iterative_feedback link 13: get_design_inputs (pyCycle defaults) came before the first
    set_inputs, so the carried BPR 10 / HPC PR 12 engine was skipped and the link flew the baseline engine."""
    from src.coordination import prompt_assembly

    dp.init_data_plane(DesignState(), {"set_inputs": "pycycle", "get_design_inputs": "pycycle"})
    dp._capture_design_space("get_design_inputs", {"design_inputs": [
        {"name": n, "current_value": v} for n, v in (("splitter.BPR", 5.105), ("fan.PR", 1.685), ("hpc.PR", 9.369))]})
    carried = {"splitter.BPR": 10.0, "fan.PR": 1.5, "hpc.PR": 12.0}
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: {"starting_design": {"engine": carried}})
    out = dp.resolve_request("set_inputs", {"session_id": "c1", "values": {"Fn_DES": 9038.6, "fc.MN": 0.78}})
    assert out["values"]["splitter.BPR"] == 10.0 and out["values"]["hpc.PR"] == 12.0


def test_link_1_has_nothing_to_carry(monkeypatch):
    from src.coordination import prompt_assembly

    dp.init_data_plane(DesignState(), {"set_inputs": "pycycle"})
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: None)
    out = dp.resolve_request("set_inputs", {"session_id": "c1", "values": {"Fn_DES": 8144.6}})
    assert "splitter.BPR" not in out["values"]


# -- B262: a result belongs to the design its call started on ----------------------------------------------

def test_a_mass_result_that_started_before_a_reshape_is_stale(tmp_path):
    from src.tools import coupling_contract as cc, duplicate_guard as dg, knowledge_base as kbm

    dp.init_data_plane(DesignState(), {"run_cycle": "pycycle", "estimate_mass": "mass"})
    kbm.configure_run(str(tmp_path / "kb.jsonl"), 0, 1, None)
    dg.observe("open_cpacs", {"source": "D150.xml"}, json.dumps({"session_id": "t1"}), "success",
               {"design_fingerprint": "geo:f", "seq": 1})
    cc.note_call_start("estimate_mass")                     # agent_3's mass call reads the AR 9.36 file
    dg.observe("set_high_level_parameters", {"session_id": "t1", "component_uid": "Wing1"},
               json.dumps({"geometry_morph": {"rebuilt": True}}), "success", {"seq": 2})   # agent_1 reshapes
    coupling.put_var(dp.get_design_state(), "mass.mtom_kg", 91000.0, source_tool="estimate_mass")
    cc.note_capture("mass", {"mtom_kg": 91000.0})           # the answer comes back after the reshape
    refusal = cc.check("run_cycle", {"session_id": "c1"})
    assert refusal["missing"][0]["state"] == "stale"
    kbm.configure_run(None, 0, 1, None)


def test_a_result_with_no_reshape_during_its_call_is_current(tmp_path):
    from src.tools import coupling_contract as cc, duplicate_guard as dg, knowledge_base as kbm

    dp.init_data_plane(DesignState(), {"run_cycle": "pycycle", "estimate_mass": "mass"})
    kbm.configure_run(str(tmp_path / "kb.jsonl"), 0, 1, None)
    dg.observe("open_cpacs", {"source": "D150.xml"}, json.dumps({"session_id": "t1"}), "success",
               {"design_fingerprint": "geo:f", "seq": 1})
    cc.note_call_start("estimate_mass")
    coupling.put_var(dp.get_design_state(), "mass.mtom_kg", 91000.0, source_tool="estimate_mass")
    cc.note_capture("mass", {"mtom_kg": 91000.0})
    assert cc.check("run_cycle", {"session_id": "c1"}) is None
    kbm.configure_run(None, 0, 1, None)


# -- B263: the next link inherits the geometry the last flown mission flew --------------------------------

def test_the_flown_geometry_is_carried_not_a_later_export(tmp_path):
    from scripts.stat_batch_runner import carry_geometry_forward

    state = DesignState()
    dp.init_data_plane(state, {"run_simulation": "aviary"})
    flown = tmp_path / "export.xml"
    flown.write_text("<wing area='95'/>")
    state.data_store["morphed_cpacs_path"] = str(flown)
    dp._snapshot_flown_geometry("run_simulation", {"summary": {"fuel_burned_kg": 24306.7}})
    flown.write_text("<wing area='85'/>")                   # exported after the last mission, never flown
    out = carry_geometry_forward(state.data_store, tmp_path / "link")
    assert open(out).read() == "<wing area='95'/>"


def test_a_mission_on_the_starting_geometry_carries_nothing_new(tmp_path):
    from scripts.stat_batch_runner import carry_geometry_forward

    state = DesignState()
    dp.init_data_plane(state, {"run_simulation": "aviary"})
    dp._snapshot_flown_geometry("run_simulation", {"summary": {"fuel_burned_kg": 21788.5}})
    later = tmp_path / "later.xml"
    later.write_text("<wing area='85'/>")
    state.data_store["morphed_cpacs_path"] = str(later)       # a morph after the last mission
    assert carry_geometry_forward(state.data_store, tmp_path / "link") is None


def test_a_mission_that_did_not_fly_keeps_nothing(tmp_path):
    state = DesignState()
    dp.init_data_plane(state, {"run_simulation": "aviary"})
    state.data_store["morphed_cpacs_path"] = str(tmp_path / "x.xml")
    dp._snapshot_flown_geometry("run_simulation", {"summary": {"fuel_burned_kg": 0.0}})
    assert "flown_cpacs_path" not in state.data_store


def test_a_recommendation_with_a_unit_symbol_is_found(tmp_path):
    rec = {"kind": "recommendation", "verdict": "CONTINUE", "recommendation_to": "geometry_engineer",
           "recommended_change": "Reduce wing area further from 95 m² to ~85 m²"}
    _link(tmp_path, 0, [], design={"coordination_signals": [rec]})
    d = tmp_path / "repeat_001" / "mdo_f25_sequential_iterative_feedback"
    _link(tmp_path, 1, [])
    (d / "trace.json").write_text(json.dumps({"geometry_engineer": [
        {"step_number": 1, "model_input_messages": [{"content": "RECOMMENDED_CHANGE: Reduce wing area further from 95 m² to ~85 m²"}]}]}))
    checks = {c[0]: c[1] for c in next(iter(check_sweep(tmp_path).values()))[1]}
    assert checks["recommendation -> next link"] == PASS
