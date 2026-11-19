"""B306-B325: the framework fixes before the final 20-iteration re-run (code comments tag them all B306).

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
    # G1 (measured CPACS tails) is tested in tests/test_g1_cpacs_tails_and_cycle_keys.py; these tests use no tails.
    monkeypatch.setenv("AVION_CPACS_TAILS", "0")
    monkeypatch.setenv("AVION_CPACS_WING_TC", "0")      # J2 (measured wing t/c): tests/test_j2_wing_tc_framework.py
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


def test_the_shipped_canonical_baseline_is_the_measured_fixed_point():
    """The shipped baseline is mass-mcp's converged sizing of the unchanged D150 with its own engine (recomputed
    2026-11-18 on the final code, 6 explicit mass <-> engine iterations, ws8
    /data/Jessica/final_fix/baseline2/results/iterate_b360): all three values set together, the scale = Fn_DES / the deck's 5,900 lbf reference with Fn_DES from that mass."""
    from src.config.canonical import load_canonical

    base = load_canonical().get("baseline") or {}
    # baseline3 (ws8 /data/Jessica/final_fix/baseline3/results/iterate_exact2): the fixed point closed exactly --
    # mass-mcp's estimate with THIS scale is this mass (last change 1.8e-5 kg), and Fn_DES at this mass from aviary's
    # FLOPS cruise L/D 17.64234806 gives this scale (last change 2.6e-10).
    assert base.get("sized_mtom_kg") == 90298.63367700706
    assert str(base.get("engine_deck_file")).endswith("deck_7815057d6edc9732.deck")
    assert base.get("engine_scale_factor") == 1.1957958109698965
    assert coupling.fn_des_from_lift_to_drag(90298.63367700706, 17.64234805670212) / 5900.0 == pytest.approx(
        base["engine_scale_factor"], rel=1e-9)


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




# ---- F1 / F2: no clamps; only the impossible fails ------------------------------------------------------









# ---- A9: the measured AR ----------------------------------------------------------------------------------





# ---- A10 / F11: a refused set leaves nothing stale; failed pre-mission steps refuse the mission ------------

def _engine_current(fresh, monkeypatch):
    monkeypatch.setattr(dp, "engine_is_current", lambda: True)
    monkeypatch.setenv("AVION_ENGINE_DECK", "0")     # V10: with the deck on and none attached no scale is set (bench here)
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
    ("Fuselage1", "length"): "37.6446", ("Fuselage1", "diameter"): "4.1437",   # V3: tigl-mcp 043ba39 (B348 tight box)
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
    assert "| pycycle | from mass and aviary | set by framework | lbf |" in text
    assert "| pycycle, su2 | from mass | set by framework | kg |" in text


# ---- F16: a coupled argument a server does not take refuses the call ---------------------------------------

def test_f16_an_undeclared_coupled_argument_refuses(fresh, monkeypatch):
    from src.tools.type_coercion import wrap_tool_with_middleware

    monkeypatch.setattr(dp, "_tool_server_map", {"configure_mission": "aviary"})
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (90000.0, "test"))
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


def test_f3_the_batch_runner_reports_the_last_converged_mission(monkeypatch, tmp_path):
    from src.runners import batch_runner
    from src.tools import knowledge_base as kbm

    rows = [
        {"tool": "run_simulation", "status": "success", "outputs": {"summary": {"fuel_burned_kg": 21000.0}}},
        {"tool": "get_results", "status": "success", "outputs": {"summary": {"gtow_kg": 99000.0}}},
        {"tool": "run_simulation", "status": "success",
         "outputs": {"converged": False, "summary": {"fuel_burned_kg": 14000.0}}},
        {"tool": "get_results", "status": "success", "outputs": {"summary": {"gtow_kg": 88000.0}}},
    ]
    kb = tmp_path / "knowledge_base.jsonl"
    kb.write_text("\n".join(json.dumps(r) for r in rows))
    monkeypatch.setattr(kbm, "_run_config", {"path": str(kb)})
    out = batch_runner._mission_metrics_from_kb()
    assert out["flown"] is True and out["fuel_burned_kg"] == 21000.0 and out["gtow_kg"] == 99000.0


# ---- Drag model decision (Jessica, 2026-11-18): aviary FLOPS drag only -------------------------------------

def test_drag_the_su2_drag_coupling_code_is_gone():
    for name in ("aero_cd_to_aviary_drag_factor", "skin_friction_cd", "total_cruise_cd", "fn_des_from_drag",
                 "mtom_to_pycycle_fn_des", "reynolds_number", "FN_DES_PER_KG_LBF"):
        assert not hasattr(coupling, name), name
    for name in ("_friction_inputs", "_friction_mac"):
        assert not hasattr(dp, name), name


