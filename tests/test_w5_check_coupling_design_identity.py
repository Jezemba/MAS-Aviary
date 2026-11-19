"""W5 (validation 2): check_coupling compares geometry, not call order, for "geometry -> SU2 mesh" / "geometry -> mass".

N_GR it1 (ws8 final_val2 val2_net_graph repeat_000): #67 morphed Wing1 to 127 m^2 / AR 9.7, #68 meshed, #69 exported,
#75 estimated the mass on that export; #101 re-opened the baseline, #102 morphed it to exactly the same wing, #107 was a
no-op (rebuilt false, before == after); #109/#110 solved on the #68 mesh. The check read "mesh #68 / mass #75 before the
last reshape #107" -- two false FAILs. It now uses the framework's design identity (duplicate_guard.design_identity,
B372), replayed from the link's own calls. Fixture: every recorded call of that link, results trimmed to the fields the
checks read (tests/fixtures/w5_n_gr_it1_calls.json). Files only.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.check_coupling import FAIL, PASS, Link, check_geometry_to_su2, check_mass
from src.tools.duplicate_guard import design_identity_trace

CALLS = json.loads((Path(__file__).parent / "fixtures" / "w5_n_gr_it1_calls.json").read_text())["calls"]


def _link(tmp_path, calls):
    d = tmp_path / "repeat_000" / "mdo_f25_networked_graph_routed"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"attempt": 1, "chain_link": 0, "org_structure": "networked"}))
    (d / "design.json").write_text("{}")
    (d / "tool_calls_attempt1.jsonl").write_text("\n".join(json.dumps(c) for c in calls))
    return Link(d)


def _status(checks, name):
    return next(c for c in checks if c[0] == name)


def test_w5_recorded_n_gr_it1_is_what_the_report_says():
    assert [CALLS[i]["tool"] for i in (66, 67, 68, 69, 75, 101, 102, 107, 109, 110)] == [
        "open_cpacs", "set_high_level_parameters", "generate_volume_mesh", "export_cpacs", "estimate_mass",
        "open_cpacs", "set_high_level_parameters", "set_high_level_parameters", "create_su2_session", "run_su2_solver"]
    assert CALLS[107]["result"]["geometry_morph"]["rebuilt"] is False
    assert CALLS[102]["result"]["geometry_morph"]["after"] == CALLS[67]["result"]["geometry_morph"]["after"]


def test_w5_identity_trace_is_the_frameworks_notion():
    ids = design_identity_trace(CALLS, lambda r: not r.get("error"))
    assert ids[67] != ids[66]                       # a real morph is a new design
    assert ids[68] == ids[75] == ids[102] == ids[107] == ids[109]   # re-open + identical morph + no-op: same design


def test_w5_n_gr_it1_identical_re_applied_wing_is_not_stale(tmp_path):
    link = _link(tmp_path, CALLS)
    mission, flight = link.final_mission()
    assert mission is not None and mission > 110
    mesh = _status(check_geometry_to_su2(link, mission), "geometry -> SU2 mesh")
    mass = _status(check_mass(link, mission), "geometry -> mass")
    assert mesh[1] == PASS and "reshape #107, mesh #68 (same design as the solve)" in mesh[2]
    assert mass[1] == PASS, mass


def test_w5_a_real_change_after_the_mesh_and_mass_still_fails(tmp_path):
    calls = json.loads(json.dumps(CALLS))
    morph = calls[107]["result"]["geometry_morph"]
    morph["rebuilt"] = True
    morph["after"] = dict(morph["after"], aspect_ratio=10.5, reference_area=130.0)   # #107 really reshapes the wing
    link = _link(tmp_path, calls)
    mission, _ = link.final_mission()
    mesh = _status(check_geometry_to_su2(link, mission), "geometry -> SU2 mesh")
    mass = _status(check_mass(link, mission), "geometry -> mass")
    assert mesh[1] == FAIL and "the solve ran on" in mesh[2]
    assert mass[1] == FAIL and "the mission" in mass[2]

