"""Tests for scripts/visualize_run.py — the post-run HTML visualizer.

The attribution heuristic is the load-bearing piece: under concurrent
stdout interleaving (concurrent_blackboard mode), three peers' Step
blocks mix without per-line agent banners. The viz must still attribute
each blackboard event to the correct peer. The most error-prone case is
a REJECTED claim_todo: the rejection message names the current owner,
not the caller, so a naive regex over the message would mis-attribute
the event to the winner.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def viz():
    """Load scripts/visualize_run.py as a module."""
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "visualize_run", repo_root / "scripts" / "visualize_run.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["visualize_run"] = module
    spec.loader.exec_module(module)
    return module


class TestAttributeAgent:
    def test_successful_claim_attributes_to_caller(self, viz):
        # A successful claim_todo response carries attempted_by =
        # current_owner = caller; attribution must be the caller.
        observation = (
            '{"success": true, "todo_name": "geometry", '
            '"attempted_by": "agent_3", "current_owner": "agent_3", '
            '"message": "\'agent_3\' successfully claimed \'geometry\'"}'
        )
        agent = viz._attribute_agent(
            "claim_todo",
            "{'todo_name': 'geometry'}",
            observation,
            last_run_banner_agent="agent_1",  # stale banner — must be overridden
        )
        assert agent == "agent_3"

    def test_failed_claim_attributes_to_caller_not_winner(self, viz):
        # The crucial regression case. agent_2 tried to claim mission
        # but agent_1 already had it. The message names agent_1 (winner),
        # but the EVENT belongs to agent_2 (the caller). The attribution
        # heuristic must read attempted_by from the JSON payload, not
        # regex-grab the first agent_X mention from the message.
        observation = (
            '{"success": false, "todo_name": "mission", '
            '"attempted_by": "agent_2", "current_owner": "agent_1", '
            '"message": "TODO \'mission\' is currently claimed by '
            "'agent_1' — 'agent_2', pick a different TODO\"}"
        )
        agent = viz._attribute_agent(
            "claim_todo",
            "{'todo_name': 'mission'}",
            observation,
            last_run_banner_agent=None,
        )
        assert agent == "agent_2", (
            "rejected claim must attribute to caller (agent_2), not "
            "winner (agent_1) named in the message"
        )

    def test_write_blackboard_still_uses_key_prefix(self, viz):
        # Regression guard: the existing write_blackboard heuristic
        # (key prefix) still works after the claim_todo overhaul.
        observation = '{"success": true, "key": "agent_4_status", "version": 1}'
        agent = viz._attribute_agent(
            "write_blackboard",
            "{'key': 'agent_4_status', 'value': 'foo', 'entry_type': 'status'}",
            observation,
            last_run_banner_agent=None,
        )
        assert agent == "agent_4"

    def test_read_blackboard_falls_back_to_banner(self, viz):
        # read_blackboard observations don't name the caller; we have
        # to fall back to the most recent run banner.
        observation = '{"entries": [], "total_entries": 0}'
        agent = viz._attribute_agent(
            "read_blackboard",
            "{}",
            observation,
            last_run_banner_agent="agent_2",
        )
        assert agent == "agent_2"

    def test_claim_todo_without_attempted_by_falls_back_gracefully(self, viz):
        # Back-compat: old logs (pre-fix) don't carry attempted_by.
        # The parser should still produce a non-empty attribution
        # rather than crashing.
        observation = (
            '{"success": false, "todo_name": "mission", '
            '"message": "TODO \'mission\' is currently claimed by '
            "'agent_1' — try a different TODO\"}"
        )
        agent = viz._attribute_agent(
            "claim_todo",
            "{'todo_name': 'mission'}",
            observation,
            last_run_banner_agent="agent_2",
        )
        # In the legacy case we have no way to tell who the caller is,
        # so we fall back to either the message-named agent or the
        # banner. Either is acceptable — we just must not crash.
        assert agent in ("agent_1", "agent_2")
