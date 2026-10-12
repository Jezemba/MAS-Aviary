"""Hand-off sandbox: does the mdo_integrator give a recommendation, and does it reach the right agent?

Two links of one or more combinations, in minutes instead of hours. Only the agents whose decisions are
under test run on the model -- the mdo_integrator (does it write a recommendation?) and, in orchestrated
combinations, the orchestrator (does it route the recommendation?). Every other agent is a STUB: it records
the exact message it was given and answers with a DESIGN_STATE recorded from a real run, so no MCP server,
mesh or SU2 solve is involved.

The run itself goes through the runner's own pieces, in-process: write_carry_over, the knowledge-base run
configuration, batch_runner.run_combination (coordinator, strategy, handler, verbatim prompt assembly) and
integrator_text. design.json is the recorded one (the stubs make no tool calls, so the framework has nothing
to write).

    python scripts/handoff_sandbox.py                                  # all sequential + orchestrated combos
    python scripts/handoff_sandbox.py --combos mdo_f25_sequential_iterative_feedback

Writes <out>/report.md (the answers) and <out>/<combo>/link<N>/{inputs.jsonl, carry_over.json, design.json}.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

COMBOS = ["mdo_f25_sequential_staged_pipeline", "mdo_f25_sequential_iterative_feedback",
          "mdo_f25_sequential_graph_routed", "mdo_f25_orchestrated_staged_pipeline",
          "mdo_f25_orchestrated_iterative_feedback", "mdo_f25_orchestrated_graph_routed"]
RECORDED = ROOT / "logs/sandbox/20261010-091649_sandbox"

# Recorded DESIGN_STATEs (sandbox 20261010-091649, SC(2) wing day); simulation is aipexws8 v2sc2_t480 link 1
# (flown, not converged), since the sandbox's own simulation was the zero-fuel one.
SIMULATION = ('DESIGN_STATE:\n  SESSION_ID: f5b343a9-5f01-4adf-992d-c03ed002476f\n  CONVERGED: false\n  EXIT_CODE: 1\n'
              '  FUEL_BURNED_KG: 22998.6\n  GROSS_MASS_KG: 102187.6\n  WING_MASS_KG: 9337.7\n  RESERVE_FUEL_KG: 1360.8\n'
              '  ZERO_FUEL_WEIGHT_KG: 77828.2\n  CRUISE_CL_AVIARY: 0.626\n  CRUISE_CD_AVIARY: 0.0448\n  RUNTIME_S: 21.0\n'
              '  CONVERGENCE_NOTE: flown; convergence_warning -- optimiser stopped short (Positive directional '
              'derivative); residuals 726 kg climb mass, 84 kg mass balance, 22 nm descent distance')
_ROLE_KEYS = [("geometry", "geometry_engineer"), ("aero", "aerodynamics_analyst"), ("struct", "structures_analyst"),
              ("propul", "propulsion_analyst"), ("mission", "mission_architect"), ("simul", "simulation_executor")]


def recorded_outputs(all_stubs: bool = False) -> dict:
    out = {}
    for line in (RECORDED / "events.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e.get("type") == "stage_end" and e.get("output"):
            out[e["stage"]] = e["output"]
    out["simulation_executor"] = SIMULATION
    if not all_stubs:
        out.pop("mdo_integrator", None)
    return out


def role_of(agent_name: str) -> str | None:
    n = agent_name.lower()
    return next((role for key, role in _ROLE_KEYS if key in n), None)


ALL_STUBS = False


def is_real(agent_name: str) -> bool:
    n = agent_name.lower()
    return not ALL_STUBS and (n == "orchestrator" or "integrator" in n)


class Recorder:
    """Stubs every agent except the orchestrator and the integrator; records every agent's input and output."""

    def __init__(self, outputs: dict):
        self.outputs = outputs
        self.rows: list[dict] = []
        self._done: set = set()

    def install(self, agent_name: str, agent) -> None:
        if id(agent) in self._done:
            return
        self._done.add(id(agent))
        orig = agent.run
        real = is_real(agent_name)
        key = "mdo_integrator" if "integrator" in agent_name.lower() else (role_of(agent_name) or "")
        canned = self.outputs.get(key, "DESIGN_STATE:\n  DONE: true")

        def run(*args, **kwargs):
            task = args[0] if args else kwargs.get("task", "")
            t0 = time.time()
            result = orig(*args, **kwargs) if real else canned
            self.rows.append({"agent": agent_name, "real": real, "seconds": round(time.time() - t0, 1),
                              "input": str(task), "output": str(result)})
            return result

        agent.run = run


