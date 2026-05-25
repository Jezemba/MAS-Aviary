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
_STEP_DURATION_RE = re.compile(r"\[Step (\d+): Duration ([\d.]+) seconds")
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
    # 1. Try the observation for an explicit 'agent_X' mention.
    obs_match = _AGENT_MENTION_RE.search(observation_text)
    if obs_match:
        return obs_match.group(1)

    # 2. write_blackboard often encodes the author in the key field.
    if tool == "write_blackboard":
        # args_text looks like: {'key': 'agent_3_status', 'value': '...', ...}
        key_match = re.search(r"'key':\s*'([^']+)'", args_text)
        if key_match:
            prefix_match = _AGENT_KEY_PREFIX_RE.match(key_match.group(1))
            if prefix_match:
                return prefix_match.group(1)

    # 3. Fall back to the most-recent New run banner.
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


def render_html(events: list[Event], summary: RunSummary, source_log: str) -> str:
    agents_in_run = sorted(summary.events_per_agent.keys())

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
    Source: <code>{_esc(source_log)}</code> · {summary.total_events} blackboard events recorded
  </div>
  <div class="meta-row">
    {wandb_link}
    {fuel_chip}
    {agent_legend_rows}
  </div>
</header>

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
    html = render_html(events, summary, source_log=os.path.basename(log_path))

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
