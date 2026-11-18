"""B281 (and the B-numbers of BUGS.md 2026-11-18): the framework fixes before the final 20-iteration re-run.

A8 trim mass, F4/F14 mass results, F9 graph cache, F1/F2 no clamps, B275 refusal, A9 measured AR, A10/F11 nothing
stale flies, A11 zero fuel, F3 converged missions only, F7 aero guard, F8 seeded fingerprints, F10 SU2 settings,
F12 fixed cycle inputs, F13 prompt defaults, F15 SU2 capture, F16 coupled arguments, F18 freestream, B273 deck.
No network, no API, no MCP servers: every tool is a local stub.
"""
import json
import math
import re
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import aero_guard, coupling

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for var in ("AVION_REQUIRE_PHYSICAL_AERO", "AVION_ENGINE_DECK", "AVION_REQUIRE_COUPLED_INPUTS"):
        monkeypatch.delenv(var, raising=False)
    state = DesignState()
    monkeypatch.setattr(dp, "_design_state", state)
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    dp._PLANFORM_CACHE.clear()
    for name in ("su2_session", "su2_config_override", "morph", "wing_uid", "engine_pending", "refusal"):
        if hasattr(dp._call_ctx, name):
            delattr(dp._call_ctx, name)
    yield state


def _carry(monkeypatch, previous):
    from src.coordination import prompt_assembly

    monkeypatch.setattr(prompt_assembly, "read_carry_over",
                        lambda: None if previous is None else {"previous_design": previous})


def _canonical(monkeypatch, **baseline):
    from src.config import canonical

    real = canonical.load_canonical()
    monkeypatch.setattr(canonical, "load_canonical", lambda path=None: {**real, "baseline": baseline})


# ---- A8: the trim mass -----------------------------------------------------------------------------------

def test_trim_mass_is_the_carried_sized_mass(monkeypatch):
    _carry(monkeypatch, {"structures": {"mtom_kg": 89455.7, "sizing_converged": True}})
    assert dp.trim_mass_basis() == (89455.7, "mass-mcp sized mass carried from the previous iteration")


def test_an_unconverged_carried_mass_is_not_a_trim_mass(monkeypatch):
    _carry(monkeypatch, {"structures": {"mtom_kg": 78126.0, "sizing_converged": False}})
    mass, why = dp.trim_mass_basis()
    assert mass is None and "no converged" in why


def test_iteration_1_uses_the_canonical_baseline_sized_mass(monkeypatch):
    _carry(monkeypatch, None)
    _canonical(monkeypatch, sized_mtom_kg=91000.0)
    assert dp.trim_mass_basis()[0] == 91000.0
    _canonical(monkeypatch, sized_mtom_kg=None)
    mass, why = dp.trim_mass_basis()
    assert mass is None and "not set" in why


def test_the_shipped_canonical_has_no_invented_baseline_mass():
    from src.config.canonical import load_canonical

    assert (load_canonical().get("baseline") or {}).get("sized_mtom_kg") is None    # to be set from mass-mcp (A15)


def test_the_trim_ignores_this_iterations_estimate_and_the_cpacs_mass(fresh, monkeypatch, tmp_path):
    _carry(monkeypatch, {"structures": {"mtom_kg": 90000.0, "sizing_converged": True}})
    fresh.data_store["mass_mtom_kg"] = 95000.0                    # this iteration's estimate
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.83, "Aircraft.Wing.ASPECT_RATIO": 9.36}
    resolved = dp.resolve_request("configure_from_cpacs", {"session_id": "s"})
    assert resolved["target_cl"] == pytest.approx(coupling.cruise_lift_coefficient(90000.0, 0.78, 33000, 122.83))


# ---- F4 / F14: a mass estimate is a result only when it converged and is in range ---------------------------

def _estimate(converged=True, mtom=91000.0, wing=9000.0):
    return {"success": True, "mass_breakdown": {
        "components": {"mWing_kg": wing, "mWing_source": "flops"}, "mTOM_kg": 78126.0,
        "mTOM_sized_kg": mtom, "sizing_converged": converged}}


