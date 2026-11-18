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


# B254: bump when the code that produces the baseline changes. The key used to be the CPACS alone, so results from
# old code were reused indefinitely and differed by machine (CD 0.02288 on ws8, 0.02232 on ws5 for one name).
# B306 (audit F9): bumped. "b254" caches were computed before B274-B306 (trim at the CPACS 78,126 kg, untrimmed
# fallbacks, CPACS-mass Fn_DES); they are left on disk (nothing is deleted) and never read again. The trim mass is
# part of the key too, so a changed canonical baseline.sized_mtom_kg never reuses a cache trimmed at another mass.
# G1 (2026-11-18): bumped again -- a "b306" cache has no measured tails (structures.tails); left on disk, never read.
# J2 (2026-11-18): bumped again -- a "b306g1" cache has no measured wing t/c (structures.wing_thickness_to_chord) and
# was flown with J1-less mission bounds; left on disk, never read.
BASELINE_VERSION = "b306g1j2"


def _cache_path(cpacs: str) -> Path:
    from src.tools.data_plane import trim_mass_basis

    digest = hashlib.md5(Path(cpacs).read_bytes()).hexdigest()
    mass, _ = trim_mass_basis()
    tag = f"m{mass:.1f}" if mass is not None else "mnone"
    return CACHE_DIR / f"{digest}.{BASELINE_VERSION}.{tag}.design.json"


def _size_engine_session(design: dict | None = None) -> None:
    """Put the mission's flight state, the design's sizing and its engine inputs on the pyCycle session.

    B254: on a seeded link nothing else did. seed_from_design records the engine RESULT, so run_cycle was not
    needed and the session stayed on the tool's defaults, M0.80 / 35,000 ft. The first mission's engine deck was
    built from that session: a different engine from the M0.78 / 33,000 ft cycle whose SFC had been seeded.
    """
    from src.tools.data_plane import get_design_state

    engine = ((design or {}).get("design_space") or {}).get("engine") or {}
    _call("set_inputs", session_id=(get_design_state().sessions or {}).get("pycycle"), values=dict(engine))


def _record_seeded_aero(store: dict, aero: dict, source: str = "the last flown link") -> None:
    """B344 (coordinator 2026-11-18, review #9): the seeded aero is kept, but recorded as what it is -- solved on the last
    flown link with ITS trim target (its trim mass), not at this link's trim mass -- in design.json (aero.provenance)
    and in the knowledge base, so nobody reads it as this link's current solve. A new SU2 solve on this link replaces
    the provenance (data_plane._note_solve_trim)."""
    from src.tools.data_plane import trim_mass_basis

    basis = aero.get("solve_trim_basis") if isinstance(aero.get("solve_trim_basis"), dict) else {}
    store["aero_solve_trim_basis"] = basis or None
    mass_then = basis.get("mass_kg")
    mass_now, now_source = trim_mass_basis()
    text = (f"SU2 aero SEEDED from {source} by graph pre-init, not solved on this link: CL "
            f"{aero.get('cl_cruise')}, CD {aero.get('cd_cruise')}, trimmed there to target CL {aero.get('solve_target_cl')} "
            + (f"at trim mass {float(mass_then):.1f} kg" if isinstance(mass_then, (int, float)) else
               "at a trim mass not recorded in that design.json")
            + "; this link's trim mass is "
            + (f"{float(mass_now):.1f} kg ({now_source})" if mass_now is not None else f"not available ({now_source})")
            + ". Re-solve SU2 for a solve at this link's trim mass.")
    store["aero_provenance"] = {"source": f"seeded from {source} (graph pre-init)",
                                "trim_target_cl": aero.get("solve_target_cl"), "trim_mass_kg": mass_then,
                                "this_link_trim_mass_kg": mass_now, "solved_on_this_link": False, "note": text}
    try:
        from src.tools.knowledge_base import get_kb

        kb = get_kb()
        if kb is not None:
            kb.append(tool="graph_preinit_seed_aero", server="framework", agent="framework", role="graph pre-init",
                      status="success", outputs={"aero_provenance": dict(store["aero_provenance"])}, note=text,
                      discipline="aerodynamics")
    except Exception as exc:      # pragma: no cover - recording must never stop pre-init
        logger.warning("[B344] could not record the seeded aero in the knowledge base: %s", exc)


