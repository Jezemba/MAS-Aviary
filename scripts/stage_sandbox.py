"""Stage sandbox: run one stage, or a few, of the sequential pipeline and watch exactly what happens.

A full two-link run takes hours, and a failure in stage 2 is found after stage 7. This runs only the
stages asked for, through the SAME code a run uses -- the real stage agents (verbatim prompts, tools,
final-answer checks) built by the sequential strategy, the data-plane middleware, the live MCP
servers -- and writes, as it goes:

    <out>/report.html          the page to watch: each stage's exact input, every step's reasoning,
                               tool call and result, the stage output and design.json after it.
                               Reloads itself every 5 s while the sandbox runs.
    <out>/events.jsonl         stage inputs / outputs / design.json, and replay comparisons
    <out>/step_log_attempt1.jsonl, knowledge_base.jsonl, design.json   as a run writes them

Two modes:

  model (default)   Qwen drives the selected stages, as in a run.
  --replay LINK     no model: the tool calls a recorded run made in the selected stages are sent again,
                    through the middleware, to the live servers, and the page shows the recorded result
                    next to today's. Separates "the code does something different now" from "the model
                    chose differently".

Starting mid-pipeline (--seed-from LINK): the data plane is seeded from that link's design.json
(geometry, aero, mass, engine, design space), the stage before the first selected one hands over its
recorded DESIGN_STATE, and that link's carry_over.json is used. A stage that needs the mesh payload
(aerodynamics) cannot be seeded: start it at geometry_engineer.

Not reproduced: the staged handler's completion-criteria bookkeeping (observational in a run) and the
runner's retries -- a sandbox runs each selected stage once.

Usage (on the machine with the GPUs and the five MCP servers):
    python scripts/stage_sandbox.py --stages geometry_engineer,aerodynamics_analyst
    python scripts/stage_sandbox.py --stages mission_architect,simulation_executor --seed-from <link_dir>
    python scripts/stage_sandbox.py --stages aerodynamics_analyst --replay <link_dir> --seed-from <link_dir>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

COMBO = "mdo_f25_sequential_staged_pipeline"


class Events:
    """events.jsonl writer; the report re-renders after every event and every 5 s in between."""

    def __init__(self, out: Path):
        self.path = out / "events.jsonl"
        self.out = out
        self._lock = threading.Lock()

    def emit(self, **event) -> None:
        event.setdefault("time", time.time())
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
        self.render()

    def render(self) -> None:
        try:
            from sandbox_report import render_target

            render_target(self.out)
        except Exception as exc:      # the page must never stop the sandbox
            print(f"  [report] not rendered: {exc}")


def _recorded_rows(link: Path) -> list[dict]:
    logs = sorted(link.glob("step_log_attempt*.jsonl"))
    if not logs:
        raise SystemExit(f"no step_log_attempt*.jsonl in {link}")
    rows = []
    for line in logs[-1].read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") == "step":
            rows.append(row)
    return rows


def _recorded_output(rows: list[dict], stage: str) -> str | None:
    """The stage's final answer in a recorded run: what the handler passed to the next stage."""
    for row in reversed(rows):
        if row.get("agent") != stage:
            continue
        for call in row.get("tool_calls") or []:
            if call.get("name") == "final_answer":
                args = call.get("arguments")
                return str(args.get("answer") if isinstance(args, dict) else args)
    return None


