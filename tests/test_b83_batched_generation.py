"""B83: concurrent peers are served by ONE padded batch on ONE thread.

Concurrent generate() on the accelerate-sharded 32B crashes with "CUDA error: invalid
argument" (7960, validate5_net/validate6_net), so these tests check the property that
replaces it: peers all sit inside generate() at once, and exactly one thread ever calls
the model. Stubbed models and CPU tensors only -- no real weights, no MCP server, no .env.
"""

import threading
import time

import pytest
import torch

from src.llm import batch_generation as bg
from src.llm.thinking_model import ThinkingModel


@pytest.fixture(autouse=True)
def fresh():
    bg.shutdown()
    bg.reset_stats()
    bg.configure(max_batch_size=4, gather_seconds=1.0)
    yield
    bg.shutdown()
    bg.configure(max_batch_size=4, gather_seconds=bg._DEFAULT_GATHER_SECONDS)


class StubTokenizer:
    pad_token_id = 0
    eos_token_id = 0

    def decode(self, tokens, skip_special_tokens=False):
        if isinstance(tokens, int) or getattr(tokens, "ndim", 1) == 0:
            return f"<{int(tokens)}>"
        return "".join(f"<{int(t)}>" for t in tokens)


class StubInner:
    """Stands in for the sharded transformers model: records who called it and when."""

    def __init__(self, new_tokens=3, hold=0.05):
        self.calls: list[dict] = []
        self.threads: set[str] = set()
        self.new_tokens = new_tokens
        self.hold = hold
        self.concurrent, self._active, self._lock = 0, 0, threading.Lock()

    def generate(self, inputs=None, attention_mask=None, **kwargs):
        with self._lock:
            self._active += 1
            self.concurrent = max(self.concurrent, self._active)
        self.threads.add(threading.current_thread().name)
        self.calls.append({"rows": int(inputs.shape[0]), "width": int(inputs.shape[1]),
                           "mask": attention_mask, "inputs": inputs, "kwargs": kwargs})
        time.sleep(self.hold)
        # Echo each row's last prompt token, so a caller can prove it got ITS row back.
        rows = []
        for i in range(inputs.shape[0]):
            marker = int(inputs[i, -1])
            rows.append([marker] * self.new_tokens)
        with self._lock:
            self._active -= 1
        return torch.cat([inputs, torch.tensor(rows, dtype=inputs.dtype)], dim=1)


class StubModel:
    """The bits of ThinkingModel/TransformersModel the worker touches."""

    kwargs = {"max_new_tokens": 64}

    def __init__(self, inner=None, prepare_error_for=None):
        self.model = inner or StubInner()
        self.tokenizer = StubTokenizer()
        self.prepare_error_for = prepare_error_for
        self.prepare_threads: set[str] = set()
        self.thinking_seen: list[bool] = []
        self._thinking = True

    from contextlib import contextmanager as _cm

    @_cm
    def _thinking_for_this_call(self, enabled):
        previous, self._thinking = self._thinking, enabled
        self.thinking_seen.append(enabled)
        try:
            yield
        finally:
            self._thinking = previous

    def _prepare_completion_args(self, messages, stop_sequences=None, tools_to_call_from=None, **kw):
        self.prepare_threads.add(threading.current_thread().name)
        text = messages[0]["content"]
        if self.prepare_error_for is not None and text == self.prepare_error_for:
            raise ValueError(f"bad request: {text}")
        # Prompt length varies per caller, so left-padding is genuinely exercised.
        token = abs(hash(text)) % 900 + 100
        length = 3 + len(text)
        ids = torch.tensor([[token] * (length - 1) + [token]], dtype=torch.long)
        args = {"inputs": ids, "use_cache": True, "max_new_tokens": kw.get("max_new_tokens", 64)}
        if stop_sequences:
            args["stopping_criteria"] = "single-row-criteria-that-must-be-replaced"
        return args


def _call(model, tag, results, **kw):
    def run():
        try:
            results[tag] = bg.submit(model, [{"role": "user", "content": tag}], **kw)
        except BaseException as exc:      # noqa: BLE001 - the test asserts on it
            results[tag] = exc
    return run


def _run_together(model, tags, **kw):
    """All tags submitted at once, exactly as the coordinator dispatches a parallel cycle."""
    results: dict = {}

    with bg.peers_dispatched(len(tags)) as peers:
        def one(tag):
            try:
                _call(model, tag, results, **(kw.get(tag) or kw.get("_all") or {}))()
            finally:
                peers.release()

        threads = [threading.Thread(target=one, args=(t,)) for t in tags]
        [t.start() for t in threads]
        [t.join(timeout=20) for t in threads]
        assert not any(t.is_alive() for t in threads), "a caller never came back"
    return results


# ---- the core property ---------------------------------------------------------------