def test_a_converged_estimate_is_captured(fresh):
    data = _estimate()
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", data)
    assert fresh.data_store["mass_mtom_kg"] == 91000.0 and fresh.data_store["mass_wing_kg"] == 9000.0
    assert coupling.get_var(fresh, "mass.mtom_kg") == 91000.0
    assert data["success"] is True


@pytest.mark.parametrize("kwargs,expect", [
    ({"converged": False}, "did not converge"),
    ({"mtom": 250000.0}, "outside [20,000, 200,000]"),
    ({"wing": 50.0}, "outside [1,000, 30,000]"),
])
def test_an_unusable_estimate_is_refused_and_withdraws_the_old_one(fresh, kwargs, expect):
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", _estimate())
    data = _estimate(**kwargs)
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", data)
    assert data["success"] is False and data["error_code"] == "MASS_ESTIMATE_NOT_USABLE" and expect in data["error"]
    assert "mass_mtom_kg" not in fresh.data_store and coupling.get_var(fresh, "mass.mtom_kg") is None
    assert "mass_wing_kg" not in fresh.data_store and coupling.get_var(fresh, "mass.wing_kg") is None
    assert "78126" not in str(fresh.data_store.get("mass_mtom_kg"))
    refusal = dp.mission_input_refusal("run_simulation", {"session_id": "av"})
    assert refusal["error_code"] == "MISSION_INPUTS_NOT_APPLIED" and expect in refusal["error"]


# ---- F9: graph baseline cache ----------------------------------------------------------------------------

def test_the_graph_cache_is_versioned_and_keyed_on_the_trim_mass(monkeypatch, tmp_path):
    from src.coordination import graph_preinit

    assert graph_preinit.BASELINE_VERSION != "b254"
    cpacs = tmp_path / "c.xml"
    cpacs.write_text("<cpacs/>")
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (91000.0, "test"))
    name = graph_preinit._cache_path(str(cpacs)).name
    assert name.endswith(f".{graph_preinit.BASELINE_VERSION}.m91000.0.design.json")
    assert ".b254." not in name


def test_an_unusable_baseline_is_not_cached(fresh, monkeypatch, tmp_path):
    from src.coordination import graph_preinit

    monkeypatch.setattr(graph_preinit, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(graph_preinit, "_call", lambda name, **kw: {})
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (91000.0, "test"))

    def compute(cpacs, mission):     # a solve that did not converge
        fresh.data_store.update({"aero_cl_cruise": 0.56, "aero_cd_cruise": 0.03, "aero_solve_trimmed": True,
                                 "aero_solve_converged": False, "mass_sizing_converged": True})

    monkeypatch.setattr(graph_preinit, "_compute_baseline", compute)
    cpacs = tmp_path / "c.xml"
    cpacs.write_text("<cpacs/>")
    report = graph_preinit.prepare({"starting_design": {"CPACS_FILE": str(cpacs)}})
    assert "did not converge" in report["cache_skipped"]
    assert not (tmp_path / "cache").exists()


def test_the_engine_key_includes_the_friction_chord(fresh, tmp_path):
    fresh.data_store["mass_mtom_kg"] = 90000.0
    a = dp._cycle_sizing_key()
    coupling.put_var(fresh, "geom.mac_length", 4.19, source_tool="get_wing_summary")
    b = dp._cycle_sizing_key()
    assert a[:3] == b[:3] and a[3] is None and b[3] == pytest.approx(4.19)


# ---- F1 / F2: no clamps; only the impossible fails ------------------------------------------------------

def test_the_drag_factor_is_not_clamped():
    f = coupling.aero_cd_to_aviary_drag_factor(0.15, 0.95, 9.0)
    assert f > 2.0
    with pytest.raises(ValueError):
        coupling.aero_cd_to_aviary_drag_factor(-0.5, 0.5, 9.0)
    with pytest.raises(ValueError):
        coupling.aero_cd_to_aviary_drag_factor(float("nan"), 0.5, 9.0)