def test_drag_no_drag_keys_are_injected_and_agents_cannot_set_them(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_tool_server_map", {"set_aircraft_parameters": "aviary"})
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.83, "Aircraft.Wing.ASPECT_RATIO": 9.36}
    coupling.put_var(fresh, "aero.cl_cruise", 0.559, source_tool="run_su2_solver")
    coupling.put_var(fresh, "aero.cd_cruise", 0.025, source_tool="run_su2_solver")
    dp.take_refusal()
    out = dp.resolve_request("set_aircraft_parameters", {"session_id": "av", "parameters": {}})
    assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" not in out["parameters"]
    assert "Mission.Design.LIFT_COEFFICIENT" not in out["parameters"]
    assert dp.take_refusal() is None
    assert fresh.data_store["aero_coupling_status"] == "solved_not_coupled_to_drag"
    for key in ("Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR", "Mission.Design.LIFT_COEFFICIENT"):
        dp.resolve_request("set_aircraft_parameters", {"session_id": "av", "parameters": {key: 1.2}})
        refusal = dp.take_refusal()
        assert refusal["error_code"] == "DRAG_FROM_AVIARY_MODEL" and key in refusal["error"]


def test_drag_design_json_says_solved_not_coupled(fresh):
    from src.tools.design_json import build_design

    doc = build_design({"aero_cl_cruise": 0.559, "aero_cd_cruise": 0.025})
    assert doc["aero"]["drag_coupling"].startswith("solved, not coupled to drag")
    # Integration with B284 (C5): the key says what aviary accepted -- no SU2 value under the drag decision.
    assert doc["aero"]["injected_into_aviary"] == {"LIFT_COEFFICIENT": None, "SUBSONIC_DRAG_COEFF_FACTOR": None}


def test_drag_the_mission_still_needs_the_su2_solve():
    from src.tools import coupling_contract as cc

    req = cc.REQUIRED_BY_TOOL["run_simulation"]
    assert cc.AERO_SOLVE in req and cc.AERO_SOLVE.parameter == ""
    assert not hasattr(cc, "AERO_CL") and not hasattr(cc, "AERO_CD")


# ---- Fn_DES from aviary's cruise point ------------------------------------------------------------------------

def _cruise(fresh, monkeypatch, ld=17.5, fp="fp-1", mass=90000.0, scale=1.33, depends=True):
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (mass, "carried sized mass"))
    monkeypatch.setattr(dp, "carried_engine", lambda: ("/decks/d.deck", 1.33, "carried"))
    monkeypatch.setattr(dp, "_tool_server_map", {"set_inputs": "pycycle", "get_cruise_point": "aviary",
                                                 "set_aircraft_parameters": "aviary", "run_cycle": "pycycle"})
    fresh.sessions["aviary"] = "av"
    dp._cruise_basis.set(dp.design_basis())       # B340/B360: as current_cruise_point asks for it
    dp.intercept_response("get_cruise_point", json.dumps({
        "success": True, "design_fingerprint": fp,
        "cruise_point": {"mach": 0.78, "altitude_ft": 33000, "mass_kg": mass, "cl": 0.62, "cd": 0.62 / ld,
                         "lift_to_drag": ld, "engine_scale_used": scale, "cd_depends_on_engine_scale": depends,
                         "design_fingerprint": fp}}))


def test_fn_des_is_sized_on_aviarys_cruise_lift_to_drag(fresh, monkeypatch):
    _cruise(fresh, monkeypatch)
    out = dp.resolve_request("set_inputs", {"session_id": "pc", "values": {"Fn_DES": 6336.0}})
    assert out["values"]["Fn_DES"] == pytest.approx(coupling.fn_des_from_lift_to_drag(90000.0, 17.5))
    assert out["values"]["Fn_DES"] == pytest.approx(90000.0 * 9.81 / 17.5 / 2 / 4.448 * 1.25)
    assert "aviary cruise L/D 17.500" in fresh.data_store["engine_fn_des_basis"]


def test_fn_des_is_deterministic_whatever_the_tool_order(fresh, monkeypatch):
    """The same design gives the same Fn_DES whether SU2, mass or the mission ran first: SU2 CL/CD and this
    iteration's mass estimate do not enter it."""
    _cruise(fresh, monkeypatch)
    a = dp.resolve_request("set_inputs", {"session_id": "pc", "values": {}})["values"]["Fn_DES"]
    coupling.put_var(fresh, "aero.cl_cruise", 0.71, source_tool="run_su2_solver")      # another SU2 answer
    coupling.put_var(fresh, "aero.cd_cruise", 0.0575, source_tool="run_su2_solver")
    fresh.data_store["mass_mtom_kg"] = 95000.0                                          # this iteration's estimate
    b = dp.resolve_request("set_inputs", {"session_id": "pc", "values": {}})["values"]["Fn_DES"]
    assert a == b


