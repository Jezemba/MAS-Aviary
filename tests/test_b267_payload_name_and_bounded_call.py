"""B267: a mesh ref in a file-name argument wedged the SU2 server, and the run waited on it for 3 hours.

l2c seq_graph (ws5, 2026-11-10): create_su2_session(mesh_file_name="generate_volume_mesh__mesh_base64") had the
155,514,712-char mesh substituted into the file name; su2-mcp's log parser then spun on it with the server's
only thread. call_watchdog released the solver calls, but a later get_su2_status waited 3 h past its budget.
"""
import json
import threading
import time

import pytest

from src.coordination.design_state import DesignState
from src.tools import call_watchdog
from src.tools import knowledge_base as kb
from src.tools.agent_context import agent_scope, current_agent_name
from src.tools.data_plane import init_data_plane, payload_into_name_error, resolve_request

MESH_KEY = "generate_volume_mesh__mesh_base64"
TOOL_SERVER_MAP = {"create_su2_session": "su2", "set_mesh": "su2", "get_su2_status": "su2"}


@pytest.fixture(autouse=True)
def _state():
    ds = DesignState()
    ds.data_store[MESH_KEY] = "TkRJTUU9Mw==" * 2000
    ds.data_store["morphed_cpacs_path"] = "/tmp/x/D150_simple_modified.xml"      # a short stored PATH
    init_data_plane(ds, TOOL_SERVER_MAP)
    yield ds
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _tool(name, inputs, server_calls, body=lambda **kw: "{}"):
    from src.tools.type_coercion import apply_coercion_to_tools

    class _T:
        def forward(self, **kwargs):
            server_calls.append(kwargs)
            return body(**kwargs)

    t = _T()
    t.name = name
    t.inputs = {k: {"type": "string", "description": k, "nullable": True} for k in inputs}
    return apply_coercion_to_tools([t])[0]


# -- the payload never becomes a file name ---------------------------------------------------------------

def test_the_mesh_ref_is_not_expanded_into_the_file_name():
    out = resolve_request("create_su2_session", {"mesh_file_name": MESH_KEY})
    assert out["mesh_file_name"] == MESH_KEY


def test_the_live_call_is_refused_before_the_server_and_names_the_right_argument():
    calls = []
    tool = _tool("create_su2_session", ["mesh_file_name", "initial_mesh", "cpacs_file_path"], calls)
    out = json.loads(tool.forward(mesh_file_name=MESH_KEY, cpacs_file_path="/tmp/a.xml"))
    assert out["error_code"] == "PAYLOAD_IN_NAME_ARGUMENT"
    assert "'initial_mesh'" in out["error"] and "NOT sent to the server" in out["error"]
    assert calls == []


@pytest.mark.parametrize("form", [MESH_KEY, f"ref:{MESH_KEY}", {"ref": MESH_KEY}, json.dumps({"ref": MESH_KEY})])
def test_every_ref_form_is_caught(form):
    assert payload_into_name_error("create_su2_session", {"mesh_file_name": form}) is not None


def test_the_payload_still_resolves_into_a_payload_argument():
    out = resolve_request("set_mesh", {"mesh_base64": MESH_KEY})
    assert out["mesh_base64"].startswith("TkRJTUU9Mw==") and len(out["mesh_base64"]) > 10000
    assert payload_into_name_error("set_mesh", {"mesh_base64": MESH_KEY}) is None


def test_a_short_stored_path_still_resolves_into_a_path_argument():
    assert payload_into_name_error("estimate_mass", {"cpacs_file_path": "morphed_cpacs_path"}) is None
    assert resolve_request("estimate_mass", {"cpacs_file_path": "morphed_cpacs_path"})["cpacs_file_path"] \
        == "/tmp/x/D150_simple_modified.xml"


def test_plain_names_pass():
    assert payload_into_name_error("create_su2_session", {"mesh_file_name": "mesh.su2"}) is None


# -- the whole call is bounded ---------------------------------------------------------------------------

def test_work_before_the_server_call_is_bounded(monkeypatch):
    """The framework's own work hangs (as get_su2_status did): the call is released at its budget."""
    release = threading.Event()
    monkeypatch.setitem(call_watchdog._TOOL_TIMEOUTS, "get_su2_status", 0.5)
    monkeypatch.setattr(call_watchdog, "_OUTER_MARGIN_SECONDS", 0.5)
    import src.tools.data_plane as dp
    real = dp.resolve_request

    def stuck(name, kwargs):
        if name == "get_su2_status":
            release.wait(30)
        return real(name, kwargs)

    monkeypatch.setattr(dp, "resolve_request", stuck)
    calls = []
    tool = _tool("get_su2_status", ["session_id"], calls)
    t0 = time.time()
    with pytest.raises(call_watchdog.ToolCallTimeout):
        tool.forward()
    assert time.time() - t0 < 5
    release.set()


def test_a_server_that_never_answers_is_still_released_by_the_inner_budget(monkeypatch):
    monkeypatch.setitem(call_watchdog._TOOL_TIMEOUTS, "get_su2_status", 0.5)
    release = threading.Event()
    calls = []
    tool = _tool("get_su2_status", ["session_id"], calls, body=lambda **kw: release.wait(30) and "{}")
    t0 = time.time()
    with pytest.raises(call_watchdog.ToolCallTimeout) as exc:
        tool.forward()
    assert "B85" in str(exc.value)          # the inner bound fired first, as before
    assert time.time() - t0 < 5
    release.set()


def test_a_timeout_writes_every_threads_stack_beside_the_link(tmp_path, monkeypatch):
    monkeypatch.setitem(kb._run_config, "path", str(tmp_path / "knowledge_base.jsonl"))
    monkeypatch.setitem(call_watchdog._TOOL_TIMEOUTS, "get_su2_status", 0.3)
    release = threading.Event()
    tool = _tool("get_su2_status", ["session_id"], [], body=lambda **kw: release.wait(30) and "{}")
    with pytest.raises(call_watchdog.ToolCallTimeout):
        tool.forward()
    release.set()
    text = (tmp_path / "hang_stacks.txt").read_text()
    assert "get_su2_status" in text and "Thread" in text


def test_a_call_marks_progress_and_starts_the_stall_monitor():
    call_watchdog._progress["last"] = None
    _tool("get_su2_status", ["session_id"], []).forward()
    assert call_watchdog._progress["last"] is not None
    assert call_watchdog._monitor_started.is_set()


def test_the_agent_identity_and_the_heartbeat_scope_travel_with_the_call():
    seen = {}

    def body(**kw):
        seen["agent"] = current_agent_name()
        seen["no_record"] = kb._no_record_on.get()
        return "{}"

    tool = _tool("get_su2_status", ["session_id"], [], body=body)
    with agent_scope("aerodynamics_analyst"), kb.not_recorded():
        tool.forward()
    assert seen == {"agent": "aerodynamics_analyst", "no_record": True}
    assert kb._no_record_on.get() is False
