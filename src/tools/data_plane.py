"""Data plane middleware — manages session IDs, file paths, large payloads,
and cross-MCP state automatically so the LLM doesn't have to.

Responsibilities:
1. **Session capture**: tool creates session → store in DesignState.sessions[mcp]
2. **Session injection**: tool needs session_id → auto-fill from DesignState
3. **CPACS path capture**: open_cpacs response → store file path in DesignState
4. **CPACS path injection**: mass tools need cpacs_file_path → auto-fill from DesignState
5. **Mesh marker capture**: mesh export → store marker names in DesignState.data_store
6. **Binary interception**: large payloads → store in data_store, return ref
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Detection heuristics ─────────────────────────────────────────────────────

_SIZE_THRESHOLD = 1024

# Structured-payload interception thresholds. Long lists and big dicts in tool
# responses (e.g. pycycle list_variables, aviary get_trajectory) bloat the
# LLM context — replace them with a ref + small preview, keep the full payload
# in DesignState.data_store so downstream tool calls can still resolve them.
_LIST_ITEM_THRESHOLD = 30
_DICT_BYTE_THRESHOLD = 2048
_LIST_PREVIEW_COUNT = 5
_DICT_KEY_PREVIEW_COUNT = 10

_BINARY_FIELD_NAMES = frozenset({
    "mesh_base64", "cad_base64", "step_base64", "stl_base64",
    "mesh_data", "cad_data", "step_data", "stl_data",
    "content_base64",
})

_BASE64_RE = re.compile(r"^[A-Za-z0-9+/=]{100,}$")

# Tools that create sessions.
_SESSION_CREATION_TOOLS = frozenset({
    "open_cpacs", "create_su2_session", "create_session", "create_cycle_model",
})

# Stateless tools — never auto-inject session_id.
_NO_SESSION_TOOLS = _SESSION_CREATION_TOOLS | frozenset({
    "ping", "get_design_space", "get_su2_status", "get_valid_config_options",
})

# Tools that need cpacs_file_path (mass-mcp tools).
_CPACS_PATH_TOOLS = frozenset({
    "estimate_mass", "validate_cpacs_inputs", "get_cpacs_mass_breakdown",
})

# Tools whose responses ARE the analytical content the agent has to
# reason about (rather than bulk binary or huge metadata trees).
# Their structured fields are NOT intercepted — the agent sees the full
# response.  Use sparingly; adding a tool here means the LLM pays the
# full token cost for its response every time it calls the tool.
#
# read_history_csv: the convergence trace IS the answer the agent needs
# to decide SOLVER_CONVERGED vs fallback.  Intercepting it hides the
# final residual and forces the agent to guess from the iter-0 jump in
# the preview.  Discovered in Run #7 (2026-05-12) — the agent reported
# "RESIDUAL_DROP_ORDERS=3.18" from the first 5 rows of a 117-row history
# that actually reached rms=-8 (8 orders) by the end.
_PASSTHROUGH_TOOLS = frozenset({
    "read_history_csv",
})


def _is_binary_payload(key: str, value: str) -> bool:
    key_lower = key.lower()
    if key_lower in _BINARY_FIELD_NAMES or key_lower.endswith("_base64"):
        return True
    if len(value) < _SIZE_THRESHOLD:
        return False
    sample = value[:200].strip()
    if _BASE64_RE.match(sample):
        return True
    if sample.startswith(("NDIME=", "solid ", "ISO-10303")):
        return True
    try:
        decoded = base64.b64decode(sample[:100] + "==", validate=False)
        if decoded[:6] in (b"NDIME=", b"solid ", b"ISO-10"):
            return True
    except Exception:
        pass
    return False


def _is_large_structured_payload(value: Any) -> bool:
    """Detect non-binary structured payloads that should be summarized.

    Triggers on:
      - lists with more than _LIST_ITEM_THRESHOLD items (e.g. pycycle
        list_variables returning a 200-item variable tree)
      - dicts whose JSON-serialized form exceeds _DICT_BYTE_THRESHOLD
        bytes (e.g. aviary get_trajectory returning a 60-point timeseries)
    """
    if isinstance(value, list) and len(value) > _LIST_ITEM_THRESHOLD:
        return True
    if isinstance(value, dict):
        try:
            return len(json.dumps(value, default=str)) > _DICT_BYTE_THRESHOLD
        except (TypeError, ValueError):
            return False
    return False


def _summarize_payload(value: Any, store_key: str) -> dict:
    """Build a compact summary of a large structured payload.

    The summary preserves enough information for the LLM to know what was
    intercepted (count, preview, ref) while keeping context cost flat.
    The full payload remains in DesignState.data_store under ``store_key``;
    downstream tools can use ``{"ref": store_key}`` to fetch it via
    resolve_request.
    """
    if isinstance(value, list):
        return {
            "_intercepted": True,
            "ref": store_key,
            "kind": "list",
            "total_count": len(value),
            "preview": value[:_LIST_PREVIEW_COUNT],
            "note": (
                f"Showing first {_LIST_PREVIEW_COUNT} of {len(value)} items. "
                f"Pass {{\"ref\": \"{store_key}\"}} downstream to use the full list."
            ),
        }
    if isinstance(value, dict):
        try:
            size = len(json.dumps(value, default=str))
        except (TypeError, ValueError):
            size = -1
        keys = list(value.keys())
        return {
            "_intercepted": True,
            "ref": store_key,
            "kind": "dict",
            "total_keys": len(keys),
            "keys_preview": keys[:_DICT_KEY_PREVIEW_COUNT],
            "size_bytes": size,
            "note": (
                f"Large dict with {len(keys)} keys ({size} bytes). "
                f"Pass {{\"ref\": \"{store_key}\"}} downstream to retrieve the full payload."
            ),
        }
    return value


# ── Global state ─────────────────────────────────────────────────────────────

_design_state = None
_tool_server_map: dict[str, str] = {}


def init_data_plane(design_state, tool_server_map: dict[str, str]) -> None:
    global _design_state, _tool_server_map
    _design_state = design_state
    _tool_server_map = dict(tool_server_map)


def get_design_state():
    return _design_state


# ── Response middleware ──────────────────────────────────────────────────────

def intercept_response(tool_name: str, response: Any) -> Any:
    """Post-process a tool response."""
    if isinstance(response, str):
        try:
            data = json.loads(response)
            if isinstance(data, dict):
                _capture_session(tool_name, data)
                _capture_cpacs_path(tool_name, data)
                _capture_mesh_markers(tool_name, data)
                _capture_aero_coefficients(tool_name, data)
                data = _intercept_binaries(tool_name, data)
                return json.dumps(data)
        except (json.JSONDecodeError, TypeError):
            pass
        if _is_binary_payload(tool_name, response) and _design_state is not None:
            key = f"{tool_name}__payload"
            _design_state.data_store[key] = response
            return json.dumps({"ref": key, "size_bytes": len(response)})
        return response

    if isinstance(response, dict):
        _capture_session(tool_name, response)
        _capture_cpacs_path(tool_name, response)
        _capture_mesh_markers(tool_name, response)
        _capture_aero_coefficients(tool_name, response)
        response = _intercept_binaries(tool_name, response)
        return response

    return response


def _capture_aero_coefficients(tool_name: str, data: dict) -> None:
    """Phase H: stash CL/CD from SU2's read_history_csv into data_store.

    The agent-driven prompt path consistently fails to plumb the SU2
    cruise CL/CD into aviary (Runs #13, #14, #15 all left aviary on
    its FLOPS defaults), so the data-plane middleware grabs the
    coefficients off the read_history_csv response — the LAST row is
    the converged state at AoA=2°, cruise Mach — and stashes them.
    ``resolve_request`` then merges them into set_aircraft_parameters
    before the call leaves the framework. Mission_architect never has
    to think about Phase H.
    """
    if _design_state is None or tool_name != "read_history_csv":
        return

    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        return
    last = rows[-1]
    if not isinstance(last, dict):
        return

    cl = last.get("CL")
    cd = last.get("CD")
    if cl is None or cd is None:
        # SU2's raw history.csv writes column headers like
        # ``       "CL"       `` (surrounding whitespace plus embedded
        # double quotes from the CSV quoting). csv.DictReader preserves
        # both, so the keys arrive as that exact literal string. Strip
        # whitespace AND embedded quote chars before matching.
        for k, v in last.items():
            if isinstance(k, str):
                ks = k.strip().strip('"').strip("'").strip()
                if ks == "CL" and cl is None:
                    cl = v
                elif ks == "CD" and cd is None:
                    cd = v

    try:
        cl_f = float(cl)
        cd_f = float(cd)
    except (TypeError, ValueError):
        return

    _design_state.data_store["aero_cl_cruise"] = cl_f
    _design_state.data_store["aero_cd_cruise"] = cd_f
    logger.info(
        "Captured aero coefficients from read_history_csv: CL=%.4f CD=%.4f",
        cl_f, cd_f,
    )


def _capture_session(tool_name: str, data: dict) -> None:
    if _design_state is None:
        return
    session_id = data.get("session_id")
    if not session_id or not isinstance(session_id, str):
        return
    mcp_name = _tool_server_map.get(tool_name)
    if not mcp_name:
        return
    if tool_name in _SESSION_CREATION_TOOLS:
        _design_state.sessions[mcp_name] = session_id
        logger.info("Captured session: sessions[%s] = %s", mcp_name, session_id)


def _capture_cpacs_path(tool_name: str, data: dict) -> None:
    """Capture the CPACS file path from open_cpacs or validate_cpacs_inputs responses."""
    if _design_state is None:
        return

    # open_cpacs: the source parameter is the file path (captured from request side)
    # But the response may contain file_name in cpacs_metadata
    if tool_name == "open_cpacs":
        metadata = data.get("cpacs_metadata", {})
        if isinstance(metadata, dict):
            fname = metadata.get("file_name")
            if fname and isinstance(fname, str) and os.path.isfile(fname):
                _design_state.cpacs_file_path = fname
                logger.info("Captured CPACS path: %s", fname)


def _capture_mesh_markers(tool_name: str, data: dict) -> None:
    """When a mesh is exported/generated, capture its marker names from the actual mesh."""
    if _design_state is None:
        return

    if tool_name not in ("export_component_mesh", "generate_volume_mesh"):
        return

    # Try to read actual marker names from the mesh data
    mesh_b64 = data.get("mesh_base64", "")
    if isinstance(mesh_b64, dict):
        # Already intercepted as ref — check stored data
        ref_key = mesh_b64.get("ref", "")
        mesh_b64 = _design_state.data_store.get(ref_key, "")

    if isinstance(mesh_b64, str) and len(mesh_b64) > 100:
        try:
            import base64
            decoded = base64.b64decode(mesh_b64)
            markers = re.findall(rb"MARKER_TAG=\s*(\S+)", decoded)
            marker_names = [m.decode() for m in markers]
            if marker_names:
                # First marker is typically the wall, last is farfield
                # Use heuristic: "farfield" if it exists, else last marker
                wall = None
                far = None
                for name in marker_names:
                    if "far" in name.lower():
                        far = name
                    elif "aircraft" in name.lower() or "wall" in name.lower() or "euler" in name.lower():
                        wall = name
                if not wall:
                    wall = marker_names[0]
                if not far and len(marker_names) > 1:
                    far = marker_names[-1]
                elif not far:
                    far = "farfield"

                _design_state.data_store["mesh_marker_wall"] = wall
                _design_state.data_store["mesh_marker_farfield"] = far
                logger.info("Captured mesh markers from data: wall=%s, farfield=%s", wall, far)
                return
        except Exception:
            pass

    # Fallback: use standard names from our gmsh meshing
    _design_state.data_store["mesh_marker_wall"] = "aircraft"
    _design_state.data_store["mesh_marker_farfield"] = "farfield"
    logger.info("Using default mesh markers: aircraft, farfield")


def _intercept_binaries(tool_name: str, data: dict) -> dict:
    if _design_state is None:
        return data
    # Passthrough tools — return the response as-is. The agent needs to
    # see the full structured content to do its job (e.g. read the final
    # rows of a convergence history to decide convergence).
    if tool_name in _PASSTHROUGH_TOOLS:
        return data
    result = {}
    for key, value in data.items():
        # Binary strings (base64 meshes, CAD, raw mesh signatures)
        if isinstance(value, str) and _is_binary_payload(key, value):
            store_key = f"{tool_name}__{key}"
            _design_state.data_store[store_key] = value
            logger.info("Stored binary %s (%d bytes)", store_key, len(value))
            result[key] = {"ref": store_key, "size_bytes": len(value)}
        # Large structured payloads (long lists, big nested dicts)
        elif _is_large_structured_payload(value):
            store_key = f"{tool_name}__{key}"
            _design_state.data_store[store_key] = value
            count = len(value) if isinstance(value, (list, dict)) else "?"
            logger.info("Stored structured %s (%s items/keys)", store_key, count)
            result[key] = _summarize_payload(value, store_key)
        # Recurse into nested dicts that didn't trip a top-level intercept
        elif isinstance(value, dict):
            result[key] = _intercept_binaries(tool_name, value)
        else:
            result[key] = value
    return result


# ── Request middleware ───────────────────────────────────────────────────────

def _extract_ref_key(value) -> str | None:
    """Recognize the ways an LLM might serialize a `{"ref": "key"}` dict
    and return the underlying key, or None if value is not a ref shape.

    Handles all observed patterns:
      • dict:        {"ref": "key", "size_bytes": 123}
      • bare string: "key"
      • prefixed:    "ref:key"  (with arbitrary surrounding whitespace)
      • JSON-string: '{"ref": "key"}'  (mcpadapt anyOf collapse)

    Returns the key as a plain string in all cases, or None when the
    value is not recognizable as a ref. ``None`` lets the caller decide
    whether to treat the value as a literal or fall through.
    """
    # dict form
    if isinstance(value, dict) and "ref" in value:
        return value["ref"] if isinstance(value["ref"], str) else None

    if not isinstance(value, str):
        return None

    stripped = value.strip()

    # prefixed string form: "ref:keyname" with arbitrary whitespace
    if stripped.lower().startswith("ref:"):
        return stripped[4:].strip() or None

    # JSON-stringified dict form: '{"ref": "keyname"}'
    if stripped.startswith("{") and "ref" in stripped:
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict) and isinstance(parsed.get("ref"), str):
                return parsed["ref"]
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def resolve_request(tool_name: str, kwargs: dict) -> dict:
    """Pre-process tool call arguments — resolve data-store refs that
    the LLM may have produced in any of several formats (see
    _extract_ref_key), inject session_id and cpacs_file_path."""
    resolved = {}
    mcp_name = _tool_server_map.get(tool_name, "")

    for key, value in kwargs.items():
        ref_key = _extract_ref_key(value)
        if ref_key is not None and _design_state and ref_key in _design_state.data_store:
            payload = _design_state.data_store[ref_key]
            logger.info(
                "Resolved ref '%s' (from %r) for %s.%s",
                ref_key, value if not isinstance(value, str) else value[:40],
                tool_name, key,
            )
            resolved[key] = payload
            continue

        # Bare string that happens to BE a data store key.
        if isinstance(value, str) and _design_state and value in _design_state.data_store:
            payload = _design_state.data_store[value]
            logger.info("Resolved string ref %s for %s.%s", value, tool_name, key)
            resolved[key] = payload
            continue

        # Not a ref — pass through unchanged (including unrecognized
        # "ref:nonexistent" strings, so the underlying server errors
        # loudly rather than the middleware silently swapping in a
        # different payload).
        resolved[key] = value

    # Auto-inject session_id
    if "session_id" not in resolved and _design_state and mcp_name:
        stored_sid = _design_state.sessions.get(mcp_name)
        if stored_sid and tool_name not in _NO_SESSION_TOOLS:
            resolved["session_id"] = stored_sid
            logger.info("Auto-injected session_id for %s from sessions[%s]", tool_name, mcp_name)

    # Auto-inject cpacs_file_path for mass-mcp tools
    if tool_name in _CPACS_PATH_TOOLS and _design_state:
        cpacs_arg = resolved.get("cpacs_file_path", "")
        real_path = _design_state.cpacs_file_path
        # If agent provided a path that doesn't exist, replace with real one
        if real_path and (not cpacs_arg or not os.path.isfile(str(cpacs_arg))):
            if cpacs_arg and cpacs_arg != real_path:
                logger.info(
                    "Replaced bad cpacs_file_path '%s' with '%s' for %s",
                    cpacs_arg, real_path, tool_name,
                )
            resolved["cpacs_file_path"] = real_path

    # Capture cpacs path from open_cpacs REQUEST (the source param)
    if tool_name == "open_cpacs" and _design_state:
        source = resolved.get("source", "")
        source_type = resolved.get("source_type", "")
        if source_type == "path" and isinstance(source, str) and os.path.isfile(source):
            _design_state.cpacs_file_path = source
            logger.info("Captured CPACS path from open_cpacs request: %s", source)

    # Auto-fix SU2 marker names in update_config_entries
    if tool_name == "update_config_entries" and _design_state:
        updates = resolved.get("updates")
        if isinstance(updates, dict):
            wall_marker = _design_state.data_store.get("mesh_marker_wall", "aircraft")
            far_marker = _design_state.data_store.get("mesh_marker_farfield", "farfield")
            _fix_su2_markers(updates, wall_marker, far_marker)

    # Phase H: merge SU2 CL/CD into set_aircraft_parameters
    if tool_name == "set_aircraft_parameters" and _design_state:
        _inject_phase_h_aero(resolved)

    return resolved


def _inject_phase_h_aero(resolved: dict) -> None:
    """Merge SU2 cruise CL/CD into a set_aircraft_parameters call.

    Adds two keys to the ``parameters`` dict if they are not already
    set by the agent:

      Mission.Design.LIFT_COEFFICIENT          = aero_cl_cruise
      Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR = scale_factor

    where scale_factor is derived from the SU2 inviscid CD plus a
    fixed skin-friction increment, divided by aviary's FLOPS-baseline
    drag-polar estimate at the target CL. The formula matches what
    the prompt SPECIFIED — the LLM kept skipping it. This middleware
    closes the gap without touching the prompt path.

    No-op when the aero coefficients haven't been captured yet (e.g.
    a non-aviary set_aircraft_parameters call, or the aero stage
    cascaded UPSTREAM_ERROR before producing data).
    """
    if _design_state is None:
        return

    cl = _design_state.data_store.get("aero_cl_cruise")
    cd = _design_state.data_store.get("aero_cd_cruise")
    if cl is None or cd is None:
        return

    parameters = resolved.get("parameters")
    # ``parameters`` may arrive as a JSON string (mcpadapt anyOf gap) —
    # coerce so we can mutate it.
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters)
        except (json.JSONDecodeError, TypeError):
            return
    if not isinstance(parameters, dict):
        return

    # CD calibration formula. Fixed constants are tuned to aviary's
    # height_energy A320-class bench. See the Phase H section of the
    # mission_architect prompt for the derivation — kept consistent
    # here so the prompt and the middleware stay aligned.
    ar = parameters.get("Aircraft.Wing.ASPECT_RATIO")
    try:
        ar_eff = max(float(ar) if ar is not None else 11.0, 8.0)
    except (TypeError, ValueError):
        ar_eff = 11.0

    cd_realistic = cd + 0.0050
    cd_aviary_default = 0.022 + (cl * cl) / (3.14159 * ar_eff * 0.85)
    scale_factor = cd_realistic / cd_aviary_default
    scale_factor = max(0.5, min(scale_factor, 2.0))

    injected: list[str] = []
    if "Mission.Design.LIFT_COEFFICIENT" not in parameters:
        parameters["Mission.Design.LIFT_COEFFICIENT"] = float(cl)
        injected.append("LIFT_COEFFICIENT")
    if "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in parameters:
        parameters["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] = float(scale_factor)
        injected.append("SUBSONIC_DRAG_COEFF_FACTOR")

    if injected:
        resolved["parameters"] = parameters
        logger.info(
            "Phase H middleware injected %s into set_aircraft_parameters "
            "(CL=%.4f, scale_factor=%.3f)",
            ", ".join(injected), float(cl), float(scale_factor),
        )


def _fix_su2_markers(updates: dict, wall: str, far: str) -> None:
    """Ensure SU2 marker config values use the actual mesh marker names.

    Agents often guess 'wall', 'WALL', 'airfoil', '1', etc. but the
    mesh has specific marker names from gmsh ('aircraft', 'farfield').
    """
    marker_wall_keys = {"MARKER_EULER", "MARKER_PLOTTING", "MARKER_MONITORING"}
    marker_far_keys = {"MARKER_FAR"}

    for key in marker_wall_keys:
        if key in updates:
            old = updates[key]
            # Normalize: strip parens and whitespace
            clean = str(old).strip().strip("()").strip()
            if clean.lower() not in (wall.lower(), f"( {wall} )"):
                updates[key] = f"( {wall} )"
                logger.info("Fixed %s: '%s' → '( %s )'", key, old, wall)

    for key in marker_far_keys:
        if key in updates:
            old = updates[key]
            clean = str(old).strip().strip("()").strip()
            if clean.lower() not in (far.lower(), f"( {far} )"):
                updates[key] = f"( {far} )"
                logger.info("Fixed %s: '%s' → '( %s )'", key, old, far)
