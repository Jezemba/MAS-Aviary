"""optimizer_compare: one value per optimizer iteration = the lowest fuel flown IN that iteration."""

import json

from analysis.link20.optimizer_compare import LINKS, optimizer_series


def _write(tmp_path, rows):
    run = tmp_path / "pso"
    run.mkdir()
    (run / "evaluations.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return run


def _ev(it, fuel, flown=True, converged=True):
    return {"kind": "evaluation", "iteration": it, "fuel_burned_kg": fuel, "flown": flown, "converged": converged}


def test_per_iteration_minimum_not_running_best(tmp_path):
    run = _write(tmp_path, [_ev(0, 19000), _ev(1, 18000), _ev(1, 20000), _ev(2, 21000), _ev(2, 0, flown=False),
                            {"kind": "iteration", "iteration": 2, "best_fuel_kg": 18000}])
    d = optimizer_series(run, converged_only=False)
    assert d.link.tolist() == [1, 2]                 # iteration 0 (the start) left out; no rows past the data
    assert d.value.tolist() == [18000, 21000]        # iteration 2 rises: not the best so far


def test_converged_only_skips_unconverged_and_keeps_flag(tmp_path):
    run = _write(tmp_path, [_ev(1, 500, converged=False), _ev(1, 19500), _ev(2, 700, converged=False)])
    assert optimizer_series(run, converged_only=False).converged.tolist() == [False, False]
    d = optimizer_series(run, converged_only=True)
    assert d.link.tolist() == [1] and d.value.tolist() == [19500]


def test_stops_at_link_count(tmp_path):
    run = _write(tmp_path, [_ev(k, 19000 - k) for k in range(LINKS + 3)])
    assert optimizer_series(run, converged_only=False).link.max() == LINKS
