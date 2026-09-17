"""Design knowledge base: a per-run record of design work actually done (B81, B80).

WHY THIS EXISTS

Stage A on the 7960 (validate3_7960, 2026-09-14/15) showed agents redoing
once-only work -- networked_graph_routed link 1 made 6 volume meshes, 5 SU2
solves and 6 open_cpacs calls in ~3 h -- because the only record of what other
agents had done was their free-text final_answer. Nothing let an agent ask
"what has been done on this design, and where is the result".

WHAT IT IS

One knowledge base (KB) per chain link, living on the DesignState so every
coordination structure shares the same object, and mirrored to
``<run dir>/knowledge_base.jsonl`` (one JSON object per line, flushed on every
write) so it survives a crash. Entries are written by the tool middleware from
the REAL tool result -- never from what an agent claims -- and large payloads are
recorded as the data_store refs the data plane already created, never copied.

Design: .claude/HANDOFF_B80_B81_KNOWLEDGE_BASE_AND_DUPLICATE_GUARD.md (sections 2
and 5); decisions in its section 7 (Jessica, 2026-09-15).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

SERVER_DISCIPLINE = {
    "tigl": "geometry",
    "su2": "aero",
    "mass": "structures",
    "pycycle": "propulsion",
    "aviary": "mission",
}
FULL_MODE_CAP_CHARS = 6000
SUMMARY_MAX_NEW_TOKENS = 400        # decided 2026-09-15 (section 7.3)
PROMPT_BUDGET_TOKENS = 30000        # decided 2026-09-15 (section 7.3)
_SMALL_STR = 200
_OUTPUTS_CAP_CHARS = 1500

# Per-run settings supplied by the runner (child process), see configure_run().
_run_config: dict[str, Any] = {"path": None, "chain_link": None, "attempt": None, "seed": None}


def configure_run(path: str | None = None, chain_link: int | None = None,
                  attempt: int | None = None, seed: dict | None = None) -> None:
    """Where this run's KB is written, and what the previous link handed over."""
    _run_config.update(path=path, chain_link=chain_link, attempt=attempt, seed=seed)
    if path:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _small(value: Any, depth: int = 0) -> Any:
    """Keep only values small enough to record; refs are kept as their key."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _SMALL_STR else f"<{len(value)} chars>"
    if isinstance(value, dict):
        if isinstance(value.get("ref"), str):
            return value["ref"]
        if depth >= 2:
            return f"<dict {len(value)} keys>"
        return {k: _small(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, (list, tuple)):
        if len(value) > 10 or depth >= 2:
            return f"<list {len(value)} items>"
        return [_small(v, depth + 1) for v in value]
    return f"<{type(value).__name__}>"


def _cap_json(obj: dict, cap: int) -> dict:
    text = json.dumps(obj, default=str)
    if len(text) <= cap:
        return obj
    kept: dict = {}
    for k, v in obj.items():
        kept[k] = v
        if len(json.dumps(kept, default=str)) > cap:
            kept.pop(k)
            kept["_truncated_keys"] = len(obj) - len(kept)
            break
    return kept


# An MCP tool error arrives as a PLAIN STRING, not JSON (B87).
_ERROR_PREFIXES = ("error calling tool", "error:", "exception:", "traceback")
_ERROR_MARKERS = ("traceback (most recent call last)", "error calling tool")


def _string_status(text: str) -> str:
    """``failed`` | ``unknown`` for a tool result that is not JSON (B87).

    ``classify_result`` used to return ``success`` for anything it could not parse. But
    an MCP tool error is exactly that -- a plain string:

        Error calling tool 'generate_volume_mesh': Component 'Wing' not found.
        Did you mean 'Wing1'? Available UIDs: Fuselage1, Wing1, Wing2H, Wing3V.

    so every string-form failure was filed as a SUCCESSFUL call with empty outputs, and
    the B81 duplicate guard was then armed by it. In validate9 that locked all three peers
    out of meshing: agent_3 meshed correctly and was refused with "agent_1 already did
    generate_volume_mesh ... Use its result: {}" -- there was no result, the call had
    failed. 28 phantom successes across every run measured on the 7960, 19 of them
    generate_volume_mesh: 12 wrong-UID and 7 cap refusals, all recoverable attempts that
    could never be retried.

    Anything still unparseable is ``unknown``, never ``success``: a result we cannot read
    is not evidence that work was completed.
    """
    head = text.strip()[:200].lower()
    if head.startswith(_ERROR_PREFIXES) or any(m in head for m in _ERROR_MARKERS):
        return "failed"
    return "unknown"


def classify_result(result: Any) -> tuple[str, dict, str]:
    """(status, parsed dict, note) for a tool response string or dict."""
    parsed: Any = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            status = _string_status(result)
            return status, {}, (result.strip()[:_SMALL_STR] if status == "failed" else "")
    if not isinstance(parsed, dict):
        return "unknown", {}, ""
    failed = (
        parsed.get("success") is False
        or str(parsed.get("status", "")).lower() in ("error", "failed", "failure")
        or bool(parsed.get("error_code"))
        or (bool(parsed.get("error")) and parsed.get("success") is not True)
    )
    note = ""
    if failed:
        note = str(parsed.get("error") or parsed.get("error_message") or parsed.get("error_code") or "")[:_SMALL_STR]
    return ("failed" if failed else "success"), parsed, note


class KnowledgeBase:
    """Append-only, thread-safe record of completed design work for one chain link."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.entries: list[dict] = []
        self.metrics: dict[str, Any] = {
            "kb_reads_full": 0,
            "kb_reads_summary": 0,
            "kb_summary_calls": 0,
            "kb_summary_seconds": 0.0,
            "duplicate_refusals": {},
            "duplicate_repeats_after_refusal": {},
            "max_prompt_tokens": 0,
            "context_trims": 0,
        }
        self._summary_cache: dict[tuple, str] = {}
        self.guard_state: dict[str, Any] = {}   # owned by duplicate_guard
        seed = _run_config.get("seed")
        if seed:
            self.append(
                tool="link_start", server="", agent="framework", role="runner", status="success",
                inputs={}, outputs=_small(seed) if not isinstance(seed, dict) else _cap_json(
                    {k: _small(v) for k, v in seed.items()}, _OUTPUTS_CAP_CHARS),
                note="previous chain link's knowledge-base summary and end-state design",
                discipline="integration",
            )

    # -- writing ---------------------------------------------------------------

    def append(self, *, tool: str, server: str, agent: str, role: str, status: str,
               inputs: dict | None = None, outputs: dict | None = None, note: str = "",
               design_fingerprint: str | None = None, discipline: str | None = None) -> dict:
        with self._lock:
            entry = {
                "seq": len(self.entries) + 1,
                "time_utc": _utc_now(),
                "chain_link": _run_config.get("chain_link"),
                "attempt": _run_config.get("attempt"),
                "agent": agent,
                "role": role,
                "discipline": discipline or SERVER_DISCIPLINE.get(server, "integration"),
                "tool": tool,
                "server": server,
                "status": status,
                "design_fingerprint": design_fingerprint,
                "inputs": _cap_json({k: _small(v) for k, v in (inputs or {}).items()}, _OUTPUTS_CAP_CHARS),
                "outputs": _cap_json({k: _small(v) for k, v in (outputs or {}).items()}, _OUTPUTS_CAP_CHARS),
                "note": note,
            }
            self.entries.append(entry)
            path = _run_config.get("path")
            if path:
                try:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, default=str) + "\n")
                        f.flush()
                except OSError:  # pragma: no cover - a disk problem must not break the run
                    pass
            return entry

    def bump(self, key: str, amount: float = 1) -> None:
        with self._lock:
            self.metrics[key] = self.metrics.get(key, 0) + amount

    def bump_nested(self, key: str, agent: str, tool: str) -> None:
        with self._lock:
            by_agent = self.metrics.setdefault(key, {})
            by_agent.setdefault(agent, {})
            by_agent[agent][tool] = by_agent[agent].get(tool, 0) + 1

    def note_prompt_tokens(self, n: int) -> None:
        with self._lock:
            if n > self.metrics.get("max_prompt_tokens", 0):
                self.metrics["max_prompt_tokens"] = int(n)

    # -- reading ---------------------------------------------------------------

    def select(self, discipline: str | None = None, agent: str | None = None,
               tool: str | None = None, since_seq: int | None = None) -> list[dict]:
        with self._lock:
            out = list(self.entries)
        if discipline:
            out = [e for e in out if e["discipline"] == discipline]
        if agent:
            out = [e for e in out if e["agent"] == agent]
        if tool:
            out = [e for e in out if e["tool"] == tool]
        if since_seq is not None:
            out = [e for e in out if e["seq"] > int(since_seq)]
        return out

    def read_full(self, **filters) -> str:
        """Matching entries exactly as stored, newest kept when over the cap."""
        self.bump("kb_reads_full")
        entries = self.select(**filters)
        if not entries:
            return json.dumps({"entries": [], "note": "No matching design work recorded yet."})
        kept: list[dict] = []
        for e in reversed(entries):
            trial = [e] + kept
            if len(json.dumps({"entries": trial}, default=str)) > FULL_MODE_CAP_CHARS - 200:
                break
            kept = trial
        payload: dict[str, Any] = {"entries": kept}
        dropped = len(entries) - len(kept)
        if dropped:
            payload["truncated"] = dropped
            payload["note"] = (f"{dropped} older entries omitted -- call with mode='summary' or "
                               "narrower filters (discipline, agent, tool, since_seq)")
        return json.dumps(payload, default=str)


