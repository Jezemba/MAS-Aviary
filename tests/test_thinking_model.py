"""Tests for ThinkingModel — think-block stripping and robust JSON parsing."""

import json

import pytest

from src.llm.thinking_model import ThinkingModel, _find_tool_call_json, strip_think_blocks

# ---------------------------------------------------------------------------
# strip_think_blocks
# ---------------------------------------------------------------------------


class TestStripThinkBlocks:
    def test_removes_single_block(self):
        text = "<think>reasoning here</think>answer"
        assert strip_think_blocks(text) == "answer"

    def test_removes_multiple_blocks(self):
        text = "<think>a</think>mid<think>b</think>end"
        assert strip_think_blocks(text) == "midend"

    def test_removes_multiline_block(self):
        text = "<think>\nline1\nline2\n{braces}\n</think>\nresult"
        assert strip_think_blocks(text) == "result"

    def test_no_think_blocks_unchanged(self):
        text = '{"name": "final_answer", "arguments": {"answer": "42"}}'
        assert strip_think_blocks(text) == text

    def test_empty_think_block(self):
        text = "<think></think>payload"
        assert strip_think_blocks(text) == "payload"

    def test_nested_angle_brackets_in_think(self):
        text = "<think>if x < 10 and y > 5: pass</think>result"
        assert strip_think_blocks(text) == "result"

    def test_think_with_json_inside(self):
        """Braces inside think blocks must not leak to the parser."""
        text = (
            '<think>{"name": "wrong", "arguments": {}}</think>\n{"name": "final_answer", "arguments": {"answer": "42"}}'
        )
        cleaned = strip_think_blocks(text)
        assert "<think>" not in cleaned
        obj = json.loads(cleaned)
        assert obj["name"] == "final_answer"


# ---------------------------------------------------------------------------
# _find_tool_call_json — robust parser
# ---------------------------------------------------------------------------


class TestFindToolCallJson:
    def test_clean_json(self):
        text = '{"name": "calculator_tool", "arguments": {"expression": "2+2"}}'
        result = _find_tool_call_json(text)
        assert result["name"] == "calculator_tool"
        assert result["arguments"]["expression"] == "2+2"

    def test_json_with_preamble(self):
        text = 'Some analysis text\n{"name": "echo_tool", "arguments": {"message": "hi"}}'
        result = _find_tool_call_json(text)
        assert result["name"] == "echo_tool"

    def test_json_with_trailing_text(self):
        text = '{"name": "echo_tool", "arguments": {"message": "hi"}}\nMore text here.'
        result = _find_tool_call_json(text)
        assert result["name"] == "echo_tool"

    def test_preamble_with_braces(self):
        """Braces in analysis text before the tool call should not confuse the parser."""
        text = (
            'The expression {x: 1} is interesting.\n{"name": "calculator_tool", "arguments": {"expression": "2*3*7"}}'
        )
        result = _find_tool_call_json(text)
        assert result["name"] == "calculator_tool"
        assert result["arguments"]["expression"] == "2*3*7"

    def test_tool_call_tags(self):
        """Model wraps JSON in <tool_call> tags."""
        text = '<tool_call>\n{"name": "final_answer", "arguments": {"answer": "42"}}\n</tool_call>'
        result = _find_tool_call_json(text)
        assert result["name"] == "final_answer"

    def test_multiple_json_objects_picks_tool_call(self):
        """When multiple JSON objects exist, pick the one with name+arguments."""
        text = '{"status": "thinking"}\n{"name": "echo_tool", "arguments": {"message": "done"}}\n{"extra": true}'
        result = _find_tool_call_json(text)
        assert result["name"] == "echo_tool"
        assert result["arguments"]["message"] == "done"

    def test_nested_json_in_arguments(self):
        text = '{"name": "complex_tool", "arguments": {"data": {"nested": [1, 2, 3]}}}'
        result = _find_tool_call_json(text)
        assert result["name"] == "complex_tool"
        assert result["arguments"]["data"]["nested"] == [1, 2, 3]

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="does not contain any JSON"):
            _find_tool_call_json("No JSON here at all.")

    def test_no_json_braces_only_raises(self):
        """Malformed braces with no valid JSON should raise."""
        with pytest.raises(ValueError, match="does not contain any JSON"):
            _find_tool_call_json("Just some {broken text")

    def test_fallback_to_non_tool_call_json(self):
        """If no object has name+arguments, return the first valid one."""
        text = '{"answer": "42"}'
        result = _find_tool_call_json(text)
        assert result["answer"] == "42"

    def test_real_qwen3_think_then_tool_call(self):
        """Simulate actual Qwen3 output: <think> stripped, then tool_call JSON."""
        raw = (
            "<think>\nOkay, I need to calculate 2*3*7.\n"
            "The expression is straightforward.\n"
            "Let me use the calculator tool.\n</think>\n\n"
            '<tool_call>\n{"name": "calculator_tool", "arguments": {"expression": "2*3*7"}}\n</tool_call>'
        )
        cleaned = strip_think_blocks(raw)
        result = _find_tool_call_json(cleaned)
        assert result["name"] == "calculator_tool"
        assert result["arguments"]["expression"] == "2*3*7"

    def test_real_qwen3_think_with_braces(self):
        """Simulate Qwen3 output where think block contains JSON-like braces."""
        raw = (
            '<think>\nThe JSON should be {"name": "final_answer", "arguments": {"answer": "42"}}\n'
            "But let me check the syntax. The correct format is:\n"
            '{"name": "final_answer", "arguments": {"answer": "42"}}\n'
            "I need to ensure proper formatting.\n</think>\n\n"
            '<tool_call>\n{"name": "final_answer", "arguments": {"answer": "42"}}\n</tool_call>'
        )
        cleaned = strip_think_blocks(raw)
        result = _find_tool_call_json(cleaned)
        assert result["name"] == "final_answer"
        assert result["arguments"]["answer"] == "42"


