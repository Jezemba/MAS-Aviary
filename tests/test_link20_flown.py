"""D1: links.csv's coupled columns are the inputs of the COUNTED run_simulation, with a source per value, and the bug
signatures of 11_bug_checks.md are one boolean column each (analysis/link20/flown.py). Synthetic link folders only:
no MCP, model, network or run data."""

import copy
import json

import pytest

from analysis.link20 import extract as ex
from analysis.link20 import flown as fl

CL, DF, SCALE, FN = "Mission.Design.LIFT_COEFFICIENT", "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", \
    "Aircraft.Engine.SCALE_FACTOR", 8850.0
DEFAULTS = {"T4_MAX": 2857.0, "splitter.BPR": 5.105, "fan.PR": 1.685, "lpc.PR": 1.935, "hpc.PR": 9.369}
FLOWN = {"Aircraft.Wing.AREA": 120.0, "Aircraft.Wing.ASPECT_RATIO": 10.0, "Aircraft.Wing.SWEEP": 25.0,
         CL: 0.55, DF: 1.2, "Aircraft.Wing.MASS": 9000.0, SCALE: FN / 5900.0}


def _row(agent, tool, args, result):
    return {"agent": agent, "tool": tool, "args": args, "result": result}


def base_rows():
    """A link that flies CL 0.55 / DF 1.2, then solves and sets CL 0.64 / DF 1.8 AFTER its counted mission."""
    return [
        _row("geometry_engineer", "open_cpacs", {"source": "/x.xml"}, {"session_id": "T"}),          # 0
        _row("geometry_engineer", "get_wing_summary", {"session_id": "T", "wing_uid": "Wing1"},
             {"mac_length": 4.19, "full_reference_area": 120.0}),                                       # 1
        _row("geometry_engineer", "generate_volume_mesh", {"session_id": "T"}, {"mesh_size_bytes": 300}),  # 2
        _row("aerodynamics_analyst", "create_su2_session",
             {"cpacs_file_path": "/auto.xml", "target_cl": 0.55, "initial_mesh": "<400 chars>", "aspect_ratio": 10.0},
             {"session_id": "S"}),                                                                       # 3
        _row("aerodynamics_analyst", "run_su2_solver", {"session_id": "S"},
             {"final_coefficients": {"CL": 0.55, "CD": 0.03},
              "drag_assessment": {"cd_reported": 0.03, "cd_induced_floor": 0.012, "source": "su2"}}),   # 4
        _row("structures_analyst", "estimate_mass", {"cpacs_file_path": "/x.xml"},
             {"mass_breakdown": {"components": {"mWing_kg": 9000.0}}}),                                 # 5
        _row("propulsion_analyst", "get_design_inputs", {"session_id": "P"},
             {"design_inputs": [{"name": k, "default": v} for k, v in DEFAULTS.items()]}),               # 6
        _row("propulsion_analyst", "set_inputs", {"session_id": "P", "values": {"Fn_DES": FN}}, {"updated": []}),  # 7
        _row("propulsion_analyst", "run_cycle", {"session_id": "P"}, {"outputs": {"perf.Fn": FN}}),     # 8
        _row("simulation_executor", "generate_engine_deck", {"session_id": "P"},
             {"design_key": "k1", "reference_fn_des_lbf": 5900.0}),                                     # 9
        _row("mission_architect", "set_aircraft_parameters", {"session_id": "A", "parameters": dict(FLOWN)},
             {"success": True}),                                                                         # 10
        _row("simulation_executor", "run_simulation", {"session_id": "A"},
             {"success": True, "summary": {"fuel_burned_kg": 20000.0, "gtow_kg": 95000.0, "wing_mass_kg": 9000.0},
              "engine": {"deck": "/d/deck_k1.deck", "external_deck": True}}),                           # 11
        _row("simulation_executor", "get_results", {"session_id": "A"},
             {"success": True, "fuel_burned_kg": 20000.0, "gtow_kg": 95000.0,
              "design_parameters": {"aircraft_params": dict(FLOWN),
                                    "mission_config": {"takeoff_mass_kg": 90000.0}}}),                  # 12
        _row("aerodynamics_analyst", "configure_from_cpacs", {"session_id": "S", "target_cl": 0.64}, {}),  # 13
        _row("aerodynamics_analyst", "run_su2_solver", {"session_id": "S"},
             {"final_coefficients": {"CL": 0.64, "CD": 0.05}}),                                         # 14
        _row("mission_architect", "set_aircraft_parameters",
             {"session_id": "A", "parameters": {**FLOWN, CL: 0.64, DF: 1.8}}, {"success": True}),       # 15
    ]


