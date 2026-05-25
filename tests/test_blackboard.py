"""Tests for the shared blackboard data structure.

No GPU needed. Tests all claiming modes, context filtering, and
CRUD operations.
"""

import pytest

from src.coordination.blackboard import (
    Blackboard,
    _strip_metrics,
    _strip_reasoning,
    _truncate_result,
)

# ---- Fixtures ----------------------------------------------------------------


@pytest.fixture
def bb_soft():
    """Blackboard with soft claiming."""
    return Blackboard(claiming_mode="soft")


@pytest.fixture
def bb_hard():
    """Blackboard with hard claiming."""
    return Blackboard(claiming_mode="hard")


@pytest.fixture
def bb_none():
    """Blackboard with no claiming."""
    return Blackboard(claiming_mode="none")


# ---- Write and Read ----------------------------------------------------------


class TestWriteAndRead:
    def test_write_new_entry(self, bb_soft):
        entry, warning = bb_soft.write("task_1", "Working on it", "agent_1", "status")
        assert entry is not None
        assert entry.key == "task_1"
        assert entry.value == "Working on it"
        assert entry.author == "agent_1"
        assert entry.entry_type == "status"
        assert entry.version == 1
        assert warning is None

    def test_read_all(self, bb_soft):
        bb_soft.write("a", "val_a", "agent_1", "status")
        bb_soft.write("b", "val_b", "agent_2", "result")
        entries = bb_soft.read_all()
        assert len(entries) == 2
        keys = {e.key for e in entries}
        assert keys == {"a", "b"}

    def test_read_by_type(self, bb_soft):
        bb_soft.write("s1", "status val", "agent_1", "status")
        bb_soft.write("r1", "result val", "agent_2", "result")
        bb_soft.write("s2", "status val 2", "agent_3", "status")
        statuses = bb_soft.read_by_type("status")
        assert len(statuses) == 2
        assert all(e.entry_type == "status" for e in statuses)

    def test_read_by_author(self, bb_soft):
        bb_soft.write("a", "v1", "agent_1", "status")
        bb_soft.write("b", "v2", "agent_2", "result")
        bb_soft.write("c", "v3", "agent_1", "result")
        entries = bb_soft.read_by_author("agent_1")
        assert len(entries) == 2
        assert all(e.author == "agent_1" for e in entries)

    def test_get_existing_key(self, bb_soft):
        bb_soft.write("task", "hello", "agent_1", "status")
        entry = bb_soft.get("task")
        assert entry is not None
        assert entry.value == "hello"

    def test_get_missing_key(self, bb_soft):
        assert bb_soft.get("nonexistent") is None

    def test_invalid_entry_type_raises(self, bb_soft):
        with pytest.raises(ValueError, match="Invalid entry_type"):
            bb_soft.write("k", "v", "a", "invalid_type")


# ---- Update ------------------------------------------------------------------


class TestUpdate:
    def test_update_by_same_author(self, bb_soft):
        bb_soft.write("task", "v1", "agent_1", "status")
        updated = bb_soft.update("task", "v2", "agent_1")
        assert updated is not None
        assert updated.value == "v2"
        assert updated.version == 2

    def test_update_increments_version(self, bb_soft):
        bb_soft.write("task", "v1", "agent_1", "status")
        bb_soft.update("task", "v2", "agent_1")
        bb_soft.update("task", "v3", "agent_1")
        entry = bb_soft.get("task")
        assert entry.version == 3

    def test_update_rejected_for_different_author(self, bb_soft):
        bb_soft.write("task", "v1", "agent_1", "status")
        result = bb_soft.update("task", "v2", "agent_2")
        assert result is None
        # Original unchanged.
        assert bb_soft.get("task").value == "v1"

    def test_update_nonexistent_key(self, bb_soft):
        result = bb_soft.update("ghost", "val", "agent_1")
        assert result is None


# ---- Delete ------------------------------------------------------------------


class TestDelete:
    def test_delete_by_author(self, bb_soft):
        bb_soft.write("task", "val", "agent_1", "status")
        assert bb_soft.delete("task", "agent_1") is True
        assert bb_soft.get("task") is None

    def test_delete_rejected_for_non_author(self, bb_soft):
        bb_soft.write("task", "val", "agent_1", "status")
        assert bb_soft.delete("task", "agent_2") is False
        assert bb_soft.get("task") is not None

    def test_delete_nonexistent_key(self, bb_soft):
        assert bb_soft.delete("ghost", "agent_1") is False


# ---- Soft Claiming -----------------------------------------------------------


