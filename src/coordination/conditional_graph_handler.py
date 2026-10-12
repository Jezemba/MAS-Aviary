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
        self.route_log: list[dict] = []
        self.preinit_report: dict = {}

    @staticmethod
    def _routing_text(node: str, level: str, reason: str, rule: str, feedback: str | None) -> str:
        lines = [f"GRAPH ROUTING (framework): complexity level {_LEVEL_TEXT[level]}.",
                 f"You are running because: {reason} (rule {rule})."]
        if feedback:
            lines.append(f"Your previous attempt did not succeed: {feedback}. Try a different approach to fix this.")
        lines.append("If you judge that the task needs more disciplines re-run, call request_complexity with the "
                     "level (moderate or complex) and the reason.")
        return "\n".join(lines)

    def execute(self, assignments: list[Assignment], agents: dict, logger_=None, turn_offset: int = 0,
                action_metadata: dict | None = None, **kwargs) -> list[AgentMessage]:
        from src.coordination import graph_preinit, prompt_assembly
        from src.tools import coupling
        from src.tools.data_plane import get_design_state
        from src.tools.knowledge_base import get_kb

        from src.tools import recommendation_tools as rt

        inst_logger = kwargs.get("logger", logger_)
        # B180: graph-routed agents reclassify with a tool call; only this handler hands that tool out.
        for agent in agents.values():
            tools = getattr(agent, "tools", None)
            if isinstance(tools, dict) and rt.RequestComplexity.name not in tools:
                tools[rt.RequestComplexity.name] = rt.RequestComplexity()
        carry = prompt_assembly.read_carry_over() or {}
        if self._preinit:
            self.preinit_report = graph_preinit.prepare(carry)
        level, node = gr.SIMPLE, gr.ENTRY_OF_LEVEL[gr.SIMPLE]
        reason, rule = ("this iteration starts at SIMPLE: the framework has prepared the sessions, CPACS, mesh "
                        "and discipline results, so the mission is set on them", "start")
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
            base = prompt_assembly.verbatim_stage_input(
                getattr(agent, "description", "") or "", sweep, carry_now, node,
                prompt_assembly.read_design_json_text())
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
            asked = [s["level"] for s in rt.complexity_requests_since(seq0)]
            requested = max(asked, key=gr.LEVELS.index) if asked else None
            recorded = next((s for s in reversed(rt.signals("recommendation")) if s["seq"] > seq0), None)
            if node == gr.REVIEW:
                decision = gr.route_after_review(content, level, recorded, requested)
            else:
                infeasible = ((state.data_store or {}).get("_simulation_flown_infeasible") or {}) if state else {}
                decision = gr.route_after_node(node, records, content if not error else f"error: {error}", level,
                                               retries, coupling.registry(state) if state else {},
                                               infeasible.get("failure"), requested)
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
        return messages
