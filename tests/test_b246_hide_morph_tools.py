"""B246: morph_wing / morph_fuselage are kept out of every agent toolset (the data plane captures geometry only from
set_high_level_parameters, so a morph left the mission flying the old wing)."""
from __future__ import annotations

from src.tools import data_plane as dp
from src.tools import tool_loader


class _T:
    def __init__(self, name):
        self.name = name          # no forward/inputs: the middleware wrap skips it


def _names(tools):
    return [t.name for t in tools]


def test_morph_tools_hidden_from_a_full_toolset(monkeypatch):
    monkeypatch.setattr(dp, "_registered_tools", {})
    all_tools = [_T(n) for n in ("open_cpacs", "set_high_level_parameters", "morph_wing", "morph_fuselage",
                                 "generate_volume_mesh", "run_simulation")]
    # what networked peers and the orchestrator's pool get: every loaded tool
    selected = tool_loader._register_framework_tools(all_tools, list(all_tools))
    assert _names(selected) == ["open_cpacs", "set_high_level_parameters", "generate_volume_mesh", "run_simulation"]


def test_morph_tools_hidden_even_when_named(monkeypatch):
    monkeypatch.setattr(dp, "_registered_tools", {})
    all_tools = [_T("set_high_level_parameters"), _T("morph_wing")]
    assert _names(tool_loader._register_framework_tools(all_tools, list(all_tools))) == ["set_high_level_parameters"]


def test_hidden_list_is_only_the_morph_tools():
    assert tool_loader.AGENT_HIDDEN_TOOLS == ("morph_wing", "morph_fuselage")


def test_no_prompt_or_role_list_offers_the_morph_tools():
    from pathlib import Path

    for f in ("sequential.md", "orchestrated.md", "networked.md", "config/mdo_f25_sequential_agents.yaml",
              "config/mdo_f25_orchestrated_agents.yaml", "config/mdo_f25_networked_agents.yaml"):
        text = Path(f).read_text(encoding="utf-8")
        assert "morph_wing" not in text and "morph_fuselage" not in text, f
