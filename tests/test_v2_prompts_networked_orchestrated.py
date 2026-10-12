"""v2 prompts for the networked and orchestrated combinations (Jessica, 2026-10-10).

The model receives exactly the system message in networked.md / orchestrated.md and, as each message, data
only: the DESIGN_STATE (with the open sessions), the TODO board, a peer's own recent work, design.json. An
orchestrated agent may message the orchestrator; a second pycycle session is refused while one is open.
No MCP server, GPU or API key is needed: the models below are scripted.
"""

import json
import time
from pathlib import Path

import pytest
from smolagents import Tool
from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, MessageRole, Model

import src.tools.data_plane as dp
from src.config.loader import load_yaml
from src.coordination import prompt_assembly
from src.coordination.design_state import DesignState
from src.coordination.history import AgentMessage

ROOT = Path(__file__).resolve().parents[1]


def _text(m):
    return m.content if isinstance(m.content, str) else "".join(c.get("text", "") for c in m.content)


def _roles(msgs):
    return [str(getattr(m.role, "value", m.role)) for m in msgs]


def _call(name, args, i=[0]):
    i[0] += 1
    return ChatMessage(role=MessageRole.ASSISTANT, content="", tool_calls=[ChatMessageToolCall(
        id=f"c{i[0]}", type="function", function=ChatMessageToolCallFunction(name=name, arguments=args))])


class Script(Model):
    """Answers from a per-agent queue chosen by the system message; records every call."""

    def __init__(self, pick):
        super().__init__(model_id="script")
        self.pick, self.calls = pick, []

    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kw):
        who = self.pick(_text(messages[0]))
        self.calls.append((who, list(messages), [t.name for t in (tools_to_call_from or [])]))
        return who_queue[who].pop(0) if who_queue.get(who) else _call("final_answer", {"answer": "done"})


who_queue: dict = {}


def fake_tool(name):
    cls = type(f"T_{name}", (Tool,), {
        "name": name, "description": name, "inputs": {}, "output_type": "string",
        "forward": lambda self: json.dumps({"success": True})})
    return cls()


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    who_queue.clear()
    monkeypatch.setattr(dp, "_design_state", DesignState())
    yield
    prompt_assembly.set_mode(None)


# ---- the sources ---------------------------------------------------------------------------------------

SECTIONS = {"networked.md": ["networked_iterative_feedback", "networked_graph_routed"],
            "orchestrated.md": ["orchestrated_staged_pipeline", "orchestrated_iterative_feedback",
                                "orchestrated_graph_routed"]}


def test_the_sources_are_ascii_and_have_every_section():
    for source, sections in SECTIONS.items():
        assert all(ord(c) < 128 for c in (ROOT / source).read_text()), source
        for sec in sections:
            assert prompt_assembly.system_text(source, sec).startswith("SYSTEM PROMPT:")


def test_the_yamls_switch_on_verbatim():
    assert load_yaml("config/mdo_f25_networked_agents.yaml")["peer_template"]["prompt_assembly"] == "verbatim"
    assert load_yaml("config/mdo_f25_orchestrated_agents.yaml")["prompt_assembly"] == "verbatim"


def test_the_propulsion_role_reuses_an_open_pycycle_session():
    for source, sections in SECTIONS.items():
        for sec in sections:
            t = prompt_assembly.system_text(source, sec)
            assert "If the DESIGN_STATE lists an open pycycle session, use it" in t
            assert "Create a new pycycle session" not in t


def test_the_link0_design_state_is_the_prompt_table_baseline_with_sessions():
    dp._design_state.sessions["aviary"] = "av-1"
    text = prompt_assembly.starting_design_state(prompt_assembly.system_text("networked.md",
                                                                             "networked_iterative_feedback"))
    assert text.startswith("DESIGN_STATE (the starting design for this iteration):")
    assert "Wing1: area 122.78, aspect_ratio 9.37, sweep 27.33" in text
    assert "Fuselage1: length 37.68, diameter 4.15" in text
    assert "splitter.BPR 5.105" in text
    assert "SCALE_FACTOR" not in text          # B170: the framework sets it from propulsion; not a starting value
    assert text.endswith("SESSIONS (already open -- reuse them):\n    aviary: av-1")


# ---- networked -----------------------------------------------------------------------------------------

