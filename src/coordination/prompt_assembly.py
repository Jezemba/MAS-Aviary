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

import json
import logging
import re
from typing import Any

_log = logging.getLogger(__name__)

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
    from src.tools.recommendation_tools import RecordRecommendation, RespondToRecommendation

    have = {getattr(t, "name", None) for t in tools}
    # B180: the coordination signals are tool calls (typed arguments), not text keys the framework parses.
    extra = [cls() for cls in (ReadDesignJson, ReadBestPractices, GetDesignState, RecordRecommendation,
                               RespondToRecommendation) if cls.name not in have]
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
    # B177 (aipexws8 v2ifb2, 2026-10-11): the integrator wrote markdown instead of the KEY: lines --
    # "**RECOMMENDED CHANGE:**" / "- **Recommendation To:** propulsion_analyst" / "- **Parameter Adjustment:**
    # ..." -- so nothing was found and no agent of link 2 received the recommendation. Only when the exact
    # forms above found nothing: the same keys case-insensitively, with spaces or underscores, markdown bold
    # and list/heading markers around them; an alias with no value on its line (a heading) is skipped.
    for key, aliases in _RECOMMENDATION_ALIASES.items():
        if key in found:
            continue
        for alias in aliases:
            value = next((v for v in (_md_value(m.group(1)) for m in re.finditer(
                rf"^[\s>#*\-+\d.)]*\**\s*{alias}\s*\**\s*[:=]\s*\**(.*)$", text, flags=re.M | re.I)) if v), None)
            if value:
                found[key] = value
                break
    return found


# Spellings tried, in order, after the exact KEY forms (B177). Word separators may be a space, "_" or "-".
_RECOMMENDATION_ALIASES = {
    "RECOMMENDATION_TO": (r"recommendation[ _-]+to", r"recommended[ _-]+to", r"recommend[ _-]+to"),
    "RECOMMENDED_CHANGE": (r"recommended[ _-]+change", r"parameter[ _-]+adjustment", r"recommended[ _-]+action"),
    "MDO_REASONING": (r"mdo[ _-]+reasoning", r"reasoning", r"rationale"),
}


def _md_value(raw: str) -> str:
    """A markdown 'label: value' value without its bold/code markers and quotes; '' for a bare heading."""
    return raw.strip().strip("*").strip().strip("'\"").strip()


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
# B177: an assessment passed on as written (no RECOMMENDATION_TO readable) is cut to this many characters.
_UNPARSED_RECOMMENDATION_CAP = 4000
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
    """The previous iteration's recommendation for every agent, plus every response so far (B172, B180).

    Addressed to one role, answerable by any: each agent that addresses it, in part or by handing it on,
    responds, and the responses accumulate. B180: the recommendation and the responses are read from the
    agents' tool calls (record_recommendation / respond_to_recommendation); the text keys are only the
    fallback for an agent that never called the tool.
    """
    from src.tools import recommendation_tools as rt

    recorded = (carry_over or {}).get("recommendation")
    integrator = (carry_over or {}).get("previous_integrator_design_state")
    rec = {}
    if isinstance(recorded, dict) and recorded.get("recommendation_to") and recorded.get("verdict") != "COMPLETE":
        rec = {"RECOMMENDATION_TO": recorded["recommendation_to"],
               "RECOMMENDED_CHANGE": recorded.get("recommended_change", "")}
        lines = ["RECOMMENDATION from the previous iteration (mdo_integrator, recorded with record_recommendation):"]
        lines += [f"  {k}: {rec[k]}" for k in _RECOMMENDATION_SENT_KEYS if rec.get(k)]
    else:
        rec = parse_recommendation(integrator)
        if rec.get("RECOMMENDATION_TO"):
            lines = ["RECOMMENDATION from the previous iteration (mdo_integrator):"]
            lines += [f"  {k}: {rec[k]}" for k in _RECOMMENDATION_SENT_KEYS if k in rec]
        else:
            # B177: a recommendation that was neither recorded nor readable must not vanish silently: when the
            # assessment talks about a recommendation, every agent gets it as written.
            text = re.sub(r"\s*TASK_COMPLETE\s*$", "", str(integrator or "")).strip()
            if not text or not re.search(r"recommend", text, re.I):
                return ""
            _log.warning("B177: no RECOMMENDATION_TO recorded or readable; passing the assessment to %s as written",
                         stage_name or "the agent")
            lines = ["RECOMMENDATION from the previous iteration (mdo_integrator): it was not recorded with "
                     "record_recommendation, so its recommendation is given as written."]
            if previous_outputs:
                lines.append(f"The previous iteration's assessment:\n{text[:_UNPARSED_RECOMMENDATION_CAP]}")
            else:
                lines.append("It is in the DESIGN_STATE from the previous iteration (mdo_integrator) above.")
    # B176: role/agent wording -- "stage" belongs to the staged pipeline; this text is shared.
    addressed = ("the role named in RECOMMENDATION_TO" if rec.get("RECOMMENDATION_TO")
                 else "the role it names")
    lines.append(f"This recommendation is addressed to {addressed}, but any agent may act "
                 "on it. If you address it, fully or in part, or find that it belongs to another role, "
                 "say how with respond_to_recommendation (and RECOMMENDATION_IMPLEMENTED in your DESIGN_STATE).")
    # Responses: recorded ones first (since the recommendation, when it was recorded in this link); then, for
    # an agent that recorded none, a RECOMMENDATION_IMPLEMENTED line in its text.
    since = recorded.get("seq", 0) if isinstance(recorded, dict) and recorded.get("in_link") else 0
    responses, answered = [], set()
    for r in rt.responses_since(since):
        responses.append(f"RECOMMENDATION_IMPLEMENTED (reported by {r['agent']}): {r['how_addressed']}")
        answered.add(r["agent"])
    for name, content, _ in previous_outputs or []:
        if name in answered:
            continue
        for value in _implemented_values(str(content or "")):
            responses.append(f"RECOMMENDATION_IMPLEMENTED (reported by {name}): {value}")
    if responses:
        lines.append("Responses so far:")
        lines += responses
    # The responses are this link's only: the next link starts from its own integrator's recommendation.
    if _stage_key(stage_name) == "mdo_integrator":
        lines.append("Use these responses in your verdict, and record your verdict and next recommendation with "
                     "record_recommendation." if responses else
                     "No agent reported RECOMMENDATION_IMPLEMENTED in this iteration. Record your verdict and next "
                     "recommendation with record_recommendation.")
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
        text += f"\n\nDESIGN_STATE from the previous agent ({prev_name}):\n{prev_content}"
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
    # Her text carries the study's placeholders (<<WING_MASS_METHOD>>, <<MATERIAL>>, ...); the sequential YAML
    # fills them at load, so these must too (found 2026-10-11: the literal placeholders reached the model).
    from src.config.canonical import substitute_text

    return substitute_text(m.group(1).strip("\n"))


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
        recorded = co.get("recommendation")
        if isinstance(recorded, dict) and recorded.get("recommendation_to"):
            # B180: what the integrator recorded with record_recommendation (typed, not parsed from its text).
            parts.append("RECOMMENDATION recorded by the mdo_integrator (record_recommendation):\n"
                         f"  RECOMMENDATION_TO: {recorded['recommendation_to']}\n"
                         f"  RECOMMENDED_CHANGE: {recorded.get('recommended_change', '')}\n"
                         f"  VERDICT: {recorded.get('verdict', '')}")
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