def test_the_design_thrust_is_not_clamped():
    assert coupling.fn_des_from_drag(150000.0, 0.99, 0.2) > 25000.0
    assert coupling.mtom_to_pycycle_fn_des(10000.0) < 2000.0
    for args in ((90000.0, 0.5, 0.0), (90000.0, 0.0, 0.03), (0.0, 0.5, 0.03)):
        with pytest.raises(ValueError):
            coupling.fn_des_from_drag(*args)


def test_an_impossible_design_thrust_refuses_set_inputs(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_tool_server_map", {"set_inputs": "pycycle"})
    fresh.data_store["mass_mtom_kg"] = 90000.0
    coupling.put_var(fresh, "aero.cl_cruise", 0.5, source_tool="run_su2_solver")
    coupling.put_var(fresh, "aero.cd_cruise", -0.5, source_tool="run_su2_solver")
    dp.take_refusal()
    dp.resolve_request("set_inputs", {"session_id": "pc", "values": {}})
    refusal = dp.take_refusal()
    assert refusal["error_code"] == "DESIGN_THRUST_NOT_PHYSICAL" and "Check those inputs" in refusal["error"]


def test_an_unclamped_factor_is_flown_and_an_impossible_one_refuses_the_mission(fresh):
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 75.0, "Aircraft.Wing.ASPECT_RATIO": 12.0}
    coupling.put_var(fresh, "aero.cl_cruise", 0.91, source_tool="run_su2_solver")
    coupling.put_var(fresh, "aero.cd_cruise", 0.1266, source_tool="run_su2_solver")
    resolved = {"parameters": {}}
    dp._inject_phase_h_aero(resolved)
    assert resolved["parameters"]["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] > 2.0     # S_GR 5: flew 2.0
    coupling.put_var(fresh, "aero.cd_cruise", -0.5, source_tool="run_su2_solver")
    resolved = {"parameters": {}}
    dp._inject_phase_h_aero(resolved)
    assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in resolved["parameters"]
    assert "not physical" in dp.mission_input_refusal("run_simulation", {"session_id": "av"})["error"]


# ---- A9: the measured AR ----------------------------------------------------------------------------------

def test_the_drag_factor_uses_the_measured_ar_not_the_typed_one(fresh):
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.79, "Aircraft.Wing.ASPECT_RATIO": 10.0}
    coupling.put_var(fresh, "aero.cl_cruise", 0.4627, source_tool="run_su2_solver")
    coupling.put_var(fresh, "aero.cd_cruise", 0.0044, source_tool="run_su2_solver")
    resolved = {"parameters": {"Aircraft.Wing.ASPECT_RATIO": 7.0}}
    dp._inject_phase_h_aero(resolved)
    reynolds, swet = dp._friction_inputs()
    want = coupling.aero_cd_to_aviary_drag_factor(0.0044, 0.4627, 10.0, reynolds=reynolds, swet_sref=swet)
    assert resolved["parameters"]["Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR"] == pytest.approx(want)
    assert fresh.data_store["aero_drag_factor_ar"] == 10.0


def test_no_measured_ar_means_no_factor_and_no_mission(fresh):
    coupling.put_var(fresh, "aero.cl_cruise", 0.5, source_tool="run_su2_solver")
    coupling.put_var(fresh, "aero.cd_cruise", 0.03, source_tool="run_su2_solver")
    resolved = {"parameters": {"Aircraft.Wing.ASPECT_RATIO": 9.0}}
    dp._inject_phase_h_aero(resolved)
    assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in resolved["parameters"]
    assert "aspect ratio" in dp.mission_input_refusal("run_simulation", {})["error"]


# ---- A10 / F11: a refused set leaves nothing stale; failed pre-mission steps refuse the mission ------------

def _engine_current(fresh, monkeypatch):
    monkeypatch.setattr(dp, "engine_is_current", lambda: True)
    coupling.put_var(fresh, "prop.fn_lbf", 17113.2, source_tool="run_cycle")
    fresh.sessions["aviary"] = "av"


