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


def reset_design_state():
    """Drop the process-global DesignState so the NEXT link starts clean.

    ``tool_loader._init_data_plane_if_needed`` deliberately REUSES an existing
    DesignState — several ``load_tools_for_agent`` calls within one link (one
    per agent) must share one state, which is what makes the typed coupling
    registry work at all. But nothing ever cleared it between links, so the
    singleton lived for the whole ``stat_batch_runner`` process and every
    chain link inherited the previous link's state (.claude/BUGS.md B8):

      - ``aero.cl_cruise`` / ``aero.cd_cruise`` from link k-1's SU2 solve were
        injected into link k's mission, which then reported
        ``aero_coupling_status = "injected"`` while flying DRAG FROM A
        DIFFERENT GEOMETRY. That is precisely the failure the coupling work
        exists to prevent: "design change -> SU2 rerun -> aviary uses THOSE
        results".
      - ``mission_coupling_error`` stayed silent on links 1..N even when SU2
        never ran there, because the stale registry looked coupled.
      - stale ``sessions['tigl']`` was auto-injected into link k's geometry
        calls (``resolve_request`` overrides a provided session_id with the
        stored one), so those calls hit link k-1's still-open server session
        and read the OLD geometry.

    Call this at each link boundary, BEFORE the pre-hook creates the new
    aviary session (the pre-hook's ``create_session`` must be captured into
    the FRESH state, not wiped by a later reset).

    A FRESH DesignState is installed immediately rather than leaving ``None``:
    the pre-hook's programmatic ``create_session`` call goes through this same
    middleware and its session must be captured, and
    ``_init_data_plane_if_needed`` would otherwise build a second state later
    and discard that capture.

    The tool->server map is intentionally preserved: it is static topology
    (which tool lives on which MCP), not per-design state.

    Returns the new DesignState.
    """
    global _design_state
    from src.coordination.design_state import DesignState

    _design_state = DesignState()
    logger.info("Data plane reset — fresh DesignState for the next link.")
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
                _capture_geometry_ref(tool_name, data)
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
        _capture_geometry_ref(tool_name, response)
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


def _note_aero_capture_failure(reason: str, data: dict) -> None:
    """Record + log that ``read_history_csv`` ran but produced NO usable CL/CD.

    This used to be a silent ``return``, and that silence cost real debugging
    time. In the networked re-measurement (Qwen3-32B, 2026-08-02) the agent did
    everything the coupling contract asks for — it ran SU2 and then called
    read_history_csv, immediately followed by four set_aircraft_parameters
    calls — yet the run came out UNCOUPLED. The reason was only visible by
    hand-reading the raw tool response: SU2 had been configured without force
    output, so history.csv contained ONLY residual columns

        Time_Iter, Outer_Iter, Inner_Iter, "rms[Rho]", "rms[RhoU]", ...

    with no CL/CD at all (typically a missing MARKER_MONITORING). The capture
    found nothing, returned quietly, and the failure was indistinguishable
    downstream from "the agent never ran aero" — which is exactly the kind of
    ambiguity that made .claude/BUGS.md B1 look like a pure ordering problem
    for months.

    The status lands in ``aero_coupling_status`` so the design ledger reports
    the DISTINCT failure mode rather than lumping it in with MISSING_no_su2_aero.
    """
    if _design_state is None:
        return
    cols = data.get("columns")
    col_preview = cols[:12] if isinstance(cols, list) else None
    _design_state.data_store["aero_coupling_status"] = "SU2_NO_FORCE_OUTPUT"
    _design_state.data_store["aero_capture_failure"] = reason
    if col_preview is not None:
        _design_state.data_store["aero_capture_columns"] = col_preview
    logger.warning(
        "AERO CAPTURE FAILED: %s. SU2 ran but its history carries no force "
        "coefficients — check that the SU2 config sets MARKER_MONITORING (and "
        "force fields in HISTORY_OUTPUT); without them the mission CANNOT be "
        "coupled even though the aero stage 'succeeded'. Columns seen: %s",
        reason, col_preview,
    )


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
        _note_aero_capture_failure("read_history_csv returned no rows", data)
        return
    last = rows[-1]
    if not isinstance(last, dict):
        _note_aero_capture_failure("read_history_csv rows are not dicts", data)
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
        _note_aero_capture_failure(
            "read_history_csv has no usable CL/CD columns", data
        )
        return

    # PHYSICAL PLAUSIBILITY BRACKET. The mass capture has had one of these since
    # the beginning; aero did not, and that gap bit hard on 2026-08-03: an SU2
    # config with no REF_AREA fell back to SU2's default of 1.0 m^2 instead of
    # the wing's ~192 m^2, inflating CL by ~190x. The captured CL=15.18 was
    # injected as Mission.Design.LIFT_COEFFICIENT, aviary's Newton solver tried
    # to trim to it and diverged, and the run reported GTOW 310,918 kg / fuel
    # 208,551 kg -- roughly 4x and 17x their real values -- while the ledger
    # cheerfully recorded status "coupled".
    #
    # A transport cruises at CL ~0.5; even full high-lift tops out near 3.0.
    # Anything outside these brackets is a broken reference quantity or an
    # unconverged solve, never a real aircraft, and injecting it is strictly
    # worse than leaving aviary on its own drag polar.
    if not (-0.5 <= cl_f <= 3.0) or not (0.0 < cd_f <= 1.0):
        if _design_state is not None:
            _design_state.data_store["aero_coupling_status"] = "AERO_IMPLAUSIBLE"
            _design_state.data_store["aero_capture_failure"] = (
                f"implausible CL={cl_f:.4g} / CD={cd_f:.4g}"
            )
            _design_state.data_store["aero_rejected_cl"] = cl_f
            _design_state.data_store["aero_rejected_cd"] = cd_f
        logger.warning(
            "AERO REJECTED as non-physical: CL=%.4g CD=%.4g (expected CL in "
            "[-0.5, 3.0], CD in (0, 1.0]). NOT injecting — a bad coefficient "
            "diverges aviary's trim and produces nonsense mass/fuel. Most likely "
            "the SU2 config is missing REF_AREA/REF_LENGTH (SU2 then defaults "
            "REF_AREA to 1.0 m^2 and every coefficient is scaled by the wing "
            "area), or the solve did not converge.",
            cl_f, cd_f,
        )
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


