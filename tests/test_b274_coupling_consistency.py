"""B274-B279: the coupled aero/drag/engine values are computed the same way in every iteration.

l20_final bug checks (11_bug_checks.md, Cases 1-4): untrimmed SU2 solves flown as the study's best iterations
(O_SP 6/19, O_IF 4), agent-typed lift targets, a pre-init SU2 session created before the carried mass, a 4.0 m
default MAC, a bench engine reference with the deck attached, and positioning angles in the prompts' sweep column.
No network, no MCP servers, no API.
"""
import json
import math
import re
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import aero_guard, coupling
from src.tools.cpacs_planform import wing_planform

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("AVION_REQUIRE_PHYSICAL_AERO", raising=False)
    monkeypatch.delenv("AVION_ENGINE_DECK", raising=False)
    state = DesignState()
    monkeypatch.setattr(dp, "_design_state", state)
    monkeypatch.setattr(dp, "_registered_tools", {})
    # B306 (A8): the trim mass comes from carry-over / canonical; these tests give it directly (the store's MTOM if set).
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (state.data_store.get("mass_mtom_kg", 78126.0), "test basis"))
    dp._PLANFORM_CACHE.clear()
    for name in ("su2_session", "su2_config_override", "morph", "wing_uid"):
        if hasattr(dp._call_ctx, name):
            delattr(dp._call_ctx, name)
    yield state


# A straight-tapered wing whose planform is known by hand: root chord 6 m at y 0, tip chord 2 m at y 10 m
# (positioning length 10, no sweep or dihedral), mirrored. Area 2 x 40 = 80 m^2, span 20 m, AR 5, taper 1/3,
# MAC 2/3 x 6 x (1 + 1/3 + 1/9) / (4/3) = 13/3 m, c/4 sweep atan((0.5 - 1.5) / 10) = -5.7106 deg.
def _wing_xml(tmp_path, mtom="78126.0", name="wing.xml"):
    def tr(sx=1, sy=1, sz=1):
        return (f"<transformation><scaling><x>{sx}</x><y>{sy}</y><z>{sz}</z></scaling>"
                "<rotation><x>0</x><y>0</y><z>0</z></rotation>"
                "<translation><x>0</x><y>0</y><z>0</z></translation></transformation>")

    sections = "".join(
        f"<section uID='W_S{i}'>{tr(c, 1, c)}<elements><element uID='W_S{i}_E1'><airfoilUID>AF</airfoilUID>"
        f"{tr()}</element></elements></section>" for i, c in ((1, 6.0), (2, 2.0)))
    xml = (
        "<cpacs><vehicles><aircraft><model>"
        f"<analyses><massBreakdown><designMasses><mTOM><mass>{mtom}</mass></mTOM></designMasses></massBreakdown></analyses>"
        f"<wings><wing uID='Wing1' symmetry='x-z-plane'>{tr()}<sections>{sections}</sections>"
        "<positionings><positioning><length>0</length><sweepAngle>0</sweepAngle><dihedralAngle>0</dihedralAngle>"
        "<toSectionUID>W_S1</toSectionUID></positioning>"
        "<positioning><length>10</length><sweepAngle>0</sweepAngle><dihedralAngle>0</dihedralAngle>"
        "<fromSectionUID>W_S1</fromSectionUID><toSectionUID>W_S2</toSectionUID></positioning></positionings>"
        "<segments><segment uID='Seg1'><fromElementUID>W_S1_E1</fromElementUID>"
        "<toElementUID>W_S2_E1</toElementUID></segment></segments></wing></wings>"
        "</model></aircraft>"
        "<profiles><wingAirfoils><wingAirfoil uID='AF'><pointList>"
        "<x>1;0.5;0;0.5;1</x><y>0;0;0;0;0</y><z>0;0.05;0;-0.05;0</z>"
        "</pointList></wingAirfoil></wingAirfoils></profiles></vehicles></cpacs>")
    p = tmp_path / name
    p.write_text(xml)
    return str(p)