def _networked(graph: bool):
    from src.coordination.strategies.networked import NetworkedStrategy

    model = Script(lambda system: "peer")
    cfg = load_yaml("config/aviary_mdo_f25_networked.yaml")
    cfg["peer_template"] = load_yaml("config/mdo_f25_networked_agents.yaml")["peer_template"]
    cfg["_worker_tools"], cfg["_model"] = {}, model
    if graph:
        from src.coordination.graph_routed_handler import GraphRoutedHandler

        cfg["networked"].update({"workflow_phases": [], "selection_mode": "concurrent_blackboard"})
        cfg["_graph_def"] = GraphRoutedHandler({"predefined_graph": "mdo_f25"})._load_graph({})
    s = NetworkedStrategy()
    s.initialize({}, cfg)
    s.set_session_id("av-1")
    return s, model


@pytest.mark.parametrize("graph", [False, True])
def test_a_peer_sees_exactly_its_system_message_then_data(graph):
    dp._design_state.sessions["aviary"] = "av-1"
    s, model = _networked(graph)
    a = s.next_step([], {"task": "RUNNER TASK TEXT"})
    assert a.action_type == "parallel_run" and a.input_context == ""
    peer = a.metadata["peers"][0]
    s._agents[peer].run(a.metadata["inputs"][peer])
    _, msgs, tools = model.calls[0]
    sec = "networked_graph_routed" if graph else "networked_iterative_feedback"
    assert _roles(msgs) == ["system", "user"]
    assert _text(msgs[0]) == prompt_assembly.system_text("networked.md", sec)
    user = _text(msgs[1])
    assert user.startswith("DESIGN_STATE (the starting design for this iteration):")
    assert "TODO BOARD:" in user and "aviary: av-1" in user
    assert ("[BLOCKED on GEOMETRY_SETUP]" in user) == graph
    assert "RUNNER" not in user and "RUNNER" not in json.dumps([e.value for e in s._blackboard.read_all()])
    assert {"claim_todo", "wait_for_board", "read_design_json", "read_best_practices", "get_design_state"} <= set(tools)


def test_a_peers_next_cycle_opens_with_a_short_recap_of_its_own_work(monkeypatch, tmp_path):
    from src.tools import knowledge_base as kbm

    kbm.configure_run(path=str(tmp_path / "knowledge_base.jsonl"), chain_link=0, attempt=1, seed=None)
    try:
        s, model = _networked(False)
        kb = kbm.get_kb()
        for tool in ("open_cpacs", "generate_volume_mesh"):
            kb.append(tool=tool, server="tigl", agent="agent_1", role="", status="success", outputs={})
        kb.append(tool="run_cycle", server="pycycle", agent="agent_2", role="", status="success", outputs={})
        s._blackboard.claim_todo("geometry", "agent_1")
        s._blackboard.complete_todo("geometry", "agent_1", "DESIGN_STATE:\n  GEOMETRY_SET: true")
        msg = prompt_assembly.peer_message("agent_1", s._system_text, s._blackboard, False)
        assert msg.startswith('YOUR WORK SO FAR (agent_1, from the knowledge base; '
                              'read_design_knowledge(agent="agent_1", mode="full") has the details):')
        recap = msg.split("\n\n")[0]
        assert "done geometry" in recap and "generate_volume_mesh success" in recap and "run_cycle" not in recap
        assert "DESIGN_STATE posted on the board by geometry (agent_1):\nDESIGN_STATE:\n  GEOMETRY_SET: true" in msg
        assert len(recap.splitlines()) <= prompt_assembly.RECAP_LINES + 2
    finally:
        kbm.configure_run(None, None, None, None)


def test_a_spawned_peer_gets_the_same_system_message():
    s, model = _networked(False)
    spawn = s._agents["agent_1"].tools["spawn_peer"]
    new = json.loads(spawn.forward("more hands"))["new_agent_name"]
    s._agents[new].run("DATA")
    assert _text(model.calls[-1][1][0]) == s._system_text


def test_read_todos_shows_a_posted_design_state_whole():
    s, _ = _networked(False)
    s._blackboard.claim_todo("aero", "agent_2")
    s._blackboard.complete_todo("aero", "agent_2", "X" * 500)
    assert "X" * 500 in s._agents["agent_1"].tools["read_todos"].forward()


# ---- orchestrated --------------------------------------------------------------------------------------

