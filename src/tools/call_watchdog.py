"""No single call may wedge a run (B85).

WHAT HAPPENED (7960, validate8_net_7960, 2026-09-16)

The first networked link stopped progressing at ~18:50 and was still frozen 48 minutes
later: 92 threads, ALL sleeping in ``futex_wait_queue``; 0% on all four GPUs with memory
still held; the runner alive; no in-flight MCP request on any of the five servers, and all
five answering. Nothing was spinning -- everything was parked on a lock -- and the run
would not have recovered on its own.

WHY A WATCHDOG RATHER THAN ONE ROOT-CAUSE FIX

The originally suspected cause does not hold: the peer's place IS released when its run
ends by max-steps or by an exception, because ``_run_one_peer_batched`` releases in a
``finally`` and ``_run_one_peer`` catches ``Exception`` itself. What the code does have is
waits with no upper bound. The one that can park a thread forever is mcpadapt's own sync
bridge:

    asyncio.run_coroutine_threadsafe(session.call_tool(...), self.loop).result()

``.result()`` is called with no timeout, so if that event loop stops serving -- its thread
gone, the stream closed under it -- the caller waits forever. We do not own that code, and
a run must not depend on it being perfect.

So every call this framework makes through a tool gets an upper bound here. A tool that has
not answered within its budget raises, the agent sees a normal tool failure, its run ends,
its peer place is released, and the LINK FINISHES AND IS RECORDED as a failed or limited
run -- which is the acceptance criterion. A hang becomes a data point instead of a night.

The budget is generous by default because these are real solvers: an SU2 solve or an aviary
mission legitimately runs for many minutes. It is a deadlock bound, not a performance one.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Per-tool budgets, in seconds. Anything not named here gets _DEFAULT_TIMEOUT.
# These bound a DEADLOCK, so they sit well above the honest worst case: the SU2 solves in
# validate8 took 150-900 s, and run_simulation is called with timeout_seconds=300.
_DEFAULT_TIMEOUT = 900.0
_TOOL_TIMEOUTS: dict[str, float] = {
    "run_su2_solver": 3600.0,
    "run_simulation": 1800.0,
    "estimate_mass": 1800.0,
    "generate_volume_mesh": 1800.0,
    "export_component_mesh": 1800.0,
    "run_cycle": 1200.0,
    # B3: a new cycle's off-design deck took 32-51 min on 12 workers (ws8, 2026-10-31); cached ones return at once.
    "generate_engine_deck": 7200.0,
}

_lock = threading.Lock()
_stats: dict[str, Any] = {"timeouts": {}, "calls_watched": 0}
# One shared pool: a timed-out call keeps its thread (we cannot kill it), so the pool must
# be allowed to grow rather than starve the next call of a worker.
_pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="avion-tool-call")


class ToolCallTimeout(RuntimeError):
    """A tool did not answer within its budget: treated as a tool failure, not a hang."""


def timeout_for(tool_name: str) -> float:
    return _TOOL_TIMEOUTS.get(tool_name, _DEFAULT_TIMEOUT)


def configure(default_seconds: float | None = None, per_tool: dict | None = None) -> None:
    global _DEFAULT_TIMEOUT
    with _lock:
        if default_seconds:
            _DEFAULT_TIMEOUT = float(default_seconds)
        if per_tool:
            _TOOL_TIMEOUTS.update({str(k): float(v) for k, v in per_tool.items()})


def call(tool_name: str, fn: Callable, *args, **kwargs):
    """Run one tool call under its budget. Raises ToolCallTimeout instead of hanging."""
    budget = timeout_for(tool_name)
    with _lock:
        _stats["calls_watched"] += 1
    from src.logging import step_log

    import contextvars

    # B267: the caller's context (agent identity, the heartbeat's not_recorded) travels into the worker.
    future = _pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
    try:
        result = future.result(timeout=budget)
        step_log.record_tool_call(tool_name, kwargs, result)
        return result
    except FutureTimeout:
        step_log.record_tool_call(tool_name, kwargs, error=TimeoutError(f"no answer within {budget:.0f}s"))
        future.cancel()          # it is already running, so this only stops a queued call
        with _lock:
            _stats["timeouts"][tool_name] = _stats["timeouts"].get(tool_name, 0) + 1
        dump_stacks(f"{tool_name}: no answer within {budget:.0f}s")      # B267
        logger.error("[B85] %s did not answer within %.0fs -- treating it as a failed call so "
                     "the run continues instead of wedging", tool_name, budget)
        raise ToolCallTimeout(
            f"{tool_name} did not return within {budget:.0f}s. The call was abandoned so the run "
            "could continue (B85). The server may still be working; do not simply repeat the "
            "call -- check the design knowledge base for what has already been produced."
        ) from None
    except Exception as exc:
        step_log.record_tool_call(tool_name, kwargs, error=exc)
        raise


# -- B267: the whole call, not only the server round trip -------------------------------------------
#
# l2c seq_graph (ws5, 2026-11-10): the su2 server was wedged (its log parser spinning on a 155 MB line).
# call() above released run_su2_solver x3 and list_result_files on time; the next get_su2_status then
# waited 3 h with no timeout, no log line and 52 of 59 threads parked on locks. call() bounds only the
# server call; the framework's own work before it (argument resolution, which can call tools itself,
# and the guards) had no bound. bounded() puts the WHOLE middleware call under one, a margin above the
# tool's own budget so call() still fires first when the server is the one that is slow, and every
# timeout now writes every thread's stack beside the link's files, so the next escape is located, not
# inferred.
_OUTER_MARGIN_SECONDS = 300.0
_outer_pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="avion-tool-outer")


def dump_stacks(reason: str) -> str | None:
    """Every thread's Python stack, appended to hang_stacks.txt in the link folder. Never raises."""
    import faulthandler
    import os
    import time

    try:
        from src.tools.design_json import design_path

        path = design_path()
        path = os.path.join(os.path.dirname(path), "hang_stacks.txt") if path else None
        if not path:
            return None
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%Y-%m-%dT%H:%M:%S')} {reason}\n")
            fh.flush()
            faulthandler.dump_traceback(file=fh, all_threads=True)
        return path
    except Exception:  # pragma: no cover - a diagnostic must never break the run
        return None


