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


def _kb_record(tool_name: str, resolved: dict, result, *, error=None, status=None, fingerprint=None,
               note: str = ""):
    """Write an MCP tool call to the design knowledge base (B81). Never breaks a call."""
    try:
        from src.tools import data_plane
        from src.tools.knowledge_base import record_tool_result

        server = data_plane._tool_server_map.get(tool_name, "")
        if server:
            return record_tool_result(tool_name, server, resolved, result, error=error,
                                      status=status, design_fingerprint=fingerprint, note=note)
    except Exception:  # pragma: no cover - recording must never break a tool call
        pass
    return None


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
        resolve_request,
        session_creation_refusal,
        unresolved_ref_error,
    )

    original_forward = getattr(tool, "forward", None)
    if original_forward is None or not getattr(tool, "inputs", None):
        return tool  # Not an MCP tool — skip

    # B84: say it in the tool's own description, so a missing coupled input never costs a
    # wasted call -- the agent reads what is required before it calls (Jessica, 2026-09-16).
    from src.tools import coupling_contract
    coupling_contract.apply_to_tool(tool)

    def middleware_forward(*args, **kwargs):
        # 1. Type coercion
        coerced = coerce_tool_arguments(tool, kwargs)
        # 2. Resolve data store references (also injects typed coupling vars)
        resolved = resolve_request(tool.name, coerced)
        # 2a. Fail fast on a ref-shaped argument that does not resolve. Otherwise
        #     the literal key string is sent to the server and interpreted as
        #     DATA, producing an error that names the wrong problem (observed:
        #     a one-character typo in a mesh ref surfaced as "Invalid
        #     base64-encoded string", and the agent reissued the same call).
        import json as _json
        bad_ref = unresolved_ref_error(tool.name, resolved)
        if bad_ref is not None:
            return _json.dumps(bad_ref)
        # 2a'. A session the runner created is authoritative: refuse creating a
        #      replacement before it reaches the server, naming the one to use.
        refused = session_creation_refusal(tool.name, resolved)
        if refused is not None:
            _kb_record(tool.name, resolved, refused, status="refused")
            return _json.dumps(refused)
        # 2a''. B81 once-only guard: refuse repeating work already done for this
        #       design (once per agent), before the call reaches the server.
        from src.tools import duplicate_guard
        from src.tools.agent_context import current_agent_name

        _fp = duplicate_guard.fingerprint(tool.name, resolved)
        _guard = duplicate_guard.check(tool.name, _fp, current_agent_name())
        _deliberate = bool(_guard and _guard.get("_deliberate_repeat"))
        if _guard is not None and not _deliberate:
            _kb_record(tool.name, resolved, _guard, status="refused", fingerprint=_fp)
            return _json.dumps(_guard)
        # 2a''''. B86: a claim must actually RESERVE the work. claim_todo is atomic but
        #        nothing in the tool path ever read it, so two peers created their own SU2
        #        session for the same design three minutes apart and neither was refused.
        #        A claimed TODO stays reserved until its holder releases it with
        #        mark_todo_done(result=<summary>) or mark_todo_failed. Unclaimed work
        #        auto-claims on first touch, so a peer is never blocked by its own diligence.
        from src.tools import work_claims
        # B88: a solve on a mesh-less session aborts in 2.6 s and leaves the link with no
        #      path to aero at all. Refuse before the server is touched, naming the ref.
        # B95: a mesh or export of the BASELINE while the design has moved on describes the
        #      wrong aircraft, and everything downstream inherits it. Refuse before the server.
        from src.tools import geometry_guard

        _stale = geometry_guard.stale_geometry(tool.name, resolved)
        if _stale is None:
            # B91: the same question one discipline along -- is this the design's geometry?
            _stale = geometry_guard.mass_on_baseline(tool.name, resolved)
        if _stale is not None:
            duplicate_guard.release(tool.name, _fp)
            _kb_record(tool.name, resolved, _stale, status="refused")
            return _json.dumps(_stale)
        from src.tools import mesh_guard
        _no_mesh = mesh_guard.mesh_missing(tool.name, resolved)
        if _no_mesh is not None:
            duplicate_guard.release(tool.name, _fp)
            _kb_record(tool.name, resolved, _no_mesh, status="refused")
            return _json.dumps(_no_mesh)
        _claim = work_claims.check(tool.name, current_agent_name())
        if _claim is not None:
            duplicate_guard.release(tool.name, _fp)     # nothing ran; drop any in-flight claim
            _kb_record(tool.name, resolved, _claim, status="refused")
            return _json.dumps(_claim)
        # 2b. B84: the coupled quantities are REQUIRED PARAMETERS of the mission call.
        #     Advisory hints were ignored every time (validate7 link 1: 5 aero warnings,
        #     7 mass hints, 0 estimate_mass, 0 run_cycle), and aviary's stand-in values
        #     made the answer look fine. A missing coupled input is missing DATA: the call
        #     comes back naming what is absent and the tool sequence that produces it,
        #     exactly as a missing argument would. resolve_request above has already had
        #     its chance to inject, so this fires only when the data does not exist.
        from src.tools import coupling_contract
        missing_inputs = coupling_contract.check(tool.name, resolved)
        if missing_inputs is not None:
            duplicate_guard.release(tool.name, _fp)     # nothing ran; drop any in-flight claim
            _kb_record(tool.name, resolved, missing_inputs, status="refused")
            return _json.dumps(missing_inputs)
        # 3. Call the actual tool, under a deadlock bound (B85). mcpadapt's sync bridge
        #    waits on .result() with no timeout, so a stalled event loop parks the caller
        #    forever -- validate8_net wedged for 48 minutes with every thread asleep. A
        #    call that overruns its budget becomes an ordinary tool failure, so the agent
        #    ends, its peer place is released, and the link finishes and is recorded.
        from src.tools import call_watchdog
        try:
            result = call_watchdog.call(tool.name, original_forward, *args, **resolved)
        except Exception as exc:
            duplicate_guard.release(tool.name, _fp)
            _kb_record(tool.name, resolved, None, error=exc, fingerprint=_fp)   # failures are knowledge too
            raise
        # 4. Intercept large binary responses
        result = intercept_response(tool.name, result)
        # 4'. B81: record the completed call in the design knowledge base, AFTER
        #     interception so large payloads are stored as data_store refs; then
        #     release the in-progress claim and update the tracked design state.
        _entry = _kb_record(tool.name, resolved, result, fingerprint=_fp,
                            note="deliberate repeat after an ALREADY_DONE refusal" if _deliberate else "")
        duplicate_guard.release(tool.name, _fp)
        _obs_status = (_entry or {}).get("status", "success")
        duplicate_guard.observe(tool.name, resolved, result, _obs_status, _entry)
        # 4a/4b (retired by B84): the aero warning and the optional-mass hint used to be
        #     attached here. They are unreachable now -- a mission call that reaches the
        #     server has its coupled inputs, because the contract above required them.
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
    # Give the data plane the live tool objects. It knows tool NAMES from the
    # server map, but creating a missing session means actually invoking
    # create_session -- see data_plane._auto_create_session.
    try:
        from src.tools.data_plane import register_tools

        register_tools(tools)
    except Exception:  # pragma: no cover - registration must never block loading
        pass
    for tool in tools:
        _fix_schema_types(tool)
        wrap_tool_with_middleware(tool)
    return tools