# ---------------------------------------------------------------------------
# ThinkingModel.parse_tool_calls (integration with ChatMessage)
# ---------------------------------------------------------------------------


class TestParseToolCalls:
    """Test the override via a minimal ThinkingModel (no real GPU needed)."""

    @pytest.fixture()
    def _chat_message_cls(self):
        from smolagents.models import ChatMessage, MessageRole

        return ChatMessage, MessageRole

    def _make_message(self, content: str):
        from smolagents.models import ChatMessage, MessageRole

        return ChatMessage(role=MessageRole.USER, content=content)

    def _make_model(self):
        """Create a ThinkingModel without loading any actual weights."""
        from unittest.mock import patch

        with patch("smolagents.TransformersModel.__init__", return_value=None):
            model = ThinkingModel.__new__(ThinkingModel)
            model.tool_name_key = "name"
            model.tool_arguments_key = "arguments"
        return model

    def test_clean_tool_call(self):
        model = self._make_model()
        msg = self._make_message('{"name": "echo_tool", "arguments": {"message": "hi"}}')
        result = model.parse_tool_calls(msg)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function.name == "echo_tool"
        assert result.tool_calls[0].function.arguments == {"message": "hi"}

    def test_think_block_stripped_before_parse(self):
        model = self._make_model()
        raw = (
            '<think>Reasoning with {braces} and "quotes"</think>\n'
            '{"name": "calculator_tool", "arguments": {"expression": "6*7"}}'
        )
        msg = self._make_message(raw)
        result = model.parse_tool_calls(msg)
        assert result.tool_calls[0].function.name == "calculator_tool"
        assert "<think>" not in result.content

    def test_preamble_braces_dont_break_parse(self):
        model = self._make_model()
        text = 'Analysis: the set {2, 3, 7} are primes.\n{"name": "final_answer", "arguments": {"answer": "2, 3, 7"}}'
        msg = self._make_message(text)
        result = model.parse_tool_calls(msg)
        assert result.tool_calls[0].function.name == "final_answer"

    def test_role_set_to_assistant(self):
        model = self._make_model()
        msg = self._make_message('{"name": "echo_tool", "arguments": {"message": "x"}}')
        result = model.parse_tool_calls(msg)
        from smolagents.models import MessageRole

        assert result.role == MessageRole.ASSISTANT

    def test_existing_tool_calls_preserved(self):
        """If tool_calls already set, parse_tool_calls should not re-parse."""
        from smolagents.models import ChatMessage, ChatMessageToolCall, ChatMessageToolCallFunction, MessageRole

        model = self._make_model()
        existing_call = ChatMessageToolCall(
            id="test-id",
            type="function",
            function=ChatMessageToolCallFunction(name="echo_tool", arguments={"message": "hi"}),
        )
        msg = ChatMessage(role=MessageRole.USER, content="anything", tool_calls=[existing_call])
        result = model.parse_tool_calls(msg)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function.name == "echo_tool"

    def test_no_content_raises(self):
        model = self._make_model()
        from smolagents.models import ChatMessage, MessageRole

        msg = ChatMessage(role=MessageRole.USER, content=None)
        with pytest.raises(AssertionError, match="no content and no tool calls"):
            model.parse_tool_calls(msg)

    def test_string_arguments_parsed(self):
        """Arguments passed as JSON string should be parsed to dict."""
        model = self._make_model()
        text = '{"name": "echo_tool", "arguments": "{\\"message\\": \\"hi\\"}"}'
        msg = self._make_message(text)
        result = model.parse_tool_calls(msg)
        assert result.tool_calls[0].function.arguments == {"message": "hi"}


# ---- llm.enable_thinking reaches the model and its chat template (2026-10-12) ----

