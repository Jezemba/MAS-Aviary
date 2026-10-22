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
    args = ap.parse_args()

    from evaluator import CoupledEvaluator

    vs = variables(args.variables)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "run.json").write_text(json.dumps({**vars(args), "out": str(args.out),
                                                   "variables": [v.__dict__ for v in vs]}, indent=1))
    evaluator = CoupledEvaluator(args.config, args.out, coarse_mesh=not args.full_mesh)
    problem = Problem(vs, lambda x: evaluator.evaluate(x, vs), args.out / "evaluations.jsonl")
    algo = ALGORITHMS[args.algo]
    if args.algo == "slsqp":
        algo(problem, max_iter=args.max_iter)
    else:
        algo(problem, pop=args.pop, max_iter=args.max_iter, seed=args.seed)
    last = problem.history[-1] if problem.history else {}
    print(json.dumps({"iterations": len(problem.history), "evaluations": problem.n_evals,
                      "best_feasible_fuel_kg": last.get("best_feasible_fuel_kg"),
                      "best_feasible_gtow_kg": last.get("best_feasible_gtow_kg")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
