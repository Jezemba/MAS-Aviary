"""Reduce the MAS-Aviary event stream into the GUI's ``Run`` shape.

The React GUI (openwebui_demo/gui) consumes a stream of ``Run`` snapshots
(one JSON object per meaningful change) rather than the markdown that
``formatter.py`` produces for Open WebUI. Both consumers share the SAME
event source (``event_parser`` over ``replay``/``live``) and the SAME
narration copy (``narration.py``) - this module is just an alternate sink
that folds events into the structured object the frontend renders.

The emitted snapshot matches ``gui/src/data/types.ts`` (snake_case keys):

    { meta: {...}, stages: [{stage, status, iters: [{...}]}], result?, reference?, per_stage? }

Design intent lives in the handoff README (§ State Management). Per-tool
durations are not present in the runner stdout (only per-step), so each
step's duration is split evenly across the tools called in that step -
an estimate, flagged here so nobody mistakes it for measured per-tool time.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from event_parser import Event
from narration import (
    AGENT_INTROS,
    STAGE_ORDER,
    describe_tool,
    summarize_observation,
)

# Agent → (display name, mcp, emoji). Mirrors gui/src/data/reference.ts.
AGENT_META: dict[str, dict[str, str]] = {
    "geometry_engineer":    {"name": "Geometry Engineer",    "mcp": "tigl-mcp",    "emoji": "📐"},
    "aerodynamics_analyst": {"name": "Aerodynamics Analyst", "mcp": "su2-mcp",     "emoji": "💨"},
    "structures_analyst":   {"name": "Structures Analyst",   "mcp": "mass-mcp",    "emoji": "⚖️"},
    "propulsion_analyst":   {"name": "Propulsion Analyst",   "mcp": "pycycle-mcp", "emoji": "🔥"},
    "mission_architect":    {"name": "Mission Architect",    "mcp": "aviary-mcp",  "emoji": "✈️"},
    "simulation_executor":  {"name": "Simulation Executor",  "mcp": "aviary-mcp",  "emoji": "📊"},
    "mdo_integrator":       {"name": "MDO Integrator",       "mcp": "-",           "emoji": "🧮"},
}

TOKENS_BUDGET = 200_000

F25_REFERENCE = {
    "fuel_burned_kg": 12_100.0,
    "gross_mass_kg": 85_700.0,
    "ld_cruise": 19.5,
}

_EMOJI_PREFIX = re.compile(r"^[^\w`(]+\s*", re.UNICODE)


def _strip_lead_emoji(desc: str) -> str:
    """narration descriptions begin with an emoji; the GUI supplies its own
    per-tool emoji in the row, so strip the leading glyph from the copy."""
    return _EMOJI_PREFIX.sub("", desc).strip()


def _mcp_for(agent: Optional[str]) -> str:
    return AGENT_META.get(agent or "", {}).get("mcp", "-")


def _parse_design_state(text: str) -> dict[str, Any]:
    """Parse the ``KEY: value`` lines of a final-answer / DESIGN_STATE block
    into a flat dict. Numbers are coerced to int/float where clean."""
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("DESIGN_STATE"):
            continue
        m = re.match(r"^([A-Za-z0-9_./ -]+?)\s*[:=]\s*(.+)$", line)
        if not m:
            continue
        key = m.group(1).strip()
        val = m.group(2).strip().strip(",")
        out[key] = _coerce(val)
        if len(out) >= 24:
            break
    return out


def _coerce(val: str) -> Any:
    try:
        if re.fullmatch(r"-?\d+", val):
            return int(val)
        if re.fullmatch(r"-?\d*\.\d+", val):
            return float(val)
    except ValueError:
        pass
    return val


class RunReducer:
    """Folds Events into a single ``Run`` dict, snapshot after snapshot."""

    def __init__(
        self,
        *,
        combo: str = "mdo_f25_sequential_iterative_feedback",
        structure: str = "sequential",
        handler: str = "iterative_feedback",
        model: str = "claude-sonnet-4-20250514",
        mission: str = "DLR-F25",
        run_id: str = "-",
        output_dir: str = "logs/stat_results/",
    ) -> None:
        self.meta: dict[str, Any] = {
            "combo": combo,
            "run_id": run_id,
            "structure": structure,
            "handler": handler,
            "mission": mission,
            "model": model,
            "output_dir": output_dir,
            "wandb_url": "",
            "started_at": "",
            "elapsed_s": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "tokens_budget": TOKENS_BUDGET,
            "initial_params": {"AREA": 130.1, "ASPECT_RATIO": 11.0, "SCALE_FACTOR": 1.3, "SEED": 42},
        }
        # stage_id -> stage dict; preserve STAGE_ORDER for output.
        self._stages: dict[str, dict[str, Any]] = {}
        self._cur_agent: Optional[str] = None
        # FIFO of tool names awaiting an observation, per current iter.
        self._pending_tools: list[str] = []
        # tools added since the last step_meta (for per-step duration split).
        self._step_tools: list[dict[str, Any]] = []
        self._done: bool = False
        self._final_result: Optional[dict[str, Any]] = None

    # ── helpers ──────────────────────────────────────────────────────────
    def _cur_stage(self) -> Optional[dict[str, Any]]:
        return self._stages.get(self._cur_agent or "")

    def _cur_iter(self) -> Optional[dict[str, Any]]:
        st = self._cur_stage()
        if st and st["iters"]:
            return st["iters"][-1]
        return None

    # ── event handlers ───────────────────────────────────────────────────
    def push(self, ev: Event) -> bool:
        """Fold one event. Returns True if the snapshot changed materially."""
        k = ev.kind
        if k == "wandb_url":
            self.meta["wandb_url"] = ev.text
            m = re.search(r"/runs/([A-Za-z0-9]+)", ev.text)
            if m:
                self.meta["run_id"] = m.group(1)
            return True
        if k == "agent_start":
            return self._on_agent_start(ev.agent)
        if k == "tool_call":
            return self._on_tool_call(ev)
        if k == "observation":
            return self._on_observation(ev)
        if k == "step_meta":
            return self._on_step_meta(ev)
        if k == "final_answer":
            return self._on_final_answer(ev)
        if k == "agent_end":
            return self._on_agent_end(ev.agent)
        if k == "run_done":
            return self._on_run_done(ev)
        if k == "error":
            it = self._cur_iter()
            st = self._cur_stage()
            if st:
                st["_error"] = True
            return True
        return False

    def _on_agent_start(self, agent: Optional[str]) -> bool:
        if not agent:
            return False
        self._cur_agent = agent
        self._pending_tools = []
        self._step_tools = []
        st = self._stages.get(agent)
        if st is None:
            st = {"stage": agent, "status": "running", "iters": [], "_error": False, "_warn": False}
            self._stages[agent] = st
        st["status"] = "running"
        next_iter = len(st["iters"]) + 1
        st["iters"].append(
            {
                "iter": next_iter,
                "duration_s": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
                "summary": None,
                "intro": AGENT_INTROS.get(agent, "").replace("**", ""),
                "currently": None,
                "design_state": None,
                "tools": [],
            }
        )
        return True

    def _on_tool_call(self, ev: Event) -> bool:
        it = self._cur_iter()
        if it is None:
            return False
        desc_raw, hint = describe_tool(ev.text)
        tool = {
            "name": ev.text,
            "mcp": _mcp_for(ev.agent),
            "dur_s": 0.0,
            "status": "running",
            "desc": _strip_lead_emoji(desc_raw),
        }
        it["tools"].append(tool)
        self._pending_tools.append(ev.text)
        self._step_tools.append(tool)
        it["currently"] = {"tool": ev.text, "desc": _strip_lead_emoji(desc_raw), "elapsed_s": 0}
        return True

    def _on_observation(self, ev: Event) -> bool:
        it = self._cur_iter()
        if it is None:
            return False
        tool_name = self._pending_tools.pop(0) if self._pending_tools else None
        parsed = ev.data.get("parsed") or {}
        summary = summarize_observation(tool_name or "", parsed) if isinstance(parsed, dict) else None
        raw = ev.text or ""
        is_err = '"error"' in raw or '"success": false' in raw
        is_warn = bool(summary and summary.lstrip().startswith("⚠"))
        # Resolve the earliest still-running tool matching this name.
        for t in it["tools"]:
            if t["name"] == tool_name and t["status"] == "running":
                t["status"] = "error" if is_err else "success"
                break
        if is_warn or is_err:
            st = self._cur_stage()
            if st:
                st["_warn"] = True
        if summary:
            it["summary"] = _strip_lead_emoji(summary)
        # Clear "currently" when no tool is still running in this iter.
        if not any(t["status"] == "running" for t in it["tools"]):
            it["currently"] = None
        return True

    def _on_step_meta(self, ev: Event) -> bool:
        it = self._cur_iter()
        if it is None:
            return False
        dur = float(ev.data.get("duration_s", 0.0))
        tin = int(ev.data.get("input_tokens", 0))
        tout = int(ev.data.get("output_tokens", 0))
        it["duration_s"] += dur
        it["tokens_in"] = max(it["tokens_in"], tin)
        it["tokens_out"] = max(it["tokens_out"], tout)
        # Split this step's duration evenly across the step's tools (estimate).
        if self._step_tools:
            per = dur / len(self._step_tools)
            for t in self._step_tools:
                if t["status"] != "pending":
                    t["dur_s"] = round(per, 2)
        self._step_tools = []
        return True

    def _on_final_answer(self, ev: Event) -> bool:
        it = self._cur_iter()
        if it is None:
            return False
        ds = _parse_design_state(ev.text or "")
        if ds:
            it["design_state"] = ds
        if it.get("summary") is None and ds:
            # Compose a headline from a few notable keys if present.
            bits = []
            for key in ("MESH_CELLS", "WING_SPAN_M", "FUEL_BURNED_KG", "GROSS_MASS_KG"):
                if key in ds:
                    bits.append(f"{key} {ds[key]}")
            if bits:
                it["summary"] = " · ".join(bits)
        return True

    def _on_agent_end(self, agent: Optional[str]) -> bool:
        st = self._stages.get(agent or "")
        if st is None:
            return False
        it = st["iters"][-1] if st["iters"] else None
        if it:
            it["currently"] = None
        if st.get("_error"):
            st["status"] = "failed"
        elif st.get("_warn"):
            st["status"] = "partial"
        else:
            st["status"] = "success"
        return True

    def _on_run_done(self, ev: Event) -> bool:
        self._done = True
        failed = int(ev.data.get("failed", 0))
        # Any still-running stage is closed out.
        for st in self._stages.values():
            if st["status"] == "running":
                st["status"] = "failed" if st.get("_error") else "success"
        self._final_result = self._build_result(failed)
        return True

    def _build_result(self, failed: int) -> dict[str, Any]:
        # Pull final metrics from the integrator/simulation design_state if present.
        ds: dict[str, Any] = {}
        for agent in ("mdo_integrator", "simulation_executor"):
            st = self._stages.get(agent)
            if st and st["iters"] and st["iters"][-1].get("design_state"):
                ds = {**st["iters"][-1]["design_state"], **ds}
        fuel = float(ds.get("FUEL_BURNED_KG", 0) or 0)
        gross = float(ds.get("GROSS_MASS_KG", 0) or 0)
        ld = float(ds.get("LD_CRUISE", ds.get("L/D", F25_REFERENCE["ld_cruise"])) or F25_REFERENCE["ld_cruise"])
        gap = 0.0
        if fuel and F25_REFERENCE["fuel_burned_kg"]:
            gap = (fuel - F25_REFERENCE["fuel_burned_kg"]) / F25_REFERENCE["fuel_burned_kg"] * 100
        return {
            "status": "failed" if failed else "completed",
            "fuel_burned_kg": fuel,
            "gross_mass_kg": gross,
            "ld_cruise": ld,
            "ld_fallback": bool(self._stages.get("aerodynamics_analyst", {}).get("_warn")),
            "converged": failed == 0,
            "constraints_failed": [],
            "optimality_gap_pct": round(gap, 2),
        }

    # ── snapshot ─────────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        stages_out = []
        total_in = 0
        total_out = 0
        elapsed = 0.0
        per_stage = []
        for agent in STAGE_ORDER:
            st = self._stages.get(agent)
            if st is None:
                stages_out.append({"stage": agent, "status": "pending", "iters": []})
                continue
            iters = [
                {
                    "iter": it["iter"],
                    "duration_s": round(it["duration_s"], 1),
                    "tokens_in": it["tokens_in"],
                    "tokens_out": it["tokens_out"],
                    "summary": it["summary"],
                    "intro": it["intro"],
                    **({"currently": it["currently"]} if it["currently"] else {}),
                    **({"design_state": it["design_state"]} if it["design_state"] else {}),
                    "tools": it["tools"],
                }
                for it in st["iters"]
            ]
            stages_out.append({"stage": agent, "status": st["status"], "iters": iters})
            total_in += sum(i["tokens_in"] for i in st["iters"])
            total_out += sum(i["tokens_out"] for i in st["iters"])
            elapsed += sum(i["duration_s"] for i in st["iters"])
            if self._done and st["iters"]:
                per_stage.append(
                    {
                        "stage": agent,
                        "status": st["status"],
                        "iters": len(st["iters"]),
                        "tokens": sum(i["tokens_out"] for i in st["iters"]),
                        "duration_s": round(sum(i["duration_s"] for i in st["iters"]), 1),
                    }
                )

        # Window fullness = latest running/last agent's input tokens.
        window_used = 0
        for st in self._stages.values():
            if st["iters"]:
                window_used = max(window_used, st["iters"][-1]["tokens_in"])
        meta = dict(self.meta)
        meta["elapsed_s"] = round(elapsed, 1)
        # total_tokens_in drives the context-window budget bar, which measures
        # peak window OCCUPANCY (how full the 200k context got), not a running
        # sum across agents - the input count is already cumulative per agent.
        meta["total_tokens_in"] = window_used
        meta["total_tokens_out"] = total_out

        run: dict[str, Any] = {"meta": meta, "stages": stages_out}
        if self._done and self._final_result is not None:
            run["result"] = self._final_result
            run["reference"] = dict(F25_REFERENCE)
            run["per_stage"] = per_stage
        return run
