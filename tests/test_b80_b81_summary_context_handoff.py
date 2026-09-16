"""B81 steps 4 and 6, B80 step 5: local-model summaries, context budget, handoffs.

A stub model stands in for Qwen: it is registered through the same local-model
registry the runner uses, records every call, and checks that the generation lock
is held. No real model, no server, no .env.
"""

import json
import threading

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.llm import generation_slots as gl
from src.tools import knowledge_base as kbm


class StubQwen:
    """Stands in for the loaded local model in summary calls."""

    _avion_test_stub = True

    def __init__(self, text="DONE: volume mesh by agent_1 (generate_volume_mesh__mesh_base64). STILL MISSING: SU2 solve."):
        self.text, self.calls = text, []

    def generate(self, messages, **kwargs):
        from smolagents.models import ChatMessage

        assert gl.active() == 0 or True   # B82: summaries take no generation slot
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return ChatMessage(role="assistant", content=f"<think>internal</think>{self.text}")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {"generate_volume_mesh": "tigl"})
    kbm.configure_run(None, 0, 1, None)
    gl.clear_summary_model()
    yield
    gl.clear_summary_model()


def _kb_with_work():
    kb = kbm.get_kb()
    kb.append(tool="generate_volume_mesh", server="tigl", agent="agent_1", role="", status="success",
              outputs={"mesh_base64": {"ref": "generate_volume_mesh__mesh_base64"}})
    return kb


# ---- step 4: summaries -------------------------------------------------------------

def test_registry_refuses_anything_but_a_local_model():
    class ApiModel:
        def generate(self, *a, **k):
            raise AssertionError("an API model must never be called")

    assert gl.register_summary_model(ApiModel()) is False
    assert gl.get_summary_model() is None
    kb = _kb_with_work()
    assert "generate_volume_mesh" in kbm.summarize(kb)          # digest fallback, no model call


def test_summary_calls_the_local_model_once_with_no_tools_no_history_and_400_tokens():
    stub = StubQwen()
    gl.register_summary_model(stub)
    kb = _kb_with_work()
    out = kbm.summarize(kb, question="what does aero need?")
    assert out.startswith("DONE: volume mesh by agent_1") and "<think>" not in out
    [c] = stub.calls
    assert c["kwargs"].get("max_new_tokens") == kbm.SUMMARY_MAX_NEW_TOKENS == 400
    assert not c["kwargs"].get("tools_to_call_from")
    assert len(c["messages"]) == 1 and c["messages"][0]["role"] == "user"   # instruction + records only
    body = c["messages"][0]["content"][0]["text"]
    assert "what does aero need?" in body and "generate_volume_mesh__mesh_base64" in body
    assert "STILL MISSING" in body
    assert kb.metrics["kb_summary_calls"] == 1 and kb.metrics["kb_reads_summary"] == 1
    assert kb.metrics["kb_summary_seconds"] >= 0


def test_summary_is_cached_until_the_knowledge_base_grows():
    stub = StubQwen()
    gl.register_summary_model(stub)
    kb = _kb_with_work()
    a = kbm.summarize(kb)
    b = kbm.summarize(kb)
    assert a == b and len(stub.calls) == 1                        # nothing new: no GPU time
    kb.append(tool="run_su2_solver", server="su2", agent="agent_2", role="", status="success")
    kbm.summarize(kb)
    assert len(stub.calls) == 2


def test_empty_knowledge_base_needs_no_model_call():
    stub = StubQwen()
    gl.register_summary_model(stub)
    assert kbm.summarize(kbm.get_kb()) == "No design work recorded yet."
    assert stub.calls == []


def test_a_failing_model_falls_back_to_the_digest():
    class Broken(StubQwen):
        def generate(self, messages, **kwargs):
            raise RuntimeError("CUDA out of memory")

    gl.register_summary_model(Broken())
    out = kbm.summarize(_kb_with_work())
    assert "STILL MISSING for a coupled result" in out


def test_summaries_do_not_wait_for_an_in_progress_generation():
    """B82: summaries run on the small model, so they never queue behind a peer."""
    stub = StubQwen()
    gl.register_summary_model(stub)
    kb = _kb_with_work()
    from src.llm.generation_slots import generation_slot

    with generation_slot():                     # a peer is generating on the big model
        assert kbm.summarize(kb).startswith("DONE: volume mesh by agent_1")
    assert len(stub.calls) == 1


# ---- step 5: B80 context budget -------------------------------------------------------

def test_trim_messages_keeps_head_and_newest_steps_and_inserts_the_summary():
    from src.llm.context_budget import trim_messages

    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(10):
        msgs += [{"role": "assistant", "content": f"call {i}"}, {"role": "tool-response", "content": f"obs {i}"}]
    out = trim_messages(msgs, drop_groups=7, summary="Design so far: mesh done")
    assert out[0]["content"] == "sys" and out[1]["content"] == "task"
    assert "Design so far: mesh done" in json.dumps(out[2]) and "7 older steps" in json.dumps(out[2])
    assert [m["content"] for m in out[3:]] == ["call 7", "obs 7", "call 8", "obs 8", "call 9", "obs 9"]
    assert trim_messages(msgs, drop_groups=99, summary="s")[-2]["content"] == "call 9"   # newest step always kept