def seed_from_design(design: dict, source: str = "the last flown link") -> list[str]:
    """Put a recorded design.json's discipline results into the data plane (as scripts/stage_sandbox.py).

    B306: a mass result is seeded only when its sizing converged (audit F4: no CPACS-mass fallback), the aero keeps
    its solve's trim and convergence record (B277 / F7), and every seeded result is stamped with the fingerprint of
    the design it belongs to -- the carried CPACS just opened -- so a morph in this link makes it stale like any
    captured result (audit F8; before, seeded results had no fingerprint and were never stale).
    """
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
    producers = []
    aero = design.get("aero") or {}
    if aero.get("cl_cruise") is not None and aero.get("cd_cruise") is not None:
        store["aero_cl_cruise"], store["aero_cd_cruise"] = aero["cl_cruise"], aero["cd_cruise"]
        coupling.put_var(ds, "aero.cl_cruise", aero["cl_cruise"], source_tool="graph pre-init")
        coupling.put_var(ds, "aero.cd_cruise", aero["cd_cruise"], source_tool="graph pre-init")
        if aero.get("drag_assessment"):
            store["aero_drag_assessment"] = aero["drag_assessment"]
        # B277 / B306: the seeded aero keeps its solve's record; a design.json without it seeds "unknown", which
        # aero_guard refuses (audit F7).
        store["aero_solve_trimmed"] = aero.get("solve_trimmed")
        store["aero_solve_target_cl"] = aero.get("solve_target_cl")
        store["aero_solve_converged"] = aero.get("solve_converged")
        _record_seeded_aero(store, aero, source)
        seeded.append("aero")
        producers.append("su2")
    st = design.get("structures") or {}
    if st.get("sizing_converged") is True and st.get("wing_mass_kg") is not None and st.get("mtom_kg") is not None:
        store["mass_wing_kg"] = st["wing_mass_kg"]
        coupling.put_var(ds, "mass.wing_kg", st["wing_mass_kg"], source_tool="graph pre-init")
        store["mass_mtom_kg"] = st["mtom_kg"]
        coupling.put_var(ds, "mass.mtom_kg", st["mtom_kg"], source_tool="graph pre-init")
        store["mass_sizing_converged"] = True
        # G1: the measured tails come with the mass result they were sized with; a design.json without them (older
        # code) seeds none, and the mission is refused until estimate_mass runs on this design.
        from src.tools.data_plane import TAIL_KEYS

        tails = st.get("tails") if isinstance(st.get("tails"), dict) else None
        if tails and all(isinstance(tails.get(k), (int, float)) for k in TAIL_KEYS):
            store["geometry_tails"] = {k: float(tails[k]) for k in TAIL_KEYS}
            store["geometry_tails"]["_source"] = f"graph pre-init ({st.get('tails_source')})"
        else:
            store["tail_geometry_error"] = "the seeded design.json has no measured tails (structures.tails)"
        # J2: the measured wing t/c comes with the same mass result; without it the mission is refused until
        # estimate_mass runs on this design.
        from src.tools.data_plane import WING_TC_KEY

        tc = st.get("wing_thickness_to_chord")
        if isinstance(tc, (int, float)) and not isinstance(tc, bool):
            store["geometry_wing_tc"] = {WING_TC_KEY: float(tc),
                                         "_source": f"graph pre-init ({st.get('wing_thickness_to_chord_source')})"}
        else:
            store["wing_tc_error"] = ("the seeded design.json has no measured wing thickness-to-chord "
                                      "(structures.wing_thickness_to_chord)")
        seeded.append("structures")
        producers.append("mass")
    elif st:
        store["mass_seed_skipped"] = (f"structures not seeded: sizing_converged {st.get('sizing_converged')!r}, "
                                      f"mtom_kg {st.get('mtom_kg')!r}")
    # B340 (review #9): the engine RESULT is not seeded. Its sizing key (mass basis, aviary cruise point of THIS
    # geometry session) cannot be reproduced from design.json, so a seeded result was never current (prop_cycle_key
    # None) yet still sat in the registry -- a half state. The engine INPUTS are seeded (design_space.engine below) and
    # prepare() re-sizes and runs the cycle on this link (_resize_engine): the engine is then either current, with
    # its cruise point and key recorded, or explicitly not current with the reason (engine_seed_note).
    if (design.get("propulsion") or {}).get("sfc_cruise") is not None:
        store["engine_seed_note"] = "engine result not seeded: re-sized and run on this link by graph pre-init (B340)"
    if producers:
        from src.tools.coupling_contract import current_design_fingerprint

        fp = current_design_fingerprint()
        for producer in producers:
            store[f"_coupled_fp_{producer}"] = fp
    space = design.get("design_space") or {}
    if space.get("geometry"):
        store["design_space_components"] = {k: dict(v) for k, v in space["geometry"].items()}
    if space.get("engine"):
        store["design_space_engine"] = dict(space["engine"])
    return seeded


