"""Render a step_log JSONL (src/logging/step_log.py) into one structured markdown file.

Heading layout (shows as a table of contents in any markdown outline view):

    # Step log (run, link, attempt)
    ## Overview                       one table row per step, linked
    ## Agent: <name>                  its system prompt (folded), its task, any handoff summary
    ### Step N · <tool>               one line at a glance, then the four sections:
    #### 1. Input to the model       only what is NEW this step; the previous step's call and result,
                                      which the prompt repeats, are referenced instead of reprinted
    #### 2. Model output             reasoning (<think>), visible response, any failed attempts
    #### 3. Tool invocation          arguments as typed vs as executed (knowledge base)
    #### 4. Tool result              status, values as a table, full text folded

Usage:
    python scripts/render_step_log.py <link_dir>/step_log_attempt1.jsonl [--kb <link_dir>/knowledge_base.jsonl]

Writes <jsonl stem>.md next to the JSONL.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

LONG_VALUE_CHARS = 100      # argument values longer than this move below the table
RESULT_TABLE_ROWS = 30      # result leaves shown in the table before the rest is folded
FOLD_LINES = 25             # text blocks longer than this are folded


# -- loading -------------------------------------------------------------------------------------

def load_rows(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _parse_kb_value(value):
    """KB rows store inputs as a JSON object or as a Python repr string; accept both."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        for parse in (json.loads, ast.literal_eval):
            try:
                out = parse(value)
                if isinstance(out, dict):
                    return out
            except (ValueError, SyntaxError):
                continue
    return None


def kb_calls_by_agent_tool(kb_path: Path | None, attempt: int | None = None) -> dict[tuple[str, str], list[dict]]:
    """KB rows grouped by (agent, tool) in seq order, for one attempt when given (a link's KB holds
    every attempt). The KB records success, failed and refused MCP calls; non-MCP tools have no row."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    if not kb_path or not kb_path.exists():
        return grouped
    for row in load_rows(kb_path):
        if attempt is not None and str(row.get("attempt")) not in ("", "None", str(attempt)):
            continue
        grouped[(str(row.get("agent")), str(row.get("tool")))].append(row)
    for rows in grouped.values():
        rows.sort(key=lambda r: int(r.get("seq") or 0))
    return grouped


# -- small formatting helpers --------------------------------------------------------------------

def fence(text: str, lang: str = "") -> str:
    """A code fence longer than any backtick run inside the text."""
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text or ''}\n{ticks}"


def fold(summary: str, body: str, open_: bool = False) -> str:
    return f"<details{' open' if open_ else ''}><summary>{summary}</summary>\n\n{body}\n\n</details>"


def block(text: str, lang: str = "", label: str = "Full text") -> str:
    """A code block, folded when it is long."""
    text = text or ""
    n = text.count("\n") + 1
    if n > FOLD_LINES:
        return fold(f"{label} ({n} lines)", fence(text, lang))
    return fence(text, lang)


def quote(text: str) -> str:
    """Prose as a blockquote, so reasoning reads as text rather than code."""
    return "\n".join("> " + line if line.strip() else ">" for line in (text or "").splitlines())


def cell(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return "`" + text.replace("|", "\\|").replace("`", "'") + "`"


def _clock(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "?"


def _num(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "?"


def flatten(value, prefix: str = "") -> list[tuple[str, object]]:
    """Leaves of a JSON value as (dotted.path, value) pairs."""
    if isinstance(value, dict) and value:
        out = []
        for k, v in value.items():
            out += flatten(v, f"{prefix}.{k}" if prefix else str(k))
        return out
    if isinstance(value, list) and value and any(isinstance(v, (dict, list)) for v in value):
        out = []
        for i, v in enumerate(value):
            out += flatten(v, f"{prefix}[{i}]")
        return out
    return [(prefix or "(value)", value)]


def parse_json(text):
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


# -- what is new in a prompt ---------------------------------------------------------------------

def new_messages(previous: list[dict] | None, current: list[dict]) -> tuple[list[dict], int]:
    """Messages after the longest common prefix with the previous prompt, and how many earlier
    messages changed or disappeared (the B80 budget trims old steps)."""
    if not previous:
        return current, 0
    common = 0
    for a, b in zip(previous, current):
        if a != b:
            break
        common += 1
    return current[common:], len(previous) - common


def is_echo(msg: dict, prev_step: dict | None) -> bool:
    """True when a new prompt message only repeats the previous step's output, call or result."""
    if not prev_step:
        return False
    role, content = msg.get("role"), (msg.get("content") or "").strip()
    gens = prev_step.get("generations") or []
    if role == "assistant" and gens:
        return content in {(gens[-1].get("visible") or "").strip(), (gens[-1].get("raw_text") or "").strip()}
    if role == "tool-call":
        return bool(prev_step.get("tool_calls"))
    if role == "tool-response":
        obs = (prev_step.get("observations") or "").strip()
        return content.startswith("Observation:") and bool(obs) and obs in content
    return False


