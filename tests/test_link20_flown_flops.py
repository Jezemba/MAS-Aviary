"""analysis/link20 under the final re-run's contract (MAS-Aviary integration/final-rerun-2026-11-18, B305-B343):
drag = aviary FLOPS only (no SU2 value flown), Fn_DES from aviary get_cruise_point, unconverged missions are failures,
measured tails, held / flown design fingerprints, mass_breakdown possibly behind a ref (B342). Synthetic link folders
only: no MCP, model, network or run data."""

import copy

from analysis.link20 import extract as ex
from analysis.link20 import flown as fl
from tests.test_link20_flown import DEFAULTS, _row, write_link

FP, OTHER_FP = "bf0767fe720e83af", "0123456789abcdef"
MASS, LD = 91197.1, 17.05298257065294
FN_DES = MASS * 9.81 / LD / 2 / 4.448 * 1.25
SCALE = FN_DES / 5900.0
TAILS = {"Aircraft.HorizontalTail.AREA": 31.1184, "Aircraft.HorizontalTail.ASPECT_RATIO": 4.981,
         "Aircraft.HorizontalTail.TAPER_RATIO": 0.33, "Aircraft.HorizontalTail.THICKNESS_TO_CHORD": 0.1,
         "Aircraft.VerticalTail.AREA": 21.5, "Aircraft.VerticalTail.ASPECT_RATIO": 1.4949,
         "Aircraft.VerticalTail.TAPER_RATIO": 0.349, "Aircraft.VerticalTail.THICKNESS_TO_CHORD": 0.11}
PARAMS = {"Aircraft.Wing.AREA": 122.8287, "Aircraft.Wing.ASPECT_RATIO": 9.3632, "Aircraft.Wing.SWEEP": 21.6502,
          "Aircraft.Wing.MASS": 8013.98, "Aircraft.Engine.SCALE_FACTOR": SCALE, **TAILS}
CARRY = {"previous_design": {"design_space": {"engine": dict(DEFAULTS)}}}


def flops_rows():
    return [
        _row("unknown", "open_cpacs", {"source": "/x.xml"}, {"session_id": "T"}),                       # 0
        _row("unknown", "generate_volume_mesh", {"session_id": "T"}, {"mesh_size_bytes": 300}),           # 1
        _row("unknown", "create_su2_session", {"target_cl": 0.6525, "initial_mesh": "<400 chars>"},
             {"session_id": "S"}),                                                                         # 2
        _row("unknown", "run_su2_solver", {"session_id": "S"},
             {"final_coefficients": {"CL": 0.6568, "CD": 0.0469},
              "drag_assessment": {"cd_reported": 0.0469, "cd_induced_floor": 0.019, "source": "su2"}}),   # 3
        _row("unknown", "estimate_mass", {"cpacs_file_path": "/x.xml"},
             {"status": "success", "mass_breakdown": {
                 "mTOM_sized_kg": 91197.0, "sizing_converged": True, "components": {"mWing_kg": 8013.98},
                 "sizing_report": {"driver": {"converged": True}, "tails": dict(TAILS)}}}),               # 4
        _row("unknown", "get_cruise_point", {"session_id": "A", "mass_kg": MASS},
             {"success": True, "cruise_point": {"mass_kg": MASS, "cl": 0.6509, "cd": 0.0382, "lift_to_drag": LD,
                                                "design_fingerprint": FP}, "design_fingerprint": FP}),    # 5
        _row("unknown", "set_inputs", {"session_id": "P", "values": {"Fn_DES": FN_DES}}, {"updated": []}),  # 6
        _row("unknown", "run_cycle", {"session_id": "P"}, {"outputs": {"perf.Fn": FN_DES}}),               # 7
        _row("unknown", "generate_engine_deck", {"session_id": "P"},
             {"design_key": "7815057d6edc9732", "reference_fn_des_lbf": 5900.0}),                          # 8
        _row("unknown", "set_aircraft_parameters", {"session_id": "A", "parameters": dict(PARAMS)},
             {"success": True, "design_fingerprint": FP}),                                                 # 9
        _row("unknown", "run_simulation", {"session_id": "A"},
             {"success": True, "converged": True, "design_fingerprint": FP, "flown_design_fingerprint": FP,
              "summary": {"fuel_burned_kg": 16638.69, "gtow_kg": 91982.2, "wing_mass_kg": 8013.98},
              "engine": {"deck": "/d/deck_7815057d6edc9732.deck", "external_deck": True}}),               # 10
        _row("unknown", "get_results", {"session_id": "A"},
             {"success": True, "converged": True, "fuel_burned_kg": 16638.69, "design_fingerprint": FP,
              "flown_design_fingerprint": FP,
              "design_parameters": {"aircraft_params": dict(PARAMS), "mission_config": {"takeoff_mass_kg": MASS}}}),
    ]


