"""sequential-prompts-v2 (Jessica, 2026-10-04): her prompts verbatim, KB handoff from the records,
best practices on request, framework-written design.json."""

import json
import re
from pathlib import Path

import pytest
import yaml

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "sequential.md"


def _her_prompts():
    text = SRC.read_text()
    parts = re.split(r"^(#{2,3} .+)$", text, flags=re.M)
    sec = {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}

    def ex(body):
        m = re.search(r"^```[^\n]*\n(.*?)^```", body, flags=re.M | re.S)
        return (m.group(1) if m else body).strip("\n")
    return {
        "BASE": ex(sec["### Initial Prompt"]),
        "geometry_engineer": ex(sec["## Agent: Geometry Engineer"]),
        "aerodynamics_analyst": ex(sec["## Agent: Aerodynamics Analyst"]),
        "structures_analyst": ex(sec["## Agent: structures_analyst"]),
        "propulsion_analyst": ex(sec["## Agent: Propulsion Agent"]),
        "mission_architect": ex(sec["## Agent: Mission Architect"]),
        "simulation_executor": ex(sec["## Agent: Simulation Executor"]),
        "mdo_integrator": ex(sec["## Agent: Mdo Integrator"]),
    }


def _norm(t):
    return "\n".join(line.rstrip() for line in t.strip("\n").split("\n"))


@pytest.mark.skipif(not SRC.exists(), reason="sequential.md not in this checkout")
def test_the_yaml_carries_her_prompts_verbatim():
    p = _her_prompts()
    d = yaml.safe_load((ROOT / "config/mdo_f25_sequential_agents.yaml").read_text())
    assert _norm(d["stage_defaults"]["base_instructions"]) == _norm(p["BASE"])
    for st in d["templates"]["mdo_f25"]["stages"]:
        assert _norm(st["role"]) == _norm(p[st["name"]]), st["name"]
        assert st.get("interface_output") == ""            # her DELIVERABLE line is not duplicated


def test_the_staged_pipeline_adds_no_stage_prompt():
    d = yaml.safe_load((ROOT / "config/mdo_f25_staged_pipeline_sequential.yaml").read_text())
    assert all(s["stage_prompt"] == "" for s in d["mdo_f25_pipeline"]["stages"])


# ---- KB handoff from the records ------------------------------------------------------------------

@pytest.fixture
def kb(monkeypatch, tmp_path):
    monkeypatch.setenv("MAS_KB_HANDOFF", "recent")
    monkeypatch.setattr(dp, "_design_state", DesignState())
    kbm.configure_run(path=str(tmp_path / "knowledge_base.jsonl"), chain_link=0, attempt=1, seed=None)
    k = kbm.get_kb()
    yield k
    kbm.configure_run(None, None, None, None)


def test_the_handoff_is_the_latest_records_newest_last(kb):
    kb.append(tool="generate_volume_mesh", server="tigl", agent="geometry_engineer", role="",
              status="success", outputs={"element_count": 2257805})
    kb.append(tool="run_su2_solver", server="su2", agent="aerodynamics_analyst", role="",
              status="success", outputs={"final_coefficients": {"CL": 0.205, "CD": 0.0021}})
    text = kbm.handoff_text("TASK")
    assert "no record of SU2" not in text and "run_su2_solver" in text
    assert text.index("generate_volume_mesh") < text.index("run_su2_solver")      # newest last
    assert re.search(r"\[\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\]", text)                  # timestamped
    assert text.rstrip().endswith("TASK")


def test_recent_records_drops_the_oldest_when_too_long():
    rows = [{"seq": i, "time_utc": "2026-10-04T00:00:00Z", "agent": "a", "tool": f"t{i}",
             "status": "success", "outputs": {"x": "y" * 200}} for i in range(40)]
    text = kbm.recent_records(rows, limit_lines=30, limit_chars=2000)
    assert "t39" in text and "t0 " not in text and len(text) <= 2000


def test_the_summary_mode_still_uses_the_summary_model_by_default(monkeypatch):
    monkeypatch.delenv("MAS_KB_HANDOFF", raising=False)
    assert kbm.handoff_mode() == "summary"


