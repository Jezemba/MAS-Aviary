"""SU2 ran but produced no force coefficients — a DISTINCT uncoupled mode.

Found in the networked re-measurement (Qwen3-32B, 2026-08-02,
logs/stat_results/qwen32b_remeasure_net). The agent did everything the coupling
contract asks: it ran SU2, called read_history_csv, then called
set_aircraft_parameters four times in a row. The run still came out UNCOUPLED.

The cause was only visible by hand-reading the raw tool response — SU2 had been
configured without force output, so history.csv carried ONLY residual columns:

    Time_Iter, Outer_Iter, Inner_Iter, "rms[Rho]", "rms[RhoU]", "rms[RhoV]", ...

no CL/CD anywhere (typically a missing MARKER_MONITORING). `_capture_aero_
coefficients` found nothing and returned SILENTLY, so downstream this was
indistinguishable from "the agent never ran aero" — the ambiguity that made
.claude/BUGS.md B1 look like a pure ordering problem.
"""

import json

import pytest

from src.coordination.design_state import DesignState
from src.logging.design_ledger import _aero_coupled
from src.tools import coupling
from src.tools.data_plane import get_design_state, init_data_plane, intercept_response

TOOL_SERVER_MAP = {"read_history_csv": "su2", "set_aircraft_parameters": "aviary"}

# Verbatim column set from the failing networked run.
RESIDUAL_ONLY_COLUMNS = [
    "Time_Iter", "Outer_Iter", "Inner_Iter",
    '    "rms[Rho]"    ', '    "rms[RhoU]"   ', '    "rms[RhoV]"   ',
    '    "rms[RhoW]"   ', '    "rms[RhoE]"   ',
]
RESIDUAL_ONLY_ROW = {
    "Time_Iter": 0.0, "Outer_Iter": 0.0, "Inner_Iter": 6.0,
    '    "rms[Rho]"    ': -0.2388999575,
    '    "rms[RhoU]"   ': 2.372836882,
    '    "rms[RhoV]"   ': 2.247809743,
    '    "rms[RhoW]"   ': 2.365612286,
    '    "rms[RhoE]"   ': 5.12163572,
}


@pytest.fixture(autouse=True)
def _clean():
    init_data_plane(DesignState(), TOOL_SERVER_MAP)
    yield
    init_data_plane(DesignState(), TOOL_SERVER_MAP)


def _read_history(payload: dict):
    intercept_response("read_history_csv", json.dumps(payload))
    return get_design_state()


class TestForceOutputMissing:
    def test_residual_only_history_is_flagged_not_silent(self):
        ds = _read_history({"columns": RESIDUAL_ONLY_COLUMNS, "rows": [RESIDUAL_ONLY_ROW]})
        assert ds.data_store["aero_coupling_status"] == "SU2_NO_FORCE_OUTPUT"
        assert "CL/CD" in ds.data_store["aero_capture_failure"]
        assert ds.data_store["aero_capture_columns"][:3] == [
            "Time_Iter", "Outer_Iter", "Inner_Iter",
        ]

    def test_no_aero_vars_are_written(self):
        ds = _read_history({"columns": RESIDUAL_ONLY_COLUMNS, "rows": [RESIDUAL_ONLY_ROW]})
        assert coupling.get_var(ds, "aero.cl_cruise") is None
        assert coupling.get_var(ds, "aero.cd_cruise") is None

    def test_empty_rows_flagged(self):
        ds = _read_history({"columns": RESIDUAL_ONLY_COLUMNS, "rows": []})
        assert ds.data_store["aero_coupling_status"] == "SU2_NO_FORCE_OUTPUT"
        assert "no rows" in ds.data_store["aero_capture_failure"]

    def test_non_dict_rows_flagged(self):
        ds = _read_history({"columns": [], "rows": [[1, 2, 3]]})
        assert ds.data_store["aero_coupling_status"] == "SU2_NO_FORCE_OUTPUT"


class TestHealthyCaptureUnaffected:
    """A history WITH force output must still capture cleanly."""

    def test_quoted_cl_cd_columns_capture(self):
        ds = _read_history({
            "columns": ["Inner_Iter", '    "CL"    ', '    "CD"    '],
            "rows": [{"Inner_Iter": 74.0, '    "CL"    ': 0.09221, '    "CD"    ': 0.0018375}],
        })
        # A healthy capture leaves aero_coupling_status unset here — it is the
        # INJECTOR (_inject_phase_h_aero) that stamps "injected" later.
        assert ds.data_store.get("aero_coupling_status") != "SU2_NO_FORCE_OUTPUT"
        assert coupling.get_var(ds, "aero.cl_cruise") == pytest.approx(0.09221)
        assert coupling.get_var(ds, "aero.cd_cruise") == pytest.approx(0.0018375)

    def test_plain_cl_cd_columns_capture(self):
        ds = _read_history({"columns": ["CL", "CD"], "rows": [{"CL": 0.22, "CD": 0.0155}]})
        assert coupling.get_var(ds, "aero.cl_cruise") == pytest.approx(0.22)


class TestLedgerReportsDistinctMode:
    def test_ledger_separates_no_force_output_from_never_ran(self):
        _read_history({"columns": RESIDUAL_ONLY_COLUMNS, "rows": [RESIDUAL_ONLY_ROW]})
        out = _aero_coupled({})
        assert out["coupled"] is False
        assert out["status"] == "uncoupled_su2_no_force_output"
        assert out["aero_coupling_status"] == "SU2_NO_FORCE_OUTPUT"
        assert out["detection"] == "data_plane"
        assert out["aero_capture_columns"] is not None

    def test_never_ran_still_reports_default_drag(self):
        ds = get_design_state()
        ds.data_store["aero_coupling_status"] = "MISSING_no_su2_aero"
        out = _aero_coupled({})
        assert out["status"] == "uncoupled_default_drag"

    def test_coupled_run_unaffected(self):
        ds = get_design_state()
        ds.data_store["aero_coupling_status"] = "injected"
        out = _aero_coupled({})
        assert out["coupled"] is True
        assert out["status"] == "coupled"