# -- sections ------------------------------------------------------------------------------------

def section_input(gen: dict, prev_step: dict | None, prev_prompt: list[dict] | None) -> list[str]:
    out = ["#### 1. Input to the model", ""]
    prompt = gen.get("prompt") if gen else None
    if not prompt:
        return out + ["_Prompt not captured (generated on another thread)._", ""]
    messages = prompt.get("messages") or []
    meta = f"Prompt: {_num(prompt.get('prompt_tokens'))} tokens, {len(messages)} messages."
    if prev_prompt is None:
        return out + [meta, "", "First step of this run: the system prompt and task are shown above.", ""]
    fresh, dropped = new_messages(prev_prompt, messages)
    if dropped:
        meta += f" {dropped} earlier message(s) were trimmed or changed (B80 prompt budget)."
    news = [m for m in fresh if not is_echo(m, prev_step)]
    echoed = len(fresh) - len(news)
    out += [meta, ""]
    if echoed:
        out += [f"Repeats the previous step's response, tool call and result "
                f"([Step {prev_step['seq']}](#step-{prev_step['seq']})).", ""]
    if not news:
        out += ["Nothing else is new.", ""]
    for msg in news:
        label = "Error fed back to the model" if "Error:" in (msg.get("content") or "")[:400] else "New message"
        out += [f"**{label}** (role `{msg.get('role')}`):", "", block(msg.get("content", "")), ""]
    return out


def _reasoning(gen: dict) -> list[str]:
    think = gen.get("think")
    if think is None:
        return ["_No `<think>` block in the output._"]
    if think == "":
        return ["_Empty `<think>` block (thinking off or skipped)._"]
    lines = [f"_{len(think):,} characters" + ("" if gen.get("think_closed", True)
                                                  else "; UNCLOSED, the output hit the token limit") + "._", ""]
    return lines + [quote(think)]


def retry_reason(gen: dict, nxt: dict) -> str:
    """Why ThinkingModel retried, read from what the next attempt actually did."""
    if not gen.get("think_closed", True):
        problem = "the output hit the token limit inside `<think>`"
    elif "{" not in (gen.get("visible") or ""):
        problem = "no tool call in the output (plain-text answer)"
    else:
        problem = "the tool call could not be parsed"
    if gen.get("thinking_enabled") and not nxt.get("thinking_enabled"):
        return problem + "; retried with thinking OFF"
    return problem + "; retried with the parse error appended to the prompt"


