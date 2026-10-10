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


# ---- networked and orchestrated (Jessica, 2026-10-10) -----------------------------------------------
# The system message of each combination is one section of networked.md / orchestrated.md, sent as written.
# Everything below adds DATA only, under the same labels sequential uses: the DESIGN_STATE (with the open
# sessions), the TODO board, the peer's own recent work, design.json.

from pathlib import Path as _Path

_REPO = _Path(__file__).resolve().parents[2]
BASELINE_CPACS = _REPO.parent / "mass-mcp" / "tests" / "fixtures" / "D150_simple.xml"
DESIGN_JSON_LABEL = ("design.json (the framework's current record of this design, from tool results; "
                     "read_design_json returns the same):")


def system_text(source: str, section: str) -> str:
    """The fenced text of '## <section>' in <source> (a file at the repo root), exactly as written."""
    text = (_REPO / source).read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(section)}\s*\n+```[^\n]*\n(.*?)^```", text, flags=re.M | re.S)
    if not m:
        raise ValueError(f"{source} has no section '## {section}'")
    return m.group(1).strip("\n")


def with_carried_cpacs(text: str) -> str:
    """On a later iteration the prompts' baseline CPACS path becomes the carried one (as for sequential)."""
    co = read_carry_over() or {}
    cpacs = (co.get("starting_design") or {}).get("CPACS_FILE")
    if cpacs and cpacs != co.get("baseline_cpacs"):
        text = re.sub(r"\S*/mass-mcp/tests/fixtures/D150_simple\.xml", lambda _m: cpacs, text)
    return text


def _baseline_lines(system: str) -> list[str]:
    """Link 0: the starting design is the Default column of the prompt's own design-space table."""
    geo: dict[str, list[str]] = {}
    eng: list[str] = []
    scale = None
    for row in (system or "").splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 7:
            continue
        where, default = cells[2], cells[4]
        if "Aircraft.Engine.SCALE_FACTOR" in where:
            scale = default
        if not cells[1].startswith("Set"):
            continue
        m = re.search(r"tigl: `set_high_level_parameters`, .*?`(\w+)`, key `(\w+)`", where)
        if m:
            geo.setdefault(m.group(1), []).append(f"{m.group(2)} {default}")
            continue
        m = re.search(r"pycycle: `([\w.]+)`", where)
        if m:
            eng.append(f"{m.group(1)} {default}")
    lines = [f"  CPACS_FILE: {BASELINE_CPACS}"] + [f"  {uid}: {', '.join(v)}" for uid, v in geo.items()]
    if eng:
        lines.append("  Engine inputs: " + ", ".join(eng))
    if scale and re.fullmatch(r"[0-9.]+", scale):   # since B170 the framework sets it from propulsion
        lines.append(f"  Mission: Aircraft.Engine.SCALE_FACTOR {scale}")
    return lines


def open_sessions() -> dict:
    """The MCP sessions the framework has recorded as open (data_plane captures every session a tool creates)."""
    try:
        from src.tools import data_plane

        ds = data_plane.get_design_state()
        return {k: v for k, v in (getattr(ds, "sessions", None) or {}).items() if v}
    except Exception:
        return {}


def sessions_lines(indent: str = "  ") -> list[str]:
    return [f"{indent}{k}: {v}" for k, v in open_sessions().items()]


def starting_design_state(system: str) -> str:
    """The DESIGN_STATE every agent's first message opens with, with the sessions already open."""
    co = read_carry_over()
    parts: list[str] = []
    if co:
        integrator = re.sub(r"\s*TASK_COMPLETE\s*$", "", (co.get("previous_integrator_design_state") or "").strip())
        if integrator:
            parts.append(f"DESIGN_STATE from the previous iteration (mdo_integrator):\n{integrator}")
        start = dict(co.get("starting_design") or {})
        lines = ["DESIGN_STATE (the starting design for this iteration, from the previous iteration's design.json):"]
        lines += _starting_design_block(start).splitlines()[1:]
    else:
        lines = ["DESIGN_STATE (the starting design for this iteration):"] + _baseline_lines(system)
    sess = sessions_lines("    ")
    if sess:
        lines += ["  SESSIONS (already open -- reuse them):"] + sess
    parts.append("\n".join(lines))
    return "\n\n".join(parts)


def sessions_block() -> str | None:
    sess = sessions_lines()
    return "SESSIONS open now (from design.json -- reuse them):\n" + "\n".join(sess) if sess else None


def design_json_block() -> str | None:
    dj = read_design_json_text()
    return f"{DESIGN_JSON_LABEL}\n{dj}" if dj else None


def _join(*parts) -> str:
    return "\n\n".join(p for p in parts if p)


RECAP_LINES = 10


def work_recap(agent: str, board=None) -> str | None:
    """A VERY short recap of one peer's own earlier work, from the knowledge base (Jessica, 2026-10-10)."""
    from src.tools.knowledge_base import get_kb

    lines: list[str] = []
    for t in (board.read_todos() if board is not None else []):
        if t.assigned_to == agent:
            lines.append(f"  {t.status} {t.name}")
    kb = get_kb()
    entries = kb.select(agent=agent) if kb is not None else []
    lines += [f"  #{e['seq']} {e['tool']} {e['status']}" for e in entries[-RECAP_LINES:]]
    if not lines:
        return None
    return (f'YOUR WORK SO FAR ({agent}, from the knowledge base; read_design_knowledge(agent="{agent}", '
            'mode="full") has the details):\n' + "\n".join(lines))


def board_block(board, show_blocking: bool) -> str:
    """The TODO board as data, then the full DESIGN_STATE each finished TODO posted."""
    todos = board.read_todos()
    done = {t.name for t in todos if t.status == "done"}
    width = max((len(t.name) for t in todos), default=0)
    rows, posted = [], []
    for t in todos:
        if t.status == "done":
            state = f"done by {t.assigned_to}"
            if t.result:
                posted.append(f"DESIGN_STATE posted on the board by {t.name} ({t.assigned_to}):\n{t.result}")
        elif t.status == "claimed":
            state = f"claimed by {t.assigned_to}"
        else:
            state = t.status + (f" ({t.failure_reason})" if t.status == "failed" and t.failure_reason else "")
            if show_blocking:
                unmet = [d for d in t.depends_on if d not in done]
                state += f"  [BLOCKED on {', '.join(unmet)}]" if unmet else "  [AVAILABLE]"
        rows.append(f"  - {t.name:{width}s}  {state}")
    return _join("TODO BOARD:\n" + "\n".join(rows), *posted)


def peer_message(agent: str, system: str, board, show_blocking: bool) -> str:
    """One networked peer's message for this cycle (its system message is sent separately)."""
    return _join(work_recap(agent, board), starting_design_state(system), board_block(board, show_blocking),
                 design_json_block())


def orchestrator_first_message(system: str, stage: str | None = None) -> str:
    return _join(stage, starting_design_state(system), design_json_block())


def worker_message(task: str, system: str, previous: tuple[str, str] | None) -> str:
    """An orchestrated agent's message: the orchestrator's task, then the DESIGN_STATE as data."""
    if previous is None:
        return _join(task, starting_design_state(system), design_json_block())
    name, output = previous
    return _join(task, f"DESIGN_STATE from the agent that worked before you ({name}):\n{output}",
                 sessions_block(), design_json_block())
