"""Completion-signal detection — decides whether a piece of text actually
*asserts* that the task is finished.

Every strategy and handler used to test completion with a bare substring scan::

    if self._termination_keyword in content:   # "TASK_COMPLETE" in content
        return True

That is wrong in two ways, both of which were observed in real runs:

1. **Negation blindness.** The MDO-integrator agent routinely writes an explicit
   *negative* verdict — ``"Not TASK_COMPLETE: the simulation converged, but
   fuel_burned_kg violates its 15,000 kg limit by 3,373 kg"``. A substring scan
   reads that as "done".

2. **Forwarded context.** Since the 2026-07-30 cross-link-feedback change, link
   *k*'s task prompt embeds link *k−1*'s full integrator assessment. Any
   keyword inside that quoted block describes the PREVIOUS link, not this one.

Together these produced the phantom-link bug (``.claude/BUGS.md`` B2):
``StagedPipelineHandler._is_task_complete_in_context`` matched "Not
TASK_COMPLETE" inside the forwarded assessment, declared the pipeline already
finished, skipped all 7 stages and returned a single pass-through message —
links 1..N of every staged chain ran in 0.17 s with 0 tokens and silently
re-reported the previous link's fuel.

This module centralises the decision so all call sites share one behaviour.
"""

from __future__ import annotations

import re

DEFAULT_KEYWORD = "TASK_COMPLETE"

# Delimiters of the cross-link feedback block injected by
# ``stat_batch_runner.build_task_with_session``. Text between them quotes the
# PREVIOUS link and must never be read as this link's completion signal.
_FEEDBACK_START = "=== FEEDBACK FROM THE PREVIOUS DESIGN ITERATION"
_FEEDBACK_END_RE = re.compile(r"^={10,}\s*$", re.MULTILINE)

# Negation cues. If one of these appears between the start of the keyword's
# clause and the keyword itself, the mention is a denial, not an assertion.
_NEGATIONS = (
    "not", "no", "never", "cannot", "cant", "isnt", "arent", "wasnt",
    "dont", "doesnt", "didnt", "wont", "without", "unless", "neither",
    "nor", "fail", "failed", "incomplete", "unfinished", "premature",
    "avoid", "refrain", "yet",
)

# Characters that end the clause we scan backwards through.
_CLAUSE_BREAK = re.compile(r"[.;:!?\n]")

# Word characters used to decide whether a keyword hit is a standalone token
# rather than part of a longer identifier (e.g. ``TASK_COMPLETED_AT``).
_WORD_RE = re.compile(r"\w")


def strip_forwarded_feedback(text: str) -> str:
    """Return *text* with the cross-link feedback block removed.

    The block quotes the previous chain link's assessment; nothing inside it
    describes the current link's state. Returns *text* unchanged when no block
    is present (the common single-link case).
    """
    if not text:
        return text or ""
    start = text.find(_FEEDBACK_START)
    if start == -1:
        return text
    end_match = _FEEDBACK_END_RE.search(text, start + len(_FEEDBACK_START))
    end = end_match.end() if end_match else len(text)
    return text[:start] + text[end:]


def _clause_before(text: str, index: int) -> str:
    """Return the clause preceding *index* — back to the nearest sentence or
    line break, whichever is closer."""
    start = 0
    for m in _CLAUSE_BREAK.finditer(text, 0, index):
        start = m.end()
    return text[start:index]


def _is_negated(text: str, index: int) -> bool:
    """Return True when the keyword occurrence at *index* is denied by a
    negation cue in its own clause."""
    clause = _clause_before(text, index).lower()
    # Normalise apostrophes/hyphens so "isn't" and "is not" both reduce to
    # tokens present in _NEGATIONS.
    words = re.findall(r"[a-z]+", clause.replace("'", ""))
    return any(w in _NEGATIONS for w in words)


def _is_standalone(text: str, index: int, keyword: str) -> bool:
    """Return True when the hit is a whole token, not a substring of a longer
    identifier (so ``TASK_COMPLETED_AT`` does not count as ``TASK_COMPLETE``)."""
    before = text[index - 1] if index > 0 else ""
    after_i = index + len(keyword)
    after = text[after_i] if after_i < len(text) else ""
    if before and _WORD_RE.match(before):
        return False
    if after and _WORD_RE.match(after):
        return False
    return True


def signals_completion(
    text: str | None,
    keyword: str = DEFAULT_KEYWORD,
    *,
    strip_feedback: bool = True,
) -> bool:
    """Return True only when *text* AFFIRMATIVELY signals task completion.

    A mention counts when it is a standalone token and is not negated within
    its own clause. ``"Not TASK_COMPLETE: fuel still violates the limit"``
    returns False; ``"All constraints satisfied. TASK_COMPLETE"`` returns True.

    Args:
        text: the content to inspect.
        keyword: the termination keyword (per-strategy configurable).
        strip_feedback: drop the forwarded previous-link feedback block first.
            Leave True for anything derived from a task prompt. Set False only
            when *text* is known to be a single agent's own fresh output.
    """
    if not text or not keyword:
        return False
    if strip_feedback:
        text = strip_forwarded_feedback(text)

    idx = text.find(keyword)
    while idx != -1:
        if _is_standalone(text, idx, keyword) and not _is_negated(text, idx):
            return True
        idx = text.find(keyword, idx + 1)
    return False


def signals_blackboard_done(text: str | None, *, strip_feedback: bool = True) -> bool:
    """Return True when the networked blackboard's ``MarkTaskDone`` marker is
    present in *text* — ``"[STATUS] task_complete"``.

    Kept separate from :func:`signals_completion` because this marker is
    written programmatically (never negated prose), but it is still subject to
    the forwarded-feedback problem.
    """
    if not text:
        return False
    if strip_feedback:
        text = strip_forwarded_feedback(text)
    return "[status] task_complete" in text.lower()
