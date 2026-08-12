"""Metrics that were hardcoded to null must actually be computed.

`org_theory_metrics` returned `orchestrator_token_growth: None` and
`information_ratio: None` unconditionally, with warnings explaining why:

    "orchestrator_token_growth: token_count is always null in current messages"
    "information_ratio: requires token_count on messages, always null in current runs"

The cause was upstream: `token_count` was set at only 3 of the 11 AgentMessage
construction sites, so the field looked universally unavailable. Task and prompt
messages carry information too -- an orchestrator's instruction to a worker is
exactly what "information asymmetry" measures -- so every site now populates it.
"""

from __future__ import annotations

import pytest

from src.coordination.history import AgentMessage, estimate_token_count
from src.logging.org_theory_metrics import compute_org_theory_metrics


def _msg(agent, content, turn, tokens=None):
    return AgentMessage(
        agent_name=agent,
        content=content,
        turn_number=turn,
        timestamp=float(turn),
        token_count=tokens if tokens is not None else estimate_token_count(content),
    )


class TestEstimator:
    def test_uses_real_usage_when_available(self):
        class _Agent:
            class memory:
                steps = [type("S", (), {"token_usage": {"input_tokens": 100, "output_tokens": 50}})()]

        assert estimate_token_count("short", _Agent()) == 150

    def test_falls_back_to_length(self):
        assert estimate_token_count("x" * 400) == 100

    def test_empty_content_is_none_not_zero(self):
        """Zero would read as a real 'no information' observation."""
        assert estimate_token_count("") is None


class TestMetricsNowComputed:
    def _metrics(self):
        messages = [
            _msg("orchestrator", "a" * 400, 1),
            _msg("aero_worker", "b" * 800, 2),
            _msg("orchestrator", "c" * 800, 3),
            _msg("mass_worker", "d" * 400, 4),
        ]
        return compute_org_theory_metrics(
            messages=messages, os_name="orchestrated",
            handler_name="iterative_feedback", config={},
        )

    def test_information_ratio_is_a_number(self):
        v = self._metrics()["information_ratio"]
        assert v is not None and 0.0 <= v <= 1.0

    def test_information_ratio_reflects_the_split(self):
        """orchestrator 100+200 tokens, workers 200+100 -> even split."""
        assert self._metrics()["information_ratio"] == pytest.approx(0.5, abs=0.01)

    def test_orchestrator_token_growth_is_a_number(self):
        assert self._metrics()["orchestrator_token_growth"] is not None

    def test_growth_is_positive_when_context_accumulates(self):
        """orchestrator turns of 100 then 200 tokens -> +100 per turn."""
        assert self._metrics()["orchestrator_token_growth"] == pytest.approx(100.0)

    def test_no_stale_null_warning_remains(self):
        w = " ".join(self._metrics().get("warnings") or [])
        assert "token_count is always null" not in w
        assert "always null in current runs" not in w


class TestDegenerateCases:
    def test_no_tokens_anywhere_reports_none_not_zero(self):
        messages = [_msg("orchestrator", "", 1, tokens=None),
                    _msg("worker", "", 2, tokens=None)]
        out = compute_org_theory_metrics(
            messages=messages, os_name="orchestrated",
            handler_name="iterative_feedback", config={},
        )
        assert out["information_ratio"] is None

    def test_single_orchestrator_turn_has_no_growth(self):
        """Growth needs at least two turns; one must not read as 0."""
        messages = [_msg("orchestrator", "a" * 400, 1), _msg("worker", "b" * 400, 2)]
        out = compute_org_theory_metrics(
            messages=messages, os_name="orchestrated",
            handler_name="iterative_feedback", config={},
        )
        assert out["orchestrator_token_growth"] is None