class TestSoftClaiming:
    def test_first_claim_succeeds(self, bb_soft):
        entry, warning = bb_soft.write("subtask_1", "claimed", "agent_1", "claim")
        assert entry is not None
        assert warning is None

    def test_duplicate_claim_returns_warning(self, bb_soft):
        bb_soft.write("subtask_1", "claimed", "agent_1", "claim")
        entry, warning = bb_soft.write("subtask_1", "also claimed", "agent_2", "claim")
        assert entry is not None  # write succeeds
        assert warning is not None
        assert "already claimed by agent_1" in warning

    def test_same_author_reclaim_no_warning(self, bb_soft):
        bb_soft.write("subtask_1", "v1", "agent_1", "claim")
        entry, warning = bb_soft.write("subtask_1", "v2", "agent_1", "claim")
        assert entry is not None
        assert warning is None
        assert entry.version == 2

    def test_is_claimed(self, bb_soft):
        bb_soft.write("subtask_1", "claimed", "agent_1", "claim")
        assert bb_soft.is_claimed("subtask_1") is True

    def test_is_not_claimed(self, bb_soft):
        bb_soft.write("task_1", "status", "agent_1", "status")
        assert bb_soft.is_claimed("task_1") is False

    def test_get_claims(self, bb_soft):
        bb_soft.write("c1", "claimed", "agent_1", "claim")
        bb_soft.write("s1", "working", "agent_2", "status")
        bb_soft.write("c2", "claimed", "agent_3", "claim")
        claims = bb_soft.get_claims()
        assert len(claims) == 2
        assert all(c.entry_type == "claim" for c in claims)

    def test_claim_conflict_counter(self, bb_soft):
        bb_soft.write("subtask_1", "claimed", "agent_1", "claim")
        bb_soft.write("subtask_1", "also claimed", "agent_2", "claim")
        assert bb_soft.claim_conflicts == 1


# ---- Hard Claiming -----------------------------------------------------------


class TestHardClaiming:
    def test_first_claim_succeeds(self, bb_hard):
        entry, warning = bb_hard.write("subtask_1", "claimed", "agent_1", "claim")
        assert entry is not None
        assert warning is None

    def test_duplicate_claim_rejected(self, bb_hard):
        bb_hard.write("subtask_1", "claimed", "agent_1", "claim")
        entry, warning = bb_hard.write("subtask_1", "also claimed", "agent_2", "claim")
        assert entry is None
        assert "locked by agent_1" in warning

    def test_same_author_reclaim_allowed(self, bb_hard):
        bb_hard.write("subtask_1", "v1", "agent_1", "claim")
        entry, warning = bb_hard.write("subtask_1", "v2", "agent_1", "claim")
        assert entry is not None
        assert entry.version == 2

    def test_claim_conflict_counter(self, bb_hard):
        bb_hard.write("subtask_1", "claimed", "agent_1", "claim")
        bb_hard.write("subtask_1", "also", "agent_2", "claim")
        assert bb_hard.claim_conflicts == 1


# ---- No Claiming Mode --------------------------------------------------------


class TestNoClaiming:
    def test_claim_type_no_enforcement(self, bb_none):
        bb_none.write("subtask_1", "claimed", "agent_1", "claim")
        entry, warning = bb_none.write("subtask_1", "also claimed", "agent_2", "claim")
        # No warning, no rejection — different author gets modified key.
        assert entry is not None
        assert warning is None

    def test_no_claim_conflict_counted(self, bb_none):
        bb_none.write("subtask_1", "claimed", "agent_1", "claim")
        bb_none.write("subtask_1", "also", "agent_2", "claim")
        assert bb_none.claim_conflicts == 0


# ---- Concurrent Writes (same key, different authors) -------------------------


class TestConcurrentWrites:
    def test_different_author_gets_modified_key(self, bb_soft):
        bb_soft.write("result_1", "output A", "agent_1", "result")
        entry, _ = bb_soft.write("result_1", "output B", "agent_2", "result")
        assert entry.key == "result_1_agent_2"
        # Both entries exist.
        assert bb_soft.get("result_1") is not None
        assert bb_soft.get("result_1_agent_2") is not None

    def test_same_author_updates_in_place(self, bb_soft):
        bb_soft.write("result_1", "v1", "agent_1", "result")
        entry, _ = bb_soft.write("result_1", "v2", "agent_1", "result")
        assert entry.key == "result_1"
        assert entry.value == "v2"
        assert entry.version == 2


# ---- Context String Rendering ------------------------------------------------


