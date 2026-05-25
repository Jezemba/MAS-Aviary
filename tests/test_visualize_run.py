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


# ---- Swim-lane parser (parallel-execution view) -----------------------------


def _make_synthetic_log(
    tool_blocks: list[tuple[str, str, str, int, float, int]],
) -> str:
    """Build a synthetic smolagents-style log string from a list of
    (tool, args_text, observation_text, step_num, duration_s, input_tokens)
    tuples. Each tuple becomes one step block in the order given."""
    lines: list[str] = []
    for tool, args, obs, step_num, dur, tokens in tool_blocks:
        lines.append("━━━━━━━━━━━━━━━━━━━━━ Step " + str(step_num) + " ━━━━━━━━━━━━━━━━━━━━━")
        lines.append("╭──────────────────────────────────────────────────────────────────────────────╮")
        lines.append(f"│ Calling tool: '{tool}' with arguments: {args}                                │")
        lines.append("╰──────────────────────────────────────────────────────────────────────────────╯")
        lines.append(f"Observations: {obs}")
        lines.append(
            f"[Step {step_num}: Duration {dur:.2f} seconds| Input tokens: {tokens} | Output tokens: 100]"
        )
    return "\n".join(lines)


class TestParseStepBlocks:
    def test_extracts_one_block_per_tool_call(self, viz, tmp_path):
        log_text = _make_synthetic_log([
            ("claim_todo", "{'todo_name': 'aero'}",
             '{"success": true, "todo_name": "aero", "attempted_by": "agent_1", '
             '"current_owner": "agent_1", "message": "\'agent_1\' successfully claimed \'aero\'"}',
             3, 1.5, 82000),
            ("claim_todo", "{'todo_name': 'mass'}",
             '{"success": true, "todo_name": "mass", "attempted_by": "agent_2", '
             '"current_owner": "agent_2", "message": "\'agent_2\' successfully claimed \'mass\'"}',
             3, 1.6, 82500),
        ])
        log_file = tmp_path / "log.txt"
        log_file.write_text(log_text)
        blocks = viz.parse_step_blocks(str(log_file))
        assert len(blocks) == 2
        assert {b.tool for b in blocks} == {"claim_todo"}
        assert {b.agent for b in blocks} == {"agent_1", "agent_2"}
        assert all(b.duration_s > 0 for b in blocks)
        assert all(b.attributed_via == "inline" for b in blocks)

    def test_token_match_attributes_mcp_calls_to_correct_peer(self, viz, tmp_path):
        # agent_1 and agent_2 both make a claim_todo at step 3 with
        # distinguishing token counts (82000 vs 82500). Then an MCP
        # tool (open_cpacs) fires at step 4 with no inline attribution
        # but with 110005 tokens — agent_1 had 82000 → ~110000 growth,
        # agent_2 had 82500 → ~110500. So 110005 is much closer to
        # agent_1's projected growth.
        log_text = _make_synthetic_log([
            ("claim_todo", "{'todo_name': 'aero'}",
             '{"success": true, "todo_name": "aero", "attempted_by": "agent_1", '
             '"current_owner": "agent_1", "message": "\'agent_1\' successfully claimed \'aero\'"}',
             3, 1.5, 82000),
            ("claim_todo", "{'todo_name': 'mass'}",
             '{"success": true, "todo_name": "mass", "attempted_by": "agent_2", '
             '"current_owner": "agent_2", "message": "\'agent_2\' successfully claimed \'mass\'"}',
             3, 1.6, 82500),
            ("claim_todo", "{'todo_name': 'geom'}",
             '{"success": true, "todo_name": "geom", "attempted_by": "agent_1", '
             '"current_owner": "agent_1", "message": "\'agent_1\' successfully claimed \'geom\'"}',
             4, 2.0, 110005),
            ("claim_todo", "{'todo_name': 'mission'}",
             '{"success": true, "todo_name": "mission", "attempted_by": "agent_2", '
             '"current_owner": "agent_2", "message": "\'agent_2\' successfully claimed \'mission\'"}',
             4, 2.1, 110500),
            # MCP tool at step 4 with no inline attribution. Token
            # count 110010 → closest to agent_1's 110005.
            ("open_cpacs", "{'path': '/tmp/f.xml'}",
             '{"session_id": "abc", "ok": true}',
             4, 8.0, 110010),
        ])
        log_file = tmp_path / "log.txt"
        log_file.write_text(log_text)
        blocks = viz.parse_step_blocks(str(log_file))
        mcp_block = next(b for b in blocks if b.tool == "open_cpacs")
        assert mcp_block.agent == "agent_1"
        assert mcp_block.attributed_via == "token-match"

    def test_input_tokens_captured_with_commas(self, viz, tmp_path):
        # smolagents pretty-prints token counts with commas. The
        # parser must accept "82,715" and produce int 82715.
        log_text = (
            "━━━ Step 3 ━━━\n"
            "│ Calling tool: 'read_todos' with arguments: {}                       │\n"
            'Observations: - mission status=pending\n'
            "[Step 3: Duration 1.50 seconds| Input tokens: 82,715 | Output tokens: 100]\n"
        )
        log_file = tmp_path / "log.txt"
        log_file.write_text(log_text)
        blocks = viz.parse_step_blocks(str(log_file))
        assert len(blocks) == 1
        assert blocks[0].input_tokens == 82715
        assert blocks[0].step_num == 3
        assert blocks[0].duration_s == 1.50

    def test_legacy_log_without_attempted_by_still_parses(self, viz, tmp_path):
        # Pre-2026-05-25 logs don't carry attempted_by. The parser
        # must not crash; it falls back to the agent named in the
        # message (winner — known acceptable for legacy back-compat).
        log_text = _make_synthetic_log([
            ("claim_todo", "{'todo_name': 'aero'}",
             '{"success": true, "todo_name": "aero", '
             '"message": "\'agent_1\' successfully claimed \'aero\'"}',
             3, 1.5, 82000),
        ])
        log_file = tmp_path / "log.txt"
        log_file.write_text(log_text)
        blocks = viz.parse_step_blocks(str(log_file))
        assert len(blocks) == 1
        assert blocks[0].agent == "agent_1"


