"""Coordination signals as typed tool calls, not text keys (B180, Jessica 2026-10-12).

The framework used to read the integrator's recommendation, the agents' responses to it and the graph's
COMPLEXITY request out of free text, and every spelling, markdown heading or prose answer the parser did
not expect lost the signal (B172, B174, B177, B178). Tool-call arguments are JSON the model emits against
a schema, so these signals are now recorded by calling a tool:

  record_recommendation      the mdo_integrator: recommendation_to (one of the roles), recommended_change,
                             verdict (COMPLETE / CONTINUE / RETRY)
  respond_to_recommendation  any agent: how_addressed
  request_complexity         any agent, graph-routed only: level (simple / moderate / complex), reason

Each call is validated, attributed to the calling agent and stored, in order, in the design state
(`data_store["coordination_signals"]`); consumers read those records first. The DESIGN_STATE text the
prompts ask for is still written for the next agent to read -- it is just no longer what the framework
parses. Text parsing stays only as the fallback when an agent never called the tool, and every consumer
reports which source it used.
"""
from __future__ import annotations

import json
import time
from typing import Any

from smolagents import Tool

ROLES = ("geometry_engineer", "aerodynamics_analyst", "structures_analyst", "propulsion_analyst",
         "mission_architect", "simulation_executor")
VERDICTS = ("COMPLETE", "CONTINUE", "RETRY")
LEVELS = ("simple", "moderate", "complex")
KEY = "coordination_signals"


def _state():
    from src.tools.data_plane import get_design_state

    return get_design_state()


def _signals() -> list[dict]:
    state = _state()
    if state is None:
        return []
    return state.data_store.setdefault(KEY, [])


def _record(kind: str, **fields) -> dict:
    from src.tools.agent_context import current_agent_name

    signals = _signals()
    entry = {"kind": kind, "seq": len(signals) + 1, "agent": current_agent_name(), "time": round(time.time(), 1),
             **fields}
    signals.append(entry)
    try:
        from src.tools.design_json import write_design

        write_design(_state())
    except Exception:  # pragma: no cover - a report must not break the call
        pass
    return entry


def _role(value: str | None) -> str | None:
    text = str(value or "").strip().lower().replace(" ", "_")
    return next((r for r in ROLES if r == text or r.split("_")[0] in text), None)


# Networked peers are named agent_N and hold a role by claiming its TODO on the board (work_claims).
_TODO_ROLE = {"geometry": "geometry_engineer", "aero": "aerodynamics_analyst", "mass": "structures_analyst",
              "propulsion": "propulsion_analyst", "mission": "mission_architect",
              "simulation": "simulation_executor", "evaluation": "mdo_integrator",
              # graph-routed board (networked.md, graph-routed section)
              "GEOMETRY_SETUP": "geometry_engineer", "AERO_ANALYSIS": "aerodynamics_analyst",
              "MASS_ESTIMATION": "structures_analyst", "PROPULSION_SIZING": "propulsion_analyst",
              "MISSION_CONFIG": "mission_architect", "SIMULATION_RUN": "simulation_executor",
              "RESULTS_REVIEW": "mdo_integrator"}


def roles_of(agent_name: str | None) -> set[str]:
    """The roles an agent holds: the one its name says (sequential / orchestrated), or, for a networked peer,
    the roles of the TODOs it holds on the board."""
    name = str(agent_name or "")
    roles = set()
    if "integrator" in name.lower():
        roles.add("mdo_integrator")
    role = _role(name)
    if role:
        roles.add(role)
    try:
        from src.tools import work_claims

        roles |= {_TODO_ROLE[t] for t in work_claims._held_by(name) if t in _TODO_ROLE}
    except Exception:  # pragma: no cover - no board (sequential / orchestrated)
        pass
    return roles


def _held(agent: str) -> list[str]:
    try:
        from src.tools import work_claims

        return list(work_claims._held_by(agent))
    except Exception:  # pragma: no cover
        return []


def _review_todo() -> str | None:
    """The integrator's TODO on the networked board (RESULTS_REVIEW or evaluation), if there is a board."""
    try:
        from src.tools import work_claims

        names = {t.name for t in work_claims._todos()}
    except Exception:  # pragma: no cover
        return None
    return next((n for n in ("RESULTS_REVIEW", "evaluation") if n in names), None)


# -- readers -----------------------------------------------------------------------------------------

