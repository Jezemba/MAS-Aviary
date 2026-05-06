"""LLM-agnostic Skill loader — reads SKILL.md and reference files from disk.

Supports two modes:
  Mode A (native): Claude API skill registration (future — stub for now)
  Mode B (inject): Reads Skill files and assembles into a system prompt string

The framework determines which reference files to load based on the
organizational structure's needs:
  Sequential:   tool_catalog.md + discipline_tradeoffs.md
  Orchestrated: all reference files (orchestrator needs full picture)
  Networked:    tool_catalog.md + data_flow.md
  Graph-routed: tool_catalog.md + f25_constraints.md
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Reference files to load per organizational structure.
_STRATEGY_REFS: dict[str, list[str]] = {
    "sequential": ["tool_catalog.md", "discipline_tradeoffs.md"],
    "orchestrated": [
        "tool_catalog.md",
        "data_flow.md",
        "f25_constraints.md",
        "discipline_tradeoffs.md",
        "design_variables.md",
    ],
    "networked": ["tool_catalog.md", "data_flow.md"],
    "graph_routed": ["tool_catalog.md", "f25_constraints.md"],
}


class SkillLoader:
    """Loads and caches Skill content for injection into agent prompts."""

    def __init__(self, skill_path: str | Path, mode: str = "auto"):
        """Initialize the loader.

        Args:
            skill_path: Path to the skill folder (e.g. "skills/aircraft-design-mdo").
            mode: "auto" (detect LLM type), "native" (Claude API), "inject" (prompt).
        """
        self._path = Path(skill_path)
        self._mode = mode
        self._cache: dict[str, str] = {}
        self._skill_md: str | None = None

    @property
    def skill_path(self) -> Path:
        return self._path

    @property
    def is_available(self) -> bool:
        """True if the skill folder and SKILL.md exist."""
        return (self._path / "SKILL.md").is_file()

    def load_skill_md(self) -> str:
        """Read and cache SKILL.md content."""
        if self._skill_md is not None:
            return self._skill_md

        skill_file = self._path / "SKILL.md"
        if not skill_file.is_file():
            logger.warning("SKILL.md not found at %s", skill_file)
            self._skill_md = ""
            return ""

        self._skill_md = skill_file.read_text(encoding="utf-8")
        return self._skill_md

    def load_reference(self, filename: str) -> str:
        """Read and cache a reference file from the references/ directory."""
        if filename in self._cache:
            return self._cache[filename]

        ref_file = self._path / "references" / filename
        if not ref_file.is_file():
            logger.debug("Reference file not found: %s", ref_file)
            self._cache[filename] = ""
            return ""

        content = ref_file.read_text(encoding="utf-8")
        self._cache[filename] = content
        return content

    def assemble_prompt(self, strategy: str = "sequential") -> str:
        """Assemble a system prompt string from Skill + relevant references.

        Args:
            strategy: Coordination strategy name. Determines which reference
                files are loaded.

        Returns:
            Assembled prompt string, or empty string if skill not available.
        """
        if not self.is_available:
            return ""

        parts = [self.load_skill_md()]

        ref_names = _STRATEGY_REFS.get(strategy, _STRATEGY_REFS["sequential"])
        for ref_name in ref_names:
            content = self.load_reference(ref_name)
            if content:
                parts.append(f"\n\n---\n\n## Reference: {ref_name}\n\n{content}")

        return "\n".join(parts)

    def get_discipline_prompt(self, discipline: str) -> str:
        """Extract discipline-specific content from the Skill.

        Looks for a section header matching the discipline name in SKILL.md
        and returns that section's content.

        Args:
            discipline: One of "geometry", "aerodynamics", "structures",
                "propulsion", "mission".

        Returns:
            The discipline section content, or empty string if not found.
        """
        skill_md = self.load_skill_md()
        if not skill_md:
            return ""

        # Look for section headers like "### Geometry" or "### Aerodynamics"
        import re

        pattern = re.compile(
            rf"^###?\s+.*{re.escape(discipline)}.*$",
            re.IGNORECASE | re.MULTILINE,
        )
        match = pattern.search(skill_md)
        if not match:
            return ""

        # Extract until next section of same or higher level.
        start = match.start()
        level = match.group().count("#")
        next_section = re.compile(rf"^#{{1,{level}}}\s+", re.MULTILINE)
        rest = skill_md[match.end():]
        next_match = next_section.search(rest)
        if next_match:
            end = match.end() + next_match.start()
        else:
            end = len(skill_md)

        return skill_md[start:end].strip()

    def detect_mode(self, model_id: str) -> str:
        """Detect whether to use native (Claude API) or inject mode.

        Args:
            model_id: The configured LLM model ID.

        Returns:
            "native" for Claude API models, "inject" for everything else.
        """
        if self._mode != "auto":
            return self._mode

        claude_indicators = ["claude", "anthropic"]
        model_lower = model_id.lower()
        if any(ind in model_lower for ind in claude_indicators):
            return "native"
        return "inject"

    def clear_cache(self) -> None:
        """Clear all cached content."""
        self._cache.clear()
        self._skill_md = None