def test_loader_sets_the_thinking_flag_and_the_template_follows_it(monkeypatch):
    from src.config.loader import LLMConfig
    from src.llm import model_loader

    class _Stub:                                   # stands in for ThinkingModel: no weights are loaded
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.thinking_enabled = True

    monkeypatch.setattr(model_loader, "ThinkingModel", _Stub)
    assert model_loader._build_model(LLMConfig(model_id="m", enable_thinking=False)).thinking_enabled is False
    assert model_loader._build_model(LLMConfig(model_id="m")).thinking_enabled is True
    assert (model_loader._cache_key(LLMConfig(model_id="m", enable_thinking=False))
            != model_loader._cache_key(LLMConfig(model_id="m")))

    m = ThinkingModel.__new__(ThinkingModel)        # the real flag -> template path, without __init__
    m.apply_chat_template_kwargs = {}
    m._thinking_enabled = False
    assert m.apply_chat_template_kwargs == {"enable_thinking": False}
    m._thinking_enabled = True
    assert "enable_thinking" not in m.apply_chat_template_kwargs


# ---- Granite 4.2 XML tool calls (ibm-granite/granite-4.2-30b chat template, 2026-10-12) ----

def _bare_model():
    m = ThinkingModel.__new__(ThinkingModel)        # parse_tool_calls only needs the key names
    m.tool_name_key, m.tool_arguments_key = "name", "arguments"
    return m


def _parse(text):
    from smolagents.models import ChatMessage, MessageRole
    return _bare_model().parse_tool_calls(ChatMessage(role=MessageRole.ASSISTANT, content=text)).tool_calls[0].function


def test_granite_xml_tool_call_is_parsed():
    f = _parse("I will set the wing.\n<tool_call>\n<function=set_high_level_parameters>\n"
               "<parameter=session_id>\nabc-123\n</parameter>\n<parameter=component_uid>\nWing1\n</parameter>\n"
               '<parameter=updates>\n{"sweep": 25, "area": 130.5}\n</parameter>\n</function>\n</tool_call>')
    assert f.name == "set_high_level_parameters"
    assert f.arguments == {"session_id": "abc-123", "component_uid": "Wing1", "updates": {"sweep": 25, "area": 130.5}}


def test_granite_xml_values_keep_types_and_multiline_text():
    f = _parse("<think></think><tool_call>\n<function=final_answer>\n<parameter=answer>\nDESIGN_STATE:\n"
               "  FUEL_KG: 1\n  VERDICT: CONTINUE\n</parameter>\n</function>\n</tool_call>")
    assert f.name == "final_answer" and f.arguments == {"answer": "DESIGN_STATE:\n  FUEL_KG: 1\n  VERDICT: CONTINUE"}
    g = _parse("<tool_call>\n<function=run_simulation>\n<parameter=session_id>\ns1\n</parameter>\n"
               "<parameter=timeout_seconds>\n300\n</parameter>\n</function>\n</tool_call>")
    assert g.arguments == {"session_id": "s1", "timeout_seconds": 300}


def test_json_tool_calls_are_unchanged():
    f = _parse('<tool_call>\n{"name": "open_cpacs", "arguments": {"source_type": "path", "source": "/x.xml"}}\n</tool_call>')
    assert f.name == "open_cpacs" and f.arguments == {"source_type": "path", "source": "/x.xml"}
    with pytest.raises(ValueError):
        _parse("no tool call here")


# ---- litellm kwargs for OpenAI reasoning models (gpt-6.1-sol rejects max_tokens and temperature != 1) ----

def test_openai_reasoning_models_get_max_completion_tokens_and_no_temperature(monkeypatch):
    import smolagents

    from src.config.loader import LLMConfig
    from src.llm import model_loader

    seen = {}

    class _Stub:                                        # stands in for LiteLLMModel: no API call is made
        def __init__(self, **kwargs):
            seen.clear(); seen.update(kwargs)

    monkeypatch.setattr(smolagents, "LiteLLMModel", _Stub)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    for mid in ("openai/gpt-6.1-sol", "openai/gpt-5", "openai/o3", "gpt-6-luna"):
        model_loader._build_model(LLMConfig(model_id=mid, backend="litellm", max_new_tokens=4096, temperature=0.3))
        assert seen.get("max_completion_tokens") == 4096 and "max_tokens" not in seen and "temperature" not in seen, mid
    model_loader._build_model(LLMConfig(model_id="openai/gpt-4.1", backend="litellm", max_new_tokens=4096, temperature=0.3))
    assert seen.get("max_tokens") == 4096 and seen.get("temperature") == 0.3 and "max_completion_tokens" not in seen