def test_n_gr_19_a_refused_set_does_not_record_the_engine_scale(fresh, monkeypatch):
    _engine_current(fresh, monkeypatch)
    resolved = {"session_id": "av", "parameters": {}}
    dp._inject_engine_scale(resolved)
    dp._capture_applied_design("set_aircraft_parameters",
                               {"success": False, "error_code": "READ_ONLY_PARAMETER", "error": "Wing.SPAN"})
    assert "av" not in (fresh.data_store.get("engine_injected_key") or {})
    assert "engine scale" in dp.mission_input_refusal("run_simulation", {"session_id": "av"})["error"]
    dp._inject_engine_scale(resolved)
    dp._capture_applied_design("set_aircraft_parameters", {"success": True, "applied": []})
    assert dp.mission_input_refusal("run_simulation", {"session_id": "av"}) is None


def test_a_take_off_mass_error_refuses_the_mission(fresh):
    fresh.data_store["takeoff_mass_error"] = "configure_mission: INVALID"
    assert "take-off mass" in dp.mission_input_refusal("run_simulation", {})["error"]


def test_results_not_applied_refuse_the_mission(fresh):
    fresh.data_store["_coupled_version"] = 3
    fresh.data_store["_coupled_version_applied"] = 2
    assert "not applied" in dp.mission_input_refusal("run_simulation", {})["error"]
    fresh.data_store["_coupled_version_applied"] = 3
    assert dp.mission_input_refusal("run_simulation", {}) is None


# ---- A11 / F3: only converged missions with fuel are results ---------------------------------------------

def test_a11_a_zero_fuel_success_is_not_a_success(fresh):
    data = {"success": True, "summary": {"fuel_burned_kg": 8.2e-06}}
    dp._flag_zero_fuel("run_simulation", data)
    assert data["success"] is False and data["error_code"] == "ZERO_FUEL" and data["flown"] is False


def test_f3_an_unconverged_mission_is_not_a_result(fresh):
    data = {"success": True, "converged": False, "summary": {"fuel_burned_kg": 35734.6},
            "failure": {"optimizer_message": "Iteration limit reached",
                        "violated_constraints": [{"constraint": "range_residual", "violation": 36704.0}]}}
    out = json.loads(dp.intercept_response("run_simulation", json.dumps(data)))
    assert out["success"] is False and out["flown"] is False and out["error_code"] == "MISSION_NOT_CONVERGED"
    assert "Iteration limit reached" in out["error"] and "range_residual" in out["error"]
    assert "flown_cpacs_path" not in fresh.data_store


def test_f3_aviary_mcps_own_refusal_keeps_its_code(fresh):
    data = {"success": False, "converged": False, "error_code": "MISSION_NOT_CONVERGED", "error": "aviary says",
            "valid_result": False, "unconverged_last_iterate": {"fuel_burned_kg": 35734.6}}
    out = json.loads(dp.intercept_response("run_simulation", json.dumps(data)))
    assert out["error_code"] == "MISSION_NOT_CONVERGED" and out["error"] == "aviary says"


def test_f3_the_runner_counts_converged_missions_only(tmp_path):
    from scripts.stat_batch_runner import measured_fuel_from_kb

    rows = [
        {"tool": "run_simulation", "status": "success", "outputs": {"summary": {"fuel_burned_kg": 21000.0}}},
        {"tool": "get_results", "status": "success", "outputs": {"summary": {"fuel_burned_kg": 21000.0}}},
        {"tool": "run_simulation", "status": "success",
         "outputs": {"converged": False, "summary": {"fuel_burned_kg": 14000.0}}},
        {"tool": "get_results", "status": "success", "outputs": {"summary": {"fuel_burned_kg": 14000.0}}},
        {"tool": "run_simulation", "status": "failed",
         "outputs": {"success": False, "error_code": "MISSION_NOT_CONVERGED"}},
    ]
    kb = tmp_path / "knowledge_base.jsonl"
    kb.write_text("\n".join(json.dumps(r) for r in rows))
    assert measured_fuel_from_kb(kb) == 21000.0


