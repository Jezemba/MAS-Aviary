"""Traditional optimizer baselines on the coupled pipeline: SLSQP (gradient-based, finite differences), PSO and a
real-coded GA. All three minimise fuel burn in the unit box of the design space, starting from the default
design; GTOW is recorded for every design.

One "iteration" is: a swarm update (PSO), a generation (GA), a major SLSQP iteration. Iteration 0 is the
starting design / initial population. Every evaluation goes through `Problem.evaluate`, which caches by design,
so a resumed run (same seed) replays its recorded evaluations instead of recomputing them.
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Callable

from design_space import GTOW_LIMIT_KG, Variable, defaults, from_unit, to_unit

NOT_FLOWN_PENALTY = 1.0e6       # a design that did not fly is worse than any that did


def penalised(rec: dict) -> float:
    """The objective: fuel burn (Jessica 2026-10-22, option b: fuel only; GTOW is recorded and plotted against
    its limit, not penalised -- the default design is already above it)."""
    if not rec.get("flown"):
        return NOT_FLOWN_PENALTY
    return float(rec["fuel_burned_kg"])


def feasible(rec: dict) -> bool:
    return bool(rec.get("flown")) and (rec.get("gtow_kg") or math.inf) <= GTOW_LIMIT_KG


class Problem:
    """The evaluation log, cache and per-iteration history of one optimizer run."""

    def __init__(self, vs: tuple[Variable, ...], evaluate: Callable[[list[float]], dict], log_path: Path,
                 evaluate_batch: Callable[[list[list[float]]], list[dict]] | None = None):
        self.vs, self._evaluate, self.log_path = vs, evaluate, Path(log_path)
        # Parallel workers when given (designs in physical units -> records, in order); else one at a time.
        self._evaluate_batch = evaluate_batch or (lambda xs: [evaluate(x) for x in xs])
        self.cache: dict[tuple, dict] = {}
        self.history: list[dict] = []
        self.iteration = 0
        self.n_evals = 0
        if self.log_path.exists():                       # resume: earlier evaluations are reused
            for line in self.log_path.read_text().splitlines():
                rec = json.loads(line)
                if rec.get("kind") == "evaluation":
                    self.cache[self._key(rec["unit"])] = rec

    @staticmethod
    def _key(u: list[float]) -> tuple:
        return tuple(round(float(ui), 6) for ui in u)

    def _write(self, rec: dict) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def evaluate(self, u: list[float]) -> dict:
        return self.evaluate_many([u])[0]

    def evaluate_many(self, us: list[list[float]]) -> list[dict]:
        """Evaluate several designs (the new ones in one parallel batch); records in the order given."""
        us = [[min(1.0, max(0.0, float(ui))) for ui in u] for u in us]
        keys = [self._key(u) for u in us]
        todo = []
        for key in keys:
            if key not in self.cache and key not in todo:
                todo.append(key)
        if todo:
            for key, rec in zip(todo, self._evaluate_batch([from_unit(list(k), self.vs) for k in todo])):
                rec.update(kind="evaluation", unit=list(key), objective=penalised(rec), iteration=self.iteration)
                self.cache[key] = rec
                self._write(rec)
        out = []
        for key in keys:
            self.n_evals += 1
            out.append(dict(self.cache[key], eval_index=self.n_evals))
        return out

    def end_iteration(self, evaluated: list[dict]) -> dict:
        """Record the best feasible design found so far after this iteration."""
        seen = [r for r in self.cache.values()]
        best = min((r for r in seen if r.get("flown")), key=lambda r: r["fuel_burned_kg"], default=None)
        best_any = min(seen, key=lambda r: r["objective"], default=None)
        row = {"kind": "iteration", "iteration": self.iteration, "evaluations": self.n_evals,
               "best_fuel_kg": best and best["fuel_burned_kg"],
               "best_gtow_kg": best and best["gtow_kg"],
               "best_design": best and best["design"],
               "best_within_gtow_limit": bool(best and feasible(best)),
               "best_objective": best_any and best_any["objective"],
               "iteration_fuel_kg": [r.get("fuel_burned_kg") for r in evaluated],
               "iteration_gtow_kg": [r.get("gtow_kg") for r in evaluated]}
        self.history.append(row)
        self._write(row)
        self.iteration += 1
        return row


def _converged(history: list[dict], window: int, rtol: float) -> bool:
    """No improvement of the best objective by more than rtol over the last `window` iterations."""
    if len(history) <= window:
        return False
    old, new = history[-window - 1]["best_objective"], history[-1]["best_objective"]
    return old is not None and new is not None and (old - new) <= rtol * abs(old)


def pso(problem: Problem, pop: int = 6, max_iter: int = 100, seed: int = 0, window: int = 10, rtol: float = 1e-3,
        w: float = 0.7, c1: float = 1.5, c2: float = 1.5, vmax: float = 0.2) -> list[dict]:
    rng = random.Random(seed)
    n = len(problem.vs)
    x = [to_unit(defaults(problem.vs), problem.vs)] + [[rng.random() for _ in range(n)] for _ in range(pop - 1)]
    v = [[rng.uniform(-vmax, vmax) for _ in range(n)] for _ in range(pop)]
    recs = problem.evaluate_many(x)
    pbest = [list(xi) for xi in x]
    pbest_f = [r["objective"] for r in recs]
    g = min(range(pop), key=lambda i: pbest_f[i])
    problem.end_iteration(recs)
    while problem.iteration <= max_iter and not _converged(problem.history, window, rtol):
        for i in range(pop):
            for d in range(n):
                v[i][d] = (w * v[i][d] + c1 * rng.random() * (pbest[i][d] - x[i][d])
                           + c2 * rng.random() * (pbest[g][d] - x[i][d]))
                v[i][d] = max(-vmax, min(vmax, v[i][d]))
                x[i][d] = max(0.0, min(1.0, x[i][d] + v[i][d]))
        recs = problem.evaluate_many(x)
        for i, r in enumerate(recs):
            if r["objective"] < pbest_f[i]:
                pbest[i], pbest_f[i] = list(x[i]), r["objective"]
        g = min(range(pop), key=lambda i: pbest_f[i])
        problem.end_iteration(recs)
    return problem.history


def ga(problem: Problem, pop: int = 6, max_iter: int = 100, seed: int = 0, window: int = 10, rtol: float = 1e-3,
       alpha: float = 0.5, sigma: float = 0.1) -> list[dict]:
    """Real-coded GA: elitism 1, binary tournament, BLX-alpha crossover, Gaussian mutation (rate 1/n per gene)."""
    rng = random.Random(seed)
    n = len(problem.vs)
    popn = [to_unit(defaults(problem.vs), problem.vs)] + [[rng.random() for _ in range(n)] for _ in range(pop - 1)]
    recs = problem.evaluate_many(popn)
    fit = [r["objective"] for r in recs]
    problem.end_iteration(recs)

    def tournament() -> list[float]:
        a, b = rng.randrange(pop), rng.randrange(pop)
        return popn[a] if fit[a] <= fit[b] else popn[b]

    while problem.iteration <= max_iter and not _converged(problem.history, window, rtol):
        elite = min(range(pop), key=lambda i: fit[i])
        children = [list(popn[elite])]
        while len(children) < pop:
            p1, p2 = tournament(), tournament()
            child = []
            for d in range(n):
                lo, hi = min(p1[d], p2[d]), max(p1[d], p2[d])
                span = hi - lo
                gene = rng.uniform(lo - alpha * span, hi + alpha * span)
                if rng.random() < 1.0 / n:
                    gene += rng.gauss(0.0, sigma)
                child.append(max(0.0, min(1.0, gene)))
            children.append(child)
        popn = children
        recs = problem.evaluate_many(popn)
        fit = [r["objective"] for r in recs]
        problem.end_iteration(recs)
    return problem.history


def slsqp(problem: Problem, max_iter: int = 100, eps: float = 0.02, ftol: float = 1e-4) -> list[dict]:
    """SLSQP on fuel / 1e4 with forward-difference gradients (absolute step eps in the unit box). The point and its
    n stepped points are evaluated in one parallel batch. A design that did not fly returns the penalty."""
    from scipy.optimize import minimize

    n = len(problem.vs)
    x0 = to_unit(defaults(problem.vs), problem.vs)
    pending: list[dict] = []

    def value(r: dict) -> float:
        return r["objective"] / 1.0e4

    def batch(us):
        recs = problem.evaluate_many([list(u) for u in us])
        pending.extend(recs)
        return recs

    def f(u):
        return value(batch([u])[0])

    def jac(u):
        u = [float(ui) for ui in u]
        steps = []
        for d in range(n):
            h = eps if u[d] + eps <= 1.0 else -eps        # step inward at the upper bound
            v = list(u)
            v[d] += h
            steps.append((v, h))
        recs = batch([u] + [v for v, _ in steps])
        f0 = value(recs[0])
        return [(value(r) - f0) / h for r, (_, h) in zip(recs[1:], steps)]

    batch([x0])
    problem.end_iteration(list(pending))
    pending.clear()

    def callback(_u):
        problem.end_iteration(list(pending))
        pending.clear()

    minimize(f, x0, method="SLSQP", jac=jac, bounds=[(0.0, 1.0)] * n, callback=callback,
             options={"maxiter": max_iter, "ftol": ftol})
    if pending:
        problem.end_iteration(list(pending))
    return problem.history


ALGORITHMS = {"pso": pso, "ga": ga, "slsqp": slsqp}
