"""B241 drag-scatter ladder: each run moves one geometry variable by a small relative step, all else at baseline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import b241_drag_scatter as ds  # noqa: E402
import oat_sensitivity as oat  # noqa: E402


def test_plan_moves_one_variable_by_each_small_step():
    vs = oat.all_variables(3540.0, 3090.0)
    base = [v.default for v in vs]
    runs = ds.plan(vs)
    assert len(runs) == sum(len(s) for s in ds.LADDERS.values())
    for label, x in runs:
        moved = [i for i, (a, b) in enumerate(zip(x, base)) if a != b]
        name, step = label.split("+")
        assert [vs[i].name for i in moved] == [name]
        assert x[moved[0]] / base[moved[0]] - 1 == __import__("pytest").approx(float(step.rstrip("%")) / 100)
        v = vs[moved[0]]
        assert v.lower <= x[moved[0]] <= v.upper or v.lower == v.upper      # tails: fixed for agents (as oat)


def test_table_reports_cd_change_against_the_baseline():
    base = {"label": "baseline_1", "coupled": {"cl": 0.55, "cd": 0.02}, "fuel_burned_kg": 20000.0,
            "mesh_elements": 100, "converged": True}
    rec = {"label": "wing_area+1%", "coupled": {"cl": 0.55, "cd": 0.018}, "fuel_burned_kg": 19000.0,
           "mesh_elements": 101, "converged": True}
    text = ds.table([rec], base)
    assert "| wing_area+1% |" in text and "-10.00" in text and "-5.00" in text
