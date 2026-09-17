"""One batching generation worker: peers really do think at the same time (B83).

WHY (the 7960's measurement, 2026-09-16)

Concurrent ``generate()`` on the single accelerate-sharded 32B instance CRASHES.
The 32B is split across GPUs 0-3 by ``device_map``; accelerate attaches per-module
hooks that move tensors between devices on every forward and keep per-module state,
so two threads inside those hooks corrupt each other and CUDA rejects the launch:

    Error while generating output: CUDA error: invalid argument

Counts per link: validate3 (no lock) 4, validate4 (exclusive lock) 0, validate5_net
(B82 slots) 2, validate6_net (B82 slots, summary model moved to GPU 2) 2. Free VRAM
was 4.5-10 GB against a 1 GB floor, so it is not memory, and moving the summary model
changed nothing. The peers that crashed died in 0.46 s against a real step of 130-185 s,
leaving ONE peer to do the whole link -- which is why B82's links still looked
serialised. **Peer parallelism has never actually worked.**

WHAT THIS DOES (Jessica chose this, 2026-09-16)

Peers keep their own threads and call ``generate()`` exactly as before. Underneath,
requests are queued and ONE worker thread runs them as a single padded batch, so the
GPU serves every peer in one forward pass and only one thread ever touches the model.
Everything that touches the model or its tokenizer -- including building the prompt
and the B80 budget trim -- happens on the worker thread, so no hook is ever re-entered.

HOW A BATCH IS GATHERED (Jessica, 2026-09-16)

Peers only arrive together on step 1; after that they drift apart by however long their
tool calls took. A 25-50 ms window would therefore batch step 1 and nothing else. So the
window is ADAPTIVE: the worker fires as soon as every LIVE peer has a request queued,
and otherwise waits at most ``batch_gather_seconds`` (5 s, ~3% of a 150 s step) for a
straggler. A lone request with no other live peer runs immediately.

Scope: networked only (Jessica, 2026-09-16). The worker is active only while the
coordinator holds peer sessions open, so sequential and orchestrated keep the direct
path they have today and their validate4 results stay comparable.
"""
from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BATCH_SIZE = 4          # peer count when the coordinator arms it
_DEFAULT_GATHER_SECONDS = 5.0        # bounded wait for a straggler peer
_IDLE_POLL_SECONDS = 0.5
# B85: 3600 s was too long to be useful -- validate8_net sat wedged for 48 minutes and was
# stopped by hand before any caller would have given up. The slowest measured step was 652 s,
# so 1800 s is ~2.7x the honest worst case and still bounds a deadlock inside one link.
_MAX_WAIT_SECONDS = 1800.0           # a caller fails loudly rather than hanging a run forever

_lock = threading.RLock()
_queue: "queue.Queue[_Request]" = queue.Queue()
_worker: threading.Thread | None = None
_running = False
_live_peers = 0
_waiting = 0
_max_batch_size = _DEFAULT_MAX_BATCH_SIZE
_gather_seconds = _DEFAULT_GATHER_SECONDS

_stats: dict[str, Any] = {
    "batches": 0,
    "batched_generations": 0,
    "max_batch_size_seen": 0,
    "batch_size_total": 0,
    "batch_gather_seconds": 0.0,
    "batch_oom_splits": 0,
    "batch_failures": 0,
}


# -- configuration and peer sessions -------------------------------------------------

def configure(max_batch_size: int | None = None, gather_seconds: float | None = None) -> None:
    """Set the batch cap and the straggler wait. ``max_batch_size=None`` keeps the default."""
    global _max_batch_size, _gather_seconds
    with _lock:
        if max_batch_size:
            _max_batch_size = max(1, int(max_batch_size))
        if gather_seconds is not None:
            _gather_seconds = max(0.0, float(gather_seconds))


def settings() -> tuple[int, float]:
    with _lock:
        return _max_batch_size, _gather_seconds


class PeerDispatch:
    """The peers of one parallel cycle, counted down as each finishes."""

    def __init__(self, count: int) -> None:
        self._remaining = count

    def release(self) -> None:
        """This peer has finished its run and will not generate again."""
        global _live_peers
        with _lock:
            if self._remaining > 0:
                self._remaining -= 1
                _live_peers = max(0, _live_peers - 1)

    def release_all(self) -> None:
        global _live_peers
        with _lock:
            _live_peers = max(0, _live_peers - self._remaining)
            self._remaining = 0