def _capture_geometry_ref(tool_name: str, data: dict) -> None:
    """Capture geometry reference data (MAC, wetted/reference area) into the typed
    registry so the physics-based drag build-up (Reynolds + Swet/Sref) responds to the
    morphed geometry. get_wing_summary -> mac/wing-wetted/reference; get_fuselage_summary
    -> fuselage-wetted."""
    if _design_state is None or not isinstance(data, dict):
        return
    from src.tools import coupling
    if tool_name == "get_wing_summary":
        coupling.put_var(_design_state, "geom.mac_length", data.get("mac_length"), source_tool=tool_name)
        coupling.put_var(_design_state, "geom.wing_wetted_area", data.get("wetted_area"), source_tool=tool_name)
        coupling.put_var(_design_state, "geom.reference_area", data.get("reference_area"), source_tool=tool_name)
    elif tool_name == "get_fuselage_summary":
        coupling.put_var(_design_state, "geom.fuselage_wetted_area", data.get("wetted_area"), source_tool=tool_name)


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

    # export_cpacs / close_cpacs(auto-export): the MORPHED geometry was just written to
    # disk. Record it as the authoritative current-design CPACS so mass tools read the
    # morph, not the baseline input file — makes the structures discipline
    # geometry-coupled for ALL combos regardless of whether the agent explicitly
    # exported or whether export_cpacs is in its toolset. close_cpacs auto-exports on
    # every geometry-stage teardown, so this fires universally.
    if tool_name in ("export_cpacs", "close_cpacs"):
        out_path = data.get("cpacs_file_path")
        if out_path and isinstance(out_path, str) and os.path.isfile(out_path):
            _design_state.data_store["morphed_cpacs_path"] = out_path
            logger.info(
                "Captured MORPHED CPACS path from %s: %s", tool_name, out_path
            )


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

    # Auto-inject cpacs_file_path for mass-mcp tools. Prefer the MORPHED export (the
    # current design's geometry) so structural mass is geometry-coupled; else fall back
    # to the captured input path. A morphed export ALWAYS wins over a stale/baseline
    # path the agent may pass, since the whole point is mass-on-the-current-design.
    if tool_name in _CPACS_PATH_TOOLS and _design_state:
        cpacs_arg = resolved.get("cpacs_file_path", "")
        morphed = _design_state.data_store.get("morphed_cpacs_path")
        if morphed and os.path.isfile(str(morphed)):
            if cpacs_arg != morphed:
                logger.info(
                    "Pointed %s at MORPHED CPACS '%s' (was '%s')",
                    tool_name, morphed, cpacs_arg,
                )
            resolved["cpacs_file_path"] = morphed
        else:
            real_path = _design_state.cpacs_file_path
            # If agent provided a path that doesn't exist, replace with the real one.
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

    # TEMP coarse/fast mesh for the deadline run (env AVION_COARSE_MESH=1). The
    # geometry prompt does NOT expose far_field/mesh_size, so agents default to a
    # FINE mesh (far_field 10, auto cell size ~10.5 -> ~660k cells -> slow SU2).
    # Force a COARSER mesh at the tool boundary so SU2 solves fast. Values chosen so
    # the Euler solve still converges enough to inject a real CD (verified via a
    # timed run_link): small domain + large max cell size = far fewer cells.
    if tool_name == "generate_volume_mesh" and os.environ.get("AVION_COARSE_MESH") == "1":
        resolved["far_field_distance"] = 5.0
        resolved["mesh_size_max"] = 25.0
        resolved["boundary_layer_enabled"] = False
        logger.info("COARSE MESH forced: far_field=5.0, mesh_size_max=25.0")

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


