"""B81 step 2: the design knowledge base is written from real tool results.

No model, no MCP server, no .env: tools are fakes wrapped by the real middleware.
"""

import json

import pytest
from smolagents import Tool, ToolCallingAgent
from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, Model

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm
from src.tools.agent_context import agent_scope
from src.tools.type_coercion import wrap_tool_with_middleware

BIG_MESH = "U1UyIE1FU0g=" * 60


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", {
        "generate_volume_mesh": "tigl", "run_su2_solver": "su2", "estimate_mass": "mass"})
    kbm.configure_run(path=str(tmp_path / "knowledge_base.jsonl"), chain_link=0, attempt=1, seed=None)
    yield
    kbm.configure_run(None, None, None, None)


def _tool(name, response=None, raises=None):
    class T(Tool):
        inputs = {"session_id": {"type": "string", "description": "s", "nullable": True}}
        output_type = "string"
        description = "fake"

        def forward(self, session_id=None):
            if raises:
                raise raises
            return json.dumps(response)
    T.name = name
    return wrap_tool_with_middleware(T())


def test_successful_call_is_recorded_with_agent_refs_and_discipline(tmp_path):
    mesh = _tool("generate_volume_mesh", {"success": True, "mesh_base64": BIG_MESH, "element_count": 660102})
    with agent_scope("agent_1", "geometry engineer"):
        mesh.forward(session_id="geo-1")
    [e] = kbm.get_kb().entries
    assert (e["seq"], e["agent"], e["role"], e["tool"], e["server"], e["discipline"], e["status"]) == \
        (1, "agent_1", "geometry engineer", "generate_volume_mesh", "tigl", "geometry", "success")
    assert e["outputs"]["mesh_base64"] == "generate_volume_mesh__mesh_base64"   # the ref, not the payload
    assert e["outputs"]["element_count"] == 660102
    assert BIG_MESH not in json.dumps(e)
    assert (e["chain_link"], e["attempt"]) == (0, 1)


def test_failed_response_and_raised_error_are_recorded_as_failed():
    _tool("run_su2_solver", {"success": False, "error": "mesh.su2 not found"}).forward(session_id="s")
    with pytest.raises(RuntimeError):
        _tool("estimate_mass", raises=RuntimeError("server down")).forward(session_id="s")
    a, b = kbm.get_kb().entries
    assert a["status"] == "failed" and "mesh.su2 not found" in a["note"]
    assert b["status"] == "failed" and "server down" in b["note"]


def test_non_mcp_tools_are_not_recorded():
    _tool("read_design_knowledge", {"entries": []}).forward()
    assert kbm.get_kb().entries == []


