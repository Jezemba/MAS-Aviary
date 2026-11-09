"""Tool and framework bugs found by the one-link smoke test, 2026-11-09. Stubbed tools only: no server, no model."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from smolagents.models import ChatMessage, MessageRole

import src.tools.data_plane as dp
from src.coordination import graph_preinit
from src.coordination.design_state import DesignState
from src.llm.thinking_model import ThinkingModel
from src.tools import coupling, coupling_contract as cc, duplicate_guard as dg, knowledge_base as kbm


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {"run_su2_solver": "su2", "run_cycle": "pycycle"})
    kbm.configure_run(str(tmp_path / "kb.jsonl"), 0, 1, None)
    yield
    kbm.configure_run(None, 0, 1, None)


def _store():
    return dp.get_design_state().data_store


def _open(session="t1", fp="geo:file"):
    dg.observe("open_cpacs", {"source": "D150.xml"}, json.dumps({"session_id": session}), "success",
               {"design_fingerprint": fp, "seq": 1})


def _close(session="t1"):
    dg.observe("close_cpacs", {"session_id": session}, json.dumps({"success": True}), "success", {"seq": 2})


def _morph(session="t1", rebuilt=True):
    dg.observe("set_high_level_parameters", {"session_id": session, "component_uid": "Wing1"},
               json.dumps({"geometry_morph": {"rebuilt": rebuilt}}), "success", {"seq": 3})


def _mass():
    state = dp.get_design_state()
    coupling.put_var(state, "mass.mtom_kg", 91000.0, source_tool="estimate_mass")
    cc.note_capture("mass", {"mtom_kg": 91000.0})


# -- B255: only a change of geometry changes the design -------------------------------------------------

def test_closing_a_session_does_not_change_the_design():
    _open()
    before = cc.current_design_fingerprint()
    _close()
    assert cc.current_design_fingerprint() == before


def test_a_mass_result_is_not_stale_after_the_session_closes():
    """networked_iterative_feedback attempt 1: mass, export, close, then run_cycle refused 12 times."""
    _open()
    _mass()
    _close()
    assert cc.check("run_cycle", {"session_id": "c1"}) is None


def test_a_morph_still_makes_the_mass_result_stale():
    _open()
    _mass()
    _morph()
    out = cc.check("run_cycle", {"session_id": "c1"})
    assert out["missing"][0]["state"] == "stale" and "EARLIER" in out["error"]


def test_a_morph_then_a_close_is_still_the_morphed_design():
    _open()
    _morph()
    morphed = cc.current_design_fingerprint()
    _close()
    assert cc.current_design_fingerprint() == morphed


def test_a_refused_morph_is_not_a_design_change():
    _open()
    before = cc.current_design_fingerprint()
    _morph(rebuilt=False)
    assert cc.current_design_fingerprint() == before


def test_opening_another_file_is_a_new_design():
    _open()
    before = cc.current_design_fingerprint()
    _open("t2", "geo:other")
    assert cc.current_design_fingerprint() != before


# -- B253: SU2's references follow the wing --------------------------------------------------------------

REPLY = {"session_id": "s1", "references": {"REF_AREA": 125.86}, "drag_floor": {"aspect_ratio": 9.759}}


def _wing(area, ar):
    _store()["geometry_wing"] = {"Aircraft.Wing.AREA": area, "Aircraft.Wing.ASPECT_RATIO": ar}


def test_the_solve_on_the_old_wings_references_is_refused():
    """networked_graph_routed: REF_AREA 125.86 on a 132.54 m^2 wing, with no warning."""
    cc.note_su2_references("create_su2_session", {}, json.dumps(REPLY))
    _wing(132.54, 10.716)
    out = cc.check("run_su2_solver", {"session_id": "s1"})
    assert out["error_code"] == cc.STALE_SU2_REFERENCES
    assert "125.9" in out["error"] and "132.5" in out["error"] and "configure_from_cpacs" in out["error"]


def test_the_solve_on_the_current_wing_runs():
    cc.note_su2_references("create_su2_session", {}, json.dumps(REPLY))
    _wing(125.86, 9.759)
    assert cc.check("run_su2_solver", {"session_id": "s1"}) is None


def test_configuring_again_clears_the_refusal():
    cc.note_su2_references("create_su2_session", {}, json.dumps(REPLY))
    _wing(132.54, 10.716)
    cc.note_su2_references("configure_from_cpacs", {"session_id": "s1"},
                           {"references": {"REF_AREA": 132.54}, "drag_floor": {"aspect_ratio": 10.716}})
    assert cc.check("run_su2_solver", {"session_id": "s1"}) is None


def test_the_cpacs_declared_area_of_the_same_wing_is_not_a_mismatch():
    cc.note_su2_references("create_su2_session", {}, {"session_id": "s1", "references": {"REF_AREA": 122.4}})
    _wing(122.83, 9.363)
    assert cc.check("run_su2_solver", {"session_id": "s1"}) is None


def test_a_stale_aspect_ratio_alone_is_refused():
    """sequential_graph_routed: AR 10.004 on a 10.520 wing."""
    cc.note_su2_references("create_su2_session", {},
                           {"session_id": "s1", "references": {"REF_AREA": 126.82}, "drag_floor": {"aspect_ratio": 10.004}})
    _wing(126.82, 10.520)
    assert cc.check("run_su2_solver", {"session_id": "s1"})["error_code"] == cc.STALE_SU2_REFERENCES


def test_a_session_or_wing_never_recorded_is_not_refused():
    assert cc.check("run_su2_solver", {"session_id": "unknown"}) is None
    cc.note_su2_references("create_su2_session", {}, json.dumps(REPLY))
    assert cc.check("run_su2_solver", {"session_id": "s1"}) is None


def test_a_failed_session_reply_records_nothing():
    cc.note_su2_references("create_su2_session", {}, json.dumps({"success": False, "error": "x"}))
    assert not _store().get("su2_session_references")


def test_the_geometrys_aspect_ratio_wins_over_the_callers(monkeypatch):
    _wing(126.82, 10.520)
    monkeypatch.setitem(dp._tool_server_map, "create_su2_session", "su2")
    out = dp.resolve_request("create_su2_session", {"aspect_ratio": 10.004, "cruise_mach": 0.78})
    assert out["aspect_ratio"] == pytest.approx(10.520)


# -- B254: a graph link's engine session flies the mission's cycle ---------------------------------------

@pytest.fixture
def calls(monkeypatch):
    seen = []

    def record(name, **kwargs):
        seen.append((name, kwargs))
        return {}

    monkeypatch.setattr(graph_preinit, "_call", record)
    monkeypatch.setattr(graph_preinit, "CACHE_DIR", graph_preinit.CACHE_DIR)
    monkeypatch.setattr(graph_preinit, "_open_sessions", lambda c, m: {"cpacs": c})
    monkeypatch.setattr("src.tools.design_json.write_design", lambda *_: None)
    dp.get_design_state().sessions["pycycle"] = "cyc1"
    return seen


DESIGN = {"aero": {"cl_cruise": 0.56, "cd_cruise": 0.023},
          "structures": {"wing_mass_kg": 8735.0, "mtom_kg": 78126.0},
          "propulsion": {"sfc_cruise": 0.643, "fn_lbf": 8144.0, "cycle_converged": True}}


def test_a_seeded_baseline_still_sets_the_engine_session(calls, monkeypatch, tmp_path):
    """The first mission's deck was built at the tool's default M0.80 / 35,000 ft, SLS 31,100 lbf instead of 29,088."""
    monkeypatch.setattr(graph_preinit, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(graph_preinit, "_cache_path", lambda c: tmp_path / "b.design.json")
    (tmp_path / "b.design.json").write_text(json.dumps(DESIGN))
    report = graph_preinit.prepare({})
    assert "error" not in report and report["results"].startswith("cached baseline")
    assert ("set_inputs", {"session_id": "cyc1", "values": {}}) in calls
    assert dp.engine_is_current()


def test_a_later_link_keeps_its_engine_inputs(calls):
    engine = {"splitter.BPR": 6.0, "fan.PR": 1.6}
    previous = dict(DESIGN, design_space={"engine": engine})
    report = graph_preinit.prepare({"previous_design": previous})
    assert report["results"] == "previous link's end-state"
    assert ("set_inputs", {"session_id": "cyc1", "values": engine}) in calls


def test_the_cache_key_changes_with_the_baseline_version(tmp_path, monkeypatch):
    cpacs = tmp_path / "c.xml"
    cpacs.write_text("<cpacs/>")
    first = graph_preinit._cache_path(str(cpacs))
    monkeypatch.setattr(graph_preinit, "BASELINE_VERSION", "next")
    assert graph_preinit._cache_path(str(cpacs)) != first


# -- B185: a report written as plain text is told what to do ---------------------------------------------

REPORT = "DESIGN_STATE:\n  CL_CRUISE: 0.5588\n  CD_CRUISE: 0.0229\n" + "  NOTE: the solve converged.\n" * 12


def _model(replies):
    model = ThinkingModel.__new__(ThinkingModel)
    model.tool_name_key, model.tool_arguments_key = "name", "arguments"
    model._thinking_enabled = False
    model._reliability = SimpleNamespace(max_retries=3)
    prompts = []

    def once(msgs, **_):
        prompts.append(list(msgs))
        return ChatMessage(role=MessageRole.ASSISTANT, content=replies.pop(0))

    model._generate_once = once
    return model, prompts


def test_a_complete_plain_text_reply_is_not_called_truncated():
    model, _ = _model([])
    assert not model._is_truncated(ChatMessage(role=MessageRole.ASSISTANT, content=REPORT))
    assert model._is_truncated(ChatMessage(role=MessageRole.ASSISTANT, content="<think>" + "x" * 500))


def test_the_retry_tells_the_model_to_call_final_answer():
    call = '{"name": "final_answer", "arguments": {"answer": "done"}}'
    model, prompts = _model([REPORT, call])
    out = model._generate_in_slot([], None, None, None)
    assert out.tool_calls[0].function.name == "final_answer"
    feedback = prompts[1][-1]["content"][0]["text"]
    assert "final_answer" in feedback and "could not be parsed" in feedback
    assert '{"name": "<tool_name>", "arguments": {...}}' in feedback and "{{" not in feedback


def test_what_the_agent_is_told_after_the_retries_names_final_answer():
    model, _ = _model([])
    with pytest.raises(ValueError, match="final_answer"):
        model.parse_tool_calls(ChatMessage(role=MessageRole.ASSISTANT, content=REPORT))