DESIGN_FLOWN = {"aero": {"drag_coupling": "solved, not coupled to drag"},
                "engine_sizing": {"fn_des_basis": f"aviary cruise L/D {LD:.3f} (...) at {MASS} kg "
                                                  "(baseline sized mass (canonical baseline.sized_mtom_kg))"},
                "structures": {"wing_mass_kg": 8013.98, "sizing_converged": True, "tails": dict(TAILS)}}


def extract(tmp_path, rows, *, design_flown=DESIGN_FLOWN, **kw):
    d = write_link(tmp_path, rows, carry=CARRY, steps=[], design={"aero": {"drag_coupling": "solved"}}, **kw)
    if design_flown is not None:
        (d / "design_flown.json").write_text(__import__("json").dumps(design_flown))
    return ex.extract_link("t", d.name, 13, d, {"completed": {f"r012_{d.name}": {}}}, dict(DEFAULTS))


def test_contract_is_detected_from_the_run(tmp_path):
    assert fl.detect_contract({}, {}, [{"tool": "get_cruise_point"}])[0] == fl.CONTRACT_FLOPS
    assert fl.detect_contract({"aero": {"drag_coupling": "x"}}, {}, [])[0] == fl.CONTRACT_FLOPS
    assert fl.detect_contract({"aero": {"cl_cruise": 0.5}}, {}, [{"tool": "run_su2_solver"}])[0] == fl.CONTRACT_V1


def test_clean_flops_flight(tmp_path):
    row = extract(tmp_path, flops_rows())
    assert row["contract"] == fl.CONTRACT_FLOPS and row["fuel_kg"] == 16638.69
    assert row["aero.cl_cruise"] is None and "B324" in row["src.aero.cl_cruise"]
    assert row["su2.cl"] == 0.6568 and "recorded only" in row["src.su2.cl"]
    assert row["flown_design_fingerprint"] == FP and "flown_design_fingerprint" in row["src.flown_design_fingerprint"]
    assert row["cruise.lift_to_drag"] == LD and row["cruise.design_fingerprint"] == FP
    assert row["engine.fn_des_lbf"] == FN_DES
    assert row["trim_mass_basis_kg"] == MASS
    assert row["trim_mass_basis_source"] == "baseline sized mass (canonical baseline.sized_mtom_kg)"
    assert row["Aircraft.VerticalTail.AREA"] == 21.5
    for f in ("su2_not_flown", "mac_default"):
        assert row[f"flag.{f}"] is None and "retired" in row[f"flag.{f}.detail"]
    for f in ("untrimmed_su2", "stale_mesh", "wing_mass_stale", "engine_scale_bench_ref", "engine_scale_stale",
              "cruise_point_mismatch", "engine_stale_basis", "mass_not_converged", "unmeasured_tails",
              "refusal_codes"):
        assert row[f"flag.{f}"] is False, (f, row[f"flag.{f}.detail"])
    assert "recorded only" in row["flag.untrimmed_su2.detail"]


def test_cruise_point_of_another_design(tmp_path):
    rows = flops_rows()
    rows[10]["result"]["flown_design_fingerprint"] = OTHER_FP          # flew a design the point was not of
    row = extract(tmp_path, rows)
    assert row["flag.cruise_point_mismatch"] is True and row["flag.engine_stale_basis"] is True
    assert "flew 0123456789abcdef" in row["flag.engine_stale_basis.detail"]


def test_older_aviary_run_reply_flies_the_held_design(tmp_path):
    rows = flops_rows()
    del rows[10]["result"]["flown_design_fingerprint"]
    row = extract(tmp_path, rows)
    assert row["flown_design_fingerprint"] == FP and "before B339" in row["src.flown_design_fingerprint"]