# ---- B274 (a): the prompts' sweep defaults are the measured quarter-chord sweeps ---------------------------

_SWEEP_ROW = re.compile(r"\| (Wing|Horizontal tail|Vertical tail) sweep angle \| Set \| tigl: `set_high_level_parameters`, "
                        r"[^|]*`(Wing1|Wing2H|Wing3V)`, key `sweep` \| [^|]* \| ([^|]+) \|")
_MEASURED = {"Wing1": "21.65", "Wing2H": "28.00", "Wing3V": "37.20"}


@pytest.mark.parametrize("source", ["sequential.md", "orchestrated.md", "networked.md",
                                    "config/mdo_f25_sequential_agents.yaml"])
def test_every_sweep_default_is_the_measured_quarter_chord_sweep(source):
    text = (REPO / source).read_text(encoding="utf-8")
    rows = _SWEEP_ROW.findall(text)
    assert rows, f"no sweep rows found in {source}"
    for _, uid, default in rows:
        assert default.strip() == _MEASURED[uid], (source, uid, default)
    for old in ("27.33", "32.21", "40.40"):
        assert f"| {old} |" not in text


@pytest.mark.parametrize("source", ["sequential.md", "orchestrated.md", "networked.md"])
def test_the_link0_design_state_starts_from_the_measured_sweeps(source):
    from src.coordination import prompt_assembly

    lines = "\n".join(prompt_assembly._baseline_lines((REPO / source).read_text(encoding="utf-8")))
    assert re.search(r"Wing1: [^\n]*sweep 21\.65", lines)
    assert re.search(r"Wing2H: [^\n]*sweep 28\.00", lines)
    assert re.search(r"Wing3V: [^\n]*sweep 37\.20", lines)
    assert "27.33" not in lines and "32.21" not in lines and "40.40" not in lines


# ---- B277 (d): the wing read straight from the CPACS file ------------------------------------------------

def test_the_planform_of_a_hand_checked_wing(tmp_path):
    p = wing_planform(_wing_xml(tmp_path))
    assert p["reference_area"] == pytest.approx(80.0, rel=1e-12)
    assert p["span"] == pytest.approx(20.0, rel=1e-12)
    assert p["aspect_ratio"] == pytest.approx(5.0, rel=1e-12)
    assert p["taper_ratio"] == pytest.approx(1.0 / 3.0, rel=1e-12)
    assert p["mac_length"] == pytest.approx(13.0 / 3.0, rel=1e-12)
    assert p["sweep_quarter_chord_deg"] == pytest.approx(math.degrees(math.atan(-0.1)), rel=1e-12)


def test_the_d150_baseline_matches_tigl():
    """TiGL 3 on aipexws8 (tigl-env, tigl-mcp 59f866b `_measure` / planform.py), 2026-11-17. The same comparison
    over all 122 geometry_end.xml files of l20_final gave max relative differences: area 6e-16, span 7e-9, AR 1.4e-8,
    c/4 sweep 1e-15, taper 3e-15, MAC 1.6e-5 (/data/Jessica/b274_test/check/cmp2.json)."""
    from src.config.canonical import AVION_ROOT

    fixture = AVION_ROOT / "mass-mcp" / "tests" / "fixtures" / "D150_simple.xml"
    if not fixture.is_file():
        pytest.skip(f"{fixture} not present")
    p = wing_planform(str(fixture), "Wing1")
    assert p["reference_area"] == pytest.approx(122.82871858371793, rel=1e-12)
    assert p["span"] == pytest.approx(33.91272350701785, rel=1e-7)
    assert p["aspect_ratio"] == pytest.approx(9.363224080853465, rel=1e-7)
    assert p["sweep_quarter_chord_deg"] == pytest.approx(21.65015342508847, rel=1e-12)
    assert p["taper_ratio"] == pytest.approx(0.24619376823302655, rel=1e-12)
    assert p["mac_length"] == pytest.approx(4.192549875742061, rel=3e-5)


