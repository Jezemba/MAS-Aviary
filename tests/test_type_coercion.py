"""Tests for type-coercion middleware (src/tools/type_coercion.py)."""

import pytest

from src.tools.type_coercion import _coerce_value, coerce_tool_arguments


class _FakeTool:
    """Minimal stand-in for a smolagents Tool with .name and .inputs."""

    def __init__(self, name: str, inputs: dict):
        self.name = name
        self.inputs = inputs


class TestCoerceValueNullHandling:
    """Verify that "null" strings get coerced to None across the various
    ways a schema can declare null acceptability."""

    def test_anyof_with_null_option(self):
        """anyOf=[{type:object}, {type:null}] — classic mcpadapt case."""
        schema = {"anyOf": [{"type": "object"}, {"type": "null"}]}
        assert _coerce_value("null", schema) is None

    def test_anyof_present_but_null_stripped(self):
        """anyOf has non-null options but null was stripped by mcpadapt."""
        schema = {"anyOf": [{"type": "object"}]}
        # bool(any_of) is True so coercion fires
        assert _coerce_value("null", schema) is None

    def test_nullable_flag(self):
        """Explicit nullable: True."""
        schema = {"type": "object", "nullable": True}
        assert _coerce_value("null", schema) is None

    def test_default_null(self):
        """Schema with explicit "default": null implies field accepts null.

        This is the create_session.initial_parameters case that broke
        in the 2026-05-11 run #3 — schema was just
        {"type": "object", "default": null} with no anyOf.
        """
        schema = {"type": "object", "default": None}
        assert _coerce_value("null", schema) is None

    def test_none_string(self):
        """Python-style 'None' should also coerce."""
        schema = {"type": "object", "default": None}
        assert _coerce_value("None", schema) is None

    def test_empty_string_when_nullable(self):
        """Empty string treated like null when null is acceptable."""
        schema = {"type": "object", "default": None}
        assert _coerce_value("", schema) is None

    def test_does_not_coerce_when_not_nullable(self):
        """Bare {"type": "object"} with no default and no anyOf — leave alone."""
        schema = {"type": "object"}
        # No anyOf, no nullable, no default=null. "null" stays a string,
        # MCP server will reject — same as pre-fix behavior.
        assert _coerce_value("null", schema) == "null"

    def test_already_none_passes_through(self):
        schema = {"type": "object", "default": None}
        assert _coerce_value(None, schema) is None


class TestCoerceValueJsonStrings:
    def test_json_object_string_to_dict(self):
        schema = {"anyOf": [{"type": "object"}, {"type": "null"}]}
        out = _coerce_value('{"a": 1, "b": "x"}', schema)
        assert out == {"a": 1, "b": "x"}

    def test_json_array_string_to_list(self):
        schema = {"anyOf": [{"type": "array"}, {"type": "null"}]}
        out = _coerce_value('["a", "b"]', schema)
        assert out == ["a", "b"]

    def test_comma_separated_to_list(self):
        schema = {"type": "array"}
        out = _coerce_value("a, b, c", schema)
        assert out == ["a", "b", "c"]


class TestCoerceValueScalars:
    def test_true_to_bool(self):
        assert _coerce_value("true", {"type": "boolean"}) is True

    def test_false_to_bool(self):
        assert _coerce_value("false", {"type": "boolean"}) is False

    def test_integer_string(self):
        assert _coerce_value("42", {"type": "integer"}) == 42

    def test_float_string(self):
        assert _coerce_value("3.14", {"type": "number"}) == 3.14


class TestCoerceToolArguments:
    def test_create_session_null_initial_parameters(self):
        """End-to-end shape of the create_session blocker from run #3.

        Schema exactly as aviary-mcp exposes it (no anyOf, just
        default=null). LLM passes 'null' string. Coercer must rewrite
        to None.
        """
        tool = _FakeTool(
            "create_session",
            inputs={
                "initial_parameters": {
                    "additionalProperties": True,
                    "default": None,
                    "type": "object",
                }
            },
        )
        out = coerce_tool_arguments(tool, {"initial_parameters": "null"})
        assert out == {"initial_parameters": None}

    def test_mixed_args_only_offending_coerced(self):
        tool = _FakeTool(
            "set_aircraft_parameters",
            inputs={
                "session_id": {"type": "string"},
                "parameters": {"type": "object", "default": None},
            },
        )
        out = coerce_tool_arguments(
            tool,
            {"session_id": "abc-123", "parameters": "null"},
        )
        assert out == {"session_id": "abc-123", "parameters": None}

    def test_missing_inputs_passthrough(self):
        """If a tool has no inputs schema, args pass through unchanged."""
        tool = _FakeTool("foo", inputs={})
        out = coerce_tool_arguments(tool, {"x": "null"})
        assert out == {"x": "null"}
