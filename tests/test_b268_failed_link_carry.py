"""B268: after a FAILED link the next link starts from the design the failed link started from.

l20 networked_graph_routed (ws8, 2026-11-11): link 1 flew an AR 11 wing, then failed at its 12 h limit. The
runner kept chain_params (aviary AR 9.37) and chain_cpacs (the AR 9.36 geometry file) but advanced
chain_design_space to the failed link's AR 11 design, and write_carry_over took previous_design from the
failed link's folder; graph pre-initialisation seeded link 2 with it. Links 2 and 3 flew AR 11 on a 9.36 wing.
"""
import json
import re
from pathlib import Path

from scripts.stat_batch_runner import write_carry_over


def _link(root, i, ar):
    d = root / f"repeat_{i:03d}" / "mdo_f25_networked_graph_routed"
    d.mkdir(parents=True)
    doc = {"design_space": {"geometry": {"Wing1": {"aspect_ratio": ar}}}, "coordination_signals": []}
    (d / "design.json").write_text(json.dumps(doc))
    (d / "design_flown.json").write_text(json.dumps(doc))
    return d


def test_previous_design_comes_from_the_last_completed_link_not_the_failed_one(tmp_path):
    done = _link(tmp_path, 0, 9.5)                  # completed
    _link(tmp_path, 1, 11.0)                        # failed: flew AR 11, not carried
    nxt = tmp_path / "repeat_002" / "mdo_f25_networked_graph_routed"
    carry = json.loads(write_carry_over(nxt, 2, "assessment", None, {}, None, previous_dir=str(done)).read_text())
    assert carry["previous_design"]["design_space"]["geometry"]["Wing1"]["aspect_ratio"] == 9.5
    assert carry["previous_integrator_design_state"] == "assessment"     # the failed link's text still goes on


def test_no_completed_link_yet_means_no_previous_design(tmp_path):
    _link(tmp_path, 0, 11.0)                        # link 1 failed
    nxt = tmp_path / "repeat_001" / "mdo_f25_networked_graph_routed"
    carry = json.loads(write_carry_over(nxt, 1, "assessment", None, {}, None, previous_dir="").read_text())
    assert carry["previous_design"] is None


def test_without_previous_dir_the_sibling_folder_is_used_as_before(tmp_path):
    _link(tmp_path, 0, 9.5)
    nxt = tmp_path / "repeat_001" / "mdo_f25_networked_graph_routed"
    carry = json.loads(write_carry_over(nxt, 1, "x", None, {}, None).read_text())
    assert carry["previous_design"]["design_space"]["geometry"]["Wing1"]["aspect_ratio"] == 9.5


def test_the_runner_advances_the_chain_design_only_on_a_completed_link():
    src = (Path(__file__).resolve().parents[1] / "scripts" / "stat_batch_runner.py").read_text()
    assert len(re.findall(r"chain_design_space = \(design_space_of\(", src)) == 1      # the success path only
    assert "previous_dir=chain_design_dir" in src
    assert len(re.findall(r"chain_design_dir = str\(out_path", src)) == 2              # resume + success
