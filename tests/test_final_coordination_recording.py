"""C1-C7 of the final re-run bug list (2026-11-18): coordination and recording.

No network, no API, no live servers. Evidence: l20_final Case 5 and Case 6
(ws8 /data/Jessica/l20_bugchecks/case5, case6).
"""

import json

import pytest
import yaml

from src.coordination import graph_router as gr
from src.coordination.design_state import DesignState
from src.coordination.history import AgentMessage
from src.coordination.strategy import CoordinationAction, CoordinationStrategy
from src.tools.data_plane import get_design_state, init_data_plane, intercept_response, resolve_request

TOOL_SERVER_MAP = {
    "read_history_csv": "su2",
    "get_design_space": "aviary",
    "set_aircraft_parameters": "aviary",
}


@pytest.fixture(autouse=True)
def _clean():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


# ---- C1 / C7: how the coordinator loop ended is recorded --------------------------------------------------------

class _SameAnswerAgent:
    def __init__(self, text="I increased Wing1's aspect ratio from 10.0 to 11.0."):
        self.text, self.runs = text, 0

    def run(self, task):
        self.runs += 1
        return self.text


class _OneAgentStrategy(CoordinationStrategy):
    """Always runs agent_1; optionally reports completion or terminates after `after` messages."""

    def __init__(self, complete_after=None, terminate_after=None):
        self.complete_after, self.terminate_after = complete_after, terminate_after
        self.completion_reason = None
        self.recorded = []

    def initialize(self, agents, config):
        pass

    def next_step(self, history, current_state):
        if self.terminate_after is not None and len(history) >= self.terminate_after:
            return CoordinationAction("terminate", None, "", {"reason": "budget of 3 role runs spent"})
        return CoordinationAction("invoke_agent", "agent_1", "task")

    def is_complete(self, history, current_state):
        if self.complete_after is not None and len(history) >= self.complete_after:
            self.completion_reason = "task_complete: agent_3 called mark_task_done (DONE: nothing left)"
            return True
        return False

    def get_agent_context(self, agent_name, history, current_state):
        return ""

    def record_coordinator_stop(self, stop):
        self.recorded.append(stop)


def _coordinator(strategy, agent=None):
    from src.coordination.coordinator import Coordinator

    return Coordinator({"agent_1": agent or _SameAnswerAgent()}, strategy, {"termination": {"max_turns": 20}})


def test_c1_the_stuck_rule_still_stops_the_loop_and_is_now_recorded():
    strategy, agent = _OneAgentStrategy(), _SameAnswerAgent()
    result = _coordinator(strategy, agent).run("task")
    # Behaviour unchanged: two identical outputs in a row from one agent end the loop.
    assert agent.runs == 2 and len(result.history) == 2
    assert result.stop["by"] == "termination_rule"
    assert result.stop["code"] == "stuck_identical_output"
    assert result.stop["agent"] == "agent_1"
    assert result.stop["reason"].startswith("stuck_identical_output")
    assert strategy.recorded == [result.stop]
    assert get_design_state().data_store["coordination_stop"]["code"] == "stuck_identical_output"


def test_c7_strategy_completion_and_terminate_are_recorded():
    result = _coordinator(_OneAgentStrategy(complete_after=1), _SameAnswerAgent()).run("task")
    assert result.stop == {"by": "strategy_complete",
                           "reason": "task_complete: agent_3 called mark_task_done (DONE: nothing left)"}

    class _Varying(_SameAnswerAgent):
        def run(self, task):
            self.runs += 1
            return f"output {self.runs}"

    result = _coordinator(_OneAgentStrategy(terminate_after=3), _Varying()).run("task")
    assert len(result.history) == 3
    assert result.stop == {"by": "strategy_terminate", "reason": "budget of 3 role runs spent"}
    assert get_design_state().data_store["coordination_stop"]["by"] == "strategy_terminate"


class _Board:
    completions = 0


