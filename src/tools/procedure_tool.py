"""``read_procedure`` -- the written-down design sequence, on demand (B85).

A discovery tool like get_design_state and read_design_knowledge, so every agent has it in
every coordination structure. read_design_knowledge says what HAS BEEN DONE; this says what
SHOULD BE DONE, in order, for a role. Jessica, 2026-09-16: not knowing to call a particular
tool is a documentation bug when the sequence is written nowhere the agent can reach.
"""
from __future__ import annotations

from typing import Any

from smolagents import Tool


class ReadProcedure(Tool):
    name = "read_procedure"
    description = (
        "Returns the design procedure: which tools to call, IN ORDER, for a role, what each step "
        "produces and what it needs first. Call it when you are unsure what to do next, when a tool "
        "refuses your call, or when you are waiting on another agent and want work that does not "
        "depend on them. role: geometry | aero | structures | propulsion | mission. With no role it "
        "returns the whole sequence, so you can see which stages depend on which."
    )
    inputs: dict[str, Any] = {
        "role": {"type": "string", "description": "geometry|aero|structures|propulsion|mission",
                 "nullable": True},
    }
    output_type = "string"

    def forward(self, role: str | None = None) -> str:
        from src.tools.procedures import render

        return render(role)
