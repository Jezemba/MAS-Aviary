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
                _capture_wing_mass_from_mass_estimate(tool_name, data)
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
        _capture_wing_mass_from_mass_estimate(tool_name, response)
        response = _intercept_binaries(tool_name, response)
        return response

    return response


def _capture_wing_mass_from_mass_estimate(tool_name: str, data: dict) -> None:
    """Phase K-A: stash mass-mcp's wing mass for later injection.

    mass-mcp's ``estimate_mass`` returns a structured ``mass_breakdown``
    block with ``components.mWing_kg`` carrying the FLOPS-sourced wing
    mass (in Aviary Wing.MASS scope after the wwr fix in mass-mcp
    commit bb99056). Without a server-side handoff that number stays
    inside the structures stage's text output — aviary then computes
    its OWN wing mass from internal FLOPS and ignores the external
    discipline. resolve_request injects a matching
    ``Aircraft.Wing.MASS_SCALER`` into the next
    ``set_aircraft_parameters`` so aviary's mission reflects the
    coupled wing mass.
    """
    if _design_state is None or tool_name != "estimate_mass":
        return

    breakdown = data.get("mass_breakdown")
    if not isinstance(breakdown, dict):
        return
    components = breakdown.get("components")
    if not isinstance(components, dict):
        return

    wing_kg = components.get("mWing_kg")
    if wing_kg is None:
        return
    try:
        wing_f = float(wing_kg)
    except (TypeError, ValueError):
        return
    # Sanity bracket — a 70-tonne narrow-body wing is ~5,000-12,000 kg
    # structural. Anything outside [1000, 30000] is almost certainly a
    # parsing accident.
    if not (1000.0 <= wing_f <= 30000.0):
        logger.info(
            "Skipping mass-mcp wing mass=%.1f kg — outside [1k, 30k] "
            "sanity bracket.",
            wing_f,
        )
        return

    _design_state.data_store["mass_wing_kg"] = wing_f
    from src.tools import coupling as _coupling
    _coupling.put_var(_design_state, "mass.wing_kg", wing_f, source_tool="estimate_mass")
    _design_state.data_store["mass_wing_source"] = str(
        components.get("mWing_source", "unknown")
    )
    logger.info(
        "Captured wing mass from estimate_mass: %.1f kg (source=%s)",
        wing_f, components.get("mWing_source"),
    )

    # Phase K-B: also stash MTOM for pycycle Fn_DES injection
    mtom_kg = breakdown.get("mTOM_kg")
    if mtom_kg is not None:
        try:
            mtom_f = float(mtom_kg)
            if 20_000.0 <= mtom_f <= 200_000.0:
                _design_state.data_store["mass_mtom_kg"] = mtom_f
                from src.tools import coupling as _cpl
                _cpl.put_var(_design_state, "mass.mtom_kg", mtom_f, source_tool="estimate_mass")
                logger.info(
                    "Captured MTOM from estimate_mass: %.1f kg", mtom_f,
                )
        except (TypeError, ValueError):
            pass


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

    # Legacy keys (kept during transition / as fallback source).
    _design_state.data_store["aero_cl_cruise"] = cl_f
    _design_state.data_store["aero_cd_cruise"] = cd_f
    # Typed registry (the coupling contract): SU2 writes its named output variables.
    from src.tools import coupling
    coupling.put_var(_design_state, "aero.cl_cruise", cl_f, source_tool="read_history_csv")
    coupling.put_var(_design_state, "aero.cd_cruise", cd_f, source_tool="read_history_csv")
    if cd_f:
        coupling.put_var(_design_state, "aero.l_over_d", cl_f / cd_f, source_tool="read_history_csv")
    logger.info(
        "Captured aero into typed registry: aero.cl_cruise=%.4f aero.cd_cruise=%.4f",
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

    # Auto-inject / override session_id.
    #
    # Two cases handled here:
    #   (a) session_id MISSING — original behavior, inject from sessions[mcp]
    #   (b) session_id PROVIDED but doesn't match the captured one — override.
    # Case (b) is for orchestrated-strategy workers (fresh agents created via
    # CreateAgent) that don't see the real UUID in their prompt context and
    # hallucinate strings like "structures_analysis_session". Sequential
    # workers don't hit case (b) because they see the session_id in
    # previous-stage context and pass the correct UUID.
    if _design_state and mcp_name and tool_name not in _NO_SESSION_TOOLS:
        stored_sid = _design_state.sessions.get(mcp_name)
        if stored_sid:
            provided = resolved.get("session_id")
            if provided is None or provided == "":
                resolved["session_id"] = stored_sid
                logger.info(
                    "Auto-injected session_id for %s from sessions[%s]",
                    tool_name, mcp_name,
                )
            elif provided != stored_sid:
                logger.info(
                    "Overrode hallucinated session_id %r with real %s for %s "
                    "(mcp=%s)",
                    provided, stored_sid, tool_name, mcp_name,
                )
                resolved["session_id"] = stored_sid

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

    # Phase H + K-A: merge externally computed CL/CD/wing-mass into
    # aviary's set_aircraft_parameters. Phase K-B: merge mass-mcp's
    # MTOM into pycycle's set_inputs (Fn_DES sizing).
    #
    # Phase J was reverted 2026-05-23 — SUBSONIC_FUEL_FLOW_SCALER had
    # no effect on the FwFm bench's tabular engine deck. K-A and K-B
    # were validated BEFORE shipping.
    if tool_name == "set_aircraft_parameters" and _design_state:
        _inject_phase_h_aero(resolved)
        _inject_phase_k_wing_mass(resolved)
    if tool_name == "set_inputs" and _design_state and mcp_name == "pycycle":
        _inject_phase_k_b_fn_des(resolved)

    return resolved


# Aviary's FLOPS Wing.MASS on the default bench geometry (no
# overrides) is 5,998 kg — measured locally via aviary_runner with
# MASS_SCALER=1.0 and AREA=124.6, AR=11.22. The middleware uses this
# as the reference to translate mass-mcp's externally computed wing
# mass into a scaler relative to aviary's own buildup.
_AVIARY_BENCH_WING_MASS_KG = 5998.0


# Phase K-B: per-engine cruise-climb thrust ≈ MTOM · g / (L/D · N_eng)
# with a climb margin. Coefficient derived as
#   0.0811 = 9.81 / 17.0 / 2.0 / 4.448 * 1.25
# (g=9.81 m/s², L/D=17 conservative narrow-body, 2 engines, N→lbf,
# 1.25× climb thrust margin). For MTOM=73 t → 5,920 lbf, matching
# pycycle HBTF's default Fn_DES of 5,900 lbf within 0.3%.
_FN_DES_PER_KG_LBF = 0.0811


def _inject_phase_k_b_fn_des(resolved: dict) -> None:
    """Merge mass-mcp's MTOM into pycycle's ``set_inputs`` as Fn_DES.

    The propulsion-weight snowball: heavier aircraft needs more
    thrust, bigger engine sizes itself for that thrust, engine mass
    feeds back into airframe mass. This closes one side of the loop
    by sizing the design-point thrust to the externally computed
    MTOM rather than letting the propulsion agent default to 5,900
    lbf regardless of airframe.

    No-op when:
      - mass-mcp didn't run (no ``mass_mtom_kg`` in data_store)
      - the agent already specified Fn_DES explicitly
      - the set_inputs call doesn't target the pycycle MCP
    """
    if _design_state is None:
        return

    mtom_kg = _design_state.data_store.get("mass_mtom_kg")
    if mtom_kg is None:
        return

    values = resolved.get("values")
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except (json.JSONDecodeError, TypeError):
            return
    if not isinstance(values, dict):
        return

    if "Fn_DES" in values:
        return  # respect explicit agent override

    # MTOM -> pycycle Fn_DES via the named, documented transform (src/tools/coupling.py).
    from src.tools import coupling as _cpl
    fn_des_lbf = _cpl.mtom_to_pycycle_fn_des(mtom_kg)

    values["Fn_DES"] = fn_des_lbf
    resolved["values"] = values
    logger.info(
        "Phase K-B middleware injected Fn_DES=%.0f lbf "
        "(mass-mcp MTOM=%.1f kg)",
        fn_des_lbf, float(mtom_kg),
    )


def _inject_phase_k_wing_mass(resolved: dict) -> None:
    """Merge mass-mcp's wing mass into a set_aircraft_parameters call.

    Adds ``Aircraft.Wing.MASS_SCALER`` to the parameters dict (if not
    already set by the agent), scaled so that aviary's structural
    wing mass lines up with mass-mcp's externally computed value.

    No-op when:
      - mass-mcp didn't run (no ``mass_wing_kg`` in data_store)
      - the agent already specified the scaler explicitly

    Validated end-to-end before shipping (Avion 2026-05-23):
    MASS_SCALER ∈ {0.5, 1.0, 1.5} on the bench moves fuel burn from
    9,733 → 10,047 → 10,423 kg — a real, monotonic coupling.
    """
    if _design_state is None:
        return

    wing_kg = _design_state.data_store.get("mass_wing_kg")
    if wing_kg is None:
        return

    parameters = resolved.get("parameters")
    if isinstance(parameters, str):
        try:
            parameters = json.loads(parameters)
        except (json.JSONDecodeError, TypeError):
            return
    if not isinstance(parameters, dict):
        return

    if "Aircraft.Wing.MASS_SCALER" in parameters:
        return  # respect explicit agent override

    from src.tools import coupling as _cpl
    scaler = _cpl.wing_mass_to_aviary_scaler(wing_kg)

    parameters["Aircraft.Wing.MASS_SCALER"] = scaler
    resolved["parameters"] = parameters
    logger.info(
        "Phase K-A middleware injected Aircraft.Wing.MASS_SCALER=%.3f "
        "(mass-mcp wing=%.1f kg / aviary bench=%.1f kg)",
        scaler, float(wing_kg), _AVIARY_BENCH_WING_MASS_KG,
    )


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

    # Read from the TYPED registry first (the coupling contract); fall back to the
    # legacy data_store keys only if the registry is empty (older capture path).
    from src.tools import coupling
    cl = coupling.get_var(_design_state, "aero.cl_cruise")
    cd = coupling.get_var(_design_state, "aero.cd_cruise")
    _aero_src = "typed_registry"
    if cl is None or cd is None:
        cl = _design_state.data_store.get("aero_cl_cruise")
        cd = _design_state.data_store.get("aero_cd_cruise")
        _aero_src = "legacy_fallback"
    if cl is None or cd is None:
        # COUPLING FAILURE (not a benign no-op): aviary is about to run WITHOUT the
        # current design's SU2 aero, so it will silently fall back to its default drag
        # polar (~CD 0.021) instead of the SU2-computed drag (~0.014). That produces a
        # spuriously high, design-insensitive fuel and makes the objective bimodal. Record
        # the status loudly so the runner can enforce a fully-coupled run (retry / reject)
        # rather than scoring an uncoupled result. See [[avion-aero-coupling-fix]].
        _design_state.data_store["aero_coupling_status"] = "MISSING_no_su2_aero"
        logger.warning(
            "AERO COUPLING MISSING: set_aircraft_parameters reached aviary with no SU2 "
            "CL/CD captured this session — aviary will use its DEFAULT drag polar. Run the "
            "aero stage (SU2 + read_history_csv) before the mission stage for a coupled run."
        )
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

    # CD -> aviary drag-scale factor via the NAMED, documented transform (was an inline
    # formula here; now src/tools/coupling.py, unit-tested). AR sourced from the design.
    ar = parameters.get("Aircraft.Wing.ASPECT_RATIO")
    scale_factor = coupling.aero_cd_to_aviary_drag_factor(cd, cl, ar)

    injected: list[str] = []
    if "Mission.Design.LIFT_COEFFICIENT" not in parameters:
        parameters["Mission.Design.LIFT_COEFFICIENT"] = float(cl)
        injected.append("LIFT_COEFFICIENT")
    if "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in parameters:
        parameters["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] = float(scale_factor)
        injected.append("SUBSONIC_DRAG_COEFF_FACTOR")

    # Record that this aviary run IS coupled to SU2 aero, with the exact CL/CD used —
    # lets the runner confirm a fully-coupled run and (future) detect stale aero if the
    # design changed after the SU2 solve that produced these coefficients.
    _design_state.data_store["aero_coupling_status"] = "injected"
    _design_state.data_store["aero_coupling_source"] = _aero_src  # typed_registry | legacy_fallback
    _design_state.data_store["aero_injected_cl"] = float(cl)
    _design_state.data_store["aero_injected_cd"] = float(cd)
    _design_state.data_store["aero_injected_drag_factor"] = float(scale_factor)
    if _aero_src == "legacy_fallback":
        logger.warning("AERO COUPLING via LEGACY fallback (typed registry empty) — check SU2 capture.")

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
