"""B81 live check against the real tigl server (no LLM, no .env), per handoff section 6.

Two simulated agents: agent_1 opens the fixture and meshes; agent_2's mesh on the
same design is refused, naming agent_1 and the mesh ref, WITHOUT reaching tigl;
agent_2's second call runs.
"""

import json
import socket

import pytest

import src.tools.data_plane as dp
from src.tools import knowledge_base as kbm
from src.tools.agent_context import agent_scope


def _up(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _up(8500), reason="live tigl server on 127.0.0.1:8500 required")
def test_live_duplicate_mesh_is_refused_then_allowed_on_the_second_call(monkeypatch):
    from scripts.stat_batch_runner import _D150_FIXTURE
    from src.config.loader import load_config
    from src.tools.tool_loader import load_tools_for_agent

    monkeypatch.setattr(dp, "_design_state", None)
    kbm.configure_run(None, 0, 1, None)
    tools = {t.name: t for t in load_tools_for_agent([], load_config("config/mdo_f25_run_qwen32b.yaml"))}
    j = lambda r: json.loads(r) if isinstance(r, str) else r

    with agent_scope("agent_1", "geometry"):
        assert j(tools["open_cpacs"].forward(source_type="path", source=str(_D150_FIXTURE)))["session_id"]
        first = j(tools["generate_volume_mesh"].forward(session_id="auto"))
    assert first.get("success") is not False, first
    meshes_after_first = sum(1 for e in kbm.get_kb().entries if e["tool"] == "generate_volume_mesh")

    with agent_scope("agent_2", "aero"):
        refused = j(tools["generate_volume_mesh"].forward(session_id="auto"))
    assert refused["error_code"] == "ALREADY_DONE" and refused["done_by"] == "agent_1"
    assert "agent_1 already did generate_volume_mesh" in refused["error"]
    assert "generate_volume_mesh__mesh_base64" in refused["error"]

    with agent_scope("agent_2", "aero"):
        second = j(tools["generate_volume_mesh"].forward(session_id="auto"))
    assert second.get("success") is not False and "error_code" not in second, second

    entries = [e for e in kbm.get_kb().entries if e["tool"] == "generate_volume_mesh"]
    assert [e["status"] for e in entries] == ["success", "refused", "success"]
    assert meshes_after_first == 1
    assert kbm.get_kb().metrics["duplicate_refusals"] == {"agent_2": {"generate_volume_mesh": 1}}
    assert kbm.get_kb().metrics["duplicate_repeats_after_refusal"] == {"agent_2": {"generate_volume_mesh": 1}}