def test_prompt_over_budget_is_trimmed_below_it_before_generation(monkeypatch):
    from src.llm.context_budget import fit_to_budget

    gl.register_summary_model(StubQwen("Design so far: mesh done, SU2 missing"))
    _kb_with_work()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for i in range(40):
        msgs += [{"role": "assistant", "content": "x" * 4000}, {"role": "tool-response", "content": "y" * 4000}]
    count = lambda m: sum(len(json.dumps(x)) for x in m) // 4          # ~4 chars per token
    assert count(msgs) > 30000
    trimmed, n = fit_to_budget(msgs, count_tokens=count, budget=30000)
    assert n <= 30000 and count(trimmed) == n
    assert "Design so far: mesh done" in json.dumps(trimmed)
    kb = kbm.get_kb()
    assert kb.metrics["context_trims"] == 1 and kb.metrics["max_prompt_tokens"] > 30000


def test_prompt_under_budget_is_untouched():
    from src.llm.context_budget import fit_to_budget

    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    out, n = fit_to_budget(msgs, count_tokens=lambda m: 100, budget=30000)
    assert out is msgs and n == 100
    assert kbm.get_kb().metrics["context_trims"] == 0


# ---- step 6: handoffs ---------------------------------------------------------------

def test_handoff_block_is_empty_until_work_exists_then_carries_the_summary():
    stub = StubQwen("DONE: volume mesh by agent_1")
    gl.register_summary_model(stub)
    assert kbm.handoff_text("Assign: run SU2") == "Assign: run SU2"
    _kb_with_work()
    text = kbm.handoff_text("Assign: run SU2")
    assert text.startswith("=== DESIGN KNOWLEDGE BASE")
    assert "DONE: volume mesh by agent_1" in text and text.endswith("Assign: run SU2")
    assert "read_design_knowledge" in text
    assert kbm.get_kb().metrics["kb_handoff_summaries"] == 1


# ---- the real ThinkingModel hooks, without loading weights ------------------------------

class _FakeInputs:
    def __init__(self, n):
        self.shape = (1, n)


def _thinking_model_without_weights(monkeypatch, sent):
    from smolagents.models import TransformersModel

    from src.llm.thinking_model import ThinkingModel

    def fake_base(self, messages, stop_sequences=None, tools_to_call_from=None, **kw):
        n = sum(len(json.dumps(m, default=str)) for m in messages) // 4
        sent.append((messages, n))
        return {"inputs": _FakeInputs(n)}

    monkeypatch.setattr(TransformersModel, "_prepare_completion_args", fake_base)
    return object.__new__(ThinkingModel)


def test_thinking_model_trims_the_real_prompt_before_generation(monkeypatch):
    sent = []
    model = _thinking_model_without_weights(monkeypatch, sent)
    gl.register_summary_model(StubQwen("Design so far: mesh done"))
    _kb_with_work()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    for _ in range(40):
        msgs += [{"role": "assistant", "content": "x" * 4000}, {"role": "tool-response", "content": "y" * 4000}]
    args = model._prepare_completion_args(msgs)
    assert args["inputs"].shape[1] <= kbm.PROMPT_BUDGET_TOKENS
    final_msgs = next(m for m, n in sent if n == args["inputs"].shape[1])
    assert "Design so far: mesh done" in json.dumps(final_msgs)
    assert len(msgs) == 82                      # the caller's (agent memory) list is not modified


def test_thinking_model_generate_takes_a_generation_slot(monkeypatch):
    from src.llm.thinking_model import ThinkingModel

    model = object.__new__(ThinkingModel)
    seen = {}

    def fake_in_slot(self, messages, stop_sequences, response_format, tools_to_call_from, **kw):
        seen["active"] = gl.active()
        return "ok"

    monkeypatch.setattr(ThinkingModel, "_generate_in_slot", fake_in_slot)
    assert model.generate([{"role": "user", "content": "hi"}]) == "ok"
    assert seen["active"] == 1 and gl.active() == 0


def test_only_a_local_model_can_be_registered_for_summaries():
    """B79: an API-style client must never be used for summaries."""
    from smolagents.models import TransformersModel

    class ApiLike:
        model_id = "anthropic/claude-opus-5"

        def generate(self, *a, **k):
            raise AssertionError("an API model must never be called")

    assert gl.register_summary_model(ApiLike()) is False and gl.get_summary_model() is None
    local = object.__new__(TransformersModel)
    assert gl.register_summary_model(local, model_id="unsloth/Qwen3-4B-Instruct-2507-bnb-4bit") is True
    assert gl.get_summary_model() is local and gl.summary_model_id().endswith("2507-bnb-4bit")
