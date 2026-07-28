"""Thread-safe token/cost accounting for a single combination run.

Why this exists: ``result.total_tokens`` was summed from the aggregated
``messages`` list, which is 0 for the networked structure (peer-thread messages
never land in that list) and under-counts elsewhere. This meter is fed by the
model wrapper (``CostTrackingLiteLLMModel``) on EVERY API call, from EVERY agent
and EVERY peer thread, so the totals are complete and comparable across all 8
combos. It also captures Anthropic prompt-cache tokens so the $ cost is the
actual BILLED cost, not a cache-blind over-estimate.

Usage:
    from src.llm.cost_meter import METER
    METER.reset()                 # at the start of a combination run
    ... run the agents ...
    snap = METER.snapshot()       # {calls, input_tokens, output_tokens,
                                  #  cache_read_tokens, cache_creation_tokens}
    cost = billed_cost_usd(snap, model_id)
"""
from __future__ import annotations

import threading
from typing import Any

# USD per 1,000,000 tokens. Anthropic bills cache-read at 0.1x input and
# cache-creation (write) at 1.25x input. VERIFY rates before publishing — these
# are the standard public tiers as of 2026-07.
PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4":   {"input": 3.0, "output": 15.0},
    "claude-opus-5":     {"input": 15.0, "output": 75.0},
    "claude-opus-4":     {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5":  {"input": 1.0, "output": 5.0},
}
_DEFAULT_RATE = {"input": 3.0, "output": 15.0}
_CACHE_READ_MULT = 0.1
_CACHE_WRITE_MULT = 1.25


class CostMeter:
    """Accumulates per-call token usage across threads for one run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.calls = 0
            self.input_tokens = 0        # total input (incl. cache), i.e. prompt_tokens
            self.output_tokens = 0
            self.cache_read_tokens = 0
            self.cache_creation_tokens = 0

    def record(self, prompt: int, completion: int, cache_read: int = 0, cache_creation: int = 0) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += int(prompt or 0)
            self.output_tokens += int(completion or 0)
            self.cache_read_tokens += int(cache_read or 0)
            self.cache_creation_tokens += int(cache_creation or 0)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self.calls,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_creation_tokens": self.cache_creation_tokens,
                "total_tokens": self.input_tokens + self.output_tokens,
            }


# Module-level singleton — shared by every model instance and peer thread.
METER = CostMeter()


def _rate(model_id: str) -> dict[str, float]:
    key = (model_id or "").split("/")[-1]  # strip "anthropic/" prefix
    return PRICING.get(key, _DEFAULT_RATE)


def extract_usage(raw_response: Any) -> tuple[int, int, int, int]:
    """Pull (prompt, completion, cache_read, cache_creation) from a litellm response.

    Defensive across litellm versions / providers: cache fields default to 0 when
    absent (e.g. caching disabled), reducing the cost formula to the no-cache case.
    """
    usage = getattr(raw_response, "usage", None)
    if usage is None and isinstance(raw_response, dict):
        usage = raw_response.get("usage")
    if usage is None:
        return 0, 0, 0, 0

    def g(obj: Any, name: str) -> int:
        v = getattr(obj, name, None)
        if v is None and isinstance(obj, dict):
            v = obj.get(name)
        return int(v or 0)

    prompt = g(usage, "prompt_tokens")
    completion = g(usage, "completion_tokens")
    # cache read: Anthropic via litellm -> usage.prompt_tokens_details.cached_tokens
    details = getattr(usage, "prompt_tokens_details", None) or (
        usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    )
    cache_read = g(details, "cached_tokens") if details is not None else 0
    # cache creation: Anthropic exposes cache_creation_input_tokens on usage
    cache_creation = g(usage, "cache_creation_input_tokens")
    return prompt, completion, cache_read, cache_creation


def billed_cost_usd(snap: dict[str, int], model_id: str) -> float:
    """Actual billed cost: non-cached input full, cache-read 0.1x, cache-write 1.25x."""
    r = _rate(model_id)
    non_cached = max(0, snap["input_tokens"] - snap["cache_read_tokens"] - snap["cache_creation_tokens"])
    cost = (
        non_cached * r["input"]
        + snap["cache_read_tokens"] * r["input"] * _CACHE_READ_MULT
        + snap["cache_creation_tokens"] * r["input"] * _CACHE_WRITE_MULT
        + snap["output_tokens"] * r["output"]
    ) / 1_000_000.0
    return round(cost, 4)