class TestToContextString:
    def _default_config(self, **overrides):
        config = {
            "peer_monitoring_visible": True,
            "trans_specialist_knowledge": True,
            "predictive_knowledge": False,
        }
        config.update(overrides)
        return config

    def test_empty_blackboard(self, bb_soft):
        result = bb_soft.to_context_string("agent_1", self._default_config())
        assert result == "Blackboard is empty."

    def test_basic_rendering(self, bb_soft):
        bb_soft.write("task_1", "Working on geometry", "agent_1", "status")
        result = bb_soft.to_context_string("agent_1", self._default_config())
        assert "[STATUS]" in result
        assert "task_1" in result
        assert "Working on geometry" in result
        assert "agent_1" in result

    def test_predictions_hidden_when_disabled(self, bb_soft):
        bb_soft.write("pred_1", "agent_2 will work on X", "agent_1", "prediction")
        bb_soft.write("status_1", "active", "agent_1", "status")
        config = self._default_config(predictive_knowledge=False)
        result = bb_soft.to_context_string("agent_1", config)
        assert "pred_1" not in result
        assert "status_1" in result

    def test_predictions_visible_when_enabled(self, bb_soft):
        bb_soft.write("pred_1", "agent_2 will work on X", "agent_1", "prediction")
        config = self._default_config(predictive_knowledge=True)
        result = bb_soft.to_context_string("agent_1", config)
        assert "pred_1" in result
        assert "PREDICTION" in result

    def test_trans_specialist_hidden_truncates_results(self, bb_soft):
        long_result = "Output: some code here\nReasoning: I chose this approach because..."
        bb_soft.write("r1", long_result, "agent_1", "result")
        config = self._default_config(trans_specialist_knowledge=False)
        result = bb_soft.to_context_string("agent_1", config)
        assert "I chose this approach because" not in result

    def test_trans_specialist_shared_shows_full_results(self, bb_soft):
        long_result = "Output: some code here\nReasoning: I chose this approach because..."
        bb_soft.write("r1", long_result, "agent_1", "result")
        config = self._default_config(trans_specialist_knowledge=True)
        result = bb_soft.to_context_string("agent_1", config)
        assert "I chose this approach because" in result

    def test_peer_monitoring_hidden_strips_metrics(self, bb_soft):
        value = "Working well\nerror_rate: 0.1\nretry_count: 2\nDone."
        bb_soft.write("s1", value, "agent_1", "status")
        config = self._default_config(peer_monitoring_visible=False)
        result = bb_soft.to_context_string("agent_1", config)
        assert "error_rate" not in result
        assert "retry_count" not in result
        assert "Working well" in result

    def test_peer_monitoring_visible_keeps_metrics(self, bb_soft):
        value = "Working well\nerror_rate: 0.1\nretry_count: 2\nDone."
        bb_soft.write("s1", value, "agent_1", "status")
        config = self._default_config(peer_monitoring_visible=True)
        result = bb_soft.to_context_string("agent_1", config)
        assert "error_rate" in result
        assert "retry_count" in result

    def test_claims_always_visible(self, bb_soft):
        """Claims are visible regardless of any toggle."""
        bb_soft.write("claim_1", "I'm working on X", "agent_1", "claim")
        config = self._default_config(
            peer_monitoring_visible=False,
            trans_specialist_knowledge=False,
            predictive_knowledge=False,
        )
        result = bb_soft.to_context_string("agent_1", config)
        assert "claim_1" in result
        assert "CLAIM" in result

    def test_gaps_always_visible(self, bb_soft):
        """Gaps are visible regardless of any toggle."""
        bb_soft.write("gap_1", "Nobody handling fillets", "agent_1", "gap")
        config = self._default_config(
            peer_monitoring_visible=False,
            trans_specialist_knowledge=False,
            predictive_knowledge=False,
        )
        result = bb_soft.to_context_string("agent_1", config)
        assert "gap_1" in result
        assert "GAP" in result


# ---- Helper function tests ---------------------------------------------------


class TestHelpers:
    def test_truncate_short_result(self):
        assert _truncate_result("short text") == "short text"

    def test_truncate_long_result(self):
        long_text = "a" * 500
        result = _truncate_result(long_text, max_chars=400)
        assert result.endswith("...")
        assert len(result) == 403  # 400 + "..."

    def test_strip_reasoning_removes_block(self):
        text = "Output: hello\nReasoning: because I decided to\n\nMore output"
        result = _strip_reasoning(text)
        assert "Output: hello" in result
        assert "because I decided to" not in result
        assert "More output" in result

    def test_strip_metrics_removes_metric_lines(self):
        text = "Status: active\nerror_rate: 0.2\nretry_count: 3\nDoing work"
        result = _strip_metrics(text)
        assert "Status: active" in result
        assert "error_rate" not in result
        assert "retry_count" not in result
        assert "Doing work" in result