# A value shaped like a data-store ref key: "<tool_name>__<field>".
_REF_SHAPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*__[A-Za-z0-9_]+$")


def unresolved_ref_error(tool_name: str, resolved: dict) -> dict | None:
    """Return an error dict when an argument LOOKS like a data-store ref but
    could not be resolved — else None.

    ``resolve_request`` deliberately passes an unrecognised ref through as a
    literal rather than guessing at a substitute (never silently swap in the
    wrong payload). That policy is right, but the resulting failure was
    unhelpful: the raw key string reached the MCP server, which tried to use it
    as DATA and produced an error naming the wrong problem.

    Observed live 2026-08-03 (sweep run 1/16): the agent passed
    ``mesh_base64="generated_volume_mesh__mesh_base64"`` — one character off the
    real key ``generate_volume_mesh__mesh_base64`` — and su2-mcp replied

        "Failed to set mesh: Invalid base64-encoded string: number of data
         characters (29) cannot be 1 more than a multiple of 4"

    which says nothing about refs. The agent could not self-correct and reissued
    the identical call.

    Catching it here fails fast with the ACTUAL available keys, and (when the
    typo is unambiguous) names the intended one, so the model's own error
    recovery can fix it in one step.
    """
    if _design_state is None:
        return None
    store = getattr(_design_state, "data_store", None) or {}
    if not store:
        return None

    for key, value in resolved.items():
        if not isinstance(value, str) or not _REF_SHAPE_RE.match(value.strip()):
            continue
        if value.strip() in store:
            continue  # resolvable — resolve_request already handled it

        available = sorted(k for k in store if isinstance(k, str) and "__" in k)
        if not available:
            continue

        import difflib

        close = difflib.get_close_matches(value.strip(), available, n=1, cutoff=0.8)
        suggestion = (
            f" Did you mean '{close[0]}'?" if close else ""
        )
        return {
            "success": False,
            "error_code": "UNRESOLVED_REF",
            "error": (
                f"Argument '{key}' looks like a stored-payload reference but no such "
                f"reference exists: '{value.strip()}'.{suggestion} "
                f"Available references: {available}. "
                "Pass one of these exactly (or the {\"ref\": \"<key>\"} form). This call "
                "was NOT sent to the server — the literal string would have been "
                "interpreted as data."
            ),
        }
    return None


def mission_coupling_error(tool_name: str, resolved: dict) -> dict | None:
    """Return an UNCOUPLED error dict if an aviary mission call is about to run WITHOUT
    the SU2 cruise aero — else None. Called by the tool wrapper AFTER resolve_request
    (so registry injection already had its chance). This is NOT a phase-gate: it does
    not reorder or force anything; it just makes the missing coupling VISIBLE to the
    model as a tool error so its own coordination can recover (run SU2, retry). The
    typed registry stays the primary path; the prompt instruction is the backup.

    Fires only for ``set_aircraft_parameters`` (the aviary mission-design call). A call
    is considered coupled if the resolved parameters carry the drag factor — whether
    injected from the registry or set by the agent by hand. Toggle with
    AVION_ENFORCE_COUPLING=0.
    """
    import os
    if tool_name != "set_aircraft_parameters":
        return None
    if os.environ.get("AVION_ENFORCE_COUPLING", "1") != "1":
        return None
    params = resolved.get("parameters")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(params, dict):
        return None
    if "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" in params:
        return None  # coupled — aero present (injected or agent-provided)
    return {
        "success": False,
        "error_code": "UNCOUPLED_MISSION",
        "error": (
            "COUPLING ADVISORY (non-blocking): this mission ran on aviary's DEFAULT "
            "drag polar because no SU2 cruise aero was captured yet — it is UNCOUPLED. "
            "To couple it, run the AERO stage (create_su2_session -> set_mesh -> "
            "run_su2_solver -> read_history_csv) and re-call set_aircraft_parameters; "
            "the SU2 CL/CD are captured as typed vars (aero.cl_cruise / aero.cd_cruise) "
            "and inject automatically — you do not need to pass them by hand. Coupling "
            "here is optional but recommended for a fully-coupled MDO result."
        ),
    }


