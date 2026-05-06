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
        response = _intercept_binaries(tool_name, response)
        return response

    return response


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
    result = {}
    for key, value in data.items():
        if isinstance(value, str) and _is_binary_payload(key, value):
            store_key = f"{tool_name}__{key}"
            _design_state.data_store[store_key] = value
            logger.info("Stored %s (%d bytes)", store_key, len(value))
            result[key] = {"ref": store_key, "size_bytes": len(value)}
        elif isinstance(value, dict):
            result[key] = _intercept_binaries(tool_name, value)
        else:
            result[key] = value
    return result


# ── Request middleware ───────────────────────────────────────────────────────

def resolve_request(tool_name: str, kwargs: dict) -> dict:
    """Pre-process tool call arguments."""
    resolved = {}
    mcp_name = _tool_server_map.get(tool_name, "")

    for key, value in kwargs.items():
        # Resolve data store refs (dict with "ref" key)
        if isinstance(value, dict) and "ref" in value:
            ref_key = value["ref"]
            payload = _design_state.data_store.get(ref_key) if _design_state else None
            if payload is not None:
                logger.info("Resolved ref %s for %s.%s", ref_key, tool_name, key)
                resolved[key] = payload
                continue
        # Resolve plain string refs
        if isinstance(value, str) and _design_state and value in _design_state.data_store:
            payload = _design_state.data_store[value]
            logger.info("Resolved string ref %s for %s.%s", value, tool_name, key)
            resolved[key] = payload
            continue
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

    return resolved


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
