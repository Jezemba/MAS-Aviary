"""Centralized design state for multi-MCP MDO pipelines.

Tracks per-MCP session IDs, scalar discipline results, constraint
evaluations, and iteration history for convergence detection.
Replaces the single SESSION_ID shared-state key with a structured
object that flows through all coordination strategies.

Backward compatible: DesignState wraps the legacy SESSION_ID key
so existing single-MCP pipelines continue to work unchanged.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field


@dataclass
class ConstraintStatus:
    """Status of a single design constraint evaluation."""

    value: float | None = None
    limit: float = 0.0
    operator: str = "<="  # "<=", ">=", "==", "<", ">"
    satisfied: bool | None = None
    fidelity: str = "unknown"  # "high", "medium", "low", "unknown"
    source_mcp: str = ""

    def evaluate(self) -> bool:
        """Evaluate the constraint and update self.satisfied."""
        if self.value is None:
            self.satisfied = None
            return False
        ops = {
            "<=": self.value <= self.limit,
            ">=": self.value >= self.limit,
            "==": self.value == self.limit,
            "<": self.value < self.limit,
            ">": self.value > self.limit,
        }
        self.satisfied = ops.get(self.operator, False)
        return self.satisfied

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "limit": self.limit,
            "operator": self.operator,
            "satisfied": self.satisfied,
            "fidelity": self.fidelity,
            "source_mcp": self.source_mcp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ConstraintStatus:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class DesignState:
    """Centralized state for multi-MCP MDO pipelines.

    Tracks:
    - CPACS file path (shared geometry contract)
    - Per-MCP session IDs
    - Scalar results from each discipline
    - Constraint evaluation status
    - Iteration counter and history for convergence tracking
    """

    # CPACS file path — shared geometry contract between MCPs.
    cpacs_file_path: str = ""

    # Per-MCP session IDs: {"tigl": "uuid-1", "su2": "uuid-2", ...}
    sessions: dict[str, str] = field(default_factory=dict)

    # Scalar results from each discipline.
    # Keys are descriptive: "drag_coefficient", "oem_kg", "fuel_burned_kg", etc.
    results: dict[str, float] = field(default_factory=dict)

    # Constraint evaluation status.
    # Keys match constraint names: "range_nm", "tofl_m", etc.
    constraints: dict[str, ConstraintStatus] = field(default_factory=dict)

    # Outer MDO loop iteration counter.
    iteration: int = 0

    # History of previous iterations for convergence tracking.
    # Each entry: {"iteration": N, "mtom_kg": ..., "fuel_burned_kg": ..., ...}
    history: list[dict] = field(default_factory=list)

    # Data store for large inter-MCP payloads (base64 meshes, STEP files,
    # etc.).  The LLM never sees these — they flow through the data plane.
    # Keys are descriptive: "tigl_wing_mesh_su2", "tigl_full_step", etc.
    # Values are the raw payload strings (base64, file paths, etc.).
    data_store: dict[str, str] = field(default_factory=dict)

    # -- Backward compatibility -------------------------------------------------

    @property
    def session_id(self) -> str | None:
        """Legacy SESSION_ID accessor for single-MCP backward compat.

        Returns the aviary session ID if available, else the first session,
        else None.
        """
        if "aviary" in self.sessions:
            return self.sessions["aviary"]
        if self.sessions:
            return next(iter(self.sessions.values()))
        return None

    @session_id.setter
    def session_id(self, value: str) -> None:
        """Legacy setter — stores as the 'aviary' session."""
        self.sessions["aviary"] = value

    # -- Session management -----------------------------------------------------

    def set_session(self, mcp_name: str, session_id: str) -> None:
        """Register a session ID for an MCP server."""
        self.sessions[mcp_name] = session_id

    def get_session(self, mcp_name: str) -> str | None:
        """Get the session ID for an MCP server."""
        return self.sessions.get(mcp_name)

    # -- Results ----------------------------------------------------------------

    def set_result(self, key: str, value: float) -> None:
        """Store a scalar result from a discipline."""
        self.results[key] = value

    def get_result(self, key: str) -> float | None:
        """Get a scalar result, or None if not set."""
        return self.results.get(key)

    # -- Data store (large payload data plane) ------------------------------------

    def store(self, key: str, payload: str) -> None:
        """Store a large payload (base64 mesh, file path, etc.)."""
        self.data_store[key] = payload

    def retrieve(self, key: str) -> str | None:
        """Retrieve a stored payload by key."""
        return self.data_store.get(key)

    def list_stored(self) -> dict[str, int]:
        """Return stored keys with their payload sizes in bytes."""
        return {k: len(v) for k, v in self.data_store.items()}

    # -- Constraints ------------------------------------------------------------

    def set_constraint(
        self,
        name: str,
        *,
        value: float | None = None,
        limit: float = 0.0,
        operator: str = "<=",
        fidelity: str = "unknown",
        source_mcp: str = "",
    ) -> None:
        """Add or update a constraint."""
        self.constraints[name] = ConstraintStatus(
            value=value,
            limit=limit,
            operator=operator,
            fidelity=fidelity,
            source_mcp=source_mcp,
        )

    def evaluate_constraints(self) -> dict[str, bool | None]:
        """Evaluate all constraints and return name→satisfied map."""
        return {name: c.evaluate() for name, c in self.constraints.items()}

    def all_constraints_satisfied(self) -> bool:
        """True if all evaluable constraints are satisfied."""
        for c in self.constraints.values():
            c.evaluate()
            if c.satisfied is not True:
                return False
        return bool(self.constraints)

    # -- Iteration / convergence ------------------------------------------------

    def record_iteration(self, **metrics: float) -> None:
        """Snapshot current results + extra metrics into history."""
        entry = {"iteration": self.iteration}
        entry.update(self.results)
        entry.update(metrics)
        self.history.append(entry)
        self.iteration += 1

    def mtom_converged(self, tolerance: float = 0.005) -> bool:
        """Check if MTOM change between last two iterations < tolerance.

        Args:
            tolerance: Fractional change threshold (0.005 = 0.5%).

        Returns:
            True if converged, False if not enough data or still changing.
        """
        if len(self.history) < 2:
            return False
        prev = self.history[-2].get("mtom_kg") or self.history[-2].get("gross_mass_kg")
        curr = self.history[-1].get("mtom_kg") or self.history[-1].get("gross_mass_kg")
        if prev is None or curr is None or prev == 0:
            return False
        return abs(curr - prev) / abs(prev) < tolerance

    # -- Serialization ----------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to a plain dict for YAML/JSON-compatible shared state.

        Note: data_store is intentionally excluded — it contains large
        binary payloads that should not be serialized into shared state
        visible to the LLM.  Use store/retrieve methods directly.
        """
        return {
            "cpacs_file_path": self.cpacs_file_path,
            "sessions": dict(self.sessions),
            "results": dict(self.results),
            "constraints": {k: v.to_dict() for k, v in self.constraints.items()},
            "iteration": self.iteration,
            "history": [dict(h) for h in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> DesignState:
        """Deserialize from a plain dict."""
        constraints = {}
        for k, v in data.get("constraints", {}).items():
            constraints[k] = ConstraintStatus.from_dict(v) if isinstance(v, dict) else v

        return cls(
            cpacs_file_path=data.get("cpacs_file_path", ""),
            sessions=dict(data.get("sessions", {})),
            results=dict(data.get("results", {})),
            constraints=constraints,
            iteration=data.get("iteration", 0),
            history=list(data.get("history", [])),
        )

    # -- Context formatting for agent prompts -----------------------------------

    def to_context_string(self) -> str:
        """Format as a human-readable context block for injection into prompts."""
        parts = ["DESIGN STATE:"]

        if self.cpacs_file_path:
            parts.append(f"  CPACS file: {self.cpacs_file_path}")

        if self.sessions:
            parts.append("  Sessions:")
            for mcp, sid in sorted(self.sessions.items()):
                parts.append(f"    {mcp}: {sid}")

        if self.results:
            parts.append("  Results:")
            for key, val in sorted(self.results.items()):
                parts.append(f"    {key}: {val}")

        if self.constraints:
            parts.append("  Constraints:")
            for name, c in sorted(self.constraints.items()):
                status = "?" if c.satisfied is None else ("PASS" if c.satisfied else "FAIL")
                val_str = f"{c.value}" if c.value is not None else "?"
                parts.append(f"    {name}: {val_str} {c.operator} {c.limit} [{status}] ({c.fidelity})")

        parts.append(f"  Iteration: {self.iteration}")

        if self.data_store:
            parts.append("  Stored data (available via ref):")
            for key, payload in sorted(self.data_store.items()):
                parts.append(f"    {key}: {len(payload):,} bytes")

        # Legacy compat: also emit SESSION_ID for stages that expect it.
        sid = self.session_id
        if sid:
            parts.append(f"  SESSION_ID: {sid}")

        return "\n".join(parts)

    def __repr__(self) -> str:
        return (
            f"DesignState(sessions={list(self.sessions.keys())}, "
            f"results={len(self.results)}, "
            f"constraints={len(self.constraints)}, "
            f"iteration={self.iteration})"
        )
