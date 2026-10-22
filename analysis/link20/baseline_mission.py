"""Fly the shared link-1 starting design (chain_anchor.json) once, unchanged, on its own aviary-mcp server.

This is the baseline fuel for the reference line: the same calls the runner makes before agents act on link 1
(create_session with the anchor as initial_parameters, configure_mission from the canonical baseline), then
run_simulation and get_results. Point it at a SEPARATE aviary-mcp instance, never the one a study is using.

Usage (aviary env on aipexws8):
    python baseline_mission.py --url http://127.0.0.1:8601/mcp --anchor chain_anchor.json --out baseline.json
    python baseline_mission.py --url ... --list      # print the tool schemas only
    python baseline_mission.py --url ... --anchor chain_anchor.json --sweep 5 10 15 20 1000 --out sweep.jsonl
        # one fresh session per optimizer_max_iter (SLSQP max_iter), one JSON line each
"""

import argparse
import asyncio
import json
from pathlib import Path

from fastmcp import Client

# config/mdo_f25_canonical_baseline.yaml -> mission (the runner's configure_mission arguments).
CANONICAL_MISSION = {"range_nmi": 2500, "num_passengers": 239, "cruise_mach": 0.78,
                     "cruise_altitude_ft": 33000, "optimizer_max_iter": 200}


def payload(res):
    if getattr(res, "structured_content", None):
        return res.structured_content
    return json.loads(res.content[0].text)


async def fly(c, anchor, mission, timeout):
    """create_session -> configure_mission -> run_simulation -> get_results on a fresh session."""
    out = {"mission": mission}
    out["create_session"] = payload(await c.call_tool("create_session", {"initial_parameters": anchor}))
    sid = out["create_session"]["session_id"]
    out["configure_mission"] = payload(await c.call_tool("configure_mission", {"session_id": sid, **mission}))
    out["run_simulation"] = payload(await c.call_tool(
        "run_simulation", {"session_id": sid, "timeout_seconds": timeout}, timeout=timeout + 120))
    out["get_results"] = payload(await c.call_tool("get_results", {"session_id": sid}))
    return out


async def main(args):
    async with Client(args.url) as c:
        tools = await c.list_tools()
        if args.list:
            for t in tools:
                print(t.name, json.dumps(t.inputSchema)[:1500], "\n")
            return
        anchor = json.loads(Path(args.anchor).read_text())["anchor_params"]
        if args.sweep:
            with open(args.out, "a") as f:
                for n in args.sweep:
                    r = await fly(c, anchor, {**CANONICAL_MISSION, "optimizer_max_iter": n}, args.timeout)
                    sim = r["run_simulation"]
                    row = {"optimizer_max_iter": n, **{k: sim.get(k) for k in (
                        "success", "converged", "exit_code", "iterations", "runtime_seconds")},
                           **(sim.get("summary") or {}),
                           "optimizer_message": (sim.get("failure") or {}).get("optimizer_message"),
                           "session_id": r["create_session"]["session_id"]}
                    f.write(json.dumps(row, default=str) + "\n")
                    f.flush()
                    print(json.dumps(row, default=str))
            return
        out = {"anchor_params": anchor, "mission": CANONICAL_MISSION}
        out["create_session"] = payload(await c.call_tool("create_session", {"initial_parameters": anchor}))
        sid = out["create_session"]["session_id"]
        out["configure_mission"] = payload(await c.call_tool("configure_mission", {"session_id": sid, **CANONICAL_MISSION}))
        out["run_simulation"] = payload(await c.call_tool(
            "run_simulation", {"session_id": sid, "timeout_seconds": args.timeout}, timeout=args.timeout + 120))
        out["get_results"] = payload(await c.call_tool("get_results", {"session_id": sid}))
        Path(args.out).write_text(json.dumps(out, indent=2, default=str))
        print(json.dumps(out["run_simulation"].get("summary"), indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--anchor")
    ap.add_argument("--out")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sweep", type=int, nargs="+", help="optimizer_max_iter values, one fresh session each")
    asyncio.run(main(ap.parse_args()))