def test_three_peers_are_served_by_one_padded_batch():
    model = StubModel()
    results = _run_together(model, ["peer_a", "peer_bb", "peer_ccc"])

    assert len(model.model.calls) == 1, f"expected ONE model call, got {len(model.model.calls)}"
    assert model.model.calls[0]["rows"] == 3
    assert set(results) == {"peer_a", "peer_bb", "peer_ccc"}
    for tag, message in results.items():
        assert not isinstance(message, BaseException), message
        assert message.content, f"{tag} got no completion"


def test_each_caller_gets_its_own_row_back():
    """Left-padding must not leak one peer's tokens into another peer's completion."""
    model = StubModel()
    results = _run_together(model, ["a", "bbbbbb", "cccccccccccc"])

    seen = {}
    for tag, message in results.items():
        assert not isinstance(message, BaseException), message
        seen[tag] = message.content
    assert len(set(seen.values())) == 3, f"rows leaked into each other: {seen}"
    # Each row echoes its own prompt token, and prompt lengths differ per caller.
    for tag, message in results.items():
        assert message.token_usage.input_tokens == 3 + len(tag)


def test_only_the_worker_thread_ever_touches_the_model():
    model = StubModel()
    _run_together(model, ["p1", "p2", "p3"])

    assert len(model.model.threads) == 1, model.model.threads
    assert model.model.threads.pop().startswith("avion-batch-generation")
    # Prompt building touches the tokenizer, so it must be on that thread too.
    assert len(model.prepare_threads) == 1 and next(iter(model.prepare_threads)).startswith(
        "avion-batch-generation")
    assert model.model.concurrent == 1


def test_left_padding_is_masked_per_row():
    model = StubModel()
    _run_together(model, ["a", "bbbb"])

    call = model.model.calls[0]
    mask, inputs = call["mask"], call["inputs"]
    assert mask.shape == inputs.shape
    for i in range(mask.shape[0]):
        real = int(mask[i].sum())
        assert mask[i, :-real].sum() == 0, "padding is on the left and masked out"
        assert mask[i, -real:].min() == 1, "the real tokens are all attended to"


# ---- bucketing -------------------------------------------------------------------------

def test_different_thinking_settings_never_share_a_batch():
    model = StubModel()
    results: dict = {}

    with bg.peers_dispatched(2) as peers:
        def one(tag, thinking):
            try:
                results[tag] = bg.submit(model, [{"role": "user", "content": tag}], thinking=thinking)
            finally:
                peers.release()

        threads = [threading.Thread(target=one, args=("think_on", True)),
                   threading.Thread(target=one, args=("think_off", False))]
        [t.start() for t in threads]
        [t.join(timeout=20) for t in threads]

    assert len(model.model.calls) == 2, "thinking and non-thinking rows were mixed"
    assert all(c["rows"] == 1 for c in model.model.calls)
    assert sorted(model.thinking_seen) == [False, True]


def test_different_max_new_tokens_never_share_a_batch():
    model = StubModel()
    results: dict = {}

    with bg.peers_dispatched(2) as peers:
        def one(tag, n):
            try:
                results[tag] = bg.submit(model, [{"role": "user", "content": tag}], max_new_tokens=n)
            finally:
                peers.release()

        threads = [threading.Thread(target=one, args=("short", 16)),
                   threading.Thread(target=one, args=("long", 512))]
        [t.start() for t in threads]
        [t.join(timeout=20) for t in threads]

    assert len(model.model.calls) == 2
    assert {c["kwargs"]["max_new_tokens"] for c in model.model.calls} == {16, 512}


# ---- isolation and back-pressure ---------------------------------------------------------

def test_one_bad_request_fails_alone():
    model = StubModel(prepare_error_for="poison")
    results = _run_together(model, ["good_1", "poison", "good_2"])

    assert isinstance(results["poison"], ValueError)
    for tag in ("good_1", "good_2"):
        assert not isinstance(results[tag], BaseException), results[tag]
        assert results[tag].content
    assert len(model.model.calls) == 1 and model.model.calls[0]["rows"] == 2


def test_a_failing_generation_never_kills_the_worker():
    class Exploding(StubInner):
        def generate(self, inputs=None, attention_mask=None, **kwargs):
            raise RuntimeError("CUDA error: invalid argument")

    model = StubModel(inner=Exploding())
    results = _run_together(model, ["x", "y"])
    assert all(isinstance(r, RuntimeError) for r in results.values())

    healthy = StubModel()
    again = _run_together(healthy, ["z1", "z2"])
    assert all(not isinstance(r, BaseException) for r in again.values()), "the worker died"


def test_back_pressure_runs_several_batches_and_starves_nobody():
    bg.configure(max_batch_size=2, gather_seconds=1.0)
    model = StubModel()
    tags = [f"caller_{i}" for i in range(5)]
    results = _run_together(model, tags)

    assert set(results) == set(tags)
    assert all(not isinstance(r, BaseException) for r in results.values())
    assert all(c["rows"] <= 2 for c in model.model.calls)
    assert sum(c["rows"] for c in model.model.calls) == 5
    assert len(model.model.calls) == 3