def get_kb() -> KnowledgeBase | None:
    """The knowledge base of the current design state, created on first use."""
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    if ds is None:
        return None
    kb = getattr(ds, "knowledge_base", None)
    if kb is None:
        with _CREATE_LOCK:
            kb = getattr(ds, "knowledge_base", None)
            if kb is None:
                kb = KnowledgeBase()
                ds.knowledge_base = kb
    return kb


_CREATE_LOCK = threading.Lock()


def record_tool_result(tool_name: str, server: str, resolved: dict, result: Any = None,
                       *, error: BaseException | None = None, design_fingerprint: str | None = None,
                       status: str | None = None, note: str = "") -> dict | None:
    """Write one KB entry for a completed (or failed/refused) MCP tool call."""
    kb = get_kb()
    if kb is None or not server:
        return None
    from src.tools.agent_context import current_agent

    ident = current_agent()
    if error is not None:
        st, parsed, n = "failed", {}, f"{type(error).__name__}: {error}"[:_SMALL_STR]
    else:
        st, parsed, n = classify_result(result)
    return kb.append(
        tool=tool_name, server=server,
        agent=ident.name if ident else "unknown", role=ident.role if ident else "",
        status=status or st, inputs=dict(resolved or {}), outputs=parsed,
        note=note or n, design_fingerprint=design_fingerprint,
    )


