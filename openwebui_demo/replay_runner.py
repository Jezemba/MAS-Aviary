"""Replay a cached run log (e.g. run #6's stdout) as a synthetic event stream.

Used by chat_server.py when the user selects the ``mas-aviary-replay``
model. Yields parsed Event objects with optional artificial delays so
the audience can see the pipeline unfold at a reasonable pace without
the ~10-minute cost of a real run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from event_parser import Event, parse_lines


# Default cached run — the successful run #6 from 2026-05-11.
DEFAULT_REPLAY_LOG = Path(
    "/tmp/claude-1000/-home-aipexws3-Jessica/c51a1a43-21f4-42a2-a966-"
    "36744abee873/tasks/bgzuxc13v.output"
)


async def replay(
    log_path: Path = DEFAULT_REPLAY_LOG,
    speed: float = 10.0,
) -> AsyncIterator[Event]:
    """Yield Events from a saved runner stdout log.

    Args:
        log_path: file to read. Each line should be one stdout line from
            a previous stat_batch_runner.py run.
        speed: playback rate multiplier. The replay applies a small delay
            between SIGNIFICANT events (agent_start, step_meta,
            final_answer) so the stream feels paced, not instantaneous.
            A speed of 1.0 = roughly realtime; 10.0 = 10x faster; 0 =
            no delay (instant dump for testing).
    """
    if not log_path.exists():
        raise FileNotFoundError(f"Replay log not found: {log_path}")

    # Read all lines first; parser is synchronous.
    text = log_path.read_text(errors="replace")
    lines = text.splitlines()

    # Map event kinds → approximate "real" wall-time for pacing.
    pacing = {
        "agent_start":   1.5,
        "step_start":    0.3,
        "tool_call":     0.4,
        "observation":   0.4,
        "step_meta":     0.1,
        "final_answer":  2.0,
        "agent_end":     0.5,
        "wandb_url":     0.1,
        "run_done":      0.0,
    }

    for ev in parse_lines(iter(lines)):
        yield ev
        if speed > 0:
            delay = pacing.get(ev.kind, 0.1) / speed
            if delay > 0:
                await asyncio.sleep(delay)


async def replay_to_stdout() -> None:
    """Smoke-test entry point: dump parsed events as JSON-ish lines."""
    import json
    async for ev in replay(speed=0):
        print(json.dumps({
            "kind": ev.kind,
            "agent": ev.agent,
            "step": ev.step,
            "text": ev.text[:120],
        }))


if __name__ == "__main__":
    asyncio.run(replay_to_stdout())