def _networked(**attrs):
    from src.coordination.strategies.networked import NetworkedStrategy

    s = NetworkedStrategy.__new__(NetworkedStrategy)
    defaults = dict(_board_graph=None, completion_reason=None, _termination_keyword="TASK_COMPLETE", _max_turns=100,
                    _consecutive_errors=0, _max_consecutive_errors=3, _blackboard=None, _agent_order=["agent_1"],
                    _graph_complete=False, _selection_mode="concurrent_blackboard")
    defaults.update(attrs)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def test_c1_networked_board_graph_gets_the_stuck_stop_reason_in_design_json():
    from src.coordination.board_graph import BoardGraph

    bg = BoardGraph(_Board(), preinit=False)
    s = _networked(_board_graph=bg)
    stop = {"by": "termination_rule", "code": "stuck_identical_output", "agent": "agent_1",
            "reason": "stuck_identical_output: agent_1 gave identical output in two consecutive turns "
                      "(coordinator stuck rule)"}
    s.record_coordinator_stop(stop)
    assert bg.stop_reason.startswith("stuck_identical_output")
    assert get_design_state().data_store["graph_route"]["stop_reason"].startswith("stuck_identical_output")
    # A reason the board graph already named is kept; other exits are the strategy's own business.
    bg.stop_reason = "budget of 25 role runs at complex spent"
    s.record_coordinator_stop(stop)
    s.record_coordinator_stop({"by": "strategy_terminate", "reason": "x"})
    assert bg.stop_reason == "budget of 25 role runs at complex spent"


def test_c7_networked_mark_task_done_exit_names_who_ended_the_attempt():
    from src.coordination.blackboard import Blackboard

    bb = Blackboard()
    bb.write("task_complete", "DONE: All tasks are blocked or failed", "agent_3", "status")
    s = _networked(_blackboard=bb)
    msg = AgentMessage(agent_name="agent_3", content="All tasks are blocked", turn_number=1, timestamp=0.0)
    assert s.is_complete([msg], {}) is True
    assert s.completion_reason.startswith("task_complete: agent_3 called mark_task_done")


# ---- C2: "complete" only for the integrator's COMPLETE verdict --------------------------------------------------

def test_c2_a_review_with_nothing_to_route_has_its_own_rule_and_still_ends_the_link():
    d = gr.route_after_review("All constraints are close. VERDICT: CONTINUE", gr.SIMPLE)
    assert (d.next_node, d.rule) == (gr.COMPLETE, gr.NO_RECOMMENDATION_RULE)
    assert "CONTINUE" in d.reason
    d = gr.route_after_review("x", gr.SIMPLE, {"verdict": "CONTINUE", "recommendation_to": None,
                                                  "recommended_change": "do better"})
    assert (d.next_node, d.rule) == (gr.COMPLETE, gr.NO_RECOMMENDATION_RULE) and "recorded: CONTINUE" in d.reason
    assert gr.route_after_review("VERDICT: COMPLETE", gr.SIMPLE).rule == "complete"
    assert gr.route_after_review("x", gr.SIMPLE, {"verdict": "COMPLETE"}).rule == "complete"


class _KB:
    def __init__(self):
        self.entries = []


def rec(tool, status="success", **outputs):
    return {"tool": tool, "status": status, "outputs": outputs, "note": outputs.get("error", "")}


class _Agent:
    def __init__(self, name, kb, script, on_run=None):
        self.name, self.description, self.tools = name, f"ROLE: {name.upper()}", {"t": 1}
        self._kb, self._script, self.calls, self._on_run = kb, list(script), [], on_run

    def run(self, context):
        self.calls.append(context)
        if self._on_run is not None:
            self._on_run(self, len(self.calls))
        records, output = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        self._kb.entries.extend(records)
        return output


