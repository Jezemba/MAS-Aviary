"""Turn parsed Event objects into Open-WebUI-friendly markdown chunks.

The output uses:
  * Markdown headers + emoji per agent for color/affinity
  * Collapsible <details>/<summary> blocks per agent so the chat stays
    short while the full transcript remains expandable
  * Inline code for tool names and JSON for tool arguments
  * A live status line at the top of each agent's card that flips from
    "running" to a tool count + final-answer preview when the agent ends.

The formatter is a small state machine that emits incremental markdown
diffs. Each diff is a string that the chat server appends to the current
assistant message. Open WebUI re-renders the whole message after every
delta, so we can rewrite earlier sections by streaming the entire
message-so-far on each update — but that's quadratic in transcript size,
so we instead emit *append-only* chunks. Each agent gets its own
<details> block, opened on agent_start and updated by appending tool
calls and the final answer inside it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Optional

from event_parser import Event


# Stable per-agent identity: emoji + display name. Order matches the
# sequential pipeline so unknown agents fall back to a neutral marker.
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


def _short_args(args: dict, max_chars: int = 220) -> str:
    """Pretty short rendering of tool args for inline display."""
    if not isinstance(args, dict):
        return str(args)[:max_chars]
    try:
        s = json.dumps(args, indent=None, default=str)
    except (TypeError, ValueError):
        s = str(args)
    if len(s) > max_chars:
        return s[: max_chars - 1] + "…"
    return s


def _short_obs(text: str, max_chars: int = 300) -> str:
    if len(text) > max_chars:
        return text[: max_chars - 1] + "…"
    return text


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


@dataclass
class FormatterState:
    """Tracks per-agent statistics so the final summary line is accurate."""
    blocks: dict[str, _AgentBlock] = field(default_factory=dict)
    wandb_url: Optional[str] = None
    run_done: Optional[dict] = None
    _intro_emitted: bool = False


def initial_intro() -> str:
    """The very first chunk of the message — sets the scene before any
    agent has fired."""
    return (
        "# 🛩️  MAS-Aviary multi-MCP MDO — DLR-F25\n\n"
        "**Design task:** Minimize fuel burn for a DLR-F25-class aircraft "
        "at 2500 nmi / 200 pax / Mach 0.78 / FL330. F25 reference: "
        "MTOM 85,700 kg, fuel 12,100 kg, AR 15.6.\n\n"
        "Five MCP servers coordinate across seven agent stages. Each "
        "card below opens to show the tool calls and observations as "
        "the agent runs.\n\n"
        "---\n\n"
    )


def format_event(event: Event, state: FormatterState) -> str:
    """Return the markdown chunk to APPEND for this event, or '' if nothing."""

    # Lazy intro on first event.
    intro = ""
    if not state._intro_emitted:
        intro = initial_intro()
        state._intro_emitted = True

    if event.kind == "wandb_url":
        state.wandb_url = event.text
        return intro + f"📈 **wandb run:** [{event.text}]({event.text})\n\n"

    if event.kind == "agent_start":
        a = event.agent or "unknown"
        sty = _agent_style(a)
        if a not in state.blocks:
            state.blocks[a] = _AgentBlock(agent=a)
        block = state.blocks[a]
        if block.started and not block.ended:
            # Already running (likely a re-invocation by iterative_feedback).
            # Surface a divider so the user sees the loop.
            return intro + (
                f"\n> *{sty['name']} is being re-invoked by "
                "iterative_feedback…*\n\n"
            )
        block.started = True
        # Open a new <details> block with a status header. We close it
        # when agent_end fires.
        return intro + (
            f'<details open>\n'
            f'<summary><strong>{sty["emoji"]} {sty["name"]}</strong> '
            f'<code>{sty["mcp"]}-mcp</code> — running…</summary>\n\n'
        )

    if event.kind == "step_start":
        sty = _agent_style(event.agent)
        return f"#### Step {event.step}\n"

    if event.kind == "tool_call":
        a = event.agent or "unknown"
        if a in state.blocks:
            state.blocks[a].n_tool_calls += 1
        args_short = _short_args(event.data.get("arguments", {}))
        return (
            f"- 🔧 `{event.text}(`{args_short}`)`\n"
        )

    if event.kind == "observation":
        obs_short = _short_obs(event.text)
        # If the observation contains a session_id, surface that
        sid = ""
        parsed = event.data.get("parsed") or {}
        if isinstance(parsed, dict) and "session_id" in parsed:
            sid = f"  *(session `{parsed['session_id'][:8]}…`)*"
        return f"  ↩ `{obs_short}`{sid}\n"

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
        d = event.data.get("duration_s", 0)
        return f"  ⏱ {d:.1f}s\n"

    if event.kind == "final_answer":
        a = event.agent or "unknown"
        if a in state.blocks:
            state.blocks[a].final_answer = event.text
        # Render the final answer in a code block (DESIGN_STATE blocks are
        # YAML-shaped and look right that way).
        return f"\n**Final answer:**\n\n```yaml\n{event.text[:1500]}\n```\n\n"

    if event.kind == "agent_end":
        a = event.agent or "unknown"
        sty = _agent_style(a)
        block = state.blocks.get(a)
        if block is None:
            return f"</details>\n\n"
        block.ended = True
        # Close the agent's details block and emit a summary line.
        summary_line = (
            f"\n*Done in {block.duration_s:.0f}s — {block.n_steps} steps, "
            f"{block.n_tool_calls} tool calls, "
            f"{block.tokens_out:,} output tokens.*\n"
        )
        return summary_line + "</details>\n\n"

    if event.kind == "run_done":
        state.run_done = event.data
        completed = event.data.get("completed", 0)
        failed = event.data.get("failed", 0)
        verdict = "🎉 **Run complete**" if failed == 0 else "❌ **Run failed**"
        return f"\n---\n\n{verdict} — {completed} completed, {failed} failed.\n"

    if event.kind == "error":
        return f"\n> ⚠️ {event.text}\n"

    return intro