def _orchestrated(lifecycle="setup_only", graph_roles=None):
    from src.agents.agent_factory import create_agent
    from src.coordination.strategies.orchestrated import OrchestratedStrategy

    model = Script(lambda system: "orchestrator" if system.startswith("SYSTEM PROMPT: MDO ORCHESTRATOR")
                   else "worker")
    orch = create_agent(name="orchestrator", role="r", system_prompt="See orchestrated.md.", tools=[],
                        model=model, max_steps=6)
    cfg = {"orchestrated": {"lifecycle_mode": lifecycle, "max_orchestrator_turns": 3}, "termination": {},
           "_worker_tools": {n: fake_tool(n) for n in ("open_cpacs", "generate_volume_mesh", "set_high_level_parameters",
                                                       "run_su2_solver")},
           "_prompt_assembly": "verbatim", "_prompt_source": "orchestrated.md"}
    if graph_roles:
        cfg["_graph_roles"] = graph_roles
    if lifecycle == "per_stage":
        cfg["_pipeline_stage_names"] = ["geometry_engineer", "aerodynamics_analyst"]
    s = OrchestratedStrategy()
    s.initialize({"orchestrator": orch}, cfg)
    return s, model, orch


@pytest.mark.parametrize("lifecycle,graph,section", [
    ("setup_only", None, "orchestrated_iterative_feedback"),
    ("setup_only", ["geometry_engineer"], "orchestrated_graph_routed"),
    ("per_stage", None, "orchestrated_staged_pipeline")])
def test_the_orchestrator_sees_exactly_its_system_message_then_data(lifecycle, graph, section):
    s, model, orch = _orchestrated(lifecycle, graph)
    a = s.next_step([], {"task": "RUNNER TASK TEXT"})
    orch.run(a.input_context)
    _, msgs, tools = model.calls[0]
    assert _roles(msgs) == ["system", "user"]
    assert _text(msgs[0]) == prompt_assembly.system_text("orchestrated.md", section)
    user = _text(msgs[1])
    assert "RUNNER" not in user
    if lifecycle == "per_stage":
        assert user.startswith("Stage 1 of 2: geometry_engineer\n\nDESIGN_STATE (the starting design")
    else:
        assert user.startswith("DESIGN_STATE (the starting design for this iteration):")
    assert {"create_agent", "assign_task", "give_tools", "reply_to_agent"} <= set(tools)


def _build_team(s, orch):
    who_queue["orchestrator"] = [
        _call("create_agent", {"name": "geometry_engineer", "persona": "PERSONA TEXT", "tools": ["open_cpacs"]}),
        _call("assign_task", {"agent_name": "geometry_engineer", "task": "TASK TEXT"}),
        _call("final_answer", {"answer": "DELEGATION_COMPLETE"})]
    a = s.next_step([], {"task": "RUNNER"})
    out = orch.run(a.input_context)
    return [AgentMessage(agent_name="orchestrator", content=str(out), turn_number=1, timestamp=time.time())]


def test_an_agent_sees_only_the_persona_then_its_task_and_the_design_state():
    s, model, orch = _orchestrated()
    history = _build_team(s, orch)
    a = s.next_step(history, {"task": "RUNNER"})
    assert a.agent_name == "geometry_engineer"
    assert a.input_context.startswith("TASK TEXT\n\nDESIGN_STATE (the starting design for this iteration):")
    worker = s._agents["geometry_engineer"]
    worker.run(a.input_context)
    _, msgs, tools = [c for c in model.calls if c[0] == "worker"][0]
    assert _roles(msgs) == ["system", "user"] and _text(msgs[0]) == "PERSONA TEXT"
    assert _text(msgs[1]) == a.input_context
    assert {"open_cpacs", "message_orchestrator", "read_design_json"} <= set(tools)


def test_a_later_agent_gets_the_previous_agents_design_state():
    out = prompt_assembly.worker_message("TASK", "", ("geometry_engineer", "DESIGN_STATE: G"))
    assert out.startswith("TASK\n\nDESIGN_STATE from the agent that worked before you (geometry_engineer):\n"
                          "DESIGN_STATE: G")


