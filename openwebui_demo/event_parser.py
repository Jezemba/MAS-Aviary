"""Parse stat_batch_runner.py stdout into structured agent events.

Used by both the live runner (parses lines as they arrive on stdout) and
the replay runner (reads a saved log line-by-line). The output is a
sequence of ``Event`` objects that the formatter turns into chat-ready
markdown.

The runner's stdout is human-readable and has a stable shape:

    ╭──────────── New run - geometry_engineer ──────────╮
    │ ...task brief... │
    ╰─ LiteLLMModel - anthropic/claude-sonnet-4-... ────╯
    ━━━━━━━━━━━━━━━ Step 1 ━━━━━━━━━━━━━━━
    ╭────────────────────────────────────────────────────╮
    │ Calling tool: 'open_cpacs' with arguments: {...}   │
    ╰────────────────────────────────────────────────────╯
    Observations: {"session_id": "..."}
    [Step 1: Duration 2.87 seconds| Input tokens: 5,260 | Output tokens: 87]
    ...
    Final answer: DESIGN_STATE: ...
    DONE: 1 completed, 0 failed out of 1

The parser is intentionally a state machine that consumes one line at a
time so the same code drives both live tailing and replay.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterator, Literal, Optional

EventKind = Literal[
    "run_start",        # batch run kicked off
    "wandb_url",        # link to the wandb dashboard
    "agent_start",      # new agent invocation
    "step_start",       # new ReAct step within an agent
    "tool_call",        # tool invocation by the LLM
    "observation",      # tool response
    "step_meta",        # duration / token counts after a step
    "final_answer",     # agent's final answer block
    "agent_end",        # implicit when the next agent_start fires
    "run_done",         # "DONE: 1 completed, 0 failed"
    "error",            # explicit error/traceback
    # Synthetic events injected by runners (replay / live) for pacing
    # and narration. Not produced by parse_lines itself.
    "narration",        # plain-text buffer line ("Now passing to X…")
    "pause",            # consumer hint to pause for ``duration_s`` seconds
]


@dataclass
class Event:
    kind: EventKind
    agent: Optional[str] = None
    step: Optional[int] = None
    text: str = ""
    data: dict = field(default_factory=dict)


# Regexes for the structural cues in the runner stdout.
_AGENT_RE = re.compile(r"New run - ([a-z_]+)")
_STEP_RE = re.compile(r"━+ Step (\d+) ━+")
_TOOL_RE = re.compile(r"Calling tool: '([^']+)' with arguments: (.+?)(?:│|$)")
_TOOL_CONT_RE = re.compile(r"^│\s*(.+?)\s*│\s*$")
_STEP_META_RE = re.compile(
    r"\[Step (\d+): Duration ([\d.]+) seconds?\|"
    r" Input tokens: ([\d,]+) \| Output tokens: ([\d,]+)\]"
)
_WANDB_RE = re.compile(r"View run at (https://wandb\.ai/\S+)")
_DONE_RE = re.compile(r"^DONE: (\d+) completed, (\d+) failed")
_TRACE_RE = re.compile(r"^Traceback \(")


class _StreamState:
    """Track state across lines so multi-line tool args / observations collapse
    back into single events."""

    def __init__(self) -> None:
        self.agent: Optional[str] = None
        self.step: int = 0
        # Buffered multi-line tool call args (the runner wraps long args
        # across several │-bordered lines).
        self._tool_name: Optional[str] = None
        self._tool_args_buf: list[str] = []
        self._in_tool_panel: bool = False
        # Buffered multi-line observations (truncated at a sensible cap).
        self._obs_buf: list[str] = []
        self._in_obs: bool = False
        # Buffered final_answer.
        self._final_buf: list[str] = []
        self._in_final: bool = False


def _flush_tool(state: _StreamState) -> Optional[Event]:
    if state._tool_name is None:
        return None
    args_str = " ".join(state._tool_args_buf).strip()
    parsed_args: dict = {}
    try:
        if args_str.startswith("{"):
            # Most tool args are dict-like; some may be JSON strings.
            parsed_args = _safe_parse_dict(args_str)
    except Exception:  # noqa: BLE001 - parser robustness > completeness
        parsed_args = {"_raw": args_str}
    ev = Event(
        kind="tool_call",
        agent=state.agent,
        step=state.step,
        text=state._tool_name,
        data={"arguments": parsed_args, "raw_args": args_str[:1200]},
    )
    state._tool_name = None
    state._tool_args_buf = []
    state._in_tool_panel = False
    return ev


def _flush_obs(state: _StreamState) -> Optional[Event]:
    if not state._obs_buf:
        return None
    raw = " ".join(state._obs_buf).strip()
    # Try to parse as JSON for a structured display; fall back to raw.
    obs_data: dict = {}
    try:
        if raw.startswith("{"):
            obs_data = _safe_parse_dict(raw)
    except Exception:  # noqa: BLE001
        pass
    ev = Event(
        kind="observation",
        agent=state.agent,
        step=state.step,
        text=raw[:4096],
        data={"parsed": obs_data},
    )
    state._obs_buf = []
    state._in_obs = False
    return ev


def _flush_final(state: _StreamState) -> Optional[Event]:
    if not state._final_buf:
        return None
    raw = "\n".join(state._final_buf).strip()
    ev = Event(
        kind="final_answer",
        agent=state.agent,
        text=raw,
    )
    state._final_buf = []
    state._in_final = False
    return ev


def _safe_parse_dict(s: str) -> dict:
    """The runner sometimes emits Python-repr ('{'a': 1}') rather than JSON,
    AND smolagents' Rich renderer replaces ``[`` with ``|`` in its
    observation pretty-printing (closing ``]`` is preserved). Try JSON
    first, then a literal_eval pass, then a JSON pass with ``|`` → ``[``
    swapped back. That last attempt is what actually recovers list
    parsing for tool responses that contain arrays."""
    for variant in (s, s.replace("|", "[")):
        try:
            return json.loads(variant)
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            import ast
            return ast.literal_eval(variant)
        except (ValueError, SyntaxError):
            pass
    return {"_raw": s[:600]}


def parse_lines(lines: Iterator[str]) -> Iterator[Event]:
    """Stream-parse runner stdout into Event objects.

    ``lines`` is any iterator of strings (without trailing newlines is fine).
    Yields ``Event``\\ s in order. Buffered states (tool args, observations,
    final answers) are flushed when the next structural cue arrives.
    """
    state = _StreamState()

    def maybe_flush_before_new_section() -> Iterator[Event]:
        # Any pending tool/observation/final should be emitted before we
        # transition into a new section.
        ev = _flush_tool(state)
        if ev:
            yield ev
        ev = _flush_obs(state)
        if ev:
            yield ev
        ev = _flush_final(state)
        if ev:
            yield ev

    for raw in lines:
        line = raw.rstrip("\n")
        # wandb URL
        m = _WANDB_RE.search(line)
        if m:
            yield Event(kind="wandb_url", text=m.group(1))
            continue

        # DONE summary
        m = _DONE_RE.search(line)
        if m:
            yield from maybe_flush_before_new_section()
            yield Event(
                kind="run_done",
                text=line,
                data={"completed": int(m.group(1)), "failed": int(m.group(2))},
            )
            continue

        # Traceback start
        if _TRACE_RE.match(line):
            yield Event(kind="error", agent=state.agent, text=line)
            continue

        # New agent invocation
        m = _AGENT_RE.search(line)
        if m:
            yield from maybe_flush_before_new_section()
            if state.agent is not None and state.agent != m.group(1):
                yield Event(kind="agent_end", agent=state.agent)
            state.agent = m.group(1)
            state.step = 0
            yield Event(kind="agent_start", agent=state.agent)
            continue

        # New step
        m = _STEP_RE.search(line)
        if m:
            yield from maybe_flush_before_new_section()
            state.step = int(m.group(1))
            yield Event(kind="step_start", agent=state.agent, step=state.step)
            continue

        # Step meta (duration / tokens)
        m = _STEP_META_RE.search(line)
        if m:
            yield from maybe_flush_before_new_section()
            yield Event(
                kind="step_meta",
                agent=state.agent,
                step=int(m.group(1)),
                text=line,
                data={
                    "duration_s": float(m.group(2)),
                    "input_tokens": int(m.group(3).replace(",", "")),
                    "output_tokens": int(m.group(4).replace(",", "")),
                },
            )
            continue

        # Tool call panel — first line has "Calling tool: '...' with arguments:"
        if "Calling tool:" in line:
            yield from maybe_flush_before_new_section()
            m = _TOOL_RE.search(line)
            if m:
                state._tool_name = m.group(1)
                tail = m.group(2).strip().rstrip("│").strip()
                state._tool_args_buf = [tail] if tail else []
                state._in_tool_panel = True
            continue

        # Inside a tool-call panel — args may wrap across multiple lines until ╰
        if state._in_tool_panel:
            if line.startswith("╰"):
                ev = _flush_tool(state)
                if ev:
                    yield ev
                continue
            m = _TOOL_CONT_RE.match(line)
            if m:
                state._tool_args_buf.append(m.group(1))
            continue

        # Observation
        if line.startswith("Observations:"):
            yield from maybe_flush_before_new_section()
            state._in_obs = True
            state._obs_buf = [line[len("Observations:"):].strip()]
            continue

        # Inside an observation — continue until step meta / next section
        if state._in_obs:
            if (
                _STEP_META_RE.search(line)
                or _STEP_RE.search(line)
                or _AGENT_RE.search(line)
                or line.startswith("Final answer:")
            ):
                ev = _flush_obs(state)
                if ev:
                    yield ev
                # Don't consume this line — fall through to re-handle it.
                # Rebuild via a stashed line.
                # (Simple way: reprocess this single line via inner loop.)
                for inner in parse_lines(iter([raw])):
                    yield inner
                continue
            state._obs_buf.append(line)
            continue

        # Final answer
        if line.startswith("Final answer:"):
            yield from maybe_flush_before_new_section()
            state._in_final = True
            state._final_buf = [line[len("Final answer:"):].strip()]
            continue

        if state._in_final:
            # Final answers in the runner end when a new section begins.
            if (
                _AGENT_RE.search(line)
                or _STEP_RE.search(line)
                or _DONE_RE.search(line)
            ):
                ev = _flush_final(state)
                if ev:
                    yield ev
                # Reprocess this line.
                for inner in parse_lines(iter([raw])):
                    yield inner
                continue
            state._final_buf.append(line)
            continue

    # End of stream — flush any pending buffers.
    yield from maybe_flush_before_new_section()
    if state.agent is not None:
        yield Event(kind="agent_end", agent=state.agent)
