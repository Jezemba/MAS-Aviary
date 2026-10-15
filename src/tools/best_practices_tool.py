"""read_best_practices: the lessons from earlier stage prompts, served on request (sequential-prompts-v2).

The v2 stage prompts are short (Jessica, 2026-10-04): what was removed from them and is still true
lives in config/best_practices.md, dated, newest entries last. Agents read it when unsure ("LOOK FOR A
TOOL FIRST"), so it does not fill every prompt.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from smolagents import Tool

PATH = Path(__file__).resolve().parents[2] / "config" / "best_practices.md"
SECTIONS = ("general", "geometry", "aero", "structures", "propulsion", "mission", "simulation", "integrator",
            "networked")


def read_sections(discipline: str | None = None) -> str:
    text = PATH.read_text(encoding="utf-8")
    if not discipline:
        return text
    want = discipline.strip().lower()
    parts = re.split(r"^## ", text, flags=re.M)
    picked = [p for p in parts[1:] if p.split("\n", 1)[0].strip().lower() in ("general", want)]
    if len(picked) < 2 and want != "general":
        return (f"No section '{discipline}'. Sections: {', '.join(SECTIONS)}.\n\n" + text)
    return "\n".join("## " + p.rstrip() for p in picked)


class ReadBestPractices(Tool):
    name = "read_best_practices"
    description = (
        "Returns the best-practice notes for a discipline: tool names that work, settings held fixed "
        "for the study, known pitfalls and how to avoid them. Call it when unsure how to use a tool. "
        "discipline: general | geometry | aero | structures | propulsion | mission | simulation | "
        "integrator | networked (the general notes are always included); omit it for every section."
    )
    inputs: dict[str, Any] = {
        "discipline": {"type": "string", "description": "which section to read", "nullable": True},
    }
    output_type = "string"

    def forward(self, discipline: str | None = None) -> str:  # noqa: D102
        try:
            return read_sections(discipline)
        except OSError as exc:  # pragma: no cover
            return f"best_practices.md could not be read: {exc}"