def test_a_cruise_point_of_another_design_or_mass_is_not_used(fresh, monkeypatch):
    _cruise(fresh, monkeypatch, fp="fp-old")
    fresh.data_store["aviary_held_fingerprint_by_session"]["av"] = "fp-new"   # aviary now holds another design (B340)
    point, why = dp.current_cruise_point()
    assert point is None and "has not reported a cruise point" in why
    _cruise(fresh, monkeypatch, mass=80000.0)
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (90000.0, "carried sized mass"))
    assert dp.current_cruise_point()[0] is None


def test_a_cruise_point_at_another_engine_scale_counts_only_if_cd_depends_on_it(fresh, monkeypatch):
    _cruise(fresh, monkeypatch, scale=1.0, depends=True)
    assert dp.current_cruise_point()[0] is None                       # carried scale is 1.33
    _cruise(fresh, monkeypatch, scale=1.0, depends=False)
    assert dp.current_cruise_point()[0] is not None


def test_without_a_cruise_point_fn_des_is_not_set_and_run_cycle_is_refused(fresh, monkeypatch):
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (90000.0, "carried sized mass"))
    monkeypatch.setattr(dp, "carried_engine", lambda: ("/decks/d.deck", 1.33, "carried"))
    monkeypatch.setattr(dp, "_tool_server_map", {"set_inputs": "pycycle", "run_cycle": "pycycle"})
    fresh.sessions["pycycle"] = "pc"
    out = dp.resolve_request("set_inputs", {"session_id": "pc", "values": {"Fn_DES": 6336.0}})
    assert "Fn_DES" not in out["values"]
    assert "get_cruise_point" in fresh.data_store["engine_sizing_error"]
    dp.take_refusal()
    dp.resolve_request("run_cycle", {"session_id": "pc"})
    refusal = dp.take_refusal()
    assert refusal["error_code"] == "DESIGN_THRUST_NOT_AVAILABLE"


def test_the_framework_asks_aviary_for_the_point_with_the_carried_scale(fresh, monkeypatch):
    calls = []

    class Point:
        def forward(self, **kw):
            calls.append(kw)
            return dp.intercept_response("get_cruise_point", json.dumps({
                "success": True, "design_fingerprint": "fp-9", "cruise_point": {
                    "mass_kg": kw["mass_kg"], "lift_to_drag": 16.0, "engine_scale_used": kw["engine_scale"],
                    "cd_depends_on_engine_scale": True, "design_fingerprint": "fp-9"}}))

    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (90000.0, "carried sized mass"))
    monkeypatch.setattr(dp, "carried_engine", lambda: ("/decks/d.deck", 1.33, "carried"))
    class Set:          # L3 fixture repair: the framework applies the current design first; aviary names it
        def forward(self, **kw):
            return dp.intercept_response("set_aircraft_parameters",
                                         json.dumps({"success": True, "design_fingerprint": "fp-9"}))

    monkeypatch.setattr(dp, "_tool_server_map", {"get_cruise_point": "aviary", "set_aircraft_parameters": "aviary"})
    dp._registered_tools["get_cruise_point"] = Point()
    dp._registered_tools["set_aircraft_parameters"] = Set()
    fresh.sessions["aviary"] = "av"
    point, why = dp.current_cruise_point()
    assert point["lift_to_drag"] == 16.0
    assert calls == [{"session_id": "av", "mass_kg": 90000.0, "mach": 0.78, "altitude_ft": 33000,
                      "engine_scale": 1.33}]
    assert fresh.data_store["cruise_point_request"]["engine_basis"] == "carried"
    assert coupling.get_var(fresh, "aviary.cruise_l_over_d") == 16.0