def section_output(gens: list[dict]) -> list[str]:
    out = ["#### 2. Model output", ""]
    if not gens:
        return out + ["_No generation recorded._", ""]
    used = gens[-1]
    out += ["##### Reasoning", ""] + _reasoning(used)
    if not used.get("thinking_enabled", True):
        out += ["", "_Thinking was OFF for this attempt._"]
    out += [""]
    out += ["##### Visible response", "", block(used.get("visible", ""), label="Visible response"), ""]
    if len(gens) > 1:
        out += ["##### Failed attempts before this one", ""]
        for gen, nxt in zip(gens, gens[1:]):
            why = retry_reason(gen, nxt)
            body = "\n".join(_reasoning(gen)) + "\n\nVisible response:\n\n" + fence(gen.get("visible", ""))
            out += [f"- Attempt {gen.get('attempt')}: {why}.", "", fold(f"Attempt {gen.get('attempt')} output", body), ""]
    return out


def _argument_table(typed, executed) -> list[str]:
    typed_flat = dict(flatten(typed if isinstance(typed, dict) else {"(arguments)": typed}))
    exec_flat = dict(flatten(executed)) if isinstance(executed, dict) else None
    keys = list(typed_flat) + [k for k in (exec_flat or {}) if k not in typed_flat]
    if not keys:
        return ["_No arguments._"]
    show_exec = exec_flat is not None and exec_flat != typed_flat
    head = "| Argument | As typed |" + (" As executed |" if show_exec else "")
    out = [head, "|---|---|" + ("---|" if show_exec else "")]
    long_values = []
    for k in keys:
        vals = [typed_flat.get(k, "—")] + ([exec_flat.get(k, "—")] if show_exec else [])
        cells = []
        for v in vals:
            text = v if isinstance(v, str) else json.dumps(v, default=str)
            if v == "—":
                cells.append("—")
            elif len(text) > LONG_VALUE_CHARS or "\n" in text:
                cells.append("_(below)_")
                long_values.append((k, text))
            else:
                cells.append(cell(v))
        if show_exec and len(cells) == 2 and cells[0] != cells[1]:
            k = f"**{k}**"
        out.append(f"| {k} | " + " | ".join(cells) + " |")
    for k, text in long_values:
        out += ["", f"`{k}`:", "", block(text, label=k)]
    if show_exec:
        out += ["", "_Bold rows were added or changed by the middleware before the call reached the server._"]
    return out


def section_tool(row: dict, kb: dict, kb_used: dict) -> tuple[list[str], list[dict]]:
    out = ["#### 3. Tool invocation", ""]
    calls = row.get("tool_calls") or []
    matched = []
    gens = row.get("generations") or []
    written = (gens[-1].get("visible") or "").count("<tool_call>") if gens else 0
    if written > len(calls):
        out += [f"**Warning:** the model wrote {written} tool calls; only {len(calls)} executed (B127).", ""]
    if not calls:
        return out + ["_No tool call._", ""], matched
    for call in calls:
        name = call.get("name")
        key = (str(row.get("agent")), str(name))
        kb_row = None
        if kb_used[key] < len(kb.get(key, [])):
            kb_row = kb[key][kb_used[key]]
            kb_used[key] += 1
        matched.append(kb_row)
        executed = _parse_kb_value(kb_row.get("inputs")) if kb_row else None
        src = f" (knowledge base seq {kb_row.get('seq')})" if kb_row else " (not an MCP call: no knowledge-base row)"
        out += [f"**Tool:** `{name}`{src}", ""] + _argument_table(call.get("arguments"), executed) + [""]
    return out, matched


