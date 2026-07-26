"""Plain-language narration of the MAS-Aviary pipeline events.

The runner's stdout speaks fluent "engineer with a debugger" - tool
names, JSON args, raw observation dicts. The narrator translates each
of those into a sentence a non-expert can follow:

  raw   :  Calling tool: 'generate_volume_mesh' with arguments: {...}
  raw   :  Observations: {"format": "su2", "mesh_size_bytes": 21240076, ...}
  narrated:
            🛠️  Building a 3-D CFD mesh - TiGL exports the wing as STL,
                gmsh wraps it in a far-field box and tetrahedralizes the
                volume. Typically takes 30-90 s.
            ✅ Mesh built: 351,391 cells (~20 MB)

Everything here is data, not logic, so it is easy to tune the wording
without touching the parser/formatter.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


# ── Per-agent introductions ──────────────────────────────────────────────────
# Rendered as a single italicized paragraph right after the agent panel opens.
# Aim for "what this expert does, in one breath."

AGENT_INTROS: dict[str, str] = {
    "geometry_engineer": (
        "I'm the **Geometry Engineer**. I read the aircraft's CPACS definition "
        "(an XML schema for parametric aircraft), inspect the wing and "
        "fuselage, record the design intent the orchestrator gave me, and "
        "produce a 3-D CFD mesh that the aerodynamics stage can solve on."
    ),
    "aerodynamics_analyst": (
        "I'm the **Aerodynamics Analyst**. I take the volume mesh from the "
        "geometry stage, configure SU2 for an Euler cruise solve at "
        "Mach 0.78 / FL330, and try to converge a lift and drag estimate. "
        "If the residuals don't drop enough, I fall back to F25 reference "
        "numbers so downstream stages still get something to work with."
    ),
    "structures_analyst": (
        "I'm the **Structures Analyst**. I read the CPACS file mass-mcp "
        "left on disk, run a FLOPS-based mass estimation, and report the "
        "operating empty mass and the wing/fuselage component masses. "
        "OpenAeroStruct is the higher-fidelity option but it tends to "
        "NaN on the D150 baseline - I fall back to FLOPS-only."
    ),
    "propulsion_analyst": (
        "I'm the **Propulsion Analyst**. I build a high-bypass turbofan "
        "thermodynamic cycle in pyCycle (NASA's gas-path code), set the "
        "design point for F25 cruise (BPR ≈ 11, OPR ≈ 40, T4 ≈ 1700 K, "
        "Mach 0.78 at FL330), and report SFC and net thrust per engine."
    ),
    "mission_architect": (
        "I'm the **Mission Architect**. I configure Aviary's mission "
        "profile (range, passengers, cruise condition) and translate the "
        "upstream geometry/mass/propulsion outputs into Aviary aircraft "
        "parameters. The validation step runs a quick Newton evaluation "
        "so we catch infeasible parameter sets before paying for the full "
        "trajectory optimization."
    ),
    "simulation_executor": (
        "I'm the **Simulation Executor**. I trigger Aviary's SLSQP "
        "trajectory optimizer - it varies throttle / altitude / speed "
        "to minimize fuel burn over the mission, subject to range and "
        "climb constraints. Typical run ≈ 40 s, max 200 iterations."
    ),
    "mdo_integrator": (
        "I'm the **MDO Integrator**. I synthesize results from every "
        "discipline, check the F25 constraints, and report the final "
        "fuel burn, MTOM, and any recommended changes for the next "
        "iteration."
    ),
}


# Short transition line shown when one agent hands off to the next.
AGENT_HANDOFFS: dict[str, str] = {
    "geometry_engineer":    "→ handing the mesh and design intent to **Aerodynamics**…",
    "aerodynamics_analyst": "→ handing the aero polar (CL, CD, L/D) to **Structures**…",
    "structures_analyst":   "→ handing the mass breakdown to **Propulsion**…",
    "propulsion_analyst":   "→ handing the engine deck (SFC, thrust) to the **Mission Architect**…",
    "mission_architect":    "→ Aviary session ready; passing to the **Simulation Executor**…",
    "simulation_executor":  "→ trajectory result ready; passing to the **MDO Integrator**…",
    "mdo_integrator":       "→ pipeline complete.",
}


# ── Tool-call narration ──────────────────────────────────────────────────────
# Map tool name → (plain-language description, optional duration hint).
# The description appears INSTEAD of the raw `tool(...)` line; the raw
# call is still tucked into a small <details> below it for engineers.

TOOL_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    # tigl-mcp
    "open_cpacs":               ("📂 Loading the aircraft definition from CPACS (the XML schema that describes wings, fuselage, engines parametrically).", ""),
    "get_configuration_summary":("🔍 Inspecting the top-level structure - how many wings, fuselages, engines does this aircraft have?", ""),
    "list_geometric_components":("🔍 Listing every geometric component (wings, fuselages, rotors, engines)…", ""),
    "get_wing_summary":         ("📏 Reading the wing geometry - span, sweep, area, aspect ratio, MAC.", ""),
    "get_fuselage_summary":     ("📏 Reading the fuselage geometry - length, cross-section, volume.", ""),
    "get_high_level_parameters":("📋 Asking TiGL for the current high-level design parameters of this component…", ""),
    "set_high_level_parameters":("✏️  Recording the design intent (wing span, sweep, etc.). TiGL stores these as session annotations - the underlying geometry doesn't morph; the mesh below reflects the baseline.", ""),
    "get_component_metadata":   ("📋 Looking up component metadata (type, symmetry plane, indices)…", ""),
    "intersect_components":     ("✂️  Computing the intersection curve between two geometric components…", ""),
    "intersect_with_plane":     ("✂️  Cutting the component with a plane and sampling the resulting curve…", ""),
    "sample_component_surface": ("🎯 Sampling 3-D points on the component's surface…", ""),
    "export_component_mesh":    ("📤 Exporting a surface-only mesh (STL) for visualization - note: surface meshes are NOT directly solvable by SU2.", ""),
    "export_configuration_cad": ("📤 Exporting the full aircraft as STEP (CAD)…", ""),
    "generate_volume_mesh":     ("🛠️  Building a 3-D CFD mesh - TiGL exports the wing as STL, gmsh wraps it in a far-field box and tetrahedralizes the volume. Usually 30-90 s.", "slow"),
    "close_cpacs":              ("💾 Closing the CPACS session and flushing the file to disk so mass-mcp can read it.", ""),

    # su2-mcp
    "create_su2_session":       ("🆕 Initializing the SU2 CFD workspace (config file, scratch dir).", ""),
    "set_mesh":                 ("📥 Loading the volume mesh into the SU2 session.", ""),
    "analyze_mesh":             ("🔍 Running mesh diagnostics - element counts, marker names, quality checks.", ""),
    "generate_mesh_from_step":  ("🛠️  Alternative mesh path: meshing directly from a CAD STEP file.", ""),
    "generate_deformed_mesh":   ("🛠️  Running SU2_DEF to produce a deformed mesh (FFD / control-point updates).", ""),
    "update_config_entries":    ("⚙️  Configuring SU2 - Mach, angle of attack, Reynolds, solver type, freestream P/T, boundary markers.", ""),
    "parse_config":             ("📋 Reading back the parsed SU2 configuration…", ""),
    "get_config_text":          ("📋 Reading the raw config text…", ""),
    "get_valid_config_options": ("📋 Looking up SU2 v8.3 valid configuration options…", ""),
    "get_su2_status":           ("🩺 Checking SU2 binary availability on the host.", ""),
    "run_su2_solver":           ("🌬️  Running SU2_CFD - Euler equations on the volume mesh, 200 iterations. Typically 1-3 minutes.", "slow"),
    "list_result_files":        ("📑 Listing convergence history and surface-solution files produced by the solver…", ""),
    "read_history_csv":         ("📈 Reading the residual convergence trace from the history CSV…", ""),
    "sample_surface_solution":  ("🎯 Sampling pressure and skin-friction on the wing surface…", ""),
    "get_result_file_base64":   ("📤 Returning a result file as base64 (intercepted by the data plane).", ""),
    "get_session_info":         ("ℹ️  Looking up SU2 session paths and run metadata…", ""),

    # mass-mcp
    "validate_cpacs_inputs":    ("🩺 Checking the CPACS file has all the parameters mass estimation will need (required + optional).", ""),
    "estimate_mass":            ("⚖️  Computing aircraft mass - OEM, wing mass, fuselage mass. Uses FLOPS (NASA empirical correlations) by default; OAS hybrid mode is the higher-fidelity option.", "slow"),
    "get_cpacs_mass_breakdown": ("📋 Reading any mass results already written into the CPACS file from a prior run…", ""),

    # pycycle-mcp
    "create_cycle_model":       ("🆕 Building a high-bypass turbofan thermodynamic cycle (gas-path components: fan, LPC, HPC, burner, HPT, LPT, nozzles).", ""),
    "list_variables":           ("📋 Discovering the cycle's inputs and outputs (BPR, OPR, T4, etc.)…", ""),
    "set_inputs":               ("⚙️  Setting the engine design point - bypass ratio, overall pressure ratio, fan PR, turbine inlet temperature, and the flight condition (altitude, Mach).", ""),
    "run_cycle":                ("🔥 Solving the cycle thermodynamics - Newton iteration on the gas-path equations. Returns SFC, net thrust, fuel flow.", "slow"),
    "get_outputs":              ("📈 Reading the cycle outputs (SFC at cruise, net thrust per engine, engine mass)…", ""),
    "get_cycle_summary":        ("📋 Getting a concise summary of the cycle model state…", ""),
    "sweep_inputs":             ("🔁 Running a parametric sweep over selected cycle inputs…", ""),
    "compute_totals":           ("📐 Computing total derivatives via OpenMDAO (gradient information)…", ""),
    "close_cycle_model":        ("👋 Closing the pyCycle session.", ""),

    # aviary-mcp
    "get_design_space":         ("📋 Asking Aviary which aircraft and mission parameters it accepts - names, units, bounds, and which ones are derived vs settable.", ""),
    "create_session":           ("🆕 Spinning up a fresh Aviary session, loading the default 737/A320-class aircraft as the baseline.", ""),
    "configure_mission":        ("🛣️  Setting the mission profile - range, passengers, cruise Mach, cruise altitude, optimizer iteration budget.", ""),
    "set_aircraft_parameters":  ("✏️  Overriding the baseline aircraft with values informed by the upstream stages (wing area, AR, sweep, engine scale factor, fuselage dimensions).", ""),
    "validate_parameters":      ("🩺 Quick feasibility check - runs a single Newton evaluation on the climb-phase aero/propulsion system to catch infinite or NaN residuals before the full optimization. Catches over-AR wings, underpowered engines, etc.", ""),
    "run_simulation":           ("🚀 Running Aviary's coupled aircraft-trajectory optimization (SLSQP). Throttle, altitude, and speed are varied along the mission to minimize fuel burn subject to constraints. Typically ~40 s, 200 iters max.", "slow"),
    "get_results":              ("📈 Reading the optimized outputs - fuel burned, gross mass, wing mass, reserve fuel, exit code, converged flag.", ""),
    "get_trajectory":           ("📈 Reading the time-series trajectory profile (altitude, Mach, throttle, mass, drag over time)…", ""),
    "check_constraints":        ("✅ Checking every F25 constraint - range achieved, climb gradient, take-off field length, approach speed.", ""),
    "ping":                     ("🩺 Health-checking the MCP server…", ""),
    "final_answer":             ("📝 Wrapping up - writing the DESIGN_STATE block for the next stage.", ""),
}


def describe_tool(tool_name: str) -> tuple[str, str]:
    """Return (human description, duration_hint).

    Falls back to "Calling <tool_name>…" when unknown. duration_hint is
    one of "", "slow" - the pacer uses this to insert a "this may take
    a moment" beat.
    """
    if tool_name in TOOL_DESCRIPTIONS:
        return TOOL_DESCRIPTIONS[tool_name]
    return (f"⚙️  Calling `{tool_name}`…", "")


SLOW_TOOL_NOTE = "*(this step takes a while - usually 30 s to a couple of minutes)*"


# ── Observation summarizers ──────────────────────────────────────────────────
# Each takes the parsed observation dict (or empty if the parser couldn't
# decode JSON) and returns a single short bullet - or None to fall back to
# a generic acknowledgement. Keep the bullets factual and easy to scan.


def _fmt_int(v: Any) -> str:
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_float(v: Any, decimals: int = 2) -> str:
    try:
        return f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(v)


def _summary_open_cpacs(obs: dict) -> Optional[str]:
    cs = obs.get("configuration_summary") or {}
    nw = cs.get("num_wings"); nf = cs.get("num_fuselages")
    ne = cs.get("num_engines"); nr = cs.get("num_rotors")
    sid = obs.get("session_id", "")[:8]
    parts = []
    if isinstance(nw, int): parts.append(f"{nw} wing{'s' if nw != 1 else ''}")
    if isinstance(nf, int): parts.append(f"{nf} fuselage{'s' if nf != 1 else ''}")
    if isinstance(ne, int) and ne > 0: parts.append(f"{ne} engine{'s' if ne != 1 else ''}")
    if isinstance(nr, int) and nr > 0: parts.append(f"{nr} rotor{'s' if nr != 1 else ''}")
    where = f", session `{sid}…`" if sid else ""
    if parts:
        return f"✅ Loaded - found {', '.join(parts)}{where}."
    return f"✅ Loaded{where}."


def _summary_get_configuration_summary(obs: dict) -> Optional[str]:
    wings = obs.get("wings") or []
    fuselages = obs.get("fuselages") or []
    if not isinstance(wings, list):
        wings = []
    if not isinstance(fuselages, list):
        fuselages = []
    bbox = obs.get("bounding_box") or {}
    bb = ""
    if bbox:
        x = bbox.get("xmax", 0) - bbox.get("xmin", 0)
        y = bbox.get("ymax", 0) - bbox.get("ymin", 0)
        z = bbox.get("zmax", 0) - bbox.get("zmin", 0)
        bb = f" - bounding box ≈ {x:.1f} × {y:.1f} × {z:.1f} m"
    nw, nf = len(wings), len(fuselages)
    return (
        f"✅ Components: {nw} wing{'s' if nw != 1 else ''}, "
        f"{nf} fuselage{'s' if nf != 1 else ''}{bb}."
    )


def _summary_get_wing_summary(obs: dict) -> Optional[str]:
    parts = []
    if "span" in obs and obs["span"] is not None:
        parts.append(f"span {_fmt_float(obs['span'], 2)} m")
    if "reference_area" in obs and obs["reference_area"] is not None:
        parts.append(f"area {_fmt_float(obs['reference_area'], 2)} m²")
    if "aspect_ratio" in obs and obs["aspect_ratio"] is not None:
        parts.append(f"AR {_fmt_float(obs['aspect_ratio'], 2)}")
    if "sweep_deg" in obs and obs["sweep_deg"] is not None:
        parts.append(f"sweep {_fmt_float(obs['sweep_deg'], 1)}°")
    return f"✅ Wing: {', '.join(parts)}." if parts else None


def _summary_get_fuselage_summary(obs: dict) -> Optional[str]:
    parts = []
    if "length" in obs and obs["length"] is not None:
        parts.append(f"length {_fmt_float(obs['length'], 1)} m")
    if "max_diameter" in obs and obs["max_diameter"] is not None:
        parts.append(f"max ⌀ {_fmt_float(obs['max_diameter'], 2)} m")
    return f"✅ Fuselage: {', '.join(parts)}." if parts else None


def _summary_set_high_level_parameters(obs: dict) -> Optional[str]:
    cuid = obs.get("component_uid", "")
    np_ = obs.get("new_parameters") or {}
    if isinstance(np_, dict) and np_:
        kvs = ", ".join(f"{k}={_fmt_float(v, 2) if isinstance(v, (int,float)) else v}" for k,v in list(np_.items())[:6])
        return f"📝 Recorded for `{cuid}`: {kvs}."
    return None


def _summary_generate_volume_mesh(obs: dict) -> Optional[str]:
    stats = obs.get("statistics") or {}
    nodes = stats.get("num_nodes") or stats.get("nodes")
    cells = stats.get("num_elements") or stats.get("elements") or stats.get("num_cells")
    size = obs.get("mesh_size_bytes")
    parts = []
    if cells: parts.append(f"{_fmt_int(cells)} cells")
    if nodes: parts.append(f"{_fmt_int(nodes)} nodes")
    if size: parts.append(f"~{int(size)//(1024*1024)} MB")
    if parts:
        return f"✅ Mesh built - {', '.join(parts)}."
    return "✅ Mesh built."


def _summary_get_su2_status(obs: dict) -> Optional[str]:
    installed = obs.get("installed")
    if installed is True:
        return "✅ SU2 binaries available."
    if installed is False:
        return "⚠️ SU2 binaries NOT available - will use F25 reference fallback values."
    return None


def _summary_run_su2_solver(obs: dict) -> Optional[str]:
    ec = obs.get("exit_code")
    rt = obs.get("runtime_seconds")
    if ec == 0:
        return f"✅ SU2 solver finished cleanly in {_fmt_float(rt, 1)} s." if rt else "✅ SU2 solver finished cleanly."
    return f"⚠️ SU2 solver exited with code {ec} after {_fmt_float(rt, 1) if rt else '?'} s - checking residuals to decide whether to trust the result."


def _summary_read_history_csv(obs: dict) -> Optional[str]:
    rows = obs.get("total_rows")
    if rows is not None:
        return f"✅ Got {_fmt_int(rows)} convergence-history rows."
    return None


def _summary_estimate_mass(obs: dict) -> Optional[str]:
    masses = obs.get("masses_kg") or obs
    oem = masses.get("oem_kg") if isinstance(masses, dict) else None
    method = obs.get("method_used") or obs.get("mass_method_used")
    warns = obs.get("warnings") or []
    note = f" ({method})" if method else ""
    parts = []
    if oem: parts.append(f"OEM {_fmt_float(oem, 0)} kg{note}")
    if warns:
        parts.append(f"{len(warns)} warning{'s' if len(warns) != 1 else ''} (OAS fallback to FLOPS likely)")
    return f"✅ Mass: {', '.join(parts)}." if parts else None


def _summary_create_cycle_model(obs: dict) -> Optional[str]:
    return "✅ Cycle model built (turbofan, design mode)."


def _summary_run_cycle(obs: dict) -> Optional[str]:
    if obs.get("success"):
        return "✅ Cycle solved at design point."
    return "⚠️ Cycle did not converge - will fall back to F25 reference."


def _summary_get_outputs(obs: dict) -> Optional[str]:
    outs = obs.get("outputs") or obs
    if not isinstance(outs, dict):
        return None
    sfc = outs.get("SFC") or outs.get("sfc") or outs.get("perf.SFC")
    fn = outs.get("Fn") or outs.get("Fn_net") or outs.get("perf.Fn")
    parts = []
    if isinstance(sfc, (int, float)): parts.append(f"SFC {_fmt_float(sfc, 4)}")
    if isinstance(fn, (int, float)): parts.append(f"net thrust {_fmt_int(fn)} lbf")
    return f"✅ Engine perf: {', '.join(parts)}." if parts else None


def _summary_create_session(obs: dict) -> Optional[str]:
    sid = (obs.get("session_id") or "")[:8]
    ac = obs.get("aircraft") or ""
    return f"✅ Aviary session `{sid}…` ({ac.split('-')[0].strip() if ac else 'default aircraft'})."


def _summary_configure_mission(obs: dict) -> Optional[str]:
    if obs.get("success"):
        ms = obs.get("mission_summary") or {}
        r = ms.get("range_nmi"); p = ms.get("num_passengers"); m = ms.get("cruise_mach"); a = ms.get("cruise_altitude_ft")
        bits = []
        if r: bits.append(f"{_fmt_int(r)} nmi")
        if p: bits.append(f"{p} pax")
        if m: bits.append(f"Mach {_fmt_float(m, 2)}")
        if a: bits.append(f"FL{int(a)//100}")
        return f"✅ Mission set: {', '.join(bits)}." if bits else "✅ Mission configured."
    err = obs.get("error", "")
    return f"⚠️ Rejected: {err[:80]}"


def _summary_set_aircraft_parameters(obs: dict) -> Optional[str]:
    applied = obs.get("applied") or []
    warns = obs.get("warnings") or []
    if isinstance(applied, list):
        changes = sum(1 for a in applied if isinstance(a, dict) and not a.get("derived"))
        warn_note = f" - {len(warns)} bound warning{'s' if len(warns) != 1 else ''}" if warns else ""
        return f"✅ Applied {changes} parameter override{'s' if changes != 1 else ''}{warn_note}."
    return None


def _summary_validate_parameters(obs: dict) -> Optional[str]:
    if obs.get("valid"):
        return "✅ Aircraft is feasible - model evaluation produced finite outputs."
    summary = obs.get("summary") or ""
    violations = obs.get("violations") or []
    if violations:
        first = violations[0] if isinstance(violations[0], dict) else {}
        msg = first.get("message", "")[:120]
        return f"❌ Invalid - {msg}"
    return f"❌ {summary[:120]}"


def _summary_run_simulation(obs: dict) -> Optional[str]:
    if obs.get("converged") or obs.get("success"):
        return "🎯 Trajectory optimizer converged - Aviary found a feasible mission."
    err = obs.get("error", "")
    if "AVIARY_SETUP_ERROR" in (obs.get("error_code") or ""):
        return "❌ Setup error - Aviary's Newton solver got inf/NaN before iteration 0 (the parameter set is infeasible for the default aircraft model)."
    return f"⚠️ Optimizer did not converge - {err[:120]}"


def _summary_get_results(obs: dict) -> Optional[str]:
    results = obs.get("results") or obs
    if not isinstance(results, dict):
        return None
    fuel = results.get("fuel_burned_kg") or results.get("FUEL_BURNED_KG")
    gross = results.get("gross_mass_kg") or results.get("GROSS_MASS_KG")
    parts = []
    if isinstance(fuel, (int, float)): parts.append(f"fuel burn **{_fmt_float(fuel, 0)} kg**")
    if isinstance(gross, (int, float)): parts.append(f"MTOM **{_fmt_float(gross, 0)} kg**")
    return f"🎯 {' · '.join(parts)}" if parts else None


def _summary_check_constraints(obs: dict) -> Optional[str]:
    results = obs.get("results") or obs.get("constraints") or {}
    if isinstance(results, dict) and "all_satisfied" in results:
        return "✅ All checkable constraints satisfied." if results["all_satisfied"] else "❌ One or more constraints violated."
    return None


def _summary_validate_cpacs_inputs(obs: dict) -> Optional[str]:
    missing = obs.get("missing") or []
    if not missing:
        return "✅ All required CPACS parameters present."
    return f"⚠️ Missing {len(missing)} optional parameter{'s' if len(missing) != 1 else ''} (mass estimation will use xpath fallback)."


OBSERVATION_SUMMARIZERS: dict[str, Callable[[dict], Optional[str]]] = {
    "open_cpacs":                _summary_open_cpacs,
    "get_configuration_summary": _summary_get_configuration_summary,
    "get_wing_summary":          _summary_get_wing_summary,
    "get_fuselage_summary":      _summary_get_fuselage_summary,
    "set_high_level_parameters": _summary_set_high_level_parameters,
    "generate_volume_mesh":      _summary_generate_volume_mesh,
    "get_su2_status":            _summary_get_su2_status,
    "run_su2_solver":            _summary_run_su2_solver,
    "read_history_csv":          _summary_read_history_csv,
    "estimate_mass":             _summary_estimate_mass,
    "validate_cpacs_inputs":     _summary_validate_cpacs_inputs,
    "create_cycle_model":        _summary_create_cycle_model,
    "run_cycle":                 _summary_run_cycle,
    "get_outputs":               _summary_get_outputs,
    "create_session":            _summary_create_session,
    "configure_mission":         _summary_configure_mission,
    "set_aircraft_parameters":   _summary_set_aircraft_parameters,
    "validate_parameters":       _summary_validate_parameters,
    "run_simulation":            _summary_run_simulation,
    "get_results":               _summary_get_results,
    "check_constraints":         _summary_check_constraints,
}


def summarize_observation(tool_name: str, obs_parsed: dict) -> Optional[str]:
    """Return a short human-readable bullet for the tool's observation,
    or None if we don't have a specialised summary (caller falls back
    to a generic acknowledgement)."""
    if not isinstance(obs_parsed, dict) or not obs_parsed:
        return None
    fn = OBSERVATION_SUMMARIZERS.get(tool_name)
    if not fn:
        return None
    try:
        return fn(obs_parsed)
    except Exception:  # noqa: BLE001 - narrator robustness > completeness
        return None


# ── Stage progress bar ───────────────────────────────────────────────────────

STAGE_ORDER = [
    "geometry_engineer",
    "aerodynamics_analyst",
    "structures_analyst",
    "propulsion_analyst",
    "mission_architect",
    "simulation_executor",
    "mdo_integrator",
]


def stage_progress_line(current_agent: Optional[str], completed: set[str]) -> str:
    """Render a one-line ASCII progress strip across the 7 stages."""
    cells = []
    for a in STAGE_ORDER:
        emoji = {
            "geometry_engineer": "📐",
            "aerodynamics_analyst": "💨",
            "structures_analyst": "⚖️",
            "propulsion_analyst": "🔥",
            "mission_architect": "✈️",
            "simulation_executor": "📊",
            "mdo_integrator": "🧮",
        }[a]
        if a in completed:
            cells.append(f"{emoji}✓")
        elif a == current_agent:
            cells.append(f"{emoji}▶")
        else:
            cells.append(f"{emoji}·")
    return " ".join(cells)