def test_f3_the_graph_router_does_not_call_an_unconverged_mission_flown():
    from src.coordination.graph_router import _flown

    assert _flown({"status": "success", "outputs": {"summary": {"fuel_burned_kg": 21000.0}}}) == 21000.0
    assert _flown({"status": "success", "outputs": {"converged": False,
                                                    "summary": {"fuel_burned_kg": 21000.0}}}) is None
    assert _flown({"status": "failed", "outputs": {"success": False, "valid_result": False,
                                                   "unconverged_last_iterate": {"fuel_burned_kg": 1.0}}}) is None


# ---- F7: the aero guard -----------------------------------------------------------------------------------

def _aero(fresh, cl=0.5590, cd=0.0250, **record):
    coupling.put_var(fresh, "aero.cl_cruise", cl, source_tool="run_su2_solver")
    coupling.put_var(fresh, "aero.cd_cruise", cd, source_tool="run_su2_solver")
    fresh.data_store.update({"aero_solve_trimmed": True, "aero_solve_target_cl": 0.5592,
                             "aero_solve_converged": True, **record})


def test_f7_a_trimmed_converged_solve_flies(fresh):
    _aero(fresh)
    assert aero_guard.aero_nonphysical("run_simulation", {}) is None


@pytest.mark.parametrize("record,expect", [
    ({"aero_solve_trimmed": None}, "not known that the SU2 solve was trimmed"),
    ({"aero_solve_target_cl": 0.70}, "is not the lift target"),
    ({"aero_solve_converged": False, "aero_solve_residual": {"rms_rho": -4.23, "target": -6.0}},
     "rms[Rho] -4.23 against -6.0"),
    ({"aero_solve_converged": None}, "did not reach its residual target"),
])
def test_f7_unknown_trim_off_target_and_unconverged_are_refused(fresh, record, expect):
    _aero(fresh, **record)
    assert expect in aero_guard.aero_nonphysical("run_simulation", {})["error"]


def test_f7_the_tolerance_is_su2_mcps_one_percent(fresh):
    _aero(fresh, cl=0.5592 * 1.009)
    assert aero_guard.aero_nonphysical("run_simulation", {}) is None
    _aero(fresh, cl=0.5592 * 1.011)
    assert aero_guard.aero_nonphysical("run_simulation", {}) is not None


def test_f7_convergence_from_the_history_when_the_tool_does_not_say(fresh):
    hist = [{'    "rms[Rho]"    ': -3.0}, {'    "rms[Rho]"    ': -6.02}]
    dp._note_solve_convergence(None, hist)
    assert fresh.data_store["aero_solve_converged"] is True
    dp._note_solve_convergence(None, [{'"rms[Rho]"': -4.23}])          # S_GR 12
    assert fresh.data_store["aero_solve_converged"] is False
    dp._note_solve_convergence(True, [{'"rms[Rho]"': -4.23}])          # su2-mcp's verdict wins
    assert fresh.data_store["aero_solve_converged"] is True
    dp._note_solve_convergence(None, None)
    assert fresh.data_store["aero_solve_converged"] is None


# ---- F15: SU2 capture -------------------------------------------------------------------------------------

def test_f15_an_unconverged_su2_reply_is_not_captured(fresh):
    dp.intercept_response("run_su2_solver", json.dumps({
        "success": False, "converged": False, "last_iteration_coefficients": {"CL": 0.989, "CD": 0.182}}))
    assert coupling.get_var(fresh, "aero.cl_cruise") is None


def test_f15_no_latest_folder_fallback(fresh, tmp_path):
    (tmp_path / "history.csv").write_text('"CL","CD","rms[Rho]"\n0.6,0.03,-7\n')
    fresh.data_store["_su2_workdir_latest"] = str(tmp_path)          # another session's folder
    dp.intercept_response("run_su2_solver", json.dumps({"success": True, "session_id": "mine"}))
    assert coupling.get_var(fresh, "aero.cl_cruise") is None


