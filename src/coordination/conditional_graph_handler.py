"""Graph-routed v2 handler for the sequential org structure (Jessica, 2026-10-12).

The staged pipeline always runs the roles in the same order. Here the next role depends on how the current
one ended (src/coordination/graph_router.py), and complexity is the conditional: every link starts at
SIMPLE (mission, simulation, review on prepared results) and goes up when a rule fires or any role writes
`COMPLEXITY: <level>` in its DESIGN_STATE. Before the first role runs, the framework prepares the sessions,
CPACS, mesh and discipline results (src/coordination/graph_preinit.py) so the graph can enter anywhere.

Each role gets Jessica's verbatim input (prompt_assembly.verbatim_stage_input). The graph adds only its own
mechanism: why the role is running (rule and reason), the current level, the COMPLEXITY field, and on a
re-run what went wrong. The integrator's recommendation is acted on inside the link: REVIEW routes it to
the role that produces the variable it names, and the sweep runs forward again, within the level's budget.

Orchestrated (D8, 2026-10-14, handler_config decider "orchestrator"): the same rule table routes the work,
but the ORCHESTRATOR decides the level. It is asked at the start of the link (default SIMPLE) and whenever an
agent asks to reclassify or a rule would raise the level, and answers with set_complexity; without that call
the level stays. After REVIEW it is told the integrator's recommendation and assigns it (assign_task) to the
agent that should carry it out -- the graph then goes there. Each agent's message is the orchestrator's task
for it plus the DESIGN_STATE as data (prompt_assembly.worker_message), as in the other orchestrated combos.
"""
from __future__ import annotations

import logging
import time

from src.coordination import graph_router as gr
from src.coordination.execution_handler import Assignment, ExecutionHandler
from src.coordination.history import AgentMessage

logger = logging.getLogger(__name__)

_LEVEL_TEXT = {gr.SIMPLE: "SIMPLE (mission-level work on the prepared discipline results)",
               gr.MODERATE: "MODERATE (aero, mass and propulsion re-run on the same geometry)",
               gr.COMPLEX: "COMPLEX (the geometry changes, so every discipline after it re-runs)"}


