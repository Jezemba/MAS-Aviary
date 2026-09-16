"""B81: the knowledge base is wired through the runner and into every agent run.

The child entry point is called IN-PROCESS with a fake pipe and a stand-in for
run_combination, so the real configure_run / summary / export path runs without
spawning a process or loading a model. No server, no .env.
"""

import json
from types import SimpleNamespace as NS

import pytest
from smolagents import Tool

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.llm import generation_slots as gl
from src.tools import knowledge_base as kbm
from src.tools.agent_context import current_agent_name
from src.tools.type_coercion import wrap_tool_with_middleware


class _Pipe:
    def __init__(self):
        self.sent = []

    def send(self, obj):
        self.sent.append(obj)

    def close(self):
        pass


class _Stub:
    _avion_test_stub = True

    def generate(self, messages, **kw):
        from smolagents.models import ChatMessage

        return ChatMessage(role="assistant", content="LINK SUMMARY: mesh done by geometry_engineer")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {"generate_volume_mesh": "tigl"})
    gl.clear_summary_model()
    yield
    kbm.configure_run(None, None, None, None)
    gl.clear_summary_model()


def _mesh_tool():
    class T(Tool):
        name = "generate_volume_mesh"
        description = "fake"
        inputs = {"session_id": {"type": "string", "description": "s", "nullable": True}}
        output_type = "string"

        def forward(self, session_id=None):
            return json.dumps({"success": True, "mesh_base64": "U1U=" * 60})
    return wrap_tool_with_middleware(T())


def test_child_writes_the_run_kb_and_returns_summary_and_metrics(monkeypatch, tmp_path):
    import scripts.stat_batch_runner as sbr
    from src.runners import batch_runner

    gl.register_summary_model(_Stub())
    path = tmp_path / "repeat_001" / "mdo_f25_x" / "knowledge_base.jsonl"

    def fake_run_combination(combo, task, config, session_id=None):
        _mesh_tool().forward(session_id="g")
        return NS(status="success")

    monkeypatch.setattr(batch_runner, "run_combination", fake_run_combination)
    pipe = _Pipe()
    sbr._subprocess_target(pipe, NS(name="mdo_f25_x"), "task", None, "sid",
                           kb_context={"path": str(path), "chain_link": 1, "attempt": 2,
                                       "seed": {"summary": "link 0 made a mesh", "end_state": {"A": 1.0}}})
    kind, _result, state = pipe.sent[0]
    assert kind == "ok"
    assert state["_kb_link_summary"] == "LINK SUMMARY: mesh done by geometry_engineer"
    assert state["_kb_metrics"]["kb_entries"] == 2                     # link_start + the mesh
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert [l["tool"] for l in lines] == ["link_start", "generate_volume_mesh"]
    assert all(l["chain_link"] == 1 and l["attempt"] == 2 for l in lines)
    assert lines[0]["outputs"]["summary"] == "link 0 made a mesh"


def test_every_agent_run_gets_the_kb_summary_and_its_identity_once(monkeypatch):
    from src.runners.batch_runner import _install_trace_capture

    gl.register_summary_model(_Stub())
    kbm.get_kb().append(tool="generate_volume_mesh", server="tigl", agent="geometry_engineer",
                        role="", status="success", outputs={"mesh_base64": "generate_volume_mesh__mesh_base64"})
    seen = {}

    class Agent:
        description = "aero analyst"
        memory = NS(get_full_steps=lambda: [])

        def run(self, task, **kw):
            seen["task"], seen["who"] = task, current_agent_name()
            return "done"

    agent = Agent()
    coord = NS(agents={"aerodynamics_analyst": agent})
    _install_trace_capture(coord)
    coord._wrap_agent_for_trace("mission_architect_alias", agent)     # a graph alias wraps it again
    agent.run("Run SU2 on the mesh")
    assert seen["task"].count(kbm.HANDOFF_MARKER) == 1
    assert "LINK SUMMARY: mesh done by geometry_engineer" in seen["task"]
    assert seen["task"].endswith("Run SU2 on the mesh")
    assert seen["who"] == "aerodynamics_analyst"                       # innermost scope: the real name


def test_an_empty_knowledge_base_leaves_the_task_unchanged():
    from src.runners.batch_runner import _install_trace_capture

    seen = {}

    class Agent:
        memory = NS(get_full_steps=lambda: [])

        def run(self, task, **kw):
            seen["task"] = task

    agent = Agent()
    coord = NS(agents={"geometry_engineer": agent})
    _install_trace_capture(coord)
    agent.run("Open the CPACS")
    assert seen["task"] == "Open the CPACS"