def section_result(row: dict, kb_rows: list[dict]) -> list[str]:
    out = ["#### 4. Tool result", ""]
    statuses = [r.get("status") for r in kb_rows if r]
    if statuses:
        out += [f"**Status:** {', '.join(statuses)}", ""]
    if row.get("error"):
        out += ["**Step error:**", "", block(str(row["error"])), ""]
    text = str(row.get("observations") or "")
    data = parse_json(text)
    if isinstance(data, (dict, list)) and data:
        leaves = flatten(data)
        out += ["| Field | Value |", "|---|---|"]
        for k, v in leaves[:RESULT_TABLE_ROWS]:
            shown = v if isinstance(v, str) else json.dumps(v, default=str)
            if len(shown) > LONG_VALUE_CHARS:
                shown = shown[:LONG_VALUE_CHARS] + "…"
            out.append(f"| {k} | {cell(shown)} |")
        if len(leaves) > RESULT_TABLE_ROWS:
            out.append(f"| … | {len(leaves) - RESULT_TABLE_ROWS} more fields in the full result |")
        out += ["", fold("Full result (JSON)", fence(json.dumps(data, indent=2, ensure_ascii=False), "json")), ""]
    else:
        out += [block(text, label="Result") if text else "_Empty._", ""]
    return out


# -- step and agent ------------------------------------------------------------------------------

def _glance(row: dict, kb_rows: list[dict]) -> str:
    gens = row.get("generations") or []
    used = gens[-1] if gens else {}
    calls = ", ".join(f"`{c.get('name')}`" for c in row.get("tool_calls") or []) or "no tool"
    status = ", ".join(r.get("status", "?") for r in kb_rows if r) or ("error" if row.get("error") else "ok")
    dur = ""
    if row.get("start_time") and row.get("end_time"):
        dur = f"{float(row['end_time']) - float(row['start_time']):.0f} s"
    think = used.get("think")
    parts = [f"called {calls} → **{status}**", dur,
             f"prompt {_num((used.get('prompt') or {}).get('prompt_tokens'))} tokens",
             f"reasoning {len(think):,} chars" if think else "no reasoning"]
    if len(gens) > 1:
        parts.append(f"{len(gens) - 1} failed attempt(s)")
    return " · ".join(p for p in parts if p)


def render_agent_header(agent: str, first_step: dict | None, summaries: list[dict]) -> list[str]:
    out = [f"## Agent: {agent}", ""]
    prompt = ((first_step or {}).get("generations") or [{}])[-1].get("prompt") or {}
    msgs = prompt.get("messages") or []
    system = [m for m in msgs if m.get("role") == "system"]
    task = [m for m in msgs if m.get("role") == "user"]
    for s in summaries:
        out += render_summary(s, under_agent=True)
    if system:
        out += [fold(f"System prompt ({len(system[0].get('content', '')):,} chars)", fence(system[0]["content"])), ""]
    if task:
        out += ["### Task given to this agent", "", block(task[0].get("content", ""), label="Task"), ""]
    return out


def render_run_header(agent: str, run: int, first_step: dict, summaries: list[dict]) -> list[str]:
    """A re-run of an agent by its method handler; its task carries the handler's feedback."""
    out = [f"### Run {run} of {agent}", "",
           "_The method handler ran this agent again with a new task (iterative feedback or a stage retry)._", ""]
    for s in summaries:
        out += render_summary(s, under_agent=True)
    prompt = (first_step.get("generations") or [{}])[-1].get("prompt") or {}
    task = [m for m in prompt.get("messages") or [] if m.get("role") == "user"]
    if task:
        out += ["**Task for this run** (includes any feedback from the previous run):", "",
                block(task[0].get("content", ""), label="Task"), ""]
    return out


def render_summary(row: dict, under_agent: bool = False) -> list[str]:
    title = "Knowledge-base handoff given to this agent" if under_agent else "Knowledge-base summary"
    prompt = ((row.get("prompt") or {}).get("messages") or [{}])[0].get("content", "")
    return [f"### {title}", "",
            f"_Written by the summary model `{row.get('model_id')}`; it prefixes the agent's task._", "",
            quote(row.get("visible") or row.get("raw_text") or ""), "",
            fold("Summary model input", fence(prompt)), ""]