def seed_data_plane(design: dict) -> list[str]:
    """Put a recorded design.json back into the data plane, as the earlier stages would have left it."""
    from src.tools import coupling
    from src.tools.data_plane import get_design_state

    ds = get_design_state()
    store = ds.data_store
    seeded = []
    geo = design.get("geometry") or {}
    if geo.get("wing"):
        store["geometry_wing"] = dict(geo["wing"], _source="sandbox seed")
        seeded.append("geometry.wing")
    if geo.get("fuselage"):
        store["geometry_fuselage"] = dict(geo["fuselage"], _source="sandbox seed")
        seeded.append("geometry.fuselage")
    if geo.get("cpacs_file") and Path(geo["cpacs_file"]).is_file():
        store["morphed_cpacs_path"] = geo["cpacs_file"]
        seeded.append("geometry.cpacs_file")
    aero = design.get("aero") or {}
    if aero.get("cl_cruise") is not None and aero.get("cd_cruise") is not None:
        store["aero_cl_cruise"], store["aero_cd_cruise"] = aero["cl_cruise"], aero["cd_cruise"]
        coupling.put_var(ds, "aero.cl_cruise", aero["cl_cruise"], source_tool="sandbox seed")
        coupling.put_var(ds, "aero.cd_cruise", aero["cd_cruise"], source_tool="sandbox seed")
        if aero.get("drag_assessment"):
            store["aero_drag_assessment"] = aero["drag_assessment"]
        seeded.append("aero")
    st = design.get("structures") or {}
    if st.get("wing_mass_kg") is not None:
        store["mass_wing_kg"] = st["wing_mass_kg"]
        store["mass_wing_source"] = st.get("wing_mass_source")
        seeded.append("structures.wing_mass_kg")
    if st.get("mtom_kg") is not None:
        store["mass_mtom_kg"] = st["mtom_kg"]
        coupling.put_var(ds, "mass.mtom_kg", st["mtom_kg"], source_tool="sandbox seed")
        seeded.append("structures.mtom_kg")
    prop = design.get("propulsion") or {}
    if prop.get("sfc_cruise") is not None:
        store["prop_cycle_outputs"] = {"prop.sfc_cruise": prop.get("sfc_cruise"), "prop.fn_lbf": prop.get("fn_lbf")}
        store["prop_cycle_converged"] = prop.get("cycle_converged")
        seeded.append("propulsion")
    space = design.get("design_space") or {}
    if space.get("geometry"):
        store["design_space_components"] = {k: dict(v) for k, v in space["geometry"].items()}
    if space.get("engine"):
        store["design_space_engine"] = dict(space["engine"])
    if space:
        seeded.append("design_space")
    return seeded


