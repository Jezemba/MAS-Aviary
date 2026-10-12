"""Graph-routed v2 (Jessica, 2026-10-12): routing rules from structured results, and the handler's walk.

Design: .claude/planning/graph_routed_v2/GRAPH_ROUTED_V2_DESIGN.md (rule numbers match the figure).
"""

import pytest

from src.coordination import graph_router as gr


def rec(tool, status="success", **outputs):
    return {"tool": tool, "status": status, "outputs": outputs, "note": outputs.get("error", "")}


# ---- forward flow, retries, escalation -------------------------------------------------------------

def test_a_successful_key_tool_moves_forward():
    d = gr.route_after_node(gr.AERO, [rec("create_su2_session"), rec("run_su2_solver", success=True)], "", gr.MODERATE, 0)
    assert (d.next_node, d.rule) == (gr.MASS, "flow")


def test_rule_1_retry_then_move_on_after_the_cap():
    bad = [rec("set_aircraft_parameters", "failed", error_code="UNKNOWN_PARAMETER",
               error="Unknown parameter: 'Aircraft.Wing.LIFT_COEFFICIENT'")]
    d = gr.route_after_node(gr.MISSION, bad, "", gr.SIMPLE, 0)
    assert (d.next_node, d.rule) == (gr.MISSION, "1")
    d = gr.route_after_node(gr.MISSION, bad, "", gr.SIMPLE, gr.RETRIES_PER_VISIT - 1)
    assert (d.next_node, d.rule) == (gr.SIMULATION, "1-cap")


def test_an_agent_reclassifies_with_the_field_only():
    ok = [rec("set_aircraft_parameters", valid=True)]
    d = gr.route_after_node(gr.MISSION, ok, "DESIGN_STATE:\n  COMPLEXITY: complex -- wing must grow", gr.SIMPLE, 0)
    assert (d.next_node, d.level, d.rule) == (gr.GEOMETRY, gr.COMPLEX, "escalate")
    # A stray word is not a reclassification (the old regex bug).
    d = gr.route_after_node(gr.MISSION, ok, "the complex wing planform was applied", gr.SIMPLE, 0)
    assert (d.next_node, d.level) == (gr.SIMULATION, gr.SIMPLE)


# ---- upstream edges from error codes (rules 2, 3/5, 8) ---------------------------------------------

def test_rule_2_aero_without_a_mesh_goes_to_geometry_complex():
    d = gr.route_after_node(gr.AERO, [rec("run_su2_solver", "refused", error_code="NO_MESH")], "", gr.MODERATE, 0)
    assert (d.next_node, d.level, d.rule) == (gr.GEOMETRY, gr.COMPLEX, "2")


def test_rule_5_missing_inputs_go_to_their_producer():
    refusal = rec("run_cycle", "refused", error_code="MISSING_REQUIRED_PARAMETERS",
                  missing=[{"variable": "mass.mtom_kg", "producer": "mass"}])
    d = gr.route_after_node(gr.PROPULSION, [refusal], "", gr.MODERATE, 0)
    assert (d.next_node, d.rule) == (gr.MASS, "5")


def test_rule_5_read_only_parameter_goes_to_the_producer():
    r = rec("set_aircraft_parameters", "failed", error_code="READ_ONLY_PARAMETER",
            error="Aircraft.Engine.SCALE_FACTOR is set by the framework")
    d = gr.route_after_node(gr.MISSION, [r], "", gr.SIMPLE, 0)
    assert (d.next_node, d.level) == (gr.PROPULSION, gr.MODERATE)


def test_rule_8_mission_on_a_different_wing_goes_to_geometry():
    r = rec("set_aircraft_parameters", "refused", error_code="GEOMETRY_NOT_APPLIED")
    d = gr.route_after_node(gr.MISSION, [r], "", gr.SIMPLE, 0)
    assert (d.next_node, d.level, d.rule) == (gr.GEOMETRY, gr.COMPLEX, "8")


# ---- simulation (rules 4, 6, 7, 9) ------------------------------------------------------------------

ZERO = [rec("run_simulation", "failed", error_code="ZERO_FUEL", summary={"fuel_burned_kg": 4e-6})]
TRIMMED = {"aero.cl_cruise": 0.5588, "aero.cd_cruise": 0.0229, "aero.target_cl": 0.559, "mass.mtom_kg": 78126.0}


