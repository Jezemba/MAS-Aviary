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


def classify_result(result: Any) -> tuple[str, dict, str]:
    """(status, parsed dict, note) for a tool response string or dict."""
    parsed: Any = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return "success", {}, ""
    if not isinstance(parsed, dict):
        return "success", {}, ""
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


def summarize(kb: "KnowledgeBase", question: str | None = None, *, count_read: bool = True,
              **filters) -> str:
    """Summary of the matching entries. Local model when registered (step 4), else digest."""
    if count_read:
        kb.bump("kb_reads_summary")
    entries = kb.select(**filters)
    return digest(entries)