def kb_metrics() -> dict:
    kb = get_kb()
    if kb is None:
        return {}
    with kb._lock:
        m = json.loads(json.dumps(kb.metrics, default=str))
        m["kb_entries"] = len(kb.entries)
    return m


# -- summaries -------------------------------------------------------------------

# What a coupled result needs, per discipline: the tool whose SUCCESS shows it ran.
_MILESTONES = [
    ("geometry", "generate_volume_mesh", "volume mesh"),
    ("aero", "run_su2_solver", "SU2 solve"),
    ("structures", "estimate_mass", "mass estimate"),
    ("propulsion", "run_cycle", "engine cycle"),
    ("mission", "run_simulation", "mission simulation"),
]


def digest(entries: list[dict], limit_chars: int = 2500) -> str:
    """Deterministic, model-free summary: DONE / FAILED / STILL MISSING."""
    if not entries:
        return "No design work recorded yet."
    done = [e for e in entries if e["status"] == "success" and e["tool"] != "link_start"]
    failed = [e for e in entries if e["status"] == "failed"]
    lines: list[str] = []
    seed = next((e for e in entries if e["tool"] == "link_start"), None)
    if seed:
        prev = (seed.get("outputs") or {}).get("summary")
        if prev:
            lines.append(f"PREVIOUS LINK: {str(prev)[:400]}")
    if done:
        lines.append("DONE:")
        last_by_tool: dict[str, dict] = {}
        for e in done:
            last_by_tool[e["tool"]] = e
        for tool, e in last_by_tool.items():
            n = sum(1 for d in done if d["tool"] == tool)
            outs = {k: v for k, v in (e.get("outputs") or {}).items()
                    if k not in ("success", "status") and v not in (None, "", {}, [])}
            out_s = json.dumps(outs, default=str)[:220]
            times = f" x{n}" if n > 1 else ""
            lines.append(f"- {tool}{times}: last by {e['agent']} (seq {e['seq']}, {e['time_utc']}) {out_s}")
    if failed:
        lines.append("FAILED:")
        for e in failed[-5:]:
            lines.append(f"- {e['tool']} by {e['agent']} (seq {e['seq']}): {e.get('note', '')[:120]}")
    missing = [label for disc, tool, label in _MILESTONES if not any(d["tool"] == tool for d in done)]
    lines.append("STILL MISSING for a coupled result: " + (", ".join(missing) if missing else "nothing"))
    text = "\n".join(lines)
    return text if len(text) <= limit_chars else text[: limit_chars - 20] + "\n...[digest truncated]"


_SUMMARY_INSTRUCTION = (
    "You summarize an aircraft-design run's knowledge base for another engineering agent. "
    "The records below were written by the framework from real tool results (not from agents' "
    "claims). Summarize them for an agent that needs: {need}. Return, briefly: what is DONE (with "
    "key values and the data refs of large results), what is STILL MISSING for a coupled result "
    "(volume mesh, SU2 solve, mass estimate, engine cycle, mission simulation), and any conflicts "
    "or failures. Do not invent values that are not in the records."
)
_SUMMARY_RECORDS_CAP_CHARS = 12000