def test_with_tigl_closed_and_nothing_measured_the_solve_is_still_trimmed(fresh, tmp_path):
    """O_SP 6: geometry closed TiGL before any measurement; create_su2_session got no target, AR or ref_area."""
    path = _wing_xml(tmp_path)
    resolved = dp.resolve_request("create_su2_session", {"cpacs_file_path": path})
    assert resolved["target_cl"] == pytest.approx(coupling.cruise_lift_coefficient(78126.0, 0.78, 33000, 80.0))
    assert resolved["aspect_ratio"] == pytest.approx(5.0)
    assert resolved["ref_area"] == pytest.approx(80.0)
    assert fresh.data_store["geometry_wing"]["_source"].startswith("cpacs file")


def test_a_measurement_beats_the_file_and_a_pending_morph_blocks_it(fresh, tmp_path):
    path = _wing_xml(tmp_path)
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 100.0, "Aircraft.Wing.ASPECT_RATIO": 9.0}
    assert dp._wing_from_cpacs_file(path)["Aircraft.Wing.AREA"] == 100.0
    fresh.data_store.pop("geometry_wing")
    fresh.data_store["morph_unexported"] = {"session_id": "t"}
    assert dp._wing_from_cpacs_file(path) is None


def test_close_cpacs_forgets_the_tigl_session_and_auto_measure_does_not_call_it(fresh):
    calls = []

    class Tool:
        def forward(self, **kw):
            calls.append(kw)
            return "{}"

    dp._registered_tools["get_high_level_parameters"] = Tool()
    fresh.sessions["tigl"] = "t-1"
    dp.note_session_closed("close_cpacs", {"session_id": "t-other"}, json.dumps({"success": True}))
    assert fresh.sessions["tigl"] == "t-1"                       # another session: kept
    dp.note_session_closed("close_cpacs", {"session_id": "t-1"}, json.dumps({"success": False}))
    assert fresh.sessions["tigl"] == "t-1"                       # a failed close: kept
    dp.note_session_closed("close_cpacs", {"session_id": "t-1"}, json.dumps({"success": True}))
    assert "tigl" not in fresh.sessions
    dp._auto_measure_wing()
    assert calls == []


# ---- B275 (b): the framework's lift target wins ------------------------------------------------------------

def _ready(state, mass=79560.0, area=122.78):
    state.data_store["mass_mtom_kg"] = mass
    state.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": area, "Aircraft.Wing.ASPECT_RATIO": 9.4}


def test_an_agents_target_cl_is_replaced_and_the_response_says_so(fresh):
    _ready(fresh)
    want = coupling.cruise_lift_coefficient(79560.0, 0.78, 33000, 122.78)
    resolved = dp.resolve_request("configure_from_cpacs", {"session_id": "s", "target_cl": 0.56})
    assert resolved["target_cl"] == pytest.approx(want)
    out = json.loads(dp.intercept_response("configure_from_cpacs", json.dumps({"trim": {"target_cl": want}})))
    assert out["framework_applied"]["overridden"]["target_cl"] == {"asked": 0.56, "used": round(want, 6)}
    # the note is used once
    again = json.loads(dp.intercept_response("configure_from_cpacs", json.dumps({})))
    assert "framework_applied" not in again


def test_trim_settings_in_overrides_cannot_undo_the_trim(fresh):
    _ready(fresh)
    resolved = dp.resolve_request("create_su2_session", {"cpacs_file_path": "x.xml",
                                                         "overrides": {"TARGET_CL": 0.5, "FIXED_CL_MODE": "NO",
                                                                       "MACH_NUMBER": 0.78}})
    assert "TARGET_CL" not in resolved["overrides"] and "FIXED_CL_MODE" not in resolved["overrides"]
    assert resolved["target_cl"] == pytest.approx(coupling.cruise_lift_coefficient(79560.0, 0.78, 33000, 122.78))
    assert dp._call_notes()["_target_cl_asked"]["overrides_removed"] == {"TARGET_CL": "0.5", "FIXED_CL_MODE": "NO"}