class ConditionalGraphHandler(ExecutionHandler):
    def __init__(self, config: dict | None = None):
        cfg = dict(config or {})
        self._preinit = cfg.get("preinit", True)
        self._decider = cfg.get("decider", "router")          # "orchestrator": D8, the orchestrator sets the level
        self._as_given = cfg.get("worker_input") == "as_given"
        self.route_log: list[dict] = []
        self.preinit_report: dict = {}
        self.orchestrator_turns = 0
        self._config = cfg

    def _load_graph(self, state: dict):
        """The predefined graph's definition, for the runner's role discovery (list_graph_roles, aliases)."""
        from src.coordination.graph_routed_handler import GraphRoutedHandler

        return GraphRoutedHandler(self._config)._load_graph(state)

    def _routing_text(self, node: str, level: str, reason: str, rule: str, feedback: str | None) -> str:
        lines = [f"GRAPH ROUTING (framework): complexity level {_LEVEL_TEXT[level]}.",
                 f"You are running because: {reason} (rule {rule})."]
        if feedback:
            lines.append(f"Your previous attempt did not succeed: {feedback}. Try a different approach to fix this.")
        who = " The orchestrator decides." if self._decider == "orchestrator" else ""
        lines.append("If you judge that the task needs more disciplines re-run, call request_complexity with the "
                     f"level (moderate or complex) and the reason.{who}")
        return "\n".join(lines)

    # -- D8: the orchestrator decides the level ------------------------------------------------------

    def _ask(self, meta: dict, message: str) -> list[dict]:
        """One orchestrator message turn; returns the set_complexity calls it made (and keeps its assignments)."""
        from src.tools import recommendation_tools as rt

        ask = meta.get("ask_orchestrator")
        if not callable(ask):
            return []
        seq = rt.last_seq()
        try:
            ask("graph_router", message)
        except Exception as exc:  # pragma: no cover - a failed turn leaves the level where it is
            logger.warning("[graph] orchestrator turn failed: %s", exc)
        self.orchestrator_turns += 1
        return rt.levels_set_since(seq)

    def _orchestrator_start_level(self, meta: dict) -> tuple[str, str]:
        sets = self._ask(meta, (
            "GRAPH ROUTING (framework): this iteration's workflow graph starts at SIMPLE -- the framework has "
            "prepared the sessions, CPACS, mesh and discipline results, so the mission is set on them. You decide "
            "the complexity level: call set_complexity(level, reason) to start at moderate (aero, mass and "
            "propulsion re-run on the same geometry) or complex (the geometry changes). Then call final_answer to "
            "end this turn; without set_complexity the graph starts at simple."))
        if sets:
            return sets[-1]["level"], f"the orchestrator set the level: {sets[-1].get('reason') or 'no reason'}"
        return gr.SIMPLE, ("this iteration starts at SIMPLE: the framework has prepared the sessions, CPACS, mesh "
                           "and discipline results, so the mission is set on them")

    def _orchestrator_level(self, meta: dict, node: str, decision, level: str, requested: str | None,
                            asker: str | None):
        """A rule or an agent would raise the level: the orchestrator decides. Returns the decision to follow."""
        asked = f"{asker} asks to reclassify the task as {requested}. " if requested else ""
        sets = self._ask(meta, (
            f"GRAPH ROUTING (framework): {asked}After {node}, the rule table suggests {decision.next_node} at "
            f"level {decision.level} (rule {decision.rule}: {decision.reason}). The level is {level}. Call "
            "set_complexity(level, reason) to change it; without that call it stays at "
            f"{level}. Then call final_answer to end this turn."))
        chosen = max((s["level"] for s in sets), key=gr.LEVELS.index) if sets else level
        if gr.LEVELS.index(chosen) <= gr.LEVELS.index(level):
            # Declined: stay at this level. A step within it stands; one that needs a higher level is replaced
            # by this level's entry (at SIMPLE that is rule 9: set the mission up again).
            target = decision.next_node
            if gr.LEVELS.index(gr.LEVEL_OF_NODE[target]) > gr.LEVELS.index(level):
                target = gr.ENTRY_OF_LEVEL[level]
            return gr.Decision(target, level, "orchestrator-kept",
                               f"the orchestrator kept the level {level} ({decision.reason})")
        if gr.LEVELS.index(chosen) < gr.LEVELS.index(gr.LEVEL_OF_NODE[decision.next_node]):
            # Raised, but not as far as the rule's target needs: start the level the orchestrator chose.
            return gr.Decision(gr.ENTRY_OF_LEVEL[chosen], chosen, "orchestrator",
                               f"the orchestrator set the level {chosen} (the rule table suggested "
                               f"{decision.next_node} at {decision.level})")
        if gr.LEVELS.index(chosen) > gr.LEVELS.index(decision.level):
            # Raised beyond what the rule needs (as an agent's request does in sequential): go back to the
            # new level's entry when it is upstream of here, else carry on at the new level.
            entry = gr.ENTRY_OF_LEVEL[chosen]
            if gr.NODES.index(entry) <= gr.NODES.index(node):
                return gr.Decision(entry, chosen, "orchestrator", f"the orchestrator set the level {chosen}")
        return gr.Decision(decision.next_node, gr.higher(chosen, decision.level), decision.rule,
                           decision.reason + f"; the orchestrator set the level {chosen}")

    def _orchestrator_after_review(self, meta: dict, decision, level: str, recorded: dict | None,
                                   assigned_before: int):
        """REVIEW asked for more work: the orchestrator assigns the recommendation; the graph goes there."""
        rec = recorded or {}
        sets = self._ask(meta, (
            "GRAPH ROUTING (framework): the mdo_integrator recorded its verdict "
            f"{rec.get('verdict', '(none recorded)')} with the recommendation to {rec.get('recommendation_to')}: "
            f"{rec.get('recommended_change')}. The rule table routes it to {decision.next_node} at level "
            f"{decision.level}. Decide which agent carries it out and give it the recommendation with assign_task; "
            f"the graph runs that agent next. Change the level with set_complexity if needed (now {level}). Then "
            "call final_answer to end this turn."))
        assigned = (meta.get("assignments") or [])[assigned_before:]
        target = next((a.get("agent_name") for a in reversed(assigned) if a.get("agent_name") in gr.NODES),
                      decision.next_node)
        chosen = max([s["level"] for s in sets] + [level], key=gr.LEVELS.index)
        # Assigning a discipline that only runs at a higher level is choosing that level.
        chosen = gr.higher(chosen, gr.LEVEL_OF_NODE[target])
        how = "assigned by the orchestrator" if assigned else "the orchestrator assigned nothing; rule table"
        return gr.Decision(target, chosen, decision.rule, f"{decision.reason} -- {how}")

    def _input(self, node: str, agent, sweep: list, carry_now: dict | None, meta: dict) -> str:
        from src.coordination import prompt_assembly

        if not self._as_given:
            return prompt_assembly.verbatim_stage_input(getattr(agent, "description", "") or "", sweep, carry_now,
                                                        node, prompt_assembly.read_design_json_text())
        tasks = meta.get("assigned_tasks") or {}
        # The latest task the orchestrator gave this agent (an in-link assignment replaces the first one).
        for a in meta.get("assignments") or []:
            if a.get("agent_name") == node:
                tasks = dict(tasks, **{node: a.get("task", "")})
        previous = (sweep[-1][0], sweep[-1][1]) if sweep else None
        earlier = [(n, o) for n, o, _ in sweep[:-1]] if sweep else []
        inbox = (meta.get("inbox") or {}).pop(node, None)
        return prompt_assembly.worker_message(tasks.get(node, ""), meta.get("system_text", ""), previous, earlier,
                                              inbox)

    def execute(self, assignments: list[Assignment], agents: dict, logger_=None, turn_offset: int = 0,
                action_metadata: dict | None = None, **kwargs) -> list[AgentMessage]:
        from src.coordination import graph_preinit, prompt_assembly
        from src.tools import coupling
        from src.tools.data_plane import get_design_state
        from src.tools import recommendation_tools as rt
        from src.tools.knowledge_base import get_kb

        inst_logger = kwargs.get("logger", logger_)
        meta = dict(action_metadata or {})
        orchestrated = self._decider == "orchestrator"
        if orchestrated:
            orch = agents.get(meta.get("orchestrator_name") or "orchestrator")
            tools = getattr(orch, "tools", None)
            if isinstance(tools, dict) and rt.SetComplexity.name not in tools:
                tools[rt.SetComplexity.name] = rt.SetComplexity()
        # B180: graph-routed agents reclassify with a tool call; only this handler hands that tool out.
        for agent in agents.values():
            tools = getattr(agent, "tools", None)
            if isinstance(tools, dict) and rt.RequestComplexity.name not in tools:
                tools[rt.RequestComplexity.name] = rt.RequestComplexity()
        carry = prompt_assembly.read_carry_over() or {}
        if self._preinit and not self.preinit_report:
            # Once per link: the orchestrated strategy may run the graph again after a failed simulation,
            # on the sessions and results already prepared.
            self.preinit_report = graph_preinit.prepare(carry)
        level, reason, rule = gr.SIMPLE, ("this iteration starts at SIMPLE: the framework has prepared the "
                                          "sessions, CPACS, mesh and discipline results, so the mission is set "
                                          "on them"), "start"
        if orchestrated:
            level, reason = self._orchestrator_start_level(meta)
        node = gr.ENTRY_OF_LEVEL[level]
        carry_now = dict(carry) if carry else None
        sweep: list[tuple[str, str, None]] = []
        messages: list[AgentMessage] = []
        executions, retries, feedback, turn = 0, 0, None, turn_offset
        stop_reason = "complete"

        while node != gr.COMPLETE:
            if executions >= gr.BUDGET[level]:
                stop_reason = f"budget of {gr.BUDGET[level]} role runs at {level} spent"
                break
            agent = agents.get(node)
            if agent is None:
                stop_reason = f"no agent for {node}"
                break
            kb = get_kb()
            start = len(kb.entries) if kb else 0
            seq0 = rt.last_seq()
            # B180 gates: each visit owes its own record (the integrator runs once per pass in a link).
            for attr, value in (("_avion_recommendation_since", seq0), ("_avion_recommendation_reminders", 0),
                                ("_avion_response_reminders", 0)):
                try:
                    setattr(agent, attr, value)
                except Exception:  # pragma: no cover
                    pass
            base = self._input(node, agent, sweep, carry_now, meta)
            context = base + "\n\n" + self._routing_text(node, level, reason, rule, feedback)
            turn += 1
            t0, error, content = time.monotonic(), None, ""
            try:
                result = agent.run(context)
                content = str(result) if result is not None else ""
            except Exception as exc:
                error = str(exc)
            records = list(kb.entries[start:]) if kb else []
            state = get_design_state()
            asks = rt.complexity_requests_since(seq0)
            asked = [s["level"] for s in asks]
            requested = max(asked, key=gr.LEVELS.index) if asked else None
            recorded = next((s for s in reversed(rt.signals("recommendation")) if s["seq"] > seq0), None)
            if node == gr.REVIEW:
                decision = gr.route_after_review(content, level, recorded, None if orchestrated else requested)
                if orchestrated and decision.next_node != gr.COMPLETE:
                    decision = self._orchestrator_after_review(meta, decision, level, recorded,
                                                               len(meta.get("assignments") or []))
            else:
                infeasible = ((state.data_store or {}).get("_simulation_flown_infeasible") or {}) if state else {}
                decision = gr.route_after_node(node, records, content if not error else f"error: {error}", level,
                                               retries, coupling.registry(state) if state else {},
                                               infeasible.get("failure"), None if orchestrated else requested)
                if orchestrated and (gr.LEVELS.index(gr.higher(decision.level, requested or gr.SIMPLE))
                                     > gr.LEVELS.index(level)):
                    decision = self._orchestrator_level(meta, node, decision, level, requested,
                                                        asks[-1].get("agent") if asks else None)
            route = {"turn": turn, "node": node, "level": level, "next": decision.next_node,
                     "next_level": decision.level, "rule": decision.rule, "reason": decision.reason}
            self.route_log.append(route)
            msg = AgentMessage(agent_name=node, content=content, turn_number=turn, timestamp=time.time(),
                               duration_seconds=time.monotonic() - t0, error=error,
                               is_retry=decision.next_node == node or retries > 0,
                               metadata={**(action_metadata or {}), "graph_route": route,
                                         "completion_met": decision.rule in ("flow", "complete")})
            messages.append(msg)
            if inst_logger is not None:
                inst_logger.log_turn(msg)
            logger.info("[graph] %s (%s) -> %s (%s), rule %s: %s", node, level, decision.next_node,
                        decision.level, decision.rule, decision.reason)

            if decision.next_node == node:
                retries, feedback = retries + 1, decision.reason
            else:
                retries, feedback = 0, None
                if node == gr.REVIEW and decision.next_node != gr.COMPLETE:
                    # The recommendation is acted on inside the link: the next sweep sees it as the previous
                    # iteration's assessment, and its responses accumulate afresh.
                    carry_now = dict(carry_now or {}, previous_integrator_design_state=content,
                                     recommendation=dict(recorded, in_link=True) if recorded else None)
                    sweep = []
                elif decision.next_node != gr.COMPLETE and gr.NODES.index(decision.next_node) <= gr.NODES.index(node):
                    # Back upstream: the roles from the target on re-run, so their earlier outputs are dropped.
                    keep = gr.NODES.index(decision.next_node)
                    sweep = [o for o in sweep if gr.NODES.index(o[0]) < keep]
                else:
                    sweep = [o for o in sweep if o[0] != node] + [(node, content, None)]
            executions += 1
            level, reason, rule = decision.level, decision.reason, decision.rule
            node = decision.next_node

        logger.info("[graph] link ended: %s after %d role runs at %s", stop_reason, executions, level)
        if messages:
            messages[-1].metadata["graph_stop_reason"] = stop_reason
            messages[-1].metadata["graph_preinit"] = self.preinit_report
            messages[-1].metadata["graph_orchestrator_turns"] = self.orchestrator_turns
        return messages
