"""B71: the cross-link quick metrics must report constraints truthfully.

Before the fix, ``constraint <=15000: PASS/FAIL`` was read from ``fuel_pass`` --
the classifier's two-sided +/-10% band around the F25 reference -- so a design
that satisfied the constraint and beat the reference was told it FAILED.
"""

import json
from pathlib import Path

import pytest

from scripts.stat_batch_runner import (
    _DEFAULT_MDO_F25_TASK,
    MDO_F25_CONSTRAINTS,
    build_quick_metrics,
)
from src.logging.eval_classifier import classify_aviary_eval

# eval_classification from the 2026-08-28 networked sweep, as persisted.
LINK1_ITERATIVE = {  # satisfied both constraints, 14.7% under reference -> was told FAIL
    "result": "omission", "fuel_burned_kg": 10763.227272649068, "gtow_kg": 74763.73087035416,
    "wing_mass_kg": 8539.527327925654, "fuel_pass": False, "gtow_pass": True, "wing_mass_pass": True,
}
LINK1_GRAPH = {  # genuinely violated both constraints
    "result": "omission", "fuel_burned_kg": 23666.3291590744, "gtow_kg": 90005.02745787549,
    "wing_mass_kg": 9396.561137714056, "fuel_pass": False, "gtow_pass": False, "wing_mass_pass": False,
}


def _line(text, name):
    return next(l for l in text.splitlines() if l.strip().startswith(name))


def test_constraints_match_the_task_text():
    for name, limit in MDO_F25_CONSTRAINTS.items():
        assert f"{name} <= {limit:g}" in _DEFAULT_MDO_F25_TASK


def test_b71_satisfied_constraint_outside_band_reports_pass():
    out = build_quick_metrics(LINK1_ITERATIVE)
    fuel = _line(out, "fuel_burned_kg")
    assert "constraint <= 15000: PASS" in fuel
    assert "FAIL" not in fuel
    assert "14.7% below the F25 reference 12612.8" in fuel


def test_real_violations_still_report_fail():
    out = build_quick_metrics(LINK1_GRAPH)
    assert "constraint <= 15000: FAIL" in _line(out, "fuel_burned_kg")
    # 90005.0 > 90000 by 5 kg -- must not round its way to PASS
    assert "constraint <= 90000: FAIL" in _line(out, "gtow_kg")


def test_boundary_value_is_inclusive():
    out = build_quick_metrics({"fuel_burned_kg": 15000.0, "gtow_kg": 90000.0, "wing_mass_kg": 8000.0})
    assert "constraint <= 15000: PASS" in out
    assert "constraint <= 90000: PASS" in out


def test_unconstrained_metric_carries_no_verdict():
    wing = _line(build_quick_metrics(LINK1_ITERATIVE), "wing_mass_kg")
    assert "PASS" not in wing and "FAIL" not in wing
    assert "no constraint" in wing


def test_band_verdict_is_never_shown():
    # The band is a detection net, not a target -- no in/out verdict reaches the agent.
    out = build_quick_metrics(LINK1_ITERATIVE).lower()
    assert "band" not in out and "outside" not in out and "threshold" not in out


@pytest.mark.parametrize("missing", [None, 0.0, "not-a-number"])
def test_unextracted_metric_is_unknown_not_pass(missing):
    out = build_quick_metrics({"fuel_burned_kg": missing, "gtow_kg": 74000.0, "wing_mass_kg": 8000.0})
    fuel = _line(out, "fuel_burned_kg")
    assert "not extracted" in fuel and "UNKNOWN" in fuel
    assert "PASS" not in fuel


def test_empty_classification_gives_empty_string():
    assert build_quick_metrics(None) == ""
    assert build_quick_metrics({}) == ""


def test_live_classifier_output_round_trips():
    # Feed the builder what the classifier really produces, including the 0.0
    # sentinels it writes for metrics it could not find (B72).
    ec = classify_aviary_eval({"fuel_burned_kg": 10763.2, "gtow_kg": 74763.7, "wing_mass_kg": 8539.5})
    out = build_quick_metrics(ec.__dict__)
    assert ec.fuel_pass is False  # band still fails -- classifier untouched
    assert "constraint <= 15000: PASS" in out


RESULTS = Path(__file__).resolve().parents[1] / "logs" / "stat_results" / "1787889840"


@pytest.mark.skipif(not RESULTS.exists(), reason="2026-08-28 sweep results not on this machine")
def test_real_sweep_results_never_contradict_their_own_values():
    files = sorted(RESULTS.glob("repeat_*/*/result.json"))
    assert files
    for f in files:
        ec = json.loads(f.read_text()).get("eval_classification") or {}
        out = build_quick_metrics(ec)
        for name, limit in MDO_F25_CONSTRAINTS.items():
            v = float(ec.get(name) or 0.0)
            if v:
                want = "PASS" if v <= limit else "FAIL"
                assert f"constraint <= {limit:g}: {want}" in _line(out, name), f.parent.name