def test_the_same_value_typed_is_not_reported_as_replaced(fresh):
    _ready(fresh)
    want = coupling.cruise_lift_coefficient(79560.0, 0.78, 33000, 122.78)
    dp.resolve_request("configure_from_cpacs", {"session_id": "s", "target_cl": want})
    assert "_target_cl_asked" not in fresh.data_store


def test_without_a_framework_target_the_callers_value_is_refused_not_kept(fresh):
    """B306: was "kept and flagged"; no target means the configuration is refused (no silent default)."""
    dp.take_refusal()
    resolved = dp.resolve_request("configure_from_cpacs", {"session_id": "s", "target_cl": 0.56})
    assert "target_cl" not in resolved
    refusal = dp.take_refusal()
    assert refusal["error_code"] == "NO_TRIM_TARGET" and "wing area" in refusal["error"]


def test_without_a_trim_mass_the_configuration_is_refused(fresh, monkeypatch):
    monkeypatch.setattr(dp, "trim_mass_basis", lambda: (None, "canonical baseline.sized_mtom_kg is not set (None)"))
    _ready(fresh)
    dp.take_refusal()
    dp.resolve_request("configure_from_cpacs", {"session_id": "s"})
    refusal = dp.take_refusal()
    assert refusal["error_code"] == "NO_TRIM_TARGET" and "sized_mtom_kg is not set" in refusal["error"]


# ---- B277 (d): the guard refuses untrimmed / unchecked / below-induced aero ------------------------------

def _aero(state, cl, cd, source="run_su2_solver"):
    state.data_store["aero_cl_cruise"], state.data_store["aero_cd_cruise"] = cl, cd
    coupling.put_var(state, "aero.cl_cruise", cl, source_tool=source)
    coupling.put_var(state, "aero.cd_cruise", cd, source_tool=source)


def test_o_sp_6_is_refused(fresh):
    """The recorded O_SP 6 solve: fixed AOA, su2_unchecked, CD 0.00532 below CL^2/(pi e AR) = 0.0093."""
    _aero(fresh, 0.4609687869, 0.005323192215)
    fresh.data_store["aero_solve_trimmed"] = False
    fresh.data_store["aero_solve_converged"] = True
    fresh.data_store["aero_drag_assessment"] = {"source": "su2_unchecked", "cd_reported": 0.005323192215}
    fresh.data_store["geometry_wing"] = {"Aircraft.Wing.AREA": 122.788, "Aircraft.Wing.ASPECT_RATIO": 9.3693}
    refusal = aero_guard.aero_nonphysical("run_simulation", {})
    assert refusal["error_code"] == "AERO_NONPHYSICAL"
    assert "no target CL" in refusal["error"]
    # Drag model decision 2026-11-18: the CD-based rules (su2_unchecked, induced drag) were removed with the drag
    # coupling -- the trim rule is what refuses this solve now.
    assert "su2_unchecked" not in refusal["error"] and "induced" not in refusal["error"]


def test_a_trimmed_checked_solve_flies(fresh):
    """O_SP 5: trimmed to 0.55919, CD 0.02496 above the floor 0.01372 (source su2)."""
    _aero(fresh, 0.558735238, 0.0249581227)
    fresh.data_store["aero_solve_trimmed"] = True
    fresh.data_store.update({"aero_solve_target_cl": 0.5591901447, "aero_solve_converged": True})
    fresh.data_store["aero_drag_assessment"] = {"source": "su2", "aspect_ratio": 9.369319,
                                                "oswald_efficiency": 0.7732, "cd_reported": 0.0249581227}
    assert aero_guard.aero_nonphysical("run_simulation", {}) is None


