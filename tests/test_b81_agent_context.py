"""B81 step 1: the calling agent's identity is visible to tool calls, per thread."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from src.tools.agent_context import agent_scope, current_agent, current_agent_name


def test_default_is_unknown():
    assert current_agent() is None
    assert current_agent_name() == "unknown"


def test_scope_sets_and_restores_including_nesting():
    with agent_scope("orchestrator", "planner"):
        assert current_agent_name() == "orchestrator"
        with agent_scope("agent_2", "aero"):
            assert current_agent().role == "aero"
        assert current_agent_name() == "orchestrator"
    assert current_agent() is None


def test_concurrent_peers_each_see_their_own_identity():
    seen, barrier = {}, threading.Barrier(2)

    def peer(name):
        with agent_scope(name):
            barrier.wait()            # both scopes are open at the same moment
            seen[name] = current_agent_name()

    ts = [threading.Thread(target=peer, args=(n,)) for n in ("agent_1", "agent_2")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert seen == {"agent_1": "agent_1", "agent_2": "agent_2"}


def test_identity_follows_smolagents_style_parallel_tool_calls():
    # smolagents runs parallel tool calls as executor.submit(copy_context().run, ...)
    with agent_scope("agent_3"):
        with ThreadPoolExecutor(2) as ex:
            names = [f.result() for f in [ex.submit(copy_context().run, current_agent_name) for _ in range(4)]]
    assert names == ["agent_3"] * 4
