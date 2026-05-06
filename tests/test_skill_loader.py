"""Tests for SkillLoader — LLM-agnostic skill loading."""

import os
import tempfile
from pathlib import Path

import pytest

from src.skills.skill_loader import SkillLoader


@pytest.fixture
def skill_dir():
    """Create a temporary skill directory with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_path = Path(tmpdir) / "test-skill"
        skill_path.mkdir()
        refs_path = skill_path / "references"
        refs_path.mkdir()

        # Write SKILL.md
        (skill_path / "SKILL.md").write_text(
            "---\nname: test-skill\n---\n\n"
            "# Test Skill\n\n"
            "## Section 1: Overview\n\nOverview content.\n\n"
            "### Geometry\n\nGeometry discipline content.\n\n"
            "### Aerodynamics\n\nAero discipline content.\n\n"
            "## Section 2: Other\n\nOther content.\n"
        )

        # Write reference files
        (refs_path / "tool_catalog.md").write_text("# Tool Catalog\nTool data here.")
        (refs_path / "data_flow.md").write_text("# Data Flow\nFlow data here.")
        (refs_path / "discipline_tradeoffs.md").write_text("# Tradeoffs\nTradeoff data.")
        (refs_path / "f25_constraints.md").write_text("# F25 Constraints\nConstraint data.")

        yield skill_path


class TestSkillLoader:
    def test_is_available(self, skill_dir):
        loader = SkillLoader(skill_dir)
        assert loader.is_available is True

    def test_not_available(self, tmp_path):
        loader = SkillLoader(tmp_path / "nonexistent")
        assert loader.is_available is False

    def test_load_skill_md(self, skill_dir):
        loader = SkillLoader(skill_dir)
        content = loader.load_skill_md()
        assert "# Test Skill" in content
        assert "Overview content" in content

    def test_load_skill_md_cached(self, skill_dir):
        loader = SkillLoader(skill_dir)
        c1 = loader.load_skill_md()
        c2 = loader.load_skill_md()
        assert c1 is c2  # same object (cached)

    def test_load_reference(self, skill_dir):
        loader = SkillLoader(skill_dir)
        content = loader.load_reference("tool_catalog.md")
        assert "Tool data here" in content

    def test_load_reference_missing(self, skill_dir):
        loader = SkillLoader(skill_dir)
        content = loader.load_reference("nonexistent.md")
        assert content == ""

    def test_load_reference_cached(self, skill_dir):
        loader = SkillLoader(skill_dir)
        c1 = loader.load_reference("tool_catalog.md")
        c2 = loader.load_reference("tool_catalog.md")
        assert c1 is c2

    def test_assemble_prompt_sequential(self, skill_dir):
        loader = SkillLoader(skill_dir)
        prompt = loader.assemble_prompt("sequential")
        assert "# Test Skill" in prompt
        assert "Tool data here" in prompt
        assert "Tradeoff data" in prompt
        # Sequential should NOT load data_flow.md
        assert "Flow data here" not in prompt

    def test_assemble_prompt_orchestrated(self, skill_dir):
        loader = SkillLoader(skill_dir)
        prompt = loader.assemble_prompt("orchestrated")
        assert "Tool data here" in prompt
        assert "Flow data here" in prompt
        assert "Constraint data" in prompt
        assert "Tradeoff data" in prompt

    def test_assemble_prompt_networked(self, skill_dir):
        loader = SkillLoader(skill_dir)
        prompt = loader.assemble_prompt("networked")
        assert "Tool data here" in prompt
        assert "Flow data here" in prompt
        assert "Tradeoff data" not in prompt

    def test_assemble_prompt_not_available(self, tmp_path):
        loader = SkillLoader(tmp_path / "nonexistent")
        prompt = loader.assemble_prompt("sequential")
        assert prompt == ""

    def test_get_discipline_prompt(self, skill_dir):
        loader = SkillLoader(skill_dir)
        geo = loader.get_discipline_prompt("Geometry")
        assert "Geometry discipline content" in geo
        aero = loader.get_discipline_prompt("Aerodynamics")
        assert "Aero discipline content" in aero

    def test_get_discipline_prompt_missing(self, skill_dir):
        loader = SkillLoader(skill_dir)
        result = loader.get_discipline_prompt("Propulsion")
        assert result == ""

    def test_detect_mode_claude(self, skill_dir):
        loader = SkillLoader(skill_dir, mode="auto")
        assert loader.detect_mode("claude-3-opus") == "native"
        assert loader.detect_mode("anthropic/claude-3") == "native"

    def test_detect_mode_other(self, skill_dir):
        loader = SkillLoader(skill_dir, mode="auto")
        assert loader.detect_mode("Qwen/Qwen3-8B") == "inject"
        assert loader.detect_mode("gpt-4") == "inject"

    def test_detect_mode_forced(self, skill_dir):
        loader = SkillLoader(skill_dir, mode="inject")
        assert loader.detect_mode("claude-3-opus") == "inject"

    def test_clear_cache(self, skill_dir):
        loader = SkillLoader(skill_dir)
        loader.load_skill_md()
        loader.load_reference("tool_catalog.md")
        assert loader._skill_md is not None
        assert len(loader._cache) > 0

        loader.clear_cache()
        assert loader._skill_md is None
        assert len(loader._cache) == 0