# ---- tools ------------------------------------------------------------------------------------------

def test_best_practices_sections():
    from src.tools.best_practices_tool import ReadBestPractices

    out = ReadBestPractices().forward("propulsion")
    assert "## general" in out and "## propulsion" in out and "## geometry" not in out
    assert "perf.TSFC" in out


def test_best_practices_do_not_repeat_fixed_bugs():
    text = (ROOT / "config/best_practices.md").read_text()
    assert 'component_uid="<<WING_UID>>"' not in text and "max_runtime_seconds=300" not in text
    assert "does NOT modify" not in text


def test_design_json_is_written_next_to_the_knowledge_base(monkeypatch, tmp_path):
    from src.tools.design_json import ReadDesignJson

    kbm.configure_run(path=str(tmp_path / "knowledge_base.jsonl"), chain_link=0, attempt=1, seed=None)
    try:
        monkeypatch.setattr(dp, "_design_state", DesignState())
        dp._design_state.data_store.update({
            "geometry_wing": {"Aircraft.Wing.AREA": 130.0, "_span": 34.9},
            "aero_cl_cruise": 0.205, "aero_cd_cruise": 0.0021, "mass_mtom_kg": 78126.0})
        dp.intercept_response("get_su2_status", json.dumps({"installed": True}))
        doc = json.loads((tmp_path / "design.json").read_text())
        assert doc["geometry"]["wing"] == {"Aircraft.Wing.AREA": 130.0}
        assert doc["aero"]["cd_cruise"] == 0.0021 and doc["structures"]["mtom_kg"] == 78126.0
        assert doc["mission"]["range_nmi"] == 2500
        assert json.loads(ReadDesignJson().forward())["aero"]["cl_cruise"] == 0.205
    finally:
        kbm.configure_run(None, None, None, None)


def test_every_agent_gets_the_two_new_tools():
    from src.tools.tool_loader import _with_design_state

    names = {t.name for t in _with_design_state([])}
    assert {"read_best_practices", "read_design_json", "get_design_state"} <= names


# ---- the study's fixed controls are held by the framework -------------------------------------------

@pytest.fixture
def ds(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    return dp._design_state


def test_configure_mission_is_pinned_to_the_study_mission(ds):
    out = dp.resolve_request("configure_mission", {"session_id": "a", "range_nmi": 1500, "num_passengers": 160,
                                                   "cruise_mach": 0.785, "cruise_altitude_ft": 35000})
    assert (out["range_nmi"], out["num_passengers"], out["cruise_mach"], out["cruise_altitude_ft"]) == \
        (2500, 239, 0.78, 33000)
    resp = json.loads(dp.intercept_response("configure_mission", json.dumps({"success": True})))
    assert resp["study_controls"]["overridden"]["range_nmi"] == {"asked": 1500, "used": 2500}


def test_a_matching_mission_is_not_reported_as_overridden(ds):
    dp.resolve_request("configure_mission", {"session_id": "a", "range_nmi": 2500.0, "cruise_mach": 0.78})
    resp = json.loads(dp.intercept_response("configure_mission", json.dumps({"success": True})))
    assert "study_controls" not in resp


def test_estimate_mass_method_and_material_are_pinned(ds):
    out = dp.resolve_request("estimate_mass", {"cpacs_file_path": "/nonexistent.xml",
                                               "wing_mass_method": "oas", "material": "composite"})
    assert out["wing_mass_method"] == "flops" and out["material"] == "aluminum"
    assert out["design_load_factor"] == 2.5


# ---- B142: one runner per output folder -------------------------------------------------------------

def test_a_second_runner_cannot_take_the_output_folder(tmp_path):
    import subprocess
    import sys

    from scripts.stat_batch_runner import acquire_output_lock

    assert acquire_output_lock(tmp_path) is True
    code = ("import sys; sys.path.insert(0, %r); from pathlib import Path; "
            "from scripts.stat_batch_runner import acquire_output_lock; "
            "sys.exit(0 if acquire_output_lock(Path(%r)) else 3)") % (str(ROOT), str(tmp_path))
    other = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT))
    assert other.returncode == 3                                   # refused while this one holds it


# ---- the run's files reach W&B ----------------------------------------------------------------------

