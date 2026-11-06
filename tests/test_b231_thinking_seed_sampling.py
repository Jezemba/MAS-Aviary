"""B231 (Jessica, 2026-10-29 / 2026-11-06): thinking OFF for every agent, LLM seed 42, the configured sampling applied.

B216: orchestrated workers were switched ON regardless of the config. B230: the code default was ON, so a launch
without MAS_AVIARY_ENABLE_THINKING=0 thought. B226: `temperature` was never passed to the transformers backend and
nothing seeded the generation. No weights are loaded here: ThinkingModel is stubbed.
"""
from __future__ import annotations

import pytest
from smolagents import ToolCallingAgent
from smolagents.models import Model

from src.config.loader import AppConfig, LLMConfig, _apply_env_overrides, load_config
from src.coordination.strategy import CoordinationAction
from src.coordination.strategies.orchestrated import OrchestratedStrategy
from src.llm import model_loader


class _Stub:
    """Stands in for ThinkingModel: records its kwargs, loads nothing."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.thinking_enabled = True


@pytest.fixture
def stub_loader(monkeypatch):
    monkeypatch.setattr(model_loader, "ThinkingModel", _Stub)
    monkeypatch.setattr(model_loader, "_configure_generation", lambda config: None)
    monkeypatch.setattr(model_loader, "load_summary_model", lambda config: None)
    monkeypatch.setattr(model_loader, "_MODEL_CACHE", {})
    seeds = []
    monkeypatch.setattr(model_loader, "seed_generation", lambda seed: seeds.append(seed) or True)
    return seeds


# ---- defaults ---------------------------------------------------------------------------------------------

def test_defaults_are_thinking_off_and_seed_42(monkeypatch):
    monkeypatch.delenv("MAS_AVIARY_ENABLE_THINKING", raising=False)
    monkeypatch.delenv("MAS_AVIARY_LLM_SEED", raising=False)
    llm = _apply_env_overrides(AppConfig()).llm
    assert llm.enable_thinking is False and llm.seed == 42


def test_seed_env_override(monkeypatch):
    monkeypatch.setenv("MAS_AVIARY_LLM_SEED", "7")
    assert _apply_env_overrides(AppConfig()).llm.seed == 7
    monkeypatch.setenv("MAS_AVIARY_LLM_SEED", "none")
    assert _apply_env_overrides(AppConfig()).llm.seed is None


def test_study_config_runs_without_thinking_at_its_temperature(monkeypatch):
    monkeypatch.delenv("MAS_AVIARY_ENABLE_THINKING", raising=False)
    monkeypatch.delenv("MAS_AVIARY_LLM_SEED", raising=False)
    llm = load_config("config/mdo_f25_run_qwen32b_2gpu.yaml").llm
    assert llm.enable_thinking is False and llm.seed == 42
    assert (llm.temperature, llm.top_p, llm.top_k) == (pytest.approx(0.7), pytest.approx(0.8), 20)   # Qwen3 card


# ---- sampling reaches generate() --------------------------------------------------------------------------

def test_sampling_kwargs():
    assert model_loader.sampling_kwargs(LLMConfig(temperature=0.3)) == {"do_sample": True, "temperature": 0.3}
    assert model_loader.sampling_kwargs(LLMConfig(temperature=0.0)) == {"do_sample": False}
    assert model_loader.sampling_kwargs(LLMConfig(temperature=0.7, top_p=0.8, top_k=20)) == {
        "do_sample": True, "temperature": 0.7, "top_p": 0.8, "top_k": 20}


def test_built_model_carries_the_sampling_and_records_it(stub_loader):
    model = model_loader._build_model(LLMConfig(model_id="m", temperature=0.3))
    assert model.kwargs["temperature"] == 0.3 and model.kwargs["do_sample"] is True
    assert model.generation_settings == {"do_sample": True, "temperature": 0.3, "seed": 42,
                                         "enable_thinking": False}
    assert model.thinking_enabled is False


def test_smolagents_passes_model_kwargs_to_every_generate_call():
    """What ThinkingModel inherits: Model.kwargs override per-call kwargs in the completion arguments."""
    model = Model(model_id="m", do_sample=True, temperature=0.3)
    out = model._prepare_completion_kwargs(messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                                           temperature=0.9)
    assert out["temperature"] == 0.3 and out["do_sample"] is True


def test_sampling_changes_are_a_different_cached_model():
    assert model_loader._cache_key(LLMConfig(model_id="m", top_p=0.8)) != model_loader._cache_key(
        LLMConfig(model_id="m"))


# ---- seed ------------------------------------------------------------------------------------------------

def test_every_load_reseeds_and_resets_the_thinking_flag(stub_loader):
    config = LLMConfig(model_id="m")
    first = model_loader.load_model(config)
    first.thinking_enabled = True                 # what a previous link's per-agent toggle could leave behind
    second = model_loader.load_model(config)
    assert second is first                        # cached, not reloaded
    assert second.thinking_enabled is False       # back to the configured setting
    assert stub_loader == [42, 42]                # seeded on the load AND on the reuse


def test_seed_generation_makes_sampling_repeatable():
    torch = pytest.importorskip("torch")
    assert model_loader.seed_generation(42) is True
    a = torch.rand(4)
    model_loader.seed_generation(42)
    assert torch.equal(a, torch.rand(4))
    assert model_loader.seed_generation(None) is False


# ---- B216: orchestrated workers follow the config ---------------------------------------------------------

class _Dummy(Model):
    def __init__(self, thinking_configured):
        super().__init__(model_id="dummy")
        self.thinking_enabled = thinking_configured
        self.generation_settings = {"enable_thinking": thinking_configured}

    def generate(self, *args, **kwargs):  # never called here
        raise AssertionError


def _strategy(model):
    orch = ToolCallingAgent(tools=[], model=model, name="orchestrator", max_steps=2, add_base_tools=False)
    config = {"orchestrated": {"authority_mode": "orchestrator", "information_mode": "transparent",
                               "lifecycle_mode": "setup_only", "max_agents": 4, "max_orchestrator_turns": 3,
                               "worker_max_steps": 2},
              "termination": {"keyword": "TASK_COMPLETE", "max_turns": 10}, "_worker_tools": {}}
    s = OrchestratedStrategy()
    s.initialize({"orchestrator": orch}, config)
    return s


@pytest.mark.parametrize("configured", [False, True])
def test_worker_thinking_follows_the_config_orchestrator_always_off(monkeypatch, configured):
    model = _Dummy(configured)
    s = _strategy(model)
    monkeypatch.setattr(s, "_creation_step", lambda history, state: CoordinationAction(
        action_type="invoke_agent", agent_name="worker_1", input_context="x"))
    s.next_step([], {"task": "t"})
    assert model.thinking_enabled is configured          # B216: was forced True for every worker
    monkeypatch.setattr(s, "_creation_step", lambda history, state: CoordinationAction(
        action_type="invoke_agent", agent_name="orchestrator", input_context="x"))
    s.next_step([], {"task": "t"})
    assert model.thinking_enabled is False
    # a later worker turn still gets the CONFIGURED value, not the orchestrator's OFF
    monkeypatch.setattr(s, "_creation_step", lambda history, state: CoordinationAction(
        action_type="invoke_agent", agent_name="worker_2", input_context="x"))
    s.next_step([], {"task": "t"})
    assert model.thinking_enabled is configured