def mass_coupling_hint(tool_name: str, resolved: dict) -> str | None:
    """Return a NON-BLOCKING coupling hint when an aviary mission call is about to run
    without the structures-discipline mass coupled — else None.

    Unlike ``mission_coupling_error`` (aero, a hard UNCOUPLED error that blocks until
    the model reruns SU2), mass coupling is OPTIONAL: aviary has its own internal FLOPS
    wing mass, so we do not block. We just make the coupling OPPORTUNITY visible — "you
    can couple structural mass here" — and let the model's own coordination decide whether
    to run estimate_mass. The hint is attached to the (successful) response, not returned
    in place of it. Same AVION_ENFORCE_COUPLING toggle as the aero error.

    Fires only for ``set_aircraft_parameters``. Suppressed when mass is already coupled —
    either the resolved params already carry ``Aircraft.Wing.MASS_SCALER`` (injected from
    the registry or set by the agent) or mass-mcp has run this session (``mass_wing_kg``
    captured, so the injector will fill it in).
    """
    import os
    if tool_name != "set_aircraft_parameters":
        return None
    if os.environ.get("AVION_ENFORCE_COUPLING", "1") != "1":
        return None
    if _design_state is None:
        return None
    params = resolved.get("parameters")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(params, dict) and "Aircraft.Wing.MASS_SCALER" in params:
        return None  # already coupled (injected or agent-provided)
    if _design_state.data_store.get("mass_wing_kg") is not None:
        return None  # mass-mcp ran; the injector couples it — no hint needed
    return (
        "COUPLING AVAILABLE (optional): the structures discipline can be coupled here. "
        "Run estimate_mass (mass-mcp) on the current morphed geometry BEFORE the mission "
        "and its wing mass is captured as the typed var mass.wing_kg and injected as "
        "Aircraft.Wing.MASS_SCALER automatically. Proceeding now uses aviary's own internal "
        "FLOPS wing mass instead — valid, but the external structures discipline is not "
        "coupled into this run. Your choice."
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

    # PHYSICS-BASED drag build-up (Option A'): total CD = SU2 inviscid pressure drag +
    # a real Schlichting skin-friction estimate from the flight-state Reynolds number and
    # the morphed geometry's wetted-area ratio. Re from canonical cruise Mach/altitude +
    # MAC; Swet/Sref from wing+fuselage wetted / reference area (design-responsive), with a
    # sane band + nominal fallback so a bad geometry read can't produce garbage drag.
    ar = parameters.get("Aircraft.Wing.ASPECT_RATIO")
    reynolds = swet_sref = None
    try:
        from src.config.canonical import load_canonical
        _mis = load_canonical().get("mission", {})
        mac = coupling.get_var(_design_state, "geom.mac_length") or 4.0
        reynolds = coupling.reynolds_number(_mis.get("cruise_mach", 0.78),
                                            _mis.get("cruise_altitude_ft", 33000), mac)
        _ref = coupling.get_var(_design_state, "geom.reference_area")
        _wwet = coupling.get_var(_design_state, "geom.wing_wetted_area")
        _fwet = coupling.get_var(_design_state, "geom.fuselage_wetted_area") or 0.0
        if _ref and _ref > 0 and _wwet:
            ratio = (_wwet + _fwet) / _ref
            swet_sref = ratio if 3.0 <= ratio <= 9.0 else None  # else fall back to nominal
    except Exception:
        pass
    scale_factor = coupling.aero_cd_to_aviary_drag_factor(cd, cl, ar, reynolds=reynolds, swet_sref=swet_sref)

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