def test_run_files_and_the_log_are_uploaded(monkeypatch, tmp_path):
    import scripts.stat_batch_runner as sbr

    link = tmp_path / "repeat_000" / "mdo_f25_sequential_staged_pipeline"
    link.mkdir(parents=True)
    for name in ("knowledge_base.jsonl", "design.json", "result.json", "trace.json"):
        (link / name).write_text("{}\n")
    (link / "step_log_attempt1.jsonl").write_text(json.dumps(
        {"seq": 1, "kind": "step", "agent": "geometry_engineer", "agent_step": 1, "generations": [],
         "tool_calls": [], "observations": ""}) + "\n")
    (tmp_path / "chain_anchor.json").write_text("{}")
    log = tmp_path / "run.log"
    log.write_text("console\n")
    monkeypatch.setenv("SWEEP_LOG_PATH", str(log))
    saved = []
    monkeypatch.setattr(sbr.wandb, "save", lambda path, base_path=None, policy=None: saved.append(path))
    sbr.upload_run_files(object(), tmp_path)
    names = {Path(p).name for p in saved}
    assert {"knowledge_base.jsonl", "design.json", "result.json", "trace.json", "chain_anchor.json",
            "step_log_attempt1.jsonl", "step_log_attempt1.md", "run.log"} <= names


# ---- verbatim prompt assembly: the model sees exactly her prompts -----------------------------------

def test_the_agents_yaml_switches_on_verbatim():
    d = yaml.safe_load((ROOT / "config/mdo_f25_sequential_agents.yaml").read_text())
    assert d["stage_defaults"]["prompt_assembly"] == "verbatim"


def test_the_model_sees_exactly_the_system_prompt_and_the_stage_prompt():
    from smolagents.models import ChatMessage, MessageRole, Model

    from src.coordination import prompt_assembly

    seen = {}

    class Capture(Model):
        def __init__(self):
            super().__init__(model_id="capture")

        def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kw):
            seen["messages"] = messages
            seen["tools"] = [t.name for t in (tools_to_call_from or [])]
            from smolagents.models import ChatMessageToolCall, ChatMessageToolCallFunction
            return ChatMessage(role=MessageRole.ASSISTANT, content="", tool_calls=[ChatMessageToolCall(
                id="c1", type="function", function=ChatMessageToolCallFunction(
                    name="final_answer", arguments={"answer": "done"}))])

    p = _her_prompts()
    agent = prompt_assembly.make_agent_class()(
        tools=[], model=Capture(), name="geometry_engineer", description=p["geometry_engineer"],
        instructions=p["BASE"], prompt_templates=prompt_assembly.verbatim_prompt_templates(),
        max_steps=2, add_base_tools=False)
    task = prompt_assembly.verbatim_stage_input(agent.description, [])
    agent.run(task)
    msgs = seen["messages"]
    text = lambda m: m.content if isinstance(m.content, str) else "".join(c.get("text", "") for c in m.content)
    assert [str(getattr(m.role, "value", m.role)) for m in msgs] == ["system", "user"]
    assert text(msgs[0]).strip() == p["BASE"].strip()                   # the system prompt, nothing else
    assert text(msgs[1]) == p["geometry_engineer"]                       # the stage prompt, nothing else
    assert "final_answer" in seen["tools"]                               # tools still reach the model


def test_a_later_stage_gets_its_prompt_then_the_previous_design_state():
    from src.coordination import prompt_assembly

    p = _her_prompts()
    out = prompt_assembly.verbatim_stage_input(p["aerodynamics_analyst"],
                                               [("geometry_engineer", "DESIGN_STATE:\n  GEOMETRY_SET: true", None)])
    assert out.startswith(p["aerodynamics_analyst"])
    assert out.endswith("DESIGN_STATE from the previous agent (geometry_engineer):\nDESIGN_STATE:\n  GEOMETRY_SET: true")


def test_framework_mode_is_unchanged_by_default():
    from src.coordination import prompt_assembly

    prompt_assembly.set_mode(None)
    assert not prompt_assembly.is_verbatim()


# ---- a new iteration inherits the previous one (verbatim carry-over, 2026-10-05) ---------------------

