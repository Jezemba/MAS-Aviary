"""B214 (2026-10-15): export_cpacs aimed at a tests/fixtures/ file is redirected to a per-run file.

Live networked validation: geometry agents exported their morphed design back to the file they had opened --
the git-tracked D150 baseline -- 4 times in two links, so every later link and combination started from a
modified "baseline" (fixture md5 b4e6b58f -> 0696bb4b)."""
import os

from src.coordination.design_state import DesignState
from src.tools import data_plane as dp


def setup_function():
    dp.init_data_plane(DesignState(), {})


def test_an_export_to_the_fixture_is_redirected_and_stays_redirected():
    fixture = "/home/x/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
    out = dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": fixture})
    assert out["output_path"] != fixture and "tests/fixtures/" not in out["output_path"]
    assert os.path.basename(out["output_path"]) == "D150_simple.xml"
    again = dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": fixture})
    assert again["output_path"] == out["output_path"]          # the same per-run file every time


def test_any_other_export_path_is_left_alone(tmp_path):
    target = str(tmp_path / "wing_v2.xml")
    out = dp.resolve_request("export_cpacs", {"session_id": "s1", "output_path": target})
    assert out["output_path"] == target
