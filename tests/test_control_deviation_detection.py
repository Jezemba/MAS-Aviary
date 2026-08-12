"""A control that CHANGED mid-run must not be recorded as held constant.

B20. `_controls_applied` was last-write-wins, so only the final `estimate_mass`
call survived. One run passed `wing_mass_method_used: "oas"` AND `"flops"` while
the canonical baseline pins flops, and the ledger recorded:

    {"applied": {"wing_mass_method": "flops"}, "held_constant": true,
     "deviations": {}}

-- a clean sheet for a run that used both methods.

Not cosmetic: OAS returned 11,419.8 kg against FLOPS' 7,560.6 kg, a 51%
difference in wing mass, and that value feeds Aircraft.Wing.MASS_SCALER straight
into the mission. A run that silently switched structural method is not
comparable with the others, and the experiment's whole premise is that the
pinned controls are actually pinned.
"""

from __future__ import annotations

import pytest

from src.logging.design_ledger import _controls_applied


def _trace(*method_values: str) -> dict:
    """A trace with one estimate_mass call per value, in order."""
    return {
        "structures": {
            "steps": [
                {
                    "tool_calls": [
                        {
                            "name": "estimate_mass",
                            "arguments": {"wing_mass_method": v},
                        }
                    ]
                }
                for v in method_values
            ]
        }
    }


class TestTheObservedFailure:
    def test_oas_then_flops_is_a_deviation(self):
        """The exact sequence that was reported as held_constant."""
        out = _controls_applied(_trace("oas", "flops"))
        assert out["held_constant"] is False
        assert "wing_mass_method" in out["deviations"]

    def test_every_value_is_kept(self):
        out = _controls_applied(_trace("oas", "flops"))
        assert out["observed"]["wing_mass_method"] == ["oas", "flops"]

    def test_a_mid_run_change_is_called_out(self):
        dev = _controls_applied(_trace("oas", "flops"))["deviations"]["wing_mass_method"]
        assert dev["changed_mid_run"] is True
        assert dev["all_values"] == ["oas", "flops"]

    def test_order_does_not_hide_it(self):
        """flops-then-oas was already caught; oas-then-flops was not. Both must be."""
        for seq in (("oas", "flops"), ("flops", "oas")):
            assert _controls_applied(_trace(*seq))["held_constant"] is False


class TestCleanRuns:
    def test_all_flops_is_held_constant(self):
        out = _controls_applied(_trace("flops", "flops", "flops"))
        assert out["held_constant"] is True
        assert out["deviations"] == {}

    def test_single_correct_call(self):
        assert _controls_applied(_trace("flops"))["held_constant"] is True

    def test_no_calls_is_not_a_deviation(self):
        """Absence of evidence is not a deviation -- it is no observation."""
        out = _controls_applied({})
        assert out["held_constant"] is True
        assert out["observed"] == {}

    def test_repeated_identical_values_are_deduped(self):
        out = _controls_applied(_trace("flops", "flops"))
        assert out["observed"]["wing_mass_method"] == ["flops"]


class TestConsistentDeviation:
    def test_all_oas_is_a_deviation_but_not_a_mid_run_change(self):
        """A run that consistently used the wrong method is still not comparable,
        but it is a different problem from one that switched."""
        dev = _controls_applied(_trace("oas", "oas"))["deviations"]["wing_mass_method"]
        assert dev["got"] == "oas"
        assert dev["changed_mid_run"] is False


class TestBackwardsCompatibility:
    def test_applied_keeps_its_last_value_meaning(self):
        """Existing readers of `applied` must be unaffected."""
        assert _controls_applied(_trace("oas", "flops"))["applied"]["wing_mass_method"] == "flops"
