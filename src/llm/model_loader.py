"""LLM model loader — creates a model from config.

Supports three backends:
- ``transformers``: Local ThinkingModel (TransformersModel subclass) with
  think-block stripping, robust JSON parsing, and reliability retry loop.
- ``vllm``: OpenAIServerModel connecting to a vLLM server. Tool calls
  arrive as structured objects — no parsing needed.
- ``litellm``: LiteLLMModel for cloud API providers (Anthropic Claude,
  OpenAI GPT, etc.) via the litellm library.
"""

import json
import os

from smolagents.models import Model

from src.config.loader import LLMConfig
from src.llm.reliability import ReliabilityConfig
from src.llm.thinking_model import ThinkingModel


def _pick_litellm_api_key(model_id: str) -> str | None:
    """Choose the env var that matches a litellm model_id's provider
    prefix. Until 2026-05-25 we always tried ANTHROPIC_API_KEY first
    and OPENAI_API_KEY as fallback — but when BOTH are set, the
    Anthropic key wins and gets sent to OpenAI's endpoint on every
    `openai/...` call, causing AuthenticationError. Map by prefix
    instead."""
    if model_id.startswith("openai/"):
        return os.environ.get("OPENAI_API_KEY")
    if model_id.startswith("anthropic/"):
        return os.environ.get("ANTHROPIC_API_KEY")
    if model_id.startswith("gemini/"):
        return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    # Unknown / unprefixed model_id — keep the legacy fallback chain
    # so older setups don't break.
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")


# Process-global cache of loaded models, keyed by the config that produced them.
#
# Coordinator.from_config calls load_model() once PER RUN, so a 16-run sweep
# loaded the same 17.9 GB local model 16 times. Beyond the waste, it caused a
# hard failure (.claude/BUGS.md B16): stat_batch_runner's _run_with_timeout
# abandons a timed-out worker THREAD while it is still alive, so that thread
# keeps the previous model resident, and the next run's load hit
#   ValueError: Some modules are dispatched on the CPU or the disk
# killing both networked_graph_routed runs at 0 turns / 0 seconds.
#
# The weights are identical across runs and carry no per-run state, so reusing
# one instance removes the double allocation entirely. Agents within a run
# already share a single model, so cross-run sharing is the same situation.
_MODEL_CACHE: dict[tuple, Model] = {}


def _cache_key(config: LLMConfig) -> tuple:
    return (
        config.backend, config.model_id, config.device_map, config.torch_dtype,
        config.max_new_tokens, config.temperature, config.api_base,
        json.dumps(config.reliability or {}, sort_keys=True),
        json.dumps(config.model_kwargs or {}, sort_keys=True, default=str),
    )


def clear_model_cache() -> None:
    """Drop cached models (frees GPU memory when nothing else holds a reference)."""
    _MODEL_CACHE.clear()


def load_model(config: LLMConfig) -> Model:
    """Create and return a model configured per the LLM config.

    Local (transformers) models are cached and REUSED across calls — see
    _MODEL_CACHE. Cloud backends are cached too, but they are cheap clients, so
    the benefit there is only avoiding redundant construction.

    Args:
        config: LLMConfig with model_id, backend, and generation params.

    Returns:
        A ready-to-use Model instance.
    """
    key = _cache_key(config)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    model = _build_model(config)
    _MODEL_CACHE[key] = model
    return model


def _build_model(config: LLMConfig) -> Model:
    """Construct a fresh model instance (uncached)."""
    if config.backend == "litellm":
        from smolagents import LiteLLMModel

        # Opus 5 / 4.8 / 4.7 (and Sonnet 5) REJECT temperature/top_p/top_k with a
        # 400. drop_params makes litellm silently strip params a model doesn't
        # accept, so the same request works across Sonnet and Opus.
        import litellm
        litellm.drop_params = True

        from src.llm.cost_meter import METER, extract_usage

        class CostTrackingLiteLLMModel(LiteLLMModel):
            """LiteLLMModel that records real billed usage into the global METER.

            Fires on every API call from every agent AND every networked peer
            thread (the meter is thread-safe), so token/cost totals are complete
            and comparable across all combos — unlike the messages-based sum that
            returned 0 for the networked structure.
            """

            def generate(self, *args, **kwargs):  # type: ignore[override]
                msg = super().generate(*args, **kwargs)
                try:
                    raw = getattr(msg, "raw", None)
                    if raw is not None:
                        prompt, completion, cread, ccreate = extract_usage(raw)
                        METER.record(prompt, completion, cread, ccreate)
                except Exception:  # never let accounting break a run
                    pass
                return msg

        # Opus 5 / 4.8 / 4.7, Fable 5, Sonnet 5 REMOVED sampling params — sending
        # temperature returns 400 ("deprecated for this model"), and litellm's
        # drop_params can't help because it doesn't yet know these new model IDs.
        # So omit temperature entirely for those; keep it for models that accept it.
        _NO_SAMPLING = ("opus-5", "opus-4-8", "opus-4-7", "fable-5", "mythos-5", "sonnet-5")
        mid = (config.model_id or "").lower()
        kwargs = {
            "model_id": config.model_id,
            "api_key": config.api_key or _pick_litellm_api_key(config.model_id),
            "max_tokens": config.max_new_tokens,
        }
        if not any(s in mid for s in _NO_SAMPLING):
            kwargs["temperature"] = config.temperature
        return CostTrackingLiteLLMModel(**kwargs)

    if config.backend == "vllm":
        from smolagents import OpenAIServerModel

        return OpenAIServerModel(
            model_id=config.model_id,
            api_base=config.api_base,
            api_key=config.api_key or "EMPTY",
            temperature=config.temperature,
            max_tokens=config.max_new_tokens,
        )

    # Default: transformers backend.
    reliability = ReliabilityConfig(**(config.reliability or {}))
    kwargs: dict = {
        "model_id": config.model_id,
        "device_map": config.device_map,
        "torch_dtype": config.torch_dtype,
        "max_new_tokens": config.max_new_tokens,
        "reliability": reliability,
    }
    # Forwarded to from_pretrained. Chiefly `max_memory`, which is how a big
    # local model gets an EVEN multi-GPU split — see LLMConfig.model_kwargs.
    if config.model_kwargs:
        kwargs["model_kwargs"] = dict(config.model_kwargs)
    return ThinkingModel(**kwargs)