def test_an_impossible_cruise_point_is_not_recorded(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_tool_server_map", {"get_cruise_point": "aviary"})
    dp.intercept_response("get_cruise_point", json.dumps({"success": True, "cruise_point": {
        "mass_kg": 90000.0, "lift_to_drag": -3.0, "design_fingerprint": "x"}}))
    assert "aviary_cruise_point" not in fresh.data_store and "not physical" in fresh.data_store["cruise_point_error"]


def test_fn_des_is_not_clamped_and_only_the_impossible_fails():
    assert coupling.fn_des_from_lift_to_drag(150000.0, 5.0) > 25000.0
    assert coupling.fn_des_from_lift_to_drag(20000.0, 30.0) < 2000.0
    for args in ((90000.0, 0.0), (90000.0, -2.0), (0.0, 17.0), (90000.0, float("nan"))):
        with pytest.raises(ValueError):
            coupling.fn_des_from_lift_to_drag(*args)


def test_the_engine_key_is_the_mass_basis_and_the_cruise_point(fresh, monkeypatch):
    _cruise(fresh, monkeypatch)
    assert dp._cycle_sizing_key() == [90000.0, 17.5, "fp-1", dp.design_basis()]        # B340: + the design
    fresh.data_store["mass_mtom_kg"] = 95000.0                    # this iteration's estimate does not enter
    assert dp._cycle_sizing_key() == [90000.0, 17.5, "fp-1", dp.design_basis()]


def test_take_off_mass_is_the_one_mass_basis(fresh, monkeypatch):
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (90000.0, "carried sized mass"))
    fresh.data_store["mass_mtom_kg"] = 95000.0
    assert dp._takeoff_mass_kg() == 90000.0


# ---- mass-mcp M3: the mass estimate flies the engine ----------------------------------------------------------

def test_m3_estimate_mass_gets_the_carried_engine(fresh, monkeypatch, tmp_path):
    deck = tmp_path / "deck_7815.deck"
    deck.write_text("deck")
    monkeypatch.setattr(dp, "carried_engine", lambda: (str(deck), 1.3314, "engine carried from the previous iteration"))
    monkeypatch.setattr(dp, "_tool_server_map", {"estimate_mass": "mass"})
    out = dp.resolve_request("estimate_mass", {"cpacs_file_path": str(deck), "engine_scale_factor": 1.0})
    assert out["engine_deck_file"] == str(deck) and out["engine_scale_factor"] == 1.3314
    assert fresh.data_store["mass_engine_basis"]["source"].startswith("engine carried")
    assert dp._call_notes()["_pinned_overrides"]["estimate_mass"]["engine_scale_factor"]["asked"] == 1.0


def test_m3_a_stale_deck_or_no_engine_refuses(fresh, monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_tool_server_map", {"estimate_mass": "mass"})
    monkeypatch.setattr(dp, "carried_engine", lambda: (None, None, "canonical baseline.engine_deck_file / "
                                                                     "engine_scale_factor are not set"))
    dp.take_refusal()
    dp.resolve_request("estimate_mass", {"cpacs_file_path": "x.xml"})
    assert dp.take_refusal()["error_code"] == "ENGINE_NOT_ON_RECORD"
    # B340 (H5): this iteration's deck is never used -- a stale one no longer refuses (that deadlocked with run_cycle).
    fresh.data_store["engine_deck"] = {"file": "/decks/old.deck", "cycle_key": "old"}
    dp.resolve_request("estimate_mass", {"cpacs_file_path": "x.xml"})
    assert dp.take_refusal()["error_code"] == "ENGINE_NOT_ON_RECORD"


def test_m3_an_unreadable_deck_refuses(fresh, monkeypatch):
    monkeypatch.setattr(dp, "_tool_server_map", {"estimate_mass": "mass"})
    monkeypatch.setattr(dp, "carried_engine", lambda: ("/home/otherhost/decks/d.deck", 1.3, "carried"))
    dp.take_refusal()
    dp.resolve_request("estimate_mass", {"cpacs_file_path": "x.xml"})
    assert dp.take_refusal()["error_code"] == "ENGINE_DECK_NOT_READABLE"


def test_m3_carried_engine_comes_from_the_flown_design_or_the_canonical_baseline(monkeypatch):
    _carry(monkeypatch, {"engine_deck": {"file": "/d/a.deck"}, "mission_applied": {"Aircraft.Engine.SCALE_FACTOR": 1.4}})
    assert dp.carried_engine()[:2] == ("/d/a.deck", 1.4)
    _carry(monkeypatch, {"mission_applied": {}})
    assert dp.carried_engine()[0] is None
    _carry(monkeypatch, None)
    _canonical(monkeypatch, engine_deck_file="/d/base.deck", engine_scale_factor=1.3314)
    assert dp.carried_engine()[:2] == ("/d/base.deck", 1.3314)


def test_a15_the_drivers_verdict_is_read(fresh):
    data = _estimate()
    data["mass_breakdown"]["sizing_report"] = {"driver": {"converged": False, "range_residual_nmi": 117.0}}
    dp._capture_wing_mass_from_mass_estimate("estimate_mass", data)
    assert data["error_code"] == "MASS_ESTIMATE_NOT_USABLE"
