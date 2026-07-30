"""Tool argument type coercion middleware.

smolagents' MCP adapter (mcpadapt) collapses complex JSON Schema types
(anyOf, oneOf) to "string" when the schema lacks a top-level "type" key.
This causes LLMs to send string representations of objects/nulls instead
of actual dicts/None values.

This middleware wraps MCP tools to coerce arguments before forwarding:
- JSON string → dict (when schema expects object)
- String "null"/"None" → None (when schema allows null)
- String "true"/"false" → bool
- String numbers → int/float (when schema expects number)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from smolagents import Tool

logger = logging.getLogger(__name__)


def _coerce_value(value: Any, schema: dict) -> Any:
    """Coerce a value based on its JSON Schema definition."""
    if value is None:
        return None

    # Handle anyOf (nullable types, union types)
    any_of = schema.get("anyOf", [])
    allowed_types = set()
    for option in any_of:
        t = option.get("type")
        if t:
            allowed_types.add(t)

    # Also check top-level type and original type (before schema fix)
    top_type = schema.get("type")
    if top_type and top_type != "any":
        allowed_types.add(top_type)
    orig_type = schema.get("_original_type")
    if orig_type:
        allowed_types.add(orig_type)

    # A field accepts null if any of:
    #   - "null" is one of the anyOf options or allowed types
    #   - the field is explicitly marked nullable
    #   - the field has an explicit null default (i.e. omission means null)
    #   - the schema declares type="null"
    accepts_null = (
        "null" in allowed_types
        or bool(any_of)  # anyOf present but mcpadapt may have stripped null option
        or schema.get("nullable") is True
        or ("default" in schema and schema.get("default") is None)
    )

    # String "null" / "None" / "" → None when the field accepts null
    if isinstance(value, str) and value.strip().lower() in ("null", "none", ""):
        if accepts_null:
            return None

    # JSON string → dict (when object is expected)
    if isinstance(value, str) and ("object" in allowed_types or top_type == "object"):
        stripped = value.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

    # String → list (when array is expected)
    if isinstance(value, str) and ("array" in allowed_types or top_type == "array"):
        stripped = value.strip()
        # JSON array string
        if stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
        # Comma-separated string → list
        if "," in stripped:
            return [s.strip().strip('"').strip("'") for s in stripped.split(",")]
        # Single value → wrap in list
        if stripped and stripped.lower() not in ("null", "none"):
            return [stripped]

    # String "true"/"false" → bool
    if isinstance(value, str) and ("boolean" in allowed_types or top_type == "boolean"):
        if value.strip().lower() == "true":
            return True
        if value.strip().lower() == "false":
            return False

    # String number → int/float
    if isinstance(value, str) and ("number" in allowed_types or "integer" in allowed_types):
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            pass

    return value


def coerce_tool_arguments(tool: Tool, kwargs: dict) -> dict:
    """Coerce tool arguments based on the tool's input schema.

    Two-stage behavior:

    1. Each value is type-coerced via ``_coerce_value`` (string "null" →
       None, JSON-string → dict, etc.).
    2. If the coerced value is None AND the schema declares
       ``"default": null`` (i.e. omission is the canonical null), the
       key is OMITTED from the final kwargs. This matters because some
       servers (e.g. aviary create_session) build their tool wrapper
       with strict pydantic and reject explicit None even when the
       schema says default is null — they only accept the kwarg being
       absent. Dropping the key lets the server's own default kick in.

    Args:
        tool: The smolagents Tool with .inputs schema.
        kwargs: The raw arguments from the LLM.

    Returns:
        Coerced arguments dict (may have fewer keys than input).
    """
    inputs = getattr(tool, "inputs", None)
    if not inputs:
        return kwargs

    coerced = {}
    for key, value in kwargs.items():
        schema = inputs.get(key, {})
        if isinstance(schema, dict):
            new_value = _coerce_value(value, schema)
            if new_value is not value:
                logger.debug(
                    "Coerced %s.%s: %r (%s) → %r (%s)",
                    tool.name, key,
                    value, type(value).__name__,
                    new_value, type(new_value).__name__,
                )
            # Drop None-valued kwargs whose schema marks them optional via
            # explicit null default. This works around servers that reject
            # explicit None for "optional" fields (run #3 P0 case).
            if (
                new_value is None
                and "default" in schema
                and schema.get("default") is None
            ):
                logger.debug(
                    "Dropping %s.%s — coerced to None and schema default is null",
                    tool.name, key,
                )
                continue
            coerced[key] = new_value
        else:
            coerced[key] = value

    return coerced


def _attach_coupling_hint(result, hint: str):
    """Attach a non-blocking coupling hint to a tool result (dict or JSON string).

    Adds the hint under ``coupling_hints`` without disturbing the payload, so the model
    sees the coupling opportunity in the response. Leaves non-JSON string results
    untouched (nothing safe to annotate)."""
    import json as _json

    payload = result
    was_str = False
    if isinstance(result, str):
        stripped = result.strip()
        if stripped[:1] not in "{[":
            return result  # plain text — don't mangle it
        try:
            payload = _json.loads(stripped)
        except (ValueError, TypeError):
            return result
        was_str = True
    if not isinstance(payload, dict):
        return result
    hints = payload.get("coupling_hints")
    if not isinstance(hints, list):
        hints = []
    hints.append(hint)
    payload["coupling_hints"] = hints
    return _json.dumps(payload) if was_str else payload


def wrap_tool_with_middleware(tool: Tool) -> Tool:
    """Wrap a Tool with the full middleware stack:

    1. Type coercion (fix LLM type errors)
    2. Data plane request resolution (replace refs with payloads)
    3. Original tool call
    4. Data plane response interception (store large payloads, return refs)

    Skips tools that don't have the expected attributes (e.g. mock tools).
    """
    from src.tools.data_plane import (
        intercept_response,
        mass_coupling_hint,
        mission_coupling_error,
        resolve_request,
    )

    original_forward = getattr(tool, "forward", None)
    if original_forward is None or not getattr(tool, "inputs", None):
        return tool  # Not an MCP tool — skip

    def middleware_forward(*args, **kwargs):
        # 1. Type coercion
        coerced = coerce_tool_arguments(tool, kwargs)
        # 2. Resolve data store references (also injects typed coupling vars)
        resolved = resolve_request(tool.name, coerced)
        # 2b. Coupling enforcement: if an aviary mission call still has no SU2 aero,
        #     surface an UNCOUPLED error to the model instead of running on defaults.
        #     Not a gate — the model's own coordination recovers (run SU2, retry).
        err = mission_coupling_error(tool.name, resolved)
        if err is not None:
            import json as _json
            return _json.dumps(err)
        # 3. Call the actual tool
        result = original_forward(*args, **resolved)
        # 4. Intercept large binary responses
        result = intercept_response(tool.name, result)
        # 4b. Non-blocking mass-coupling hint: if the mission runs without the structures
        #     discipline coupled, annotate the response so the model can CHOOSE to couple it
        #     (run estimate_mass) — unlike the aero error, this never blocks.
        hint = mass_coupling_hint(tool.name, resolved)
        if hint:
            result = _attach_coupling_hint(result, hint)
        return result

    tool.forward = middleware_forward
    return tool


def _fix_schema_types(tool: Tool) -> None:
    """Fix type declarations so smolagents' validation doesn't reject
    valid values before our coercion middleware runs.

    Problems fixed:
    1. mcpadapt sets type="string" for anyOf params (object|null, array|null).
    2. LLMs may send a string for array params (e.g. "fuel_burned_kg" instead of ["fuel_burned_kg"]).
    3. LLMs may send a string for boolean params.

    Strategy: set type="any" for all params where the LLM might reasonably
    send a different type than declared. Our coercion in forward() handles
    the actual conversion.
    """
    inputs = getattr(tool, "inputs", None)
    if not inputs:
        return

    for key, schema in inputs.items():
        if not isinstance(schema, dict):
            continue

        any_of = schema.get("anyOf", [])
        declared_type = schema.get("type", "")

        # anyOf params: mcpadapt corrupted type to "string"
        if any_of:
            real_types = {opt.get("type") for opt in any_of if opt.get("type")}
            if declared_type == "string" and real_types - {"string", "null"}:
                schema["type"] = "any"
                schema["nullable"] = "null" in real_types
            elif "null" in real_types:
                schema["nullable"] = True

        # Array params: LLMs often send a single string instead of an array.
        # Set to "any" so smolagents doesn't reject, our coercion wraps it.
        # Store original type so coercion knows what to convert to.
        if declared_type == "array":
            schema["type"] = "any"
            schema["_original_type"] = "array"
            schema["nullable"] = schema.get("nullable", bool(any_of))

        # Object params: LLMs may send a JSON string instead of a dict.
        if declared_type == "object":
            schema["type"] = "any"
            schema["_original_type"] = "object"
            schema["nullable"] = schema.get("nullable", bool(any_of))


def apply_coercion_to_tools(tools: list[Tool]) -> list[Tool]:
    """Apply the full middleware stack to all tools in a list.

    1. Fix corrupted schema types from mcpadapt (anyOf → "any").
    2. Wrap forward() with type coercion + data plane middleware.

    Call this after loading tools from MCP but before passing them
    to agents.  Modifies tools in-place.
    """
    for tool in tools:
        _fix_schema_types(tool)
        wrap_tool_with_middleware(tool)
    return tools
