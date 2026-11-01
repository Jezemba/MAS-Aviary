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


def test_table_flags_inert_variables_against_the_noise_floor():
    recs = [{"label": "baseline_1", "fuel_burned_kg": 19000.0, "gtow_kg": 90000.0, "changed": {}},
            {"label": "baseline_2", "fuel_burned_kg": 19002.0, "gtow_kg": 90000.5, "changed": {}},
            {"label": "bypass_ratio", "fuel_burned_kg": 18500.0, "gtow_kg": 89000.0,
             "changed": {"bypass_ratio": [5.105, 6.0]}, "engine": {"mission_engine": {"deck_key": "k"},
                                                                     "propulsion_coupled": True}},
            {"label": "htail_sweep", "fuel_burned_kg": 19001.5, "gtow_kg": 90000.2,
             "changed": {"htail_sweep": [28.0, 31.0]}},
            {"label": "vtail_area", "fuel_burned_kg": None, "gtow_kg": None, "failure": "SU2 solve failed",
             "changed": {"vtail_area": [21.5, 23.65]}}]
    text = oat.table(recs)
    rows = {line.split(" | ")[0].strip("| "): line for line in text.splitlines() if line.startswith("| ")}
    assert "yes / yes" in rows["bypass_ratio"] and "k (coupled)" in rows["bypass_ratio"]
    assert "NO / NO" in rows["htail_sweep"]                     # within the 2 kg / 1 kg baseline spread
    assert "FAILED: SU2 solve failed" in rows["vtail_area"]
    assert "noise floor" in text and "19,001.0" in text
