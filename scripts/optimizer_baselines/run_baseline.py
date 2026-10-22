"""Run one optimizer baseline on the coupled pipeline (no LLM), to convergence, logging every evaluation.

    PYTHONPATH=. ../.venv/bin/python scripts/optimizer_baselines/run_baseline.py --algo pso --out logs/baselines/pso

One run to convergence gives the 5 / 10 / 15 / 20-iteration answers too: with the same seed, capping the run
does not change its earlier iterations, so the best-so-far at those iterations is read from the history
(plot_baselines.py). Relaunching with the same --out resumes from the evaluation log.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_space import variables  # noqa: E402
from optimizers import ALGORITHMS, Problem  # noqa: E402

_EVALUATOR = None
_VARIABLES = None


def _init_worker(config: str, out: str, coarse: bool, port_offset: int, which: str, counter, tigl_ports) -> None:
    """One coupled evaluator per worker process: its own MCP connections, data plane, knowledge base, and its
    own tigl server when tigl_ports is given."""
    from evaluator import CoupledEvaluator

    global _EVALUATOR, _VARIABLES
    with counter.get_lock():
        index = counter.value
        counter.value += 1
    _VARIABLES = variables(which)
    _EVALUATOR = CoupledEvaluator(config, Path(out) / "workers" / f"w{index}", coarse_mesh=coarse,
                                  port_offset=port_offset,
                                  tigl_port=tigl_ports[index % len(tigl_ports)] if tigl_ports else None)


def _evaluate_in_worker(x: list[float]) -> dict:
    return _EVALUATOR.evaluate(x, _VARIABLES)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--algo", choices=sorted(ALGORITHMS), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--variables", default="geometry", help="geometry | geometry+engine")
    ap.add_argument("--pop", type=int, default=6, help="population (PSO / GA)")
    ap.add_argument("--max-iter", type=int, default=100, help="upper bound; the run stops earlier on convergence")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--config", default="config/mdo_f25_run_qwen32b_2gpu.yaml")
    ap.add_argument("--full-mesh", action="store_true", help="the agents' mesh instead of the coarse one")
    ap.add_argument("--workers", type=int, default=6, help="designs evaluated in parallel")
    ap.add_argument("--port-offset", type=int, default=0, help="use MCP servers on the config's ports + offset")
    ap.add_argument("--tigl-ports", default="", help="one tigl server per worker, e.g. 9510-9515")
    args = ap.parse_args()
    tigl_ports = []
    if args.tigl_ports:
        lo, hi = (int(p) for p in args.tigl_ports.split("-"))
        tigl_ports = list(range(lo, hi + 1))

    import multiprocessing as mp

    vs = variables(args.variables)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "run.json").write_text(json.dumps({**vars(args), "out": str(args.out),
                                                   "variables": [v.__dict__ for v in vs]}, indent=1))
    ctx = mp.get_context("spawn")
    counter = ctx.Value("i", 0)
    pool = ctx.Pool(
        args.workers, initializer=_init_worker,
        initargs=(args.config, str(args.out), not args.full_mesh, args.port_offset, args.variables, counter,
                  tigl_ports))
    problem = Problem(vs, lambda x: pool.apply(_evaluate_in_worker, (x,)), args.out / "evaluations.jsonl",
                      evaluate_batch=lambda xs: pool.map(_evaluate_in_worker, xs, chunksize=1))
    algo = ALGORITHMS[args.algo]
    if args.algo == "slsqp":
        algo(problem, max_iter=args.max_iter)
    else:
        algo(problem, pop=args.pop, max_iter=args.max_iter, seed=args.seed)
    pool.close()
    pool.join()
    last = problem.history[-1] if problem.history else {}
    print(json.dumps({"iterations": len(problem.history), "evaluations": problem.n_evals,
                      "best_fuel_kg": last.get("best_fuel_kg"), "best_gtow_kg": last.get("best_gtow_kg")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
