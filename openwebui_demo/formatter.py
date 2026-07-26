"""Turn parsed Event objects into Open-WebUI-friendly markdown chunks.

The formatter is a small state machine that emits incremental markdown
appended to the assistant's message. The new (v2) design adds:

  * Per-tool plain-language narration via narration.TOOL_DESCRIPTIONS,
    so the chat reads like a story rather than a debugger trace.
  * Per-observation one-line summaries via narration.summarize_observation,
    extracting headline numbers (span, AR, mesh cells, FUEL_BURNED, …).
  * Buffer/loading lines for slow tools ("this step takes a while").
  * Per-agent intro paragraph + hand-off line, plus a 7-stage progress
    strip refreshed at every agent boundary.
  * Raw tool args / observations are still available in a collapsible
    "show raw" section right under each tool call.

The output uses ``<details>`` blocks (Open WebUI markdown supports them)
so each agent's section is collapsible. Append-only deltas only — no
retroactive rewrites of earlier message text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from event_parser import Event
from narration import (
    AGENT_HANDOFFS,
    AGENT_INTROS,
    SLOW_TOOL_NOTE,
    STAGE_ORDER,
    describe_tool,
    stage_progress_line,
    summarize_observation,
)


AGENT_STYLE = {
    "geometry_engineer":    {"emoji": "📐", "name": "Geometry Engineer",   "mcp": "tigl"},
    "aerodynamics_analyst": {"emoji": "💨", "name": "Aerodynamics Analyst", "mcp": "su2"},
    "structures_analyst":   {"emoji": "⚖️", "name": "Structures Analyst",  "mcp": "mass"},
    "propulsion_analyst":   {"emoji": "🔥", "name": "Propulsion Analyst",  "mcp": "pycycle"},
    "mission_architect":    {"emoji": "✈️", "name": "Mission Architect",   "mcp": "aviary"},
    "simulation_executor":  {"emoji": "📊", "name": "Simulation Executor", "mcp": "aviary"},
    "mdo_integrator":       {"emoji": "🧮", "name": "MDO Integrator",      "mcp": "—"},
}


def _agent_style(agent: Optional[str]) -> dict:
    if not agent:
        return {"emoji": "•", "name": "(unknown)", "mcp": "—"}
    return AGENT_STYLE.get(
        agent,
        {"emoji": "•", "name": agent.replace("_", " ").title(), "mcp": "—"},
    )


def _short_json(args: dict, max_chars: int = 240) -> str:
    """Render small dicts compactly; truncate gracefully."""
    if not isinstance(args, dict):
        return str(args)[:max_chars]
    try:
        s = json.dumps(args, default=str)
    except (TypeError, ValueError):
        s = str(args)
    if len(s) > max_chars:
        return s[: max_chars - 1] + "…"
    return s


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class _AgentBlock:
    agent: str
    n_tool_calls: int = 0
    n_steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_s: float = 0.0
    final_answer: Optional[str] = None
    started: bool = False
    ended: bool = False
    # FIFO of pending tool names whose observations have not been
    # matched yet. A single step may call multiple tools and produce
    # multiple observations, in order — we pop the queue head when an
    # observation arrives.
    pending_tools: list[str] = field(default_factory=list)


@dataclass
class FormatterState:
    """Tracks per-agent statistics so the final summary line is accurate."""
    blocks: dict[str, _AgentBlock] = field(default_factory=dict)
    completed_agents: set[str] = field(default_factory=set)
    current_agent: Optional[str] = None
    wandb_url: Optional[str] = None
    run_done: Optional[dict] = None
    _intro_emitted: bool = False


# ── Markdown chunks ──────────────────────────────────────────────────────────


def initial_intro() -> str:
    """The very first chunk of the message — sets the scene before any
    agent has fired."""
    return (
        "# 🛩️  MAS-Aviary multi-MCP MDO\n\n"
        "**Design task:** minimize fuel burn for a DLR-F25-class transport "
        "aircraft on a **2,500 nmi · 200 pax · Mach 0.78 · FL330** mission.\n\n"
        "Seven specialist agents will collaborate across five disciplines. "
        "Each card below opens to show what that agent did — tool calls, "
        "key numbers, and a short narration of what the step means in "
        "plain language.\n\n"
        "**F25 reference targets:** MTOM 85,700 kg · fuel 12,100 kg · "
        "L/D 19.5 · AR 15.6.\n\n"
        "---\n\n"
    )


def _progress_strip(state: FormatterState) -> str:
    bar = stage_progress_line(state.current_agent, state.completed_agents)
    return f"`{bar}`\n\n"


def format_event(event: Event, state: FormatterState) -> str:
    """Return the markdown chunk to APPEND for this event, or '' if nothing."""
    intro = ""
    if not state._intro_emitted:
        intro = initial_intro()
        state._intro_emitted = True

    if event.kind == "wandb_url":
        state.wandb_url = event.text
        return intro + f"📈 **wandb run:** [{event.text}]({event.text})\n\n"

    if event.kind == "narration":
        # Synthetic event injected by the runner for pacing / context.
        return intro + (event.text or "") + "\n"

    if event.kind == "agent_start":
        a = event.agent or "unknown"
        sty = _agent_style(a)
        # Mark previous agent (if any) as completed for the progress strip.
        if state.current_agent and state.current_agent != a:
            state.completed_agents.add(state.current_agent)
        state.current_agent = a

        if a not in state.blocks:
            state.blocks[a] = _AgentBlock(agent=a)
        block = state.blocks[a]

        if block.started and not block.ended:
            # Re-invocation by iterative_feedback handler — surface it inline.
            return intro + (
                f"> 🔁 *{sty['name']} is being re-invoked by the orchestrator…*\n\n"
            )

        block.started = True

        # Open a new <details> block, expanded by default. Inside it,
        # a one-paragraph intro of what this agent does.
        intro_text = AGENT_INTROS.get(a, "")
        head = (
            f"{_progress_strip(state)}"
            f'<details open>\n'
            f'<summary><strong>{sty["emoji"]} {sty["name"]}</strong> '
            f'<code>{sty["mcp"]}-mcp</code> — working…</summary>\n\n'
            f"_{intro_text}_\n\n"
        )
        return intro + head

    if event.kind == "step_start":
        # Step boundaries are not surfaced as headers any more — they
        # add noise. The agent intro + per-tool narration carry the story.
        return ""

    if event.kind == "tool_call":
        a = event.agent or "unknown"
        if a in state.blocks:
            state.blocks[a].n_tool_calls += 1
            state.blocks[a].pending_tools.append(event.text)
        desc, hint = describe_tool(event.text)
        slow_note = ""
        if hint == "slow":
            slow_note = f"\n  > {SLOW_TOOL_NOTE}"
        # Plain-language line. Raw args go inline as small monospace
        # under the description so the reader sees them without
        # clicking, but they don't dominate the page.
        raw_args = _short_json(event.data.get("arguments", {}), max_chars=200)
        return (
            f"\n**{desc}**{slow_note}\n"
            f"  <sub>↳ <code>{event.text}({raw_args})</code></sub>\n"
        )

    if event.kind == "observation":
        a = event.agent or "unknown"
        block = state.blocks.get(a)
        tool_name = None
        if block and block.pending_tools:
            # Pop the FIFO head — match this observation to the next
            # un-answered tool call in order.
            tool_name = block.pending_tools.pop(0)
        parsed = event.data.get("parsed") or {}
        summary = summarize_observation(tool_name or "", parsed) if isinstance(parsed, dict) else None
        if summary:
            line = f"  {summary}\n"
        else:
            # Generic acknowledgement for tools we don't have a summarizer for.
            raw = event.text or ""
            ack = "✓ Got a response."
            if '"error"' in raw or '"success": false' in raw:
                ack = "⚠ The tool reported an error (see raw)."
            line = f"  {ack}\n"

        # Tuck the raw observation into a small collapsible. HTML
        # blocks in markdown must start at column 0, otherwise some
        # renderers treat them as inline text. Keep the JSON short.
        raw_short = _truncate(event.text or "", 1500)
        line += (
            "\n<details><summary><sub>raw response</sub></summary>\n\n"
            f"```json\n{raw_short}\n```\n\n"
            "</details>\n"
        )
        return line

    if event.kind == "step_meta":
        a = event.agent or "unknown"
        if a in state.blocks:
            state.blocks[a].n_steps = max(state.blocks[a].n_steps, event.step or 0)
            state.blocks[a].duration_s += event.data.get("duration_s", 0)
            state.blocks[a].tokens_in = max(
                state.blocks[a].tokens_in, event.data.get("input_tokens", 0)
            )
            state.blocks[a].tokens_out = max(
                state.blocks[a].tokens_out, event.data.get("output_tokens", 0)
            )
            # End-of-step: drop any unmatched pending tools so they
            # don't leak into the next step.
            state.blocks[a].pending_tools.clear()
        d = event.data.get("duration_s", 0)
        return f"  ⏱ <sub>{d:.1f}s</sub>\n"

    if event.kind == "final_answer":
        a = event.agent or "unknown"
        if a in state.blocks:
            state.blocks[a].final_answer = event.text
        return (
            f"\n**Final answer**\n\n"
            f"```yaml\n{_truncate(event.text, 1800)}\n```\n\n"
        )

    if event.kind == "agent_end":
        a = event.agent or "unknown"
        sty = _agent_style(a)
        block = state.blocks.get(a)
        if block is None:
            return f"</details>\n\n"
        block.ended = True
        state.completed_agents.add(a)
        # Close the <details> block with a stats footer + hand-off line.
        handoff = AGENT_HANDOFFS.get(a, "")
        summary_line = (
            f"\n*<small>{sty['emoji']} {sty['name']} done — "
            f"{block.n_steps} steps · {block.n_tool_calls} tool calls · "
            f"{block.duration_s:.0f} s · "
            f"{block.tokens_out:,} output tokens.</small>*\n"
        )
        handoff_line = f"\n{handoff}\n\n" if handoff else "\n"
        return summary_line + "</details>\n" + handoff_line

    if event.kind == "run_done":
        state.run_done = event.data
        completed = event.data.get("completed", 0)
        failed = event.data.get("failed", 0)
        if failed == 0:
            verdict = "🎉 **Pipeline complete.**"
        else:
            verdict = "❌ **Pipeline failed.**"
        return (
            f"\n---\n\n{verdict} {completed} completed, {failed} failed.\n\n"
            f"{_progress_strip_final(state)}\n"
        )

    if event.kind == "error":
        return f"\n> ⚠️ {event.text}\n"

    return intro


def _progress_strip_final(state: FormatterState) -> str:
    """Same as the regular progress strip but with every stage marked done
    (for the final summary)."""
    cells = []
    for a in STAGE_ORDER:
        emoji = AGENT_STYLE.get(a, {"emoji": "•"})["emoji"]
        # ✓ if we've ever seen this agent, otherwise · (skipped).
        if a in state.completed_agents or a in state.blocks:
            cells.append(f"{emoji}✓")
        else:
            cells.append(f"{emoji}·")
    return "`" + " ".join(cells) + "`"
