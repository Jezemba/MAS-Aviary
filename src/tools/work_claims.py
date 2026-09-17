"""A claim reserves the work, and a peer looks at the board before starting (B86).

WHAT VALIDATE9 SHOWED (7960, 2026-09-16, first networked link, first 25 minutes)

    claim_todo / read_todos / write_blackboard calls : 0
    read_procedure calls                            : 0
    get_design_space                                : 3   (one per peer, identical call)
    set_aircraft_parameters                         : 3   (same round, same session)
    Step 1 durations                                : 166 s, 278 s, 382 s

All three peers spent their whole first step on the same read-only question, then all three
wrote conflicting wing areas (136.3 / 130.0 / unset) into one aviary session in one round
with nothing simulated between, so two of them held a design that no longer existed. Later,
two peers created their own SU2 session for the same design three minutes apart, and a third
tried to re-mesh it -- stopped only by a typo in the component UID, not by any guard.

TWO SEPARATE DEFECTS

1. Peers never claim. The tools are there -- ``_build_tool_list`` gives every peer ReadTodos,
   ClaimTodo, MarkTodoDone, MarkTodoFailed -- and the prompt and ``procedures.py`` both tell
   them to claim. They do not. As with B31 (mesh), B81 (duplicates) and B84 (coupled inputs):
   instruction does not change behaviour here, mechanism does.
2. A claim reserves nothing even when it is made. ``Blackboard.claim_todo`` is properly atomic
   -- at most one winner -- but NOTHING in the tool path ever consulted it. So a peer that
   never claimed "aero" could still run create_su2_session, generate_volume_mesh, set_mesh and
   run_su2_solver, and only the B81 guard would stop it, and only once the work was already
   done or in flight.

THE RULE (Jessica, 2026-09-16)

A tool belongs to the TODO whose work it performs (the mapping is the procedure table B85
added, so the refusal and the reference can never drift apart). Then:

  * claimed by another peer  -> refuse, in the B81 shape, naming who holds it, what else is
    free, and how to take it over if that peer has stalled;
  * claimed by the caller    -> proceed silently;
  * unclaimed                -> the FIRST domain call a peer makes is refused once with
    CLAIM_FIRST, so that every peer looks at the board before starting (this is the thing
    that stops three peers opening with the same call); after that one refusal, touching an
    unclaimed TODO AUTO-CLAIMS it, so doing the work is the claim and a peer can never be
    blocked by its own diligence.

There is no cap on how many TODOs a peer holds: with auto-claim-on-touch a claim means "I am
doing this now", not "I might later", so hoarding is not a failure mode -- and a peer that
flows from geometry into meshing into solving is never interrupted.

BOUNDED, ALWAYS (B85's lesson)

Never issued when the board is empty or when everything is claimed or done; the CLAIM_FIRST
refusal happens at most once per agent per run; every message names something the peer CAN
do. Networked only -- ``set_structure`` is called by the networked strategy and nothing else,
so sequential and orchestrated never see a claim refusal.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

ERROR_CLAIM_FIRST = "CLAIM_FIRST"
ERROR_CLAIMED_BY_OTHER = "CLAIMED_BY_ANOTHER_AGENT"

_lock = threading.RLock()
_blackboard: Any = None
_structure: str = ""
_first_call_refused: set[str] = set()
_stats: dict[str, Any] = {"claims_made": {}, "first_call_refusals": {}, "claim_violations": {}}

# Which TODO a tool belongs to. The tools come from the procedure table (B85) so there is one
# source of truth; the board's names differ slightly from the procedure roles, hence the alias.
_ROLE_TO_TODO = {"geometry": "geometry", "aero": "aero", "structures": "mass",
                 "propulsion": "propulsion", "mission": "mission"}
# Board TODOs with no procedure of their own: they are steps of the mission role.
_EXTRA_TOOL_TODOS = {"run_simulation": "simulation", "get_results": "simulation",
                     "check_constraints": "evaluation"}


def register_blackboard(blackboard: Any) -> None:
    global _blackboard
    with _lock:
        _blackboard = blackboard


def set_structure(name: str) -> None:
    """Only the networked strategy calls this; everything else is exempt by construction."""
    global _structure
    with _lock:
        _structure = str(name or "")


def reset() -> None:
    global _blackboard, _structure
    with _lock:
        _blackboard, _structure = None, ""
        _first_call_refused.clear()
        _stats.update({"claims_made": {}, "first_call_refusals": {}, "claim_violations": {},
                       "board_waits": {}})


def _tool_todos() -> dict[str, str]:
    """tool name -> TODO name, derived from the procedure table."""
    from src.tools.procedures import PROCEDURES

    mapping = dict(_EXTRA_TOOL_TODOS)
    for role, procedure in PROCEDURES.items():
        todo = _ROLE_TO_TODO.get(role)
        if not todo:
            continue
        for step in procedure.steps:
            mapping.setdefault(step.tool, todo)
    return mapping


def todo_for_tool(tool_name: str) -> str | None:
    return _tool_todos().get(tool_name)


# -- the board, read defensively ---------------------------------------------------------------

def _todos() -> list:
    board = _blackboard
    if board is None:
        return []
    try:
        return list(board.read_todos())
    except Exception:      # pragma: no cover - a board problem must never block work
        return []


def _owner_of(name: str) -> tuple[str | None, str]:
    """(owner, status) for a TODO, or (None, "") when the board does not have it."""
    for todo in _todos():
        if todo.name == name:
            return (todo.assigned_to, str(todo.status))
    return (None, "")


def _unclaimed_names() -> list[str]:
    """What a peer can take right now.

    A FAILED TODO counts: fail_todo frees the claim precisely so someone else picks it up,
    and it is the one a stalled peer most needs pointing at.
    """
    from src.coordination.blackboard import TODO_STATUS_FAILED, TODO_STATUS_PENDING

    return [t.name for t in _todos() if str(t.status) in (TODO_STATUS_PENDING, TODO_STATUS_FAILED)]


def _held_by(agent: str) -> list[str]:
    return [t.name for t in _todos() if t.assigned_to == agent]


def _when(name: str) -> str:
    for todo in _todos():
        if todo.name == name and getattr(todo, "claimed_at", None):
            return time.strftime("%H:%M", time.localtime(todo.claimed_at))
    return "earlier"


# -- the check -----------------------------------------------------------------------------------

def check(tool_name: str, agent: str | None) -> dict | None:
    """Refusal payload when this call trespasses on another peer's work, else None.

    Also performs the auto-claim, so calling this IS taking the TODO when it is free.
    """
    import os

    if os.environ.get("AVION_ENFORCE_CLAIMS", "1") != "1":
        return None
    with _lock:
        if _structure != "networked" or _blackboard is None:
            return None            # sequential and orchestrated never see a claim refusal
    todo_name = todo_for_tool(tool_name)
    if todo_name is None:
        return None                # discovery tools, tigl readbacks, anything not owned work
    agent = agent or "unknown"

    from src.coordination.blackboard import TODO_STATUS_CLAIMED

    owner, status = _owner_of(todo_name)
    if owner == agent:
        return None                # the caller holds it: proceed silently
    if owner is not None and status == TODO_STATUS_CLAIMED:
        return _refuse_claimed(tool_name, todo_name, owner, agent)
    # Only a live CLAIM reserves the work. Done means it is finished -- repeating it is
    # B81's business, not ours -- and failed means the holder gave it up for someone else.

    # Unclaimed. Make every peer look at the board ONCE before it starts working, which is
    # the thing that stops three peers opening with the same call (validate9).
    with _lock:
        already_refused = agent in _first_call_refused
    if not already_refused and not _held_by(agent) and _unclaimed_names():
        with _lock:
            _first_call_refused.add(agent)
            _stats["first_call_refusals"][agent] = _stats["first_call_refusals"].get(agent, 0) + 1
        return _refuse_claim_first(tool_name, todo_name, agent)

    _auto_claim(todo_name, agent)
    return None


def _auto_claim(todo_name: str, agent: str) -> None:
    """Doing the work is the claim, so a peer is never blocked by its own diligence."""
    board = _blackboard
    if board is None:
        return
    try:
        ok, _ = board.claim_todo(todo_name, agent)
    except Exception:      # pragma: no cover - a board problem must never block work
        return
    if ok:
        with _lock:
            _stats["claims_made"][todo_name] = agent
        logger.info("[B86] %s auto-claimed %r by starting its work", agent, todo_name)


def _refuse_claimed(tool_name: str, todo_name: str, owner: str, agent: str) -> dict:
    with _lock:
        by_agent = _stats["claim_violations"].setdefault(agent, {})
        by_agent[tool_name] = by_agent.get(tool_name, 0) + 1
        # This peer has now been shown the board, so it must not also collect a CLAIM_FIRST
        # on its next call: one refusal is the budget, whichever kind arrives first.
        _first_call_refused.add(agent)
    free = _unclaimed_names()
    alternatives = (
        "Unclaimed right now: " + ", ".join(free) + ". Call claim_todo(<name>) and take one of those"
        if free else
        "Everything else is claimed, so do not go looking for more: call "
        "wait_for_board(reason='everything is claimed') and it will hand you the next TODO that "
        "frees up, or mark your own TODO done if you have finished it"
    )
    # B93: tell this peer what IT should be doing, not only what it may not do.
    own_next = ""
    for held in held_unfinished(agent):
        from src.tools.procedures import next_step_line, role_for_todo

        line = next_step_line(role_for_todo(held))
        if line:
            own_next = f" You hold {held}: {line}"
            break
    return {
        "success": False,
        "error_code": ERROR_CLAIMED_BY_OTHER,
        "error": (
            f"{todo_name} is claimed by {owner} (since {_when(todo_name)}), and {tool_name} is part of "
            f"that work -- doing it too would duplicate {owner}'s run. "
            # B89 (Jessica, 2026-09-16): if you need this done, ASK for it rather than taking
            # it over. agent_2 held 'mass' and needed export_cpacs, which belongs to geometry:
            # the right move is to tell the holder, not to run another peer's step.
            f"If you NEED it for your own work, ask for it: write_blackboard(key='request_{tool_name}', "
            f"value='<what you need and why>', entry_type='gap') -- {owner} reads the board and can "
            f"run it. It stays reserved until {owner} releases it with "
            f"mark_todo_done('{todo_name}', result='<short summary>'), or mark_todo_failed"
            f"('{todo_name}') if {owner} has stalled -- which you may call yourself to free it. "
            f"{alternatives}. read_procedure(role='<name>') lists that TODO's tools in order, and "
            "structures and propulsion need no aero at all."
        ),
        "todo": todo_name,
        "claimed_by": owner,
        "unclaimed": free,
    }


def _refuse_claim_first(tool_name: str, todo_name: str, agent: str) -> dict:
    free = _unclaimed_names()
    return {
        "success": False,
        "error_code": ERROR_CLAIM_FIRST,
        "error": (
            f"Claim your work before you start it. You called {tool_name}, which is part of the "
            f"'{todo_name}' TODO, without claiming anything -- and your peers are choosing right now "
            "too, so without claims you will all do the same thing.\n"
            f"Unclaimed: {', '.join(free)}.\n"
            f"Call claim_todo('{todo_name}') if that is the work you want, or claim_todo(<other>) "
            "for one of the others, then carry on. read_todos shows who holds what; "
            "read_procedure(role='<name>') lists that TODO's tools in order.\n"
            "This is said once: after you claim, starting an unclaimed TODO's work claims it for you "
            "automatically."
        ),
        "todo": todo_name,
        "unclaimed": free,
    }


# -- metrics -------------------------------------------------------------------------------------

def stats() -> dict:
    with _lock:
        violations = {a: dict(t) for a, t in _stats["claim_violations"].items()}
        return {
            "claims_made": dict(_stats["claims_made"]),
            "claims_made_count": len(_stats["claims_made"]),
            "first_call_refusals": dict(_stats["first_call_refusals"]),
            "first_call_refusals_total": sum(_stats["first_call_refusals"].values()),
            "claim_violations": violations,
            "claim_violations_total": sum(sum(t.values()) for t in violations.values()),
            "board_waits": {a: dict(v) for a, v in (_stats.get("board_waits") or {}).items()},
            "board_wait_seconds_total": round(
                sum(v["seconds"] for v in (_stats.get("board_waits") or {}).values()), 1),
        }

# -- B89: the board a peer acts on must be the board as it is NOW -------------------------------

def unclaimed_now() -> list[str]:
    """What is takeable at this instant. Public because the claim tool needs it too (B89)."""
    return _unclaimed_names()


def board_snapshot() -> list[dict]:
    """The live board, small enough to ride on every claim result (B89)."""
    return [{"name": t.name, "status": str(t.status), "owner": t.assigned_to} for t in _todos()]


def held_unfinished(agent: str) -> list[str]:
    """TODOs this peer holds that are neither done nor failed."""
    from src.coordination.blackboard import TODO_STATUS_CLAIMED

    return [t.name for t in _todos()
            if t.assigned_to == agent and str(t.status) == TODO_STATUS_CLAIMED]


def suggest_split(peer_names: list[str]) -> dict:
    """One different free TODO per peer (B89).

    ``_with_board_and_uids`` used to suggest ``free[0]`` to everybody, so all three peers read
    the identical line "claim_todo('geometry')" and two of them lost the race. The peers share
    one prompt string, so the split is written out by NAME and each peer reads its own.
    """
    free = _unclaimed_names()
    if not free or not peer_names:
        return {}
    return {peer: free[i % len(free)] for i, peer in enumerate(peer_names)}

# -- B90: waiting is a state, not idling ---------------------------------------------------------

def board_signature() -> tuple:
    """Cheap fingerprint of the board, so a waiter can tell when anything moved."""
    return tuple(sorted((t.name, str(t.status), t.assigned_to or "") for t in _todos()))


def claimable_now(agent: str | None = None) -> list[str]:
    """TODOs takeable RIGHT NOW: free AND their dependencies are done.

    Different from ``unclaimed_now``, which ignores dependencies -- a peer told to take
    'mission' while aero is still running would only lose another step.
    """
    board = _blackboard
    if board is None:
        return []
    try:
        available = [t.name for t in board.read_available_todos()]
    except Exception:      # pragma: no cover - a board problem must never block work
        return []
    if agent and held_unfinished(agent):
        return []          # it already has work; it should be doing that, not taking more
    return available


def blocked_now() -> list[dict]:
    """What is left but not yet takeable, and which unfinished TODO each is waiting on."""
    from src.coordination.blackboard import TODO_STATUS_DONE, TODO_STATUS_FAILED, TODO_STATUS_PENDING

    by_name = {t.name: t for t in _todos()}
    out = []
    for todo in by_name.values():
        if str(todo.status) not in (TODO_STATUS_PENDING, TODO_STATUS_FAILED):
            continue
        unmet = [d for d in (todo.depends_on or [])
                 if d not in by_name or str(by_name[d].status) != TODO_STATUS_DONE]
        if unmet:
            out.append({"name": todo.name, "waiting_on": unmet,
                        "owners": [by_name[d].assigned_to for d in unmet if d in by_name]})
    return out


def anyone_working(excluding: str | None = None) -> bool:
    """Is another peer holding unfinished work? If not, waiting can change nothing."""
    from src.coordination.blackboard import TODO_STATUS_CLAIMED

    return any(str(t.status) == TODO_STATUS_CLAIMED and t.assigned_to != excluding
               for t in _todos())


def note_wait(agent: str, seconds: float, woke_on_change: bool) -> None:
    with _lock:
        waits = _stats.setdefault("board_waits", {})
        entry = waits.setdefault(agent, {"waits": 0, "seconds": 0.0, "woken_by_change": 0})
        entry["waits"] += 1
        entry["seconds"] = round(entry["seconds"] + seconds, 1)
        entry["woken_by_change"] += 1 if woke_on_change else 0


# -- B93: blocked is not idle -------------------------------------------------------------------

def done_todo_names() -> set:
    """Board names that are finished."""
    from src.coordination.blackboard import TODO_STATUS_DONE

    return {t.name for t in _todos() if str(t.status) == TODO_STATUS_DONE}


def blocked_holdings(agent: str) -> dict:
    """{held TODO -> the roles it is waiting on}, for the holdings this peer CANNOT start (B93).

    validate11, 00:52-01:16: agent_3 held `aero`, had its SU2 session ready, and needed a mesh
    that belongs to `geometry` -- held by a peer that was not producing one. It could not
    wait_for_board (it holds work), could not claim `mass` (no forward reservation), and its only
    exit was to abandon a TODO it legitimately owned. Holding blocked work is a third state, and
    the peer in it must be allowed to do something useful.
    """
    from src.tools.procedures import blocking_roles, role_for_todo

    done = done_todo_names()
    out = {}
    for name in held_unfinished(agent):
        role = role_for_todo(name)
        if not role:
            continue
        waiting_on = blocking_roles(role, done)
        if waiting_on:
            out[name] = waiting_on
    return out


def all_holdings_blocked(agent: str) -> bool:
    """True when this peer holds work and every piece of it is waiting on someone else (B93)."""
    holding = held_unfinished(agent)
    return bool(holding) and len(blocked_holdings(agent)) == len(holding)