def _runner():
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    import stat_batch_runner

    return stat_batch_runner


START = {"Aircraft.Wing.AREA": 122.3256, "Aircraft.Wing.SWEEP": 21.65, "_seed": 42}


def test_the_first_link_has_nothing_to_inherit(tmp_path):
    r = _runner()
    assert r.write_carry_over(tmp_path, 0, None, None, START) is None
    assert not (tmp_path / "carry_over.json").exists()


def test_a_later_link_inherits_the_integrator_and_the_design(tmp_path, monkeypatch):
    from src.coordination import prompt_assembly
    from src.tools import knowledge_base

    r = _runner()
    morph = str(tmp_path / "link0_morph.xml")
    space = {"geometry": {"Wing1": {"area_m2": 122.69, "aspect_ratio": 9.519, "sweep_set": 25.0,
                                    "sweep_quarter_chord_deg": 21.97},
                          "Fuselage1": {"length_m": 37.684, "diameter_m": 4.149}},
             "engine": {"splitter.BPR": 6.5, "fan.PR": 1.6}, "mission": {"Aircraft.Engine.SCALE_FACTOR": 1.1}}
    r.write_carry_over(tmp_path, 1, "DESIGN_STATE:\n  VERDICT: CONTINUE\n  RECOMMENDATION_TO: geometry_engineer",
                       morph, START, space)
    monkeypatch.setattr(knowledge_base, "_run_config", {"path": str(tmp_path / "knowledge_base.jsonl")})
    carry = prompt_assembly.read_carry_over()
    p = _her_prompts()
    first = prompt_assembly.verbatim_stage_input(p["geometry_engineer"], [], carry)
    assert "DESIGN_STATE from the previous iteration (mdo_integrator):\nDESIGN_STATE:\n  VERDICT: CONTINUE" in first
    assert "STARTING DESIGN for this iteration (the previous iteration's design, from its design.json)" in first
    assert "  Wing1: area_m2 122.69, aspect_ratio 9.519, sweep_set 25, sweep_quarter_chord_deg 21.97" in first
    assert "  Fuselage1: length_m 37.684, diameter_m 4.149" in first
    assert "set them again to keep them): splitter.BPR 6.5, fan.PR 1.6" in first
    assert "  Mission: Aircraft.Engine.SCALE_FACTOR 1.1" in first
    # The aviary chain values are the framework's business now, not the prompt's (2026-10-08).
    assert "Aircraft.Wing.SWEEP" not in first and "_seed" not in first
    assert f"Use CPACS file at {morph}" in first and "D150_simple.xml" not in first
    # Later stages: the carried CPACS and the recommendation (B172), but not the previous iteration's assessment.
    later = prompt_assembly.verbatim_stage_input(p["structures_analyst"], [("aerodynamics_analyst", "X", None)], carry)
    assert morph in later and "DESIGN_STATE from the previous iteration" not in later and "VERDICT" not in later
    assert "RECOMMENDATION_TO: geometry_engineer" in later


def test_without_a_morph_the_prompt_keeps_the_baseline_path(tmp_path):
    from src.coordination import prompt_assembly

    r = _runner()
    r.write_carry_over(tmp_path, 1, "VERDICT: RETRY", None, START)
    import json as _json

    carry = _json.loads((tmp_path / "carry_over.json").read_text())
    p = _her_prompts()
    out = prompt_assembly.verbatim_stage_input(p["geometry_engineer"], [], carry)
    assert out.startswith(p["geometry_engineer"])


# ---- the previous iteration's recommendation reaches the stage it names (2026-10-05) ----------------

LINES_FORMAT = ("DESIGN_STATE:\nITERATION: N/A\nRECOMMENDATION_TO: mission_architect\n"
                "RECOMMENDED_CHANGE: Use set_aircraft_parameters with AREA=120.26 to align mission with geometry\n"
                "MDO_REASONING: The simulation failed because mission parameters were not synchronized.\nVERDICT: RETRY")
JSON_FORMAT = ('{"DESIGN_STATE": {"ITERATION": 1, "RECOMMENDATION_TO": "propulsion_analyst", '
               '"RECOMMENDED_CHANGE": "Raise splitter.BPR to 7", "MDO_REASONING": "Fuel burn is 3726 kg over.", '
               '"VERDICT": "CONTINUE"}}\nTASK_COMPLETE')


