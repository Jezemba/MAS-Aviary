"""B77: a spawned run must use the runner's aviary session, not a blank one.

The runner's pre-hook creates the aviary session (seed-42 design + canonical
mission) in the parent. Each run executes in a ``spawn``ed child whose data plane
starts empty; since 2026-08-15 an empty plane auto-created a BLANK aviary session
and overrode every agent-supplied id with it, so runs flew aviary's default
mission and no chain link carried a design forward.
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
RUNNER_SID = "4159ec00-runner-session"


@pytest.fixture(autouse=True)
def fresh_plane(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", None)
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_tool_server_map", {"run_simulation": "aviary", "create_session": "aviary",
                                                 "configure_mission": "aviary", "get_results": "aviary"})
    yield


class _CreateSessionStub:
    name = "create_session"

    def __init__(self):
        self.calls = 0

    def forward(self, **kw):
        self.calls += 1
        return json.dumps({"success": True, "session_id": "blank-plane-session"})


def test_mechanism_without_registration_plane_swaps_in_a_blank_session():
    """Documents the bug: an empty plane replaces the runner's valid id."""
    stub = _CreateSessionStub()
    dp._registered_tools["create_session"] = stub
    dp._design_state = DesignState()
    out = dp.resolve_request("run_simulation", {"session_id": RUNNER_SID})
    assert stub.calls == 1
    assert out["session_id"] == "blank-plane-session"


def test_registered_presession_is_used_and_nothing_is_auto_created(monkeypatch):
    monkeypatch.setattr(dp, "_auto_create_session", lambda *a, **k: pytest.fail("auto-create must not run"))
    dp.register_presession("aviary", RUNNER_SID)
    assert dp.resolve_request("run_simulation", {"session_id": RUNNER_SID})["session_id"] == RUNNER_SID
    # a missing id is filled with the runner's, not a new one
    assert dp.resolve_request("configure_mission", {"range_nmi": 2500})["session_id"] == RUNNER_SID


def test_register_presession_creates_state_when_none_and_ignores_empty_id():
    dp.register_presession("aviary", None)
    assert dp._design_state is None
    dp.register_presession("aviary", RUNNER_SID)
    assert dp._design_state.sessions["aviary"] == RUNNER_SID


def test_tool_loading_reuses_the_registered_state(monkeypatch):
    from src.tools import tool_loader

    dp.register_presession("aviary", RUNNER_SID)
    registered = dp._design_state

    class _Conn:
        tool_server_map = {"run_simulation": "aviary"}

    tool_loader._init_data_plane_if_needed(_Conn())
    assert dp.get_design_state() is registered
    assert dp.get_design_state().sessions["aviary"] == RUNNER_SID


def test_export_reports_the_session_in_use_at_the_end():
    dp.register_presession("aviary", RUNNER_SID)
    assert dp.export_state_summary()["_aviary_session_at_end"] == RUNNER_SID


def test_run_combination_registers_before_any_agent_runs(monkeypatch):
    from src.runners import batch_runner

    seen = {}

    def fake_execute(combo, task, config, *, model=None, tools=None, session_id=None):
        seen["sessions"] = dict(dp.get_design_state().sessions)
        return [], {}

    monkeypatch.setattr(batch_runner, "_execute_combination", fake_execute)
    combo = next(c for c in batch_runner.AVIARY_COMBINATIONS if c.name == "mdo_f25_sequential_iterative_feedback")

    class _Cfg:
        llm = None

    try:
        batch_runner.run_combination(combo, "task", _Cfg(), session_id=RUNNER_SID)
    except Exception:
        pass  # metrics on an empty run may fail; registration happens first
    assert seen.get("sessions", {}).get("aviary") == RUNNER_SID


class _GetResultsStub:
    def __init__(self, payload):
        self.payload = payload

    def forward(self, **kw):
        return json.dumps(self.payload)


def _tm(mission):
    body = {"success": True, "design_parameters": {"mission_config": mission, "aircraft_params": {}}}
    return {"get_results": _GetResultsStub(body)}


def test_flown_mission_canonical_matches():
    from scripts.stat_batch_runner import read_flown_mission

    r = read_flown_mission(_tm({"range_nmi": 2500.0, "num_passengers": 239, "cruise_mach": 0.78,
                                "cruise_altitude_ft": 33000.0}), "s")
    assert r["matches_canonical"] is True and r["mismatches"] == {}


def test_flown_mission_default_is_flagged():
    from scripts.stat_batch_runner import read_flown_mission

    r = read_flown_mission(_tm({"range_nmi": 1500, "num_passengers": 162, "cruise_mach": 0.785,
                                "cruise_altitude_ft": 35000}), "s")
    assert r["matches_canonical"] is False
    assert set(r["mismatches"]) == {"range_nmi", "num_passengers", "cruise_mach", "cruise_altitude_ft"}


def test_flown_mission_unreadable_is_none_not_pass():
    from scripts.stat_batch_runner import read_flown_mission

    tm = {"get_results": _GetResultsStub({"success": False, "error_code": "NO_RESULTS"})}
    r = read_flown_mission(tm, "s")
    assert r["matches_canonical"] is None and r["error"] == "NO_RESULTS"


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
    sid, register = sys.argv[1], sys.argv[2] == "1"
    if register:
        data_plane.register_presession("aviary", sid)
    from src.tools.tool_loader import load_tools_for_agent
    tools = {t.name: t for t in load_tools_for_agent([], load_config("config/mdo_f25_run_qwen32b.yaml"))}
    r = tools["run_simulation"].forward(session_id=sid, timeout_seconds=300)
    d = json.loads(r) if isinstance(r, str) else r
    from scripts.stat_batch_runner import read_flown_mission
    used = data_plane.get_design_state().sessions["aviary"]
    m = read_flown_mission(tools, used)
    print("RESULT=" + json.dumps({"ran_on": d.get("session_id"), "matches": m["matches_canonical"],
                                  "flown": m["flown"]}))
''')


def _py(code, *args):
    out = subprocess.run([sys.executable, "-c", code, *args], cwd=MAS, capture_output=True, text=True,
                         timeout=600, env={**__import__("os").environ, "CUDA_VISIBLE_DEVICES": ""})
    return out.stdout


@pytest.mark.skipif(not _aviary_up(), reason="live aviary server on 127.0.0.1:8600 required")
def test_live_two_processes_child_flies_the_runner_session_and_canonical_mission():
    """Mirrors the real runner: pre-hook in one process, the run in a fresh one."""
    sid = next(l for l in _py(PREHOOK).splitlines() if l.startswith("SID="))[4:]

    fixed = json.loads(next(l for l in _py(CHILD, sid, "1").splitlines() if l.startswith("RESULT="))[7:])
    assert fixed["ran_on"] == sid
    assert fixed["matches"] is True, fixed["flown"]

    broken = json.loads(next(l for l in _py(CHILD, sid, "0").splitlines() if l.startswith("RESULT="))[7:])
    assert broken["ran_on"] != sid, "control: without registration the plane should swap sessions"
    assert broken["matches"] is False, broken["flown"]
