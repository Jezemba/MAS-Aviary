"""One lock for every generation on the local model, and the local-model registry (B81/B80).

WHY ONE LOCK (Jessica, 2026-09-15)

The runner holds a single Qwen3-32B instance. Networked peers (concurrent_blackboard)
call it from several threads at once, and before this there was no lock at all, so
generations overlapped on the same GPUs -- the setting in which the networked OOMs
occurred (B70/B80). The knowledge-base summary is a second use of the same model and
must never overlap an agent's generation either. Every ThinkingModel.generate and
every summary call now takes GENERATION_LOCK, so exactly one generation touches the
GPUs at a time. Peers still run concurrently and interleave their tool calls; only
their LLM steps queue. It is re-entrant so a summary requested while building a
prompt (the B80 context trim) can run inside the generation that asked for it.

WHY A REGISTRY THAT ONLY ACCEPTS A LOCAL MODEL

Summaries must come from the already-loaded local model and never from a hosted API
(B79: paid calls were once made by accident). register_local_model accepts only a
smolagents TransformersModel -- weights in this process -- or an object explicitly
marked as a test stub. Anything else (LiteLLM, OpenAI-compatible servers) is refused
and summaries fall back to the deterministic digest.
"""
from __future__ import annotations

import threading
from typing import Any

GENERATION_LOCK = threading.RLock()

_local_model: Any = None


def register_local_model(model: Any) -> bool:
    """Make ``model`` available for knowledge-base summaries. Returns whether it was accepted."""
    global _local_model
    from smolagents.models import TransformersModel

    if isinstance(model, TransformersModel) or getattr(model, "_avion_test_stub", False):
        _local_model = model
        return True
    return False


def get_local_model() -> Any:
    return _local_model


def clear_local_model() -> None:
    global _local_model
    _local_model = None