def _walk(monkeypatch, agents_fn):
    from types import SimpleNamespace

    from src.coordination import conditional_graph_handler as h_mod
    from src.coordination import prompt_assembly
    from src.coordination.execution_handler import Assignment

    kb = _KB()
    monkeypatch.setattr(prompt_assembly, "_mode", prompt_assembly.VERBATIM)
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: None)
    monkeypatch.setattr(prompt_assembly, "read_design_json_text", lambda: None)
    monkeypatch.setattr("src.tools.knowledge_base.get_kb", lambda: kb)
    real_state = get_design_state()
    monkeypatch.setattr("src.tools.data_plane.get_design_state",
                        lambda: SimpleNamespace(data_store=real_state.data_store, sessions={}))
    handler = h_mod.ConditionalGraphHandler({"preinit": False})
    msgs = handler.execute([Assignment(agent_name=gr.GEOMETRY, task="T")], agents_fn(kb), None)
    return handler, msgs


FLOWN = ([rec("run_simulation", summary={"fuel_burned_kg": 22998.6})], "DESIGN_STATE:\n  FUEL_BURNED_KG: 22998.6")


def test_c2_the_handler_names_the_no_recommendation_end(monkeypatch):
    def agents(kb):
        return {gr.MISSION: _Agent(gr.MISSION, kb, [([rec("set_aircraft_parameters", valid=True)], "ok")]),
                gr.SIMULATION: _Agent(gr.SIMULATION, kb, [FLOWN]),
                gr.REVIEW: _Agent(gr.REVIEW, kb, [([], '{"VERDICT": "CONTINUE", "MDO_REASONING": "close"}')])}

    handler, msgs = _walk(monkeypatch, agents)
    assert [(r["node"], r["rule"]) for r in handler.route_log] == [
        (gr.MISSION, "flow"), (gr.SIMULATION, "flow"), (gr.REVIEW, gr.NO_RECOMMENDATION_RULE)]
    assert msgs[-1].metadata["graph_stop_reason"] == "no_actionable_recommendation"
    assert msgs[-1].metadata["completion_met"] is True     # as before: the review ran


# ---- C6: the integrator can record the same recommendation on a later visit -------------------------------------

def _record(agent_name, verdict, to=None, change=None):
    from src.tools import recommendation_tools as rt
    from src.tools.agent_context import agent_scope

    with agent_scope(agent_name):
        return json.loads(rt.RecordRecommendation().forward(verdict, to, change))


def test_c6_a_repeat_is_refused_within_a_visit_but_recorded_in_a_new_one():
    from src.tools import recommendation_tools as rt

    args = ("mdo_integrator", "CONTINUE", "propulsion_analyst", "raise BPR to 6")
    assert _record(*args)["recorded"] is True
    # No visit marked (networked, staged): the old rule stands.
    assert "Already recorded" in _record(*args)["error"]
    rt.begin_visit(["mdo_integrator"], rt.last_seq())
    second = _record(*args)
    assert second["recorded"] is True and second["seq"] == 2
    assert "Already recorded (seq 2)" in _record(*args)["error"]


def test_c6_the_graph_routes_a_repeated_recommendation_instead_of_ending(monkeypatch):
    """S_GR iterations 4, 6, 7, 8, 10, 11, 12, 18, 20: the last review repeated the previous one, the tool refused
    it, and the review ended the link as 'no recommendation'. Now the repeat is recorded and routed."""
    verdicts = [("CONTINUE", "propulsion_analyst", "raise BPR to 6")] * 2 + [("COMPLETE", None, None)]

    def integrator_records(agent, visit):
        _record(agent.name, *verdicts[visit - 1])

    def agents(kb):
        return {
            gr.MISSION: _Agent(gr.MISSION, kb, [([rec("set_aircraft_parameters", valid=True)], "ok")]),
            gr.SIMULATION: _Agent(gr.SIMULATION, kb, [FLOWN]),
            gr.REVIEW: _Agent(gr.REVIEW, kb, [([], '{"VERDICT": "CONTINUE"}')], on_run=integrator_records),
            gr.PROPULSION: _Agent(gr.PROPULSION, kb, [([rec("run_cycle", converged=True)], "ok")]),
        }

    handler, msgs = _walk(monkeypatch, agents)
    reviews = [r["rule"] for r in handler.route_log if r["node"] == gr.REVIEW]
    assert reviews == ["11", "11", "complete"]
    assert msgs[-1].metadata["graph_stop_reason"] == "complete"