def test_an_agent_messages_the_orchestrator_which_grants_a_tool_and_replies():
    s, model, orch = _orchestrated()
    history = _build_team(s, orch)
    a = s.next_step(history, {"task": "RUNNER"})
    who_queue["worker"] = [_call("message_orchestrator", {"message": "I need run_su2_solver"}),
                           _call("final_answer", {"answer": "done"})]
    who_queue["orchestrator"] = [
        _call("give_tools", {"agent_name": "geometry_engineer", "tools": ["run_su2_solver"]}),
        _call("reply_to_agent", {"agent_name": "geometry_engineer", "message": "granted"}),
        _call("final_answer", {"answer": "ok"})]
    worker = s._agents["geometry_engineer"]
    worker.run(a.input_context)
    orch_calls = [c for c in model.calls if c[0] == "orchestrator"]
    orch_turn = orch_calls[-2]                                    # give_tools, then reply_to_agent: the reply ends it
    assert _text(orch_turn[1][-1]) == "MESSAGE from geometry_engineer:\nI need run_su2_solver"
    assert len(orch_turn[1]) > 2                                  # it kept its own earlier turns
    assert len(who_queue["orchestrator"]) == 1                    # the queued final_answer was never needed
    worker_second = [c for c in model.calls if c[0] == "worker"][1]
    assert "granted" in json.dumps([_text(m) for m in worker_second[1]])
    assert "run_su2_solver" in worker_second[2]                   # usable from its next step
    assert not s._context.in_message_turn


# ---- handlers ------------------------------------------------------------------------------------------

def test_the_iterative_handler_passes_the_message_through_plus_its_retry_text():
    from src.coordination.iterative_feedback_handler import IterativeFeedbackHandler

    h = IterativeFeedbackHandler({"worker_input": "as_given"})
    assert h._build_context("MSG", "prev", 0, 20, [], None) == "MSG"


def test_the_graph_handler_skips_classification_and_sends_the_assigned_task():
    from src.coordination.graph_routed_handler import GraphRoutedHandler

    h = GraphRoutedHandler({"predefined_graph": "mdo_f25", "worker_input": "as_given"})
    g = h._load_graph({})
    h._state_dict = {"last_agent_output": "", "last_error": ""}
    h._resource_mgr = None
    assert h._past_classification(g, g.initial_state) == "GEOMETRY_SETUP"
    agent = type("A", (), {"name": "geometry_engineer"})()
    ctx = h._as_given_context(g.states["GEOMETRY_SETUP"], agent,
                              {"assigned_tasks": {"geometry_engineer": "TASK TEXT"}, "system_text": ""})
    assert ctx.startswith("TASK TEXT\n\nDESIGN_STATE (the starting design")


def test_framework_mode_is_reset_when_a_combination_does_not_ask_for_verbatim():
    from src.coordination.strategies.networked import NetworkedStrategy

    prompt_assembly.set_mode("verbatim")
    NetworkedStrategy().initialize({}, {"peer_template": {}, "_worker_tools": {}, "_model": Script(lambda s: "p")})
    assert not prompt_assembly.is_verbatim()


# ---- pycycle sessions ----------------------------------------------------------------------------------

def test_a_second_pycycle_session_is_refused_while_one_is_open():
    assert dp.pycycle_session_refusal("create_cycle_model", {}) is None
    dp._design_state.sessions["pycycle"] = "py-1"
    refused = dp.pycycle_session_refusal("create_cycle_model", {})
    assert refused["error_code"] == "SESSION_ALREADY_OPEN" and "py-1" in refused["error"]
    dp.note_session_closed("close_cycle_model", {"session_id": "py-1"}, json.dumps({"success": True}))
    assert dp.pycycle_session_refusal("create_cycle_model", {}) is None
    assert dp.pycycle_session_refusal("open_cpacs", {}) is None


# ---- recommendations across links (Jessica, 2026-10-10) ---------------------------------------------

def test_every_orchestrator_prompt_routes_the_recommendation():
    for sec in SECTIONS["orchestrated.md"]:
        t = prompt_assembly.system_text("orchestrated.md", sec)
        assert "RECOMMENDATIONS\nYour mdo_integrator ends each iteration" in t
        assert "report to you with message_orchestrator how it implemented the recommendation" in t


def test_the_next_link_carries_the_mdo_integrators_text_not_another_agents():
    from types import SimpleNamespace

    import scripts.stat_batch_runner as r
    msgs = [{"agent_name": "mdo_integrator", "content": "DESIGN_STATE:\n  RECOMMENDATION_TO: aerodynamics_analyst"},
            {"agent_name": "orchestrator", "content": "DELEGATION_COMPLETE"},
            {"agent_name": "geometry_engineer", "content": "DESIGN_STATE:\n  GEOMETRY_SET: true"}]
    assert r.integrator_text(SimpleNamespace(messages=msgs)).startswith("DESIGN_STATE:\n  RECOMMENDATION_TO")