def bounded(tool_name: str, fn: Callable, *args, **kwargs):
    """Run the whole tool call (framework work + server call) under the tool's budget plus a margin."""
    import contextvars

    budget = timeout_for(tool_name) + _OUTER_MARGIN_SECONDS
    ctx = contextvars.copy_context()          # the calling agent's identity travels with the call
    future = _outer_pool.submit(ctx.run, fn, *args, **kwargs)
    try:
        return future.result(timeout=budget)
    except FutureTimeout:
        future.cancel()
        with _lock:
            key = f"{tool_name} (whole call)"
            _stats["timeouts"][key] = _stats["timeouts"].get(key, 0) + 1
        where = dump_stacks(f"{tool_name}: the whole call did not finish within {budget:.0f}s")
        logger.error("[B267] %s: the whole call did not finish within %.0fs -- released; stacks in %s",
                     tool_name, budget, where)
        raise ToolCallTimeout(
            f"{tool_name} did not finish within {budget:.0f}s and was abandoned so the run could continue "
            "(B267). Do not simply repeat the call -- check the design knowledge base for what has already "
            "been produced."
        ) from None


def stats() -> dict:
    with _lock:
        return {"tool_call_timeouts": dict(_stats["timeouts"]),
                "tool_call_timeouts_total": sum(_stats["timeouts"].values()),
                "tool_calls_watched": _stats["calls_watched"]}


def reset_stats() -> None:
    with _lock:
        _stats["timeouts"] = {}
        _stats["calls_watched"] = 0