def test_both_integrator_formats_parse():
    from src.coordination.prompt_assembly import parse_recommendation

    a = parse_recommendation(LINES_FORMAT)
    assert a["RECOMMENDATION_TO"] == "mission_architect" and a["RECOMMENDED_CHANGE"].startswith("Use set_aircraft")
    b = parse_recommendation(JSON_FORMAT)
    assert b == {"RECOMMENDATION_TO": "propulsion_analyst", "RECOMMENDED_CHANGE": "Raise splitter.BPR to 7",
                 "MDO_REASONING": "Fuel burn is 3726 kg over."}
    assert parse_recommendation("no recommendation here") == {}


def test_every_stage_gets_just_the_recommendation():
    """B172 (Jessica, 2026-10-10): all stages see RECOMMENDATION_TO / RECOMMENDED_CHANGE (not the reasoning)
    and any of them may respond. Role-name routing had sent an aero recommendation to geometry only."""
    from src.coordination import prompt_assembly

    p = _her_prompts()
    carry = {"previous_integrator_design_state": JSON_FORMAT, "starting_design": {}}
    prev = [("structures_analyst", "DESIGN_STATE:\n  OEM_KG: 47164", None)]
    block = ("RECOMMENDATION from the previous iteration (mdo_integrator):\n"
             "  RECOMMENDATION_TO: propulsion_analyst\n  RECOMMENDED_CHANGE: Raise splitter.BPR to 7\n"
             "This recommendation is addressed to the role named in RECOMMENDATION_TO, but any agent may act on "
             "it. If you address it, fully or in part, or find that it belongs to another role, add "
             "RECOMMENDATION_IMPLEMENTED: <how you addressed it> to your DESIGN_STATE.")
    for stage in ("propulsion_analyst", "mission_architect", "structures_analyst"):
        out = prompt_assembly.verbatim_stage_input(p[stage], prev, carry, stage)
        assert out.startswith(p[stage]) and out.endswith(block)
        assert "MDO_REASONING" not in out.split(p[stage], 1)[1]
    first = prompt_assembly.verbatim_stage_input(p["geometry_engineer"], [], carry, "geometry_engineer")
    assert first.endswith(block)
    # No carry-over (first link): nothing added.
    assert "RECOMMENDATION from" not in prompt_assembly.verbatim_stage_input(
        p["propulsion_analyst"], prev, None, "propulsion_analyst")


def test_responses_from_any_stage_accumulate():
    """An aero recommendation answered by geometry (the change) and by aero (it was a geometry change)."""
    from src.coordination import prompt_assembly

    p = _her_prompts()
    carry = {"previous_integrator_design_state": JSON_FORMAT, "starting_design": {}}
    prev = [("geometry_engineer", "DESIGN_STATE:\n  RECOMMENDATION_IMPLEMENTED: aspect ratio 9.36 -> 10.5\n", None),
            ("aerodynamics_analyst", '{"RECOMMENDATION_IMPLEMENTED": "geometry change, made by geometry; re-solved",', None),
            ("structures_analyst", "DESIGN_STATE:\n  OEM_KG: 47500", None)]
    out = prompt_assembly.verbatim_stage_input(p["mdo_integrator"], prev, carry, "mdo_integrator")
    assert out.endswith(
        "Responses so far:\n"
        "RECOMMENDATION_IMPLEMENTED (reported by geometry_engineer): aspect ratio 9.36 -> 10.5\n"
        "RECOMMENDATION_IMPLEMENTED (reported by aerodynamics_analyst): geometry change, made by geometry; re-solved\n"
        "Use these responses in your verdict and your next recommendation.")
    assert "Use these responses" not in prompt_assembly.verbatim_stage_input(p["structures_analyst"], prev, carry,
                                                                            "structures_analyst")


