"""Shared mutable blackboard for peer-based networked coordination.

The blackboard is a key-value store that all peer agents can read and
write. It sits alongside SharedHistory as a separate data structure:
SharedHistory is an append-only log of agent messages; the Blackboard
is a live view of current system state (statuses, claims, results,
gaps, predictions) that agents update as they work.

Entry types:
  - status: agent reports what it's currently doing
  - claim: agent claims a subtask (enforcement depends on claiming mode)
  - result: agent posts completed work
  - gap: agent identifies something nobody is handling
  - prediction: agent predicts what another agent will do next

In addition to the entry-based API, the blackboard hosts a TODO list
(see TodoEntry below) used by the concurrent-blackboard selection mode
in NetworkedStrategy. TODOs have atomic claim semantics so multiple
peers can run in parallel threads without two of them executing the
same discipline. The TODO methods (seed_todos, read_todos, claim_todo,
complete_todo, fail_todo, all_todos_done) are thread-safe; the legacy
entry methods are also lock-protected for safety under concurrent
access.
"""

import threading
import time
from dataclasses import dataclass, field

VALID_ENTRY_TYPES = frozenset({"status", "claim", "result", "gap", "prediction"})


# TODO lifecycle states.
TODO_STATUS_PENDING = "pending"
TODO_STATUS_CLAIMED = "claimed"
TODO_STATUS_DONE = "done"
TODO_STATUS_FAILED = "failed"
_VALID_TODO_STATUS = frozenset({
    TODO_STATUS_PENDING,
    TODO_STATUS_CLAIMED,
    TODO_STATUS_DONE,
    TODO_STATUS_FAILED,
})


@dataclass
class TodoEntry:
    """A unit of work on the shared blackboard.

    Used by the concurrent-blackboard selection mode: peers scan the
    TODO list, atomically claim a pending TODO, execute it, then mark
    it done. The atomic claim is what gives the at-most-one-winner
    safety property (see arxiv 2510.18893 / CodeCRDT for the formal
    statement).
    """

    name: str
    description: str
    status: str = TODO_STATUS_PENDING
    assigned_to: str | None = None
    result: str = ""
    failure_reason: str = ""
    created_at: float = field(default_factory=time.time)
    claimed_at: float | None = None
    completed_at: float | None = None


@dataclass
class BlackboardEntry:
    """A single entry on the shared blackboard."""

    key: str
    value: str
    author: str
    entry_type: str
    timestamp: float
    version: int = 1


