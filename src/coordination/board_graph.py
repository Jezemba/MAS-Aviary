"""Graph-routed v2 for the networked org structure (Jessica, 2026-10-12, design D8).

Same levels, budgets and rule table as the sequential graph (src/coordination/graph_router.py); what
differs is who decides. In sequential the router picks the next role. Here the peers decide, on the board:

  - Every link starts at SIMPLE. The framework has prepared the sessions, CPACS, mesh and discipline
    results (graph_preinit.prepare), so the board holds only MISSION_CONFIG -> SIMULATION_RUN ->
    RESULTS_REVIEW.
  - Any peer reclassifies with request_complexity. The board then gains that level's TODOs (MODERATE: aero,
    mass, propulsion; COMPLEX: geometry too) and the TODOs downstream of them open again.
  - The integrator's recorded recommendation (record_recommendation, by the peer holding RESULTS_REVIEW)
    reopens the TODO that produces the variable it names, and everything after it, inside the link.
  - The rule table's reading of a simulation that did not fly is posted to the board as a suggestion; it
    does not move anything by itself.
  - The link stops when the integrator records COMPLETE, every TODO is done with nothing left to act on,
    or the level's budget of role runs (TODO completions) is spent.
"""
from __future__ import annotations

import logging

from src.coordination import graph_router as gr

logger = logging.getLogger(__name__)

# Her board names (networked.md, graph-routed) and the router node each stands for.
TODO_OF_NODE = {gr.GEOMETRY: "GEOMETRY_SETUP", gr.AERO: "AERO_ANALYSIS", gr.MASS: "MASS_ESTIMATION",
                gr.PROPULSION: "PROPULSION_SIZING", gr.MISSION: "MISSION_CONFIG", gr.SIMULATION: "SIMULATION_RUN",
                gr.REVIEW: "RESULTS_REVIEW"}
NODE_OF_TODO = {v: k for k, v in TODO_OF_NODE.items()}
# Her WORKFLOW ROUTE, restricted at each level to the TODOs on the board.
ROUTE = {gr.GEOMETRY: [], gr.AERO: [gr.GEOMETRY], gr.MASS: [gr.GEOMETRY], gr.PROPULSION: [gr.MASS],
         gr.MISSION: [gr.AERO, gr.MASS], gr.SIMULATION: [gr.MISSION], gr.REVIEW: [gr.SIMULATION, gr.PROPULSION]}
_DESCRIPTION = {gr.GEOMETRY: "geometry engineer", gr.AERO: "aerodynamics analyst", gr.MASS: "structures analyst",
                gr.PROPULSION: "propulsion agent", gr.MISSION: "mission architect",
                gr.SIMULATION: "simulation executor", gr.REVIEW: "mdo integrator"}
AUTHOR = "graph_router"


def nodes_at(level: str) -> list[str]:
    return [n for n in gr.NODES if gr.LEVELS.index(gr.LEVEL_OF_NODE[n]) <= gr.LEVELS.index(level)]