def test_responses_do_not_cross_into_the_next_link():
    """Link 2's responses answer link 1's recommendation; link 3 starts from link 2's recommendation only."""
    from src.coordination import prompt_assembly

    p = _her_prompts()
    link3_carry = {"previous_integrator_design_state": LINES_FORMAT, "starting_design": {}}
    first = prompt_assembly.verbatim_stage_input(p["geometry_engineer"], [], link3_carry, "geometry_engineer")
    assert "Responses so far" not in first and "RECOMMENDATION_TO: mission_architect" in first
    second = prompt_assembly.verbatim_stage_input(
        p["aerodynamics_analyst"], [("geometry_engineer", "DESIGN_STATE:\n  GEOMETRY_SET: true", None)],
        link3_carry, "aerodynamics_analyst")
    assert "Responses so far" not in second and "RECOMMENDATION_IMPLEMENTED (reported" not in second


def test_the_real_almost_json_from_v2b143_routes_to_aero():
    """v2b143 link 0's integrator wrote "CONSTRAINTS_PASSED": 0/2 (not valid JSON); the first parser
    returned {} and the aero stage of link 1 never received its recommendation."""
    import json as _json

    from src.coordination import prompt_assembly

    carry = _json.loads((ROOT / "tests/fixtures_carry_over_v2b143.json").read_text())
    rec = prompt_assembly.parse_recommendation(carry["previous_integrator_design_state"])
    assert rec["RECOMMENDATION_TO"] == "aerodynamics_analyst"
    assert rec["RECOMMENDED_CHANGE"].startswith("Decrease wing sweep angle from 27.33")
    p = _her_prompts()
    aero = prompt_assembly.verbatim_stage_input(p["aerodynamics_analyst"], [("geometry_engineer", "X", None)],
                                                carry, "aerodynamics_analyst")
    assert "RECOMMENDATION from the previous iteration (mdo_integrator):\n  RECOMMENDATION_TO: aerodynamics_analyst" in aero
    first = prompt_assembly.verbatim_stage_input(p["geometry_engineer"], [], carry, "geometry_engineer")
    assert "TASK_COMPLETE" not in first


# ---- design.json in every stage's context; the tools the prompts name exist (2026-10-08) ---------

def test_every_stage_gets_design_json_after_its_input(tmp_path, monkeypatch):
    from src.coordination import prompt_assembly
    from src.tools import knowledge_base

    (tmp_path / "design.json").write_text('{"design_space": {"engine": {"splitter.BPR": 6.5}}}')
    monkeypatch.setattr(knowledge_base, "_run_config", {"path": str(tmp_path / "knowledge_base.jsonl")})
    dj = prompt_assembly.read_design_json_text()
    p = _her_prompts()
    out = prompt_assembly.verbatim_stage_input(p["propulsion_analyst"], [("structures_analyst", "X", None)],
                                               None, "propulsion_analyst", dj)
    assert out.startswith(p["propulsion_analyst"])
    head, _, tail = out.partition("\n\ndesign.json (the framework's current record of this design")
    assert "DESIGN_STATE from the previous agent (structures_analyst):\nX" in head
    assert tail.endswith('{"design_space": {"engine": {"splitter.BPR": 6.5}}}')


def test_no_design_json_yet_adds_nothing(tmp_path, monkeypatch):
    from src.coordination import prompt_assembly
    from src.tools import knowledge_base

    monkeypatch.setattr(knowledge_base, "_run_config", {"path": str(tmp_path / "knowledge_base.jsonl")})
    assert prompt_assembly.read_design_json_text() is None
    p = _her_prompts()
    assert prompt_assembly.verbatim_stage_input(p["geometry_engineer"], [], None, "geometry_engineer",
                                                None) == p["geometry_engineer"]


def test_the_reference_tools_the_prompts_name_are_given_once():
    from src.coordination import prompt_assembly
    from src.tools.design_json import ReadDesignJson

    names = [t.name for t in prompt_assembly.with_reference_tools([ReadDesignJson()])]
    assert sorted(names) == ["get_design_state", "read_best_practices", "read_design_json"]