def signals(kind: str | None = None) -> list[dict]:
    return [s for s in _signals() if kind is None or s.get("kind") == kind]


def latest_recommendation() -> dict | None:
    recs = signals("recommendation")
    return recs[-1] if recs else None


def responses_since(seq: int = 0) -> list[dict]:
    """Responses recorded after the recommendation with this seq (0 = all of this link)."""
    return [s for s in signals("response") if s["seq"] > seq]


def complexity_requests_since(seq: int = 0) -> list[dict]:
    return [s for s in signals("complexity") if s["seq"] > seq]


def activate(recommendation_to: str | None, recommended_change: str = "") -> None:
    """The framework shows a recommendation to the agents: remember it, so the addressed role is asked to answer."""
    state = _state()
    role = _role(recommendation_to)
    if state is None or not role:
        return
    current = state.data_store.get("active_recommendation") or {}
    if current.get("recommendation_to") == role and current.get("recommended_change") == recommended_change:
        return
    state.data_store["active_recommendation"] = {"recommendation_to": role, "recommended_change": recommended_change,
                                                 "activated_at": last_seq()}


def active_recommendation() -> dict | None:
    state = _state()
    return (state.data_store.get("active_recommendation") if state is not None else None) or None


def last_seq() -> int:
    s = _signals()
    return s[-1]["seq"] if s else 0


# -- tools -------------------------------------------------------------------------------------------

_FINISH = "Recorded once is enough. Finish your work, then give your final answer with your DESIGN_STATE (final_answer)."


def _err(message: str) -> str:
    return json.dumps({"error": message, "recorded": False})


class RecordRecommendation(Tool):
    name = "record_recommendation"
    description = (
        "The mdo_integrator records its verdict and its ONE recommendation for the next step. This is how the "
        "framework receives the recommendation: write it here as well as in your DESIGN_STATE. "
        "recommendation_to: the role that should act -- one of geometry_engineer, aerodynamics_analyst, "
        "structures_analyst, propulsion_analyst, mission_architect, simulation_executor. "
        "recommended_change: one design variable and the direction (e.g. 'Wing1 aspect_ratio 9.4 -> 10.5'). "
        "verdict: COMPLETE, CONTINUE or RETRY. With COMPLETE the other two may be empty."
    )
    inputs: dict[str, Any] = {
        "verdict": {"type": "string", "description": "COMPLETE | CONTINUE | RETRY"},
        "recommendation_to": {"type": "string", "description": "the role that should act", "nullable": True},
        "recommended_change": {"type": "string", "description": "one design variable and its direction",
                               "nullable": True},
    }
    output_type = "string"

    def forward(self, verdict: str, recommendation_to: str | None = None,
                recommended_change: str | None = None) -> str:  # noqa: D102
        from src.tools.agent_context import current_agent_name

        agent = current_agent_name()
        if agent != "unknown" and "mdo_integrator" not in roles_of(agent):
            review = _review_todo()
            if review:
                # Networked (live 2026-10-15): a peer that flew the mission kept writing the verdict here while
                # still holding its own TODO, so the review TODO stayed blocked and nobody could record one.
                held = ", ".join(_held(agent)) or "none"
                return _err(f"Only the peer holding the integrator's TODO {review} records the recommendation; "
                            f"you are {agent} (holding: {held}). If you have finished your TODO, mark_todo_done it "
                            f"with your DESIGN_STATE, then claim {review} when it is available.")
            return _err(f"Only the mdo_integrator records the recommendation; you are {agent}. To answer one, "
                        "call respond_to_recommendation.")
        v = str(verdict or "").strip().upper()
        if v not in VERDICTS:
            return _err(f"verdict must be one of {', '.join(VERDICTS)}; got {verdict!r}.")
        role = _role(recommendation_to)
        change = str(recommended_change or "").strip()
        if v != "COMPLETE":
            if role is None:
                return _err(f"recommendation_to must be one of {', '.join(ROLES)}; got {recommendation_to!r}.")
            if not change:
                return _err("recommended_change is empty: name one design variable and the direction.")
        # Live sandbox 2026-10-14 (Qwen3-32B): with only {"recorded": true} back, the integrator re-called this
        # 16 times to its step cap and never gave its final answer. The result now says what comes next, and
        # a repeat of the same record is refused with that instruction.
        last = latest_recommendation()
        if last and last.get("agent") == current_agent_name() and (last.get("verdict"), last.get("recommendation_to"),
                                                                    last.get("recommended_change")) == (v, role, change):
            return _err(f"Already recorded (seq {last['seq']}); do not call record_recommendation again. "
                        f"{_FINISH}")
        entry = _record("recommendation", verdict=v, recommendation_to=role, recommended_change=change)
        return json.dumps({"recorded": True, "seq": entry["seq"], "verdict": v, "recommendation_to": role,
                           "recommended_change": change, "next": _FINISH})