def test_c6_a_new_in_link_record_of_the_same_recommendation_is_a_new_activation():
    from src.tools import recommendation_tools as rt

    rt.activate("propulsion_analyst", "raise BPR to 6", recorded_seq=3)
    first = dict(rt.active_recommendation())
    rt.activate("propulsion_analyst", "raise BPR to 6", recorded_seq=3)
    assert rt.active_recommendation() == first
    _record("mdo_integrator", "CONTINUE", "propulsion_analyst", "raise BPR to 6")   # seq moves on
    rt.activate("propulsion_analyst", "raise BPR to 6", recorded_seq=7)
    assert rt.active_recommendation()["recorded_seq"] == 7
    assert rt.active_recommendation()["activated_at"] > first["activated_at"]
    # Without an in-link seq (carry-over, staged) a repeat is still not re-activated.
    again = dict(rt.active_recommendation())
    rt.activate("propulsion_analyst", "raise BPR to 6")
    assert rt.active_recommendation() == again


# ---- C3: every stage criterion names a tool its role has --------------------------------------------------------

def _stage_criteria(path):
    doc = yaml.safe_load(open(path))
    sp = doc.get("staged_pipeline", doc)
    return {s["name"]: s.get("completion_criteria") or {} for s in sp["stages"]}


def test_c3_sequential_aero_criterion_checks_the_su2_solve():
    seq = _stage_criteria("config/mdo_f25_staged_pipeline_sequential.yaml")
    assert seq["aerodynamics_analyst"]["tool_name"] == "run_su2_solver"
    # The orchestrated pipeline (O_SP) checks the same tool for every stage.
    orch = _stage_criteria("config/mdo_f25_staged_pipeline.yaml")
    for stage, crit in seq.items():
        assert crit.get("tool_name") == orch[stage].get("tool_name"), stage


def test_c3_every_sequential_criterion_tool_is_in_that_roles_toolset():
    agents = yaml.safe_load(open("config/mdo_f25_sequential_agents.yaml"))
    tools = {}

    def walk(node):
        if isinstance(node, dict):
            if "name" in node and isinstance(node.get("allowed_tools") or node.get("tools"), list):
                tools[node["name"]] = set(node.get("allowed_tools") or node.get("tools"))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(agents)
    for stage, crit in _stage_criteria("config/mdo_f25_staged_pipeline_sequential.yaml").items():
        if crit.get("check") == "tool_called":
            assert stage in tools, stage
            assert crit["tool_name"] in tools[stage], (stage, crit["tool_name"])


# ---- C4: a skipped eval is missing, not failed -----------------------------------------------------------------

def test_c4_eval_skipped_is_none_not_false():
    from src.logging.eval_classifier import classify_aviary_eval
    from src.logging.org_theory_metrics import _convergence_classification
    from src.runners.batch_runner import _eval_success_input

    skipped = classify_aviary_eval(None)
    assert skipped.result == "eval_skipped"
    assert _eval_success_input(skipped) is None
    assert _convergence_classification(0.4, _eval_success_input(skipped)) == "low_convergence"
    assert _eval_success_input(None) is None
    flew = classify_aviary_eval({"fuel_burned_kg": 16602.1, "gtow_kg": 92480.3})
    assert flew.result in ("omission", "commission") and _eval_success_input(flew) is False
    assert _convergence_classification(0.4, _eval_success_input(flew)) == "incoherent_team"


# ---- C5: injected_into_aviary is what aviary accepted -----------------------------------------------------------

