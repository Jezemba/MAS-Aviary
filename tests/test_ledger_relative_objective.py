"""B32: absolute fuel rewards runs that did nothing, so the ledger must also say
whether a run touched the aircraft and how it did against its OWN start state.

Measured 2026-08-12 (final4e run 5): `set_high_level_parameters: 0`,
`design_applied: {}`, fuel 12,612.8 kg, 5/5 constraints, `eval: success`. Two of
the three best fuel figures in the dataset came from runs that never modified the
aircraft (6,879 / 6,963) while coupled runs scored 19,348 / 19,712 -- a
fuel-ordered table is topped by the runs that did no design work at all.

Absolute fuel is deliberately left untouched so no existing row is invalidated.
"""
from src.logging.design_ledger import build_record

TOUCHED = {"w": {"steps": [{"tool_calls": [
    {"name": "set_high_level_parameters", "arguments": '{"ASPECT_RATIO": 11.5}'}]}]}}
UNTOUCHED = {"w": {"steps": [{"tool_calls": [
    {"name": "run_simulation", "arguments": "{}"}]}]}}


def _rec(traces, **kw):
    return build_record({"name": "c", "eval_classification": kw.pop("ec", {}), **kw}, traces)


class TestDesignTouched:
    def test_a_run_that_changed_nothing_is_flagged(self):
        r = _rec(UNTOUCHED, ec={"fuel_burned_kg": 12612.8, "result": "success"})
        assert r["objective"]["design_touched"] is False
        assert r["objective"]["scoreable"] is False

    def test_a_run_that_changed_the_design_is_scoreable(self):
        r = _rec(TOUCHED, ec={"fuel_burned_kg": 19348.0})
        assert r["objective"]["design_touched"] is True

    def test_absolute_fuel_is_untouched(self):
        """The point of the fix is an ADDITIONAL view, not a replacement."""
        r = _rec(UNTOUCHED, ec={"fuel_burned_kg": 12612.8})
        assert r["outcomes"]["fuel_burned_kg"] == 12612.8


class TestFuelDeltaVsStart:
    def test_improvement_against_the_handed_over_design_is_positive(self):
        r = _rec(TOUCHED, chain_start_fuel_kg=20000.0, ec={"fuel_burned_kg": 19348.0})
        assert r["objective"]["fuel_delta_vs_start"] == 652.0

    def test_regression_is_negative(self):
        r = _rec(TOUCHED, chain_start_fuel_kg=19000.0, ec={"fuel_burned_kg": 19348.0})
        assert r["objective"]["fuel_delta_vs_start"] == -348.0

    def test_link_zero_is_none_not_zero(self):
        """No predecessor means 'not comparable', which must not read as 'no change'."""
        r = _rec(TOUCHED, ec={"fuel_burned_kg": 19348.0})
        assert r["objective"]["fuel_delta_vs_start"] is None

    def test_failed_run_without_fuel_is_none(self):
        r = _rec(TOUCHED, chain_start_fuel_kg=20000.0, ec={})
        assert r["objective"]["fuel_delta_vs_start"] is None

    def test_junk_start_fuel_does_not_raise(self):
        r = _rec(TOUCHED, chain_start_fuel_kg="n/a", ec={"fuel_burned_kg": 1.0})
        assert r["objective"]["fuel_delta_vs_start"] is None

    def test_start_fuel_is_recorded_on_the_row(self):
        r = _rec(TOUCHED, chain_start_fuel_kg=20000.0, ec={"fuel_burned_kg": 19348.0})
        assert r["chain"]["start_fuel_burned_kg"] == 20000.0


class TestBuildRecordStillWorks:
    def test_no_nameerror_on_a_minimal_record(self):
        """`record.get(...)` inside the returned literal raised NameError for every
        row -- caught only because a real call was made, not by parsing."""
        assert build_record({}, {})["objective"]["design_touched"] is False