def test_flown_goes_to_review_even_unconverged():
    d = gr.route_after_node(gr.SIMULATION, [rec("run_simulation", converged=False, summary={"fuel_burned_kg": 22998.6})],
                            "", gr.SIMPLE, 0)
    assert (d.next_node, d.rule) == (gr.REVIEW, "flow")


def test_rule_6_untrimmed_aero():
    reg = dict(TRIMMED, **{"aero.cl_cruise": 0.24})
    d = gr.route_after_node(gr.SIMULATION, ZERO, "", gr.SIMPLE, 0, reg)
    assert (d.next_node, d.level, d.rule) == (gr.AERO, gr.MODERATE, "6")


def test_rule_4_thrust_short_of_drag_scales_the_engine():
    d = gr.route_after_node(gr.SIMULATION, ZERO, "", gr.SIMPLE, 0, dict(TRIMMED, **{"prop.fn_lbf": 6336.0}))
    assert (d.next_node, d.level, d.rule) == (gr.PROPULSION, gr.MODERATE, "4")


def test_rule_6_engine_already_huge_means_the_drag_is_suspect():
    d = gr.route_after_node(gr.SIMULATION, ZERO, "", gr.SIMPLE, 0, dict(TRIMMED, **{"prop.fn_lbf": 13000.0}))
    assert (d.next_node, d.rule) == (gr.AERO, "6")


def test_rule_7_mass_residual_and_rule_9_nothing_implicated():
    reg = dict(TRIMMED, **{"prop.fn_lbf": 8100.0})
    mass_fail = {"violated_constraints": [{"constraint": "mission:constraints:mass_residual", "violation": 12455.9}]}
    assert gr.route_after_node(gr.SIMULATION, ZERO, "", gr.SIMPLE, 0, reg, mass_fail).next_node == gr.MASS
    dist_fail = {"violated_constraints": [{"constraint": "traj.cruise.collocation_constraint.defects:distance"}]}
    d = gr.route_after_node(gr.SIMULATION, ZERO, "", gr.SIMPLE, 0, reg, dist_fail)
    assert (d.next_node, d.rule) == (gr.MISSION, "9")


# ---- review (rules 10-13) ---------------------------------------------------------------------------

def test_review_routes_by_the_variable_not_the_named_role():
    out = ("DESIGN_STATE:\n  RECOMMENDATION_TO: aerodynamics_analyst\n"
           "  RECOMMENDED_CHANGE: Increase wing aspect ratio 9.36 -> 10.5 to reduce induced drag\n  VERDICT: CONTINUE")
    d = gr.route_after_review(out, gr.SIMPLE)
    assert (d.next_node, d.level, d.rule) == (gr.GEOMETRY, gr.COMPLEX, "13")
    d = gr.route_after_review("RECOMMENDATION_TO: propulsion_analyst\nRECOMMENDED_CHANGE: raise splitter BPR to 6\n"
                              "VERDICT: CONTINUE", gr.SIMPLE)
    assert (d.next_node, d.level) == (gr.PROPULSION, gr.MODERATE)


def test_review_complete_or_nothing_to_act_on_ends_the_link():
    assert gr.route_after_review("VERDICT: COMPLETE\nTASK_COMPLETE", gr.MODERATE).next_node == gr.COMPLETE
    assert gr.route_after_review("All constraints are close. VERDICT: CONTINUE", gr.SIMPLE).next_node == gr.COMPLETE


# ---- the handler's walk -----------------------------------------------------------------------------

class _KB:
    def __init__(self):
        self.entries = []


class _Agent:
    """Appends its scripted tool records to the knowledge base, records its input, returns its output."""

    def __init__(self, name, kb, script):
        self.description, self.tools, self._kb, self._script, self.calls = f"ROLE: {name.upper()}", {"t": 1}, kb, list(script), []

    def run(self, context):
        self.calls.append(context)
        records, output = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        self._kb.entries.extend(records)
        return output


