"""B175: sequential + iterative_feedback builds each stage's input with Jessica's verbatim assembly (2026-10-10).

Before, verbatim mode switched the system prompt but this handler still sent the runner's task text plus
"Context from previous agent": no stage prompt, no design.json, no carry-over, no recommendation. Now the
stage input is exactly the staged pipeline's, and the handler adds only its own mechanism (retry feedback).
The coordinator calls the handler once per stage, so earlier stages' outputs are kept on the handler.
"""

import pytest

from src.coordination import prompt_assembly
from src.coordination.execution_handler import Assignment
from src.coordination.iterative_feedback_handler import IterativeFeedbackHandler

REC = ('{"DESIGN_STATE": {"RECOMMENDATION_TO": "aerodynamics_analyst", '
       '"RECOMMENDED_CHANGE": "Increase aspect ratio 9.36 -> 10.5", "MDO_REASONING": "x"}}')


class _Agent:
    def __init__(self, description, responses):
        self.description = description
        self.tools = {"t": True}
        self._responses = list(responses)
        self.calls = []

    def run(self, context):
        self.calls.append(context)
        r = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(r, Exception):
            raise r
        return r


def _cfg():
    return {"max_retries": 5, "feedback_window": 3, "retry_toolless_agents": False,
            "aspiration_mode": "tool_success", "human_feedback_mode": "none",
            "termination_keyword": "TASK_COMPLETE"}


@pytest.fixture
def verbatim(monkeypatch):
    monkeypatch.setattr(prompt_assembly, "_mode", prompt_assembly.VERBATIM)
    monkeypatch.setattr(prompt_assembly, "read_carry_over",
                        lambda: {"previous_integrator_design_state": REC, "starting_design": {}})
    monkeypatch.setattr(prompt_assembly, "read_design_json_text", lambda: '{"geometry": {}}')


def test_each_stage_gets_its_prompt_the_previous_design_state_and_the_responses(verbatim):
    h = IterativeFeedbackHandler(_cfg())
    geo = _Agent("ROLE: GEOMETRY ENGINEER", ["{'GEOMETRY_SET': True, 'RECOMMENDATION_IMPLEMENTED': 'AR 9.36 -> 10.5'}"])
    aero = _Agent("ROLE: AERODYNAMICS ANALYST", ["DESIGN_STATE:\n  CL_CRUISE: 0.55"])
    integ = _Agent("ROLE: MDO INTEGRATOR", ["VERDICT: CONTINUE"])
    agents = {"geometry_engineer": geo, "aerodynamics_analyst": aero, "mdo_integrator": integ}
    for name in ("geometry_engineer", "aerodynamics_analyst", "mdo_integrator"):   # one call per stage
        h.execute([Assignment(agent_name=name, task="RUNNER TASK TEXT")], agents, logger=None)

    first = geo.calls[0]
    assert first.startswith("ROLE: GEOMETRY ENGINEER") and "RUNNER TASK TEXT" not in first
    assert "RECOMMENDED_CHANGE: Increase aspect ratio 9.36 -> 10.5" in first and "design.json" in first
    second = aero.calls[0]
    assert second.startswith("ROLE: AERODYNAMICS ANALYST")
    assert "DESIGN_STATE from the previous agent (geometry_engineer):\n{'GEOMETRY_SET': True" in second
    assert "RECOMMENDATION_IMPLEMENTED (reported by geometry_engineer): AR 9.36 -> 10.5" in second
    assert "Context from previous agent" not in second
    last = integ.calls[0]
    assert "DESIGN_STATE from the previous agent (aerodynamics_analyst)" in last
    assert "Use these responses in your verdict" in last


def test_a_failed_attempt_adds_only_the_retry_feedback(verbatim):
    h = IterativeFeedbackHandler(_cfg())
    geo = _Agent("ROLE: GEOMETRY ENGINEER", [RuntimeError("mesh failed"), "DESIGN_STATE:\n  GEOMETRY_SET: true"])
    h.execute([Assignment(agent_name="geometry_engineer", task="T")], {"geometry_engineer": geo}, logger=None)
    assert len(geo.calls) == 2
    retry = geo.calls[1]
    assert retry.startswith("ROLE: GEOMETRY ENGINEER")
    assert "mesh failed" in retry.split("design.json", 1)[1]


def test_framework_mode_is_unchanged(monkeypatch):
    monkeypatch.setattr(prompt_assembly, "_mode", prompt_assembly.FRAMEWORK)
    h = IterativeFeedbackHandler(_cfg())
    a = _Agent("ROLE: X", ["ok"])
    b = _Agent("ROLE: Y", ["ok2"])
    h.execute([Assignment(agent_name="a", task="T1"), Assignment(agent_name="b", task="T2")],
              {"a": a, "b": b}, logger=None)
    assert a.calls[0].endswith("T1") and "Context from previous agent:\nok" in b.calls[0]