@contextlib.contextmanager
def peers_dispatched(count: int):
    """Held by the coordinator around the whole parallel cycle.

    The count is declared UP FRONT, not discovered as peer threads happen to start:
    otherwise the first request to arrive sees itself as the only live peer and fires a
    batch of one before its peers have queued anything. Each peer calls ``release()``
    when its run ends, so the worker stops waiting for peers that have finished and the
    last peer of a link never pays the gather window (Jessica, 2026-09-16).
    """
    global _live_peers
    with _lock:
        _live_peers += max(0, int(count))
    dispatch = PeerDispatch(max(0, int(count)))
    try:
        yield dispatch
    finally:
        dispatch.release_all()


def live_peers() -> int:
    with _lock:
        return _live_peers


def is_active() -> bool:
    """True while peers are dispatched: generations go through the worker (networked only)."""
    return live_peers() > 0


# -- the request ---------------------------------------------------------------------

@dataclass
class _Request:
    model: Any
    messages: list
    stop_sequences: list | None
    tools_to_call_from: list | None
    thinking: bool
    kwargs: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None
    prepared: dict | None = None
    queued_at: float = field(default_factory=time.monotonic)

    def bucket_key(self) -> tuple:
        """Requests may share a batch only when their generation settings match.

        ``enable_thinking`` is baked into the prompt tokens, but it is part of the key
        anyway: a thinking and a non-thinking request are different generations and are
        kept apart explicitly (B83 §3.1).
        """
        model_kwargs = getattr(self.model, "kwargs", {}) or {}
        max_new = (self.kwargs.get("max_new_tokens") or self.kwargs.get("max_tokens")
                   or model_kwargs.get("max_new_tokens") or model_kwargs.get("max_tokens"))
        sampling = tuple(sorted((k, _hashable(v)) for k, v in self.kwargs.items()
                                if k in ("do_sample", "temperature", "top_p", "top_k",
                                         "repetition_penalty", "num_beams")))
        return (id(self.model), bool(self.thinking), max_new,
                tuple(self.stop_sequences or ()), sampling)


