"""Agents cannot replace the runner's aviary session (Jessica, 2026-09-14, option 2).

Before this, an agent's create_session was captured as the run's session and the
override redirected EVERY agent's mission calls to it, so the run silently flew
aviary's default mission. Now the call is refused before it reaches the server,
the existing session is named, and the attempt is counted.
"""

import json
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

MAS = Path(__file__).resolve().parents[1]
SID = "runner-aviary-session"


@pytest.fixture(autouse=True)
def fresh_plane(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", None)
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", {
        "create_session": "aviary", "run_simulation": "aviary", "open_cpacs": "tigl",
    })
    yield


def test_no_runner_session_means_no_refusal():
    """The runner's own pre-hook and interactive use must still create sessions."""
    dp._design_state = DesignState()
    assert dp.session_creation_refusal("create_session", {}) is None


def test_refused_when_runner_session_registered_and_names_it():
    dp.register_presession("aviary", SID)
    r = dp.session_creation_refusal("create_session", {"initial_parameters": {"Aircraft.Wing.AREA": 130}})
    assert r["success"] is False and r["error_code"] == "SESSION_EXISTS"
    assert r["session_id"] == SID
    assert f'session_id="{SID}"' in r["error"]
    assert "set_aircraft_parameters" in r["error"] and "2500 nmi" in r["error"]


def test_attempts_are_counted_and_exported():
    dp.register_presession("aviary", SID)
    for _ in range(3):
        dp.session_creation_refusal("create_session", {"initial_parameters": {"x": 1}})
    out = dp.export_state_summary()
    assert out["create_session_refused"] == 3
    assert out["create_session_refused_args"] == [{"initial_parameters": {"x": 1}}] * 3


def test_other_servers_creation_tools_are_not_refused():
    dp.register_presession("aviary", SID)
    assert dp.session_creation_refusal("open_cpacs", {"source": "x.xml"}) is None


def test_reset_between_links_clears_the_pin():
    dp.register_presession("aviary", SID)
    dp.reset_design_state()
    assert dp.session_creation_refusal("create_session", {}) is None


def test_capture_never_overwrites_the_runner_session():
    dp.register_presession("aviary", SID)
    dp._capture_session("create_session", {"session_id": "agent-made-session"})
    assert dp._design_state.sessions["aviary"] == SID


def test_middleware_short_circuits_before_the_server():
    from src.tools.type_coercion import wrap_tool_with_middleware

    class FakeCreateSession:
        name = "create_session"
        inputs = {"initial_parameters": {"type": "object", "description": "", "nullable": True}}
        output_type = "string"
        called = 0

        def forward(self, **kw):
            FakeCreateSession.called += 1
            return json.dumps({"success": True, "session_id": "would-be-blank"})

    tool = wrap_tool_with_middleware(FakeCreateSession())
    dp.register_presession("aviary", SID)
    out = json.loads(tool.forward())
    assert FakeCreateSession.called == 0
    assert out["error_code"] == "SESSION_EXISTS" and out["session_id"] == SID
    assert dp._design_state.sessions["aviary"] == SID


def test_preamble_is_accurate():
    from scripts.stat_batch_runner import build_task_with_session

    task = build_task_with_session("base", "s1")
    assert "WILL FAIL" not in task
    assert "refuses create_session" in task and "silently fly the default mission" in task


def _aviary_up():
    try:
        with socket.create_connection(("127.0.0.1", 8600), timeout=1):
            return True
    except OSError:
        return False


PREHOOK = textwrap.dedent('''
    from src.config.loader import load_config
    from scripts.stat_batch_runner import _load_mcp_tools, generate_random_params, setup_session_with_params
    tm = _load_mcp_tools(load_config("config/mdo_f25_run_qwen32b.yaml"))
    anchor = {k: v for k, v in generate_random_params(42).items() if not k.startswith("_")}
    print("SID=" + setup_session_with_params(tm, anchor)["session_id"])
''')

CHILD = textwrap.dedent('''
    import json, sys
    from src.config.loader import load_config
    from src.tools import data_plane
    sid = sys.argv[1]
    data_plane.register_presession("aviary", sid)          # what run_combination does
    from src.tools.tool_loader import load_tools_for_agent
    tools = {t.name: t for t in load_tools_for_agent([], load_config("config/mdo_f25_run_qwen32b.yaml"))}
    # exactly what the sequential mission stage prompt instructs
    c = tools["create_session"].forward()
    cd = json.loads(c) if isinstance(c, str) else c
    handed = cd.get("session_id")
    r = tools["run_simulation"].forward(session_id=handed, timeout_seconds=300)
    rd = json.loads(r) if isinstance(r, str) else r
    from scripts.stat_batch_runner import read_flown_mission
    m = read_flown_mission(tools, data_plane.get_design_state().sessions["aviary"])
    print("RESULT=" + json.dumps({"code": cd.get("error_code"), "handed": handed, "ran_on": rd.get("session_id"),
                                  "matches": m["matches_canonical"],
                                  "refused": data_plane.export_state_summary().get("create_session_refused")}))
''')


def _py(code, *args):
    import os
    out = subprocess.run([sys.executable, "-c", code, *args], cwd=MAS, capture_output=True, text=True,
                         timeout=600, env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
    return out.stdout


@pytest.mark.skipif(not _aviary_up(), reason="live aviary server on 127.0.0.1:8600 required")
def test_live_agent_create_session_is_refused_and_run_stays_on_canonical_mission():
    sid = next(l for l in _py(PREHOOK).splitlines() if l.startswith("SID="))[4:]
    res = json.loads(next(l for l in _py(CHILD, sid).splitlines() if l.startswith("RESULT="))[7:])
    assert res["code"] == "SESSION_EXISTS"
    assert res["handed"] == sid
    assert res["ran_on"] == sid
    assert res["matches"] is True
    assert res["refused"] == 1
