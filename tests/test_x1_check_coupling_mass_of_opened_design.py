"""X1 (validation 3, 2026-11-19): check_coupling `geometry -> mass` false FAIL after no-op reshapes.

N_IF val3 (ws8 final_val3 val3_net_iter repeat_000): #6 estimate_mass (agent_3) read the framework's copy
(avion_mass_*/D150_simple.xml) of the CPACS the session was opened from (#0); #9-#13 set_high_level_parameters
("apply starting design") all answered no_op true / rebuilt false; #14 exported that unchanged geometry; the mission flew
it. The check passed the design-identity test (B377) and then still demanded an export before the mass: "mass read
.../avion_mass_qvvgsskf/D150_simple.xml, last export None" -> FAIL. Fixture: every recorded call of that link, long
strings trimmed (tests/fixtures/x1_n_if_val3_calls.json). Files only.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.check_coupling import FAIL, PASS, Link, check_mass

CALLS = json.loads((Path(__file__).parent / "fixtures" / "x1_n_if_val3_calls.json").read_text())["calls"]


def _link(tmp_path, calls):
    d = tmp_path / "repeat_000" / "mdo_f25_networked_iterative_feedback"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"attempt": 1, "chain_link": 0, "org_structure": "networked"}))
    (d / "design.json").write_text("{}")
    (d / "tool_calls_attempt1.jsonl").write_text("\n".join(json.dumps(c) for c in calls))
    return Link(d)


def _mass(link):
    mission, _ = link.final_mission()
    return next(c for c in check_mass(link, mission) if c[0] == "geometry -> mass")


def test_x1_recorded_n_if_is_what_the_report_says():
    assert [CALLS[i]["tool"] for i in (0, 6, 9, 10, 11, 13, 14)] == [
        "open_cpacs", "estimate_mass", "set_high_level_parameters", "set_high_level_parameters",
        "set_high_level_parameters", "set_high_level_parameters", "export_cpacs"]
    assert "avion_mass_" in CALLS[6]["args"]["cpacs_file_path"]
    assert all(CALLS[i]["result"]["geometry_morph"]["no_op"] is True for i in (9, 10, 11, 13))


def test_x1_mass_of_the_opened_design_that_flew_passes(tmp_path):
    status = _mass(_link(tmp_path, CALLS))
    assert status[1] == PASS, status
    assert "framework copy of the opened" in status[2] and "no export before it" in status[2]


def test_x1_mass_of_another_file_still_fails(tmp_path):
    calls = json.loads(json.dumps(CALLS))
    calls[6]["args"]["cpacs_file_path"] = "/data/x/tmp/avion_mass_abc/some_other_wing.xml"
    status = _mass(_link(tmp_path, calls))
    assert status[1] == FAIL, status


def test_x1_a_real_reshape_after_the_mass_still_fails(tmp_path):
    calls = json.loads(json.dumps(CALLS))
    morph = calls[9]["result"]["geometry_morph"]
    morph.update(rebuilt=True, no_op=False)
    morph["after"] = dict(morph["after"], aspect_ratio=10.5, reference_area=130.0)
    status = _mass(_link(tmp_path, calls))
    assert status[1] == FAIL and "the mission" in status[2], status


def test_x1_an_auto_exporting_close_before_the_mass_is_not_excused(tmp_path):
    # With a close (auto-export) before the mass the framework hands mass-mcp that export, not the opened file; a
    # mass on the opened file then is not what the framework would have done and is not excused by X1.
    calls = json.loads(json.dumps(CALLS))
    sid = calls[0]["result"]["session_id"]
    close = {"tool": "close_cpacs", "agent": "agent_1", "args": {"session_id": sid},
             "result": {"success": True, "auto_exported": True,
                        "cpacs_file_path": f"/tmp/cpacs_autoexport_{sid}.xml"}, "error": None}
    calls.insert(5, close)
    status = _mass(_link(tmp_path, calls))
    assert status[1] == FAIL, status