def role_contracts(system: str) -> dict:
    """Each role template's block from DELIVERABLE to its end (DESIGN_STATE, SESSION STATE FILE), by role name.

    Jessica, 2026-10-11: attached by the framework to every agent the orchestrator creates under that name,
    so the orchestrator cannot drop the DESIGN_STATE contract or the CPACS line from a persona.
    """
    parts = re.split(r"^AGENT NAME: (\w+)\n", system or "", flags=re.M)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1]
        k = body.find("DELIVERABLE")
        if k >= 0:
            out[parts[i]] = body[k:].strip("\n")
    return out


SUMMARY_LINE_CHARS = 400
_STATE_LINE = re.compile(r"^\s*([A-Z][A-Z0-9_]{2,})\s*:\s*(.+?)\s*$")


def summary_line(name: str, output: str) -> str:
    """One line for an earlier agent: its DESIGN_STATE fields (KEY=value), else the start of its answer."""
    fields = []
    text = (output or "").strip()
    try:
        data = json.loads(text[text.index("{"):]) if "{" in text else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        fields = [f"{k}={v}" for k, v in data.items() if not isinstance(v, (dict, list))]
    if not fields:
        fields = [f"{m.group(1)}={m.group(2)}" for m in map(_STATE_LINE.match, text.splitlines()) if m]
    line = ", ".join(fields) if fields else " ".join(text.split())
    if len(line) > SUMMARY_LINE_CHARS:
        line = line[:SUMMARY_LINE_CHARS] + "..."
    return f"  {name}: {line}"


def worker_message(task: str, system: str, previous: tuple[str, str] | None,
                   earlier: list[tuple[str, str]] | None = None, inbox: list[str] | None = None) -> str:
    """An orchestrated agent's message: the orchestrator's task, its stored messages to this agent, then the
    DESIGN_STATE as data -- the previous agent's in full and (iterative, graph) every earlier one as a line."""
    messages = ("Messages from the orchestrator:\n" + "\n".join(f"- {m}" for m in inbox)) if inbox else None
    if previous is None:
        return _join(task, messages, starting_design_state(system), design_json_block())
    name, output = previous
    summary = ("DESIGN_STATE summary of the agents before that:\n" + "\n".join(summary_line(n, o) for n, o in earlier)
               if earlier else None)
    return _join(task, messages, f"DESIGN_STATE from the agent that worked before you ({name}):\n{output}",
                 summary, sessions_block(), design_json_block())
