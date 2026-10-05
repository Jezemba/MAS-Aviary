"""How a stage agent's messages are assembled: the framework's layering, or verbatim (Jessica, 2026-10-04).

Framework mode (default, unchanged): smolagents' tool-calling template wraps the base instructions and
"Your role: <role>"; each stage's task is STAGE/TASK + the runner's task text + earlier stages'
outputs, prefixed with the knowledge-base handoff and smolagents' "New task:".

Verbatim mode (``stage_defaults.prompt_assembly: verbatim`` in the agents YAML):
  * the system message is the base instructions exactly as written -- no library template around it;
  * each stage's message is that stage's prompt (its role) exactly as written, followed by the
    previous stage's DESIGN_STATE, which the prompts say each stage receives;
  * no runner task text, no knowledge-base block, no "New task:" prefix.
The tool definitions still reach the model through its chat template (Qwen renders them as a tools
block), which is how tool calling works; they are not repeated as prose.
"""
from __future__ import annotations

from typing import Any

FRAMEWORK = "framework"
VERBATIM = "verbatim"
_mode = FRAMEWORK


def set_mode(mode: str | None) -> None:
    global _mode
    _mode = VERBATIM if str(mode or "").strip().lower() == VERBATIM else FRAMEWORK


def is_verbatim() -> bool:
    return _mode == VERBATIM


def verbatim_prompt_templates() -> dict:
    """smolagents' tool-calling templates with the system prompt reduced to the instructions alone."""
    import importlib.resources

    import yaml

    templates = yaml.safe_load(
        importlib.resources.files("smolagents.prompts").joinpath("toolcalling_agent.yaml").read_text())
    templates["system_prompt"] = "{{custom_instructions}}"
    return templates


def verbatim_stage_input(stage_prompt: str, previous_outputs: list) -> str:
    """The stage prompt as written, then the previous stage's DESIGN_STATE (if there is one)."""
    text = (stage_prompt or "").strip("\n")
    if previous_outputs:
        prev_name, prev_content, _ = previous_outputs[-1]
        text += f"\n\nDESIGN_STATE from the previous stage ({prev_name}):\n{prev_content}"
    return text


def make_agent_class():
    """ToolCallingAgent whose task message is the task alone (no "New task:" prefix)."""
    from smolagents import ToolCallingAgent
    from smolagents.memory import TaskStep
    from smolagents.models import ChatMessage, MessageRole

    class VerbatimToolCallingAgent(ToolCallingAgent):
        def write_memory_to_messages(self, summary_mode: bool = False) -> list[Any]:
            messages = self.memory.system_prompt.to_messages(summary_mode=summary_mode)
            for step in self.memory.steps:
                if isinstance(step, TaskStep):
                    messages.append(ChatMessage(role=MessageRole.USER,
                                                content=[{"type": "text", "text": step.task}]))
                else:
                    messages.extend(step.to_messages(summary_mode=summary_mode))
            return messages

    return VerbatimToolCallingAgent