def summarize(kb: "KnowledgeBase", question: str | None = None, *, count_read: bool = True,
              **filters) -> str:
    """Summary of the matching entries by the already-loaded LOCAL model, else the digest.

    The call gets only an instruction and the matching records -- no tools, no
    conversation history -- with SUMMARY_MAX_NEW_TOKENS new tokens, on the small
    summary model, taking no generation slot, so it never competes with the agents
    for the big model (B82). Cached per (filters, question, KB size):
    a repeat call with nothing new costs no GPU time. Never an API model: only a
    model accepted by register_local_model is used.
    """
    if kb is None:
        return "No design work recorded yet."
    if count_read:
        kb.bump("kb_reads_summary")
    entries = kb.select(**filters)
    if not entries:
        return "No design work recorded yet."
    key = (tuple(sorted((k, v) for k, v in filters.items() if v is not None)), question or "", len(kb.entries))
    with kb._lock:
        cached = kb._summary_cache.get(key)
    if cached is not None:
        return cached

    # B82: summaries run on the SMALL summary model, never the big one -- they cost
    # 40.8 minutes of the 32B's time in one link -- and take no generation slot, so
    # they never compete with the peers. No summary model configured (or it failed
    # to load) means the deterministic digest, never the big model.
    from src.llm.generation_slots import get_summary_model

    model = get_summary_model()
    text = None
    if model is not None:
        need = question or "the current state of the design work"
        records = json.dumps(_newest_within(entries, 9000), default=str)
        instruction = (
            "Summarize these design records for an agent that needs: " + need + ".\n"
            "Return, in under 300 words: what is DONE (with key values and data refs), what is "
            "STILL MISSING for a coupled result (volume mesh, SU2 solve, mass estimate, engine cycle, "
            "mission simulation), and any conflicts or failures. Use only these records.\n\n"
            "RECORDS:\n" + records + "\n\nDeterministic digest of the same records:\n" + digest(entries)
        )
        messages = [{"role": "user", "content": [{"type": "text", "text": instruction}]}]
        t0 = time.monotonic()
        try:
            text = _call_summary_model(model, messages)
            kb.bump("kb_summary_calls")
        except Exception as exc:  # a failed summary must never break the caller
            kb.bump("kb_summary_failures")
            text = None
            _ = exc
        finally:
            kb.bump("kb_summary_seconds", round(time.monotonic() - t0, 3))
    if not text:
        text = digest(entries)
    with kb._lock:
        kb._summary_cache[key] = text
    return text


def _newest_within(entries: list[dict], cap_chars: int) -> list[dict]:
    kept: list[dict] = []
    for e in reversed(entries):
        if len(json.dumps([e] + kept, default=str)) > cap_chars:
            break
        kept = [e] + kept
    return kept


def _call_summary_model(model: Any, messages: list) -> str:
    """One short, tool-free generation on the SMALL summary model; returns plain text.

    Called straight through TransformersModel.generate: no tool-call parsing, no parse
    retries, and no shared chat-template state to mutate (B82 -- that mutation was the
    race the old lock hid). The configured summary model is a non-thinking instruct
    build, so the whole token budget goes to the summary.
    """
    from smolagents.models import TransformersModel

    from src.llm.thinking_model import ThinkingModel, strip_think_blocks

    if isinstance(model, ThinkingModel):
        # Structurally impossible (register_summary_model refuses it); counted and
        # refused here too so the acceptance criterion is measured, not assumed.
        from src.llm.generation_slots import note_summary_on_big_model

        note_summary_on_big_model()
        raise RuntimeError("summaries must never run on the agents' model (B82)")
    if isinstance(model, TransformersModel):
        msg = TransformersModel.generate(model, messages, max_new_tokens=SUMMARY_MAX_NEW_TOKENS,
                                         do_sample=False)
    else:   # a registered test stub
        msg = model.generate(messages, max_new_tokens=SUMMARY_MAX_NEW_TOKENS, do_sample=False)
    content = msg.content if not isinstance(msg.content, list) else " ".join(
        part.get("text", "") for part in msg.content if isinstance(part, dict))
    return strip_think_blocks(content or "").strip()


HANDOFF_MARKER = "=== DESIGN KNOWLEDGE BASE ("


def handoff_text(task: str) -> str:
    """Prefix a handoff or assignment with the knowledge-base summary (decided 2026-09-15, 7.4).

    Every agent run starts with what has really been done, from tool results, instead
    of relying on another agent's free-text claim. Empty knowledge base: unchanged.
    """
    kb = get_kb()
    if kb is None or not kb.entries:
        return task
    summary = summarize(kb, count_read=False, question="an agent starting its next piece of work")
    kb.bump("kb_handoff_summaries")
    return (
        f"{HANDOFF_MARKER}framework record of work already done in this run: real tool "
        "results, not claims) ===\n"
        f"{summary}\n"
        "For details or data refs call read_design_knowledge(mode='full', tool=...). Do not redo "
        "once-only work listed here.\n"
        "=== END DESIGN KNOWLEDGE BASE ===\n\n"
        f"{task}"
    )
