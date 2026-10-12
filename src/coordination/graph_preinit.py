"""Graph-routed v2 pre-initialization (Jessica, 2026-10-12; graph-routed combinations ONLY).

Graph-routed may enter the pipeline in the middle -- SIMPLE starts at the mission -- so before the first
agent turn of a link the framework prepares what any node needs: the TiGL session on the CPACS (the
baseline on link 1, the carried design afterwards), the default volume mesh, the SU2 session with that
mesh, the pyCycle session, and the discipline results (aero, mass, engine) in the coupling registry and
design.json, exactly as if agents had produced them.

Results: link 1 uses a baseline computed ONCE per CPACS and cached (it is the same every run); later
links use the previous link's design.json (its end-state). Every call goes through the registered,
middleware-wrapped tools, so the data plane injects and captures as it does for an agent's call.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAS = Path(__file__).resolve().parents[2]
CACHE_DIR = _MAS / "logs" / "graph_baselines"


def _call(name: str, **kwargs) -> dict:
    from src.tools.data_plane import _registered_tools

    tool = (_registered_tools or {}).get(name)
    if tool is None:
        return {"error": f"{name} is not registered"}
    try:
        out = tool.forward(**kwargs)
    except Exception as exc:  # pragma: no cover - a pre-init call must never stop the link
        return {"error": f"{name}: {exc}"}
    if isinstance(out, str):
        try:
            return json.loads(out)
        except ValueError:
            return {"raw": out[:500]}
    return out if isinstance(out, dict) else {"raw": str(out)[:500]}


def baseline_cpacs() -> str:
    from src.config.canonical import AVION_ROOT

    return str(AVION_ROOT / "mass-mcp" / "tests" / "fixtures" / "D150_simple.xml")


def _cache_path(cpacs: str) -> Path:
    digest = hashlib.md5(Path(cpacs).read_bytes()).hexdigest()
    return CACHE_DIR / f"{digest}.design.json"


def seed_from_design(design: dict) -> list[str]:
    """Put a recorded design.json's discipline results into the data plane (as scripts/stage_sandbox.py)."""
    from src.tools import coupling
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    store, seeded = ds.data_store, []
    geo = design.get("geometry") or {}
    if geo.get("wing"):
        store["geometry_wing"] = dict(geo["wing"], _source="graph pre-init")
        seeded.append("geometry.wing")
    if geo.get("fuselage"):
        store["geometry_fuselage"] = dict(geo["fuselage"], _source="graph pre-init")
    aero = design.get("aero") or {}
    if aero.get("cl_cruise") is not None and aero.get("cd_cruise") is not None:
        store["aero_cl_cruise"], store["aero_cd_cruise"] = aero["cl_cruise"], aero["cd_cruise"]
        coupling.put_var(ds, "aero.cl_cruise", aero["cl_cruise"], source_tool="graph pre-init")
        coupling.put_var(ds, "aero.cd_cruise", aero["cd_cruise"], source_tool="graph pre-init")
        if aero.get("drag_assessment"):
            store["aero_drag_assessment"] = aero["drag_assessment"]
        seeded.append("aero")
    st = design.get("structures") or {}
    if st.get("wing_mass_kg") is not None:
        store["mass_wing_kg"] = st["wing_mass_kg"]
        coupling.put_var(ds, "mass.wing_kg", st["wing_mass_kg"], source_tool="graph pre-init")
        seeded.append("structures.wing_mass_kg")
    if st.get("mtom_kg") is not None:
        store["mass_mtom_kg"] = st["mtom_kg"]
        coupling.put_var(ds, "mass.mtom_kg", st["mtom_kg"], source_tool="graph pre-init")
    prop = design.get("propulsion") or {}
    if prop.get("sfc_cruise") is not None:
        store["prop_cycle_outputs"] = {"prop.sfc_cruise": prop.get("sfc_cruise"), "prop.fn_lbf": prop.get("fn_lbf")}
        store["prop_cycle_converged"] = prop.get("cycle_converged")
        coupling.put_var(ds, "prop.sfc_cruise", prop["sfc_cruise"], source_tool="graph pre-init")
        if prop.get("fn_lbf") is not None:
            coupling.put_var(ds, "prop.fn_lbf", prop["fn_lbf"], source_tool="graph pre-init")
        seeded.append("propulsion")
    space = design.get("design_space") or {}
    if space.get("geometry"):
        store["design_space_components"] = {k: dict(v) for k, v in space["geometry"].items()}
    if space.get("engine"):
        store["design_space_engine"] = dict(space["engine"])
    return seeded


