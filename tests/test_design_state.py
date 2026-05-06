"""Tests for DesignState — centralized multi-MCP design state tracking."""

import pytest

from src.coordination.design_state import ConstraintStatus, DesignState


class TestConstraintStatus:
    def test_evaluate_le_pass(self):
        c = ConstraintStatus(value=100.0, limit=200.0, operator="<=")
        assert c.evaluate() is True
        assert c.satisfied is True

    def test_evaluate_le_fail(self):
        c = ConstraintStatus(value=300.0, limit=200.0, operator="<=")
        assert c.evaluate() is False
        assert c.satisfied is False

    def test_evaluate_ge_pass(self):
        c = ConstraintStatus(value=2500.0, limit=2500.0, operator=">=")
        assert c.evaluate() is True

    def test_evaluate_none_value(self):
        c = ConstraintStatus(value=None, limit=100.0)
        assert c.evaluate() is False
        assert c.satisfied is None

    def test_round_trip(self):
        c = ConstraintStatus(value=42.0, limit=50.0, operator="<=", fidelity="high", source_mcp="aviary")
        d = c.to_dict()
        c2 = ConstraintStatus.from_dict(d)
        assert c2.value == 42.0
        assert c2.limit == 50.0
        assert c2.fidelity == "high"


class TestDesignState:
    def test_default_state(self):
        ds = DesignState()
        assert ds.cpacs_file_path == ""
        assert ds.sessions == {}
        assert ds.results == {}
        assert ds.constraints == {}
        assert ds.iteration == 0
        assert ds.history == []

    def test_session_management(self):
        ds = DesignState()
        ds.set_session("tigl", "uuid-1")
        ds.set_session("su2", "uuid-2")
        ds.set_session("aviary", "uuid-3")
        assert ds.get_session("tigl") == "uuid-1"
        assert ds.get_session("su2") == "uuid-2"
        assert ds.get_session("missing") is None

    def test_legacy_session_id(self):
        """session_id property returns aviary session for backward compat."""
        ds = DesignState()
        ds.set_session("aviary", "aviary-uuid")
        ds.set_session("tigl", "tigl-uuid")
        assert ds.session_id == "aviary-uuid"

    def test_legacy_session_id_setter(self):
        ds = DesignState()
        ds.session_id = "legacy-uuid"
        assert ds.sessions["aviary"] == "legacy-uuid"
        assert ds.session_id == "legacy-uuid"

    def test_legacy_session_id_fallback(self):
        """If no aviary session, returns first available."""
        ds = DesignState()
        ds.set_session("tigl", "first-uuid")
        assert ds.session_id == "first-uuid"

    def test_legacy_session_id_none(self):
        ds = DesignState()
        assert ds.session_id is None

    def test_results(self):
        ds = DesignState()
        ds.set_result("fuel_burned_kg", 7000.0)
        ds.set_result("oem_kg", 46300.0)
        assert ds.get_result("fuel_burned_kg") == 7000.0
        assert ds.get_result("missing") is None

    def test_constraints(self):
        ds = DesignState()
        ds.set_constraint("range_nm", value=2500.0, limit=2500.0, operator=">=", fidelity="medium")
        ds.set_constraint("tofl_m", value=2300.0, limit=2200.0, operator="<=", fidelity="low")

        results = ds.evaluate_constraints()
        assert results["range_nm"] is True
        assert results["tofl_m"] is False
        assert ds.all_constraints_satisfied() is False

    def test_all_constraints_satisfied(self):
        ds = DesignState()
        ds.set_constraint("c1", value=100.0, limit=200.0, operator="<=")
        ds.set_constraint("c2", value=300.0, limit=200.0, operator=">=")
        assert ds.all_constraints_satisfied() is True

    def test_all_constraints_empty(self):
        ds = DesignState()
        assert ds.all_constraints_satisfied() is False

    def test_iteration_tracking(self):
        ds = DesignState()
        ds.set_result("mtom_kg", 85700.0)
        ds.record_iteration()
        assert ds.iteration == 1
        assert ds.history[0]["mtom_kg"] == 85700.0

        ds.set_result("mtom_kg", 85600.0)
        ds.record_iteration()
        assert ds.iteration == 2
        assert len(ds.history) == 2

    def test_mtom_converged(self):
        ds = DesignState()
        assert ds.mtom_converged() is False  # not enough data

        ds.set_result("mtom_kg", 85700.0)
        ds.record_iteration()
        assert ds.mtom_converged() is False  # only 1 iteration

        ds.set_result("mtom_kg", 85600.0)
        ds.record_iteration()
        assert ds.mtom_converged() is True  # < 0.5% change

    def test_mtom_not_converged(self):
        ds = DesignState()
        ds.set_result("mtom_kg", 85700.0)
        ds.record_iteration()
        ds.set_result("mtom_kg", 80000.0)  # ~6.6% change
        ds.record_iteration()
        assert ds.mtom_converged() is False

    def test_serialization_round_trip(self):
        ds = DesignState(cpacs_file_path="/tmp/test.xml")
        ds.set_session("tigl", "uuid-1")
        ds.set_session("aviary", "uuid-2")
        ds.set_result("fuel_burned_kg", 7000.0)
        ds.set_constraint("range_nm", value=2500.0, limit=2500.0, operator=">=", fidelity="medium")
        ds.record_iteration(extra_metric=42.0)

        d = ds.to_dict()
        ds2 = DesignState.from_dict(d)

        assert ds2.cpacs_file_path == "/tmp/test.xml"
        assert ds2.sessions == {"tigl": "uuid-1", "aviary": "uuid-2"}
        assert ds2.results == {"fuel_burned_kg": 7000.0}
        assert ds2.constraints["range_nm"].value == 2500.0
        assert ds2.iteration == 1
        assert ds2.history[0]["extra_metric"] == 42.0

    def test_to_context_string(self):
        ds = DesignState()
        ds.set_session("aviary", "abc-123")
        ds.set_result("fuel_burned_kg", 7000.0)
        ds.set_constraint("range_nm", value=2500.0, limit=2500.0, operator=">=")

        ctx = ds.to_context_string()
        assert "DESIGN STATE:" in ctx
        assert "aviary: abc-123" in ctx
        assert "fuel_burned_kg: 7000.0" in ctx
        assert "SESSION_ID: abc-123" in ctx  # backward compat

    def test_repr(self):
        ds = DesignState()
        ds.set_session("tigl", "uuid-1")
        ds.set_result("oem_kg", 46300.0)
        r = repr(ds)
        assert "sessions=['tigl']" in r
        assert "results=1" in r
