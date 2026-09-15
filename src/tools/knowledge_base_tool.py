"""``read_design_knowledge`` -- the agent-facing view of the design knowledge base (B81).

Every agent gets it, in every coordination structure, alongside get_design_state:
it is a DISCOVERY tool and must not privilege one structure over another.
"""
from __future__ import annotations

from typing import Any

from smolagents import Tool


class ReadDesignKnowledge(Tool):
    name = "read_design_knowledge"
    description = (
        "Returns the design knowledge base for this run: a record, written by the framework from "
        "real tool results, of design work already DONE -- who did it, when, for which design, the "
        "key values, and the data refs of large results (e.g. the volume mesh). Check it before "
        "repeating geometry, meshing, SU2, mass or cycle work, and to find an existing result. "
        "mode='summary' (default) gives a short digest of what is done, what is still missing and "
        "any failures; mode='full' returns the matching entries themselves (size-capped). Filters: "
        "discipline (geometry|aero|structures|propulsion|mission|integration), agent, tool (e.g. "
        "'generate_volume_mesh'), since_seq. In summary mode, 'question' says what you need to know."
    )
    inputs: dict[str, Any] = {
        "mode": {"type": "string", "description": "'summary' or 'full'", "nullable": True},
        "discipline": {"type": "string", "description": "filter by discipline", "nullable": True},
        "agent": {"type": "string", "description": "filter by agent name", "nullable": True},
        "tool": {"type": "string", "description": "filter by tool name", "nullable": True},
        "since_seq": {"type": "integer", "description": "only entries after this seq", "nullable": True},
        "question": {"type": "string", "description": "summary mode: what you need to know", "nullable": True},
    }
    output_type = "string"

    def forward(self, mode: str | None = None, discipline: str | None = None, agent: str | None = None,
                tool: str | None = None, since_seq: int | None = None, question: str | None = None) -> str:
        from src.tools.knowledge_base import get_kb, summarize

        kb = get_kb()
        if kb is None:
            return '{"entries": [], "note": "No design knowledge base in this context."}'
        filters = dict(discipline=discipline, agent=agent, tool=tool, since_seq=since_seq)
        if (mode or "summary").lower() == "full":
            return kb.read_full(**filters)
        return summarize(kb, question=question, **filters)