CARRY = {"previous_design": {"design_space": {"engine": dict(DEFAULTS)},
                             "geometry": {"wing": {"Aircraft.Wing.AREA": 120.0, "Aircraft.Wing.ASPECT_RATIO": 10.0}},
                             "aero": {"cl_cruise": 0.55, "cd_cruise": 0.03},
                             "structures": {"wing_mass_kg": 9000.0}}}
STEPS = [{"kind": "step", "agent": "aerodynamics_analyst",
          "tool_calls": [{"name": "create_su2_session", "arguments": {"cpacs_file_path": "/typed.xml"}},
                         {"name": "configure_from_cpacs", "arguments": {"session_id": "S"}}]}]


def write_link(tmp_path, rows, *, kb_status=None, carry=CARRY, result=None, steps=STEPS, design=None,
               name="mdo_f25_orchestrated_iterative_feedback"):
    d = tmp_path / "repeat_012" / name
    d.mkdir(parents=True)
    kb = []
    for i, r in enumerate(rows):
        ok = fl.call_ok(r["result"])
        kb.append({"seq": i + 1, "attempt": 1, "agent": r["agent"], "tool": r["tool"],
                   "status": (kb_status or {}).get(i, "success" if ok else "failed"),
                   "inputs": r["args"], "outputs": r["result"]})
    (d / "knowledge_base.jsonl").write_text("\n".join(json.dumps(e) for e in kb))
    (d / "tool_calls_attempt1.jsonl").write_text("\n".join(
        json.dumps({"seq": i, "agent": r["agent"], "tool": r["tool"], "args": r["args"], "result": r["result"],
                    "error": None}) for i, r in enumerate(rows)))
    (d / "step_log_attempt1.jsonl").write_text("\n".join(json.dumps(s) for s in steps))
    (d / "result.json").write_text(json.dumps(result if result is not None else {"attempt": 1}))
    (d / "design.json").write_text(json.dumps(design if design is not None else {
        "mission_applied": {**FLOWN, CL: 0.64, DF: 1.8}, "aero": {"cl_cruise": 0.64, "cd_cruise": 0.05},
        "structures": {"mtom_kg": 91234.5}}))
    if carry is not None:
        (d / "carry_over.json").write_text(json.dumps(carry))
    return d


def extract(tmp_path, rows, **kw):
    d = write_link(tmp_path, rows, **kw)
    return ex.extract_link("t", d.name, 13, d, {"completed": {f"r012_{d.name}": {}}}, dict(DEFAULTS))


# ---------------------------------------------------------------- D1: the flown values

def test_coupled_columns_are_the_counted_flight_not_design_json(tmp_path):
    """O_IF 13: design.json holds the link's last (unflown) CL 0.64 / DF 1.8; the mission flew 0.55 / 1.2."""
    row = extract(tmp_path, base_rows())
    assert row["fuel_kg"] == 20000.0
    assert row[CL] == 0.55 and row[DF] == 1.2 and row["Aircraft.Wing.MASS"] == 9000.0
    assert row[SCALE] == pytest.approx(FN / 5900.0, rel=1e-15)
    assert row[f"src.{CL}"] == "get_results tool_calls_attempt1.jsonl row 12"
    assert row["structures.mtom_kg"] == 90000.0 and "mission_config.takeoff_mass_kg" in row["src.structures.mtom_kg"]
    assert (row["aero.cl_cruise"], row["aero.cd_cruise"]) == (0.55, 0.03)
    assert row["src.aero.cd_cruise"] == "run_su2_solver tool_calls_attempt1.jsonl row 4 drag_assessment.cd_reported"
    assert row["engine_deck_key"] == "k1"
    # what the link left / design.json is kept, under its own names
    assert row[f"design_json.{CL}"] == 0.64 and row["design_json.aero.cl_cruise"] == 0.64
    assert row["design_json.structures.mtom_kg"] == 91234.5
    for f in fl.FLAGS:                       # the clean link raises no flag
        assert row[f"flag.{f}"] is False, (f, row[f"flag.{f}.detail"])


