"""Thin CLI to call any of the 5 MCP tools directly — lets an INTERACTIVE Claude
session act as the MDO agent (real LLM reasoning, watched live, no paid batch-API
agent). Every call is appended to logs/live_agent_chain.jsonl for real-time tracking.

Usage:
  python scripts/mcp_call.py LIST                       # list all tool names
  python scripts/mcp_call.py <tool> '<json-args>'       # call a tool
  python scripts/mcp_call.py get_wing_summary '{"session_id":"...","wing_uid":"Wing1"}'

Notes:
  - MCP sessions are server-side, so a session_id from one call is valid in the next
    even though this CLI reconnects each time (~0.5s overhead).
  - Ignore any "Cannot close a running event loop" lines on stderr — cosmetic MCP
    teardown noise; the JSON on stdout is the real result.
"""
import io
import json
import os
import re
import sys
import time
import contextlib
from pathlib import Path

sys.path.insert(0, ".")

LOG = Path("logs/live_agent_chain.jsonl")
# Data-plane cache: replicates the real pipeline's middleware so large handoffs
# (mesh_base64, exported file paths, etc.) don't have to be pasted by hand. Any
# string arg of the form "<tool>__<field>" (e.g. "generate_volume_mesh__mesh_base64")
# is replaced by that field from the cached result of that tool's last call.
CACHE = Path("logs/mcp_call_cache.json")
_REF = re.compile(r"^([a-z_]+)__([A-Za-z0-9_.]+)$")


def _load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            return {}
    return {}


def _dig(d, dotted):
    cur = d
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _substitute(obj, cache):
    """Recursively replace 'tool__field' string refs with cached values."""
    if isinstance(obj, str):
        m = _REF.match(obj)
        if m and m.group(1) in cache:
            val = _dig(cache[m.group(1)], m.group(2))
            if val is not None:
                return val
        return obj
    if isinstance(obj, dict):
        return {k: _substitute(v, cache) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, cache) for v in obj]
    return obj


def _summ(d):
    """Short human summary of a tool result for the live log."""
    if not isinstance(d, dict):
        return str(d)[:200]
    for keys in (("fuel_burned_kg", "gtow_kg"), ("session_id",), ("success", "valid"),
                 ("runtime_seconds", "exit_code"), ("oem_kg",), ("perf.TSFC",)):
        hit = {k: d.get(k) for k in keys if k in d}
        if hit:
            return json.dumps(hit)
    return json.dumps({k: d[k] for k in list(d)[:4]}, default=str)[:200]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    tool = sys.argv[1]
    raw_args = sys.argv[2] if len(sys.argv) > 2 else "{}"
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad JSON args: {e}"}))
        return 1

    # Suppress noisy MCP-connect stderr while loading tools.
    from src.config.loader import load_config
    from scripts.stat_batch_runner import _load_mcp_tools
    with contextlib.redirect_stderr(io.StringIO()):
        tm = _load_mcp_tools(load_config("config/mdo_f25_run_claude.yaml"))

    if tool == "LIST":
        print(json.dumps(sorted(tm.keys()), indent=2))
        return 0
    if tool not in tm:
        print(json.dumps({"error": f"unknown tool '{tool}'", "available": sorted(tm.keys())}, indent=2))
        return 1

    # Data-plane: substitute any "tool__field" refs from cached prior results.
    cache = _load_cache()
    args = _substitute(args, cache)

    t0 = time.time()
    with contextlib.redirect_stderr(io.StringIO()):
        r = tm[tool].forward(**args)
    dt = round(time.time() - t0, 2)

    if isinstance(r, dict):
        d = r
    elif isinstance(r, str) and r.strip()[:1] in "{[":
        try:
            d = json.loads(r)
        except json.JSONDecodeError:
            d = {"raw": r}
    else:
        d = {"raw": str(r)}

    # Cache this result for downstream "tool__field" handoffs (mesh_base64 etc.).
    if isinstance(d, dict):
        cache[tool] = d
        try:
            CACHE.write_text(json.dumps(cache, default=str))
        except Exception:
            pass

    # Live log (append) — tail -f logs/live_agent_chain.jsonl to watch. Args are
    # summarized so a giant mesh blob doesn't bloat the log.
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
        f.flush()

    print(json.dumps(d, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