# ---- Constructor validation --------------------------------------------------


class TestConstructor:
    def test_invalid_claiming_mode(self):
        with pytest.raises(ValueError, match="Invalid claiming_mode"):
            Blackboard(claiming_mode="invalid")

    def test_valid_modes(self):
        for mode in ("none", "soft", "hard"):
            bb = Blackboard(claiming_mode=mode)
            assert bb.claiming_mode == mode


# ---- Len and write count -----------------------------------------------------


class TestCounters:
    def test_len(self, bb_soft):
        assert len(bb_soft) == 0
        bb_soft.write("a", "v", "agent_1", "status")
        assert len(bb_soft) == 1
        bb_soft.write("b", "v", "agent_2", "result")
        assert len(bb_soft) == 2

    def test_write_count(self, bb_soft):
        bb_soft.write("a", "v1", "agent_1", "status")
        bb_soft.write("a", "v2", "agent_1", "status")  # update
        assert bb_soft.write_count == 2


# -- TODO board + concurrent claim semantics ----------------------------------
#
# Regression for the concurrent-blackboard selection mode added 2026-05-25.
# The TODO list is a separate data structure from entries; peers atomically
# claim a TODO via claim_todo() (check-and-set under the blackboard lock)
# and complete it via complete_todo(). The at-most-one-winner property is
# what enables truly parallel peer execution without duplicating work.


class TestTodoBoard:
    """Tests for the new TODO list API used by concurrent-blackboard mode."""

    def test_seed_todos_idempotent(self, bb_soft):
        n1 = bb_soft.seed_todos([("geometry", "open cpacs"), ("aero", "su2")])
        n2 = bb_soft.seed_todos([("geometry", "different desc")])
        assert n1 == 2
        # geometry already present; not re-seeded (existing entry preserved).
        assert n2 == 0
        todos = bb_soft.read_todos()
        assert {t.name for t in todos} == {"geometry", "aero"}
        # Original description preserved (idempotent on duplicates).
        geo = next(t for t in todos if t.name == "geometry")
        assert geo.description == "open cpacs"

    def test_read_todos_returns_defensive_copy(self, bb_soft):
        bb_soft.seed_todos([("a", "x")])
        snap1 = bb_soft.read_todos()
        # Mutating the snapshot must not affect the board.
        snap1[0].status = "claimed"
        snap2 = bb_soft.read_todos()
        assert snap2[0].status == "pending"

    def test_claim_unclaimed_succeeds(self, bb_soft):
        bb_soft.seed_todos([("geometry", "x")])
        ok, msg = bb_soft.claim_todo("geometry", "agent_1")
        assert ok
        assert "successfully claimed" in msg
        t = bb_soft.read_todos()[0]
        assert t.status == "claimed"
        assert t.assigned_to == "agent_1"

    def test_claim_already_claimed_rejected(self, bb_soft):
        bb_soft.seed_todos([("aero", "x")])
        bb_soft.claim_todo("aero", "agent_1")
        ok, msg = bb_soft.claim_todo("aero", "agent_2")
        assert not ok
        assert "claimed by 'agent_1'" in msg
        # The original claim is preserved.
        t = bb_soft.read_todos()[0]
        assert t.assigned_to == "agent_1"

    def test_claim_idempotent_for_same_agent(self, bb_soft):
        bb_soft.seed_todos([("mass", "x")])
        ok1, _ = bb_soft.claim_todo("mass", "agent_1")
        ok2, _ = bb_soft.claim_todo("mass", "agent_1")
        assert ok1 and ok2

    def test_claim_nonexistent_returns_error(self, bb_soft):
        ok, msg = bb_soft.claim_todo("doesntexist", "agent_1")
        assert not ok
        assert "no such TODO" in msg

    def test_claim_done_todo_rejected(self, bb_soft):
        bb_soft.seed_todos([("mission", "x")])
        bb_soft.claim_todo("mission", "agent_1")
        bb_soft.complete_todo("mission", "agent_1", "done")
        ok, msg = bb_soft.claim_todo("mission", "agent_2")
        assert not ok
        assert "already done" in msg

    def test_complete_by_non_claimant_rejected(self, bb_soft):
        bb_soft.seed_todos([("eval", "x")])
        bb_soft.claim_todo("eval", "agent_1")
        ok, msg = bb_soft.complete_todo("eval", "agent_2", "result")
        assert not ok
        assert "claimed by 'agent_1'" in msg
        # Status unchanged.
        t = bb_soft.read_todos()[0]
        assert t.status == "claimed"

    def test_fail_releases_claim_for_retry(self, bb_soft):
        bb_soft.seed_todos([("propulsion", "x")])
        bb_soft.claim_todo("propulsion", "agent_1")
        ok, _ = bb_soft.fail_todo("propulsion", "agent_1", "ran out of time")
        assert ok
        t = bb_soft.read_todos()[0]
        assert t.status == "failed"
        assert t.assigned_to is None  # claim released
        # Another agent can now claim it.
        ok2, _ = bb_soft.claim_todo("propulsion", "agent_2")
        assert ok2

    def test_all_todos_done_false_when_pending(self, bb_soft):
        bb_soft.seed_todos([("a", "x"), ("b", "y")])
        bb_soft.claim_todo("a", "agent_1")
        bb_soft.complete_todo("a", "agent_1", "result a")
        # 'b' still pending.
        assert not bb_soft.all_todos_done()

    def test_all_todos_done_true_when_all_complete(self, bb_soft):
        bb_soft.seed_todos([("a", "x"), ("b", "y")])
        bb_soft.claim_todo("a", "agent_1")
        bb_soft.complete_todo("a", "agent_1", "ra")
        bb_soft.claim_todo("b", "agent_2")
        bb_soft.complete_todo("b", "agent_2", "rb")
        assert bb_soft.all_todos_done()

    def test_all_todos_done_false_when_empty(self, bb_soft):
        # Vacuously-false: an empty TODO board hasn't completed anything.
        assert not bb_soft.all_todos_done()

    def test_read_pending_filters_correctly(self, bb_soft):
        bb_soft.seed_todos([("a", ""), ("b", ""), ("c", "")])
        bb_soft.claim_todo("a", "agent_1")
        bb_soft.complete_todo("a", "agent_1", "ra")
        bb_soft.claim_todo("b", "agent_2")
        pending = bb_soft.read_pending_todos()
        assert {t.name for t in pending} == {"c"}


