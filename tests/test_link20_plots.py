"""Unit tests for the 20-link plotting helpers (pure functions; no figures written)."""

import numpy as np
import pandas as pd

from analysis.link20 import plots as pl
from analysis.link20 import recommendations as rec


def test_variable_labels_multi_and_other():
    assert rec.variable_labels("Decrease wing area and/or increase wing aspect ratio") == [
        "Wing area", "Wing aspect ratio"]
    assert rec.variable_labels("Reduce Aircraft.Engine.SCALE_FACTOR from 2.10 to 1.5") == ["Engine scale factor"]
    assert rec.variable_labels("Increase optimizer_max_iter beyond 200") == ["Optimizer settings"]
    assert rec.variable_labels("Run simulation to validate the design parameters") == ["Run or validate only"]
    assert rec.variable_labels("Increase wing fuel volume ratio compliance") == ["Other"]
    assert rec.variable_labels(None) == [rec.NO_RECOMMENDATION]
    # "drag" as a motive is not an aero-coefficient recommendation
    assert rec.variable_labels("Increase wing aspect ratio to reduce drag") == ["Wing aspect ratio"]


def test_stop_category():
    assert pl.stop_category("complete") == "Complete"
    assert pl.stop_category("every TODO is done and nothing was left to act on") == "Complete"
    assert pl.stop_category("budget of 15 role runs at moderate spent") == "Role-run budget spent"
    assert pl.stop_category("30 board cycles dispatched") == "Board-cycle cap reached"
    assert pl.stop_category(np.nan) is None


def test_series_hides_nonflying_values():
    df = pd.DataFrame({"structure": ["sequential"] * 2, "handler": ["staged_pipeline"] * 2, "link": [1, 2],
                       "fuel_kg": [20000.0, np.nan], "flew": [True, False]})
    s = pl.series(df, pl.Metric("f", "fuel_kg", "Fuel (kg)", flight=True))
    assert s.nofly.tolist() == [False, True] and np.isnan(s.value.iloc[1])


def test_param_change_link1_against_baseline():
    df = pd.DataFrame({"structure": "sequential", "handler": "staged_pipeline", "link": [1, 2],
                       "Aircraft.Wing.AREA": [110.0, 121.0]})
    for col, _ in pl.PARAMS[1:]:
        df[col] = np.nan
    t = pl.param_change_table(df, {"Aircraft.Wing.AREA": 100.0}, "sequential", "staged_pipeline")
    row = t.loc["Wing area (m²)"]
    assert np.isclose(row[1], 10.0) and np.isclose(row[2], 10.0) and np.isnan(row[3])


def test_tool_group_mapping():
    srv = {"run_su2_solver": "su2-mcp", "run_simulation": "aviary"}
    assert pl.tool_group("run_su2_solver", srv) == "Aerodynamics (SU2 MCP)"
    assert pl.tool_group("run_simulation", srv) == "Mission (Aviary MCP)"
    assert pl.tool_group("final_answer", srv) == pl.TIME_GROUPS[0]
    assert pl.tool_group("claim_todo", srv) == "Board tools (networked)"
    assert pl.tool_group("assign_task", srv) == "Orchestrator tools"
    assert pl.tool_group("read_design_json", srv) == "Other framework tools"
