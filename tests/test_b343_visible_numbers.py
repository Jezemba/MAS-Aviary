"""B343 (fix of B342): agents see the mass numbers and the mission outcome; only bulky sub-fields go behind a ref.

Replies are shaped like the servers' (mass-mcp fix/a7-quarter-chord-sweep dca2b1e estimate_mass, aviary-mcp
fix/final-rerun run_simulation mission_constraints and get_cruise_point aero_details). No network, no servers.
"""
import json

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

SERVERS = {"estimate_mass": "mass", "run_simulation": "aviary", "get_results": "aviary",
           "get_cruise_point": "aviary", "configure_mission": "aviary", "run_su2_solver": "su2"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("AVION_CPACS_TAILS", "0")
    state = DesignState()
    dp.init_data_plane(state, SERVERS)
    monkeypatch.setattr(dp, "_registered_tools", {})
    dp._call_ctx.__dict__.clear()
    yield state
    dp._call_ctx.__dict__.clear()
    dp.init_data_plane(DesignState(), {})


def _out(tool, reply):
    out = dp.intercept_response(tool, json.dumps(reply))
    return json.loads(out) if isinstance(out, str) else out


def _sizing_report():
    """About 4.5 kB, as for the D150 (driver, engine, tails, mission flown, bench values, stations)."""
    return {"driver": {"converged": True, "model_evaluations": 11, "range_residual_nmi": 0.0},
            "engine": {"deck": "/decks/deck_7815057d6edc9732.deck", "scale": 1.24943, "note": "x" * 300},
            "tails": {t: {"area_m2": 31.1, "aspect_ratio": 4.98, "taper_ratio": 0.33, "tc_avg": 0.1, "uid": t}
                      for t in ("horizontal", "vertical")},
            "mission_flown": {"cruise_mach_mean": 0.78, "phases": [{"name": f"p{i}", "duration_min": 100.0 + i}
                                                                   for i in range(3)], "note": "y" * 600},
            "bench_values_kept": {f"aircraft:x:{i}": "1.0,unitless" for i in range(40)},
            "wing_stations": {"input_station_dist": [0.0, 0.3, 1.0], "chord_per_semispan": [0.4, 0.25, 0.1]},
            "computed_by_aviary": [f"aircraft:computed:{i}" for i in range(20)]}


def test_b343_estimate_mass_masses_stay_visible(fresh):
    breakdown = {"mTOM_kg": 78126.0, "mTOM_sized_kg": 91197.1, "sizing_converged": True, "mOEM_kg": 44557.0,
                 "mFuel_kg": 23000.0, "mPayload_kg": 21677.0, "components": {"mWing_kg": 7833.0, "mPropulsion_kg": 9731.6},
                 "oas_comparison": {"mWing_kg": 7700.0}, "sizing_report": _sizing_report()}
    assert len(json.dumps(breakdown)) > dp._DICT_BYTE_THRESHOLD           # it used to be replaced whole
    out = _out("estimate_mass", {"status": "success", "mass_breakdown": breakdown})
    mb = out["mass_breakdown"]
    assert mb["mTOM_sized_kg"] == 91197.1 and mb["components"]["mWing_kg"] == 7833.0
    assert mb["mOEM_kg"] == 44557.0 and mb["mFuel_kg"] == 23000.0 and mb["sizing_converged"] is True
    assert mb["sizing_report"]["_intercepted"] is True
    assert fresh.data_store[mb["sizing_report"]["ref"]] == breakdown["sizing_report"]
    assert len(json.dumps(out)) < 2500                                    # the bulk is not in the context
    assert fresh.data_store["mass_mtom_kg"] == 91197.1                     # the capture still reads the full reply


def test_b343_mission_outcome_stays_visible_and_the_detail_is_stored(fresh):
    constraints = {f"traj.phase{i}.collocation_constraint.defects:state{j}": {
        "violation": 0.0, "worst_value": 1e-9, "equals": 0.0, "points": 20, "points_violated": 0, "units": "m"}
        for i in range(3) for j in range(4)}
    constraints["mission:constraints:mass_residual"] = {"violation": 725.0, "worst_value": 725.0, "equals": 0.0,
                                                        "points": 1, "points_violated": 1, "units": "lbm"}
    reply = {"success": False, "error_code": "MISSION_NOT_CONVERGED", "error": "no valid result",
             "mission_residuals": {"range_residual": 0.0, "mass_residual": 725.0}, "mission_constraints": constraints,
             "unconverged_last_iterate": {"fuel_burned_kg": 35735.0}}
    assert len(json.dumps(constraints)) > dp._DICT_BYTE_THRESHOLD
    out = _out("run_simulation", reply)
    mc = out["mission_constraints"]
    assert mc["constraints"] == 13 and mc["violated"] == {"mission:constraints:mass_residual":
                                                              {"violation": 725.0, "units": "lbm"}}
    assert fresh.data_store[mc["ref"]] == constraints
    assert out["mission_residuals"] == {"range_residual": 0.0, "mass_residual": 725.0}
    assert out["error"] == "no valid result" and out["unconverged_last_iterate"]["fuel_burned_kg"] == 35735.0


def test_b343_small_constraint_sets_are_untouched(fresh):
    small = {"mission:constraints:range_residual": {"violation": 0.0, "points": 1, "units": "NM"}}
    out = _out("run_simulation", {"success": True, "mission_constraints": small, "summary": {"fuel_burned_kg": 1.5e4}})
    assert out["mission_constraints"] == small


def test_b343_cruise_point_aero_details_stay_visible(fresh):
    details = {"drag_n": 41000.0, "dynamic_pressure_pa": 11200.0, "reference_area_m2": 122.8,
               "design_lift_coefficient": 0.5674, "design_mach": 0.79, "subsonic_drag_coeff_factor": 1.0,
               "fingerprint_covers": sorted(f"aircraft:some_group:input_name_{i}" for i in range(54))}
    assert len(json.dumps(details)) > dp._DICT_BYTE_THRESHOLD
    out = _out("get_cruise_point", {"success": True, "aero_details": details, "cruise_point": {"lift_to_drag": 17.0}})
    ad = out["aero_details"]
    assert ad["drag_n"] == 41000.0 and ad["design_lift_coefficient"] == 0.5674
    assert ad["fingerprint_covers"]["_intercepted"] is True and ad["fingerprint_covers"]["total_count"] == 54
    assert out["cruise_point"]["lift_to_drag"] == 17.0


def test_b343_other_grown_replies_stay_inline(fresh):
    payload = {"num_passengers": 239, "mass_per_passenger_kg": 74.8, "baggage_per_passenger_kg": 20.4,
               "per_passenger_with_baggage_kg": 95.3, "passengers_kg": 17877.2, "baggage_kg": 4875.6,
               "cargo_kg": 0.0, "total_payload_kg": 22752.8, "source": "Aviary FLOPS CargoMass"}
    out = _out("configure_mission", {"success": True, "mission_summary": {"payload_kg": 22752.8, "payload": payload}})
    assert out["mission_summary"]["payload"] == payload
    conv = {"converged": True, "basis": "SU2 exit message", "final_residual": -6.2, "target_residual": -6.0,
            "iterations": 312, "max_iterations": 500, "trimmed": True,
            "trim": {"fixed_cl_mode": True, "target_cl": 0.56, "achieved_cl": 0.5601, "relative_error": 0.0002,
                     "tolerance": 0.01, "on_target": True, "su2_stop_tolerance_abs": 0.00504},
            "summary": "Converged. Trimmed: CL 0.5601 is on the target 0.56 (+0.02%)."}
    out = _out("run_su2_solver", {"success": True, "convergence": conv,
                                  "final_coefficients": {"CL": 0.5601, "CD": 0.021}})
    assert out["convergence"] == conv and out["final_coefficients"]["CL"] == 0.5601