class TestBuildSwimLanes:
    def test_per_peer_cumulative_time_is_monotonic(self, viz, tmp_path):
        # Each peer's blocks should be sorted by step number and their
        # cumulative start times must be monotonically non-decreasing.
        log_text = _make_synthetic_log([
            ("claim_todo", "{}",
             '{"success": true, "attempted_by": "agent_1", "current_owner": "agent_1", '
             '"message": "\'agent_1\' ok"}',
             3, 2.0, 82000),
            ("write_blackboard", "{'key': 'agent_1_status', 'value': 'v', 'entry_type': 'status'}",
             '{"success": true, "key": "agent_1_status"}',
             4, 3.0, 110000),
            ("mark_todo_done", "{}",
             '{"success": true, "attempted_by": "agent_1", "current_owner": "agent_1", '
             '"message": "ok"}',
             5, 1.5, 140000),
        ])
        log_file = tmp_path / "log.txt"
        log_file.write_text(log_text)
        blocks = viz.parse_step_blocks(str(log_file))
        lanes = viz.build_swim_lanes(blocks)
        assert "agent_1" in lanes
        cum_times = [start for start, _, _ in lanes["agent_1"]]
        assert cum_times == sorted(cum_times)
        # Three steps; first starts at 0; second at 2.0; third at 5.0
        assert cum_times[0] == 0.0
        assert cum_times[1] == 2.0
        assert cum_times[2] == 5.0

    def test_zero_duration_blocks_excluded_from_lanes(self, viz):
        # Multi-tool-step synthetic blocks (step_num=-1, duration_s=0)
        # should be dropped before building lanes.
        block_zero = viz.StepBlock(
            step_num=-1, duration_s=0.0, input_tokens=0,
            tool="set_inputs", args_text="{}", observation="",
            agent="agent_1", attributed_via="inline", log_line=10,
        )
        block_normal = viz.StepBlock(
            step_num=3, duration_s=1.5, input_tokens=82000,
            tool="claim_todo", args_text="{}", observation="",
            agent="agent_1", attributed_via="inline", log_line=20,
        )
        lanes = viz.build_swim_lanes([block_zero, block_normal])
        assert len(lanes["agent_1"]) == 1
        assert lanes["agent_1"][0][2].tool == "claim_todo"


class TestSwimLaneRender:
    def test_render_includes_lane_per_known_agent(self, viz):
        # Build a tiny lanes dict and check the HTML contains a lane
        # for each agent plus the wall-clock total.
        block = viz.StepBlock(
            step_num=3, duration_s=2.5, input_tokens=82000,
            tool="claim_todo", args_text="{}", observation="ok",
            agent="agent_2", attributed_via="inline", log_line=10,
        )
        lanes = {"agent_2": [(0.0, 2.5, block)]}
        html, total = viz._render_swim_lanes(lanes)
        assert "agent_2" in html
        assert "lane-block" in html
        assert "1 steps" in html
        assert total == 2.5

    def test_render_empty_lanes_returns_placeholder(self, viz):
        html, total = viz._render_swim_lanes({})
        assert "swim-empty" in html
        assert total == 0.0
