"""B3 one-at-a-time sensitivity plan: every settable variable moved alone, inside its range."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import oat_sensitivity as oat  # noqa: E402


def test_plan_moves_each_variable_alone_once():
    vs = oat.all_variables(3100.0, 3000.0)
    runs = oat.plan(vs)
    base = [v.default for v in vs]
    assert [label for label, _ in runs[:2]] == ["baseline_1", "baseline_2"] and runs[0][1] == base
    steps = runs[2:]
    assert [label for label, _ in steps] == [v.name for v in vs]
    for (label, x), v in zip(steps, vs):
        moved = [i for i, (a, b) in enumerate(zip(x, base)) if a != b]
        assert moved == [vs.index(v)], label
        assert v.lower <= x[moved[0]] <= v.upper or v.lower == v.upper, label


def test_every_settable_variable_is_covered():
    names = {v.name for v in oat.all_variables(3100.0, 3000.0)}
    assert len(names) == 23 and set(oat.STEP) == names
    assert {"t4_takeoff", "t4_climb", "htail_area", "vtail_sweep", "hpt_efficiency", "bypass_ratio"} <= names


def test_ratings_use_the_given_defaults():
    to, clb = oat.ratings(3150.0, 3020.0)
    assert (to.target, to.default, clb.target, clb.default) == ("T4_TAKEOFF", 3150.0, "T4_CLIMB", 3020.0)