def test_a_floored_solve_flies(fresh):
    cl, ar, e = 0.5587, 9.37, 0.7732
    floor = cl * cl / (math.pi * ar * e)
    _aero(fresh, cl, floor)
    fresh.data_store["aero_solve_trimmed"] = True
    fresh.data_store.update({"aero_solve_target_cl": cl, "aero_solve_converged": True})
    fresh.data_store["aero_drag_assessment"] = {"source": "induced_floor", "aspect_ratio": ar,
                                                "oswald_efficiency": e, "cd_reported": floor}
    assert aero_guard.aero_nonphysical("run_simulation", {}) is None


def test_the_solve_inherits_its_sessions_trim_end_to_end(fresh):
    """create_su2_session without a target -> run_su2_solver -> the captured aero is marked untrimmed."""
    dp.note_su2_trim("create_su2_session", {"cpacs_file_path": "x.xml"},
                     json.dumps({"session_id": "su2-a", "configured": True}))
    dp.note_su2_trim("configure_from_cpacs", {"session_id": "su2-b", "target_cl": 0.559},
                     json.dumps({"trim": {"target_cl": 0.559}}))
    dp.note_su2_trim("create_su2_session", {}, json.dumps({"session_id": "su2-c"}))       # never configured
    assert fresh.data_store["su2_session_trim"] == {"su2-a": None, "su2-b": 0.559, "su2-c": None}
    for sid, trimmed in (("su2-a", False), ("su2-b", True), ("su2-unknown", None)):
        dp.resolve_request("run_su2_solver", {"session_id": sid})
        dp.intercept_response("run_su2_solver", json.dumps({
            "success": True, "final_coefficients": {"CL": 0.56, "CD": 0.03},
            "drag_assessment": {"source": "su2", "aspect_ratio": 9.37, "oswald_efficiency": 0.77,
                                "cd_reported": 0.03}}))
        assert fresh.data_store["aero_solve_trimmed"] is trimmed
    dp.resolve_request("run_su2_solver", {"session_id": "su2-a"})
    dp.intercept_response("run_su2_solver", json.dumps({"success": True, "final_coefficients": {"CL": 0.56, "CD": 0.03},
                                                        "drag_assessment": {"source": "su2", "aspect_ratio": 9.37,
                                                                            "oswald_efficiency": 0.77, "cd_reported": 0.03}}))
    assert "no target CL" in aero_guard.aero_nonphysical("run_simulation", {})["error"]


def test_a_solve_without_an_assessment_drops_the_previous_one(fresh):
    fresh.data_store["aero_drag_assessment"] = {"source": "su2", "aspect_ratio": 9.37, "cd_reported": 0.03}
    dp.intercept_response("run_su2_solver", json.dumps({"success": True, "final_coefficients": {"CL": 0.56, "CD": 0.03}}))
    assert "aero_drag_assessment" not in fresh.data_store


# ---- B276 (c): graph pre-init seeds the carried mass before the SU2 session --------------------------------

def test_graph_preinit_creates_the_su2_session_on_the_carried_mass(fresh, monkeypatch, tmp_path):
    from src.coordination import graph_preinit

    seen = {}

    def fake_call(name, **kwargs):
        if name == "create_su2_session":
            seen["mass_at_su2"] = coupling.get_var(dp._design_state, "mass.mtom_kg")
        return {}

    monkeypatch.setattr(graph_preinit, "_call", fake_call)
    cpacs = _wing_xml(tmp_path)
    previous = {"aero": {"cl_cruise": 0.62, "cd_cruise": 0.032, "solve_trimmed": True, "solve_target_cl": 0.62},
                "structures": {"mtom_kg": 89455.7, "wing_mass_kg": 9000.0, "sizing_converged": True}}
    report = graph_preinit.prepare({"starting_design": {"CPACS_FILE": cpacs}, "previous_design": previous})
    assert seen["mass_at_su2"] == 89455.7
    assert report["mass_seeded_before_su2"] == 89455.7
    assert fresh.data_store["aero_solve_trimmed"] is True


# ---- B279 (f): the engine scale divides by the reference of the deck that is attached ---------------------

