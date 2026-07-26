"""Replay a cached run log as a synthetic event stream with pacing + narration.

Used by chat_server.py when the user selects ``mas-aviary-replay``. We
parse the saved Run #6 stdout via event_parser.parse_lines, then weave
in extra ``narration`` events between agent transitions and around slow
tool calls so the chat reads at a comfortable pace.

Pacing model
────────────
Each parsed event has a baseline "wall-time it would take in real life"
(see ``_pacing``). The replay scales that by ``1/speed`` and sleeps before
emitting the NEXT event. Slow tools (``generate_volume_mesh``,
``run_su2_solver``, ``run_simulation``, …) get a longer pause and an
explicit loading note so the audience knows the system is working.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from event_parser import Event, parse_lines
from narration import TOOL_DESCRIPTIONS


# Default cached run - Run #6, the canonical replay the design references. Saved
# in-repo under logs/ so replay works without any temp files (the old /tmp
# task-output path was ephemeral and has since been cleaned up). Its saved log
# is truncated just before the terminating DONE line, so replay produces a clean
# seven-stage flow but no final `result` - Live doesn't need one, and real live
# runs emit DONE. For a complete run with a terminal result, point this at
# logs/mdo_f25_claude_run_003.log instead.
DEFAULT_REPLAY_LOG = Path(__file__).resolve().parent.parent / "logs" / "mdo_f25_claude_run_006.log"


# Baseline pacing in seconds per event kind (before /speed scaling).
# These reflect the rough "narrative tempo" the audience needs to follow
# along, not the real wall-clock of the underlying run.
_PACING = {
    "wandb_url":     1.0,
    "agent_start":   4.0,   # let the intro paragraph land before the first call
    "step_start":    0.3,
    "tool_call":     1.6,   # give the description a beat
    "observation":   1.2,
    "step_meta":     0.4,
    "final_answer":  3.0,
    "agent_end":     2.5,
    "narration":     1.5,
    "run_done":      1.0,
}

# Extra delay added after a slow tool's observation lands, so the
# audience absorbs the result of a long-running step.
_SLOW_TOOL_EXTRA = 1.5


def _is_slow_tool(name: str) -> bool:
    return TOOL_DESCRIPTIONS.get(name, ("", ""))[1] == "slow"


async def replay(
    log_path: Path = DEFAULT_REPLAY_LOG,
    speed: float = 2.0,
) -> AsyncIterator[Event]:
    """Yield Events from a saved runner stdout log with narration + pacing.

    Args:
        log_path: file to read.
        speed: playback rate multiplier. 1.0 = the pacing baked into _PACING;
            2.0 = twice as fast; 0 = no delay (instant dump, for tests).
    """
    if not log_path.exists():
        raise FileNotFoundError(f"Replay log not found: {log_path}")

    text = log_path.read_text(errors="replace")
    raw_events = list(parse_lines(iter(text.splitlines())))

    # Track which tool a forthcoming observation belongs to, so we can
    # decide whether to extend its pause.
    pending_tool: str | None = None

    for i, ev in enumerate(raw_events):
        # Inject a "loading" note BEFORE the observation of a slow tool.
        if ev.kind == "tool_call":
            pending_tool = ev.text
            yield ev
            if _is_slow_tool(ev.text) and speed > 0:
                # Tell the audience this will take a beat.
                yield Event(
                    kind="narration",
                    agent=ev.agent,
                    text="  ⏳ *working on it - this is a longer step…*",
                )
                await asyncio.sleep(2.5 / speed)
            else:
                await asyncio.sleep(_PACING.get("tool_call", 0.5) / max(speed, 0.01) if speed > 0 else 0)
            continue

        # After an observation we may want a longer beat if the tool was slow.
        yield ev

        if speed <= 0:
            continue

        delay = _PACING.get(ev.kind, 0.3) / speed

        if ev.kind == "observation" and pending_tool and _is_slow_tool(pending_tool):
            delay += _SLOW_TOOL_EXTRA / speed
            pending_tool = None  # consume

        if ev.kind == "step_meta":
            # Reset pending_tool when the step formally closes.
            pending_tool = None

        if delay > 0:
            await asyncio.sleep(delay)


async def replay_to_stdout() -> None:
    """Smoke-test entry point."""
    import json
    async for ev in replay(speed=0):
        print(json.dumps({
            "kind": ev.kind,
            "agent": ev.agent,
            "step": ev.step,
            "text": (ev.text or "")[:120],
        }))


if __name__ == "__main__":
    asyncio.run(replay_to_stdout())