def test_engine_sized_at_another_mass(tmp_path):
    rows = flops_rows()
    rows[11]["result"]["design_parameters"]["mission_config"]["takeoff_mass_kg"] = 88000.0
    row = extract(tmp_path, rows)
    assert row["flag.engine_stale_basis"] is True and "take-off mass flown 88000.0" in row["flag.engine_stale_basis.detail"]
    assert row["flag.cruise_point_mismatch"] is False


def test_scale_from_no_cycle_of_this_attempt(tmp_path):
    rows = flops_rows()
    for i in (9, 11):
        p = rows[i]["args"]["parameters"] if i == 9 else rows[i]["result"]["design_parameters"]["aircraft_params"]
        p["Aircraft.Engine.SCALE_FACTOR"] = 1.3
    row = extract(tmp_path, rows)
    assert row["flag.engine_stale_basis"] is True and "matches no run_cycle" in row["flag.engine_stale_basis.detail"]


def test_mass_not_from_a_converged_sizing(tmp_path):
    rows = flops_rows()
    rows[4]["result"]["mass_breakdown"]["sizing_report"]["driver"]["converged"] = False
    row = extract(tmp_path, rows)
    assert row["flag.mass_not_converged"] is True and "driver.converged" in row["flag.mass_not_converged.detail"]


def test_unmeasured_or_different_tails(tmp_path):
    rows = flops_rows()
    for i in (9, 11):
        p = rows[i]["args"]["parameters"] if i == 9 else rows[i]["result"]["design_parameters"]["aircraft_params"]
        for k in TAILS:
            p.pop(k)
    row = extract(tmp_path, rows)
    assert row["flag.unmeasured_tails"] is True and "bench tails" in row["flag.unmeasured_tails.detail"]
    rows = flops_rows()
    rows[11]["result"]["design_parameters"]["aircraft_params"]["Aircraft.VerticalTail.AREA"] = 25.0
    assert extract(tmp_path / "b", rows)["flag.unmeasured_tails"] is True


def test_new_refusal_codes(tmp_path):
    rows = flops_rows()
    rows.insert(10, _row("unknown", "run_simulation", {"session_id": "A"},
                         {"success": False, "error_code": "MISSION_INPUTS_NOT_APPLIED"}))
    row = extract(tmp_path, rows, kb_status={10: "refused"})
    assert row["flag.refusal_codes"] is True and "MISSION_INPUTS_NOT_APPLIED" in row["flag.refusal_codes.detail"]
    assert '"MISSION_INPUTS_NOT_APPLIED": 1' in row["error_codes"]


def test_unconverged_mission_and_its_results_never_count(tmp_path):
    """B314: a later unconverged mission (success false) and a get_results after it do not replace the fuel."""
    rows = flops_rows() + [
        _row("unknown", "run_simulation", {"session_id": "A"},
             {"success": False, "converged": False, "error_code": "MISSION_NOT_CONVERGED",
              "summary": {"fuel_burned_kg": 35000.0, "gtow_kg": 99000.0}}),
        _row("unknown", "get_results", {"session_id": "A"},
             {"success": True, "converged": False, "fuel_burned_kg": 35000.0})]
    row = extract(tmp_path, rows)
    assert row["fuel_kg"] == 16638.69
    assert '"MISSION_NOT_CONVERGED": 1' in row["error_codes"] and row["flag.refusal_codes"] is True


def test_intercepted_mass_reply_reads_the_frameworks_capture(tmp_path):
    """B342: the estimate_mass reply held a ref; the flown wing mass is checked against design_flown.json."""
    rows = flops_rows()
    rows[4]["result"]["mass_breakdown"] = {"_intercepted": True, "ref": "estimate_mass__mass_breakdown"}
    row = extract(tmp_path, rows)
    assert row["flag.wing_mass_stale"] is False and row["flag.mass_not_converged"] is False
    assert row["flag.unmeasured_tails"] is False
    row = extract(tmp_path / "b", copy.deepcopy(rows), design_flown={"aero": {"drag_coupling": "x"}})
    assert row["flag.wing_mass_stale"] is None and "intercepted" in row["flag.wing_mass_stale.detail"]


def test_old_contract_leaves_the_new_flags_not_applicable(tmp_path):
    from tests.test_link20_flown import base_rows, extract as extract_v1
    row = extract_v1(tmp_path, base_rows())
    assert row["contract"] == fl.CONTRACT_V1
    for f in fl.NEW_CONTRACT_FLAGS:
        assert row[f"flag.{f}"] is None and "not applicable" in row[f"flag.{f}.detail"]
