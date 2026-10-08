"""How a stage agent's messages are assembled: the framework's layering, or verbatim (Jessica, 2026-10-04).

Framework mode (default, unchanged): smolagents' tool-calling template wraps the base instructions and
"Your role: <role>"; each stage's task is STAGE/TASK + the runner's task text + earlier stages'
outputs, prefixed with the knowledge-base handoff and smolagents' "New task:".

Verbatim mode (``stage_defaults.prompt_assembly: verbatim`` in the agents YAML):
  * the system message is the base instructions exactly as written -- no library template around it;
  * each stage's message is that stage's prompt (its role) exactly as written, followed by the
    previous stage's DESIGN_STATE, which the prompts say each stage receives;
  * no runner task text, no knowledge-base block, no "New task:" prefix;
  * on a later iteration (chain link 1+), carry_over.json supplies what the runner's task text
    used to: the first stage also receives the previous iteration's mdo_integrator DESIGN_STATE
    and the STARTING DESIGN, and the baseline CPACS path in the prompts becomes the carried one.
The tool definitions still reach the model through its chat template (Qwen renders them as a tools
block), which is how tool calling works; they are not repeated as prose.
"""
from __future__ import annotations

import re
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


def read_carry_over() -> dict | None:
    """carry_over.json beside this link's knowledge base: what a new iteration inherits.

    Written by the runner for link 1 onward (scripts/stat_batch_runner.py write_carry_over);
    absent on the first link.
    """
    import json
    import os

    from src.tools.knowledge_base import _run_config

    kb_path = (_run_config or {}).get("path")
    if not kb_path:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(kb_path)), "carry_over.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


_RECOMMENDATION_KEYS = ("RECOMMENDATION_TO", "RECOMMENDED_CHANGE", "MDO_REASONING")


def parse_recommendation(integrator_text: str | None) -> dict:
    """RECOMMENDATION_TO / RECOMMENDED_CHANGE / MDO_REASONING from an mdo_integrator DESIGN_STATE.

    The integrator writes it either as `KEY: value` lines or as JSON (both seen in the
    v2verbatim run); missing keys are left out.
    """
    import json
    import re

    text = integrator_text or ""
    found: dict = {}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _RECOMMENDATION_KEYS and not isinstance(v, (dict, list)) and k not in found:
                    found[k] = str(v).strip()
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    start = text.find("{")
    if start >= 0:
        try:
            walk(json.JSONDecoder().raw_decode(text[start:])[0])
        except ValueError:
            pass
    for key in _RECOMMENDATION_KEYS:
        if key in found:
            continue
        # Almost-JSON is common (v2b143 link 0 wrote "CONSTRAINTS_PASSED": 0/2, which no JSON
        # parser accepts), so match the quoted form directly before the KEY: value line form.
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if m:
            found[key] = m.group(1).replace('\\"', '"').strip()
            continue
        m = re.search(rf"^\s*{key}\s*:\s*(.+)$", text, flags=re.M)
        if m:
            found[key] = m.group(1).strip().strip("'\"")
    return found


def _stage_key(name: str | None) -> str:
    return re.sub(r"[^a-z]+", "_", str(name or "").lower()).strip("_")


def _fmt(values: dict) -> str:
    return ", ".join(f"{k} {v:.6g}" if isinstance(v, float) else f"{k} {v}" for k, v in values.items())


def _starting_design_block(start: dict) -> str:
    """The previous iteration's design_space (design.json), in the prompt table's terms (2026-10-08)."""
    lines = ["STARTING DESIGN for this iteration (the previous iteration's design, from its design.json):"]
    if start.get("CPACS_FILE"):
        lines.append(f"  CPACS_FILE: {start['CPACS_FILE']}")
    for uid, values in (start.get("geometry") or {}).items():
        if values:
            lines.append(f"  {uid}: {_fmt(values)}")
    if start.get("engine"):
        lines.append("  Engine inputs last set (a new pycycle session starts at its defaults; set them again "
                     f"to keep them): {_fmt(start['engine'])}")
    if start.get("mission"):
        lines.append(f"  Mission: {_fmt(start['mission'])}")
    # carry_over files written before 2026-10-08 hold flat aviary values; show them as they are.
    for k, v in start.items():
        if k not in ("CPACS_FILE", "geometry", "engine", "mission"):
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def verbatim_stage_input(stage_prompt: str, previous_outputs: list, carry_over: dict | None = None,
                         stage_name: str | None = None) -> str:
    """The stage prompt as written, then the previous stage's DESIGN_STATE (if there is one).

    On a later iteration (carry_over given) every stage's prompt names the carried-forward
    CPACS instead of the baseline file, and the first stage also receives the previous
    iteration's mdo_integrator DESIGN_STATE and the design this iteration starts from.
    """
    text = (stage_prompt or "").strip("\n")
    start = dict((carry_over or {}).get("starting_design") or {})
    baseline, cpacs = (carry_over or {}).get("baseline_cpacs"), start.get("CPACS_FILE")
    if cpacs and cpacs != baseline:
        # The prompts name the baseline by its absolute path on the run machine; match the file,
        # not the machine (aipexws3 and aipexws5 differ only in the home directory).
        text = re.sub(r"\S*/mass-mcp/tests/fixtures/D150_simple\.xml", lambda _m: cpacs, text)
    if previous_outputs:
        prev_name, prev_content, _ = previous_outputs[-1]
        text += f"\n\nDESIGN_STATE from the previous stage ({prev_name}):\n{prev_content}"
        # The stage the previous iteration's integrator addressed also gets its recommendation
        # (Jessica, 2026-10-05). The first stage already receives the whole DESIGN_STATE below.
        rec = parse_recommendation((carry_over or {}).get("previous_integrator_design_state"))
        if rec.get("RECOMMENDATION_TO") and stage_name and _stage_key(rec["RECOMMENDATION_TO"]) == _stage_key(stage_name):
            lines = "\n".join(f"  {k}: {rec[k]}" for k in _RECOMMENDATION_KEYS if k in rec)
            text += f"\n\nRECOMMENDATION from the previous iteration (mdo_integrator):\n{lines}"
    elif carry_over:
        # The framework appends its termination keyword to the last message; it is not the integrator's.
        integrator = re.sub(r"\s*TASK_COMPLETE\s*$", "",
                            (carry_over.get("previous_integrator_design_state") or "").strip())
        if integrator:
            text += f"\n\nDESIGN_STATE from the previous iteration (mdo_integrator):\n{integrator}"
        if start:
            text += "\n\n" + _starting_design_block(start)
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