def seed_mass_from_design(design: dict) -> float | None:
    """Put a recorded design.json's sized MTOM into the data plane before the SU2 session opens (B276).

    B306: only a converged sizing (audit F4). The SU2 trim itself no longer reads this (data_plane.trim_mass_basis
    takes the carried sized mass from carry_over); the seed is the MTOM Fn_DES and the take-off mass start from.
    """
    from src.tools import coupling
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    st = design.get("structures") or {}
    mtom = st.get("mtom_kg")
    if mtom is None or st.get("sizing_converged") is not True:
        return None
    ds.data_store["mass_mtom_kg"] = mtom
    ds.data_store["mass_sizing_converged"] = True
    coupling.put_var(ds, "mass.mtom_kg", mtom, source_tool="graph pre-init")
    return float(mtom)


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


def _resize_engine(design: dict | None, report: dict) -> None:
    """B340 (review #9): size the engine on this link's design and run the cycle, through the middleware.

    set_inputs gets Fn_DES from the carried mass and aviary's cruise point of the current geometry (the framework asks
    aviary for it); run_cycle records the result with its sizing key. When that is not possible (no mass basis, no
    cruise point) the engine is explicitly not current and the reason is recorded -- the agents' run_cycle re-sizes.
    """
    from src.tools.data_plane import engine_is_current, get_design_state

    _size_engine_session(design)
    _call("run_cycle", session_id=(get_design_state().sessions or {}).get("pycycle"))
    current = engine_is_current()
    report["engine_current"] = current
    if not current:
        store = get_design_state().data_store
        why = store.get("engine_sizing_error") or "the cycle was not sized or did not converge"
        store["engine_seed_note"] = f"engine not current after graph pre-init: {why}; the link must re-size it"
        report["engine_not_current"] = store["engine_seed_note"]