class BoardGraph:
    """The networked graph's level, budget and board edits. Called by the strategy between cycles."""

    def __init__(self, board, preinit: bool = True):
        self.board = board
        self.level = gr.SIMPLE
        self.preinit = preinit
        self.preinit_report: dict = {}
        self.started = False
        self.seq_seen = 0          # coordination signals already acted on
        self.kb_seen = 0           # KB entries already read for the rule-table suggestion
        self.base_completions = 0  # board completions before this link
        self.role_runs = 0
        self.stop_reason: str | None = None
        self.log: list[dict] = []

    # -- board edits -----------------------------------------------------------------------------

    def _deps(self, node: str, level: str) -> list[str]:
        on_board = set(nodes_at(level))
        return [TODO_OF_NODE[d] for d in ROUTE[node] if d in on_board]

    def _post(self, key: str, text: str, entry_type: str = "status") -> None:
        try:
            self.board.write(key=key, value=text, author=AUTHOR, entry_type=entry_type)
        except Exception:  # pragma: no cover - a post must never stop the link
            logger.debug("[board graph] post %s failed", key, exc_info=True)

    def _level_text(self, reason: str) -> str:
        on = ", ".join(TODO_OF_NODE[n] for n in nodes_at(self.level))
        return (f"GRAPH ROUTING (framework): complexity level {self.level.upper()} -- {reason}. TODOs on the "
                f"board at this level: {on}. Any peer may call request_complexity (moderate or complex, with "
                "the reason) when more disciplines need to re-run; the board then gains those TODOs.")

    def _set_level(self, level: str, reason: str) -> None:
        old = self.level
        self.level = level
        self._limit()
        new_nodes = [n for n in nodes_at(level) if n not in nodes_at(old)]
        self.board.seed_todos([(TODO_OF_NODE[n], _DESCRIPTION[n], self._deps(n, level)) for n in new_nodes])
        for n in nodes_at(level):
            self.board.set_todo_depends(TODO_OF_NODE[n], self._deps(n, level))
        entry = gr.ENTRY_OF_LEVEL[level]
        reopened = self.board.reopen_todos([TODO_OF_NODE[n] for n in gr.downstream_of(entry)])
        self.log.append({"event": "level", "from": old, "to": level, "reason": reason, "reopened": reopened})
        self._post("graph_level", self._level_text(reason))

    def _reopen_from(self, node: str, reason: str) -> None:
        names = [TODO_OF_NODE[n] for n in gr.downstream_of(node) if n in nodes_at(self.level)]
        reopened = self.board.reopen_todos(names)
        self.log.append({"event": "reopen", "from": node, "reason": reason, "reopened": reopened})
        self._post("graph_route", f"GRAPH ROUTING (framework): {reason}. Open again: {', '.join(names)}.")

    # -- per cycle -------------------------------------------------------------------------------

    def start(self, carry_over: dict | None) -> None:
        from src.tools import recommendation_tools as rt

        self.started = True
        if self.preinit:
            from src.coordination import graph_preinit

            self.preinit_report = graph_preinit.prepare(carry_over)
        self.seq_seen = rt.last_seq()
        self.base_completions = self.board.completions
        self._limit()
        level = self.level
        self.board.seed_todos([(TODO_OF_NODE[n], _DESCRIPTION[n], self._deps(n, level)) for n in nodes_at(level)])
        self._post("graph_level", self._level_text(
            "this iteration starts at SIMPLE: the framework has prepared the sessions, CPACS, mesh and discipline "
            "results, so the mission is set on them"))

    def _count_runs(self) -> None:
        self.role_runs = self.board.completions - self.base_completions

    def _limit(self) -> None:
        """The level's budget, enforced by the board itself (it refuses claims once it is spent)."""
        self.board.set_claim_limit(self.base_completions + gr.BUDGET[self.level],
                                   f"the budget of {gr.BUDGET[self.level]} role runs at {self.level} is spent")

    def _suggest(self) -> None:
        """The rule table's reading of a simulation that did not fly, posted as advice."""
        from src.tools import coupling
        from src.tools.data_plane import get_design_state
        from src.tools.knowledge_base import get_kb

        kb = get_kb()
        if kb is None:
            return
        records = list(kb.entries[self.kb_seen:])
        self.kb_seen = len(kb.entries)
        if not any(r.get("tool") == "run_simulation" for r in records):
            return
        state = get_design_state()
        infeasible = ((state.data_store or {}).get("_simulation_flown_infeasible") or {}) if state else {}
        d = gr.route_after_simulation(records, coupling.registry(state) if state else {},
                                      infeasible.get("failure"), self.level)
        if d.next_node == gr.REVIEW:
            return
        self.log.append({"event": "suggestion", "node": d.next_node, "level": d.level, "rule": d.rule,
                         "reason": d.reason})
        self._post("graph_suggestion",
                   f"GRAPH ROUTING SUGGESTION (framework rule {d.rule}, advice only): {d.reason}. The rule table "
                   f"would re-run {TODO_OF_NODE[d.next_node]} at level {d.level.upper()}. To act on it, call "
                   "request_complexity with that level; it is your call.", entry_type="gap")

    def record(self) -> None:
        """The route so far, in the design state (design.json / the run's audit)."""
        try:
            from src.tools.data_plane import get_design_state

            state = get_design_state()
            if state is not None:
                self._count_runs()
                state.data_store["graph_route"] = {"level": self.level, "role_runs": self.role_runs,
                                                   "stop_reason": self.stop_reason, "log": list(self.log),
                                                   "preinit": self.preinit_report}
                from src.tools.design_json import write_design

                write_design(state)
        except Exception:  # pragma: no cover
            pass

    def step(self) -> bool:
        """Apply what the peers recorded since the last cycle. False when the link should stop."""
        go = self._step()
        if not go:
            logger.info("[board graph] link ended: %s after %d role runs at %s", self.stop_reason, self.role_runs,
                        self.level)
            self._post("graph_stop", f"GRAPH ROUTING (framework): the link ends -- {self.stop_reason}.")
        self.record()
        return go

    def _step(self) -> bool:
        from src.tools import recommendation_tools as rt

        self._count_runs()
        self._suggest()
        new = [s for s in rt.signals() if s["seq"] > self.seq_seen]
        if new:
            self.seq_seen = new[-1]["seq"]
        asked = [s["level"] for s in new if s.get("kind") == "complexity"]
        if asked:
            top = max(asked, key=gr.LEVELS.index)
            if gr.LEVELS.index(top) > gr.LEVELS.index(self.level):
                who = next(s for s in new if s.get("kind") == "complexity" and s["level"] == top)
                self._set_level(top, f"{who.get('agent')} reclassified the task: {who.get('reason') or 'no reason'}")
        recorded = next((s for s in reversed(new) if s.get("kind") == "recommendation"), None)
        if recorded is not None:
            if recorded.get("verdict") == "COMPLETE":
                self.stop_reason = "the integrator recorded the verdict COMPLETE"
                return False
            rec = {"RECOMMENDATION_TO": recorded.get("recommendation_to"),
                   "RECOMMENDED_CHANGE": recorded.get("recommended_change")}
            target = gr.recommendation_target(rec)
            if target:
                need = gr.LEVEL_OF_NODE[target]
                if gr.LEVELS.index(need) > gr.LEVELS.index(self.level):
                    self._set_level(need, f"the integrator's recommendation needs {TODO_OF_NODE[target]}")
                self._reopen_from(target, f"the integrator recommends: {str(rec['RECOMMENDED_CHANGE'])[:200]} "
                                          f"-- produced by {TODO_OF_NODE[target]}")
        if self.role_runs >= gr.BUDGET[self.level]:
            self.stop_reason = f"budget of {gr.BUDGET[self.level]} role runs at {self.level} spent"
            return False
        if self.board.all_todos_done():
            self.stop_reason = "every TODO is done and nothing was left to act on"
            return False
        return True
