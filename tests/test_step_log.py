"""Per-step LLM I/O log (src/logging/step_log.py) and its renderer (scripts/render_step_log.py)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from smolagents import Tool, ToolCallingAgent, TransformersModel
from smolagents.models import ChatMessage, MessageRole, Model

from src.llm.reliability import ReliabilityConfig
from src.llm.thinking_model import ThinkingModel
from src.logging import step_log

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_step_log  # noqa: E402

THINK_CALL = ('<think>\nThe span is 33.9 m; I should read the wing first.\n</think>\n\n'
              '<tool_call>\n{"name": "get_wing", "arguments": {"wing_uid": "Wing1"}}\n</tool_call>')
FINAL = '<think>\nDone.\n</think>\n{"name": "final_answer", "arguments": {"answer": "area 122"}}'


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "link" / "step_log_attempt1.jsonl"
    step_log.configure(path)
    step_log._pending().clear()
    step_log._tls.prompt = None
    yield path
    step_log.configure(None)


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# -- split_think --------------------------------------------------------------------------------

def test_split_think_separates_reasoning_from_the_call():
    out = step_log.split_think(THINK_CALL)
    assert out["think"] == "The span is 33.9 m; I should read the wing first."
    assert out["visible"].startswith("<tool_call>") and "<think>" not in out["visible"]
    assert out["think_closed"] is True


def test_split_think_without_tags_and_with_an_empty_block():
    assert step_log.split_think("plain") == {"think": None, "visible": "plain", "think_closed": True}
    assert step_log.split_think("<think>\n\n</think>\n\n{}")["think"] == ""


def test_split_think_unclosed_block_is_truncation():
    out = step_log.split_think("<think>\nstill reasoning when the token cap hit")
    assert out == {"think": "still reasoning when the token cap hit", "visible": "", "think_closed": False}


def test_split_think_closing_tag_only_and_several_blocks():
    out = step_log.split_think("opened in the template\n</think>\nanswer")
    assert out["think"] == "opened in the template" and out["visible"] == "answer"
    out = step_log.split_think("<think>a</think>x<think>b</think>y")
    assert out["think"] == "a\n\nb" and out["visible"] == "xy"


# -- writer --------------------------------------------------------------------------------------

def test_disabled_log_writes_nothing(tmp_path):
    step_log.configure(None)
    step_log.record_prompt([{"role": "user", "content": "x"}], 5)
    step_log.record_generation(ChatMessage(role=MessageRole.ASSISTANT, content=THINK_CALL), attempt=0, thinking=True)
    step_log.on_action_step(NS(step_number=1))
    assert step_log._pending() == [] and not list(tmp_path.iterdir())


def test_configure_from_env_only_when_switched_on(tmp_path, monkeypatch):
    kb = tmp_path / "repeat_000" / "combo" / "knowledge_base.jsonl"
    monkeypatch.delenv("MAS_STEP_LOG", raising=False)
    assert step_log.configure_from_env(kb, 2) is None and not step_log.enabled()
    monkeypatch.setenv("MAS_STEP_LOG", "1")
    try:
        assert step_log.configure_from_env(kb, 2) == kb.parent / "step_log_attempt2.jsonl"
        assert step_log.enabled()
    finally:
        step_log.configure(None)


def test_a_step_row_pairs_prompt_raw_text_tool_call_and_result(log_file):
    step_log.record_prompt([{"role": MessageRole.USER, "content": [{"type": "text", "text": "x" * 20000}]}], 4321)
    step_log.record_generation(ChatMessage(role=MessageRole.ASSISTANT, content=THINK_CALL), attempt=0, thinking=True)
    step = NS(step_number=3, timing=NS(start_time=1.0, end_time=2.5), error=None, is_final_answer=False,
              tool_calls=[NS(name="get_wing", arguments={"wing_uid": "Wing1"}, id="c1")],
              observations='{"area": 122.4}')
    step_log.on_action_step(step, agent=NS(name="geometry_engineer"))
    (row,) = rows(log_file)
    assert row["kind"] == "step" and row["agent"] == "geometry_engineer" and row["agent_step"] == 3
    (gen,) = row["generations"]
    assert gen["prompt"]["prompt_tokens"] == 4321
    assert gen["prompt"]["messages"][0] == {"role": "user", "content": "x" * 20000}   # never truncated
    assert "<think>" in gen["raw_text"] and gen["think"].startswith("The span")
    assert row["tool_calls"] == [{"name": "get_wing", "arguments": {"wing_uid": "Wing1"}, "id": "c1"}]
    assert row["observations"] == '{"area": 122.4}'
    assert step_log._pending() == []


def test_unclaimed_generations_are_flushed_not_lost(log_file):
    step_log.record_generation(ChatMessage(role=MessageRole.ASSISTANT, content="late answer"), attempt=0, thinking=False)
    step_log.flush_unclaimed(NS(name="mdo_integrator"))
    step_log.flush_unclaimed(NS(name="mdo_integrator"))          # nothing left: no second row
    (row,) = rows(log_file)
    assert row["kind"] == "generation_without_step" and row["generations"][0]["visible"] == "late answer"


# -- ThinkingModel hooks ------------------------------------------------------------------------

def _stub_thinking_model(outputs, monkeypatch):
    """A ThinkingModel with no weights: _generate_once replays ``outputs`` and builds its prompt
    through the real _prepare_completion_args (B80 budget) over a fake token count."""
    monkeypatch.setattr(TransformersModel, "_prepare_completion_args",
                        lambda self, messages, **kw: {"inputs": NS(shape=(1, 100 * len(messages)))})
    model = ThinkingModel.__new__(ThinkingModel)
    Model.__init__(model, model_id="stub-qwen")
    model._reliability = ReliabilityConfig(max_retries=2)
    model._thinking_enabled = True
    queue = list(outputs)

    def _generate_once(msgs, **kw):
        model._prepare_completion_args(msgs)
        return ChatMessage(role=MessageRole.ASSISTANT, content=queue.pop(0))

    model._generate_once = _generate_once
    return model


def test_think_is_logged_before_parse_strips_it_and_retries_are_kept(log_file, monkeypatch):
    model = _stub_thinking_model(["<think>hmm</think> I will not call a tool", THINK_CALL], monkeypatch)
    msg = model._generate_in_slot([{"role": "user", "content": [{"type": "text", "text": "go"}]}],
                                  None, None, None)
    assert "<think>" not in msg.content                      # what smolagents keeps
    first, second = step_log._pending()
    assert first["attempt"] == 0 and first["think"] == "hmm"
    assert second["attempt"] == 1 and "<think>" in second["raw_text"]
    assert first["prompt"]["prompt_tokens"] == 100
    assert second["prompt"]["prompt_tokens"] == 300          # retry prompt carries the failed output + error


# -- end to end: real smolagents agent through the runner's trace capture, then the renderer ----

class GetWing(Tool):
    name = "get_wing"
    description = "Read a wing summary."
    inputs = {"wing_uid": {"type": "string", "description": "wing uid"}}
    output_type = "string"

    def forward(self, wing_uid):
        return json.dumps({"wing_uid": wing_uid, "area": 122.4})


def test_agent_run_produces_one_row_per_step_and_renders_four_sections(log_file, monkeypatch):
    from src.runners.batch_runner import _install_trace_capture

    model = _stub_thinking_model([THINK_CALL, FINAL], monkeypatch)
    agent = ToolCallingAgent(tools=[GetWing()], model=model, max_steps=4, name="geometry_engineer")
    coord = NS(agents={"geometry_engineer": agent})
    _install_trace_capture(coord)
    coord._wrap_agent_for_trace("geometry_alias", agent)     # a second wrap must not double-log
    agent.run("Read Wing1")

    logged = rows(log_file)
    assert [r["kind"] for r in logged] == ["step", "step"]
    first = logged[0]
    assert first["agent"] == "geometry_engineer"
    assert first["generations"][0]["think"] == "The span is 33.9 m; I should read the wing first."
    assert first["tool_calls"][0]["name"] == "get_wing"
    assert "122.4" in first["observations"]
    assert logged[1]["tool_calls"][0]["name"] == "final_answer"

    kb = log_file.parent / "knowledge_base.jsonl"
    kb.write_text(json.dumps({"seq": 2, "attempt": 1, "agent": "geometry_engineer", "tool": "get_wing",
                              "status": "success",
                              "inputs": "{'wing_uid': 'Wing1', 'session_id': 'injected-by-middleware'}"}) + "\n")
    page = render_step_log.render(log_file, kb).read_text()
    assert page.startswith("# Step log")
    assert "## Overview" in page and "## Agent: geometry_engineer" in page
    for heading in ("#### 1. Input to the model", "#### 2. Model output", "#### 3. Tool invocation",
                    "#### 4. Tool result"):
        assert page.count(heading) == 2
    assert "> The span is 33.9 m; I should read the wing first." in page     # reasoning as prose
    assert "| **session_id** | — | `injected-by-middleware` |" in page        # added by the middleware
    assert "Repeats the previous step's response, tool call and result" in page
    assert "Nothing else is new." in page                                     # echo is not reprinted


# -- renderer helpers ---------------------------------------------------------------------------

def test_new_messages_reports_a_trimmed_prefix():
    a, b, c, d = ({"role": "user", "content": s} for s in "abcd")
    assert render_step_log.new_messages(None, [a, b]) == ([a, b], 0)
    assert render_step_log.new_messages([a, b], [a, b, c]) == ([c], 0)
    assert render_step_log.new_messages([a, b, c], [a, d]) == ([d], 2)


def test_fence_outlasts_backticks_in_the_text():
    assert render_step_log.fence("x ``` y").startswith("````\n")


def test_kb_rows_are_filtered_to_the_attempt(tmp_path):
    kb = tmp_path / "knowledge_base.jsonl"
    kb.write_text("\n".join(json.dumps({"seq": i, "attempt": a, "agent": "g", "tool": "t", "inputs": {}})
                            for i, a in ((1, 1), (2, 2), (3, 2))) + "\n")
    got = render_step_log.kb_calls_by_agent_tool(kb, attempt=2)
    assert [r["seq"] for r in got[("g", "t")]] == [2, 3]


def _step(seq, visible="", calls=None, obs="", gens=None):
    gens = gens or [{"attempt": 0, "thinking_enabled": True, "think": "t", "think_closed": True,
                     "visible": visible, "raw_text": visible, "prompt": None}]
    return {"seq": seq, "kind": "step", "agent": "a", "generations": gens,
            "tool_calls": calls or [], "observations": obs}


def test_echo_detection_hides_only_the_repeat_of_the_previous_step():
    prev = _step(1, visible="<tool_call>x</tool_call>", calls=[{"name": "t"}], obs='{"k": 1}')
    assert render_step_log.is_echo({"role": "assistant", "content": "<tool_call>x</tool_call>"}, prev)
    assert render_step_log.is_echo({"role": "tool-call", "content": "Calling tools: ..."}, prev)
    assert render_step_log.is_echo({"role": "tool-response", "content": 'Observation:\n{"k": 1}'}, prev)
    assert not render_step_log.is_echo({"role": "tool-response", "content": "Call id: 1\nError:\nNOT FINISHED"}, prev)
    assert not render_step_log.is_echo({"role": "user", "content": "new instruction"}, prev)


def test_retry_reason_reads_what_the_next_attempt_did():
    plain = {"think_closed": True, "visible": "DESIGN_STATE: ...", "thinking_enabled": True}
    assert render_step_log.retry_reason(plain, {"thinking_enabled": False}) == (
        "no tool call in the output (plain-text answer); retried with thinking OFF")
    cut = {"think_closed": False, "visible": "", "thinking_enabled": True}
    assert render_step_log.retry_reason(cut, {"thinking_enabled": False}).startswith("the output hit the token limit")
    bad = {"think_closed": True, "visible": "{oops", "thinking_enabled": True}
    assert render_step_log.retry_reason(bad, {"thinking_enabled": True}).endswith("parse error appended to the prompt")


def test_dropped_second_tool_call_is_flagged(tmp_path):
    two = "<tool_call>{}</tool_call>\n<tool_call>{}</tool_call>"
    lines, _ = render_step_log.section_tool(_step(1, visible=two, calls=[{"name": "t", "arguments": {}}]),
                                            {}, __import__("collections").defaultdict(int))
    assert any("wrote 2 tool calls; only 1 executed" in line for line in lines)


def test_handoff_summary_is_shown_under_the_agent_it_was_written_for(tmp_path):
    path = tmp_path / "step_log_attempt1.jsonl"
    summary = {"seq": 1, "kind": "summary", "agent": "unknown", "model_id": "small",
               "prompt": {"messages": [{"role": "user", "content": "records"}]}, "raw_text": "DONE: mesh",
               "think": None, "visible": "DONE: mesh", "think_closed": True}
    path.write_text(json.dumps(summary) + "\n" + json.dumps({**_step(2, obs="x"), "agent": "aero"}) + "\n")
    page = render_step_log.render(path).read_text()
    assert page.index("## Agent: aero") < page.index("### Knowledge-base handoff given to this agent")
    assert "> DONE: mesh" in page


def test_json_results_become_a_field_table():
    lines = render_step_log.section_result({"observations": '{"span": 45.3, "mac": {"x": 1}}'}, [{"status": "success"}])
    text = "\n".join(lines)
    assert "**Status:** success" in text and "| span | `45.3` |" in text and "| mac.x | `1` |" in text


def test_a_rerun_of_the_same_agent_starts_a_new_run_not_a_trimmed_prompt(tmp_path):
    def step(seq, agent_step, task):
        prompt = {"prompt_tokens": 10, "messages": [{"role": "system", "content": "sys"},
                                                     {"role": "user", "content": task}]}
        gens = [{"attempt": 0, "thinking_enabled": True, "think": "t", "think_closed": True,
                 "visible": "v", "raw_text": "v", "prompt": prompt}]
        return {**_step(seq, gens=gens), "agent": "aero", "agent_step": agent_step}

    path = tmp_path / "step_log_attempt1.jsonl"
    rows = [step(1, 1, "Run SU2"), step(2, 1, "Run SU2. FEEDBACK: the solver diverged")]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    page = render_step_log.render(path).read_text()
    assert "### Run 2 of aero" in page and "FEEDBACK: the solver diverged" in page
    assert "B80" not in page                                  # not mistaken for a trimmed prompt
    assert "| aero (run 2) |" in page
