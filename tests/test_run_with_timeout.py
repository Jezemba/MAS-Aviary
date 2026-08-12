"""A timed-out run must actually be killable.

B16. `_run_with_timeout` documented "uses a subprocess so that on timeout the
entire process (and its GPU memory) can be killed cleanly" while using
`threading.Thread(daemon=True)`. Python cannot kill a thread, so a timed-out run
kept executing and kept its agents, activations and KV cache on the GPU. Only
the WEIGHTS were spared, by the model cache.

Measured consequence: two 75-minute timeouts left their threads alive, and both
`networked_graph_routed` runs then died at model load ("Some modules are
dispatched on the CPU or the disk") with 0 turns and 0 seconds. Two combos
produced no data for an infrastructure reason.

These tests exercise the mechanism without a GPU. Whether a fully-populated
CombinationResult survives the process boundary is verified separately against a
real run -- `messages`/`traces` are the fields at risk, and a serialisation
failure is deliberately reported as an infrastructure error rather than a failed
design run, so it can never be mistaken for a bad result.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def _runner():
    import stat_batch_runner

    return stat_batch_runner


# Module-level so `spawn` can import them in the child.
class _FakeCombo:
    def __init__(self, name="fake"):
        self.name = name


def _ok(combo, task, config, session_id=None):
    return {"status": "success", "combo": combo.name}


def _boom(combo, task, config, session_id=None):
    raise ValueError("discipline exploded")


def _hangs(combo, task, config, session_id=None):
    time.sleep(600)


class TestNormalCompletion:
    def test_result_crosses_the_process_boundary(self, monkeypatch):
        r = _runner()
        monkeypatch.setattr(r, "_subprocess_target", _direct_target_ok)
        out = r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 30)
        assert out["status"] == "success"

    def test_exception_propagates(self, monkeypatch):
        r = _runner()
        monkeypatch.setattr(r, "_subprocess_target", _direct_target_boom)
        with pytest.raises(ValueError, match="discipline exploded"):
            r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 30)


class TestTimeoutActuallyKills:
    def test_timeout_raises(self, monkeypatch):
        r = _runner()
        monkeypatch.setattr(r, "_subprocess_target", _direct_target_hang)
        monkeypatch.setattr(r, "_aggressive_gpu_cleanup", lambda: None)
        start = time.time()
        with pytest.raises(TimeoutError):
            r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 2)
        assert time.time() - start < 60, "must not wait for the hung run"

    def test_no_process_survives_the_timeout(self, monkeypatch):
        """The whole point: the thread version left the run executing."""
        import multiprocessing as mp

        r = _runner()
        monkeypatch.setattr(r, "_subprocess_target", _direct_target_hang)
        monkeypatch.setattr(r, "_aggressive_gpu_cleanup", lambda: None)
        before = {p.pid for p in mp.active_children()}
        with pytest.raises(TimeoutError):
            r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 2)
        after = {p.pid for p in mp.active_children()}
        assert not (after - before), "a child outlived its timeout"


class TestFailuresAreNotMistakenForBadDesigns:
    def test_unsendable_result_is_an_infrastructure_error(self, monkeypatch):
        r = _runner()
        monkeypatch.setattr(r, "_subprocess_target", _direct_target_unsendable)
        with pytest.raises(RuntimeError, match="infrastructure failure"):
            r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 30)

    def test_unsendable_says_the_run_completed(self, monkeypatch):
        """A retry of a run that WORKED would waste 30 minutes and corrupt the
        chain, so the message must be unambiguous."""
        r = _runner()
        monkeypatch.setattr(r, "_subprocess_target", _direct_target_unsendable)
        with pytest.raises(RuntimeError) as exc:
            r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 30)
        assert "COMPLETED" in str(exc.value)

    def test_silent_death_is_reported_as_infrastructure(self, monkeypatch):
        r = _runner()
        monkeypatch.setattr(r, "_subprocess_target", _direct_target_dies)
        with pytest.raises(RuntimeError, match="without returning a result"):
            r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 30)


# --- child entry points (module level: `spawn` re-imports this module) -------
def _direct_target_ok(pipe, combo, task, config, session_id):
    pipe.send(("ok", _ok(combo, task, config)))
    pipe.close()


def _direct_target_boom(pipe, combo, task, config, session_id):
    try:
        _boom(combo, task, config)
    except Exception as exc:
        pipe.send(("error", exc))
    pipe.close()


def _direct_target_hang(pipe, combo, task, config, session_id):
    time.sleep(600)


def _direct_target_unsendable(pipe, combo, task, config, session_id):
    pipe.send(("unsendable", "TypeError: cannot pickle '_thread.lock' object"))
    pipe.close()


def _direct_target_dies(pipe, combo, task, config, session_id):
    pipe.close()
    import os

    os._exit(1)


# --- state must cross the boundary too -------------------------------------
def _target_with_state(pipe, combo, task, config, session_id):
    """Mimics a real run: sets data-plane state, then returns."""
    from src.coordination.design_state import DesignState
    from src.tools.data_plane import export_state_summary, init_data_plane

    ds = DesignState()
    ds.data_store["aero_coupling_status"] = "injected"
    ds.data_store["aero_injected_cl"] = 0.1849
    ds.data_store["mesh_blob"] = "X" * 50000      # bulk must NOT cross
    init_data_plane(ds, {})
    pipe.send(("ok", {"status": "success"}, export_state_summary()))
    pipe.close()


class TestDataPlaneStateSurvivesTheBoundary:
    """B16 moved each run into a subprocess so a timed-out run's GPU memory could
    be reclaimed. That silently severed a second channel: the design ledger's
    authoritative coupling check calls get_design_state() in the PARENT, after
    the run returns, while the state now lives and dies in the CHILD.

    Verified directly at the time -- a child setting aero_coupling_status left
    the parent reading None -- so every run fell back to the cruise_cd heuristic
    the ledger itself flags as unreliable, and an entire sweep's coupling column
    became unusable.

    The original tests asserted only that a RESULT came back. That was the gap:
    returning the result was never sufficient.
    """

    def _run(self):
        from src.coordination.design_state import DesignState
        from src.tools.data_plane import init_data_plane

        r = _runner()
        init_data_plane(DesignState(), {})     # parent starts clean, as the runner does
        import stat_batch_runner as sbr

        orig = sbr._subprocess_target
        sbr._subprocess_target = _target_with_state
        try:
            return r._run_with_timeout(_FakeCombo(), "task", {}, "domain", 60)
        finally:
            sbr._subprocess_target = orig

    def test_result_still_returns(self):
        assert self._run()["status"] == "success"

    def test_parent_sees_the_child_coupling_status(self):
        """The exact read the ledger performs."""
        from src.tools.data_plane import get_design_state

        self._run()
        store = getattr(get_design_state(), "data_store", {}) or {}
        assert store.get("aero_coupling_status") == "injected"

    def test_injected_values_cross(self):
        from src.tools.data_plane import get_design_state

        self._run()
        store = getattr(get_design_state(), "data_store", {}) or {}
        assert store.get("aero_injected_cl") == 0.1849

    def test_bulk_payloads_do_not_cross(self):
        """A DesignState can hold tens of MB of mesh; only bounded state moves."""
        from src.tools.data_plane import get_design_state

        self._run()
        store = getattr(get_design_state(), "data_store", {}) or {}
        assert "mesh_blob" not in store