def test_without_get_results_the_inputs_are_session_start_plus_successful_sets(tmp_path):
    rows = base_rows()
    rows[12] = _row("simulation_executor", "check_constraints", {}, {})          # no get_results
    refused = _row("mission_architect", "set_aircraft_parameters",
                   {"session_id": "A", "parameters": {CL: 0.9, "Aircraft.Wing.SPAN": 38.0}},
                   {"success": False, "error_code": "READ_ONLY_PARAMETER"})
    rows.insert(10, refused)                                                     # refused before the good set
    start = {"Aircraft.Fuselage.LENGTH": 37.68, SCALE: 1.0}
    row = extract(tmp_path, rows, result={"attempt": 1, "initial_params": start})
    assert row[CL] == 0.55                                                       # the refused 0.9 never flew
    assert row[f"src.{CL}"] == "set_aircraft_parameters tool_calls_attempt1.jsonl row 11"
    assert row["Aircraft.Fuselage.LENGTH"] == 37.68
    assert row["src.Aircraft.Fuselage.LENGTH"] == "result.json initial_params (session start)"
    assert row["Aircraft.Wing.MASS_SCALER"] is None
    assert row["src.Aircraft.Wing.MASS_SCALER"].startswith("not set on the flown session")
    assert row["flown_inputs_from"] == "session start + successful set_aircraft_parameters"


def test_get_results_after_a_new_set_is_not_the_flight(tmp_path):
    rows = base_rows()
    rows.insert(12, _row("mission_architect", "set_aircraft_parameters",
                         {"session_id": "A", "parameters": {CL: 0.7}}, {"success": True}))
    rows[13]["result"]["design_parameters"]["aircraft_params"][CL] = 0.7          # get_results now reports 0.7
    row = extract(tmp_path, rows, result={"attempt": 1, "initial_params": {}})
    assert row[CL] == 0.55 and row[f"src.{CL}"].startswith("set_aircraft_parameters")


def test_uncounted_zero_fuel_mission_reports_its_inputs_labelled(tmp_path):
    """N_IF 12: the only mission returned 8.2e-06 kg and the KB recorded it as failed; the link did not fly."""
    rows = base_rows()[:13]
    rows[11]["result"]["summary"]["fuel_burned_kg"] = 8.2e-06
    rows[12] = _row("simulation_executor", "check_constraints", {}, {})
    row = extract(tmp_path, rows, kb_status={11: "failed"}, result={"attempt": 1})
    assert row["flew"] is False and row["fuel_kg"] is None and row["wing_mass_kg"] is None
    assert row["Aircraft.Wing.MASS"] == 9000.0
    assert row["src.Aircraft.Wing.MASS"].startswith("NOT COUNTED")
    assert row["flown_inputs_from"].startswith("NOT COUNTED")


def test_no_mission_leaves_every_flown_column_empty(tmp_path):
    row = extract(tmp_path, base_rows()[:10])
    assert row[CL] is None and row[f"src.{CL}"] == "no run_simulation returned a fuel figure"
    assert all(row[f"flag.{f}"] is None for f in fl.FLAGS)


# ---------------------------------------------------------------- the bug-signature flags

def _flag(tmp_path, rows, flag, **kw):
    row = extract(tmp_path, rows, **kw)
    return row[f"flag.{flag}"], row[f"flag.{flag}.detail"]


def test_untrimmed_su2(tmp_path):
    rows = base_rows()
    del rows[3]["args"]["target_cl"]
    rows[4]["result"]["drag_assessment"]["source"] = "su2_unchecked"
    flag, why = _flag(tmp_path, rows, "untrimmed_su2")
    assert flag is True and "no target_cl" in why and "su2_unchecked" in why


def test_cd_below_induced_drag_is_untrimmed(tmp_path):
    rows = base_rows()
    rows[4]["result"]["drag_assessment"].update(cd_reported=0.005, cd_induced_floor=0.009)
    assert _flag(tmp_path, rows, "untrimmed_su2")[0] is True


def test_agent_typed_target_cl(tmp_path):
    steps = [{"kind": "step", "agent": "aerodynamics_analyst", "tool_calls": [
        {"name": "create_su2_session", "arguments": json.dumps({"cpacs_file_path": "/typed.xml", "target_cl": 0.55})}]}]
    flag, why = _flag(tmp_path, base_rows(), "agent_target_cl", steps=steps)
    assert flag is True and "agent typed" in why