def test_f15_a_history_read_of_an_unconverged_solve_is_not_captured(fresh):
    dp.intercept_response("read_history_csv", json.dumps({
        "rows": [{"CL": 0.6, "CD": 0.03}], "convergence": {"converged": False, "trimmed": True}}))
    assert coupling.get_var(fresh, "aero.cl_cruise") is None


# ---- F8: seeded results carry the design's fingerprint ------------------------------------------------------

def test_f8_seeded_results_are_stamped_and_unconverged_mass_is_not_seeded(fresh, monkeypatch):
    from src.coordination import graph_preinit
    from src.tools import coupling_contract

    monkeypatch.setattr(coupling_contract, "current_design_fingerprint", lambda: "fp-carried")
    seeded = graph_preinit.seed_from_design({
        "aero": {"cl_cruise": 0.56, "cd_cruise": 0.025, "solve_trimmed": True, "solve_converged": True},
        "structures": {"mtom_kg": 78126.0, "wing_mass_kg": 8735.3, "sizing_converged": False}})
    assert "aero" in seeded and "structures" not in seeded
    assert fresh.data_store["_coupled_fp_su2"] == "fp-carried"
    assert "_coupled_fp_mass" not in fresh.data_store and coupling.get_var(fresh, "mass.mtom_kg") is None
    assert "sizing_converged False" in fresh.data_store["mass_seed_skipped"]
    monkeypatch.setattr(coupling_contract, "current_design_fingerprint", lambda: "fp-after-morph")
    assert coupling_contract._captured_fingerprint("su2") != coupling_contract.current_design_fingerprint()


# ---- F10: the framework's SU2 settings in create / configure ----------------------------------------------

def test_f10_protected_settings_and_su2_arguments_in_overrides_are_kept_back(fresh, monkeypatch):
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (90000.0, "test"))
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.83, "Aircraft.Wing.ASPECT_RATIO": 9.36}
    resolved = dp.resolve_request("create_su2_session", {
        "cpacs_file_path": "x.xml", "cruise_mach": 0.8, "oswald_efficiency": 0.75, "ref_area": 100.0,
        "overrides": {"ITER": 50, "conv_residual_minval": -3, "FREESTREAM_PRESSURE": 24800, "ASPECT_RATIO": 10.0,
                      "OSWALD_EFFICIENCY": 0.75, "CFL_NUMBER": 5}})
    ov = resolved["overrides"]
    assert ov["ITER"] == 500 and ov["CONV_RESIDUAL_MINVAL"] == -6       # the canonical values
    assert "conv_residual_minval" not in ov and "FREESTREAM_PRESSURE" not in ov
    assert "ASPECT_RATIO" not in ov and "OSWALD_EFFICIENCY" not in ov
    assert ov["CFL_NUMBER"] == 5                                         # not protected: the caller's
    assert resolved["cruise_mach"] == 0.78 and "oswald_efficiency" not in resolved
    assert resolved["ref_area"] == pytest.approx(122.83) and resolved["aspect_ratio"] == pytest.approx(9.36)
    out = json.loads(dp.intercept_response("create_su2_session", json.dumps({"session_id": "s"})))
    kept = out["framework_kept"]["ignored"]
    for key in ("ITER", "conv_residual_minval", "FREESTREAM_PRESSURE", "ASPECT_RATIO", "OSWALD_EFFICIENCY",
                "oswald_efficiency", "cruise_mach", "ref_area"):
        assert key in kept


def test_f10_no_canonical_settings_refuses(fresh, monkeypatch):
    from src.config import canonical

    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (90000.0, "test"))
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.83, "Aircraft.Wing.ASPECT_RATIO": 9.36}

    def broken():
        raise OSError("no file")

    monkeypatch.setattr(canonical, "render_snippets", broken)
    dp.take_refusal()
    dp.resolve_request("configure_from_cpacs", {"session_id": "s"})
    assert dp.take_refusal()["error_code"] == "CANONICAL_SU2_SETTINGS_UNAVAILABLE"


