"""Concurrent generation slots, a VRAM guard, and the summary-model registry (B82).

WHY THIS REPLACES THE LOCK (Jessica, 2026-09-16)

B80/B81 put ONE re-entrant lock around every generation (`generation_lock.py`).
It removed the CUDA OOMs, but it also serialised the networked peers, and peers
thinking simultaneously is the behaviour the networked structure exists to show.
Measured on the 7960, networked_iterative_feedback link 1: 129 min -> 333 min,
mean step 241 s -> 592 s. B80/B81 were meant to fix the OOMs and the repeated
tool calls, not concurrency.

WHAT REPLACES IT

1. Slots. `generation_slot()` admits several generations at once. The cap comes
   from `llm.max_concurrent_generations`; unset means "as many as there are
   peers" (Jessica chose uncapped, so the guard below is the real limiter).
   A cap of 1 reproduces the old serialised behaviour for debugging.
2. A VRAM guard, which targets the actual failure mode. Before a generation
   starts, the free memory of the tightest visible card is read
   (`torch.cuda.mem_get_info`); below `llm.min_free_vram_gb` the caller waits for
   running generations to finish instead of piling another one on. Waiting is
   bounded so a run can never deadlock on it.
3. A separate registry for the SMALL summary model, so knowledge-base summaries
   never compete with the agents for the big model (they cost 40.8 minutes of it
   in one link). Only an in-process TransformersModel (or an explicit test stub)
   is accepted, so summaries can never reach an API model (B79).

The B80 context budget stays: it is what actually removed the OOMs.
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Any

_DEFAULT_MAX_SLOTS = 16          # "uncapped": the VRAM guard is the real limiter
_DEFAULT_MIN_FREE_VRAM_GB = 3.0
_MAX_VRAM_WAIT_SECONDS = 300.0   # never deadlock a run on the guard
_POLL_SECONDS = 0.25

_config_lock = threading.Lock()
_state_lock = threading.Lock()
_semaphore = threading.BoundedSemaphore(_DEFAULT_MAX_SLOTS)
_slots = _DEFAULT_MAX_SLOTS
_min_free_vram_gb = _DEFAULT_MIN_FREE_VRAM_GB
_active = 0
_stats: dict[str, Any] = {
    "max_concurrent_generations": 0,
    "generation_wait_seconds": 0.0,
    "vram_guard_waits": 0,
    "vram_guard_seconds": 0.0,
    "generations": 0,
    "summary_calls_big_model": 0,     # B82 acceptance criterion: must stay 0
}

_summary_model: Any = None
_summary_model_id: str | None = None


def configure(max_concurrent: int | None = None, min_free_vram_gb: float | None = None) -> None:
    """Set the slot cap and the VRAM floor. ``max_concurrent=None`` means uncapped."""
    global _semaphore, _slots, _min_free_vram_gb
    with _config_lock:
        if min_free_vram_gb is not None:
            _min_free_vram_gb = float(min_free_vram_gb)
        want = int(max_concurrent) if max_concurrent else _DEFAULT_MAX_SLOTS
        want = max(1, want)
        if want != _slots:
            _slots = want
            _semaphore = threading.BoundedSemaphore(want)


def slots() -> int:
    return _slots


def min_free_vram_gb() -> float:
    return _min_free_vram_gb


def free_vram_gb() -> float | None:
    """Free memory on the tightest visible CUDA device, or None without CUDA."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        return min(torch.cuda.mem_get_info(i)[0] for i in range(torch.cuda.device_count())) / 2**30
    except Exception:      # pragma: no cover - a guard failure must never block a run
        return None


def _wait_for_vram() -> float:
    """Wait (bounded) until the tightest card has room. Returns seconds waited."""
    start = time.monotonic()
    while True:
        free = free_vram_gb()
        if free is None or free >= _min_free_vram_gb:
            break
        with _state_lock:
            running = _active
        if running == 0:            # nothing of ours to wait for: proceed, trimmed prompts and all
            break
        if time.monotonic() - start >= _MAX_VRAM_WAIT_SECONDS:
            break
        time.sleep(_POLL_SECONDS)
    waited = time.monotonic() - start
    if waited > 0:
        with _state_lock:
            _stats["vram_guard_waits"] += 1
            _stats["vram_guard_seconds"] = round(_stats["vram_guard_seconds"] + waited, 3)
    return waited


@contextlib.contextmanager
def generation_slot():
    """Hold a generation slot: peers generate together, but not past the VRAM floor."""
    global _active
    start = time.monotonic()
    waited_vram = _wait_for_vram()
    _semaphore.acquire()
    waited = time.monotonic() - start
    with _state_lock:
        _active += 1
        _stats["generations"] += 1
        _stats["generation_wait_seconds"] = round(_stats["generation_wait_seconds"] + waited, 3)
        if _active > _stats["max_concurrent_generations"]:
            _stats["max_concurrent_generations"] = _active
    try:
        yield
    finally:
        with _state_lock:
            _active -= 1
        _semaphore.release()
    _ = waited_vram


def active() -> int:
    with _state_lock:
        return _active


def stats() -> dict:
    with _state_lock:
        out = dict(_stats)
    out["summary_model_id"] = _summary_model_id
    out["generation_slots"] = _slots
    return out


def reset_stats() -> None:
    with _state_lock:
        _stats.update({"max_concurrent_generations": 0, "generation_wait_seconds": 0.0,
                       "vram_guard_waits": 0, "vram_guard_seconds": 0.0, "generations": 0,
                       "summary_calls_big_model": 0})


def note_summary_on_big_model() -> None:
    """Defensive counter: a summary that reached the agents' model (must never happen)."""
    with _state_lock:
        _stats["summary_calls_big_model"] += 1


# -- summary model registry (small, local, never the big model, never an API) ------------

def register_summary_model(model: Any, model_id: str | None = None) -> bool:
    """Register the SMALL local model used for knowledge-base summaries.

    In-process local models only (B79: never an API model), and never the agents'
    own big model -- summaries cost 40.8 minutes of it in one link (B82).
    """
    global _summary_model, _summary_model_id
    from smolagents.models import TransformersModel

    from src.llm.thinking_model import ThinkingModel

    if isinstance(model, ThinkingModel):
        return False
    if isinstance(model, TransformersModel) or getattr(model, "_avion_test_stub", False):
        _summary_model = model
        _summary_model_id = model_id or getattr(model, "model_id", None) or type(model).__name__
        return True
    return False


def get_summary_model() -> Any:
    return _summary_model


def clear_summary_model() -> None:
    global _summary_model, _summary_model_id
    _summary_model, _summary_model_id = None, None


def summary_model_id() -> str | None:
    return _summary_model_id
