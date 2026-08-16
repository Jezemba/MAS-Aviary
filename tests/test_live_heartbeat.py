"""Progress must reach W&B WHILE a run is in flight, not only when it ends.

Every wandb.log in stat_batch_runner sits at a run boundary, so a 2-3 hour run
left the dashboard empty until it finished -- and a run killed by the wall clock
(orchestrated_graph_routed did this three times) logged nothing at all, which is
exactly the case most worth seeing.

The run executes in a spawned subprocess, so the parent cannot read its counters
directly. It reads the sweep log both processes write to, passed in as
SWEEP_LOG_PATH by scripts/launch_sweep.sh.
"""

import importlib
import sys
import types

import pytest


@pytest.fixture
def runner(monkeypatch):
    sys.modules.pop("scripts.stat_batch_runner", None)
    mod = importlib.import_module("scripts.stat_batch_runner")
    return mod


class TestItDoesNotStartWithoutWhatItNeeds:
    def test_no_wandb_run_means_no_thread(self, runner, monkeypatch, tmp_path):
        monkeypatch.setenv("SWEEP_LOG_PATH", str(tmp_path / "x.log"))
        stop = runner._start_live_heartbeat(None)
        assert stop is not None and not stop.is_set()

    def test_no_log_path_means_no_thread(self, runner, monkeypatch):
        monkeypatch.delenv("SWEEP_LOG_PATH", raising=False)
        assert runner._start_live_heartbeat(object()) is not None


class TestItCountsWhatMatters:
    """Runs the REAL metric function against log text.

    The first version of these tests only mirrored the counting logic and checked
    that starting the thread did not raise -- so the thread body was never
    executed, and `re.findall` without a module-level `import re` reached a live
    run and died there with "NameError: name 're' is not defined". The metric
    computation is now a separate function precisely so a test can call it.
    """

    PATTERNS = {
        "live/mission_solves": "Calling tool: 'run_simulation'",
        "live/su2_solves": "Calling tool: 'run_su2_solver'",
        "live/design_state_lookups": "Calling tool: 'get_design_state'",
    }

    def _metrics(self, runner):
        return runner._live_metrics(self.LOG, self.PATTERNS)

    def test_the_real_function_runs_without_a_missing_import(self, runner):
        """The regression: this exact call raised NameError in production."""
        assert self._metrics(runner)["live/mission_solves"] == 3

    def test_budget_is_parsed_by_the_real_function(self, runner):
        d = self._metrics(runner)
        assert d["live/passes_remaining"] == 5 and d["live/passes_max"] == 25

    def test_failure_counters_from_the_real_function(self, runner):
        d = self._metrics(runner)
        assert d["live/su2_config_rejections"] == 4
        assert d["live/session_errors"] == 2

    def test_lookup_tool_counter_from_the_real_function(self, runner):
        assert self._metrics(runner)["live/design_state_lookups"] == 1

    def test_empty_log_does_not_raise(self, runner):
        d = runner._live_metrics("", self.PATTERNS)
        assert d["live/mission_solves"] == 0
        assert "live/passes_remaining" not in d

    LOG = (
        "Calling tool: 'run_simulation'\n" * 3
        + "Calling tool: 'run_su2_solver'\n" * 2
        + "Calling tool: 'get_design_state'\n"
        + "Line 40 AIRCRAFT.WING.TAPER_RATIO: invalid option name\n" * 4
        + "'Unknown session_id: abc'\n" * 2
        + "BUDGET: 7 of 25 passes remaining\n"
        + "BUDGET: 5 of 25 passes remaining\n"
    )

    def test_the_expensive_units_are_counted(self):
        assert self.LOG.count("Calling tool: 'run_simulation'") == 3
        assert self.LOG.count("Calling tool: 'run_su2_solver'") == 2

    def test_the_new_lookup_tool_is_counted(self):
        """B55: whether the agent actually CALLS get_design_state is the open
        question the next run answers, so it must be visible live."""
        assert self.LOG.count("Calling tool: 'get_design_state'") == 1

    def test_failure_signals_are_counted(self):
        assert self.LOG.count("invalid option name") == 4      # B54 class
        assert self.LOG.count("Unknown session_id") == 2       # B53 class

    def test_the_latest_budget_line_wins(self):
        import re
        m = re.findall(r"BUDGET: (\d+) of (\d+) passes remaining", self.LOG)
        assert (int(m[-1][0]), int(m[-1][1])) == (5, 25)


class TestItCannotTakeDownARun:
    def test_a_missing_log_file_is_survivable(self, runner, monkeypatch, tmp_path):
        """Telemetry must never kill a multi-hour run."""
        monkeypatch.setenv("SWEEP_LOG_PATH", str(tmp_path / "does-not-exist.log"))
        stop = runner._start_live_heartbeat(object(), interval_s=1)
        stop.set()   # no exception on start or stop
