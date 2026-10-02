"""Per-step LLM I/O log: what the model was sent, what it wrote, which tool it called, what came back.

trace.json cannot answer "why did the agent do that": ThinkingModel.parse_tool_calls deletes the
``<think>`` block in place before smolagents stores the step, parse retries inside generate() never
become steps, and smolagents' ``model_input_messages`` is the prompt BEFORE the B80 budget trimmed it.
This module records each of those at the point it exists, and pairs them per agent step.

Off unless ``MAS_STEP_LOG=1``: every hook returns immediately when no log path is configured, so a
run without it behaves exactly like one without this module.

Rows (one JSON object per line, flushed as written, so a killed run keeps what it had):
  kind="step"                       one smolagents ActionStep: prompt(s), raw text(s), tool calls, result
  kind="generation_without_step"    generations no ActionStep claimed (e.g. the max-steps final answer)
  kind="summary"                    one knowledge-base summary on the small summary model
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

_lock = threading.Lock()
_path: Path | None = None
_seq = 0
_tls = threading.local()


# -- think-block parsing ----------------------------------------------------------------------

def split_think(text: str | None) -> dict:
    """Split raw model text into reasoning and visible text.

    Returns ``{"think": str | None, "visible": str, "think_closed": bool}``. ``think`` is None when the
    text has no think tags at all, and "" for an empty block (thinking disabled still emits one).
    Handles several blocks, an unclosed ``<think>`` (output truncated at max_new_tokens) and a
    ``</think>`` whose opening tag was part of the prompt template rather than the output.
    """
    text = text or ""
    if "<think>" not in text and "</think>" not in text:
        return {"think": None, "visible": text.strip(), "think_closed": True}

    thoughts = [m.strip() for m in _THINK_BLOCK_RE.findall(text)]
    rest = _THINK_BLOCK_RE.sub("", text)
    closed = True
    if "</think>" in rest:                       # opening tag lived in the prompt
        head, _, rest = rest.partition("</think>")
        thoughts.insert(0, head.replace("<think>", "").strip())
    if "<think>" in rest:                        # opened, never closed: truncated
        rest, _, tail = rest.partition("<think>")
        thoughts.append(tail.strip())
        closed = False
    return {"think": "\n\n".join(t for t in thoughts if t), "visible": rest.strip(), "think_closed": closed}


# -- configuration ----------------------------------------------------------------------------

def configure(path: str | os.PathLike | None) -> None:
    """Start (or with None, stop) writing to ``path``."""
    global _path, _seq
    with _lock:
        _path = Path(path) if path else None
        _seq = 0
        if _path is not None:
            _path.parent.mkdir(parents=True, exist_ok=True)


def configure_from_env(kb_path: str | os.PathLike | None, attempt: int | None) -> Path | None:
    """Put the log next to the run's knowledge base, one file per attempt, when MAS_STEP_LOG=1."""
    if os.environ.get("MAS_STEP_LOG") != "1" or not kb_path:
        return None
    path = Path(kb_path).parent / f"step_log_attempt{int(attempt or 1)}.jsonl"
    configure(path)
    return path


def enabled() -> bool:
    return _path is not None


def log_path() -> Path | None:
    return _path


# -- serialization ----------------------------------------------------------------------------

def _role(value: Any) -> str:
    value = getattr(value, "value", value)
    return str(value or "")


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "image":
                    parts.append("[image]")
                else:
                    text = part.get("text", "")
                    parts.append(text if isinstance(text, str) else json.dumps(text, default=str))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return str(content)


def serialize_message(msg: Any) -> dict:
    """One chat message as {role, content[, tool_calls]}; full text, never truncated."""
    get = msg.get if isinstance(msg, dict) else (lambda k, d=None: getattr(msg, k, d))
    out = {"role": _role(get("role")), "content": _content_text(get("content"))}
    calls = get("tool_calls")
    if calls:
        out["tool_calls"] = [_tool_call(c) for c in calls]
    return out


def _tool_call(call: Any) -> dict:
    if isinstance(call, dict):
        func = call.get("function", call)
        return {"name": func.get("name"), "arguments": func.get("arguments"), "id": call.get("id", "")}
    func = getattr(call, "function", call)
    return {"name": getattr(func, "name", None), "arguments": getattr(func, "arguments", None),
            "id": getattr(call, "id", "")}


def _agent_name(agent: Any = None) -> str:
    from src.tools.agent_context import current_agent_name

    name = current_agent_name(default="")
    return name or str(getattr(agent, "name", "") or "unknown")


def _write(row: dict) -> None:
    global _seq
    with _lock:
        if _path is None:
            return
        _seq += 1
        row = {"seq": _seq, "time": time.time(), **row}
        with open(_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")


def _pending() -> list:
    if not hasattr(_tls, "pending"):
        _tls.pending = []
    return _tls.pending


# -- hooks ------------------------------------------------------------------------------------

def record_prompt(messages: list, prompt_tokens: int) -> None:
    """The prompt as actually sent (after the B80 trim), for the next record_generation on this thread."""
    if _path is None:
        return
    _tls.prompt = {"messages": [serialize_message(m) for m in messages], "prompt_tokens": int(prompt_tokens)}


def record_generation(message: Any, *, attempt: int, thinking: bool) -> None:
    """One raw generation attempt, BEFORE parse_tool_calls strips its think block."""
    if _path is None:
        return
    raw = _content_text(getattr(message, "content", None))
    usage = getattr(message, "token_usage", None)
    prompt = getattr(_tls, "prompt", None)
    _tls.prompt = None
    _pending().append({
        "attempt": int(attempt),
        "thinking_enabled": bool(thinking),
        "prompt": prompt,   # None: built on another thread (networked batch worker), not captured
        "raw_text": raw,
        **split_think(raw),
        "output_tokens": getattr(usage, "output_tokens", None),
    })


def on_action_step(step: Any, agent: Any = None) -> None:
    """smolagents step callback: pair the buffered generations with this step's tool calls and result."""
    if _path is None:
        return
    generations = list(_pending())
    _pending().clear()
    timing = getattr(step, "timing", None)
    error = getattr(step, "error", None)
    _write({
        "kind": "step",
        "agent": _agent_name(agent),
        "agent_step": getattr(step, "step_number", None),
        "start_time": getattr(timing, "start_time", None),
        "end_time": getattr(timing, "end_time", None),
        "generations": generations,
        "tool_calls": [{"name": getattr(c, "name", None), "arguments": getattr(c, "arguments", None),
                        "id": getattr(c, "id", "")} for c in (getattr(step, "tool_calls", None) or [])],
        "observations": getattr(step, "observations", None),
        "error": str(error) if error else None,
        "is_final_answer": bool(getattr(step, "is_final_answer", False)),
    })


def flush_unclaimed(agent: Any = None) -> None:
    """Write generations no ActionStep claimed, so nothing the model said is lost."""
    if _path is None or not _pending():
        return
    generations = list(_pending())
    _pending().clear()
    _write({"kind": "generation_without_step", "agent": _agent_name(agent), "generations": generations})


def record_summary(messages: list, raw_text: str, model_id: str | None = None) -> None:
    """One knowledge-base summary generation (its text prefixes the next agent's task)."""
    if _path is None:
        return
    _write({
        "kind": "summary",
        "agent": _agent_name(),
        "model_id": model_id,
        "prompt": {"messages": [serialize_message(m) for m in messages]},
        "raw_text": raw_text,
        **split_think(raw_text),
    })