def test_the_orchestrators_first_message_on_link_2_carries_the_recommendation(tmp_path):
    from src.tools import knowledge_base as kbm

    (tmp_path / "carry_over.json").write_text(json.dumps({
        "link": 1, "baseline_cpacs": "/x/mass-mcp/tests/fixtures/D150_simple.xml",
        "previous_integrator_design_state": "DESIGN_STATE:\n  RECOMMENDATION_TO: aerodynamics_analyst\n"
                                            "  RECOMMENDED_CHANGE: Wing1 aspect_ratio -> 10.5\nTASK_COMPLETE",
        "starting_design": {"CPACS_FILE": "/runs/link0/geometry_end.xml", "geometry": {"Wing1": {"area_m2": 122.8}}}}))
    kbm.configure_run(path=str(tmp_path / "knowledge_base.jsonl"), chain_link=1, attempt=1, seed=None)
    try:
        s, model, orch = _orchestrated()
        a = s.next_step([], {"task": "RUNNER"})
        assert a.input_context.startswith("DESIGN_STATE from the previous iteration (mdo_integrator):\nDESIGN_STATE:\n"
                                          "  RECOMMENDATION_TO: aerodynamics_analyst\n"
                                          "  RECOMMENDED_CHANGE: Wing1 aspect_ratio -> 10.5")
        assert "TASK_COMPLETE" not in a.input_context
        assert "CPACS_FILE: /runs/link0/geometry_end.xml" in a.input_context
        assert "/runs/link0/geometry_end.xml" in s._system_text      # the prompts name the carried CPACS
    finally:
        kbm.configure_run(None, None, None, None)


# ---- message turns end (v2no6, 2026-10-11) ----------------------------------------------------------

def test_a_reply_to_an_agent_that_is_not_waiting_is_refused_and_the_turn_goes_on():
    s, model, orch = _orchestrated()
    history = _build_team(s, orch)
    a = s.next_step(history, {"task": "RUNNER"})
    s._context.agents["propulsion_analyst"] = s._context.agents["geometry_engineer"]
    who_queue["worker"] = [_call("message_orchestrator", {"message": "need the engine changed"}),
                           _call("final_answer", {"answer": "done"})]
    who_queue["orchestrator"] = [
        _call("reply_to_agent", {"agent_name": "propulsion_analyst", "message": "change BPR"}),
        _call("reply_to_agent", {"agent_name": "geometry_engineer", "message": "assigned to propulsion"})]
    s._agents["geometry_engineer"].run(a.input_context)
    orch_calls = [c for c in model.calls if c[0] == "orchestrator"]
    refused = _text(orch_calls[-1][1][-1])
    assert "Only geometry_engineer is waiting for a reply" in refused   # the refusal reached the orchestrator
    worker_second = [c for c in model.calls if c[0] == "worker"][1]
    assert "assigned to propulsion" in json.dumps([_text(m) for m in worker_second[1]])
    assert s._context.message_sender is None and not s._context.in_message_turn


def test_a_message_turn_that_never_replies_stops_after_a_few_steps():
    from src.coordination.strategies.orchestrated import MESSAGE_TURN_STEPS

    s, model, orch = _orchestrated()
    history = _build_team(s, orch)
    a = s.next_step(history, {"task": "RUNNER"})
    who_queue["worker"] = [_call("message_orchestrator", {"message": "help"}), _call("final_answer", {"answer": "x"})]
    who_queue["orchestrator"] = [_call("list_available_tools", {}) for _ in range(30)]
    before = len([c for c in model.calls if c[0] == "orchestrator"])
    s._agents["geometry_engineer"].run(a.input_context)
    used = len([c for c in model.calls if c[0] == "orchestrator"]) - before
    assert used <= MESSAGE_TURN_STEPS + 1                               # + smolagents' final summary call
    worker = [c for c in model.calls if c[0] == "worker" and _text(c[1][0]) == "PERSONA TEXT"]
    assert "The orchestrator did not reply." in json.dumps([_text(m) for m in worker[1][1]])