class TestConcurrentClaimSafety:
    """Stress test the at-most-one-winner safety property under thread
    contention. Spawn N threads that all try to claim the same TODO; only
    ONE may succeed (this is the CodeCRDT formal property)."""

    def test_only_one_winner_under_contention(self, bb_soft):
        import threading

        bb_soft.seed_todos([("contested", "only one peer can win")])
        results: list[bool] = []
        lock = threading.Lock()
        barrier = threading.Barrier(8)

        def try_claim(agent_name: str) -> None:
            barrier.wait()  # release all threads simultaneously
            ok, _ = bb_soft.claim_todo("contested", agent_name)
            with lock:
                results.append(ok)

        threads = [
            threading.Thread(target=try_claim, args=(f"agent_{i}",))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one of the 8 racers must have won.
        wins = sum(1 for r in results if r)
        assert wins == 1, f"expected exactly 1 winner under contention, got {wins}"

    def test_many_distinct_todos_no_double_assign(self, bb_soft):
        """Multiple peers across multiple TODOs — no TODO ever ends up
        with two distinct assigned_to values, and no peer claims a TODO
        another already owns."""
        import threading

        n_todos = 20
        bb_soft.seed_todos([(f"todo_{i}", "") for i in range(n_todos)])
        peers = [f"agent_{i}" for i in range(4)]
        round_results: list[tuple[str, str, bool]] = []
        lock = threading.Lock()

        def peer_loop(peer_name: str) -> None:
            # Each peer tries to claim every TODO; only one should win each.
            for i in range(n_todos):
                ok, _ = bb_soft.claim_todo(f"todo_{i}", peer_name)
                with lock:
                    round_results.append((peer_name, f"todo_{i}", ok))

        threads = [threading.Thread(target=peer_loop, args=(p,)) for p in peers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Per-TODO: exactly one peer should have won (claimed first).
        per_todo_wins: dict[str, list[str]] = {}
        for peer, todo, ok in round_results:
            if ok:
                per_todo_wins.setdefault(todo, []).append(peer)
        for todo_name, winners in per_todo_wins.items():
            # Same-peer re-claims are allowed (idempotent), but only ONE
            # distinct peer should have won each TODO.
            assert len(set(winners)) == 1, (
                f"TODO {todo_name!r} ended up with multiple distinct winners: {set(winners)}"
            )
        # All 20 TODOs got claimed (no orphan).
        assert len(per_todo_wins) == n_todos