def test_f18_no_mislabelled_freestream_in_the_canonical_settings():
    from src.config.canonical import load_canonical

    su2 = load_canonical()["su2_config"]
    assert "FREESTREAM_PRESSURE" not in su2 and "FREESTREAM_TEMPERATURE" not in su2


# ---- F12: fixed cycle inputs pinned, T4 take-off / climb carried -------------------------------------------

_FIXED_ROW = re.compile(r"\| Set \(fixed\) \| pycycle: `([\w.]+)` \| pycycle \| ([0-9.]+) \| fixed \|")


@pytest.mark.parametrize("source", ["sequential.md", "orchestrated.md", "networked.md",
                                    "config/mdo_f25_sequential_agents.yaml"])
def test_f12_the_pinned_values_are_the_prompt_tables(source):
    from src.config.canonical import load_canonical

    fixed = load_canonical()["propulsion"]["fixed_inputs"]
    rows = _FIXED_ROW.findall((REPO / source).read_text(encoding="utf-8"))
    assert rows
    for key, value in rows:
        assert fixed[key] == float(value), (source, key)


def test_f12_set_inputs_carries_the_fixed_values(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_tool_server_map", {"set_inputs": "pycycle"})
    resolved = dp.resolve_request("set_inputs", {"session_id": "pc", "values": {"lpc.eff": 0.92, "splitter.BPR": 6.0}})
    assert resolved["values"]["lpc.eff"] == 0.9243 and resolved["values"]["fan.eff"] == 0.8948
    assert resolved["values"]["splitter.BPR"] == 6.0
    out = json.loads(dp.intercept_response("set_inputs", json.dumps({"success": True})))
    assert out["framework_applied"]["overridden"]["lpc.eff"] == {"asked": 0.92, "used": 0.9243}


def test_f12_t4_take_off_and_climb_are_carried(fresh, monkeypatch):
    from src.coordination import prompt_assembly

    monkeypatch.setattr(dp, "_tool_server_map", {"set_inputs": "pycycle"})
    monkeypatch.setattr(prompt_assembly, "read_carry_over",
                        lambda: {"starting_design": {"engine": {"T4_TAKEOFF": 3600.0, "T4_CLIMB": 3100.0}}})
    resolved = dp.resolve_request("set_inputs", {"session_id": "pc", "values": {}})
    assert resolved["values"]["T4_TAKEOFF"] == 3600.0 and resolved["values"]["T4_CLIMB"] == 3100.0


# ---- F13: the prompt defaults are the measured baseline ---------------------------------------------------

_MEASURED = {  # TiGL on D150_simple.xml (ws8 tigl-env, tigl-mcp _measure / _fuselage_dims), 4 decimals
    ("Wing1", "area"): "122.8287", ("Wing1", "aspect_ratio"): "9.3632",
    ("Wing2H", "area"): "31.1184", ("Wing2H", "aspect_ratio"): "4.9810",
    ("Wing3V", "area"): "21.5", ("Wing3V", "aspect_ratio"): "1.6069",
    ("Fuselage1", "length"): "37.6843", ("Fuselage1", "diameter"): "4.1490",
}
_SET_ROW = re.compile(r"\| Set \| tigl: `set_high_level_parameters`, [^|]*`(\w+)`, key `(\w+)` \| [^|]* \| ([^|]+) \|")


@pytest.mark.parametrize("source", ["sequential.md", "orchestrated.md", "networked.md",
                                    "config/mdo_f25_sequential_agents.yaml"])
def test_f13_set_defaults_are_measured_and_derived_rows_are_not_stale(source):
    text = (REPO / source).read_text(encoding="utf-8")
    for uid, key, default in _SET_ROW.findall(text):
        if (uid, key) in _MEASURED:
            assert default.strip() == _MEASURED[(uid, key)], (source, uid, key)
    assert "| 6336 |" not in text and "| 78,126 |" not in text
    assert "| pycycle | from mass and su2 | set by framework | lbf |" in text
    assert "| pycycle, su2 | from mass | set by framework | kg |" in text


# ---- F16: a coupled argument a server does not take refuses the call ---------------------------------------

def test_f16_an_undeclared_coupled_argument_refuses(fresh, monkeypatch):
    from src.tools.type_coercion import wrap_tool_with_middleware

    monkeypatch.setattr(dp, "_tool_server_map", {"configure_mission": "aviary"})
    fresh.data_store["mass_mtom_kg"] = 90000.0
    coupling.put_var(fresh, "mass.mtom_kg", 90000.0, source_tool="estimate_mass")

    class OldConfigureMission:
        name = "configure_mission"
        description = ""
        inputs = {"session_id": {"type": "string", "description": "", "nullable": True}}   # no takeoff_mass_kg
        output_type = "string"
        calls = 0

        def forward(self, **kw):
            OldConfigureMission.calls += 1
            return json.dumps({"success": True})

    tool = wrap_tool_with_middleware(OldConfigureMission())
    out = json.loads(tool.forward(session_id="av"))
    assert out["error_code"] == "SERVER_LACKS_COUPLED_ARGUMENT"
    assert OldConfigureMission.calls == 0
    assert "takeoff_mass_kg" in fresh.data_store["compat_dropped_arguments"]["configure_mission"]


# ---- B273: the deck is built before the mission, outside its budget -----------------------------------------

def test_b273_the_deck_is_built_before_run_simulation_under_its_own_budget(fresh, monkeypatch):
    from src.tools import call_watchdog
    from src.tools.type_coercion import wrap_tool_with_middleware

    monkeypatch.setattr(dp, "_tool_server_map", {"run_simulation": "aviary"})
    monkeypatch.setenv("AVION_REQUIRE_COUPLED_INPUTS", "0")
    monkeypatch.setenv("AVION_REQUIRE_PHYSICAL_AERO", "0")
    order, budgets = [], []
    real_bounded = call_watchdog.bounded

    def spy(tool_name, fn, *a, **k):
        budgets.append(tool_name)
        return real_bounded(tool_name, fn, *a, **k)

    monkeypatch.setattr(call_watchdog, "bounded", spy)
    monkeypatch.setattr(dp, "prebuild_engine_deck", lambda tool_name, kw: order.append(("deck", tool_name)))

    class Sim:
        name = "run_simulation"
        description = ""
        inputs = {"session_id": {"type": "string", "description": "", "nullable": True}}
        output_type = "string"

        def forward(self, **kw):
            order.append(("sim", None))
            return json.dumps({"success": False, "error_code": "X", "error": "stub"})

    wrap_tool_with_middleware(Sim()).forward(session_id="av")
    assert order[0] == ("deck", "run_simulation")
    assert budgets[:2] == ["generate_engine_deck", "run_simulation"]


def test_b273_a_deck_that_is_not_ready_refuses_the_mission(fresh, monkeypatch):
    from src.tools import call_watchdog
    from src.tools.type_coercion import wrap_tool_with_middleware

    monkeypatch.setattr(dp, "_tool_server_map", {"run_simulation": "aviary"})

    def slow(tool_name, kw):
        raise call_watchdog.ToolCallTimeout("generate_engine_deck did not finish within 7500s")

    monkeypatch.setattr(dp, "prebuild_engine_deck", slow)

    class Sim:
        name = "run_simulation"
        description = ""
        inputs = {"session_id": {"type": "string", "description": "", "nullable": True}}
        output_type = "string"
        calls = 0

        def forward(self, **kw):
            Sim.calls += 1
            return "{}"

    out = json.loads(wrap_tool_with_middleware(Sim()).forward(session_id="av"))
    assert out["error_code"] == "ENGINE_DECK_NOT_READY" and Sim.calls == 0
