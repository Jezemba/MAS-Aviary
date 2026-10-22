"""Unit tests for the recommendation parser (analysis/link20/rec_params.py)."""

from analysis.link20 import rec_params as rp


def _one(text):
    items = [i for i in rp.parse(text) if i.get("kind")]
    assert len(items) == 1, items
    return items[0]


def test_value_from_arrow_and_from_to():
    i = _one("Wing1 aspect_ratio 9.36 -> increase to 10.5")
    assert (i["param"], i["kind"], i["lo"], i["direction"]) == ("Aircraft.Wing.ASPECT_RATIO", "value", 10.5, 1)
    i = _one("Reduce wing area from 122.83 m² to 115 m² to lower fuel burn")
    assert (i["param"], i["lo"], i["from"], i["direction"]) == ("Aircraft.Wing.AREA", 115.0, 122.83, -1)


def test_range_bound_and_below():
    i = _one("Reduce Aircraft.Engine.SCALE_FACTOR from 1.87 to 1.5–1.6")
    assert (i["kind"], i["lo"], i["hi"]) == ("range", 1.5, 1.6)
    assert _one("Reduce Aircraft.Engine.SCALE_FACTOR from 1.97 to <=1.5 by adjusting HPC.PR or BPR")["kind"] == "bound"
    i = _one("Further reduce Aircraft.Engine.SCALE_FACTOR below 1.5 by optimizing compressor/turbine PRs or "
             "exploring alternative engine cycle configurations (current SCALE_FACTOR=2.0998 with OPR=16.8)")
    assert (i["kind"], i["lo"]) == ("bound", 1.5)


def test_multi_clause_and_direction_only():
    items = [i for i in rp.parse("Decrease wing area and/or increase wing aspect ratio to reduce fuel burn")]
    assert [(i["param"].split(".")[-1], i["kind"], i["direction"]) for i in items] == [
        ("AREA", "direction", -1), ("ASPECT_RATIO", "direction", 1)]
    items = rp.parse("Aircraft.Wing.TAPER_RATIO 0.246 -> 0.38 AND Aircraft.Wing.ASPECT_RATIO 10.998 -> 13.0")
    assert [i["lo"] for i in items] == [0.38, 13.0]


def test_dropped_clauses():
    assert all(not i.get("kind") for i in rp.parse("Increase optimizer_max_iter beyond 200"))
    assert all(not i.get("kind") for i in rp.parse("Wing CL/CD mismatch (0.616/0.0401 vs SU2 targets) -> reduce drag"))
    assert all(not i.get("kind") for i in rp.parse("Evaluate structural implications of higher aspect ratio"))
    assert all(not i.get("kind") for i in rp.parse("Adjust T4_MAX to improve cycle convergence"))


def test_change_pct_uses_current_value():
    i = _one("Wing1 aspect_ratio 9.36 -> increase to 10.5")
    assert abs(rp.recommended_change_pct(i, 10.0) - 5.0) < 1e-9
    assert abs(rp.recommended_change_pct(i, None) - 100 * (10.5 - 9.36) / 9.36) < 1e-9
    assert rp.recommended_change_pct(_one("Increase wing area to reduce fuel burn"), 120.0) is None
