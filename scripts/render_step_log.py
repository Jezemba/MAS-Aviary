"""Render a step_log JSONL (src/logging/step_log.py) into readable markdown, one file per agent.

Each step shows four sections: 1 Input (the messages new since that agent's previous step; the full
prompt stays in the JSONL), 2 Assistant (think block, visible text, any parse-retry attempts),
3 Tool invocation (as the LLM typed it, plus the arguments as executed from knowledge_base.jsonl when
the middleware changed them), 4 Tool result (the observation fed back to the agent).

Usage:
    python scripts/render_step_log.py <link_dir>/step_log_attempt1.jsonl [--kb <link_dir>/knowledge_base.jsonl]

Writes <jsonl stem>/ next to the JSONL: 00_index.md, NN_<agent>.md, summaries.md.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


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


def fence(text: str, lang: str = "") -> str:
    """A code fence longer than any backtick run inside the text."""
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{lang}\n{text or ''}\n{ticks}"


def _clock(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "?"


def _json(value) -> str:
    return json.dumps(value, indent=2, default=str, ensure_ascii=False)


def render_input(gen: dict, previous_prompt: list[dict] | None) -> list[str]:
    prompt = gen.get("prompt")
    if not prompt:
        return ["_Prompt not captured (generated on another thread)._"]
    messages = prompt.get("messages") or []
    fresh, dropped = new_messages(previous_prompt, messages)
    head = f"Prompt sent: {prompt.get('prompt_tokens', '?')} tokens, {len(messages)} messages"
    if previous_prompt:
        head += f"; showing the {len(fresh)} new since this agent's previous step"
    if dropped:
        head += f". {dropped} earlier message(s) changed or were dropped (B80 trim)"
    out = [f"_{head}._", ""]
    for msg in fresh:
        out.append(f"**[{msg.get('role')}]**")
        out.append(fence(msg.get("content", "")))
        if msg.get("tool_calls"):
            out.append(fence(_json(msg["tool_calls"]), "json"))
    return out


def render_generation(gen: dict) -> list[str]:
    out = []
    think = gen.get("think")
    if think is None:
        out.append("_No think tags in the output._")
    elif think == "":
        out.append("_Empty think block._")
    else:
        note = "" if gen.get("think_closed", True) else " (UNCLOSED: output truncated)"
        out.append(f"<details><summary>think ({len(think)} chars){note}</summary>\n")
        out.append(fence(think))
        out.append("</details>\n")
    out.append("Visible text:")
    out.append(fence(gen.get("visible", "")))
    return out


def render_step(row: dict, previous_prompt: list[dict] | None, kb: dict, kb_used: dict) -> list[str]:
    gens = row.get("generations") or []
    dur = ""
    if row.get("start_time") and row.get("end_time"):
        dur = f", {float(row['end_time']) - float(row['start_time']):.1f} s"
    out = [f"## Step {row['seq']} · {row.get('agent')} · agent step {row.get('agent_step')} "
           f"({_clock(row.get('start_time'))}{dur})", ""]

    out += ["### 1. Input", ""]
    if gens:
        out += render_input(gens[-1], previous_prompt)
    else:
        out.append("_No generation recorded for this step._")

    out += ["", "### 2. Assistant", ""]
    for gen in gens[:-1]:
        out.append(f"**Attempt {gen.get('attempt')} failed to parse** (thinking={gen.get('thinking_enabled')}); "
                   "the retry prompt appends this output and an error message.")
        out += render_generation(gen)
    if gens:
        if len(gens) > 1:
            out.append(f"**Attempt {gens[-1].get('attempt')} (used):**")
        out += render_generation(gens[-1])

    out += ["", "### 3. Tool invocation", ""]
    calls = row.get("tool_calls") or []
    if not calls:
        out.append("_No tool call._")
    for call in calls:
        name = call.get("name")
        out.append(f"`{name}` as typed by the model:")
        out.append(fence(_json(call.get("arguments")), "json"))
        key = (str(row.get("agent")), str(name))
        idx = kb_used[key]
        if idx < len(kb.get(key, [])):
            kb_row = kb[key][idx]
            kb_used[key] += 1
            executed = _parse_kb_value(kb_row.get("inputs"))
            status = kb_row.get("status")
            if executed is not None and executed != call.get("arguments"):
                out.append(f"As executed after middleware (KB seq {kb_row.get('seq')}, status {status}):")
                out.append(fence(_json(executed), "json"))
            else:
                out.append(f"_Executed unchanged (KB seq {kb_row.get('seq')}, status {status})._")

    out += ["", "### 4. Tool result", ""]
    out.append(fence(str(row.get("observations") or "")))
    if row.get("error"):
        out.append("Step error:")
        out.append(fence(str(row["error"])))
    out.append("")
    return out


def render(jsonl: Path, kb_path: Path | None = None, out_dir: Path | None = None) -> Path:
    rows = load_rows(jsonl)
    out_dir = out_dir or jsonl.with_suffix("")
    out_dir.mkdir(parents=True, exist_ok=True)
    m = re.search(r"attempt(\d+)", jsonl.stem)
    kb = kb_calls_by_agent_tool(kb_path, int(m.group(1)) if m else None)
    kb_used: dict[tuple[str, str], int] = defaultdict(int)

    order: list[str] = []
    per_agent: dict[str, list[str]] = defaultdict(list)
    last_prompt: dict[str, list[dict]] = {}
    summaries: list[str] = []
    for row in rows:
        agent = str(row.get("agent"))
        if row.get("kind") == "summary":
            summaries += [f"## Summary {row['seq']} (during {agent}, {row.get('model_id')})", "",
                          "### Input", fence((row.get("prompt") or {}).get("messages", [{}])[0].get("content", "")),
                          "### Output", fence(row.get("raw_text", "")), ""]
            continue
        if agent not in per_agent:
            order.append(agent)
        if row.get("kind") == "generation_without_step":
            per_agent[agent].append(f"## Seq {row['seq']} · {agent} · generation not attached to a step\n")
            for gen in row.get("generations") or []:
                per_agent[agent] += render_input(gen, last_prompt.get(agent)) + render_generation(gen)
            continue
        per_agent[agent] += render_step(row, last_prompt.get(agent), kb, kb_used)
        gens = row.get("generations") or []
        if gens and gens[-1].get("prompt"):
            last_prompt[agent] = gens[-1]["prompt"]["messages"]

    index = [f"# Step log: {jsonl.name}", "", f"{len(rows)} rows.", "", "| # | Agent | Steps |", "|---|---|---|"]
    for i, agent in enumerate(order, 1):
        fname = f"{i:02d}_{re.sub(r'[^A-Za-z0-9_.-]', '_', agent)}.md"
        n = sum(1 for r in rows if str(r.get("agent")) == agent and r.get("kind") == "step")
        (out_dir / fname).write_text(f"# {agent}\n\n" + "\n".join(per_agent[agent]), encoding="utf-8")
        index.append(f"| {i} | [{agent}]({fname}) | {n} |")
    if summaries:
        (out_dir / "summaries.md").write_text("# Knowledge-base summaries\n\n" + "\n".join(summaries),
                                              encoding="utf-8")
        index += ["", f"[Knowledge-base summaries](summaries.md): {sum(1 for r in rows if r.get('kind') == 'summary')}"]
    (out_dir / "00_index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return out_dir


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
