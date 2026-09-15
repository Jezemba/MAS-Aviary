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


def fit_to_budget(messages: list, count_tokens: Callable[[list], int], budget: int) -> tuple[list, int]:
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

    _, groups = _split(messages)
    if len(groups) <= 1:
        if kb is not None:
            kb.bump("context_over_budget_untrimmable")
        return messages, n

    summary = summarize(kb, count_read=False, question="an agent whose older steps were trimmed "
                        "needs the state of the design work") if kb is not None else "No design work recorded yet."
    lo, hi, best = 1, len(groups) - 1, None
    while lo <= hi:                       # smallest number of dropped steps that fits
        mid = (lo + hi) // 2
        candidate = trim_messages(messages, mid, summary)
        c = int(count_tokens(candidate))
        if c <= budget:
            best, hi = (candidate, c), mid - 1
        else:
            lo = mid + 1
    if best is None:
        candidate = trim_messages(messages, len(groups) - 1, summary)
        best = (candidate, int(count_tokens(candidate)))
        if kb is not None:
            kb.bump("context_over_budget_untrimmable")
    if kb is not None:
        kb.bump("context_trims")
    return best