def test_the_runner_sets_aside_a_failed_attempts_design_json(tmp_path):
    r = _runner()
    (tmp_path / "design.json").write_text('{"design_space": {"geometry": {"Wing1": {"area_m2": 130.0}}}}')
    assert r.design_space_of(tmp_path) == {"geometry": {"Wing1": {"area_m2": 130.0}}}
    r.set_aside_design_json(tmp_path, 1)                    # first attempt: nothing to set aside
    assert (tmp_path / "design.json").exists()
    r.set_aside_design_json(tmp_path, 2)
    assert not (tmp_path / "design.json").exists() and (tmp_path / "design_attempt1.json").exists()
    assert r.design_space_of(tmp_path) is None


def test_integrator_text_is_read_from_a_failed_attempts_messages():
    from types import SimpleNamespace

    r = _runner()
    res = SimpleNamespace(messages=[{"content": "geometry done"},
                                    {"content": "DESIGN_STATE:\n  RECOMMENDATION_TO: aerodynamics_analyst\n  VERDICT: RETRY"},
                                    {"content": "TASK_COMPLETE"}])
    assert r.integrator_text(res).startswith("DESIGN_STATE:\n  RECOMMENDATION_TO: aerodynamics_analyst")
    assert r.integrator_text(SimpleNamespace(messages=[])) == ""


def test_a_response_inside_a_one_line_dict_answer_is_found():
    """B174: aipexws8 v2b173 link 2 -- the geometry engineer's response never reached the integrator. A final
    answer given as a dict reaches the next stage as one line, and the line-start pattern missed it."""
    from src.coordination import prompt_assembly

    p = _her_prompts()
    carry = {"previous_integrator_design_state": JSON_FORMAT, "starting_design": {}}
    prev = [("geometry_engineer", "{'GEOMETRY_SET': True, 'RECOMMENDATION_IMPLEMENTED': 'Increased aspect ratio "
             "9.36 -> 10.5 via set_high_level_parameters', 'MESH_ELEMENTS': 2400000}", None),
            ("aerodynamics_analyst", "- **RECOMMENDATION_IMPLEMENTED**: handed to geometry (AR is geometry)", None),
            ("structures_analyst", "Saw RECOMMENDATION_IMPLEMENTED (reported by geometry_engineer): Increased ...\n"
             "RECOMMENDATION_IMPLEMENTED: <how you addressed it>", None)]
    out = prompt_assembly.verbatim_stage_input(p["mdo_integrator"], prev, carry, "mdo_integrator")
    assert out.endswith(
        "Responses so far:\n"
        "RECOMMENDATION_IMPLEMENTED (reported by geometry_engineer): Increased aspect ratio 9.36 -> 10.5 via "
        "set_high_level_parameters\n"
        "RECOMMENDATION_IMPLEMENTED (reported by aerodynamics_analyst): handed to geometry (AR is geometry)\n"
        "Use these responses in your verdict and your next recommendation.")


def test_the_integrator_is_told_when_nobody_responded():
    from src.coordination import prompt_assembly

    p = _her_prompts()
    carry = {"previous_integrator_design_state": JSON_FORMAT, "starting_design": {}}
    out = prompt_assembly.verbatim_stage_input(
        p["mdo_integrator"], [("simulation_executor", "DESIGN_STATE:\n  CONVERGED: true", None)], carry, "mdo_integrator")
    assert out.endswith("No agent reported RECOMMENDATION_IMPLEMENTED in this iteration.")
    assert "Use these responses" not in out


# ---- B177: a markdown recommendation is read, and an unreadable one is passed on as written ----------

def test_the_real_markdown_recommendation_from_v2ifb2_reaches_propulsion():
    """aipexws8 v2ifb2 link 1's integrator wrote '- **Recommendation To:** propulsion_analyst' under a
    '**RECOMMENDED CHANGE:**' heading; the parser returned {} and no agent of link 2 got the recommendation."""
    import json as _json

    from src.coordination import prompt_assembly

    carry = _json.loads((ROOT / "tests/fixtures_carry_over_v2ifb2.json").read_text())
    rec = prompt_assembly.parse_recommendation(carry["previous_integrator_design_state"])
    assert rec["RECOMMENDATION_TO"] == "propulsion_analyst"
    assert rec["RECOMMENDED_CHANGE"] == "Increase bypass ratio (`splitter.BPR`) from 5.105 to 6.0"
    assert rec["MDO_REASONING"].startswith("Higher BPR improves SFC")
    p = _her_prompts()
    for stage in ("propulsion_analyst", "mission_architect"):
        out = prompt_assembly.verbatim_stage_input(p[stage], [("structures_analyst", "X", None)], carry, stage)
        assert ("RECOMMENDATION from the previous iteration (mdo_integrator):\n"
                "  RECOMMENDATION_TO: propulsion_analyst\n"
                "  RECOMMENDED_CHANGE: Increase bypass ratio (`splitter.BPR`) from 5.105 to 6.0\n"
                "This recommendation is addressed to the role named in RECOMMENDATION_TO") in out


