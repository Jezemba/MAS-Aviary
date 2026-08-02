"""gpt-oss / harmony tool-call parsing in ThinkingModel.

gpt-oss-20b names the function in a channel header rather than in the JSON body:

    commentary to=functions.get_wing_area json{"aircraft_name": "DLR-F25"}

so the JSON carries ONLY the arguments. _find_tool_call_json looked for an
object containing both "name" and "arguments", failed, and fell back to the
first dict it had seen — the bare argument object — raising
"Tool call needs a 'name' key. Got keys: ['aircraft_name']". That burned a
retry on essentially every gpt-oss tool call (observed live in the smoke test).

These tests pin the harmony path AND guard that models emitting the standard
shape (Qwen3 et al.) are unaffected.
"""

import pytest

from src.llm.thinking_model import _find_tool_call_json, strip_think_blocks

# Verbatim content from the live gpt-oss-20b smoke test.
REAL_GPT_OSS_OUTPUT = (
    'commentary to=functions.get_wing_area jsonanalysisWe need to respond with '
    'correct JSON tool call. Provide:\n\n{\n"name": "get_wing_area",\n'
    '"arguments": {"aircraft_name":"DLR-F25"}\n}\n\nassistantcommentary '
    'to=functions.get_wing_areajsoncommentary'
    '{"name":"get_wing_area","arguments":{"aircraft_name":"DLR-F25"}}'
)

# The failing shape: header + bare arguments, no wrapping object.
HARMONY_ARGS_ONLY = 'commentary to=functions.get_wing_area json{"aircraft_name": "DLR-F25"}'


class TestHarmonyFormat:
    def test_args_only_call_is_reconstructed(self):
        """THE regression: header supplies the name, body supplies the args."""
        out = _find_tool_call_json(HARMONY_ARGS_ONLY)
        assert out["name"] == "get_wing_area"
        assert out["arguments"] == {"aircraft_name": "DLR-F25"}

    def test_real_smoke_test_output_parses(self):
        out = _find_tool_call_json(REAL_GPT_OSS_OUTPUT)
        assert out["name"] == "get_wing_area"
        assert out["arguments"] == {"aircraft_name": "DLR-F25"}

    def test_complete_call_after_header_wins(self):
        text = 'to=functions.ignored_name {"name": "real_tool", "arguments": {"x": 1}}'
        out = _find_tool_call_json(text)
        assert out["name"] == "real_tool"
        assert out["arguments"] == {"x": 1}

    def test_singular_function_prefix(self):
        out = _find_tool_call_json('to=function.open_cpacs {"source": "/tmp/a.xml"}')
        assert out["name"] == "open_cpacs"
        assert out["arguments"] == {"source": "/tmp/a.xml"}

    def test_whitespace_around_equals(self):
        out = _find_tool_call_json('to = functions.run_su2_solver {"session_id": "abc"}')
        assert out["name"] == "run_su2_solver"

    def test_empty_arguments_object(self):
        out = _find_tool_call_json('to=functions.ping {}')
        assert out["name"] == "ping"
        assert out["arguments"] == {}

    def test_realistic_mcp_call(self):
        text = (
            'commentary to=functions.set_aircraft_parameters json'
            '{"session_id": "b32fd352", "parameters": {"Aircraft.Wing.AREA": 160.0}}'
        )
        out = _find_tool_call_json(text)
        assert out["name"] == "set_aircraft_parameters"
        assert out["arguments"]["parameters"]["Aircraft.Wing.AREA"] == 160.0


class TestStandardFormatUnaffected:
    """Qwen3 and friends emit {name, arguments} — must not regress."""

    def test_plain_call(self):
        out = _find_tool_call_json('{"name": "get_wing_area", "arguments": {"aircraft_name": "F25"}}')
        assert out["name"] == "get_wing_area"
        assert out["arguments"] == {"aircraft_name": "F25"}

    def test_call_with_surrounding_prose(self):
        text = (
            'I should look up the wing area.\n'
            '{"name": "get_wing_area", "arguments": {"aircraft_name": "F25"}}\n'
            'That will tell me the reference area.'
        )
        out = _find_tool_call_json(text)
        assert out["name"] == "get_wing_area"

    def test_think_block_with_braces_then_call(self):
        raw = (
            '<think>Maybe {"a": 1} is relevant, or {"b": 2}.</think>'
            '{"name": "ping", "arguments": {}}'
        )
        out = _find_tool_call_json(strip_think_blocks(raw))
        assert out["name"] == "ping"

    def test_no_json_at_all_raises(self):
        with pytest.raises(ValueError):
            _find_tool_call_json("I have no idea what to call here.")

    def test_bare_args_without_header_still_falls_back(self):
        """Without a harmony header there is no name to recover — preserve the
        old fallback so the caller's error message is unchanged."""
        out = _find_tool_call_json('{"aircraft_name": "F25"}')
        assert "name" not in out
