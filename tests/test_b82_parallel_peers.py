"""B82: peers generate simultaneously again, safely, and summaries leave the big model alone.

Stubbed models only -- no real weights, no MCP server, no .env.
"""

import json
import threading
import time

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.llm import generation_slots as gs
from src.llm.thinking_model import ThinkingModel
from src.tools import knowledge_base as kbm


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {})
    kbm.configure_run(None, 0, 1, None)
    gs.clear_summary_model()
    gs.reset_stats()
    gs.configure(max_concurrent=None, min_free_vram_gb=0.0)
    yield
    gs.clear_summary_model()
    gs.configure(max_concurrent=None, min_free_vram_gb=3.0)


def _concurrency_probe(monkeypatch, hold=0.15):
    """Run 3 generations at once through the real ThinkingModel.generate; report the peak."""
    peak, active, lock = {"n": 0}, {"n": 0}, threading.Lock()

    def fake_in_slot(self, messages, stop_sequences, response_format, tools_to_call_from, **kw):
        with lock:
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
        time.sleep(hold)
        with lock:
            active["n"] -= 1
        return "ok"

    monkeypatch.setattr(ThinkingModel, "_generate_in_slot", fake_in_slot)
    model = object.__new__(ThinkingModel)
    threads = [threading.Thread(target=model.generate, args=([{"role": "user", "content": "x"}],))
               for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return peak["n"]


def test_three_peers_generate_at_the_same_time(monkeypatch):
    """The B80/B81 regression: peers were serialised. They must overlap again."""
    gs.configure(max_concurrent=3, min_free_vram_gb=0.0)
    assert _concurrency_probe(monkeypatch) >= 2
    assert gs.stats()["max_concurrent_generations"] >= 2


def test_a_cap_of_one_reproduces_the_old_serialised_behaviour(monkeypatch):
    gs.configure(max_concurrent=1, min_free_vram_gb=0.0)
    assert _concurrency_probe(monkeypatch) == 1
    assert gs.stats()["max_concurrent_generations"] == 1


def test_the_networked_strategy_opens_a_slot_per_peer():
    from src.llm.generation_slots import configure, slots

    configure(max_concurrent=3)          # what coordinator does with len(peer_names)
    assert slots() == 3
    configure(max_concurrent=7)          # the team spawned more peers
    assert slots() == 7


# ---- the race the lock was hiding ------------------------------------------------------

def test_concurrent_calls_never_flip_each_others_thinking_setting(monkeypatch):
    """Fails on the pre-B82 implementation, which mutated shared instance state."""
    from smolagents.models import TransformersModel

    seen, barrier = {}, threading.Barrier(2)

    class FakeInputs:
        shape = (1, 10)

    def fake_base(self, messages, stop_sequences=None, tools_to_call_from=None, **kw):
        who = messages[0]["content"]
        barrier.wait()                      # both threads are inside the template step
        time.sleep(0.05)
        seen[who] = self.apply_chat_template_kwargs.get("enable_thinking", "absent")
        return {"inputs": FakeInputs()}

    monkeypatch.setattr(TransformersModel, "_prepare_completion_args", fake_base)
    model = object.__new__(ThinkingModel)
    model.apply_chat_template_kwargs = {}

    def call(tag, enabled):
        with model._thinking_for_this_call(enabled):
            model._prepare_completion_args([{"role": "user", "content": tag}])

    ts = [threading.Thread(target=call, args=("thinking_on", True)),
          threading.Thread(target=call, args=("thinking_off", False))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert seen == {"thinking_on": "absent", "thinking_off": False}
    assert model.apply_chat_template_kwargs == {}          # shared state left clean


# ---- VRAM guard ------------------------------------------------------------------------

def test_a_generation_waits_when_the_tightest_card_is_low(monkeypatch):
    gs.configure(max_concurrent=3, min_free_vram_gb=3.0)
    free = {"gb": 0.5}
    monkeypatch.setattr(gs, "free_vram_gb", lambda: free["gb"])
    released = threading.Event()

    def holder():
        with gs.generation_slot():
            released.wait(2.0)

    t = threading.Thread(target=holder)
    t.start()
    while gs.active() == 0:
        time.sleep(0.01)

    started = threading.Event()

    def waiter():
        with gs.generation_slot():
            started.set()

    w = threading.Thread(target=waiter)
    w.start()
    assert not started.wait(0.4), "a generation started while VRAM was below the floor"
    free["gb"] = 12.0                                   # memory freed up
    assert started.wait(2.0), "generation did not start once VRAM was free"
    released.set(); t.join(); w.join()
    assert gs.stats()["vram_guard_waits"] >= 1 and gs.stats()["vram_guard_seconds"] > 0


def test_the_guard_never_deadlocks_when_nothing_of_ours_is_running(monkeypatch):
    gs.configure(max_concurrent=2, min_free_vram_gb=8.0)
    monkeypatch.setattr(gs, "free_vram_gb", lambda: 0.1)
    start = time.monotonic()
    with gs.generation_slot():
        pass
    assert time.monotonic() - start < 1.0      # proceeds rather than hanging the run


# ---- summary routing --------------------------------------------------------------------

class SmallStub:
    _avion_test_stub = True
    model_id = "unsloth/Qwen3-4B-Instruct-2507-bnb-4bit"

    def __init__(self):
        self.calls = []

    def generate(self, messages, **kw):
        from smolagents.models import ChatMessage

        self.calls.append(kw)
        return ChatMessage(role="assistant", content="DONE: mesh. STILL MISSING: SU2 solve.")


def _kb_with_work():
    kb = kbm.get_kb()
    kb.append(tool="generate_volume_mesh", server="tigl", agent="agent_1", role="", status="success",
              outputs={"mesh_base64": "generate_volume_mesh__mesh_base64"})
    return kb


def test_summaries_run_on_the_small_model_and_never_on_the_big_one(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("the big model must never be asked for a summary")

    monkeypatch.setattr(ThinkingModel, "generate", explode)
    small = SmallStub()
    gs.register_summary_model(small, model_id=small.model_id)
    out = kbm.summarize(_kb_with_work())
    assert out.startswith("DONE: mesh")
    assert len(small.calls) == 1 and small.calls[0]["max_new_tokens"] == kbm.SUMMARY_MAX_NEW_TOKENS
    assert gs.stats()["summary_model_id"].endswith("2507-bnb-4bit")
    assert gs.stats()["generations"] == 0            # took no slot on the big model


def test_without_a_summary_model_the_digest_is_used(monkeypatch):
    monkeypatch.setattr(ThinkingModel, "generate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("big model")))
    assert gs.get_summary_model() is None
    assert "STILL MISSING for a coupled result" in kbm.summarize(_kb_with_work())


# ---- the budget must bind ----------------------------------------------------------------

def test_one_enormous_message_is_truncated_so_the_prompt_fits(monkeypatch):
    from smolagents.models import TransformersModel

    from src.llm.context_budget import HARD_PROMPT_LIMIT_TOKENS

    class FakeInputs:
        def __init__(self, n):
            self.shape = (1, n)

    def fake_base(self, messages, stop_sequences=None, tools_to_call_from=None, **kw):
        return {"inputs": FakeInputs(sum(len(json.dumps(m, default=str)) for m in messages) // 4)}

    monkeypatch.setattr(TransformersModel, "_prepare_completion_args", fake_base)
    model = object.__new__(ThinkingModel)
    model.apply_chat_template_kwargs = {}
    # validate4's shape: trimming whole steps cannot fix one huge observation.
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"},
            {"role": "assistant", "content": "call"}, {"role": "tool-response", "content": "z" * 700_000}]
    args = model._prepare_completion_args(msgs)
    tokens = args["inputs"].shape[1]
    assert tokens <= kbm.PROMPT_BUDGET_TOKENS
    assert tokens <= HARD_PROMPT_LIMIT_TOKENS
    assert kbm.get_kb().metrics.get("message_truncations", 0) >= 1
    assert kbm.get_kb().metrics.get("prompt_over_limit_count", 0) == 0


def test_a_prompt_that_cannot_be_reduced_is_a_hard_error(monkeypatch):
    from smolagents.models import TransformersModel

    class FakeInputs:
        shape = (1, 99_000)

    monkeypatch.setattr(TransformersModel, "_prepare_completion_args",
                        lambda self, messages, **kw: {"inputs": FakeInputs()})
    model = object.__new__(ThinkingModel)
    model.apply_chat_template_kwargs = {}
    with pytest.raises(ValueError, match="above the model maximum"):
        model._prepare_completion_args([{"role": "system", "content": "sys"}])


# ---- the summary model may only ever be a small local one (B79 + B82) ----------------------

def test_an_api_model_is_never_accepted_for_summaries():
    """B79: a summary must never be able to reach a paid API model."""

    class ApiLikeModel:                      # what a LiteLLM/Anthropic model looks like here
        model_id = "claude-opus-5"

        def generate(self, messages, **kw):  # pragma: no cover - must never be called
            raise AssertionError("an API model was asked for a summary")

    assert gs.register_summary_model(ApiLikeModel()) is False
    assert gs.get_summary_model() is None
    assert "STILL MISSING for a coupled result" in kbm.summarize(_kb_with_work())


def test_the_big_agent_model_is_never_accepted_for_summaries():
    """Summaries cost 40.8 min of the 32B in one link; it must be refused outright."""
    big = object.__new__(ThinkingModel)      # a TransformersModel subclass, but ours
    assert gs.register_summary_model(big, model_id="unsloth/Qwen3-32B-bnb-4bit") is False
    assert gs.get_summary_model() is None
    assert gs.stats()["summary_calls_big_model"] == 0