# ---- the adaptive window -------------------------------------------------------------------

def test_a_lone_request_with_no_other_live_peer_runs_immediately():
    """Jessica, 2026-09-16: nobody can join, so waiting the window would be pure latency."""
    bg.configure(max_batch_size=4, gather_seconds=30.0)     # would hang if it waited
    model = StubModel()
    started = time.monotonic()
    with bg.peers_dispatched(1):
        message = bg.submit(model, [{"role": "user", "content": "alone"}])
    assert message.content
    assert time.monotonic() - started < 5.0


def test_the_window_is_not_paid_once_a_peer_has_finished():
    """A peer that finished its run is no longer waited for, so the last peer runs on."""
    bg.configure(max_batch_size=3, gather_seconds=30.0)
    model = StubModel()
    with bg.peers_dispatched(3) as peers:
        peers.release()                            # two peers finished without generating
        peers.release()
        started = time.monotonic()
        message = bg.submit(model, [{"role": "user", "content": "last_peer"}])
    assert message.content
    assert time.monotonic() - started < 5.0, "the last peer paid the gather window"


def test_a_straggler_is_waited_for_within_the_window():
    bg.configure(max_batch_size=2, gather_seconds=10.0)
    model = StubModel()
    results: dict = {}

    with bg.peers_dispatched(2) as peers:
        def early():
            try:
                _call(model, "early", results)()
            finally:
                peers.release()

        def late():
            try:
                time.sleep(0.6)                    # still doing its tool call
                _call(model, "late", results)()
            finally:
                peers.release()

        threads = [threading.Thread(target=late), threading.Thread(target=early)]
        [t.start() for t in threads]
        [t.join(timeout=20) for t in threads]

    assert len(model.model.calls) == 1, "the straggler was not waited for"
    assert model.model.calls[0]["rows"] == 2


# ---- per-row stop sequences ------------------------------------------------------------------

def test_stop_sequences_are_applied_to_the_right_row():
    class Scripted(StubInner):
        """Row 0 emits a stop sequence mid-text; row 1 does not."""

        def generate(self, inputs=None, attention_mask=None, **kwargs):
            self.calls.append({"rows": int(inputs.shape[0]), "width": int(inputs.shape[1]),
                               "mask": attention_mask, "inputs": inputs, "kwargs": kwargs})
            self.threads.add(threading.current_thread().name)
            return torch.cat([inputs, torch.tensor([[1, 2, 3], [4, 5, 6]], dtype=inputs.dtype)], dim=1)

    model = StubModel(inner=Scripted())
    model.tokenizer = StubTokenizer()
    results = _run_together(model, ["row0", "row1"], _all={"stop_sequences": ["<2>"]})

    texts = sorted(m.content for m in results.values())
    assert texts == ["<1>", "<4><5><6>"], texts       # only the row that emitted it is cut


def test_the_batch_criteria_replace_the_single_row_ones():
    """smolagents' StopOnStrings reads input_ids[0] only: in a batch it would stop everyone."""
    model = StubModel()
    _run_together(model, ["a", "b"], _all={"stop_sequences": ["NEVER"]})

    criteria = model.model.calls[0]["kwargs"].get("stopping_criteria")
    assert criteria is not None and not isinstance(criteria, str), "the single-row criteria leaked in"
    probe = torch.tensor([[7, 8], [9, 10]])
    flags = criteria[0](probe, None)
    assert hasattr(flags, "shape") and tuple(flags.shape) == (2,), "stops must be per row"


# ---- metrics ------------------------------------------------------------------------------------

def test_the_batch_metrics_are_recorded():
    model = StubModel()
    _run_together(model, ["m1", "m2", "m3"])

    stats = bg.stats()
    assert stats["batches"] == 1
    assert stats["batched_generations"] == 3
    assert stats["mean_batch_size"] == 3.0
    assert stats["max_batch_size_seen"] == 3
    assert stats["batch_failures"] == 0


# ---- scope: networked only ------------------------------------------------------------------------

def test_the_worker_is_inactive_when_no_peers_are_dispatched():
    """Sequential and orchestrated keep the direct path, so validate4 stays comparable."""
    assert bg.is_active() is False
    with bg.peers_dispatched(3):
        assert bg.is_active() is True
    assert bg.is_active() is False


def test_generate_takes_the_direct_path_outside_a_peer_session(monkeypatch):
    from smolagents.models import TransformersModel

    seen = {}

    def fake_base(self, messages, **kw):
        seen["direct"] = True
        return "direct-answer"

    monkeypatch.setattr(TransformersModel, "generate", fake_base)
    monkeypatch.setattr(bg, "submit", lambda *a, **k: pytest.fail("the worker was used"))
    model = object.__new__(ThinkingModel)
    assert model._generate_once([{"role": "user", "content": "x"}]) == "direct-answer"
    assert seen["direct"] is True
