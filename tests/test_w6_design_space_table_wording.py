"""W6 (validation 2; Jessica 2026-11-19): the design-space table's Default column and its sentence say these are the
iteration-1 baseline values and that from iteration 2 the DESIGN_STATE values apply.

Every agent of iteration 2 was shown "The following STARTING parameters have been applied ..." with the baseline
122.8287 m^2 / AR 9.3632 next to a DESIGN_STATE of 127 / 9.7 (N_GR) or AR 11.0 (S_GR). Minimal wording, all seven
copies; the Default column is still where link 1's starting design is read from (prompt_assembly._baseline_lines).
"""
from pathlib import Path

import pytest

from src.coordination import prompt_assembly

REPO = Path(__file__).resolve().parents[1]
COPIES = {"sequential.md": 1, "orchestrated.md": 3, "networked.md": 2, "config/mdo_f25_sequential_agents.yaml": 1}
OLD_SENTENCE = "The following STARTING parameters have been applied as an initial baseline they are valid but NOT optimized:"
NEW_SENTENCE = ("The following STARTING parameters have been applied as an initial baseline they are valid but NOT "
                "optimized (the Default column is the iteration-1 baseline; from iteration 2, the DESIGN_STATE values "
                "apply):")
OLD_HEADER = "| Variable | Type | Set in (tool: input) | Used by | Default | Range | Units |"
NEW_HEADER = "| Variable | Type | Set in (tool: input) | Used by | Default (iteration-1 baseline) | Range | Units |"


@pytest.mark.parametrize("source,count", list(COPIES.items()))
def test_w6_every_copy_says_iteration_1_baseline(source, count):
    text = (REPO / source).read_text(encoding="utf-8")
    assert text.count(NEW_SENTENCE) == count and text.count(NEW_HEADER) == count
    assert OLD_SENTENCE not in text and OLD_HEADER not in text


@pytest.mark.parametrize("source,section", [("orchestrated.md", "orchestrated_staged_pipeline"),
                                            ("orchestrated.md", "orchestrated_iterative_feedback"),
                                            ("orchestrated.md", "orchestrated_graph_routed"),
                                            ("networked.md", "networked_iterative_feedback"),
                                            ("networked.md", "networked_graph_routed")])
def test_w6_the_verbatim_system_text_carries_it_and_link1_still_reads_the_defaults(source, section, monkeypatch):
    monkeypatch.setattr(prompt_assembly, "read_carry_over", lambda: None)
    system = prompt_assembly.system_text(source, section)
    assert NEW_SENTENCE in system and NEW_HEADER in system
    lines = "\n".join(prompt_assembly._baseline_lines(system))
    assert "Wing1: area 122.8287, aspect_ratio 9.3632, sweep 21.65" in lines
    assert "Fuselage1: length 37.6446, diameter 4.1437" in lines
    assert "splitter.BPR 5.105" in lines and "T4_MAX 2857" in lines


def test_w6_sequential_yaml_and_md_tables_agree():
    yaml_text = (REPO / "config/mdo_f25_sequential_agents.yaml").read_text(encoding="utf-8")
    md_text = (REPO / "sequential.md").read_text(encoding="utf-8")
    for line in (NEW_SENTENCE, NEW_HEADER):
        assert line in yaml_text and line in md_text