class Blackboard:
    """Shared mutable state readable and writable by all peer agents.

    Supports three claiming modes:
      - "none": claims are informational only, no enforcement
      - "soft": duplicate claims are allowed with a warning
      - "hard": duplicate claims are rejected (locked)
    """

    def __init__(self, claiming_mode: str = "soft"):
        if claiming_mode not in ("none", "soft", "hard"):
            raise ValueError(f"Invalid claiming_mode {claiming_mode!r}. Must be 'none', 'soft', or 'hard'.")
        self._entries: dict[str, BlackboardEntry] = {}
        self._claiming_mode = claiming_mode
        # Track write events for metrics.
        self._write_count: int = 0
        self._claim_conflicts: int = 0
        # TODO board for concurrent-blackboard selection mode.
        self._todos: dict[str, TodoEntry] = {}
        # Reentrant lock: methods can call other methods that also lock.
        # All public methods on this class take this lock, so the
        # blackboard is safe to share across peer threads.
        self._lock = threading.RLock()

    @property
    def claiming_mode(self) -> str:
        return self._claiming_mode

    @property
    def write_count(self) -> int:
        return self._write_count

    @property
    def claim_conflicts(self) -> int:
        return self._claim_conflicts

    # -- Read operations -------------------------------------------------------

    def read_all(self) -> list[BlackboardEntry]:
        """Return all entries."""
        with self._lock:
            return list(self._entries.values())

    def read_by_type(self, entry_type: str) -> list[BlackboardEntry]:
        """Return entries filtered by type."""
        with self._lock:
            return [e for e in self._entries.values() if e.entry_type == entry_type]

    def read_by_author(self, author: str) -> list[BlackboardEntry]:
        """Return entries filtered by author."""
        with self._lock:
            return [e for e in self._entries.values() if e.author == author]

    def get(self, key: str) -> BlackboardEntry | None:
        """Get a single entry by key."""
        with self._lock:
            return self._entries.get(key)

    def get_claims(self) -> list[BlackboardEntry]:
        """Return all claim entries."""
        return self.read_by_type("claim")

    def is_claimed(self, key: str) -> bool:
        """Check if a key has an active claim entry."""
        with self._lock:
            entry = self._entries.get(key)
            return entry is not None and entry.entry_type == "claim"

    # -- Write operations ------------------------------------------------------

    def write(self, key: str, value: str, author: str, entry_type: str) -> tuple[BlackboardEntry | None, str | None]:
        """Write a new entry or update an existing one.

        Returns:
            (entry, warning) — entry is None on hard-claim rejection,
            warning is a string if a soft-claim conflict occurred.
        """
        if entry_type not in VALID_ENTRY_TYPES:
            raise ValueError(f"Invalid entry_type {entry_type!r}. Must be one of {sorted(VALID_ENTRY_TYPES)}.")

        with self._lock:
            return self._write_locked(key, value, author, entry_type)

    def _write_locked(
        self, key: str, value: str, author: str, entry_type: str
    ) -> tuple[BlackboardEntry | None, str | None]:
        """Caller must hold self._lock."""
        self._write_count += 1
        warning = None

        # Claiming logic.
        if entry_type == "claim" and self._claiming_mode != "none":
            existing = self._entries.get(key)
            if existing and existing.entry_type == "claim" and existing.author != author:
                self._claim_conflicts += 1
                if self._claiming_mode == "hard":
                    return None, f"{key} is locked by {existing.author}"
                else:  # soft
                    warning = f"Warning: {key} already claimed by {existing.author}"

        # If key exists and same author, update in place.
        existing = self._entries.get(key)
        if existing and existing.author == author:
            existing.value = value
            existing.entry_type = entry_type
            existing.timestamp = time.time()
            existing.version += 1
            return existing, warning

        # If key exists but different author, create modified key.
        if existing and existing.author != author:
            modified_key = f"{key}_{author}"
            entry = BlackboardEntry(
                key=modified_key,
                value=value,
                author=author,
                entry_type=entry_type,
                timestamp=time.time(),
                version=1,
            )
            self._entries[modified_key] = entry
            return entry, warning

        # New entry.
        entry = BlackboardEntry(
            key=key,
            value=value,
            author=author,
            entry_type=entry_type,
            timestamp=time.time(),
            version=1,
        )
        self._entries[key] = entry
        return entry, warning

    def update(self, key: str, value: str, author: str) -> BlackboardEntry | None:
        """Update an existing entry's value. Only the author can update.

        Returns the updated entry, or None if key not found or wrong author.
        """
        with self._lock:
            existing = self._entries.get(key)
            if existing is None:
                return None
            if existing.author != author:
                return None
            existing.value = value
            existing.timestamp = time.time()
            existing.version += 1
            self._write_count += 1
            return existing

    def delete(self, key: str, author: str) -> bool:
        """Delete an entry. Only the original author can delete.

        Returns True if deleted, False if not found or wrong author.
        """
        with self._lock:
            existing = self._entries.get(key)
            if existing is None:
                return False
            if existing.author != author:
                return False
            del self._entries[key]
            return True

    # -- TODO board (concurrent-blackboard selection mode) ---------------------
    #
    # The TODO list is a separate data structure from the entries dict.
    # It exists to support the concurrent-blackboard selection mode where
    # multiple peers run in parallel threads and need atomic claim
    # semantics: scan -> claim -> verify -> execute -> done. The lock
    # protecting these methods is the same RLock as the entries methods,
    # so a peer that holds the lock to claim a TODO is also safe to read
    # entries in the same critical section.

    def seed_todos(self, todos: list[tuple[str, str]]) -> int:
        """Seed the TODO list with (name, description) pairs.

        Idempotent on duplicates — a name already on the board is left
        alone (preserves any existing claim/result). Returns the number
        of NEW TODOs added.
        """
        with self._lock:
            added = 0
            for name, description in todos:
                if name in self._todos:
                    continue
                self._todos[name] = TodoEntry(name=name, description=description)
                added += 1
            return added

    def read_todos(self) -> list[TodoEntry]:
        """Snapshot of the TODO list (defensive copy)."""
        with self._lock:
            # Return shallow copies so callers can't mutate internal state
            # accidentally while another thread is updating it.
            return [
                TodoEntry(
                    name=t.name,
                    description=t.description,
                    status=t.status,
                    assigned_to=t.assigned_to,
                    result=t.result,
                    failure_reason=t.failure_reason,
                    created_at=t.created_at,
                    claimed_at=t.claimed_at,
                    completed_at=t.completed_at,
                )
                for t in self._todos.values()
            ]

    def read_pending_todos(self) -> list[TodoEntry]:
        """Snapshot of TODOs in pending status only."""
        return [t for t in self.read_todos() if t.status == TODO_STATUS_PENDING]

    def claim_todo(self, name: str, agent: str) -> tuple[bool, str]:
        """Atomically claim a pending TODO for an agent.

        Returns (success, message). success=True only when this agent
        is the one recorded as assigned_to after the call. This is the
        primitive that gives at-most-one-winner safety to the concurrent
        peers — the entire check-and-set runs under self._lock.
        """
        with self._lock:
            todo = self._todos.get(name)
            if todo is None:
                return False, f"no such TODO: {name!r}"
            if todo.status == TODO_STATUS_DONE:
                return (
                    False,
                    f"TODO {name!r} is already done by {todo.assigned_to!r}",
                )
            if todo.status == TODO_STATUS_CLAIMED and todo.assigned_to != agent:
                return (
                    False,
                    f"TODO {name!r} is currently claimed by {todo.assigned_to!r} — "
                    "try a different TODO",
                )
            # Allow re-claim by the same agent (idempotent) and fresh
            # claim of a previously-failed TODO.
            todo.status = TODO_STATUS_CLAIMED
            todo.assigned_to = agent
            todo.claimed_at = time.time()
            todo.failure_reason = ""
            return True, f"{agent!r} successfully claimed {name!r}"

    def complete_todo(self, name: str, agent: str, result: str) -> tuple[bool, str]:
        """Mark a TODO done. Only the agent that claimed it can complete it."""
        with self._lock:
            todo = self._todos.get(name)
            if todo is None:
                return False, f"no such TODO: {name!r}"
            if todo.assigned_to != agent:
                return (
                    False,
                    f"cannot complete {name!r} — claimed by {todo.assigned_to!r}, "
                    f"not by you ({agent!r})",
                )
            todo.status = TODO_STATUS_DONE
            todo.result = result
            todo.completed_at = time.time()
            return True, f"{agent!r} marked {name!r} done"

    def fail_todo(self, name: str, agent: str, reason: str) -> tuple[bool, str]:
        """Mark a TODO failed and release the claim so another peer can
        retry. Only the claiming agent may fail its own TODO."""
        with self._lock:
            todo = self._todos.get(name)
            if todo is None:
                return False, f"no such TODO: {name!r}"
            if todo.assigned_to != agent:
                return (
                    False,
                    f"cannot fail {name!r} — claimed by {todo.assigned_to!r}, "
                    f"not by you ({agent!r})",
                )
            todo.status = TODO_STATUS_FAILED
            todo.failure_reason = reason
            # Free the claim so another agent can pick it up by issuing
            # a fresh claim_todo call.
            todo.assigned_to = None
            todo.claimed_at = None
            return True, f"{agent!r} marked {name!r} failed: {reason[:80]}"

    def all_todos_done(self) -> bool:
        """True iff the TODO list is non-empty AND every TODO is done."""
        with self._lock:
            if not self._todos:
                return False
            return all(t.status == TODO_STATUS_DONE for t in self._todos.values())

    def render_todos(self, max_chars_result: int = 80) -> str:
        """Human-readable TODO board render for inclusion in agent prompts."""
        with self._lock:
            if not self._todos:
                return "(TODO board empty)"
            lines = []
            for t in self._todos.values():
                row = f"  - {t.name:14s} status={t.status}"
                if t.assigned_to:
                    row += f" assigned_to={t.assigned_to}"
                if t.result:
                    row += f" result={t.result[:max_chars_result]}"
                if t.failure_reason:
                    row += f" failed={t.failure_reason[:max_chars_result]}"
                lines.append(row)
            return "\n".join(lines)

    # -- Context rendering -----------------------------------------------------

    def to_context_string(
        self,
        requesting_agent: str,
        config: dict,
    ) -> str:
        """Render blackboard contents as a string for agent context.

        Filtering rules based on config toggles:
          - peer_monitoring_visible=False: strip metric values from entries
          - trans_specialist_knowledge=False: truncate result values to
            first 100 chars and strip reasoning patterns
          - predictive_knowledge=False: exclude prediction entries entirely

        Claims, statuses, and gaps are always visible regardless of toggles.

        Args:
            requesting_agent: name of the agent requesting the view
            config: dict with toggle keys:
                peer_monitoring_visible (bool, default True)
                trans_specialist_knowledge (bool, default True)
                predictive_knowledge (bool, default False)
        """
        peer_monitoring = config.get("peer_monitoring_visible", True)
        trans_specialist = config.get("trans_specialist_knowledge", True)
        predictive = config.get("predictive_knowledge", False)

        lines = []
        # Snapshot the entries under the lock so we don't iterate over a
        # dict that another thread is mutating. Render outside the lock.
        with self._lock:
            entries_snapshot = list(self._entries.values())
        for entry in entries_snapshot:
            # Filter predictions if disabled.
            if entry.entry_type == "prediction" and not predictive:
                continue

            # Build display value.
            display_value = entry.value

            # Filter trans-specialist knowledge from results.
            if entry.entry_type == "result" and not trans_specialist:
                display_value = _truncate_result(display_value)

            # Filter peer monitoring metrics.
            if not peer_monitoring:
                display_value = _strip_metrics(display_value)

            lines.append(
                f"[{entry.entry_type.upper()}] {entry.key} (by {entry.author}, v{entry.version}): {display_value}"
            )

        if not lines:
            return "Blackboard is empty."

        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._entries)