def _seed_source(carry_over: dict, cpacs: str) -> tuple[dict | None, str]:
    """B340 (review #5): the recorded design whose results may be seeded on this link, or None with the reason.

    The last link that FLEW (carry_over.last_flown_design, as trim_mass_basis / carried_engine use), and only when it
    flew THIS link's geometry: when an unflown link exported another wing (G2), the flown design's mass, tails and aero
    belong to G1 and stamping them with G2's fingerprint made them count as G2's results. Then nothing is seeded and
    the results are computed on G2 (cache / baseline path). A carry_over of an older runner (no last_flown_design)
    seeds its previous_design as before.
    """
    if "last_flown_design" not in carry_over:
        previous = carry_over.get("previous_design")
        return (previous, "previous link's end-state") if isinstance(previous, dict) and previous.get("aero") \
            else (None, "no previous design")
    design = carry_over.get("last_flown_design")
    if not (isinstance(design, dict) and design.get("aero")):
        return None, "no link has flown yet"
    flown_geometry = carry_over.get("last_flown_geometry") or baseline_cpacs()
    try:
        same = hashlib.md5(Path(flown_geometry).read_bytes()).hexdigest() == \
            hashlib.md5(Path(cpacs).read_bytes()).hexdigest()
    except OSError as exc:
        return None, f"the last flown geometry {flown_geometry} cannot be compared ({exc}); nothing seeded"
    if not same:
        return None, (f"the last flown link flew {flown_geometry}, this link starts from {cpacs}: its results are not "
                      "this geometry's, so they are computed here (B340)")
    return design, "last flown link's end-state"


def _compute_baseline(cpacs: str, mission: dict) -> None:
    """The discipline results on this design, through the same tools and middleware an agent would use."""
    from src.tools.data_plane import get_design_state

    sessions = get_design_state().sessions or {}
    _call("estimate_mass", cpacs_file_path=cpacs)
    _call("run_su2_solver", session_id=sessions.get("su2"), max_runtime_seconds=3600)
    _size_engine_session()
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
        previous, seed_note = _seed_source(carry_over, cpacs)
        report["seed_source"] = seed_note
        if previous is not None:
            # B276 (l20_final Case 3.3): the SU2 session below takes its cruise-lift target from the mass on record.
            # It used to be created BEFORE seed_from_design put the carried mass there, so the target fell back to the
            # CPACS-declared 78,126 kg while any later re-configure used the carried, sized mass (O_GR 13/17/19: CL
            # 0.715 against 0.624 on the same wing, +9.5 t fuel). Seed the carried mass first.
            report["mass_seeded_before_su2"] = seed_mass_from_design(previous)
        report.update(_open_sessions(cpacs, mission))
        if previous is not None:
            report["results"] = seed_note
            report["seeded"] = seed_from_design(previous)
            _resize_engine(previous, report)
        else:
            cache = _cache_path(cpacs)
            if cache.is_file():
                report["results"] = f"cached baseline {cache.name}"
                cached = json.loads(cache.read_text())
                report["seeded"] = seed_from_design(cached, source=f"the cached baseline {cache.name}")
                _resize_engine(cached, report)
            else:
                report["results"] = "baseline computed now (first run for this CPACS)"
                _compute_baseline(cpacs, mission)
                # Cache what was just computed, built from the design state itself -- not read back from
                # design.json, which something else may have written (the hand-off sandbox keeps a recorded
                # one in place, and an earlier cache held that recorded design instead of the baseline).
                from src.tools.design_json import build_design

                state = get_design_state()
                doc = build_design(state.data_store or {}, state.sessions or {})
                # B306: only a usable baseline is cached -- a trimmed, converged SU2 solve and a converged mass
                # sizing -- or every later link 1 would reuse a bad one.
                aero, st = doc.get("aero") or {}, doc.get("structures") or {}
                problems = [name for name, ok in (
                    ("no SU2 CL/CD", aero.get("cl_cruise") is not None),
                    ("the SU2 solve was not trimmed", aero.get("solve_trimmed") is True),
                    ("the SU2 solve did not converge", aero.get("solve_converged") is True),
                    ("the mass sizing did not converge", st.get("sizing_converged") is True),
                    ("no measured tails (G1)", isinstance(st.get("tails"), dict))) if not ok]
                if not problems:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(doc, indent=1, default=str))
                    report["cached_as"] = cache.name
                else:
                    report["cache_skipped"] = "; ".join(problems)
        write_design(get_design_state())
    except Exception as exc:  # pragma: no cover - pre-init must not stop a link; the agents can still work
        logger.warning("[graph pre-init] %s", exc)
        report["error"] = str(exc)
    logger.info("[graph pre-init] %s", report)
    return report