class RespondToRecommendation(Tool):
    name = "respond_to_recommendation"
    description = (
        "Record how you addressed the current recommendation -- fully, in part, or by finding it belongs to "
        "another role (say which). Any agent may respond; the responses are passed to the agents after you "
        "and to the mdo_integrator. Write it here as well as RECOMMENDATION_IMPLEMENTED in your DESIGN_STATE."
    )
    inputs: dict[str, Any] = {
        "how_addressed": {"type": "string", "description": "what you changed, or why it belongs to another role"},
    }
    output_type = "string"

    def forward(self, how_addressed: str) -> str:  # noqa: D102
        text = str(how_addressed or "").strip()
        if not text:
            return _err("how_addressed is empty.")
        from src.tools.agent_context import current_agent_name

        mine = [r for r in signals("response") if r.get("agent") == current_agent_name()]
        if mine and mine[-1].get("how_addressed") == text:
            return _err(f"Already recorded (seq {mine[-1]['seq']}); do not call respond_to_recommendation again. "
                        f"{_FINISH}")
        entry = _record("response", how_addressed=text)
        return json.dumps({"recorded": True, "seq": entry["seq"], "next": _FINISH})


class RequestComplexity(Tool):
    name = "request_complexity"
    description = (
        "Graph-routed only: ask for the task to be reclassified when more disciplines need to re-run. "
        "level: simple (mission only), moderate (aero, mass, propulsion re-run on the same geometry) or "
        "complex (the geometry changes, so everything after it re-runs). reason: why."
    )
    inputs: dict[str, Any] = {
        "level": {"type": "string", "description": "simple | moderate | complex"},
        "reason": {"type": "string", "description": "why the task needs this level"},
    }
    output_type = "string"

    def forward(self, level: str, reason: str) -> str:  # noqa: D102
        lv = str(level or "").strip().lower()
        if lv not in LEVELS:
            return _err(f"level must be one of {', '.join(LEVELS)}; got {level!r}.")
        entry = _record("complexity", level=lv, reason=str(reason or "").strip())
        return json.dumps({"recorded": True, "seq": entry["seq"], "level": lv, "next": _FINISH})


class SetComplexity(Tool):
    name = "set_complexity"
    description = (
        "Orchestrated graph-routed only, for the orchestrator: set the workflow graph's complexity level. "
        "simple: the mission is set on the prepared discipline results; moderate: aero, mass and propulsion "
        "re-run on the same geometry; complex: the geometry changes, so every discipline after it re-runs. "
        "reason: why. Without this call the level stays where it is."
    )
    inputs: dict[str, Any] = {
        "level": {"type": "string", "description": "simple | moderate | complex"},
        "reason": {"type": "string", "description": "why the task needs this level"},
    }
    output_type = "string"

    def forward(self, level: str, reason: str) -> str:  # noqa: D102
        lv = str(level or "").strip().lower()
        if lv not in LEVELS:
            return _err(f"level must be one of {', '.join(LEVELS)}; got {level!r}.")
        from src.tools.agent_context import current_agent_name

        mine = [r for r in signals("complexity_set") if r.get("agent") == current_agent_name()]
        if mine and mine[-1].get("level") == lv and mine[-1].get("seq") == last_seq():
            return _err(f"Already set to {lv}; do not call set_complexity again. Call final_answer to end this turn.")
        entry = _record("complexity_set", level=lv, reason=str(reason or "").strip())
        # Live sandbox 2026-10-14: without the next step the orchestrator re-called this until its turn's cap.
        return json.dumps({"recorded": True, "seq": entry["seq"], "level": lv,
                           "next": "the level is set; call final_answer to end this turn"})


def levels_set_since(seq: int = 0) -> list[dict]:
    """The orchestrator's set_complexity calls after this seq."""
    return [s for s in signals("complexity_set") if s["seq"] > seq]