DESIGN_SPACE = {"success": True, "parameters": [
    {"name": "Mission.Design.LIFT_COEFFICIENT", "min": 0.05, "max": 1.0},
    {"name": "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "min": 0.5, "max": 2.0},
    {"name": "Aircraft.Wing.ASPECT_RATIO", "min": 7.0, "max": 17.0}]}


def test_c5_a_refused_set_is_not_recorded_as_injected():
    from src.tools.design_json import build_design

    intercept_response("get_design_space", json.dumps(DESIGN_SPACE))
    intercept_response("read_history_csv",
                       json.dumps({"columns": ["CL", "CD"], "rows": [{"CL": 0.1802433149, "CD": -0.004643166003}]}))
    resolved = resolve_request("set_aircraft_parameters", {"session_id": "s", "parameters": {}})
    store = get_design_state().data_store
    params = resolved["parameters"]
    assert "Mission.Design.LIFT_COEFFICIENT" in params      # the framework prepared the call
    # N_IF 17: aviary refused the whole call (READ_ONLY_PARAMETER) -- nothing reached the mission.
    intercept_response("set_aircraft_parameters", {"success": False, "error_code": "READ_ONLY_PARAMETER",
                                                   "error": "'Aircraft.Wing.SPAN' is derived"})
    assert build_design(store)["aero"]["injected_into_aviary"] == {"LIFT_COEFFICIENT": None,
                                                                   "SUBSONIC_DRAG_COEFF_FACTOR": None}
    # Accepted: aviary's "applied" list is what the record shows.
    intercept_response("set_aircraft_parameters", {"success": True, "applied": [
        {"name": k, "old_value": None, "new_value": v} for k, v in params.items()]})
    injected = build_design(store)["aero"]["injected_into_aviary"]
    assert injected["LIFT_COEFFICIENT"] == pytest.approx(0.1802433149)
    assert injected["SUBSONIC_DRAG_COEFF_FACTOR"] == pytest.approx(params["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"])


def test_c5_a_later_refused_set_leaves_the_earlier_accepted_values():
    from src.tools.design_json import build_design

    store = get_design_state().data_store
    store["design_params_applied"] = {"Mission.Design.LIFT_COEFFICIENT": 0.55,
                                      "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR": 1.2}
    store["aero_injected_cl"], store["aero_injected_drag_factor"] = 0.63, 1.7   # prepared, then refused
    assert build_design(store)["aero"]["injected_into_aviary"] == {"LIFT_COEFFICIENT": 0.55,
                                                                   "SUBSONIC_DRAG_COEFF_FACTOR": 1.2}


# ---- C7: a suggestion at the current level does not tell the peers to request it --------------------------------

def _suggest_at(monkeypatch, board_level):
    from types import SimpleNamespace

    from src.coordination.board_graph import BoardGraph

    posts = []

    class _PostBoard(_Board):
        def write(self, key, value, author, entry_type):
            posts.append((key, value))

    kb = SimpleNamespace(entries=[{"tool": "run_simulation", "status": "refused"}])
    monkeypatch.setattr("src.tools.knowledge_base.get_kb", lambda: kb)
    monkeypatch.setattr(gr, "route_after_simulation", lambda *a, **k: gr.Decision(
        gr.GEOMETRY, gr.COMPLEX, "8", "run_simulation returned GEOMETRY_NOT_APPLIED: the geometry has to be redone"))
    bg = BoardGraph(_PostBoard(), preinit=False)
    bg.level = board_level
    bg._suggest()
    return bg.log[-1], posts[-1][1]


def test_c7_suggestion_at_the_current_level_says_request_complexity_cannot_act(monkeypatch):
    event, text = _suggest_at(monkeypatch, gr.COMPLEX)
    assert event["request_complexity_applies"] is False
    assert "call request_complexity" not in text and "cannot act on it" in text
    event, text = _suggest_at(monkeypatch, gr.SIMPLE)
    assert event["request_complexity_applies"] is True
    assert "To act on it, call request_complexity with that level; it is your call." in text
