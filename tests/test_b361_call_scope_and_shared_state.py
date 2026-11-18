"""B361 / B362: per-call state, framework-initiated calls, and shared state written by parallel peers (units).

The end-to-end links are in test_b361_end_to_end_threaded_link.py. Here: each mechanism on its own. No network, no
API, no MCP servers.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import call_scope, call_watchdog, work_claims
from src.tools import knowledge_base as kbm
from src.tools.agent_context import agent_scope


# -- the scope of one call ----------------------------------------------------------------------------------------------

def test_b361_a_call_scope_does_not_outlive_its_call_on_a_reused_thread():
    """The watchdog pool reuses threads: what one call set must not be there for the next call on that thread."""
    import contextvars

    seen = []

    def one_call(set_value):
        tokens = call_scope.open_call_scope()
        try:
            seen.append(getattr(dp._call_ctx, "wing_uid", None))
            if set_value:
                dp._call_ctx.wing_uid = set_value
                dp._call_notes()["_coupled_override_last"] = {"x": 1}
        finally:
            call_scope.close_call_scope(tokens)

    with ThreadPoolExecutor(max_workers=1) as pool:             # one worker: both calls on the same thread
        pool.submit(contextvars.copy_context().run, one_call, "Wing3V").result()
        pool.submit(contextvars.copy_context().run, one_call, None).result()
    assert seen == [None, None]


def test_b361_real_middleware_on_one_pooled_thread_starts_each_call_clean(monkeypatch, tmp_path):
    """Through the real wrapper with single-thread pools: get_fuselage_summary's post-hook does not see the wing uid
    get_wing_summary's pre-hook set (with the thread-local it did: same pooled thread, attribute never cleared)."""
    from src.tools.type_coercion import apply_coercion_to_tools

    monkeypatch.setattr(call_watchdog, "_outer_pool", ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(call_watchdog, "_pool", ThreadPoolExecutor(max_workers=1))
    dp.init_data_plane(DesignState(), {"get_wing_summary": "tigl", "get_fuselage_summary": "tigl"})
    kbm.configure_run(None, 0, 1, None)
    seen = {}
    real = dp.intercept_response

    def spy(tool_name, response):
        seen[tool_name] = (getattr(dp._call_ctx, "wing_uid", None), threading.current_thread().name)
        return real(tool_name, response)

    monkeypatch.setattr(dp, "intercept_response", spy)

    class _T:
        def __init__(self, name):
            self.name = name
            self.inputs = {k: {"type": "any", "description": k, "nullable": True}
                           for k in ("session_id", "wing_uid", "fuselage_uid")}

        def forward(self, **kw):
            return json.dumps({"success": True})

    wing, fus = apply_coercion_to_tools([_T("get_wing_summary"), _T("get_fuselage_summary")])
    try:
        wing.forward(session_id="t", wing_uid="Wing3V")
        fus.forward(session_id="t", fuselage_uid="Fuselage1")
    finally:
        dp.init_data_plane(DesignState(), {})
    assert seen["get_wing_summary"][0] == "Wing3V"
    assert seen["get_fuselage_summary"][1] == seen["get_wing_summary"][1]     # the same pooled thread
    assert seen["get_fuselage_summary"][0] is None


def test_b361_reply_notes_are_per_call_and_parallel_calls_do_not_share_them():
    import contextvars

    barrier = threading.Barrier(2)
    got = {}

    def call(name):
        tokens = call_scope.open_call_scope()
        try:
            dp._note_coupled_override("Fn_DES", 1000.0 if name == "a" else 2000.0, 7055.2)
            barrier.wait(10)
            got[name] = dict(dp._call_notes().get("_coupled_override_last") or {})
        finally:
            call_scope.close_call_scope(tokens)

    threads = [threading.Thread(target=contextvars.copy_context().run, args=(call, n)) for n in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert got["a"]["Fn_DES"]["asked"] == 1000.0 and got["b"]["Fn_DES"]["asked"] == 2000.0


def test_b361_coupling_contract_start_fingerprint_is_the_calls_own():
    """B262's start-of-call fingerprint lives in the call's scope (was a thread-local of the pooled thread)."""
    from src.tools import coupling_contract as cc

    tokens = call_scope.open_call_scope()
    try:
        cc.note_call_start("estimate_mass")
        assert "mass" in (getattr(dp._call_ctx, "producer_start_fp", None) or {})
    finally:
        call_scope.close_call_scope(tokens)
    tokens = call_scope.open_call_scope()
    try:
        assert getattr(dp._call_ctx, "producer_start_fp", None) is None
    finally:
        call_scope.close_call_scope(tokens)


# -- B362: framework-initiated calls ------------------------------------------------------------------------------------

def test_b362_a_nested_call_is_framework_initiated_and_an_agent_call_is_not():
    import contextvars

    marks = []

    def middleware(inner):
        tokens = call_scope.open_call_scope()
        try:
            marks.append(call_scope.framework_initiated())
            if inner:
                ctx = contextvars.copy_context()           # call_watchdog.bounded runs the nested call like this
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(ctx.run, middleware, False).result()
        finally:
            call_scope.close_call_scope(tokens)

    contextvars.copy_context().run(middleware, True)
    contextvars.copy_context().run(middleware, False)
    assert marks == [False, True, False]
    assert contextvars.copy_context().run(call_scope.run_as_framework,
                                          lambda: (call_scope.open_call_scope(), call_scope.framework_initiated())[1])


def test_b362_claims_do_not_refuse_the_frameworks_calls(monkeypatch):
    """A framework call carries its caller's identity; the claim board must not treat it as that peer's work."""
    from src.coordination.blackboard import Blackboard
    from src.tools.type_coercion import apply_coercion_to_tools

    dp.init_data_plane(DesignState(), {"set_aircraft_parameters": "aviary"})
    kbm.configure_run(None, 0, 1, None)
    board = Blackboard(claiming_mode="hard")
    board.seed_todos([("mission", "mission"), ("propulsion", "propulsion")])
    assert board.claim_todo("mission", "peer_3")[0] and board.claim_todo("propulsion", "peer_2")[0]
    work_claims.reset()
    work_claims.register_blackboard(board)
    work_claims.set_structure("networked")
    server = []

    class _T:
        name = "set_aircraft_parameters"
        inputs = {k: {"type": "any", "description": k, "nullable": True} for k in ("session_id", "parameters")}

        def forward(self, **kw):
            server.append(kw)
            return json.dumps({"success": True, "applied": []})

    (tool,) = apply_coercion_to_tools([_T()])
    try:
        with agent_scope("peer_2"):
            own = json.loads(tool.forward(session_id="av", parameters={}))
            framework = json.loads(call_scope.run_as_framework(tool.forward, session_id="av", parameters={}))
    finally:
        work_claims.reset()
        dp.init_data_plane(DesignState(), {})
    assert own.get("error_code") == work_claims.ERROR_CLAIMED_BY_OTHER        # peer_2's own call: refused
    assert framework.get("error_code") is None and len(server) == 1          # the framework's: runs
    assert {t.name: t.assigned_to for t in board.read_todos()}["mission"] == "peer_3"


# -- shared state written by parallel peers -------------------------------------------------------------------------------

def test_b361_design_json_concurrent_writes_are_all_written_and_valid(tmp_path):
    from src.tools import design_json

    state = DesignState()
    state.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.8}
    kbm.configure_run(str(tmp_path / "link" / "knowledge_base.jsonl"), 1, 1, None)
    stop = threading.Event()
    results = []

    def mutate():
        i = 0
        while not stop.is_set():
            state.data_store[f"k{i % 50}"] = {"v": i}
            state.data_store.pop(f"k{(i + 25) % 50}", None)
            i += 1

    def write():
        for _ in range(20):
            results.append(design_json.write_design(state))

    try:
        m = threading.Thread(target=mutate)
        m.start()
        writers = [threading.Thread(target=write) for _ in range(6)]
        for t in writers:
            t.start()
        for t in writers:
            t.join(60)
        stop.set()
        m.join(10)
        assert len(results) == 120 and all(results), "a design.json write was lost"
        assert "design_json_write_errors" not in state.data_store
        json.loads((tmp_path / "link" / "design.json").read_text())
        assert not list((tmp_path / "link").glob("*.tmp"))
    finally:
        kbm.configure_run(None, 0, 1, None)


def test_b361_auto_size_refusal_names_its_own_sizing_reply(monkeypatch):
    """run_cycle's refusal quotes the framework sizing call's OWN reply, not engine_sizing_error another peer wrote."""
    state = DesignState()
    state.sessions["pycycle"] = "pc"
    dp.init_data_plane(state, {"set_inputs": "pycycle"})
    state.data_store["engine_sizing_error"] = "ANOTHER PEER'S REASON"

    class _SetInputs:
        def forward(self, **kw):
            return json.dumps({"success": False, "error_code": "DESIGN_THRUST_NOT_PHYSICAL", "error": "OWN REASON"})

    monkeypatch.setattr(dp, "_registered_tools", {"set_inputs": _SetInputs()})
    monkeypatch.setattr(dp, "current_cruise_point", lambda: ({"lift_to_drag": 17.6}, "ok"))
    monkeypatch.setattr(dp, "_cycle_sizing_key", lambda: [1.0])
    monkeypatch.setattr(dp, "cycle_is_sized", lambda sid: False)
    try:
        dp.take_refusal()
        dp._auto_size_cycle({"session_id": "pc"})
        refusal = dp.take_refusal()
    finally:
        dp.init_data_plane(DesignState(), {})
    assert refusal["error_code"] == "DESIGN_THRUST_NOT_AVAILABLE"
    assert "OWN REASON" in refusal["error"] and "ANOTHER PEER" not in refusal["error"]


def test_b361_batched_generation_hands_the_prompt_back_to_the_peer(tmp_path):
    """The networked batch worker builds each peer's prompt on its own thread; the peer's step log gets it."""
    import contextlib

    from src.llm import batch_generation
    from src.logging import step_log

    class _Model:
        @contextlib.contextmanager
        def _thinking_for_this_call(self, enabled):
            yield

        def _prepare_completion_args(self, messages, **kw):
            step_log.record_prompt(messages, 123)
            return {"inputs": None}

    step_log.configure(tmp_path / "step_log_attempt1.jsonl")
    try:
        request = batch_generation._Request(model=_Model(), messages=[{"role": "user", "content": "hi"}],
                                            stop_sequences=None, tools_to_call_from=None, thinking=False, kwargs={})
        worker = threading.Thread(target=lambda: batch_generation._prepare(request), name="avion-batch-generation")
        worker.start()
        worker.join(10)
        assert request.step_log_prompt and request.step_log_prompt["prompt_tokens"] == 123
        # The peer's thread: what submit() does after the worker answered, then thinking_model's record_generation.
        step_log.put_prompt(request.step_log_prompt)
        step_log.record_generation(type("M", (), {"content": "ok", "token_usage": None})(), attempt=0,
                                   thinking=False)
        pending = step_log._pending()
        assert pending and pending[-1]["prompt"]["prompt_tokens"] == 123
        pending.clear()
    finally:
        step_log.configure(None)


@pytest.fixture(autouse=True)
def _clean():
    yield
    work_claims.reset()
    kbm.configure_run(None, 0, 1, None)