# -- Helpers -------------------------------------------------------------------


def _truncate_result(value: str, max_chars: int = 400) -> str:
    """Truncate a result value and strip reasoning indicators.

    ~100 tokens ≈ ~400 characters.
    """
    # Strip common reasoning patterns.
    stripped = _strip_reasoning(value)
    if len(stripped) <= max_chars:
        return stripped
    return stripped[:max_chars] + "..."


def _strip_reasoning(value: str) -> str:
    """Remove reasoning-indicator patterns from a value string."""
    lines = value.split("\n")
    filtered = []
    skip = False
    for line in lines:
        lower = line.lower().strip()
        # Skip lines that begin with reasoning indicators.
        if lower.startswith(("reasoning:", "because:", "my reasoning:", "explanation:", "rationale:", "thinking:")):
            skip = True
            continue
        # Resume after a blank line following a reasoning block.
        if skip and not lower:
            skip = False
            continue
        if not skip:
            filtered.append(line)
    return "\n".join(filtered)


def _strip_metrics(value: str) -> str:
    """Remove metric/performance data from an entry value."""
    lines = value.split("\n")
    filtered = []
    for line in lines:
        lower = line.lower().strip()
        if any(
            kw in lower
            for kw in (
                "error_rate",
                "retry_count",
                "success_rate",
                "tool_error",
                "performance:",
                "metric:",
            )
        ):
            continue
        filtered.append(line)
    return "\n".join(filtered)
