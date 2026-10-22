"""Unit tests for the replay planner: which solve is replayed, and on which geometry."""

import json

from run_viewer.extract import replay


def _kb(tmp_path, rows, geometry_end=False):
    link = tmp_path / "link"
    link.mkdir()
    (link / "knowledge_base.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    if geometry_end:
        (link / "geometry_end.xml").write_text("<cpacs/>")
    return str(link)


def _rows(opened_path, modify_before=False, modify_after=False):
    rows = [
        {"seq": 1, "tool": "open_cpacs", "server": "tigl", "status": "success",
         "inputs": {"source_type": "path", "source": opened_path}, "outputs": {"session_id": "T1"}},
    ]
    if modify_before:
        rows.append({"seq": 2, "tool": "set_high_level_parameters", "server": "tigl", "status": "success",
                     "inputs": {"session_id": "T1"}, "outputs": {}})
    rows += [
        {"seq": 3, "tool": "generate_volume_mesh", "server": "tigl", "status": "success",
         "inputs": {"session_id": "T1", "mesh_size_min": 0.33}, "outputs": {}},
        {"seq": 4, "tool": "create_su2_session", "server": "su2", "status": "success",
         "inputs": {"cpacs_file_path": opened_path, "initial_mesh": "<big>", "target_cl": 0.55},
         "outputs": {"session_id": "S1"}},
        {"seq": 5, "tool": "configure_from_cpacs", "server": "su2", "status": "success",
         "inputs": {"session_id": "S1", "cpacs_file_path": opened_path, "target_cl": 0.6}, "outputs": {}},
        {"seq": 6, "tool": "run_su2_solver", "server": "su2", "status": "success",
         "inputs": {"session_id": "S1", "max_runtime_seconds": 3600},
         "outputs": {"final_coefficients": {"CL": 0.6, "CD": 0.02}}},
    ]
    if modify_after:
        rows.append({"seq": 7, "tool": "set_high_level_parameters", "server": "tigl", "status": "success",
                     "inputs": {"session_id": "T1"}, "outputs": {}})
    return rows


def test_exact_replay_on_the_opened_cpacs(tmp_path):
    src = tmp_path / "prev_geometry.xml"
    src.write_text("<cpacs/>")
    plan = replay.plan_su2(_kb(tmp_path, _rows(str(src))), {}, None)
    assert plan["geometry"] == str(src) and plan["solve_seq"] == 6
    assert plan["mesh_params"] == {"mesh_size_min": 0.33}
    assert "initial_mesh" not in plan["create"] and plan["create"]["target_cl"] == 0.55
    assert plan["configure"] == [{"cpacs_file_path": str(src), "target_cl": 0.6}]
    assert plan["original"]["CL"] == 0.6


def test_recorded_paths_are_mapped_to_this_machine(tmp_path):
    local = tmp_path / "copy" / "g.xml"
    local.parent.mkdir()
    local.write_text("<cpacs/>")
    plan = replay.plan_su2(_kb(tmp_path, _rows("/home/aipexws5/run/g.xml")), {"/home/aipexws5/run/": str(local.parent) + "/"}, None)
    assert plan["geometry"] == str(local)


def test_modified_session_falls_back_to_geometry_end_only_if_nothing_changed_after(tmp_path):
    src = tmp_path / "g.xml"
    src.write_text("<cpacs/>")
    plan = replay.plan_su2(_kb(tmp_path, _rows(str(src), modify_before=True), geometry_end=True), {}, None)
    assert plan["geometry"].endswith("geometry_end.xml")


def test_skip_when_the_geometry_of_the_moment_was_not_saved(tmp_path):
    src = tmp_path / "g.xml"
    src.write_text("<cpacs/>")
    plan = replay.plan_su2(_kb(tmp_path, _rows(str(src), modify_before=True, modify_after=True), geometry_end=True),
                           {}, None)
    assert plan["skip"] == "the geometry at the time of the mesh was not saved"


def test_aviary_plan_needs_recorded_inputs(tmp_path):
    rows = [{"seq": 1, "tool": "run_simulation", "status": "success", "outputs": {"summary": {"fuel_burned_kg": 9.0}}}]
    (tmp_path / "a").mkdir()
    assert "skip" in replay.plan_aviary(_kb(tmp_path / "a", rows))
    rows.append({"seq": 2, "tool": "get_results", "status": "success",
                 "outputs": {"design_parameters": {"aircraft_params": {"a": 1}, "mission_config": {"range_nmi": 2500}}}})
    (tmp_path / "b").mkdir()
    plan = replay.plan_aviary(_kb(tmp_path / "b", rows))
    assert plan["aircraft_params"] == {"a": 1} and plan["original"]["fuel_burned_kg"] == 9.0
