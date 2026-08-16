"""An agent-facing read tool for the shared design state.

WHY THIS EXISTS (B55, 2026-08-16)

The design state already holds the right answers, per server:

    "sessions": {"su2": "b8db9e82-...", "aviary": "b06be523-..."}

but none of the 58 tools an agent is given could read it. `get_session_info`
needs a session id you already have, which is circular. So a worker's only source
for a session id was the task preamble -- which hands every worker the AVIARY id
and says "use this session_id for ALL tool calls".

Measured consequences in one run (budget_test, orchestrated_graph_routed link 0):

    set_mesh(session_id=<aviary id>)  -> "Unknown session_id: db6bdca4-..."   (B53)
    SU2 config written with Aircraft.Wing.ASPECT_RATIO / TAPER_RATIO
      -> 43 x "invalid option name", every solve dead at parse time  (B54)

Both are the same defect wearing different clothes: aviary-scoped facts presented
as global ones. The agent was not hallucinating -- it was using what it was given.

WHY A READ TOOL RATHER THAN INJECTION

Rewriting a worker's wrong session id in the middleware would fix the symptom for
every combination, including the ones that currently succeed -- handing a crutch
to structures that do not need it and erasing a real behavioural difference
between them. Whether an agent *looks up* the state it needs is itself a
coordination behaviour worth measuring; doing it silently on the agent's behalf
would destroy the measurement.

This tool is read-only, uniform across all eight combinations, and additive: it
changes what the agent CAN find out, not what happens to its calls.
"""
from __future__ import annotations

import json
from typing import Any

from smolagents import Tool

# Responses are model context. The state can grow large (captured meshes, full
# config dumps), so the payload is bounded and truncation is reported rather than
# silently applied -- a caller must never believe it saw the whole picture.
_MAX_CHARS = 3000


class GetDesignState(Tool):
    """Expose the shared design state: per-server sessions and current values."""

    name = "get_design_state"
    description = (
        "Returns the SHARED design state for this run: the session id for EACH "
        "MCP server (su2, aviary, tigl, pycycle, mass), the design parameters "
        "captured so far, and whether SU2 aero has been coupled into the mission. "
        "Use this to find the session id for the server you are about to call -- "
        "each server has its OWN session namespace, so a session id from one "
        "server is not valid for another. Call it before passing a session_id you "
        "did not create yourself."
    )
    inputs: dict[str, Any] = {}
    output_type = "string"

    def forward(self) -> str:  # noqa: D102
        from src.tools.data_plane import get_design_state

        ds = get_design_state()
        if ds is None:
            return json.dumps({
                "sessions": {},
                "note": "No design state yet — nothing has been captured in this run.",
            })

        store = dict(getattr(ds, "data_store", None) or {})
        payload: dict[str, Any] = {
            # The whole point: which id belongs to which server.
            "sessions": dict(getattr(ds, "sessions", None) or {}),
            "design_parameters": store.get("analysis_vars") or {},
            "aero_coupling_status": store.get("aero_coupling_status"),
        }
        payload["note"] = (
            "Each server has its own session namespace. Use sessions['su2'] for "
            "SU2 tools, sessions['aviary'] for aviary tools, and so on. If the "
            "server you need is absent, create its session with that server's own "
            "call (create_su2_session, create_session, open_cpacs, "
            "create_cycle_model). Design parameter names such as "
            "'Aircraft.Wing.TAPER_RATIO' belong to aviary/tigl and are NOT valid "
            "SU2 config options."
        )

        text = json.dumps(payload, indent=2, default=str)
        if len(text) > _MAX_CHARS:
            payload["design_parameters"] = {
                "_truncated": True,
                "count": len(payload["design_parameters"]),
                "note": "Too large to inline; query the owning server directly.",
            }
            text = json.dumps(payload, indent=2, default=str)
        return text