def _hashable(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


# -- the public call ------------------------------------------------------------------

def submit(model: Any, messages: list, stop_sequences: list | None = None,
           tools_to_call_from: list | None = None, thinking: bool = True, **kwargs) -> Any:
    """Queue one generation and block until THIS request's row comes back.

    The caller blocks on its own request only -- never on a global lock -- so every peer
    is inside ``generate()`` at the same time, which is the behaviour the networked
    structure is meant to show.
    """
    request = _Request(model=model, messages=messages, stop_sequences=stop_sequences,
                       tools_to_call_from=tools_to_call_from, thinking=thinking, kwargs=dict(kwargs))
    _ensure_worker()
    global _waiting
    with _lock:
        _waiting += 1
    try:
        _queue.put(request)
        if not request.done.wait(_MAX_WAIT_SECONDS):
            raise TimeoutError(
                f"batched generation did not return within {_MAX_WAIT_SECONDS:.0f}s (B83). "
                "The worker thread is the only one that may touch the model, so this means "
                "it is stuck or was shut down while requests were in flight."
            )
    finally:
        with _lock:
            _waiting -= 1
    if request.error is not None:
        raise request.error
    return request.result


# -- the worker ------------------------------------------------------------------------

def _ensure_worker() -> None:
    global _worker, _running
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        _running = True
        _worker = threading.Thread(target=_worker_loop, name="avion-batch-generation", daemon=True)
        _worker.start()


def shutdown(timeout: float = 5.0) -> None:
    """Stop the worker (tests; a run never needs it -- the thread is a daemon).

    Anything still queued is failed rather than left to time out: a caller must never be
    left blocked on a worker that is no longer running.
    """
    global _worker, _running
    with _lock:
        _running = False
        worker = _worker
        _worker = None
    if worker is not None:
        worker.join(timeout)
    while True:
        try:
            pending = _queue.get_nowait()
        except queue.Empty:
            break
        pending.error = RuntimeError("the batch generation worker was shut down (B83)")
        pending.done.set()


def _worker_loop() -> None:
    while True:
        with _lock:
            if not _running:
                return
        try:
            first = _queue.get(timeout=_IDLE_POLL_SECONDS)
        except queue.Empty:
            continue
        batch = _gather(first)
        try:
            _run_batch(batch)
        except BaseException as exc:      # never let the worker die: one bad batch, one error
            logger.exception("batch generation failed")
            for request in batch:
                if not request.done.is_set():
                    request.error = exc
                    request.done.set()


def _gather(first: _Request) -> list[_Request]:
    """Collect requests for this cycle: all live peers, the cap, or the bounded window."""
    max_size, gather_seconds = settings()
    batch = [first]
    start = time.monotonic()
    while len(batch) < max_size:
        with _lock:
            expected = min(max_size, max(_live_peers, _waiting))
        if len(batch) >= expected:
            break                                   # everyone who could join has joined
        remaining = gather_seconds - (time.monotonic() - start)
        if remaining <= 0:
            break                                   # a peer is busy elsewhere; go without it
        try:
            batch.append(_queue.get(timeout=remaining))
        except queue.Empty:
            break
    waited = time.monotonic() - start
    with _lock:
        _stats["batch_gather_seconds"] = round(_stats["batch_gather_seconds"] + waited, 3)
    return batch


def _run_batch(batch: list[_Request]) -> None:
    """Prepare every prompt and run one padded generation per compatible bucket."""
    buckets: dict[tuple, list[_Request]] = {}
    for request in batch:
        try:
            request.prepared = _prepare(request)
        except BaseException as exc:      # a bad request fails alone (B83 §3.1)
            request.error = exc
            request.done.set()
            continue
        buckets.setdefault(request.bucket_key(), []).append(request)
    for rows in buckets.values():         # different buckets go in consecutive batches
        _generate_rows(rows)


def _prepare(request: _Request) -> dict:
    """Build one request's generation inputs ON THE WORKER THREAD.

    The B80 context budget runs here, per request, before batching: ``_prepare_completion_args``
    is the model's own budgeted path. Doing it here (rather than in the peer's thread) is what
    keeps the promise that only this thread ever touches the model or its tokenizer.
    """
    model = request.model
    with model._thinking_for_this_call(request.thinking):
        return model._prepare_completion_args(
            request.messages,
            stop_sequences=request.stop_sequences,
            tools_to_call_from=request.tools_to_call_from,
            **request.kwargs,
        )


def _generate_rows(rows: list[_Request]) -> None:
    """One ``model.generate`` for these rows, splitting on OOM rather than failing."""
    limit = _vram_row_limit(len(rows))
    if limit >= len(rows):
        chunks = [rows]
    else:
        chunks = [rows[i:i + limit] for i in range(0, len(rows), limit)]
    for chunk in chunks:
        try:
            _generate_one_batch(chunk)
        except Exception as exc:
            if _is_oom(exc) and len(chunk) > 1:
                # B83 §3.3: a batch of N long prompts is the new memory peak. Halve and
                # retry rather than failing every caller in it.
                with _lock:
                    _stats["batch_oom_splits"] += 1
                half = len(chunk) // 2
                _generate_rows(chunk[:half])
                _generate_rows(chunk[half:])
                continue
            with _lock:
                _stats["batch_failures"] += 1
            for request in chunk:
                if not request.done.is_set():
                    request.error = exc
                    request.done.set()


def _is_oom(exc: BaseException) -> bool:
    return "out of memory" in str(exc).lower() or type(exc).__name__ == "OutOfMemoryError"


def _vram_row_limit(wanted: int) -> int:
    """B82's VRAM guard survives as a batch-size limiter (B83 §3.2), not a serialiser."""
    try:
        from src.llm.generation_slots import free_vram_gb, min_free_vram_gb

        free = free_vram_gb()
        if free is not None and free < min_free_vram_gb():
            return 1
    except Exception:      # pragma: no cover - a guard failure must never block a run
        pass
    return wanted


def _generate_one_batch(rows: list[_Request]) -> None:
    import torch

    model = rows[0].model
    prepared = [dict(r.prepared or {}) for r in rows]
    tensors = [p.pop("inputs") for p in prepared]
    lengths = [int(t.shape[-1]) for t in tensors]
    width = max(lengths)
    pad_id = _pad_token_id(model)
    device = tensors[0].device

    # Left-padding: with a decoder-only model every row's completion then starts at the
    # same column, so the slice below cannot pick up another row's tokens.
    padded = torch.full((len(rows), width), pad_id, dtype=tensors[0].dtype, device=device)
    mask = torch.zeros((len(rows), width), dtype=torch.long, device=device)
    for i, tensor in enumerate(tensors):
        row = tensor[0] if tensor.dim() > 1 else tensor
        padded[i, width - lengths[i]:] = row
        mask[i, width - lengths[i]:] = 1

    shared = dict(prepared[0])
    shared.pop("stopping_criteria", None)
    stops = rows[0].stop_sequences
    if stops:
        shared["stopping_criteria"] = _batch_stopping_criteria(model, stops, len(rows))

    started = time.monotonic()
    out = model.model.generate(inputs=padded, attention_mask=mask, **shared)
    elapsed = time.monotonic() - started

    with _lock:
        _stats["batches"] += 1
        _stats["batched_generations"] += len(rows)
        _stats["batch_size_total"] += len(rows)
        _stats["max_batch_size_seen"] = max(_stats["max_batch_size_seen"], len(rows))
    logger.info("[B83] batch of %d rows in %.1fs (prompt widths %s)", len(rows), elapsed, lengths)

    completions = out[:, width:]
    for i, request in enumerate(rows):
        try:
            request.result = _row_to_message(model, completions[i], lengths[i], request.stop_sequences)
        except BaseException as exc:
            request.error = exc
        request.done.set()


def _pad_token_id(model: Any) -> int:
    tokenizer = getattr(model, "tokenizer", None)
    for attr in ("pad_token_id", "eos_token_id"):
        value = getattr(tokenizer, attr, None)
        if isinstance(value, int):
            return value
    return 0


def _row_to_message(model: Any, tokens: Any, prompt_tokens: int, stop_sequences: list | None):
    from smolagents.models import ChatMessage, MessageRole, TokenUsage, remove_content_after_stop_sequences

    pad_id = _pad_token_id(model)
    kept = [int(t) for t in tokens.tolist()]
    while kept and kept[-1] == pad_id:      # trailing padding after this row finished
        kept.pop()
    text = model.tokenizer.decode(kept, skip_special_tokens=True)
    if stop_sequences:
        text = remove_content_after_stop_sequences(text, stop_sequences)
    return ChatMessage(
        role=MessageRole.ASSISTANT,
        content=text,
        raw={"out": text, "batched": True},
        token_usage=TokenUsage(input_tokens=prompt_tokens, output_tokens=len(kept)),
    )


def _batch_stopping_criteria(model: Any, stop_sequences: list, batch_size: int):
    """Per-row stop handling.

    smolagents' own ``StopOnStrings`` reads ``input_ids[0]`` into one shared stream, so in a
    batch it would watch row 0 and stop every row with it. This keeps one stream per row and
    returns a per-row flag, which is what transformers expects for batched generation.
    """
    import torch
    from transformers import StoppingCriteria, StoppingCriteriaList

    tokenizer = model.tokenizer

    class _BatchStopOnStrings(StoppingCriteria):
        def __init__(self) -> None:
            self.streams = [""] * batch_size
            self.done = torch.zeros(batch_size, dtype=torch.bool)

        def reset(self) -> None:
            self.streams = [""] * batch_size
            self.done = torch.zeros(batch_size, dtype=torch.bool)

        def __call__(self, input_ids, scores, **kwargs):
            for i in range(min(batch_size, input_ids.shape[0])):
                if bool(self.done[i]):
                    continue
                self.streams[i] += tokenizer.decode(int(input_ids[i][-1]), skip_special_tokens=True)
                if any(self.streams[i].endswith(stop) for stop in stop_sequences):
                    self.done[i] = True
            return self.done.to(input_ids.device)

    return StoppingCriteriaList([_BatchStopOnStrings()])


# -- metrics ---------------------------------------------------------------------------

def stats() -> dict:
    with _lock:
        out = dict(_stats)
        out["max_batch_size"] = _max_batch_size
        out["batch_gather_window_seconds"] = _gather_seconds
    batches = out["batches"] or 0
    out["mean_batch_size"] = round(out["batch_size_total"] / batches, 2) if batches else 0.0
    return out


def reset_stats() -> None:
    with _lock:
        _stats.update({"batches": 0, "batched_generations": 0, "max_batch_size_seen": 0,
                       "batch_size_total": 0, "batch_gather_seconds": 0.0,
                       "batch_oom_splits": 0, "batch_failures": 0})