def test_markdown_spellings_parse_and_exact_keys_still_win():
    from src.coordination.prompt_assembly import parse_recommendation

    md = ("### Verdict: CONTINUE\n**Recommendation_to**: aerodynamics_analyst\n"
          "* **Recommended change:** lower CFL to 10\n- Rationale: residuals oscillate")
    assert parse_recommendation(md) == {"RECOMMENDATION_TO": "aerodynamics_analyst",
                                        "RECOMMENDED_CHANGE": "lower CFL to 10",
                                        "MDO_REASONING": "residuals oscillate"}
    # A bare heading has no value; the parser moves on rather than returning "".
    assert "RECOMMENDED_CHANGE" not in parse_recommendation("**RECOMMENDED CHANGE:**\nRecommendation To: x")
    # The exact form is used when present, even next to a markdown spelling.
    both = "RECOMMENDATION_TO: geometry_engineer\n- **Recommendation To:** propulsion_analyst"
    assert parse_recommendation(both)["RECOMMENDATION_TO"] == "geometry_engineer"
    assert parse_recommendation(LINES_FORMAT)["RECOMMENDATION_TO"] == "mission_architect"


def test_an_unreadable_recommendation_is_passed_on_as_written(caplog):
    """v2b171 link 1's integrator wrote '4. **Recommendation**:' then '**Aerodynamics Analyst**: ...' with no
    target field; rather than vanish, every agent gets the assessment (the first one is pointed at it)."""
    import json as _json
    import logging

    from src.coordination import prompt_assembly

    carry = _json.loads((ROOT / "tests/fixtures_carry_over_v2b171_prose.json").read_text())
    text = carry["previous_integrator_design_state"]
    assert "RECOMMENDATION_TO" not in prompt_assembly.parse_recommendation(text)
    p = _her_prompts()
    with caplog.at_level(logging.WARNING, logger="src.coordination.prompt_assembly"):
        later = prompt_assembly.verbatim_stage_input(p["propulsion_analyst"], [("structures_analyst", "X", None)],
                                                     carry, "propulsion_analyst")
    assert "B177: no RECOMMENDATION_TO could be read" in caplog.text
    head = ("RECOMMENDATION from the previous iteration (mdo_integrator): it did not use the "
            "RECOMMENDATION_TO / RECOMMENDED_CHANGE fields, so its recommendation is given as written.\n"
            "The previous iteration's assessment:\n")
    assert head + text.replace("TASK_COMPLETE", "").strip() in later
    assert "This recommendation is addressed to the role it names, but any agent may act" in later
    first = prompt_assembly.verbatim_stage_input(p["geometry_engineer"], [], carry, "geometry_engineer")
    assert "It is in the DESIGN_STATE from the previous iteration (mdo_integrator) above." in first
    assert first.count("Validate Aviary's drag polar derivation") == 1      # not repeated for the first agent
    integ = prompt_assembly.verbatim_stage_input(p["mdo_integrator"], [("simulation_executor", "X", None)],
                                                 carry, "mdo_integrator")
    assert integ.endswith("No agent reported RECOMMENDATION_IMPLEMENTED in this iteration.")


def test_no_recommendation_text_means_no_block():
    from src.coordination import prompt_assembly

    p = _her_prompts()
    for integrator in ("VERDICT: RETRY", "", None):
        carry = {"previous_integrator_design_state": integrator, "starting_design": {}}
        out = prompt_assembly.verbatim_stage_input(p["structures_analyst"], [("x", "X", None)], carry,
                                                   "structures_analyst")
        assert "RECOMMENDATION from" not in out