class _DummyModel:
    """--all-stubs: nothing may call a model."""

    model_id = "none (all stubs)"

    def __call__(self, *a, **k):
        raise RuntimeError("a model was called in --all-stubs mode")

    generate = __call__


def patch_runner(recorder: Recorder) -> None:
    from src.runners import batch_runner
    from src.tools import design_json

    design_json.write_design = lambda state: None        # keep the recorded design.json
    original = batch_runner._install_trace_capture

    def install(coordinator):
        original(coordinator)
        for name, agent in list(coordinator.agents.items()):
            recorder.install(name, agent)
        wrap = coordinator._wrap_agent_for_trace

        def wrap_and_stub(name, agent, _wrap=wrap):
            _wrap(name, agent)
            recorder.install(name, agent)

        coordinator._wrap_agent_for_trace = wrap_and_stub

    batch_runner._install_trace_capture = install


def run_link(combo, config, link_dir: Path, link: int, carried: str, recorder: Recorder) -> str:
    import stat_batch_runner as sbr
    from src.runners.batch_runner import run_combination
    from src.tools.knowledge_base import configure_run

    link_dir.mkdir(parents=True, exist_ok=True)
    space = json.loads((RECORDED / "design.json").read_text()).get("design_space")
    sbr.write_carry_over(link_dir, link, carried or None, None, {}, space)
    shutil.copy(RECORDED / "design.json", link_dir / "design.json")
    configure_run(path=str(link_dir / "knowledge_base.jsonl"), chain_link=link, attempt=1, seed=None)
    task = sbr.build_task_with_session(sbr.base_task_for(combo.name, None), "handoff-sandbox-session", {},
                                       prior_feedback=carried or None)
    start = len(recorder.rows)
    result = run_combination(combo, task, config, session_id="handoff-sandbox-session",
                             model=_DummyModel() if ALL_STUBS else None)
    rows = recorder.rows[start:]
    status = {k: str(getattr(result, k, None))[:2000] for k in ("status", "error", "success", "termination_reason")}
    (link_dir / "run_status.json").write_text(json.dumps(status, indent=2))
    print(f"    link {link}: {len(rows)} agent calls; {status}", flush=True)
    with open(link_dir / "inputs.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return sbr.integrator_text(result)


def assess(combo_name: str, link0_integrator: str, link1_dir: Path) -> list[str]:
    from src.coordination.prompt_assembly import parse_recommendation

    rec = parse_recommendation(link0_integrator)
    to, change = rec.get("RECOMMENDATION_TO"), rec.get("RECOMMENDED_CHANGE")
    lines = [f"## {combo_name}", "",
             "**Q1 -- did the mdo_integrator give a recommendation (link 1)?** "
             + ("yes, readable: RECOMMENDATION_TO = `%s`; RECOMMENDED_CHANGE = `%s`" % (to, change) if to else
                ("only as prose (no RECOMMENDATION_TO field could be read)" if re.search(r"recommend", link0_integrator or "", re.I)
                 else "NO recommendation in its output")),
             "", "<details><summary>integrator output, link 1</summary>", "", "```", (link0_integrator or "(none)")[:4000],
             "```", "</details>", ""]
    carried = json.loads((link1_dir / "carry_over.json").read_text()).get("previous_integrator_design_state", "")
    lines.append("**Carried to link 2:** " + ("the integrator's text" if carried.strip() == (link0_integrator or "").strip()
                                             else "a DIFFERENT text (not the integrator's)"))
    rows = [json.loads(x) for x in (link1_dir / "inputs.jsonl").read_text().splitlines()]
    probe = (change or "")[:60].strip()
    lines += ["", "**Q2 -- what each agent received in link 2** (first message per agent):", "",
              "| agent | real model | recommendation block | contains the recommended change | names the target role |",
              "|---|---|---|---|---|"]
    seen = set()
    for r in rows:
        if r["agent"] in seen:
            continue
        seen.add(r["agent"])
        text = r["input"]
        block = "RECOMMENDATION from the previous iteration" in text or "RECOMMENDED_CHANGE" in text
        has_change = bool(probe) and probe.lower() in text.lower()
        names = bool(to) and to.lower() in text.lower()
        lines.append(f"| {r['agent']} | {'yes' if r['real'] else 'stub'} | {'yes' if block else 'no'} | "
                     f"{'yes' if has_change else 'no'} | {'yes' if names else 'no'} |")
    if any(r["real"] and r["agent"] == "orchestrator" for r in rows):
        lines += ["", "<details><summary>orchestrator's assignments in link 2 (agent: task)</summary>", "", "```"]
        for r in rows:
            if not r["real"]:
                lines.append(f"{r['agent']}: {r['input'][:1500]}")
                lines.append("-" * 60)
        lines += ["```", "</details>"]
    lines.append("")
    return lines


def main() -> int:
    global RECORDED, ALL_STUBS
    ap = argparse.ArgumentParser()
    ap.add_argument("--combos", default=",".join(COMBOS))
    ap.add_argument("--config", default="config/mdo_f25_run_qwen32b_2gpu.yaml")
    ap.add_argument("--recorded", type=Path, default=RECORDED, help="recorded link with events.jsonl + design.json")
    ap.add_argument("--all-stubs", action="store_true", help="plumbing smoke test: no model, the integrator is stubbed too")
    ap.add_argument("--out", type=Path, default=ROOT / "logs/handoff_sandbox" / time.strftime("%Y%m%d-%H%M%S"))
    args = ap.parse_args()

    from src.config.loader import load_config
    from src.runners.batch_runner import AVIARY_COMBINATIONS

    RECORDED, ALL_STUBS = args.recorded, args.all_stubs
    config = load_config(args.config)
    by_name = {c.name: c for c in AVIARY_COMBINATIONS}
    recorder = Recorder(recorded_outputs(args.all_stubs))
    patch_runner(recorder)
    args.out.mkdir(parents=True, exist_ok=True)
    report = ["# Hand-off sandbox: recommendation given? passed to the right agent?", "",
              f"Stubbed agents answer with recorded DESIGN_STATEs; the orchestrator and the mdo_integrator run on "
              f"`{config.llm.model_id}`.", ""]
    for name in [c.strip() for c in args.combos.split(",") if c.strip()]:
        combo = by_name[name]
        print(f"=== {name}", flush=True)
        try:
            t0 = time.time()
            integ0 = run_link(combo, config, args.out / name / "link0", 0, "", recorder)
            run_link(combo, config, args.out / name / "link1", 1, integ0, recorder)
            report += assess(name, integ0, args.out / name / "link1")
            report.append(f"_{name}: {time.time() - t0:.0f} s_\n")
        except Exception as exc:          # one combo failing must not hide the others
            import traceback

            report += [f"## {name}", "", f"**FAILED to run:** `{exc}`", "", "```", traceback.format_exc()[-3000:], "```", ""]
        (args.out / "report.md").write_text("\n".join(report))
        print(f"    report: {args.out / 'report.md'}", flush=True)
    import os

    os._exit(0)        # do not wait on MCP/model threads


if __name__ == "__main__":
    sys.exit(main())
