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
import re
import logging
import os

from smolagents.models import Model

from src.config.loader import LLMConfig
from src.llm.reliability import ReliabilityConfig
from src.llm.thinking_model import ThinkingModel

logger = logging.getLogger(__name__)


# OpenAI's reasoning families (gpt-5*, gpt-6*, o1/o3/o4) reject `max_tokens` ("Use 'max_completion_tokens'
# instead"), any temperature but the default 1, and `stop` -- measured on aipexws8 2026-10-12 against
# openai/gpt-6.1-sol, which the pinned litellm 1.83 / smolagents 1.24 do not know (smolagents' own
# supports_stop_parameter stops at gpt-5*), so neither can translate or drop them itself.
_OPENAI_REASONING_RE = re.compile(r"^(openai/)?(responses/)?(gpt-[5-9]|o[1-9])")
# gpt-6* on /v1/chat/completions refuses function tools unless reasoning_effort is 'none', and then refuses
# 'none' ("Supported values are: 'low', 'medium', 'high', and 'xhigh'") -- so it must go through the
# Responses API, which litellm reaches with the "openai/responses/" prefix (same date, same model).
_OPENAI_RESPONSES_RE = re.compile(r"^(openai/)?(responses/)?gpt-[6-9]")


def _is_openai_reasoning(model_id: str) -> bool:
    return bool(_OPENAI_REASONING_RE.match((model_id or "").lower()))


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
        config.max_new_tokens, config.temperature, config.api_base, getattr(config, "enable_thinking", True),
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
    _configure_generation(config)
    load_summary_model(config)          # B82: summaries never run on the big model
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    model = _build_model(config)
    _MODEL_CACHE[key] = model
    return model


def _configure_generation(config: LLMConfig) -> None:
    """B82 slot cap and VRAM floor, plus the B83 batching worker's size and window."""
    from src.llm.batch_generation import configure as configure_batching
    from src.llm.generation_slots import configure

    configure(max_concurrent=getattr(config, "max_concurrent_generations", None),
              min_free_vram_gb=getattr(config, "min_free_vram_gb", None))
    configure_batching(max_batch_size=getattr(config, "max_batch_size", None),
                       gather_seconds=getattr(config, "batch_gather_seconds", None))


def load_summary_model(config: LLMConfig):
    """Load and register the SMALL model that writes knowledge-base summaries (B82).

    Pinned to one card (default cuda:0, the one with the most headroom -- `balanced`
    gives GPU 3 the most layers of the 32B and it OOMed first). Cached like the big
    model. Any failure is non-fatal: summaries fall back to the deterministic digest,
    never to the big model, and never to an API (B79).
    """
    from src.llm.generation_slots import get_summary_model, register_summary_model

    spec = dict(getattr(config, "summary_model", None) or {})
    model_id = spec.pop("model_id", None)
    if not model_id:
        return None
    if get_summary_model() is not None:
        return get_summary_model()
    cached = _MODEL_CACHE.get(("summary", model_id))
    if cached is None:
        device = spec.pop("device", "cuda:0")
        try:
            from smolagents import TransformersModel

            cached = TransformersModel(model_id=model_id, device_map={"": device},
                                       max_new_tokens=spec.pop("max_new_tokens", 400), **spec)
            _MODEL_CACHE[("summary", model_id)] = cached
        except Exception as exc:
            logger.warning("B82: summary model %s did not load (%s); summaries use the digest", model_id, exc)
            return None
    register_summary_model(cached, model_id=model_id)
    return cached


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

            @property
            def supports_stop_parameter(self) -> bool:  # type: ignore[override]
                if _is_openai_reasoning(self.model_id or ""):
                    return False
                return super().supports_stop_parameter

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
        openai_reasoning = _is_openai_reasoning(mid)
        kwargs = {
            "model_id": config.model_id,
            "api_key": config.api_key or _pick_litellm_api_key(config.model_id),
        }
        kwargs["max_completion_tokens" if openai_reasoning else "max_tokens"] = config.max_new_tokens
        if _OPENAI_RESPONSES_RE.match(mid):
            bare = re.sub(r"^(openai/)?(responses/)?", "", config.model_id)
            kwargs["model_id"] = f"openai/responses/{bare}"
            # llm.enable_thinking=False -> the lowest effort this family accepts ('none' is refused);
            # litellm 1.83 does not know the model, so the parameter is allowed through explicitly.
            kwargs["reasoning_effort"] = (config.reasoning_effort or "medium") if getattr(config, "enable_thinking", True) else "low"
            kwargs["allowed_openai_params"] = ["reasoning_effort"]
        if not any(s in mid for s in _NO_SAMPLING) and not openai_reasoning:
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
        # transformers 4.57 deprecates `torch_dtype` (it warned on every link) in favour of `dtype`;
        # smolagents still forwards `torch_dtype`, so pass None there and the value as `dtype`.
        "torch_dtype": None,
        "max_new_tokens": config.max_new_tokens,
        "reliability": reliability,
    }
    # Forwarded to from_pretrained. Chiefly `max_memory`, which is how a big
    # local model gets an EVEN multi-GPU split — see LLMConfig.model_kwargs.
    model_kwargs = dict(config.model_kwargs or {})
    if config.torch_dtype is not None:
        model_kwargs.setdefault("dtype", config.torch_dtype)
    if model_kwargs:
        kwargs["model_kwargs"] = model_kwargs
    model = ThinkingModel(**kwargs)
    # llm.enable_thinking (default True = the model's own default). Every call reads this flag unless a
    # strategy sets it per agent or per call.
    model.thinking_enabled = bool(getattr(config, "enable_thinking", True))
    return model
