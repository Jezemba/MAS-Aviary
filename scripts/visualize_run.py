"""Parse a networked-pipeline log and emit an HTML visualization.

Builds a single self-contained HTML file showing every blackboard
read/write/claim/done event from one networked-strategy pipeline run,
color-coded per peer agent. Usage:

    python scripts/visualize_run.py <log_file> [-o <output.html>]

Defaults to the most recent concurrent_blackboard log if no arg given.

Attribution heuristic (read this before trusting attribution under
concurrent threads): for tool calls whose response message names an
agent (claim_todo / mark_todo_done / mark_todo_failed / write_blackboard
via key encoding), we use that. For read_blackboard / read_todos /
generic tool calls we fall back to the most-recent "New run - agent_X"
banner before the call. Threads can interleave stdout at the line level
so attribution may be wrong for a handful of read events in interleaved
sections; the action and observation are always correct.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


_RUN_BANNER_RE = re.compile(r"New run - (agent_\d+)")
_CALLING_TOOL_RE = re.compile(r"Calling tool: '([^']+)' with arguments: (.+?)(?:\s+│|$)")
_OBSERVATION_LINE_RE = re.compile(r"^Observations:\s*(.+)$")
_STEP_DURATION_RE = re.compile(
    r"\[Step (\d+): Duration ([\d.]+) seconds\|\s*Input tokens:\s*([\d,]+)"
)
_TODO_LINE_RE = re.compile(
    r"\s*-\s+(\S+)\s+status=(\w+)(?:\s+assigned_to=(\S+))?(?:\s+result=(.+?))?$"
)
_AGENT_MENTION_RE = re.compile(r"'(agent_\d+)'")
_AGENT_KEY_PREFIX_RE = re.compile(r"^(agent_\d+)(?:_|$)")

# Tools we surface in the timeline. Everything else (open_cpacs,
# generate_volume_mesh, run_simulation, etc.) is folded into a single
# "discipline work" event so the blackboard story stays readable.
_BLACKBOARD_TOOLS = frozenset({
    "read_blackboard",
    "write_blackboard",
    "read_todos",
    "claim_todo",
    "mark_todo_done",
    "mark_todo_failed",
    "spawn_peer",
    "mark_task_done",
})


@dataclass
class Event:
    """One row on the timeline."""

    index: int  # sequential event index in the log
    agent: str  # attributed agent name
    tool: str  # e.g. "claim_todo"
    args_text: str  # raw arguments text from the log
    observation: str  # response text (first 600 chars)
    success: bool | None = None
    todo_name: str | None = None
    key: str | None = None
    log_line: int = 0  # 1-indexed line in the source log


@dataclass
class StepBlock:
    """One peer ReAct step: tool call + observation + close-marker duration.

    Smolagents emits one Step N block per tool call. The block opens at
    the ━━ Step N ━━ banner and closes at a `[Step N: Duration X.Xs]`
    line. Three peers running concurrently produce three Step N blocks
    that interleave in stdout; this dataclass holds one such block
    after parsing it out of the raw log.
    """

    step_num: int  # smolagents step number within the peer's ReAct loop
    duration_s: float  # wall-clock seconds for this step
    input_tokens: int  # from the [Step N: ... Input tokens: X] close-marker
    tool: str  # the single tool call made during this step
    args_text: str  # raw tool arguments
    observation: str  # tool response text (first 600 chars)
    agent: str  # attributed peer (may be "unknown")
    attributed_via: str  # how attribution was determined: blackboard / fwd-fill / unknown
    log_line: int  # 1-indexed line of the Calling-tool line


@dataclass
class RunSummary:
    """Aggregate facts about the run."""

    log_path: str
    total_events: int = 0
    events_per_agent: Counter = field(default_factory=Counter)
    events_per_tool: Counter = field(default_factory=Counter)
    final_todos: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)
    fuel_reported: str | None = None
    wandb_run_id: str | None = None


def _safe_parse_json_msg(text: str) -> dict | None:
    """Best-effort: pull a JSON-ish dict out of an observation string."""
    if not text:
        return None
    # Many observations wrap their JSON inside other text; find the first {.
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except Exception:
        # Truncated JSON: try greedy raw_decode.
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(text[start:])
            return obj if isinstance(obj, dict) else None
        except Exception:
            return None


def _attribute_agent(
    tool: str,
    args_text: str,
    observation_text: str,
    last_run_banner_agent: str | None,
) -> str:
    """Best-effort attribution. Prefer signals from the observation
    (which names the acting agent for the TODO-API tools), then fall back
    to the most recent 'New run' banner."""
    # 1. TODO-claim tools carry an explicit `attempted_by` field in their
    # JSON response (added 2026-05-25). This is the only reliable signal
    # under concurrent contention — a REJECTED claim_todo's message
    # names the winner, not the caller, so regexing the message would
    # mis-attribute the event to the wrong peer.
    if tool in ("claim_todo", "mark_todo_done", "mark_todo_failed"):
        parsed = _safe_parse_json_msg(observation_text)
        if parsed and isinstance(parsed.get("attempted_by"), str):
            return parsed["attempted_by"]

    # 2. Otherwise fall back to the first 'agent_X' mention in the
    # observation. Works for legacy logs and for tools whose response
    # names the acting agent inline (e.g. read_todos rendering).
    obs_match = _AGENT_MENTION_RE.search(observation_text)
    if obs_match:
        return obs_match.group(1)

    # 3. write_blackboard often encodes the author in the key field.
    if tool == "write_blackboard":
        # args_text looks like: {'key': 'agent_3_status', 'value': '...', ...}
        key_match = re.search(r"'key':\s*'([^']+)'", args_text)
        if key_match:
            prefix_match = _AGENT_KEY_PREFIX_RE.match(key_match.group(1))
            if prefix_match:
                return prefix_match.group(1)

    # 4. Fall back to the most-recent New run banner.
    return last_run_banner_agent or "unknown"


def _extract_observation(lines: list[str], start_idx: int, max_lookahead: int = 8) -> str:
    """Walk forward from start_idx looking for the Observations: line.
    Returns the observation text (joined across continuation lines until
    the next blank line or smolagents step marker)."""
    for i in range(start_idx, min(start_idx + max_lookahead, len(lines))):
        match = _OBSERVATION_LINE_RE.match(lines[i])
        if match:
            parts = [match.group(1).strip()]
            # Continue collecting until a blank line or a smolagents
            # control line.
            j = i + 1
            while j < len(lines) and j < i + 10:
                ln = lines[j].strip()
                if not ln:
                    break
                if ln.startswith("[Step ") or ln.startswith("━") or ln.startswith("╭"):
                    break
                parts.append(ln)
                j += 1
            return " ".join(parts)[:600]
    return ""


def _extract_final_todos(lines: list[str]) -> list[tuple[str, str, str | None, str | None]]:
    """Find the last read_todos response and parse its TODO table out."""
    last_todos_block: list[str] | None = None
    for i, line in enumerate(lines):
        if "Calling tool: 'read_todos'" not in line:
            continue
        # Look for the Observations block.
        for j in range(i + 1, min(i + 6, len(lines))):
            if lines[j].startswith("Observations:"):
                block: list[str] = []
                k = j
                while k < len(lines) and k < j + 30:
                    text = lines[k]
                    # Stop at next step marker.
                    if k > j and (text.startswith("[Step ") or text.startswith("━") or text.startswith("╭")):
                        break
                    block.append(text)
                    k += 1
                last_todos_block = block
                break

    if last_todos_block is None:
        return []

    parsed: list[tuple[str, str, str | None, str | None]] = []
    for line in last_todos_block:
        # The very first TODO row in a read_todos response is on the
        # same line as the "Observations:" prefix because that's how
        # smolagents prints multi-line tool output. Strip the prefix
        # before matching.
        cleaned = line
        if cleaned.startswith("Observations:"):
            cleaned = cleaned[len("Observations:"):]
        match = _TODO_LINE_RE.match(cleaned)
        if match:
            name, status, assigned, result = match.groups()
            parsed.append((name, status, assigned, result))
    return parsed


def parse_log(log_path: str) -> tuple[list[Event], RunSummary]:
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().split("\n")

    events: list[Event] = []
    summary = RunSummary(log_path=log_path)
    last_run_banner: str | None = None

    for line_idx, line in enumerate(lines):
        # Track current agent context from the smolagents banner.
        banner_match = _RUN_BANNER_RE.search(line)
        if banner_match:
            last_run_banner = banner_match.group(1)
            continue

        if "Calling tool:" not in line:
            continue

        call_match = _CALLING_TOOL_RE.search(line)
        if not call_match:
            continue
        tool = call_match.group(1)
        args_text = call_match.group(2).strip()

        # Only surface blackboard / TODO ops on the main timeline.
        if tool not in _BLACKBOARD_TOOLS:
            continue

        observation = _extract_observation(lines, line_idx + 1)
        agent = _attribute_agent(tool, args_text, observation, last_run_banner)

        # Pull a few derived fields out of args / observation for the table.
        success: bool | None = None
        todo_name: str | None = None
        key: str | None = None

        if tool in ("claim_todo", "mark_todo_done", "mark_todo_failed"):
            todo_match = re.search(r"'todo_name':\s*'([^']+)'", args_text)
            todo_name = todo_match.group(1) if todo_match else None
        if tool == "write_blackboard":
            key_match = re.search(r"'key':\s*'([^']+)'", args_text)
            key = key_match.group(1) if key_match else None

        obs_json = _safe_parse_json_msg(observation)
        if isinstance(obs_json, dict) and "success" in obs_json:
            success = bool(obs_json["success"])

        ev = Event(
            index=len(events),
            agent=agent,
            tool=tool,
            args_text=args_text[:300],
            observation=observation,
            success=success,
            todo_name=todo_name,
            key=key,
            log_line=line_idx + 1,
        )
        events.append(ev)
        summary.events_per_agent[agent] += 1
        summary.events_per_tool[tool] += 1

    summary.total_events = len(events)
    summary.final_todos = _extract_final_todos(lines)

    # Fish wandb run id + fuel out of the tail.
    for line in lines:
        if "fuel_burned_kg " in line and "wandb:" in line:
            tokens = line.split()
            for tok in tokens:
                try:
                    val = float(tok)
                    summary.fuel_reported = f"{val:.2f}"
                    break
                except ValueError:
                    pass
        if "wandb: 🚀 View run" in line or "https://wandb.ai/" in line:
            url_match = re.search(r"runs/([a-z0-9]+)", line)
            if url_match:
                summary.wandb_run_id = url_match.group(1)

    return events, summary


# ---------------------------------------------------------------------------
# Swim-lane timeline (parallel peer execution view)
# ---------------------------------------------------------------------------


def parse_step_blocks(log_path: str) -> list[StepBlock]:
    """Extract every (tool call, step number, duration) tuple from a
    networked-run log, and attribute each to a peer.

    Smolagents emits one Step N block per ReAct turn: a tool call (or
    multiple), the observation, then a `[Step N: Duration X.X | Input
    tokens: T]` close-marker. Under concurrent_blackboard mode three
    peers' Step blocks interleave in stdout but each tool call still
    pairs with EXACTLY ONE close-marker — the next one in log order
    after the tool call AND before any newer tool call.

    Attribution strategy:
    1. Tool's response carries `attempted_by` (TODO-claim tools after
       2026-05-25 fix) — used directly.
    2. Tool's response inline-mentions an `'agent_X'` (e.g.
       `write_blackboard` key prefix, `read_todos` rendering).
    3. Token-signature match: for MCP tool calls (no inline
       attribution), pair (step_num, input_tokens) against the inline
       -attributed peers' signatures. Each peer accumulates a
       slightly different token count by step N because their prompt
       contexts differ marginally, so the closest match identifies
       the peer reliably from step 2 onward.
    """
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.read().split("\n")

    # Single-pass walk: maintain a pending tool call until its close-
    # marker fires. If a NEW tool call appears before a close, the
    # previous one was part of a multi-tool step — flush it with
    # duration=0 so we don't lose the call but don't double-count time.
    raw_blocks: list[StepBlock] = []
    pending: dict | None = None

    def _flush_pending_with_close(close_step: int, close_dur: float, close_tokens: int) -> None:
        if pending is None:
            return
        agent = _attribute_agent(
            pending["tool"], pending["args_text"], pending["observation"], None
        )
        raw_blocks.append(
            StepBlock(
                step_num=close_step,
                duration_s=close_dur,
                input_tokens=close_tokens,
                tool=pending["tool"],
                args_text=pending["args_text"][:300],
                observation=pending["observation"],
                agent=agent,
                attributed_via="inline" if agent != "unknown" else "deferred",
                log_line=pending["line_idx"] + 1,
            )
        )

    for line_idx, line in enumerate(lines):
        close_match = _STEP_DURATION_RE.search(line)
        if close_match:
            step_num = int(close_match.group(1))
            duration_s = float(close_match.group(2))
            input_tokens = int(close_match.group(3).replace(",", ""))
            _flush_pending_with_close(step_num, duration_s, input_tokens)
            pending = None
            continue

        call_match = _CALLING_TOOL_RE.search(line)
        if not call_match:
            continue
        tool = call_match.group(1)
        args_text = call_match.group(2).strip()
        observation = _extract_observation(lines, line_idx + 1)

        if pending is not None:
            # A new tool call before any close-marker — the previous
            # one was an earlier tool inside the same step. Record it
            # with zero duration so it still appears on the timeline
            # but doesn't inflate wall-clock.
            agent = _attribute_agent(
                pending["tool"], pending["args_text"], pending["observation"], None
            )
            raw_blocks.append(
                StepBlock(
                    step_num=-1,
                    duration_s=0.0,
                    input_tokens=0,
                    tool=pending["tool"],
                    args_text=pending["args_text"][:300],
                    observation=pending["observation"],
                    agent=agent,
                    attributed_via="inline" if agent != "unknown" else "deferred",
                    log_line=pending["line_idx"] + 1,
                )
            )

        pending = {
            "tool": tool,
            "args_text": args_text,
            "observation": observation,
            "line_idx": line_idx,
        }

    # Second pass: attribute deferred blocks via token-signature
    # matching. Each peer accumulates a slightly different input-token
    # count by step N because their prompt / context strings differ
    # marginally (system-prompt text, tool ordering, etc.). The inline
    # -attributed blocks give us each peer's exact (step_num, tokens)
    # signature; deferred blocks at the same step_num match by minimum
    # token-distance.
    signature: dict[tuple[str, int], int] = {
        (blk.agent, blk.step_num): blk.input_tokens
        for blk in raw_blocks
        if blk.attributed_via == "inline"
    }
    peers_known = sorted({a for (a, _) in signature.keys()})

    for blk in raw_blocks:
        if blk.attributed_via != "deferred":
            continue
        # Candidate signatures at this step number, one per peer.
        candidates = [
            (a, signature[(a, blk.step_num)])
            for a in peers_known
            if (a, blk.step_num) in signature
        ]
        if candidates:
            # Closest token distance wins.
            best_agent, best_dist = None, None
            for agent, sig_tokens in candidates:
                dist = abs(sig_tokens - blk.input_tokens)
                if best_dist is None or dist < best_dist:
                    best_agent, best_dist = agent, dist
            blk.agent = best_agent or "unknown"
            blk.attributed_via = "token-match"
        else:
            # No inline-attributed peer ever ran step `step_num` —
            # fall back to nearest-step token signature across peers.
            all_sigs = [
                (a, s, t) for (a, s), t in signature.items()
            ]
            if all_sigs:
                best_agent, best_score = None, None
                for agent, sig_step, sig_tokens in all_sigs:
                    if sig_step == blk.step_num:
                        continue
                    # Score combines step distance and token distance,
                    # with token distance weighted heavily.
                    score = (
                        abs(sig_step - blk.step_num) * 1000
                        + abs(sig_tokens - blk.input_tokens)
                    )
                    if best_score is None or score < best_score:
                        best_agent, best_score = agent, score
                blk.agent = best_agent or "unknown"
                blk.attributed_via = "token-nearest"
            else:
                blk.agent = "unknown"
                blk.attributed_via = "unknown"

    return raw_blocks


def build_swim_lanes(
    blocks: list[StepBlock],
) -> dict[str, list[tuple[float, float, StepBlock]]]:
    """Convert StepBlocks into per-peer (start_s, duration_s, block)
    tuples for the swim-lane visualization.

    Each peer's events are positioned by cumulative step duration so
    blocks early in the log appear left, later blocks appear right.
    The wall-clock-end of the run is max over peers."""
    # Skip the synthetic step_num=-1 / duration=0 multi-tool flush
    # entries — those have no wall-clock to plot.
    plottable = [b for b in blocks if b.step_num > 0 and b.duration_s > 0]

    per_peer: dict[str, list[StepBlock]] = {}
    for blk in plottable:
        per_peer.setdefault(blk.agent, []).append(blk)

    lanes: dict[str, list[tuple[float, float, StepBlock]]] = {}
    for agent, peer_blocks in per_peer.items():
        # Sort by step number so cumulative time is monotonic.
        peer_blocks.sort(key=lambda b: (b.step_num, b.log_line))
        cumulative = 0.0
        entries: list[tuple[float, float, StepBlock]] = []
        last_step_seen = 0
        for blk in peer_blocks:
            # Detect gaps in step numbering (rare with forward-fill)
            # and treat them as zero-duration jumps so the cumulative
            # axis doesn't lie about elapsed time.
            if blk.step_num > last_step_seen + 1:
                # gap — keep cumulative as is; the missing step's time
                # is unknown and we don't synthesize it.
                pass
            entries.append((cumulative, blk.duration_s, blk))
            cumulative += blk.duration_s
            last_step_seen = blk.step_num
        lanes[agent] = entries
    return lanes


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_AGENT_COLORS = [
    ("agent_1", "#4F8EF7"),  # blue
    ("agent_2", "#34C76A"),  # green
    ("agent_3", "#F2994A"),  # orange
    ("agent_4", "#9B51E0"),  # purple
    ("agent_5", "#EB5757"),  # red
    ("agent_6", "#2D9CDB"),  # cyan
    ("agent_7", "#6FCF97"),  # mint
    ("unknown", "#828282"),  # gray
]


_TOOL_LABELS = {
    "read_blackboard": ("READ", "📖"),
    "read_todos": ("READ-TODOS", "📋"),
    "write_blackboard": ("WRITE", "✏️"),
    "claim_todo": ("CLAIM", "🎯"),
    "mark_todo_done": ("DONE", "✅"),
    "mark_todo_failed": ("FAILED", "❌"),
    "spawn_peer": ("SPAWN", "👤"),
    "mark_task_done": ("TASK-DONE", "🏁"),
}


def _agent_color(name: str) -> str:
    for n, c in _AGENT_COLORS:
        if n == name:
            return c
    return "#444444"


def _esc(s: str) -> str:
    return html_lib.escape(s, quote=True)


def _render_swim_lanes(
    lanes: dict[str, list[tuple[float, float, StepBlock]]],
) -> tuple[str, float]:
    """Build the HTML for the swim-lane panel. Returns (html, total_run_seconds)."""
    if not lanes:
        return ('<div class="swim-empty">no step-duration data parsed</div>', 0.0)

    # Compute total wall-clock from max peer cumulative time.
    total = max(
        (entries[-1][0] + entries[-1][1]) if entries else 0.0
        for entries in lanes.values()
    )
    total = max(total, 0.1)  # avoid div-by-zero

    # Agents in canonical order so the colors line up with the rest of the page.
    ordered_agents = [
        a for a, _ in _AGENT_COLORS if a in lanes and a != "unknown"
    ]
    if "unknown" in lanes:
        ordered_agents.append("unknown")
    for a in lanes:
        if a not in ordered_agents:
            ordered_agents.append(a)

    # Axis ticks at every ~total/6 seconds, rounded.
    def _tick_marks(total_s: float) -> str:
        step = max(round(total_s / 6.0), 1)
        ticks: list[str] = []
        t = 0
        while t <= total_s + step - 1:
            x_pct = min(100.0, 100.0 * t / total_s)
            ticks.append(
                f'<div class="lane-tick" style="left:{x_pct:.2f}%">'
                f'<span class="lane-tick-label">{t}s</span></div>'
            )
            t += step
        return "".join(ticks)

    lane_rows: list[str] = []
    for agent in ordered_agents:
        entries = lanes[agent]
        color = _agent_color(agent)
        blocks: list[str] = []
        for start_s, dur_s, blk in entries:
            left_pct = 100.0 * start_s / total
            width_pct = max(100.0 * dur_s / total, 0.3)  # min visible width
            label, icon = _TOOL_LABELS.get(blk.tool, (blk.tool.upper(), "•"))
            obs_short = blk.observation[:240]
            if len(blk.observation) > 240:
                obs_short += "…"
            tooltip = (
                f"step {blk.step_num}  ·  {dur_s:.2f}s  ·  {blk.tool}"
                f"  ·  {blk.attributed_via}\n{obs_short}"
            )
            blocks.append(
                f'<div class="lane-block" style="left:{left_pct:.3f}%; '
                f'width:{width_pct:.3f}%; --color:{color}" '
                f'title="{_esc(tooltip)}">'
                f'<span class="lane-block-icon">{icon}</span></div>'
            )
        total_peer_s = entries[-1][0] + entries[-1][1] if entries else 0.0
        lane_rows.append(
            f'<div class="lane">'
            f'<div class="lane-label" style="color:{color}">{_esc(agent)}'
            f'<span class="lane-summary">{len(entries)} steps · '
            f'{total_peer_s:.1f}s</span></div>'
            f'<div class="lane-track">{"".join(blocks)}</div>'
            f'</div>'
        )

    return (
        '<div class="swim-lanes-panel">'
        '<div class="lane-axis">' + _tick_marks(total) + '</div>'
        + "".join(lane_rows)
        + '</div>',
        total,
    )


def render_html(
    events: list[Event],
    summary: RunSummary,
    lanes: dict[str, list[tuple[float, float, StepBlock]]],
    source_log: str,
) -> str:
    agents_in_run = sorted(summary.events_per_agent.keys())
    swim_lanes_html, run_total_s = _render_swim_lanes(lanes)

    # Top stats per agent
    agent_legend_rows = "".join(
        f'<span class="legend-chip" style="--color: {_agent_color(a)}">'
        f"{_esc(a)} ({summary.events_per_agent[a]})</span>"
        for a in agents_in_run
    )

    # Final TODO board.
    if summary.final_todos:
        todo_rows = "".join(
            f'<tr class="todo-row todo-{_esc(status)}">'
            f"<td>{_esc(name)}</td>"
            f"<td><span class=\"status-pill status-{_esc(status)}\">{_esc(status)}</span></td>"
            f'<td style="color: {_agent_color(assigned or "unknown")}">{_esc(assigned or "—")}</td>'
            f"<td>{_esc((result or '')[:90])}</td>"
            "</tr>"
            for name, status, assigned, result in summary.final_todos
        )
    else:
        todo_rows = '<tr><td colspan="4" style="text-align:center;color:#888">no TODO board snapshot found in log</td></tr>'

    # Tool usage breakdown.
    tool_breakdown_rows = "".join(
        f'<tr><td>{_esc(tool)}</td><td>{count}</td></tr>'
        for tool, count in summary.events_per_tool.most_common()
    )

    # Timeline rows.
    timeline_items: list[str] = []
    for ev in events:
        label, icon = _TOOL_LABELS.get(ev.tool, (ev.tool.upper(), "•"))
        agent_color = _agent_color(ev.agent)

        # Build a compact subtitle showing the relevant arg.
        sub_bits: list[str] = []
        if ev.todo_name:
            sub_bits.append(f"<code>{_esc(ev.todo_name)}</code>")
        if ev.key:
            sub_bits.append(f"<code>{_esc(ev.key)}</code>")
        if ev.success is True:
            sub_bits.append('<span class="ok">✓ success</span>')
        elif ev.success is False:
            sub_bits.append('<span class="fail">✗ rejected</span>')
        subtitle = " · ".join(sub_bits) if sub_bits else ""

        observation_short = ev.observation[:240]
        if len(ev.observation) > 240:
            observation_short += "…"

        timeline_items.append(
            f'<li class="evt evt-{_esc(ev.tool)}" style="--agent: {agent_color}">'
            f'<div class="evt-bar"></div>'
            f'<div class="evt-meta"><span class="evt-idx">#{ev.index + 1:03d}</span>'
            f'<span class="evt-agent" style="color:{agent_color}">{_esc(ev.agent)}</span>'
            f'<span class="evt-label">{icon} {_esc(label)}</span></div>'
            f'<div class="evt-sub">{subtitle}</div>'
            f'<div class="evt-obs">{_esc(observation_short) or "<em>(no observation captured)</em>"}</div>'
            f'<div class="evt-loc">log line {ev.log_line}</div>'
            "</li>"
        )

    timeline_html = "\n".join(timeline_items)

    wandb_link = (
        f'<a class="wandb-link" href="https://wandb.ai/jessicae/mas-aviary-stat/runs/'
        f'{summary.wandb_run_id}" target="_blank">wandb · {_esc(summary.wandb_run_id)}</a>'
        if summary.wandb_run_id
        else ""
    )
    fuel_chip = (
        f'<span class="metric-chip">fuel_burned_kg: <b>{_esc(summary.fuel_reported)}</b></span>'
        if summary.fuel_reported
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Networked run · blackboard activity · MAS-Aviary</title>
<style>
  :root {{
    --bg: #0f1115;
    --panel: #1a1d24;
    --panel-2: #232730;
    --border: #2c3038;
    --text: #e5e7eb;
    --text-dim: #9aa0aa;
    --accent: #4F8EF7;
    --ok: #34C76A;
    --fail: #EB5757;
  }}
  * {{ box-sizing: border-box }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    margin: 0; background: var(--bg); color: var(--text);
    font-size: 14px; line-height: 1.45;
  }}
  header {{
    padding: 28px 36px 16px; border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #161922 0%, var(--bg) 100%);
  }}
  header h1 {{ margin: 0 0 6px; font-size: 22px; font-weight: 600 }}
  header .subtitle {{ color: var(--text-dim); font-size: 13px; margin-bottom: 14px }}
  header .meta-row {{ display: flex; gap: 14px; flex-wrap: wrap; align-items: center }}
  .metric-chip, .wandb-link, .legend-chip {{
    background: var(--panel); border: 1px solid var(--border);
    padding: 6px 12px; border-radius: 999px; font-size: 12px;
  }}
  .metric-chip b {{ color: var(--accent) }}
  .wandb-link {{ color: var(--accent); text-decoration: none }}
  .wandb-link:hover {{ text-decoration: underline }}
  .legend-chip {{
    display: inline-flex; align-items: center; gap: 6px;
    border-color: var(--color);
  }}
  .legend-chip::before {{
    content: ""; display: inline-block; width: 8px; height: 8px;
    border-radius: 50%; background: var(--color);
  }}
  main {{
    display: grid; grid-template-columns: 380px 1fr;
    gap: 24px; padding: 24px 36px 60px;
  }}
  .panel {{
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px;
  }}
  .panel h2 {{
    margin: 0 0 14px; font-size: 13px; font-weight: 600;
    color: var(--text-dim); text-transform: uppercase;
    letter-spacing: 0.6px;
  }}
  /* TODO board */
  table.todo-board {{
    width: 100%; border-collapse: collapse; font-size: 13px;
  }}
  table.todo-board th, table.todo-board td {{
    text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border);
  }}
  table.todo-board th {{ color: var(--text-dim); font-weight: 500; font-size: 11px }}
  .status-pill {{
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 500;
  }}
  .status-done    {{ background: rgba(52,199,106,.18); color: #6FE39A }}
  .status-claimed {{ background: rgba(242,153,74,.22); color: #FFB870 }}
  .status-pending {{ background: rgba(120,120,120,.25); color: #c8c8c8 }}
  .status-failed  {{ background: rgba(235,87,87,.22); color: #FF7E7E }}
  /* Tool breakdown */
  table.tools {{ width: 100%; border-collapse: collapse; font-size: 13px }}
  table.tools td {{ padding: 4px 8px }}
  table.tools td:last-child {{ text-align: right; color: var(--accent) }}
  /* Timeline */
  ul.timeline {{
    list-style: none; margin: 0; padding: 0; position: relative;
    counter-reset: tl;
  }}
  ul.timeline::before {{
    content: ""; position: absolute; top: 0; bottom: 0; left: 2px;
    width: 2px; background: var(--border);
  }}
  li.evt {{
    position: relative; padding: 10px 0 12px 24px;
    border-bottom: 1px solid transparent;
  }}
  li.evt + li.evt {{ border-top: 1px solid var(--panel-2) }}
  .evt-bar {{
    position: absolute; left: -4px; top: 17px;
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--agent); box-shadow: 0 0 0 3px var(--bg);
  }}
  .evt-meta {{
    display: flex; gap: 12px; align-items: baseline; font-size: 13px;
  }}
  .evt-idx {{ color: var(--text-dim); font-family: ui-monospace, monospace; font-size: 11px }}
  .evt-agent {{ font-weight: 600 }}
  .evt-label {{ background: var(--panel-2); padding: 1px 8px; border-radius: 4px; font-size: 11px; letter-spacing: 0.4px }}
  .evt-sub  {{ font-size: 12px; color: var(--text-dim); margin-top: 4px }}
  .evt-sub code {{ background: var(--panel-2); padding: 1px 6px; border-radius: 4px; font-size: 11px }}
  .evt-sub .ok   {{ color: var(--ok); font-weight: 500 }}
  .evt-sub .fail {{ color: var(--fail); font-weight: 500 }}
  .evt-obs {{
    margin-top: 6px; padding: 8px 10px; background: var(--panel-2);
    border-radius: 6px; font-size: 12px; color: var(--text-dim);
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    white-space: pre-wrap; word-break: break-word;
  }}
  .evt-loc {{ font-size: 10px; color: #555; margin-top: 4px }}
  /* Swim-lanes panel (parallel-execution view) */
  .swim-lanes-panel {{
    margin: 18px 36px 0; background: var(--panel);
    border: 1px solid var(--border); border-radius: 12px;
    padding: 20px 22px 14px;
  }}
  .swim-lanes-panel::before {{
    content: "Parallel execution · per-peer swim lanes";
    display: block; font-size: 11px; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.7px;
    margin-bottom: 12px;
  }}
  .lane-axis {{
    position: relative; height: 16px; margin-left: 110px;
    margin-bottom: 6px; border-bottom: 1px solid var(--border);
  }}
  .lane-tick {{
    position: absolute; top: 0; height: 100%;
    border-left: 1px dashed var(--panel-2);
  }}
  .lane-tick-label {{
    position: absolute; top: -2px; left: 4px;
    font-size: 10px; color: var(--text-dim);
    font-family: ui-monospace, monospace;
  }}
  .lane {{
    display: flex; align-items: center; gap: 12px;
    margin: 6px 0;
  }}
  .lane-label {{
    width: 98px; flex-shrink: 0; font-weight: 600;
    font-size: 13px; display: flex; flex-direction: column;
    line-height: 1.2;
  }}
  .lane-summary {{
    font-size: 10px; font-weight: 400; color: var(--text-dim);
    font-family: ui-monospace, monospace; margin-top: 2px;
  }}
  .lane-track {{
    flex: 1; position: relative; height: 26px;
    background: rgba(255,255,255,.03); border-radius: 4px;
    overflow: hidden;
  }}
  .lane-block {{
    position: absolute; top: 3px; bottom: 3px;
    background: var(--color); border-radius: 3px;
    min-width: 2px; display: flex; align-items: center;
    justify-content: center; cursor: help;
    box-shadow: inset 0 0 0 1px rgba(0,0,0,.18);
    transition: filter 0.15s ease;
  }}
  .lane-block:hover {{ filter: brightness(1.25) }}
  .lane-block-icon {{
    font-size: 10px; line-height: 1; opacity: 0.92;
    pointer-events: none;
  }}
  .swim-empty {{
    margin: 18px 36px 0; padding: 14px 18px; background: var(--panel);
    border: 1px solid var(--border); border-radius: 12px;
    color: var(--text-dim); font-size: 12px;
  }}
  /* Filter controls */
  .filters {{
    margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border);
  }}
  .filters label {{ display: block; margin: 4px 0; font-size: 13px; cursor: pointer }}
  .filters input {{ margin-right: 6px }}
  footer {{
    color: var(--text-dim); font-size: 11px; text-align: center;
    padding: 16px 36px 28px;
  }}
</style>
</head>
<body>
<header>
  <h1>Networked run · blackboard activity</h1>
  <div class="subtitle">
    Source: <code>{_esc(source_log)}</code> · {summary.total_events} blackboard events recorded · {run_total_s:.1f}s wall-clock
  </div>
  <div class="meta-row">
    {wandb_link}
    {fuel_chip}
    {agent_legend_rows}
  </div>
</header>

{swim_lanes_html}

<main>
  <aside>
    <div class="panel">
      <h2>Final TODO board</h2>
      <table class="todo-board">
        <thead>
          <tr>
            <th>TODO</th><th>Status</th><th>Assigned</th><th>Result</th>
          </tr>
        </thead>
        <tbody>
          {todo_rows}
        </tbody>
      </table>
    </div>

    <div class="panel" style="margin-top: 18px">
      <h2>Tool usage</h2>
      <table class="tools">
        <tbody>
          {tool_breakdown_rows}
        </tbody>
      </table>
      <div class="filters">
        <h2 style="margin-top: 4px">Filter timeline</h2>
        <label><input type="checkbox" class="agent-filter" data-agent="all" checked>Show all agents</label>
        {"".join(
            f'<label><input type="checkbox" class="agent-filter" data-agent="{_esc(a)}" checked>'
            f'<span style="color:{_agent_color(a)}">●</span> {_esc(a)}</label>'
            for a in agents_in_run
        )}
      </div>
    </div>
  </aside>

  <section>
    <div class="panel">
      <h2>Timeline (chronological)</h2>
      <ul class="timeline">
        {timeline_html}
      </ul>
    </div>
  </section>
</main>

<footer>
  Generated by <code>scripts/visualize_run.py</code> · MAS-Aviary networked
  concurrent_blackboard mode (arxiv 2510.18893 CodeCRDT pattern)
</footer>

<script>
  // Per-agent timeline filter. "Show all" master toggle + per-agent checkboxes.
  const master = document.querySelector('.agent-filter[data-agent="all"]');
  const perAgent = Array.from(document.querySelectorAll('.agent-filter:not([data-agent="all"])'));
  function apply() {{
    const showAll = master.checked;
    const allowed = new Set(
      perAgent.filter(cb => cb.checked).map(cb => cb.dataset.agent)
    );
    document.querySelectorAll('li.evt').forEach(el => {{
      const agentSpan = el.querySelector('.evt-agent');
      const agent = agentSpan ? agentSpan.textContent : '';
      el.style.display = (showAll || allowed.has(agent)) ? '' : 'none';
    }});
  }}
  master.addEventListener('change', () => {{
    perAgent.forEach(cb => {{ cb.checked = master.checked; }});
    apply();
  }});
  perAgent.forEach(cb => cb.addEventListener('change', () => {{
    master.checked = perAgent.every(c => c.checked);
    apply();
  }}));
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "log",
        nargs="?",
        default="/tmp/pipeline_mdo_f25_networked_concurrent_v2.log",
        help="Path to the pipeline log file.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output HTML path (default: viz/run_<wandb-id>.html next to repo root).",
    )
    args = parser.parse_args()

    log_path = args.log
    if not os.path.isfile(log_path):
        print(f"log file not found: {log_path}")
        return 2

    events, summary = parse_log(log_path)
    step_blocks = parse_step_blocks(log_path)
    lanes = build_swim_lanes(step_blocks)
    html = render_html(
        events, summary, lanes, source_log=os.path.basename(log_path)
    )

    if args.output:
        out_path = args.output
    else:
        repo_root = Path(__file__).resolve().parent.parent
        out_dir = repo_root / "viz"
        out_dir.mkdir(exist_ok=True)
        run_id = summary.wandb_run_id or "run"
        out_path = str(out_dir / f"run_{run_id}.html")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Parsed {summary.total_events} blackboard events from {log_path}")
    print(f"Agents: {sorted(summary.events_per_agent.keys())}")
    print(f"Final TODOs: {len(summary.final_todos)} entries")
    print(f"Wrote HTML to {out_path}")
    print(f"Open with: xdg-open '{out_path}'  (or paste into a browser)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
