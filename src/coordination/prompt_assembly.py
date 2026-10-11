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


REFERENCE_TOOLS = ("read_design_json", "read_best_practices", "get_design_state")


def with_reference_tools(tools: list) -> list:
    """The stage's tools plus the reference tools its prompt names (B150, 2026-10-08).

    A sequential stage gets only its allowed_tools, and no stage listed these, so "read it with
    read_design_json" (and get_design_state, read_best_practices) named tools the model did not
    have: read_design_json was called 0 times in 16 attempts. Added in verbatim mode only.
    """
    from src.tools.best_practices_tool import ReadBestPractices
    from src.tools.design_json import ReadDesignJson
    from src.tools.design_state_tool import GetDesignState

    have = {getattr(t, "name", None) for t in tools}
    extra = [cls() for cls in (ReadDesignJson, ReadBestPractices, GetDesignState) if cls.name not in have]
    return list(tools) + extra


def read_design_json_text() -> str | None:
    """The current design.json, as the framework last wrote it, or None if it does not exist yet."""
    try:
        from src.tools.design_json import design_path

        path = design_path()
        if not path:
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


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


_RECOMMENDATION_SENT_KEYS = ("RECOMMENDATION_TO", "RECOMMENDED_CHANGE")
# B174: found anywhere, not only at a line start. A final answer given as a dict reaches the next stage as
# one line ({'GEOMETRY_SET': True, 'RECOMMENDATION_IMPLEMENTED': '...'}), and the line-start pattern missed
# the geometry engineer's response in aipexws8 v2b173 link 2. Quoted values (JSON, dict text) are read to
# their closing quote; unquoted ones to the end of the line.
_IMPLEMENTED_RE = re.compile(
    r"""RECOMMENDATION[_ ]IMPLEMENTED\**["']?\**\s*[:=]\s*"""
    r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|([^\n]+))""",
    re.IGNORECASE)


def _implemented_values(content: str) -> list[str]:
    values = []
    for m in _IMPLEMENTED_RE.finditer(content):
        value = next(g for g in m.groups() if g is not None)
        value = value.replace('\\"', '"').replace("\\'", "'").strip()
        if m.group(3) is not None:          # unquoted: trim JSON/dict punctuation left on the line
            value = value.rstrip(",}] ").strip().strip("*").strip()
        if value and not value.startswith("<how"):
            values.append(value)
    return values


def recommendation_block(carry_over: dict | None, previous_outputs: list, stage_name: str | None = None) -> str:
    """The previous iteration's recommendation for every stage, plus every response so far (B172).

    Addressed to one stage, answerable by any: an integrator recommendation is often vague or spans
    roles (an aero recommendation that is really a geometry change), so each stage that addresses it,
    in part or by handing it on, reports RECOMMENDATION_IMPLEMENTED and the responses accumulate.
    """
    rec = parse_recommendation((carry_over or {}).get("previous_integrator_design_state"))
    if not rec.get("RECOMMENDATION_TO"):
        return ""
    lines = ["RECOMMENDATION from the previous iteration (mdo_integrator):"]
    lines += [f"  {k}: {rec[k]}" for k in _RECOMMENDATION_SENT_KEYS if k in rec]
    lines.append("This recommendation is addressed to the stage named in RECOMMENDATION_TO, but any stage may act "
                 "on it. If your stage addresses it, fully or in part, or finds that it belongs to another stage, "
                 "add RECOMMENDATION_IMPLEMENTED: <how you addressed it> to your DESIGN_STATE.")
    responses = []
    for name, content, _ in previous_outputs or []:
        for value in _implemented_values(str(content or "")):
            responses.append(f"RECOMMENDATION_IMPLEMENTED (reported by {name}): {value}")
    if responses:
        lines.append("Responses so far:")
        lines += responses
    # The responses are this link's only: the next link starts from its own integrator's recommendation.
    if _stage_key(stage_name) == "mdo_integrator":
        lines.append("Use these responses in your verdict and your next recommendation." if responses else
                     "No stage reported RECOMMENDATION_IMPLEMENTED in this iteration.")
    return "\n".join(lines)


def verbatim_stage_input(stage_prompt: str, previous_outputs: list, carry_over: dict | None = None,
                         stage_name: str | None = None, design_json: str | None = None) -> str:
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
    elif carry_over:
        # The framework appends its termination keyword to the last message; it is not the integrator's.
        integrator = re.sub(r"\s*TASK_COMPLETE\s*$", "",
                            (carry_over.get("previous_integrator_design_state") or "").strip())
        if integrator:
            text += f"\n\nDESIGN_STATE from the previous iteration (mdo_integrator):\n{integrator}"
        if start:
            text += "\n\n" + _starting_design_block(start)
    # B172 (Jessica, 2026-10-10): every stage sees the recommendation itself (not the whole integrator
    # assessment); any stage may respond with RECOMMENDATION_IMPLEMENTED, and the framework carries every
    # response to every later stage. Routing by role name had sent an aero recommendation to geometry only.
    rec_block = recommendation_block(carry_over, previous_outputs, stage_name)
    if rec_block:
        text += "\n\n" + rec_block
    # Jessica (2026-10-08): the design record goes into the context, not behind a tool the model may
    # never call (read_design_json was called 0 times in 16 attempts).
    if design_json:
        text += ("\n\ndesign.json (the framework's current record of this design, from tool results; "
                 f"read_design_json returns the same):\n{design_json}")
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