def _open_sessions(cpacs: str, mission: dict) -> dict:
    from src.tools.data_plane import get_design_state

    report = {"cpacs": cpacs}
    _call("open_cpacs", source_type="path", source=cpacs)
    sid = (get_design_state().sessions or {}).get("tigl")
    for uid in ("Wing1", "Fuselage1"):
        _call("get_high_level_parameters", session_id=sid, component_uid=uid)
    mesh = _call("generate_volume_mesh", session_id=sid, output_format="su2")
    report["mesh_elements"] = (mesh.get("statistics") or {}).get("element_count")
    su2 = _call("create_su2_session", cpacs_file_path=cpacs, cruise_mach=mission.get("cruise_mach", 0.78),
                cruise_altitude_ft=mission.get("cruise_altitude_ft", 33000))
    report["su2_configured"] = su2.get("configured")
    _call("create_cycle_model", cycle_type="turbofan", mode="design")
    report["sessions"] = dict(get_design_state().sessions or {})
    return report


def _compute_baseline(cpacs: str, mission: dict) -> None:
    """The discipline results on this design, through the same tools and middleware an agent would use."""
    from src.tools.data_plane import get_design_state

    sessions = get_design_state().sessions or {}
    _call("estimate_mass", cpacs_file_path=cpacs)
    _call("run_su2_solver", session_id=sessions.get("su2"), max_runtime_seconds=3600)
    _call("set_inputs", session_id=sessions.get("pycycle"), values={})
    _call("run_cycle", session_id=sessions.get("pycycle"))


def prepare(carry_over: dict | None) -> dict:
    """Open the sessions and load (or compute) the discipline results for this link. Never raises."""
    from src.config.canonical import load_canonical
    from src.tools.design_json import write_design
    from src.tools.data_plane import get_design_state

    report: dict = {}
    try:
        mission = load_canonical().get("mission", {}) or {}
        carry_over = carry_over or {}
        start = carry_over.get("starting_design") or {}
        cpacs = start.get("CPACS_FILE") if start.get("CPACS_FILE") and Path(start["CPACS_FILE"]).is_file() \
            else baseline_cpacs()
        report.update(_open_sessions(cpacs, mission))
        previous = carry_over.get("previous_design")
        if isinstance(previous, dict) and previous.get("aero"):
            report["results"] = "previous link's end-state"
            report["seeded"] = seed_from_design(previous)
        else:
            cache = _cache_path(cpacs)
            if cache.is_file():
                report["results"] = f"cached baseline {cache.name}"
                report["seeded"] = seed_from_design(json.loads(cache.read_text()))
            else:
                report["results"] = "baseline computed now (first run for this CPACS)"
                _compute_baseline(cpacs, mission)
                # Cache what was just computed, built from the design state itself -- not read back from
                # design.json, which something else may have written (the hand-off sandbox keeps a recorded
                # one in place, and an earlier cache held that recorded design instead of the baseline).
                from src.tools.design_json import build_design

                state = get_design_state()
                doc = build_design(state.data_store or {}, state.sessions or {})
                if (doc.get("aero") or {}).get("cl_cruise") is not None:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(doc, indent=1, default=str))
                    report["cached_as"] = cache.name
                else:
                    report["cache_skipped"] = "the baseline SU2 solve produced no CL/CD"
        write_design(get_design_state())
    except Exception as exc:  # pragma: no cover - pre-init must not stop a link; the agents can still work
        logger.warning("[graph pre-init] %s", exc)
        report["error"] = str(exc)
    logger.info("[graph pre-init] %s", report)
    return report