def render(jsonl: Path, kb_path: Path | None = None, out_path: Path | None = None) -> Path:
    rows = load_rows(jsonl)
    m = re.search(r"attempt(\d+)", jsonl.stem)
    kb = kb_calls_by_agent_tool(kb_path, int(m.group(1)) if m else None)
    kb_used: dict[tuple[str, str], int] = defaultdict(int)

    body: list[str] = []
    overview: list[str] = []
    current_agent = None
    pending_summaries: list[dict] = []
    prev_step: dict[str, dict] = {}
    prev_prompt: dict[str, list[dict]] = {}
    runs: dict[str, int] = {}

    for row in rows:
        agent = str(row.get("agent"))
        kind = row.get("kind")
        if kind == "summary":
            # A handoff summary is written just before the next agent starts, outside any agent scope.
            if agent in ("unknown", "", "None") or agent != current_agent:
                pending_summaries.append(row)
            else:
                body += render_summary(row)
            continue
        # A method handler (iterative feedback, a retried stage) re-runs an agent: its step counter
        # restarts at 1 and it gets a fresh prompt, so that is a new run, not a trimmed prompt.
        restart = kind == "step" and row.get("agent_step") == 1 and agent in prev_step
        if agent not in runs:
            runs[agent] = 1
            body += render_agent_header(agent, row if kind == "step" else None, pending_summaries)
            pending_summaries = []
        elif restart:
            runs[agent] += 1
            body += render_run_header(agent, runs[agent], row, pending_summaries)
            pending_summaries = []
            prev_step.pop(agent, None)
            prev_prompt.pop(agent, None)
        current_agent = agent
        if kind == "generation_without_step":
            body += [f"### Generation not attached to a step (seq {row['seq']})", ""]
            for gen in row.get("generations") or []:
                body += section_output([gen])
            continue

        gens = row.get("generations") or []
        tool_lines, kb_rows = section_tool(row, kb, kb_used)
        names = ", ".join(c.get("name") for c in row.get("tool_calls") or []) or "no tool call"
        body += [f'<a id="step-{row["seq"]}"></a>', "",
                 f"### Step {row['seq']} · {names}", "",
                 f"_{agent}, its step {row.get('agent_step')}, started {_clock(row.get('start_time'))}._ "
                 + _glance(row, kb_rows), ""]
        body += section_input(gens[-1] if gens else {}, prev_step.get(agent), prev_prompt.get(agent))
        body += section_output(gens)
        body += tool_lines
        body += section_result(row, kb_rows)
        body += ["---", ""]

        used = gens[-1] if gens else {}
        status = ", ".join(r.get("status", "?") for r in kb_rows if r) or ("error" if row.get("error") else "ok")
        think = used.get("think")
        reasoning = f"{len(think):,}" if think else "—"
        who = agent if runs[agent] == 1 else f"{agent} (run {runs[agent]})"
        overview.append(f"| [{row['seq']}](#step-{row['seq']}) | {who} | {names} | {status} | "
                        f"{reasoning} | {len(gens) - 1 or ''} |")
        prev_step[agent] = row
        if used.get("prompt"):
            prev_prompt[agent] = used["prompt"]["messages"]

    for s in pending_summaries:      # a summary after the last step
        body += render_summary(s)

    head = [f"# Step log: {jsonl.parent.name} · {jsonl.stem}", "",
            f"Source `{jsonl.name}`, {len(rows)} rows. Each step has four sections: input to the model, "
            "model output (reasoning and visible response), tool invocation, tool result.", "",
            "## Overview", "",
            "| Step | Agent | Tool | Status | Reasoning chars | Failed attempts |",
            "|---|---|---|---|---|---|"] + overview + [""]
    out_path = out_path or jsonl.with_suffix(".md")
    out_path.write_text("\n".join(head + body) + "\n", encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--kb", type=Path, default=None,
                        help="knowledge_base.jsonl (default: next to the JSONL)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    kb = args.kb or args.jsonl.parent / "knowledge_base.jsonl"
    print(render(args.jsonl, kb, args.out))


if __name__ == "__main__":
    main()
