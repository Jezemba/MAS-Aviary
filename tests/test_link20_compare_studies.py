"""compare_studies: which links are kept, and the per-study summary."""
import pandas as pd

from analysis.link20.compare_studies import load, summary


def _links(tmp_path, rows):
    p = tmp_path / "links.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_load_keeps_flown_finished_links_and_reads_convergence_only_there(tmp_path):
    base = {"combination": "c", "structure": "sequential", "handler": "staged_pipeline", "gtow_kg": 95000.0}
    p = _links(tmp_path, [
        {**base, "link": 1, "run_status": "completed", "flew": True, "fuel_kg": 20000.0, "ec_converged": True},
        {**base, "link": 2, "run_status": "completed", "flew": False, "fuel_kg": None, "ec_converged": True},
        {**base, "link": 3, "run_status": "in_progress", "flew": True, "fuel_kg": 19000.0, "ec_converged": True},
        {**base, "link": 4, "run_status": "completed", "flew": True, "fuel_kg": 15000.0, "ec_converged": False}])
    d = load(p, "after")
    assert d.link.tolist() == [1, 4] and d.converged.tolist() == [True, False]


def test_summary_best_converged_saving_against_its_own_baseline(tmp_path):
    base = {"combination": "c", "structure": "sequential", "handler": "staged_pipeline", "gtow_kg": 95000.0,
            "run_status": "completed", "flew": True}
    p = _links(tmp_path, [{**base, "link": 1, "fuel_kg": 18000.0, "ec_converged": True},
                          {**base, "link": 2, "fuel_kg": 12000.0, "ec_converged": False}])
    t = summary(load(p, "after"), {"after": 20000.0}).iloc[0]
    assert t.best_converged_fuel_kg == 18000.0 and t.best_converged_saving_pct == 10.0
    assert (t.flown_links, t.converged_links, t.fuel_last_kg) == (2, 1, 12000.0)
