"""Keep every prompt inside the context budget before generation (B80).

On the 7960 (validate3_7960) networked prompts reached 41,050 to 61,710 tokens,
above Qwen3-32B's 40,960 maximum; those are the steps that hit CUDA OOM, and past
the maximum the model cannot use the context correctly on any hardware. The
networked strategy's ``max_context_tokens`` did not bound the agent's own
accumulated step memory.

Before a generation, if the prompt exceeds PROMPT_BUDGET_TOKENS (30,000, decided
2026-09-15), the oldest steps are replaced by one knowledge-base summary block.
Steps are removed as whole groups -- an assistant message together with the
observations that follow it -- so no observation is left without its call. The
system prompt, the task and the newest step are always kept. The agent's own
memory is not modified (traces keep the full history); only what is sent to the
model is trimmed, and it is re-trimmed on every step from the full memory.
"""
from __future__ import annotations

from typing import Any, Callable

# Qwen3-32B's max_position_embeddings. A prompt above this is a hard error (B82):
# beyond it the model cannot use the context correctly on any hardware.
HARD_PROMPT_LIMIT_TOKENS = 40960
_MIN_KEEP_CHARS = 400          # never truncate a message below this


def _role(message: Any) -> str:
    role = message.get("role") if isinstance(message, dict) else getattr(message, "role", "")
    return str(getattr(role, "value", role) or "")


def _split(messages: list) -> tuple[list, list[list]]:
    """(head, groups): head is everything before the first assistant message."""
    first = next((i for i, m in enumerate(messages) if _role(m) == "assistant"), len(messages))
    head, groups = list(messages[:first]), []
    for m in messages[first:]:
        if _role(m) == "assistant" or not groups:
            groups.append([m])
        else:
            groups[-1].append(m)
    return head, groups


def _text_of(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _with_text(message: Any, text: str) -> dict:
    role = _role(message)
    return {"role": role, "content": [{"type": "text", "text": text}]}


def truncate_middle(text: str, keep: int) -> str:
    """Head + tail of a message with an explicit elision marker in between."""
    if len(text) <= keep:
        return text
    head = keep // 2
    tail = keep - head
    return (text[:head] + f"\n...[{len(text) - keep} characters elided by the framework "
            "to fit the model's context window (B82); call read_design_knowledge for the "
            "full record]...\n" + text[-tail:])


def shrink_largest_message(messages: list) -> list | None:
    """Halve the longest message's text. None when nothing can usefully shrink."""
    sizes = [(len(_text_of(m)), i) for i, m in enumerate(messages)]
    size, idx = max(sizes) if sizes else (0, -1)
    if idx < 0 or size <= _MIN_KEEP_CHARS:
        return None
    out = list(messages)
    out[idx] = _with_text(messages[idx], truncate_middle(_text_of(messages[idx]), max(_MIN_KEEP_CHARS, size // 2)))
    return out


def summary_message(dropped: int, summary: str) -> dict:
    text = (
        f"[Context trimmed by the framework: {dropped} older steps removed so the prompt fits the "
        "model's window (B80). Design knowledge base summary of the work so far -- real tool "
        f"results, not claims. Call read_design_knowledge for details:]\n{summary}"
    )
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def trim_messages(messages: list, drop_groups: int, summary: str) -> list:
    """Messages with the oldest ``drop_groups`` steps replaced by one summary block."""
    head, groups = _split(messages)
    drop = max(0, min(int(drop_groups), len(groups) - 1))   # the newest step is always kept
    if drop == 0:
        return list(messages)
    kept = [m for g in groups[drop:] for m in g]
    return head + [summary_message(drop, summary)] + kept


def fit_to_budget(messages: list, count_tokens: Callable[[list], int], budget: int,
                  truncate_messages: bool = False) -> tuple[list, int]:
    """Return (messages, prompt tokens) with the prompt at or under ``budget`` if at all possible.

    ``count_tokens`` must count the prompt exactly as it will be generated (the model
    passes its own chat-template tokenization). Records max_prompt_tokens and
    context_trims in the knowledge base.
    """
    from src.tools.knowledge_base import get_kb, summarize

    kb = get_kb()
    n = int(count_tokens(messages))
    if kb is not None:
        kb.note_prompt_tokens(n)
    if n <= budget:
        return messages, n

    best: tuple[list, int] = (messages, n)      # identity kept: the caller reuses the built args
    _, groups = _split(messages)
    if len(groups) > 1:
        summary = summarize(kb, count_read=False, question="an agent whose older steps were trimmed "
                            "needs the state of the design work") if kb is not None else "No design work recorded yet."
        lo, hi, found = 1, len(groups) - 1, None
        while lo <= hi:                       # smallest number of dropped steps that fits
            mid = (lo + hi) // 2
            candidate = trim_messages(messages, mid, summary)
            c = int(count_tokens(candidate))
            if c <= budget:
                found, hi = (candidate, c), mid - 1
            else:
                lo = mid + 1
        if found is None:
            candidate = trim_messages(messages, len(groups) - 1, summary)
            found = (candidate, int(count_tokens(candidate)))
        best = found
        if kb is not None:
            kb.bump("context_trims")

    if best[1] > budget and truncate_messages:
        # B82: dropping whole steps cannot help when ONE message is itself enormous
        # (validate4 run 9 trimmed 78 times and still sent 45,707 tokens). Halve the
        # largest message, head+tail with an elision marker, until the prompt fits.
        current, tokens = best
        while tokens > budget:
            shrunk = shrink_largest_message(current)
            if shrunk is None:                # nothing left that can usefully shrink
                break
            current, tokens = shrunk, int(count_tokens(shrunk))
            if kb is not None:
                kb.bump("message_truncations")
        best = (current, tokens)

    if best[1] > budget and kb is not None:
        kb.bump("context_over_budget_untrimmable")
    return best
