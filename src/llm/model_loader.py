"""LLM model loader — creates a model from config.

Supports three backends:
- ``transformers``: Local ThinkingModel (TransformersModel subclass) with
  think-block stripping, robust JSON parsing, and reliability retry loop.
- ``vllm``: OpenAIServerModel connecting to a vLLM server. Tool calls
  arrive as structured objects — no parsing needed.
- ``litellm``: LiteLLMModel for cloud API providers (Anthropic Claude,
  OpenAI GPT, etc.) via the litellm library.
"""

import os

from smolagents.models import Model

from src.config.loader import LLMConfig
from src.llm.reliability import ReliabilityConfig
from src.llm.thinking_model import ThinkingModel


def load_model(config: LLMConfig) -> Model:
    """Create and return a model configured per the LLM config.

    Args:
        config: LLMConfig with model_id, backend, and generation params.

    Returns:
        A ready-to-use Model instance.
    """
    if config.backend == "litellm":
        from smolagents import LiteLLMModel

        return LiteLLMModel(
            model_id=config.model_id,
            api_key=config.api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            max_tokens=config.max_new_tokens,
            temperature=config.temperature,
        )

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
    return ThinkingModel(
        model_id=config.model_id,
        device_map=config.device_map,
        torch_dtype=config.torch_dtype,
        max_new_tokens=config.max_new_tokens,
        reliability=reliability,
    )
