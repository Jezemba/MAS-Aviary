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
