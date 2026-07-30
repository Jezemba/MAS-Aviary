"""Thin CLI to call any of the 5 MCP tools directly — lets an INTERACTIVE Claude
session act as the MDO agent (real reasoning, watched live, no paid batch-API agent).

Persists the shared data-plane DesignState across invocations (logs/mcp_designstate.json)
so the typed coupling registry, captured aero/mass, mesh blobs, and session handles survive
between calls — exactly as they would inside one agent process. This makes the full pipeline
(and the UNCOUPLED_MISSION error -> rerun SU2 -> couple recovery) drivable one call at a time.

Every call is appended to logs/live_agent_chain.jsonl for real-time monitoring.

Usage:
  python scripts/mcp_call.py LIST                     # list tool names
  python scripts/mcp_call.py STATE                    # dump the typed registry + coupling status
  python scripts/mcp_call.py RESET                    # clear persisted DesignState (fresh run)
  python scripts/mcp_call.py <tool> '<json-args>'     # call a tool
Ignore "Cannot close a running event loop" stderr — cosmetic MCP teardown.
"""
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")

# Per-run isolation: parallel subagents each set MCP_STATE_FILE / MCP_LOG_FILE so their
# DesignState (typed registry, sessions, captured aero/geom) and live logs don't collide.
LOG = Path(os.environ.get("MCP_LOG_FILE", "logs/live_agent_chain.jsonl"))
STATE = Path(os.environ.get("MCP_STATE_FILE", "logs/mcp_designstate.json"))


def _summ(d):
    if not isinstance(d, dict):
        return str(d)[:200]
    if d.get("error_code") == "UNCOUPLED_MISSION":
        return "UNCOUPLED_MISSION (aero not run yet)"
    for keys in (("fuel_burned_kg", "gtow_kg"), ("session_id",), ("success", "valid"),
                 ("runtime_seconds", "exit_code"), ("oem_kg",)):
        hit = {k: d.get(k) for k in keys if k in d}
        if hit:
            return json.dumps(hit)
    return json.dumps({k: d[k] for k in list(d)[:4]}, default=str)[:200]


def _restore(ds):
    """Load persisted data_store + sessions into the fresh singleton DesignState."""
    if not STATE.exists():
        return
    try:
        saved = json.loads(STATE.read_text())
        if isinstance(getattr(ds, "data_store", None), dict):
            ds.data_store.update(saved.get("data_store", {}))
        if isinstance(getattr(ds, "sessions", None), dict):
            ds.sessions.update(saved.get("sessions", {}))
    except Exception as e:
        print(f"[mcp_call] restore skipped: {e}", file=sys.stderr)


def _persist(ds):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "data_store": getattr(ds, "data_store", {}) or {},
            "sessions": getattr(ds, "sessions", {}) or {},
        }, default=str))
    except Exception as e:
        print(f"[mcp_call] persist skipped: {e}", file=sys.stderr)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    tool = sys.argv[1]

    if tool == "RESET":
        if STATE.exists():
            STATE.unlink()
        print(json.dumps({"reset": True}))
        return 0

    from src.config.loader import load_config
    from scripts.stat_batch_runner import _load_mcp_tools
    from src.tools.data_plane import get_design_state
    with contextlib.redirect_stderr(io.StringIO()):
        tm = _load_mcp_tools(load_config("config/mdo_f25_run_claude.yaml"))
    ds = get_design_state()
    _restore(ds)

    if tool == "LIST":
        print(json.dumps(sorted(tm.keys()), indent=2))
        return 0
    if tool == "STATE":
        reg = (ds.data_store or {}).get("analysis_vars", {})
        print(json.dumps({
            "analysis_vars": reg,
            "aero_coupling_status": (ds.data_store or {}).get("aero_coupling_status"),
            "aero_coupling_source": (ds.data_store or {}).get("aero_coupling_source"),
            "sessions": {k: str(v)[:8] for k, v in (ds.sessions or {}).items()},
        }, indent=2, default=str))
        return 0
    if tool not in tm:
        print(json.dumps({"error": f"unknown tool '{tool}'", "available": sorted(tm.keys())}))
        return 1

    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    t0 = time.time()
    with contextlib.redirect_stderr(io.StringIO()):
        r = tm[tool].forward(**args)
    dt = round(time.time() - t0, 2)
    _persist(ds)

    if isinstance(r, dict):
        d = r
    elif isinstance(r, str) and r.strip()[:1] in "{[":
        try:
            d = json.loads(r)
        except json.JSONDecodeError:
            d = {"raw": r}
    else:
        d = {"raw": str(r)}

    def _shrink(a):
        if isinstance(a, str) and len(a) > 120:
            return a[:60] + f"...<{len(a)} chars>"
        if isinstance(a, dict):
            return {k: _shrink(v) for k, v in a.items()}
        if isinstance(a, list):
            return [_shrink(v) for v in a]
        return a
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": round(time.time(), 1), "tool": tool, "args": _shrink(args),
                            "dt_s": dt, "summary": _summ(d)}, default=str) + "\n")
    print(json.dumps(d, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
