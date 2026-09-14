"""B31: an agent that can generate the volume mesh may not finish without one.

Observed 2026-08-12 and again 2026-09-14 (7960 Stage A run 1): the geometry stage
finished with MESH_BASE64 'exported' having never called generate_volume_mesh,
every one of its tool calls succeeded, and the stage was accepted.
"""

import ast
import json
import socket
from pathlib import Path

import pytest
from smolagents import Tool, ToolCallingAgent
from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, Model

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import artifact_checks as ac

MAS = Path(__file__).resolve().parents[1]
BIG_MESH = "U1UyIE1FU0g=" * 50  # >100 chars, base64-looking


@pytest.fixture(autouse=True)
def fresh_plane(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", {"generate_volume_mesh": "tigl", "open_cpacs": "tigl"})
    yield


class _Agent:
    def __init__(self, tools, name="geometry_engineer"):
        self.tools = {t: object() for t in tools}
        self.name = name


def _memory(*called):
    """Minimal smolagents-shaped memory: steps with tool_calls carrying names."""
    from types import SimpleNamespace as NS
    return NS(steps=[NS(tool_calls=[NS(name=n) for n in called])])


GEO = _memory("open_cpacs", "get_wing_summary", "set_high_level_parameters")


def test_agent_without_the_mesh_tool_is_never_blocked():
    assert ac.mesh_before_finish("done", GEO, agent=_Agent(["open_cpacs", "run_su2_solver"])) is True


def test_holding_the_tool_without_doing_geometry_is_never_blocked():
    """A networked peer holds every tool; one doing mass or aero work must be free to finish."""
    peer = _Agent(["open_cpacs", "generate_volume_mesh", "estimate_mass", "run_su2_solver"], name="peer_2")
    assert ac.mesh_before_finish("done", _memory("estimate_mass", "get_wing_summary"), agent=peer) is True
    assert ac.mesh_before_finish("done", _memory(), agent=peer) is True
    assert "final_answer_refused_no_mesh" not in dp._design_state.data_store


def test_agent_with_the_tool_and_no_mesh_is_refused_with_actionable_message():
    agent = _Agent(["open_cpacs", "generate_volume_mesh"])
    with pytest.raises(ac.MissingVolumeMesh) as e:
        ac.mesh_before_finish("MESH_BASE64: 'exported'", GEO, agent=agent)
    msg = str(e.value)
    assert "generate_volume_mesh" in msg and "refusal 1 of 3" in msg and "omit" in msg
    assert "open_cpacs" in msg and "set_high_level_parameters" in msg
    assert dp._design_state.data_store["final_answer_refused_no_mesh"] == 1


def test_a_claimed_or_attempted_mesh_is_not_enough_only_a_captured_payload():
    agent = _Agent(["generate_volume_mesh"])
    dp._design_state.data_store["generate_volume_mesh__mesh_base64"] = "exported"
    with pytest.raises(ac.MissingVolumeMesh):
        ac.mesh_before_finish("done", GEO, agent=agent)
    dp._design_state.data_store["generate_volume_mesh__mesh_base64"] = BIG_MESH
    assert ac.mesh_before_finish("done", GEO, agent=agent) is True


def test_refusals_are_bounded_and_the_meshless_finish_is_recorded():
    agent = _Agent(["generate_volume_mesh"], name="peer_3")
    for _ in range(ac.MAX_MESH_REFUSALS):
        with pytest.raises(ac.MissingVolumeMesh):
            ac.mesh_before_finish("done", GEO, agent=agent)
    assert ac.mesh_before_finish("done", GEO, agent=agent) is True
    assert dp._design_state.data_store["finished_without_mesh"] == ["peer_3"]
    assert dp._design_state.data_store["final_answer_refused_no_mesh"] == ac.MAX_MESH_REFUSALS


def test_with_artifact_checks_keeps_existing_checks_and_does_not_duplicate():
    def other(a, m, agent=None):
        return True
    combined = ac.with_artifact_checks([other])
    assert combined == [other, ac.mesh_before_finish]
    assert ac.with_artifact_checks(combined) == combined
    assert ac.with_artifact_checks(None) == [ac.mesh_before_finish]


def test_export_reports_whether_a_mesh_was_generated():
    assert dp.export_state_summary()["_volume_mesh_generated"] is False
    dp._design_state.data_store["generate_volume_mesh__mesh_base64"] = BIG_MESH
    assert dp.export_state_summary()["_volume_mesh_generated"] is True


def test_every_agent_constructor_in_src_carries_the_artifact_checks():
    """Guards future constructors: an agent built without the check reopens B31."""
    offenders, found = [], 0
    for path in (MAS / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ToolCallingAgent":
                found += 1
                kw = {k.arg: k.value for k in node.keywords}
                v = kw.get("final_answer_checks")
                ok = isinstance(v, ast.Call) and getattr(v.func, "id", None) == "with_artifact_checks"
                if not ok:
                    offenders.append(f"{path.relative_to(MAS)}:{node.lineno}")
    assert found >= 5
    assert not offenders, offenders


# ---- real smolagents loop, real middleware, fake mesh server ------------------

class _ScriptedModel(Model):
    def __init__(self, script):
        super().__init__(model_id="scripted")
        self.script = list(script)
        self.n = 0

    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kw):
        name, args = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        tc = ChatMessageToolCall(id=f"c{self.n}", type="function",
                                 function=ChatMessageToolCallFunction(name=name, arguments=json.dumps(args)))
        return ChatMessage(role="assistant", content="", tool_calls=[tc])


class _FakeOpenCpacs(Tool):
    name = "open_cpacs"
    description = "fake geometry loader"
    inputs = {"source": {"type": "string", "description": "path"}}
    output_type = "string"

    def forward(self, source):
        return json.dumps({"success": True, "session_id": "geo-1"})


class _FakeMassTool(Tool):
    name = "estimate_mass"
    description = "fake mass"
    inputs = {"cpacs_file_path": {"type": "string", "description": "path"}}
    output_type = "string"

    def forward(self, cpacs_file_path):
        return json.dumps({"status": "success"})


class _FakeMeshTool(Tool):
    name = "generate_volume_mesh"
    description = "fake mesher"
    inputs = {"session_id": {"type": "string", "description": "session"}}
    output_type = "string"

    def forward(self, session_id):
        return json.dumps({"success": True, "mesh_base64": BIG_MESH})


def test_real_agent_is_refused_then_meshes_then_finishes():
    from src.tools.type_coercion import wrap_tool_with_middleware

    mesh_tool = wrap_tool_with_middleware(_FakeMeshTool())
    model = _ScriptedModel([
        ("open_cpacs", {"source": "D150_simple.xml"}),
        ("final_answer", {"answer": "MESH_BASE64: 'exported'"}),
        ("generate_volume_mesh", {"session_id": "s1"}),
        ("final_answer", {"answer": "meshed"}),
    ])
    agent = ToolCallingAgent(tools=[_FakeOpenCpacs(), mesh_tool], model=model, max_steps=6,
                             add_base_tools=False, final_answer_checks=ac.with_artifact_checks())
    out = agent.run("make the geometry and mesh")
    assert out == "meshed"
    errors = [str(s.error) for s in agent.memory.steps if getattr(s, "error", None)]
    assert len(errors) == 1 and "NOT FINISHED" in errors[0]
    assert dp._design_state.data_store["final_answer_refused_no_mesh"] == 1
    assert ac.volume_mesh_generated()


def test_real_networked_style_peer_doing_other_work_finishes_without_a_mesh():
    """Full toolset (as networked peers get), no geometry work: finishes on its first try."""
    model = _ScriptedModel([
        ("estimate_mass", {"cpacs_file_path": "D150_simple.xml"}),
        ("final_answer", {"answer": "mass done"}),
    ])
    agent = ToolCallingAgent(tools=[_FakeOpenCpacs(), _FakeMeshTool(), _FakeMassTool()], model=model,
                             max_steps=4, add_base_tools=False, final_answer_checks=ac.with_artifact_checks())
    assert agent.run("estimate the mass") == "mass done"
    assert not [s for s in agent.memory.steps if getattr(s, "error", None)]
    assert not ac.volume_mesh_generated()


# ---- live tigl ------------------------------------------------------------------

def _up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _up(8500), reason="live tigl server on 127.0.0.1:8500 required")
def test_live_geometry_agent_that_skips_meshing_is_made_to_mesh(monkeypatch):
    from scripts.stat_batch_runner import _D150_FIXTURE
    from src.config.loader import load_config
    from src.tools.tool_loader import load_tools_for_agent

    monkeypatch.setattr(dp, "_design_state", None)  # let the loader build the real plane
    tools = {t.name: t for t in load_tools_for_agent([], load_config("config/mdo_f25_run_qwen32b.yaml"))}
    model = _ScriptedModel([
        ("open_cpacs", {"source_type": "path", "source": str(_D150_FIXTURE)}),
        ("final_answer", {"answer": "MESH_BASE64: 'exported'"}),       # the 2026-09-14 behaviour
        ("generate_volume_mesh", {"session_id": "auto"}),                # plane injects the tigl session
        ("final_answer", {"answer": "meshed for real"}),
    ])
    agent = ToolCallingAgent(tools=[tools["open_cpacs"], tools["generate_volume_mesh"]], model=model,
                             max_steps=8, add_base_tools=False, final_answer_checks=ac.with_artifact_checks())
    out = agent.run("geometry stage")
    assert out == "meshed for real"
    assert ac.volume_mesh_generated(), "the real tigl mesh payload must be captured by the data plane"
    assert dp.get_design_state().data_store["final_answer_refused_no_mesh"] == 1
