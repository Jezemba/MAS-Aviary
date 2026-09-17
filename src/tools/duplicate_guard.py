"""Once-only duplicate-work guard (B81 part B).

WHY THIS EXISTS

networked_graph_routed link 1 on the 7960 made 6 volume meshes, 5 SU2 solves and
6 open_cpacs calls in ~3 h (validate3_7960): peers redid finished work because
nothing told them it was done or where the result was.

BEHAVIOUR (Jessica's specification, handoff section 3.1; decisions section 7)

For a once-only tool, a call that repeats work already DONE for the same design
is refused -- inside the middleware, before the MCP server -- with who did it,
when, and where the result is. The refusal is information, not a wall:

  * the refusal is issued once per (agent, tool); that agent's next call runs
    and is recorded as a deliberate repeat;
  * a different agent gets its own first refusal;
  * the agent that did the work is refused too, with an explicit reason
    (decided 2026-09-15, section 7.1);
  * a changed design (new fingerprint) runs normally;
  * only a SUCCESSFUL prior result counts; a failed or refused one does not;
  * a call already IN PROGRESS for the same fingerprint counts as done, so two
    concurrent peers cannot both run it;
  * tools not in ONCE_ONLY_TOOLS are never touched.

"The same work" is the design fingerprint (section 3.3), computed from the
resolved call arguments plus the design state this module tracks from earlier
successful calls: per tigl session a geometry epoch bumped by every successful
geometry change, per SU2 session its mesh and configuration, and which CPACS
sessions are still open.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Callable

_LOCK = threading.RLock()
_blackboard = None   # registered by the networked strategy (the only structure with one)

GEOMETRY_CHANGE_TOOLS = frozenset({"set_high_level_parameters", "morph_wing", "morph_fuselage"})
SU2_CONFIG_TOOLS = frozenset({"update_config_entries", "configure_from_cpacs"})
_MESH_ARGS = ("component_uid", "far_field_distance", "mesh_size_min", "mesh_size_max", "surface_mesh_size",
              "boundary_layer_enabled", "boundary_layer_thickness", "boundary_layer_layers",
              "boundary_layer_growth", "output_format", "format", "meshing_options")
_MASS_CONFIG_EXCLUDE = frozenset({"cpacs_file_path", "write_back_to_cpacs"})


def register_blackboard(blackboard) -> None:
    global _blackboard
    _blackboard = blackboard


def _unclaimed_work_hint() -> str:
    """Name the TODOs nobody has claimed, so "wait" is never the only option (B85)."""
    board = _blackboard
    if board is None:
        return (" Do not sit idle: call read_todos for unclaimed work, and read_procedure() for "
                "which stages depend on which -- structures and propulsion need no aero at all.")
    try:
        pending = [t.name for t in board.read_pending_todos()]
    except Exception:      # pragma: no cover - a hint must never break a refusal
        return ""
    if not pending:
        return " Nothing else is unclaimed, so waiting is correct here."
    return (" Do NOT sit idle while you wait -- this costs you a step. Unclaimed right now: "
            + ", ".join(pending) + ". Call claim_todo(<name>) and do one of those; "
            "read_procedure(role='<name>') lists its tools in order. Structures and propulsion "
            "need no aero at all, so they can run while this solve finishes.")


def _h(obj: Any) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _payload_hash(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return hashlib.sha1(value.encode()).hexdigest()[:16]
    return _h(value)


def _file_hash(path: str) -> str | None:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return None


def _state() -> dict | None:
    from src.tools.knowledge_base import get_kb

    kb = get_kb()
    if kb is None:
        return None
    gs = kb.guard_state
    gs.setdefault("geometry_epoch", {})       # tigl session -> int
    gs.setdefault("open_cpacs", {})           # tigl session -> {"fp", "seq"}
    gs.setdefault("su2", {})                  # su2 session -> {"mesh", "config", "live", "mesh_seq"}
    gs.setdefault("in_flight", {})            # (tool, fp) -> {"agent", "time_utc"}
    gs.setdefault("refused_keys", set())      # (agent, tool) whose one refusal is spent
    return gs


# -- fingerprints (section 3.3) --------------------------------------------------------

def _fp_mesh(tool: str, args: dict, gs: dict) -> str | None:
    sid = args.get("session_id")
    epoch = gs["geometry_epoch"].get(sid, 0)
    return "geo:" + _h({"tool": tool, "session": sid, "epoch": epoch,
                        "args": {k: args.get(k) for k in _MESH_ARGS if args.get(k) is not None}})


def _su2_signature(sess: dict) -> dict:
    return {"mesh": sess.get("mesh"), "config": sess.get("config", {})}


def _fp_su2_solve(tool: str, args: dict, gs: dict) -> str | None:
    sess = gs["su2"].get(args.get("session_id"))
    if not sess or not sess.get("mesh"):
        return None      # no mesh on this session: nothing to repeat; the solver will say why
    return "su2:" + _h({**_su2_signature(sess), "solver": args.get("solver") or "SU2_CFD",
                        "override": args.get("config_override_path")})


def _latest_mesh_hash() -> str | None:
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    payload = ((getattr(ds, "data_store", None) or {}).get("generate_volume_mesh__mesh_base64")) if ds else None
    return _payload_hash(payload) if isinstance(payload, str) and len(payload) > 100 else None


def _fp_su2_session(tool: str, args: dict, gs: dict) -> str | None:
    mesh = _latest_mesh_hash()
    return f"su2session:{mesh}" if mesh else None


def _fp_cycle(tool: str, args: dict, gs: dict) -> str | None:
    return "cycle:" + _h({k: v for k, v in args.items()})


def _fp_open_cpacs(tool: str, args: dict, gs: dict) -> str | None:
    src = args.get("source")
    if args.get("source_type") != "path" or not isinstance(src, str) or not os.path.isfile(src):
        return None
    st = os.stat(src)
    return "cpacs:" + _h({"path": os.path.abspath(src), "size": st.st_size, "mtime": st.st_mtime})


def _fp_mass(tool: str, args: dict, gs: dict) -> str | None:
    path = args.get("cpacs_file_path")
    if not isinstance(path, str) or not os.path.isfile(path):
        return None
    # The G1 working copy gains a mass block on every write-back, so hash the
    # geometry it was copied FROM: the file the mass estimate actually sizes.
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    copies = ((getattr(ds, "data_store", None) or {}).get("_mass_working_copies") or {}) if ds else {}
    source = next((s for s, e in copies.items() if e.get("copy") == os.path.abspath(path)), path)
    geometry = _file_hash(source)
    if geometry is None:
        return None
    return "mass:" + _h({"geometry": geometry,
                         "config": {k: v for k, v in args.items() if k not in _MASS_CONFIG_EXCLUDE}})


ONCE_ONLY_TOOLS: dict[str, Callable[[str, dict, dict], str | None]] = {
    "generate_volume_mesh": _fp_mesh,
    "export_component_mesh": _fp_mesh,
    "run_su2_solver": _fp_su2_solve,
    "create_su2_session": _fp_su2_session,
    "create_cycle_model": _fp_cycle,
    "open_cpacs": _fp_open_cpacs,
    "estimate_mass": _fp_mass,      # added by Jessica 2026-09-15 (section 7.2)
}


def fingerprint(tool: str, args: dict) -> str | None:
    """Design fingerprint for a once-only call, or None when the tool is not guarded."""
    fn = ONCE_ONLY_TOOLS.get(tool)
    if fn is None:
        return None
    with _LOCK:
        gs = _state()
        if gs is None:
            return None
        try:
            return fn(tool, dict(args or {}), gs)
        except Exception:  # pragma: no cover - a fingerprint bug must never block work
            return None


# -- prior work lookup ---------------------------------------------------------------

def _prior(tool: str, fp: str, gs: dict) -> dict | None:
    from src.tools.knowledge_base import get_kb

    kb = get_kb()
    if tool == "open_cpacs":
        # Done only while a session opened from this exact file is still open.
        for sid, info in gs["open_cpacs"].items():
            if info["fp"] == fp:
                entry = next((e for e in kb.entries if e["seq"] == info["seq"]), None)
                if entry:
                    return {**entry, "_result": f"session_id {sid} (still open)"}
        return None
    if tool == "create_su2_session":
        # Done when a live SU2 session already holds this design's mesh.
        mesh = fp.split(":", 1)[1]
        for sid, sess in gs["su2"].items():
            if sess.get("live") and sess.get("mesh") == mesh:
                entry = next((e for e in kb.entries if e["seq"] == sess.get("mesh_seq")), None)
                if entry:
                    return {**entry, "_result": f"SU2 session_id {sid}, which already has this design's mesh"}
        return None
    for e in reversed(kb.entries):
        # B87: a success with nothing to show for it is not a duplicate. A refusal that
        # says "Use its result: {}" points at nothing and locks the tool for everyone.
        if e["tool"] == tool and e.get("design_fingerprint") == fp and e["status"] == "success" \
                and _has_usable_result(e):
            return e
    return None


def _has_usable_result(entry: dict) -> bool:
    """Did this call leave anything at all to point at? (B87)

    Narrow on purpose: this is the "Use its result: {}" case from validate9 and nothing more.
    A terse-but-real success still counts as work done, because B81's job is to stop the work
    being repeated, not to judge how informative the response was. What must never happen is a
    refusal that cites nothing -- that is the shape a phantom success takes.
    """
    return bool(entry.get("_result")) or bool(entry.get("outputs"))



def _next_step_hint(tool: str) -> str:
    """What the caller should do NEXT, appended to an ALREADY_DONE refusal (B93).

    validate11: the geometry holder was refused a repeated open_cpacs and its following steps were
    a re-claim and another open_cpacs -- it did not know that generate_volume_mesh was next. The
    procedure table knows, and the knowledge base knows what has already succeeded, so say it here
    rather than hoping for a read_procedure call that has never once been made.
    """
    try:
        from src.tools.procedures import next_step_line, role_for_todo
        from src.tools.work_claims import todo_for_tool

        from src.tools.procedures import _succeeded_tools

        todo = todo_for_tool(tool)
        if not todo:
            return ""
        # The refused call IS this tool, so count it as done even if the knowledge base is
        # unavailable -- otherwise the hint would name the very tool we just refused.
        line = next_step_line(role_for_todo(todo), _succeeded_tools() | {tool})
        return (" " + line) if line else ""
    except Exception:      # pragma: no cover - a hint must never break a refusal
        return ""


def _result_text(prior: dict) -> str:
    if prior.get("_result"):
        return prior["_result"]
    outs = {k: v for k, v in (prior.get("outputs") or {}).items() if v not in (None, "", {}, [])}
    text = json.dumps(outs, default=str)
    return text if len(text) <= 400 else text[:380] + "...}"


# -- the guard -------------------------------------------------------------------------

def check(tool: str, fp: str | None, agent: str) -> dict | None:
    """Refusal payload for a duplicate, or None to let the call run.

    When the call may run, it is registered as in progress; release() must be
    called when it finishes, whatever the outcome.
    """
    if fp is None:
        return None
    from src.tools.knowledge_base import get_kb

    with _LOCK:
        gs = _state()
        if gs is None:
            return None
        kb = get_kb()
        prior = _prior(tool, fp, gs)
        running = gs["in_flight"].get((tool, fp))
        if prior is None and running is None:
            gs["in_flight"][(tool, fp)] = {"agent": agent, "time_utc": _now()}
            return None
        key = (agent, tool)
        if key in gs["refused_keys"]:
            # This agent was already told once; its choice to repeat is allowed and measured.
            kb.bump_nested("duplicate_repeats_after_refusal", agent, tool)
            gs["in_flight"][(tool, fp)] = {"agent": agent, "time_utc": _now()}
            return {"_deliberate_repeat": True}
        gs["refused_keys"].add(key)
        kb.bump_nested("duplicate_refusals", agent, tool)
        if prior is None:
            who = "you" if running["agent"] == agent else running["agent"]
            message = (
                f"{who} {'are' if who == 'you' else 'is'} already running {tool} for this design "
                f"(started {running['time_utc']}). Wait for it to finish and use its result: call "
                f"read_design_knowledge(tool='{tool}'). If it really must be run again, call it again "
                "and it will run."
                # B85/2.3: a peer told to wait has nothing to do, and in validate8_net spent a
                # 400-900 s step deciding to wait -- while aero, mass and propulsion all sat
                # pending, and mass and propulsion depend on no aero at all. Name the work.
                + _unclaimed_work_hint()
            )
            done_by, seq = running["agent"], None
        else:
            own = prior["agent"] == agent
            subject = f"you ({agent}) already did" if own else f"{prior['agent']} already did"
            message = (
                f"{subject} {tool} for this design (seq {prior['seq']}, {prior['time_utc']}). "
                f"Use its result: {_result_text(prior)}.{_next_step_hint(tool)} "
                f"Call read_design_knowledge(tool='{tool}') "
                "for details. If it really must be redone, call it again and it will run."
            )
            done_by, seq = prior["agent"], prior["seq"]
        # B85: same step-budget warning as the missing-data refusal -- 11 ALREADY_DONE
        # refusals in one link, and a refused step costs 200-900 s like any other.
        from src.tools.coupling_contract import refusals_so_far
        from src.tools.procedures import budget_warning

        message += budget_warning(refusals_so_far(agent) + 1)
        return {"success": False, "error_code": "ALREADY_DONE", "error": message,
                "done_by": done_by, "kb_seq": seq, "design_fingerprint": fp}


def release(tool: str, fp: str | None) -> None:
    if fp is None:
        return
    with _LOCK:
        gs = _state()
        if gs is not None:
            gs["in_flight"].pop((tool, fp), None)


def observe(tool: str, args: dict, result: Any, status: str, entry: dict | None) -> None:
    """Update tracked design state after a call, and mirror completed once-only work."""
    if status != "success":
        return
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            parsed = {}
    parsed = parsed if isinstance(parsed, dict) else {}
    seq = entry["seq"] if entry else None
    with _LOCK:
        gs = _state()
        if gs is None:
            return
        sid = args.get("session_id") or parsed.get("session_id")
        if tool in GEOMETRY_CHANGE_TOOLS:
            gs["geometry_epoch"][sid] = gs["geometry_epoch"].get(sid, 0) + 1
        elif tool == "open_cpacs" and parsed.get("session_id"):
            gs["geometry_epoch"][parsed["session_id"]] = 0
            fp = entry.get("design_fingerprint") if entry else None
            if fp:
                gs["open_cpacs"][parsed["session_id"]] = {"fp": fp, "seq": seq}
        elif tool == "close_cpacs":
            gs["open_cpacs"].pop(args.get("session_id"), None)
        elif tool == "create_su2_session" and parsed.get("session_id"):
            mesh = _payload_hash(args.get("initial_mesh"))
            gs["su2"][parsed["session_id"]] = {
                "mesh": mesh, "mesh_seq": seq if mesh else None, "live": True,
                "config": {"created": _h({k: v for k, v in args.items() if k != "initial_mesh"})},
            }
        elif tool == "set_mesh" and sid in gs["su2"]:
            gs["su2"][sid].update(mesh=_payload_hash(args.get("mesh_base64")) or _h(args), mesh_seq=seq)
        elif tool == "generate_deformed_mesh" and sid in gs["su2"]:
            gs["su2"][sid].update(mesh="deformed:" + _h([args, seq]), mesh_seq=seq)
        elif tool in SU2_CONFIG_TOOLS and sid in gs["su2"]:
            cfg = gs["su2"][sid].setdefault("config", {})
            if tool == "update_config_entries":
                cfg.update({str(k): v for k, v in (args.get("updates") or {}).items()})
            else:
                cfg["from_cpacs"] = _h({k: v for k, v in args.items() if k != "session_id"})
        elif tool == "close_su2_session" and sid in gs["su2"]:
            gs["su2"][sid]["live"] = False
    if tool in ONCE_ONLY_TOOLS and entry and _blackboard is not None:
        try:
            _blackboard.write(
                key=f"done:{tool}:{entry.get('design_fingerprint')}",
                value=json.dumps({"tool": tool, "kb_seq": seq, "result": _result_text(entry),
                                  "note": "completed once-only work; reuse the result"}, default=str),
                author=entry["agent"], entry_type="done",
            )
        except Exception:  # pragma: no cover - a mirror failure must never break the call
            pass


def _now() -> str:
    from src.tools.knowledge_base import _utc_now

    return _utc_now()