def test_target_cl_in_overrides_does_not_pin_it(tmp_path):
    """O_GR 17: TARGET_CL inside overrides is dropped by su2-mcp; the data plane's target_cl ran."""
    steps = [{"kind": "step", "agent": "aerodynamics_analyst", "tool_calls": [
        {"name": "create_su2_session", "arguments": {"cpacs_file_path": "/t.xml", "overrides": {"TARGET_CL": 0.6}}}]}]
    assert _flag(tmp_path, base_rows(), "agent_target_cl", steps=steps)[0] is False


def test_framework_pre_init_target_is_not_the_agents(tmp_path):
    rows = base_rows()
    rows[3]["agent"] = "unknown"
    flag, why = _flag(tmp_path, rows, "agent_target_cl", steps=[])
    assert flag is False and "pre-init" in why


def test_mac_default(tmp_path):
    rows = base_rows()
    rows[1] = _row("geometry_engineer", "get_high_level_parameters", {"session_id": "T", "component_uid": "Wing1"},
                   {"parameters": {"area": 120.0, "aspect_ratio": 10.0, "sweep": 25.0}})
    flag, why = _flag(tmp_path, rows, "mac_default")
    assert flag is True and "no MAC captured" in why


def test_engine_scale_on_bench_reference(tmp_path):
    rows = base_rows()
    for i in (10, 12):
        params = rows[i]["args"]["parameters"] if i == 10 else rows[i]["result"]["design_parameters"]["aircraft_params"]
        params[SCALE] = FN / fl.BENCH_ENGINE_CRUISE_FN_LBF
    flag, why = _flag(tmp_path, rows, "engine_scale_bench_ref")
    assert flag is True and "/ 5936" in why
    rows[11]["result"]["engine"]["external_deck"] = False                          # bench deck flown: correct
    assert _flag(tmp_path / "b", rows, "engine_scale_bench_ref")[0] is False


def test_bench_reference_is_the_frameworks():
    # Runs where the MAS-Aviary src/ tree is next to analysis/ (a full checkout); the analysis-only copy on aipexws8
    # has no src/ and skips it.
    coupling = pytest.importorskip("src.tools.coupling")
    assert fl.BENCH_ENGINE_CRUISE_FN_LBF == coupling.BENCH_ENGINE_CRUISE_FN_LBF


def test_stale_mesh(tmp_path):
    rows = base_rows()
    rows.insert(3, _row("geometry_engineer", "set_high_level_parameters",
                        {"session_id": "T", "component_uid": "Wing1", "updates": {"aspect_ratio": 10.5}},
                        {"geometry_morph": {"rebuilt": True, "after": {"reference_area": 120.0, "aspect_ratio": 10.0,
                                                                        "sweep_deg": 25.0}}}))
    flag, why = _flag(tmp_path, rows, "stale_mesh")
    assert flag is True and "made before reshape" in why


def test_su2_not_flown(tmp_path):
    rows = base_rows()
    rows[10]["args"]["parameters"][CL] = 0.5
    rows[12]["result"]["design_parameters"]["aircraft_params"][CL] = 0.5
    assert _flag(tmp_path, rows, "su2_not_flown")[0] is True


def test_wing_mass_stale(tmp_path):
    rows = base_rows()
    rows[10]["args"]["parameters"]["Aircraft.Wing.MASS"] = 8800.0
    rows[12]["result"]["design_parameters"]["aircraft_params"]["Aircraft.Wing.MASS"] = 8800.0
    flag, why = _flag(tmp_path, rows, "wing_mass_stale")
    assert flag is True and "!= mass-mcp 9000.0" in why


def test_carried_engine_replaced(tmp_path):
    carry = copy.deepcopy(CARRY)
    carry["previous_design"]["design_space"]["engine"]["splitter.BPR"] = 10.0      # deck session never got BPR 10
    flag, why = _flag(tmp_path, base_rows(), "carried_engine_replaced", carry=carry)
    assert flag is True and "intended" in why


def test_engine_scale_left_stale_by_a_refused_set(tmp_path):
    rows = base_rows()
    rows.insert(11, _row("mission_architect", "set_aircraft_parameters",
                         {"session_id": "A", "parameters": {SCALE: 1.7, "Aircraft.Wing.SPAN": 38.0}},
                         {"success": False, "error_code": "READ_ONLY_PARAMETER"}))
    flag, why = _flag(tmp_path, rows, "engine_scale_stale")
    assert flag is True and "refused/failed" in why
