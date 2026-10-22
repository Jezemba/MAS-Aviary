"""Optimizer baselines (no servers): the three algorithms on an analytic stand-in for the coupled pipeline."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "optimizer_baselines"))

from design_space import GTOW_LIMIT_KG, defaults, from_unit, to_unit, variables  # noqa: E402
from optimizers import ALGORITHMS, NOT_FLOWN_PENALTY, Problem, feasible, penalised  # noqa: E402

VS = variables("geometry")


def fake(x):
    """Fuel falls with aspect ratio and smaller area; GTOW rises with area; very small wings do not fly."""
    d = dict(zip([v.name for v in VS], x))
    fuel = 20000 - 800 * (d["wing_aspect_ratio"] - 9.0) + 40 * abs(d["wing_area"] - 120) + 5 * abs(d["wing_sweep"] - 25)
    gtow = 80000 + 120 * (d["wing_area"] - 100) + 50 * (d["fuselage_length"] - 37)
    flown = d["wing_area"] > 102
    return {"design": d, "fuel_burned_kg": fuel if flown else 0.0, "gtow_kg": gtow, "flown": flown, "failure": None}


def test_unit_box_round_trip_and_defaults():
    u = to_unit(defaults(VS), VS)
    assert all(0.0 <= ui <= 1.0 for ui in u)
    assert from_unit(u, VS) == pytest.approx(defaults(VS))
    assert from_unit([-1.0] * len(VS), VS) == pytest.approx([v.lower for v in VS])


def test_the_objective_is_fuel_only_and_unflown_designs_rank_last():
    ok = {"flown": True, "fuel_burned_kg": 20000.0, "gtow_kg": 85000.0}
    heavy = {"flown": True, "fuel_burned_kg": 19000.0, "gtow_kg": GTOW_LIMIT_KG + 1000}
    assert penalised(heavy) < penalised(ok) < NOT_FLOWN_PENALTY == penalised({"flown": False})
    assert feasible(ok) and not feasible(heavy)                 # still recorded, not penalised


def test_a_batch_is_evaluated_once_per_new_design(tmp_path):
    batches = []

    def batch(xs):
        batches.append(len(xs))
        return [fake(x) for x in xs]

    problem = Problem(VS, fake, tmp_path / "e.jsonl", evaluate_batch=batch)
    u = to_unit(defaults(VS), VS)
    recs = problem.evaluate_many([u, u, [0.5] * len(VS)])
    assert len(recs) == 3 and batches == [2]                    # the duplicate is evaluated once
    problem.evaluate_many([u])
    assert batches == [2]                                        # cached


@pytest.mark.parametrize("algo", ["pso", "ga", "slsqp"])
def test_each_algorithm_improves_on_the_default_and_logs_every_iteration(tmp_path, algo):
    problem = Problem(VS, fake, tmp_path / "evaluations.jsonl")
    kwargs = {"max_iter": 25} if algo == "slsqp" else {"pop": 6, "max_iter": 25, "seed": 1}
    history = ALGORITHMS[algo](problem, **kwargs)
    start, end = history[0]["best_fuel_kg"], history[-1]["best_fuel_kg"]
    assert end < start
    assert [h["iteration"] for h in history] == list(range(len(history)))
    rows = [json.loads(l) for l in (tmp_path / "evaluations.jsonl").read_text().splitlines()]
    assert sum(r["kind"] == "iteration" for r in rows) == len(history)
    first = next(r for r in rows if r["kind"] == "evaluation")
    assert first["design"]["wing_area"] == pytest.approx(VS[0].default)       # starts from the default design


def test_a_resumed_run_replays_its_evaluations(tmp_path):
    calls = []

    def counting(x):
        calls.append(x)
        return fake(x)

    ALGORITHMS["pso"](Problem(VS, counting, tmp_path / "e.jsonl"), pop=6, max_iter=5, seed=3)
    n = len(calls)
    calls.clear()
    ALGORITHMS["pso"](Problem(VS, counting, tmp_path / "e.jsonl"), pop=6, max_iter=5, seed=3)
    assert n > 0 and calls == []                                                # nothing recomputed


def test_plots_and_table_are_written(tmp_path):
    import plot_baselines

    for algo in ("pso", "ga"):
        run = tmp_path / algo
        run.mkdir()
        (run / "run.json").write_text(json.dumps({"algo": algo}))
        ALGORITHMS[algo](Problem(VS, fake, run / "evaluations.jsonl"), pop=6, max_iter=22, seed=2)
    out = tmp_path / "plots"
    sys.argv = ["plot", "--runs", str(tmp_path / "pso"), str(tmp_path / "ga"), "--out", str(out)]
    assert plot_baselines.main() == 0
    for name in ("fuel_vs_iteration.png", "gtow_vs_iteration.png", "pareto_fuel_gtow.png", "best_at_iterations.csv"):
        assert (out / name).stat().st_size > 0
    table = (out / "best_at_iterations.csv").read_text()
    assert ",5," in table and ",20," in table and "converged" in table