def test_openai_reasoning_models_are_sent_no_stop_sequences(monkeypatch):
    """gpt-6.1-sol rejects `stop`; smolagents 1.24's own check only knows o3/o4/gpt-5*."""
    from src.config.loader import LLMConfig
    from src.llm import model_loader

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    for mid, expected in (("openai/gpt-6.1-sol", False), ("openai/gpt-5", False), ("openai/o3", False),
                          ("openai/gpt-4.1", True), ("anthropic/claude-x", True)):
        m = model_loader._build_model(LLMConfig(model_id=mid, backend="litellm", max_new_tokens=64))
        assert m.supports_stop_parameter is expected, mid
        kw = m._prepare_completion_kwargs(messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                                          stop_sequences=["Observation:"])
        assert ("stop" in kw) is expected, mid


def test_gpt6_goes_through_the_responses_api_with_the_thinking_switch(monkeypatch):
    import smolagents

    from src.config.loader import LLMConfig
    from src.llm import model_loader

    seen = {}

    class _Stub:
        def __init__(self, **kwargs):
            seen.clear(); seen.update(kwargs)

    monkeypatch.setattr(smolagents, "LiteLLMModel", _Stub)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    model_loader._build_model(LLMConfig(model_id="openai/gpt-6.1-sol", backend="litellm", enable_thinking=False))
    assert seen["model_id"] == "openai/responses/gpt-6.1-sol" and seen["reasoning_effort"] == "low"
    assert seen["allowed_openai_params"] == ["reasoning_effort"] and "temperature" not in seen
    model_loader._build_model(LLMConfig(model_id="openai/gpt-6.1-sol", backend="litellm", reasoning_effort="high"))
    assert seen["reasoning_effort"] == "high"
    model_loader._build_model(LLMConfig(model_id="openai/gpt-5", backend="litellm"))
    assert seen["model_id"] == "openai/gpt-5" and "reasoning_effort" not in seen      # gpt-5: unchanged route
    assert model_loader._is_openai_reasoning("openai/responses/gpt-6.1-sol")


# ---- Gemma 4 tool calls (google/gemma-4-31B-it chat template, format_argument macro) ----

def test_gemma4_tool_call_is_parsed():
    s = '<|"|>'
    f = _parse("<|tool_call>call:set_aircraft_parameters{parameters:{Aircraft.Wing.AREA:122.8,"
               f"Mission.Design.LIFT_COEFFICIENT:0.559}},session_id:{s}abc-1{s}}}<tool_call|>")
    assert f.name == "set_aircraft_parameters"
    assert f.arguments == {"session_id": "abc-1",
                           "parameters": {"Aircraft.Wing.AREA": 122.8, "Mission.Design.LIFT_COEFFICIENT": 0.559}}


def test_gemma4_values_strings_with_punctuation_lists_bools():
    s = '<|"|>'
    f = _parse(f"<|tool_call>call:final_answer{{answer:{s}DESIGN_STATE:\n  A: 1, B: {{x}}\n  C: [y]{s}}}<tool_call|>")
    assert f.name == "final_answer" and f.arguments == {"answer": "DESIGN_STATE:\n  A: 1, B: {x}\n  C: [y]"}
    g = _parse(f"<|tool_call>call:read_history_csv{{columns:[{s}CL{s},{s}CD{s}],max_rows:20,verbose:true,x:null}}<tool_call|>")
    assert g.arguments == {"columns": ["CL", "CD"], "max_rows": 20, "verbose": True, "x": None}
    h = _parse("<|tool_call>call:get_design_state{}<tool_call|>")
    assert h.name == "get_design_state" and h.arguments == {}


def test_decode_keeps_gemma_tool_markup_but_drops_other_special_tokens():
    from src.llm.thinking_model import _keep_tool_markup_when_decoding

    class _Tok:                                   # Gemma-like: markers are special tokens
        all_special_tokens = ["<bos>", "<|tool_call>", "<tool_call|>", '<|"|>', "<|tool_response>"]
        added_tokens_encoder, added_tokens_decoder = {}, {}
        def decode(self, ids, skip_special_tokens=False):
            raw = '<bos><|tool_call>call:f{a:<|"|>x<|"|>}<tool_call|><|tool_response>'
            return raw if not skip_special_tokens else "call:f{a:x}"

    t = _Tok(); _keep_tool_markup_when_decoding(t)
    assert t.decode([1], skip_special_tokens=True) == '<|tool_call>call:f{a:<|"|>x<|"|>}<tool_call|>'
    assert t.decode([1]) == '<bos><|tool_call>call:f{a:<|"|>x<|"|>}<tool_call|><|tool_response>'

    class _Plain:                                 # no markup tokens: decode untouched
        all_special_tokens = ["<|im_end|>"]
        added_tokens_encoder, added_tokens_decoder = {}, {}
        def decode(self, ids, skip_special_tokens=False):
            return "x"
    p = _Plain(); orig = p.decode; _keep_tool_markup_when_decoding(p)
    assert p.decode == orig