def _engine(state, monkeypatch, attached=True, deck_cycle_key="old"):
    monkeypatch.setattr(dp, "engine_is_current", lambda: True)
    coupling.put_var(state, "prop.fn_lbf", 20087.769907364054, source_tool="run_cycle")
    state.sessions["aviary"] = "av"
    state.data_store["prop_cycle_key"] = "new"
    state.data_store["engine_deck"] = {"file": "deck_7815057d6edc9732.deck", "key": "7815057d6edc9732",
                                       "reference_fn_des_lbf": 5900.0, "cycle_key": deck_cycle_key}
    if attached:
        state.data_store["engine_deck_attached"] = {"av": "deck_7815057d6edc9732.deck"}


def test_s_gr_5_the_attached_deck_reference_is_used_after_a_resize(fresh, monkeypatch):
    """S_GR 5: run_cycle re-sized the engine (new cycle key, same deck file attached); the scale was Fn/5936."""
    _engine(fresh, monkeypatch, deck_cycle_key="old")
    assert dp.engine_deck_current("av") is False          # the condition that used to pick the bench reference
    resolved = {"session_id": "av", "parameters": {}}
    dp._inject_engine_scale(resolved)
    assert resolved["parameters"]["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(20087.769907364054 / 5900.0)
    assert fresh.data_store["engine_scale_basis"] == "engine deck 7815057d6edc9732"


def test_no_deck_attached_flies_the_bench_reference(fresh, monkeypatch):
    _engine(fresh, monkeypatch, attached=False)
    resolved = {"session_id": "av", "parameters": {}}
    dp._inject_engine_scale(resolved)
    assert resolved["parameters"]["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(
        20087.769907364054 / coupling.BENCH_ENGINE_CRUISE_FN_LBF)


def test_the_deck_reference_is_recorded_per_file(fresh, monkeypatch):
    monkeypatch.setattr(dp, "engine_is_current", lambda: True)

    class Gen:
        def forward(self, **kw):
            return json.dumps({"success": True, "deck_file": "d1.deck", "design_key": "k1",
                               "reference_fn_des_lbf": 5900.0})

    class Attach:
        def forward(self, **kw):
            return json.dumps({"success": True})

    dp._registered_tools.update({"generate_engine_deck": Gen(), "set_engine_deck": Attach()})
    fresh.sessions.update({"aviary": "av", "pycycle": "pc"})
    fresh.data_store["prop_cycle_session"] = "pc"
    dp._ensure_engine_deck({"session_id": "av"})
    assert fresh.data_store["engine_deck_references"] == {"d1.deck": {"reference_fn_des_lbf": 5900.0, "key": "k1"}}
    assert fresh.data_store["engine_deck_attached"] == {"av": "d1.deck"}


# ---- B275: every path that sets the SU2 lift target -------------------------------------------------------

def test_o_gr_17_a_lowercase_target_cl_in_create_overrides_is_the_frameworks(fresh):
    """su2-mcp takes `target_cl` inside overrides as the argument (B173, any case); the framework's wins."""
    _ready(fresh)
    want = coupling.cruise_lift_coefficient(79560.0, 0.78, 33000, 122.78)
    resolved = dp.resolve_request("create_su2_session", {"cpacs_file_path": "x.xml",
                                                         "overrides": {"target_cl": 0.7148, "MACH_NUMBER": 0.78}})
    assert resolved["target_cl"] == pytest.approx(want)
    assert not any(str(k).upper() in ("TARGET_CL", "FIXED_CL_MODE") for k in resolved["overrides"])
    assert dp._call_notes()["_target_cl_asked"]["asked"] == 0.7148


def test_a_trim_in_initial_config_text_is_removed(fresh):
    _ready(fresh)
    cfg = "SOLVER= EULER\nFIXED_CL_MODE= NO\ntarget_cl = 0.3\nMACH_NUMBER= 0.78\n"
    resolved = dp.resolve_request("create_su2_session", {"cpacs_file_path": "x.xml", "initial_config": cfg})
    assert resolved["initial_config"] == "SOLVER= EULER\nMACH_NUMBER= 0.78\n"
    assert dp._call_notes()["_target_cl_asked"]["overrides_removed"] == {
        "initial_config:FIXED_CL_MODE": "NO", "initial_config:TARGET_CL": "0.3"}


def test_update_config_entries_cannot_change_the_trim(fresh):
    resolved = dp.resolve_request("update_config_entries", {"session_id": "s", "updates": {
        "TARGET_CL": 0.56, "fixed_cl_mode": "NO", "CFL_NUMBER": 5}})
    assert resolved["updates"] == {"CFL_NUMBER": 5}


def test_a_solve_on_another_config_file_is_untrimmed(fresh):
    dp.note_su2_trim("configure_from_cpacs", {"session_id": "s", "target_cl": 0.559},
                     json.dumps({"trim": {"target_cl": 0.559}}))
    dp.resolve_request("run_su2_solver", {"session_id": "s", "config_override_path": "my.cfg"})
    dp.intercept_response("run_su2_solver", json.dumps({"success": True, "final_coefficients": {"CL": 0.56, "CD": 0.03}}))
    assert fresh.data_store["aero_solve_trimmed"] is False
    dp.resolve_request("run_su2_solver", {"session_id": "s"})
    dp.intercept_response("run_su2_solver", json.dumps({"success": True, "final_coefficients": {"CL": 0.56, "CD": 0.03}}))
    assert fresh.data_store["aero_solve_trimmed"] is True


# ---- B279: the 16 l20_final iterations, as a flow ---------------------------------------------------------

def test_the_second_mission_of_an_iteration_keeps_the_deck_reference(fresh, monkeypatch):
    """All 16 flagged iterations (S_GR 1,2,3,5,9,12,14,15,19; O_GR 5,6,8,17; N_GR 1,19; N_IF 5) follow one KB pattern
    (b279_scan.py): deck attached at the first mission -> run_cycle re-sizes (new cycle key) -> set_aircraft_parameters
    -> the next mission regenerates the SAME cached deck file and re-attaches nothing. The scale must be Fn/5900."""
    monkeypatch.setattr(dp, "engine_is_current", lambda: True)
    attach_calls = []

    class Gen:
        def forward(self, **kw):
            return json.dumps({"success": True, "deck_file": "deck_7815057d6edc9732.deck",
                               "design_key": "7815057d6edc9732", "reference_fn_des_lbf": 5900.0, "cached": True})

    class Attach:
        def forward(self, **kw):
            attach_calls.append(kw)
            return json.dumps({"success": True})

    dp._registered_tools.update({"generate_engine_deck": Gen(), "set_engine_deck": Attach()})
    fresh.sessions.update({"aviary": "av", "pycycle": "pc"})
    fresh.data_store["prop_cycle_session"] = "pc"
    fresh.data_store["prop_cycle_key"] = "k1"
    dp._ensure_engine_deck({"session_id": "av"})                     # first mission: deck attached
    assert len(attach_calls) == 1
    fresh.data_store["prop_cycle_key"] = "k2"                        # run_cycle re-sized the engine
    coupling.put_var(fresh, "prop.fn_lbf", 7716.79, source_tool="run_cycle")
    assert dp.engine_deck_current("av") is False
    resolved = {"session_id": "av", "parameters": {}}
    dp._inject_engine_scale(resolved)                                # the agent's / framework's set call
    dp._capture_applied_design("set_aircraft_parameters", {"success": True, "applied": []})   # aviary accepted it
    assert resolved["parameters"]["Aircraft.Engine.SCALE_FACTOR"] == pytest.approx(7716.79 / 5900.0)
    dp._ensure_engine_deck({"session_id": "av"})                     # second mission: same cached file
    assert len(attach_calls) == 1                                    # nothing re-attached ...
    assert fresh.data_store["engine_injected_key"]["av"] == dp._engine_scale_key("av")   # ... and no re-apply needed
