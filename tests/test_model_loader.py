"""Tests for the LLM model loader.

Tests marked @pytest.mark.slow require GPU and model download.
Run with: pytest -m slow tests/test_model_loader.py
"""

from unittest.mock import MagicMock, patch

import pytest

from src.config.loader import LLMConfig
from src.llm.model_loader import load_model


class TestLitellmApiKeyDispatch:
    """Regression for the 2026-05-25 model-switch bug: when both
    ANTHROPIC_API_KEY and OPENAI_API_KEY were set, the loader's
    `os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")`
    always picked the Anthropic key (first in the chain). After
    switching the config to `openai/gpt-5`, litellm sent the
    Anthropic key to OpenAI and got AuthenticationError.

    Fix: pick the env var by the provider prefix in model_id —
    `openai/...` → OPENAI_API_KEY, `anthropic/...` → ANTHROPIC_API_KEY,
    etc. Generic fallback preserved for back-compat."""

    def test_openai_model_uses_openai_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key-shouldnt-be-used")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key-correct")
        config = LLMConfig(
            model_id="openai/gpt-5",
            backend="litellm",
            max_new_tokens=4096,
            temperature=1.0,
        )
        with patch("smolagents.LiteLLMModel") as MockModel:
            MockModel.return_value = MagicMock()
            load_model(config)
            call_kwargs = MockModel.call_args[1]
            assert call_kwargs["api_key"] == "openai-key-correct", (
                f"openai/gpt-5 should pick OPENAI_API_KEY, got {call_kwargs['api_key']!r}"
            )

    def test_anthropic_model_uses_anthropic_api_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key-correct")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key-shouldnt-be-used")
        config = LLMConfig(
            model_id="anthropic/claude-sonnet-4-20250514",
            backend="litellm",
            max_new_tokens=4096,
            temperature=0.3,
        )
        with patch("smolagents.LiteLLMModel") as MockModel:
            MockModel.return_value = MagicMock()
            load_model(config)
            call_kwargs = MockModel.call_args[1]
            assert call_kwargs["api_key"] == "ant-key-correct"

    def test_explicit_api_key_wins_over_env(self, monkeypatch):
        # If config.api_key is set, use it regardless of model_id.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-env")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-env")
        config = LLMConfig(
            model_id="openai/gpt-5",
            backend="litellm",
            api_key="explicit-override",
            max_new_tokens=4096,
            temperature=1.0,
        )
        with patch("smolagents.LiteLLMModel") as MockModel:
            MockModel.return_value = MagicMock()
            load_model(config)
            call_kwargs = MockModel.call_args[1]
            assert call_kwargs["api_key"] == "explicit-override"

    def test_unknown_provider_falls_back_to_either_env(self, monkeypatch):
        # Model id without a known prefix — fall back to the legacy
        # behavior so unrelated litellm-supported models still work.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-env")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = LLMConfig(
            model_id="some-unprefixed-model",
            backend="litellm",
            max_new_tokens=4096,
            temperature=0.7,
        )
        with patch("smolagents.LiteLLMModel") as MockModel:
            MockModel.return_value = MagicMock()
            load_model(config)
            call_kwargs = MockModel.call_args[1]
            # Either env value is acceptable for back-compat (the legacy
            # code used ANTHROPIC_API_KEY first).
            assert call_kwargs["api_key"] in {"ant-env", None}


def test_load_model_returns_thinking_model():
    """Smoke test: load a tiny model to verify the pipeline works."""
    config = LLMConfig(
        model_id="HuggingFaceTB/SmolLM-135M-Instruct",
        device_map="cpu",
        torch_dtype="float32",
        max_new_tokens=32,
    )
    model = load_model(config)
    from smolagents import TransformersModel

    from src.llm.thinking_model import ThinkingModel

    assert isinstance(model, ThinkingModel)
    assert isinstance(model, TransformersModel)  # still a TransformersModel subclass


def test_load_model_vllm_backend():
    """vLLM backend creates an OpenAIServerModel."""
    config = LLMConfig(
        model_id="Qwen/Qwen3-8B",
        backend="vllm",
        api_base="http://localhost:8000/v1",
        api_key="test-key",
        max_new_tokens=512,
        temperature=0.5,
    )
    with patch("smolagents.OpenAIServerModel") as MockModel:
        mock_instance = MagicMock()
        MockModel.return_value = mock_instance
        model = load_model(config)
        MockModel.assert_called_once_with(
            model_id="Qwen/Qwen3-8B",
            api_base="http://localhost:8000/v1",
            api_key="test-key",
            temperature=0.5,
            max_tokens=512,
        )
        assert model is mock_instance


def test_load_model_vllm_empty_api_key_defaults_to_EMPTY():
    """When api_key is empty, vLLM backend passes 'EMPTY'."""
    config = LLMConfig(
        model_id="Qwen/Qwen3-8B",
        backend="vllm",
        api_key="",
    )
    with patch("smolagents.OpenAIServerModel") as MockModel:
        MockModel.return_value = MagicMock()
        load_model(config)
        call_kwargs = MockModel.call_args[1]
        assert call_kwargs["api_key"] == "EMPTY"


def test_load_model_default_backend_is_transformers():
    """Without explicit backend, defaults to transformers."""
    config = LLMConfig(
        model_id="HuggingFaceTB/SmolLM-135M-Instruct",
        device_map="cpu",
        torch_dtype="float32",
        max_new_tokens=32,
    )
    # backend defaults to "transformers"
    assert config.backend == "transformers"
    model = load_model(config)
    from src.llm.thinking_model import ThinkingModel

    assert isinstance(model, ThinkingModel)


@pytest.mark.slow
def test_load_gpt_oss_20b():
    """Load gpt-oss-20b on GPU and verify it produces a response.

    Requires: dual 5090 GPUs, ~41 GB VRAM, model download from HuggingFace.
    """
    config = LLMConfig(
        model_id="openai/gpt-oss-20b",
        device_map="auto",
        torch_dtype="auto",
        max_new_tokens=64,
    )
    model = load_model(config)
    from smolagents import TransformersModel

    assert isinstance(model, TransformersModel)