def build_agents(config):
    """The stage agents exactly as a run builds them (batch_runner._execute_combination, sequential)."""
    from src.coordination.coordinator import Coordinator
    from src.logging.logger import InstrumentationLogger
    from src.runners import batch_runner as br

    combo = next(c for c in br.ALL_COMBINATIONS if c.name == COMBO)
    agents_path, coord_path = br._MDO_F25_STRATEGY_CONFIGS[combo.org_structure]
    config.agents_config, config.coordination_config = agents_path, coord_path
    coordinator = Coordinator.from_config(config, logger=InstrumentationLogger(config={}),
                                          strategy_override=combo.org_structure)
    handler = br._build_handler(combo.handler, combo.handler_config)
    if handler is not None:
        coordinator.execution_handler = handler
        coordinator.config["execution_handler"] = combo.handler
    if combo.strategy_config:
        br._deep_merge(coordinator.config, combo.strategy_config)
    coordinator.strategy.initialize(coordinator.agents, coordinator.config)
    return coordinator.agents


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stages", default="", help="comma-separated stage names, in pipeline order (default: all)")
    ap.add_argument("--config", default="config/mdo_f25_run_qwen32b_2gpu.yaml")
    ap.add_argument("--seed-from", type=Path, help="a recorded link folder to start from")
    ap.add_argument("--replay", type=Path, help="a recorded link folder whose tool calls to replay (no model)")
    ap.add_argument("--out", type=Path, help="output folder (default logs/sandbox/<time>_<stages>)")
    args = ap.parse_args()
    os.chdir(ROOT)

    order = ["geometry_engineer", "aerodynamics_analyst", "structures_analyst", "propulsion_analyst",
             "mission_architect", "simulation_executor", "mdo_integrator"]
    stages = [s.strip() for s in args.stages.split(",") if s.strip()] or order
    unknown = [s for s in stages if s not in order]
    if unknown:
        raise SystemExit(f"unknown stage(s) {unknown}; stages are {order}")
    out = args.out or ROOT / "logs" / "sandbox" / (time.strftime("%Y%m%d-%H%M%S") + "_" + "-".join(
        s.split("_")[0] for s in stages) + ("_replay" if args.replay else ""))
    out.mkdir(parents=True, exist_ok=True)
    ev = Events(out)
    print(f"Sandbox folder: {out}\nWatch: {out / 'report.html'}")

    # Same logging a run configures in its child process.
    os.environ["MAS_STEP_LOG"] = "1"
    from src.logging import step_log
    from src.tools.knowledge_base import configure_run

    kb_path = out / "knowledge_base.jsonl"
    configure_run(path=str(kb_path), chain_link=0, attempt=1)
    step_log.configure_from_env(str(kb_path), 1)

    import stat_batch_runner as sbr
    from src.config.loader import load_config
    from src.tools.data_plane import register_presession, reset_design_state

    seed = args.seed_from
    seed_design = json.loads((seed / "design.json").read_text()) if seed and (seed / "design.json").exists() else {}
    carry = None
    if seed and (seed / "carry_over.json").exists():
        carry = json.loads((seed / "carry_over.json").read_text())
        (out / "carry_over.json").write_text(json.dumps(carry, indent=2))      # read_carry_over() finds it here

    config = load_config(args.config)
    tool_map = sbr._load_mcp_tools(config)
    params = (carry or {}).get("aviary_session_start") or sbr.anchor_params_for("baseline", 42)
    setup = sbr.setup_session_with_params(tool_map, params)
    session_id = setup["session_id"]
    reset_design_state()
    register_presession("aviary", session_id)          # exactly as run_combination does

    agents = build_agents(config) if not args.replay else None
    if args.replay:
        from src.tools.tool_loader import load_tools_for_agent

        replay_tools = {t.name: t for t in load_tools_for_agent([], config)}
    seeded = seed_data_plane(seed_design) if seed_design else []
    if seeded:
        from src.tools.data_plane import _write_design_json

        _write_design_json()                            # the seeded record is what the first stage sees

    recorded = _recorded_rows(seed) if seed and list(seed.glob("step_log_attempt*.jsonl")) else []
    previous_outputs: list = []
    first = order.index(stages[0])
    if first > 0 and recorded:
        prev_stage = order[first - 1]
        prev_text = _recorded_output(recorded, prev_stage)
        if prev_text:
            previous_outputs.append((prev_stage, prev_text, None))

    ev.emit(type="start", mode="replay" if args.replay else "model", stages=stages,
            seed_from=str(seed) if seed else None, seeded=seeded, aviary_session=session_id,
            handed_over_from=previous_outputs[0][0] if previous_outputs else None,
            carry_over=bool(carry))

    stop = threading.Event()

    def refresher():
        while not stop.wait(5):
            ev.render()

    threading.Thread(target=refresher, daemon=True).start()

    from src.coordination import prompt_assembly

    replay_rows = _recorded_rows(args.replay) if args.replay else []
    try:
        for stage in stages:
            t0 = time.time()
            error = None
            if args.replay:
                ev.emit(type="stage_start", stage=stage, input="(replay: no model; the recorded tool calls are sent again)")
                for row in [r for r in replay_rows if r.get("agent") == stage]:
                    for call in row.get("tool_calls") or []:
                        name, call_args = call.get("name"), call.get("arguments") or {}
                        if name == "final_answer":
                            continue
                        tool = replay_tools.get(name)
                        try:
                            now = tool(**call_args) if tool else f"tool {name!r} is not loaded"
                        except Exception as exc:
                            now = f"{type(exc).__name__}: {exc}"
                        ev.emit(type="replay_call", stage=stage, tool=name, arguments=call_args,
                                recorded=row.get("observations"), replayed=str(now))
                content = _recorded_output(replay_rows, stage) or ""
            else:
                agent = agents[stage]
                context = prompt_assembly.verbatim_stage_input(
                    getattr(agent, "description", "") or "", previous_outputs,
                    prompt_assembly.read_carry_over(), stage, prompt_assembly.read_design_json_text())
                ev.emit(type="stage_start", stage=stage, input=context)
                try:
                    raw = agent.run(context)
                    content = str(raw) if raw is not None else ""
                except Exception as exc:             # the handler records this text and moves on, too
                    content, error = f"Agent error: {exc}", f"{type(exc).__name__}: {exc}"
            ev.emit(type="stage_end", stage=stage, output=content, error=error, seconds=time.time() - t0,
                    design_json=prompt_assembly.read_design_json_text())
            previous_outputs.append((stage, content, None))
    finally:
        stop.set()
        ev.emit(type="done")
        print(f"Done. Report: {out / 'report.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