def test_the_handler_walks_simple_then_escalates_on_the_recommendation(monkeypatch):
    from types import SimpleNamespace

    from src.coordination import conditional_graph_handler as h_mod
    from src.coordination import prompt_assembly
    from src.coordination.execution_handler import Assignment

    kb = _KB()
    monkeypatch.setattr(prompt_assembly, "_mode", prompt_assembly.VERBATIM)
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: None)
    monkeypatch.setattr(prompt_assembly, "read_design_json_text", lambda: '{"aero": {}}')
    monkeypatch.setattr("src.tools.knowledge_base.get_kb", lambda: kb)
    monkeypatch.setattr("src.tools.data_plane.get_design_state",
                        lambda: SimpleNamespace(data_store={}, sessions={}))
    flown = ([rec("run_simulation", summary={"fuel_burned_kg": 22998.6})], "DESIGN_STATE:\n  FUEL_BURNED_KG: 22998.6")
    agents = {
        gr.MISSION: _Agent(gr.MISSION, kb, [([rec("set_aircraft_parameters", "failed", error_code="UNKNOWN_PARAMETER")], "x"),
                                            ([rec("set_aircraft_parameters", valid=True)], "DESIGN_STATE:\n  SESSION_ID: s")]),
        gr.SIMULATION: _Agent(gr.SIMULATION, kb, [flown]),
        gr.REVIEW: _Agent(gr.REVIEW, kb, [([], "RECOMMENDATION_TO: propulsion_analyst\nRECOMMENDED_CHANGE: raise BPR to 6\n"
                                               "VERDICT: CONTINUE"), ([], "VERDICT: COMPLETE")]),
        gr.PROPULSION: _Agent(gr.PROPULSION, kb, [([rec("run_cycle", converged=True)],
                                                   "DESIGN_STATE:\n  RECOMMENDATION_IMPLEMENTED: BPR 5.1 -> 6.0")]),
    }
    handler = h_mod.ConditionalGraphHandler({"preinit": False})
    msgs = handler.execute([Assignment(agent_name=gr.GEOMETRY, task="T")], agents, None)
    path = [(r["node"], r["rule"]) for r in handler.route_log]
    assert path == [(gr.MISSION, "1"), (gr.MISSION, "flow"), (gr.SIMULATION, "flow"), (gr.REVIEW, "11"),
                    (gr.PROPULSION, "flow"), (gr.MISSION, "flow"), (gr.SIMULATION, "flow"), (gr.REVIEW, "complete")]
    assert handler.route_log[3]["next_level"] == gr.MODERATE
    # The re-run mission got the failure; the propulsion analyst got the recommendation; the second review
    # saw its response.
    assert "Your previous attempt did not succeed" in agents[gr.MISSION].calls[1]
    assert "RECOMMENDED_CHANGE: raise BPR to 6" in agents[gr.PROPULSION].calls[0]
    assert "RECOMMENDATION_IMPLEMENTED (reported by propulsion_analyst): BPR 5.1 -> 6.0" in agents[gr.REVIEW].calls[1]
    assert msgs[-1].metadata["graph_stop_reason"] == "complete"


def test_the_budget_stops_the_link(monkeypatch):
    from types import SimpleNamespace

    from src.coordination import conditional_graph_handler as h_mod
    from src.coordination import prompt_assembly
    from src.coordination.execution_handler import Assignment

    kb = _KB()
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: None)
    monkeypatch.setattr(prompt_assembly, "read_design_json_text", lambda: None)
    monkeypatch.setattr("src.tools.knowledge_base.get_kb", lambda: kb)
    monkeypatch.setattr("src.tools.data_plane.get_design_state", lambda: SimpleNamespace(data_store={}, sessions={}))
    zero = ([rec("run_simulation", "failed", error_code="ZERO_FUEL", summary={"fuel_burned_kg": 0.0})], "no fuel")
    agents = {gr.MISSION: _Agent(gr.MISSION, kb, [([rec("set_aircraft_parameters", valid=True)], "ok")]),
              gr.SIMULATION: _Agent(gr.SIMULATION, kb, [zero])}
    handler = h_mod.ConditionalGraphHandler({"preinit": False})
    msgs = handler.execute([Assignment(agent_name=gr.GEOMETRY, task="T")], agents, None)
    # No aero in the registry -> rule 6 sends it to AERO, which has no agent here: the link stops, not loops.
    assert len(handler.route_log) <= gr.BUDGET[gr.MODERATE]
    assert msgs[-1].metadata["graph_stop_reason"]


def test_the_retry_cap_also_applies_to_the_simulation():
    d = gr.route_after_node(gr.SIMULATION, [], "", gr.SIMPLE, gr.RETRIES_PER_VISIT - 1)
    assert (d.next_node, d.rule) == (gr.REVIEW, "1-cap")
