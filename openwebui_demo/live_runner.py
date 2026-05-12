"""Run the real MAS-Aviary pipeline and stream events as they arrive.

Spawns ``run_batch.sh`` as a subprocess, tails its stdout line-by-line,
and yields parsed Event objects in order.  The subprocess writes to a
tempfile so the tail logic is straightforward (an unbounded pipe could
deadlock when output is buffered).
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import AsyncIterator

from event_parser import Event, parse_lines


REPO_ROOT = Path(__file__).resolve().parent.parent  # MAS-Aviary
RUN_BATCH_SH = REPO_ROOT / "run_batch.sh"

DEFAULT_ARGS = [
    "--config", "config/mdo_f25_run_claude.yaml",
    "--combinations", "mdo_f25_sequential_iterative_feedback",
    "--repeats", "1",
    "--timeout", "60",
]


async def live() -> AsyncIterator[Event]:
    """Spawn the runner and stream Events from its stdout.

    Yields Events as the subprocess produces output. If the subprocess
    exits or the consumer is cancelled, the child process group is
    SIGTERM'd to release the MCP server connections.
    """
    if not RUN_BATCH_SH.exists():
        raise FileNotFoundError(f"run_batch.sh not found at {RUN_BATCH_SH}")

    # Tee the output to a tempfile too so the parser can re-handle
    # the recursive "reprocess this line" path safely. Actually the
    # parser already supports streaming a single line at a time — the
    # only quirk is its internal recursion when a buffered observation
    # is flushed mid-line; that recursion takes an iter([raw]) and is
    # fine here.
    proc = await asyncio.create_subprocess_exec(
        "/bin/bash",
        str(RUN_BATCH_SH),
        *DEFAULT_ARGS,
        cwd=str(REPO_ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # so we can kill the whole group on cancel
    )

    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4096)

    async def pump_stdout() -> None:
        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    await queue.put(line.decode("utf-8", errors="replace"))
                except asyncio.CancelledError:
                    break
        finally:
            await queue.put(None)  # sentinel

    pump_task = asyncio.create_task(pump_stdout())

    # Adapter: turn the async queue into a sync iterator for parse_lines.
    # parse_lines is a generator over an Iterator[str], so we read all
    # buffered lines synchronously between yields.
    pending: list[str] = []
    parser_iter = None

    def drain_pending() -> str | None:
        return pending.pop(0) if pending else None

    def sync_line_iter():
        while True:
            line = drain_pending()
            if line is None:
                return  # exhausted current batch; parser yields control
            yield line

    try:
        while True:
            # Block until at least one line or sentinel arrives.
            item = await queue.get()
            if item is None:
                # No more lines coming. Final flush via parse_lines on
                # the trailing buffer.
                if pending:
                    for ev in parse_lines(iter(pending)):
                        yield ev
                    pending.clear()
                break
            pending.append(item)
            # Greedily drain anything else immediately available so we
            # batch lines and the parser sees a coherent panel block.
            while not queue.empty():
                more = queue.get_nowait()
                if more is None:
                    pending.append("")  # marker; we'll re-detect EOF
                    break
                pending.append(more)
            # Parse what we have so far.
            batch = pending
            pending = []
            for ev in parse_lines(iter(batch)):
                yield ev
    except asyncio.CancelledError:
        # Consumer cancelled — kill the subprocess group so MCPs are released.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        raise
    finally:
        pump_task.cancel()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