def test_jsonl_is_flushed_on_every_write(tmp_path):
    t = _tool("generate_volume_mesh", {"success": True, "mesh_base64": BIG_MESH})
    t.forward(session_id="a")
    lines = (tmp_path / "knowledge_base.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["tool"] == "generate_volume_mesh"
    t.forward(session_id="b")
    assert len((tmp_path / "knowledge_base.jsonl").read_text().splitlines()) == 2


def test_full_mode_is_capped_keeps_newest_and_says_how_to_narrow():
    kb = kbm.get_kb()
    for i in range(300):
        kb.append(tool="run_su2_solver", server="su2", agent=f"agent_{i % 3}", role="", status="success",
                  outputs={"CL": 0.2, "CD": 0.003, "note": "x" * 150})
    out = kb.read_full()
    assert len(out) <= kbm.FULL_MODE_CAP_CHARS
    d = json.loads(out)
    assert d["truncated"] > 0 and "summary" in d["note"]
    assert d["entries"][-1]["seq"] == 300
    assert kb.metrics["kb_reads_full"] == 1


def test_full_mode_filters():
    kb = kbm.get_kb()
    kb.append(tool="generate_volume_mesh", server="tigl", agent="agent_1", role="", status="success")
    kb.append(tool="run_su2_solver", server="su2", agent="agent_2", role="", status="success")
    kb.append(tool="run_su2_solver", server="su2", agent="agent_2", role="", status="failed")
    assert [e["seq"] for e in json.loads(kb.read_full(tool="run_su2_solver"))["entries"]] == [2, 3]
    assert [e["seq"] for e in json.loads(kb.read_full(agent="agent_1"))["entries"]] == [1]
    assert [e["seq"] for e in json.loads(kb.read_full(discipline="aero", since_seq=2))["entries"]] == [3]


def test_link_start_seed_carries_previous_link_forward(monkeypatch, tmp_path):
    kbm.configure_run(path=str(tmp_path / "kb2.jsonl"), chain_link=1, attempt=1,
                      seed={"summary": "mesh done; SU2 CL 0.2", "end_state": {"Aircraft.Wing.AREA": 130.0}})
    monkeypatch.setattr(dp, "_design_state", DesignState())
    [e] = kbm.get_kb().entries
    assert e["tool"] == "link_start" and e["outputs"]["summary"].startswith("mesh done")
    assert "PREVIOUS LINK: mesh done" in kbm.digest(kbm.get_kb().entries)


def test_kb_resets_with_the_design_state_between_links(monkeypatch):
    kbm.get_kb().append(tool="run_su2_solver", server="su2", agent="a", role="", status="success")
    monkeypatch.setattr(dp, "_design_state", DesignState())
    assert kbm.get_kb().entries == []


def test_digest_reports_done_failed_and_missing():
    kb = kbm.get_kb()
    kb.append(tool="generate_volume_mesh", server="tigl", agent="agent_1", role="", status="success",
              outputs={"mesh_base64": {"ref": "generate_volume_mesh__mesh_base64"}})
    kb.append(tool="run_su2_solver", server="su2", agent="agent_2", role="", status="failed", note="diverged")
    text = kbm.digest(kb.entries)
    assert "generate_volume_mesh: last by agent_1" in text and "generate_volume_mesh__mesh_base64" in text
    assert "run_su2_solver by agent_2" in text and "diverged" in text
    assert "STILL MISSING for a coupled result: SU2 solve, mass estimate, engine cycle, mission simulation" in text


def test_every_agent_toolset_gets_read_design_knowledge():
    from src.tools.tool_loader import _with_design_state

    names = [t.name for t in _with_design_state([])]
    assert names == ["get_design_state", "read_design_knowledge"]
    assert [t.name for t in _with_design_state(_with_design_state([]))] == names


class _Scripted(Model):
    def __init__(self, script):
        super().__init__(model_id="scripted")
        self.script, self.n = list(script), 0

    def generate(self, messages, stop_sequences=None, response_format=None, tools_to_call_from=None, **kw):
        name, args = self.script[min(self.n, len(self.script) - 1)]
        self.n += 1
        return ChatMessage(role="assistant", content="", tool_calls=[ChatMessageToolCall(
            id=f"c{self.n}", type="function", function=ChatMessageToolCallFunction(name=name, arguments=json.dumps(args)))])


def test_claims_in_final_answer_are_never_recorded_only_real_calls():
    mesh = _tool("generate_volume_mesh", {"success": True, "mesh_base64": BIG_MESH})
    agent = ToolCallingAgent(tools=[mesh], model=_Scripted([
        ("final_answer", {"answer": "MESH_BASE64: exported; SU2 converged CL=0.5"}),
    ]), max_steps=3, add_base_tools=False)
    with agent_scope("geometry_engineer"):
        agent.run("geometry")
    assert kbm.get_kb().entries == []            # a claim is not a record
    agent = ToolCallingAgent(tools=[mesh], model=_Scripted([
        ("generate_volume_mesh", {"session_id": "g"}), ("final_answer", {"answer": "done"}),
    ]), max_steps=3, add_base_tools=False)
    with agent_scope("geometry_engineer"):
        agent.run("geometry")
    assert [e["tool"] for e in kbm.get_kb().entries] == ["generate_volume_mesh"]
