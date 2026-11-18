"""Second independent review of integration/final-rerun-2026-11-18 (L1-L8, 2026-11-18).

L1 mission_constraints summary uses aviary-mcp's own violated test (real server values as fixtures).
L2 baseline deck named relative to the machine's study decks folder (AVION_DECKS_DIR, md5) + scripts/prelaunch_check.sh.
L3 the cruise point is stamped with the current design only after a successful framework set with the same fingerprint.
L4 a failed run_cycle does not count as run; set_inputs resets the convergence verdict.
L5 get_trajectory / check_constraints replies without flown_design_fingerprint are held-only.
L6 resume replays the chain exactly as the live loop (a flown link with an empty end-state still carries).
L8 an aviary reply is filed under its own session id; error / unconverged replies are not "flown".

Stub servers only (the B340 World), no network, no API, no MCP servers. The prelaunch-check test runs the bash script
on a local git clone of this checkout with a fake A16 log; its PASS case needs the real study deck (skipped without it).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import coupling
from tests.test_b340_review_fixes import TRIM_MASS, World, _expected, _start_on_A, world  # noqa: F401 (fixture)

HERE = Path(__file__).parent
REPO = HERE.parent


# ---------------------------------------------------------------- L1
def _real(name):
    return json.loads((HERE / f"fixtures_l1_mission_constraints_{name}.json").read_text())


@pytest.fixture
def plain(monkeypatch):
    state = DesignState()
    state.sessions.update({"aviary": "av"})
    dp.init_data_plane(state, {"run_simulation": "aviary", "get_results": "aviary", "get_trajectory": "aviary",
                               "check_constraints": "aviary", "create_session": "aviary",
                               "set_aircraft_parameters": "aviary", "get_cruise_point": "aviary"})
    monkeypatch.setattr(dp, "_registered_tools", {})
    dp._call_ctx.__dict__.clear()
    yield state
    dp._call_ctx.__dict__.clear()
    dp.init_data_plane(DesignState(), {})


def _intercept(tool, reply, sid=None):
    if sid is not None:
        dp._call_ctx.aviary_sid = sid
    out = dp.intercept_response(tool, json.dumps(reply))
    return json.loads(out) if isinstance(out, str) else out


def test_l1_converged_baseline_lists_no_violated_constraint(plain):
    mc = _real("baseline_anchor2")
    assert sum(1 for v in mc.values() if v["violation"] > 0.0) == 8          # what the old rule listed
    assert len(json.dumps(mc)) > dp._DICT_BYTE_THRESHOLD
    out = _intercept("run_simulation", {"success": True, "converged": True, "mission_constraints": mc,
                                        "summary": {"fuel_burned_kg": 16638.7}}, sid="av")
    s = out["mission_constraints"]
    assert s["constraints"] == 17 and s["violated"] == {} and "unreadable" not in s
    assert "1e-06" in s["violated_rule"]
    assert plain.data_store[s["ref"]] == mc


def test_l1_lists_exactly_the_constraints_aviary_counts_as_violated(plain):
    mc = _real("slsqp_seed0_eval2")              # converged SLSQP evaluation; 4 constraints beyond 1e-6
    want = {k for k, v in mc.items() if v["points_violated"] > 0}
    assert want == {"traj.climb.collocation_constraint.defects:mass", "traj.cruise.collocation_constraint.defects:mass",
                    "traj.descent.collocation_constraint.defects:mass",
                    "traj.descent.collocation_constraint.defects:distance"}
    out = _intercept("get_results", {"success": True, "mission_constraints": mc}, sid="av")
    v = out["mission_constraints"]["violated"]
    assert set(v) == want
    assert v["traj.cruise.collocation_constraint.defects:mass"]["points_violated"] == 15
    assert "mission:constraints:mass_residual" not in v                      # 1.2e-10: met


def test_l1_without_points_violated_the_same_tolerance_applies_and_unreadable_is_listed(plain):
    mc = _real("baseline_anchor2")
    for d in mc.values():
        d.pop("points_violated")
    mc["traj.climb.collocation_constraint.defects:mass"]["violation"] = 2.4e-6
    mc["broken"] = {"violation": None, "units": "kg"}
    s = _intercept("run_simulation", {"success": True, "mission_constraints": mc}, sid="av")["mission_constraints"]
    assert set(s["violated"]) == {"traj.climb.collocation_constraint.defects:mass"}
    assert s["unreadable"] == ["broken"]


# ---------------------------------------------------------------- L2
BASE = {"sized_mtom_kg": 91197.1, "engine_scale_factor": 1.2494328339916752}


def test_l2_canonical_deck_is_a_file_name_with_its_md5():
    from src.config.canonical import load_canonical

    base = load_canonical()["baseline"]
    assert base["engine_deck_file"] == "deck_7815057d6edc9732.deck" and not os.path.isabs(base["engine_deck_file"])
    assert base["engine_deck_md5"] == "b1c1e040e4abe82782e47d1ed7666ca8"


def test_l2_relative_deck_needs_the_decks_dir_and_the_right_file(tmp_path, monkeypatch):
    base = dict(BASE, engine_deck_file="deck_x.deck", engine_deck_md5=hashlib.md5(b"right").hexdigest())
    monkeypatch.delenv(dp.DECKS_DIR_ENV, raising=False)
    path, why = dp.resolve_baseline_deck(base)
    assert path is None and "AVION_DECKS_DIR is not set" in why
    monkeypatch.setenv(dp.DECKS_DIR_ENV, str(tmp_path))
    path, why = dp.resolve_baseline_deck(base)
    assert path is None and "does not exist" in why
    (tmp_path / "deck_x.deck").write_bytes(b"wrong")
    path, why = dp.resolve_baseline_deck(base)
    assert path is None and "md5" in why
    (tmp_path / "deck_x.deck").write_bytes(b"right")
    path, why = dp.resolve_baseline_deck(base)
    assert path == str(tmp_path / "deck_x.deck") and "AVION_DECKS_DIR" in why


def test_l2_carried_engine_refuses_rather_than_guess(tmp_path, monkeypatch):
    from src.config import canonical

    real = canonical.load_canonical()
    base = dict(BASE, engine_deck_file="deck_x.deck", engine_deck_md5=hashlib.md5(b"right").hexdigest())
    monkeypatch.setattr(canonical, "load_canonical", lambda path=None: {**real, "baseline": base})
    monkeypatch.setattr(dp, "flown_basis_design", lambda: (None, "no link flew"))
    monkeypatch.delenv(dp.DECKS_DIR_ENV, raising=False)
    deck, scale, why = dp.carried_engine()
    assert deck is None and scale is None and "AVION_DECKS_DIR" in why
    (tmp_path / "deck_x.deck").write_bytes(b"right")
    monkeypatch.setenv(dp.DECKS_DIR_ENV, str(tmp_path))
    deck, scale, why = dp.carried_engine()
    assert deck == str(tmp_path / "deck_x.deck") and scale == BASE["engine_scale_factor"]


def _study_deck():
    for d in (os.environ.get("AVION_STUDY_DECKS"), Path.home() / "Jessica" / "Avion" / "study_decks"):
        if d and (Path(d) / "deck_7815057d6edc9732.deck").is_file():
            return Path(d)
    return None


PINS = {"MAS-Aviary": None, "aviary-mcp": "aaaa111", "mass-mcp": "bbbb222", "tigl-mcp": "cccc333",
        "su2-mcp": "dddd444", "pycycle-mcp": "eeee555"}
SERVERS_A16 = [("tigl-mcp", "tigl_mcp", "tigl_mcp", "tigl-mcp"), ("mass-mcp-server", "mass_mcp", "mass_mcp", "mass-mcp"),
               ("su2-mcp", "su2_mcp", "su2_mcp", "su2-mcp"), ("pycycle-mcp", "pycycle_mcp", "pycycle_mcp", "pycycle-mcp"),
               ("aviary_mcp_server.py", "aviary_mcp_server", "aviary_runner", "aviary-mcp")]


def _a16_log(path, pid, *, skip_loaded=None, commit_override=None, mismatch=None):
    lines = []
    for entry, pkg, mod, repo in SERVERS_A16:
        sha = (commit_override or {}).get(repo, PINS[repo] + "f" * 33)
        tail = "MISMATCH" if mismatch == repo else "OK"
        lines.append(f"[A16] 2026-11-18T12:49:56 pid={pid} entry={entry} {pkg}: /code/{repo}/x.py commit={sha} "
                     f"tree=clean expected_under=/code/{repo} {tail}")
        if skip_loaded != repo:
            lines.append(f"[A16] 2026-11-18T12:50:56 pid={pid} entry={entry} loaded {mod}: /code/{repo}/x.py "
                         f"commit={sha} tree=clean OK")
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def launch_tree(tmp_path):
    """tmp/Avion/{MAS-Aviary (local clone of this checkout), mass-mcp fixture}, slot with a decks folder."""
    root = tmp_path / "Avion"
    root.mkdir()
    mas = root / "MAS-Aviary"
    subprocess.run(["git", "clone", "-q", "--shared", str(REPO), str(mas)], check=True, timeout=120)
    head = subprocess.run(["git", "-C", str(mas), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    (mas / "logs").mkdir()
    fx = root / "mass-mcp" / "tests" / "fixtures"
    fx.mkdir(parents=True)
    (fx / "D150_simple.xml").write_text("<cpacs/>")
    slot = tmp_path / "slot1"
    (slot / "decks").mkdir(parents=True)
    pins = dict(PINS, **{"MAS-Aviary": head[:7]})
    return {"mas": mas, "slot": slot, "pins": " ".join(f"{k}={v}" for k, v in pins.items())}


def _check(tree, decks=None, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    env["PINS"] = tree["pins"]
    env.pop(dp.DECKS_DIR_ENV, None)
    if decks is not None:
        env[dp.DECKS_DIR_ENV] = str(decks)
    env.update(extra_env or {})
    r = subprocess.run(["bash", str(tree["mas"] / "scripts" / "prelaunch_check.sh"), "--slot-home", str(tree["slot"]),
                        "--python", sys.executable, "--run-dir", str(tree["mas"])],
                       capture_output=True, text=True, env=env, timeout=550)
    return r.returncode, r.stdout + r.stderr


def test_l2_prelaunch_check_passes_on_a_ready_machine(launch_tree):
    study = _study_deck()
    if study is None:
        pytest.skip("the study deck deck_7815057d6edc9732.deck is not on this machine")
    decks = launch_tree["slot"] / "decks"
    (decks / "deck_7815057d6edc9732.deck").write_bytes((study / "deck_7815057d6edc9732.deck").read_bytes())
    _a16_log(launch_tree["slot"] / "a16.log", os.getpid())
    rc, out = _check(launch_tree, decks)
    assert rc == 0, out
    assert "PRELAUNCH CHECK PASSED" in out and "FAIL" not in out.replace("PRELAUNCH CHECK FAILED", "")


@pytest.mark.parametrize("fault", ["no_decks_env", "wrong_deck", "commit", "mismatch", "no_loaded", "dead_pid",
                                   "no_logs", "no_mass_mcp", "no_a16"])
def test_l2_prelaunch_check_fails_on_each_fault(launch_tree, fault):
    decks = launch_tree["slot"] / "decks"
    study = _study_deck()
    if study is not None and fault != "wrong_deck":
        (decks / "deck_7815057d6edc9732.deck").write_bytes((study / "deck_7815057d6edc9732.deck").read_bytes())
    else:
        (decks / "deck_7815057d6edc9732.deck").write_bytes(b"not the study deck")
    a16 = launch_tree["slot"] / "a16.log"
    pid = os.getpid()
    if fault == "dead_pid":
        p = subprocess.Popen(["true"])
        p.wait()
        pid = p.pid
    _a16_log(a16, pid, skip_loaded="su2-mcp" if fault == "no_loaded" else None,
             commit_override={"mass-mcp": "9" * 40} if fault == "commit" else None,
             mismatch="tigl-mcp" if fault == "mismatch" else None)
    if fault == "no_a16":
        a16.write_text("")
    if fault == "no_logs":
        (launch_tree["mas"] / "logs").rmdir()
    if fault == "no_mass_mcp":
        (launch_tree["mas"].parent / "mass-mcp" / "tests" / "fixtures" / "D150_simple.xml").rename(
            launch_tree["mas"].parent / "D150_moved.xml")
    rc, out = _check(launch_tree, None if fault == "no_decks_env" else decks)
    assert rc == 1, out
    assert "PRELAUNCH CHECK FAILED" in out
    expect = {"no_decks_env": "AVION_DECKS_DIR is not set", "wrong_deck": "md5", "commit": "pin bbbb222",
              "mismatch": "MISMATCH", "no_loaded": "no 'loaded su2_mcp' line", "dead_pid": "is not running",
              "no_logs": "no logs/", "no_mass_mcp": "B251", "no_a16": "a16.log missing or empty"}[fault]
    assert expect in out, out


# ---------------------------------------------------------------- L3
class FailingSetWorld(World):
    """aviary refuses the set (session busy) but still names the design it holds (aviary-mcp B339)."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.set_fails = False
        self.point_fp = None

    def server(self, tool, kw):
        if tool == "set_aircraft_parameters" and self.set_fails:
            return {"success": False, "error_code": "SESSION_BUSY", "error": "the session is running a mission",
                    "design_fingerprint": self._fp(kw["session_id"])}
        reply = super().server(tool, kw)
        if tool == "get_cruise_point" and self.point_fp:
            reply["cruise_point"]["design_fingerprint"] = self.point_fp
        return reply


@pytest.fixture
def fworld(world, tmp_path):
    w = FailingSetWorld(tmp_path)
    w.__dict__.update({k: v for k, v in world.__dict__.items() if k not in ("set_fails", "point_fp")})
    from tests.test_b340_review_fixes import _Tool

    dp.register_tools([_Tool(w, n) for n in ("set_aircraft_parameters", "get_cruise_point", "set_inputs")])
    return w


def test_l3_a_failed_framework_set_never_stamps_the_new_design_on_the_old_point(fworld):
    _start_on_A(fworld)
    fn_a = coupling.get_var(dp._design_state, "prop.fn_lbf")
    fworld.morph("B")
    fworld.call("estimate_mass", cpacs_file_path=str(fworld.cpacs))
    fworld.set_fails = True
    point, why = dp.current_cruise_point()
    assert point is None and "did not succeed" in why and "SESSION_BUSY" in why
    stored = dp._design_state.data_store.get("aviary_cruise_point") or {}
    assert stored.get("_design_basis") != dp.design_basis()          # A's point is not B's
    sent = len(fworld.fn_des_sent())
    fworld.call("set_inputs", session_id="pc", values={})
    assert all(f != pytest.approx(fn_a) for f in fworld.fn_des_sent()[sent:])     # never sized on A's L/D for B
    assert not dp.engine_is_current()
    fworld.set_fails = False                                          # the set goes through: B is sized on B
    fworld.call("set_inputs", session_id="pc", values={})
    fworld.call("run_cycle", session_id="pc")
    assert fworld.fn_des_sent()[-1] == pytest.approx(_expected(fworld)) and dp.engine_is_current()


def test_l3_a_raising_set_leaves_no_point_and_no_basis(fworld, monkeypatch):
    _start_on_A(fworld)
    fworld.morph("B")
    orig = fworld.server

    def boom(tool, kw):
        if tool == "set_aircraft_parameters":
            raise RuntimeError("watchdog: call abandoned")
        return orig(tool, kw)
    monkeypatch.setattr(fworld, "server", boom)
    point, why = dp.current_cruise_point()
    assert point is None and "watchdog" in why
    assert getattr(dp._call_ctx, "cruise_basis", None) is None


def test_l3_a_point_of_another_design_than_the_set_returned_is_not_used(fworld):
    _start_on_A(fworld)
    fworld.morph("B")
    fworld.point_fp = "fp-someone-else"
    point, why = dp.current_cruise_point()
    assert point is None and "not the design the framework just applied" in why
    assert dp._design_state.data_store["aviary_cruise_point"]["_design_basis"] is None


def test_l3_the_point_is_stamped_after_a_successful_set_with_its_fingerprint(fworld):
    _start_on_A(fworld)
    fworld.morph("B")
    point, why = dp.current_cruise_point()
    assert point is not None and point["_design_basis"] == dp.design_basis()
    req = dp._design_state.data_store["cruise_point_request"]
    assert req["applied_design_fingerprint"] == point["design_fingerprint"]


def test_l3_without_a_measured_wing_the_set_is_still_sent_first(fworld):
    dp._design_state.data_store.pop("geometry_wing")
    n = len(fworld.calls)
    point, why = dp.current_cruise_point()
    assert [t for t, _ in fworld.calls[n:]] == ["set_aircraft_parameters", "get_cruise_point"]
    assert point is not None and point["_design_basis"] == dp.design_basis()


def test_l3_no_setter_registered_no_point(fworld, monkeypatch):
    monkeypatch.setitem(dp._registered_tools, "set_aircraft_parameters", None)
    monkeypatch.delitem(dp._registered_tools, "set_aircraft_parameters")
    point, why = dp.current_cruise_point()
    assert point is None and "no set_aircraft_parameters" in why


# ---------------------------------------------------------------- L4
class CycleWorld(World):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.cycle_mode = "ok"

    def server(self, tool, kw):
        if tool == "run_cycle" and self.cycle_mode == "error":
            return {"error": {"type": "AnalysisError", "message": "Newton failed", "details": None}}
        if tool == "run_cycle" and self.cycle_mode == "wrong_names":
            return {"success": False, "model_ran": True, "converged": True, "outputs": {"SFC": None},
                    "missing_outputs": ["SFC"], "messages": ["'SFC' is not an output"]}
        if tool == "get_outputs":
            return {"values": {"perf.Fn": [self.cycle_values.get("Fn_DES", 5900.0)], "perf.TSFC": [0.55]}}
        return super().server(tool, kw)


@pytest.fixture
def cworld(world, tmp_path):
    w = CycleWorld(tmp_path)
    w.__dict__.update({k: v for k, v in world.__dict__.items() if k != "cycle_mode"})
    from tests.test_b340_review_fixes import _Tool

    dp.register_tools([_Tool(w, n) for n in ("set_aircraft_parameters", "get_cruise_point", "set_inputs")])
    return w


def test_l4_a_failed_run_cycle_does_not_reopen_the_get_outputs_escape(cworld):
    _start_on_A(cworld)
    key_a = dp._design_state.data_store["prop_cycle_key"]
    cworld.morph("B")
    cworld.call("estimate_mass", cpacs_file_path=str(cworld.cpacs))
    cworld.call("set_inputs", session_id="pc", values={})
    assert dp._design_state.data_store["prop_cycle_converged"] is None          # reset by the sizing
    cworld.cycle_mode = "error"
    cworld.call("run_cycle", session_id="pc")
    store = dp._design_state.data_store
    assert store["cycle_sizing"]["ran"] is False and store["prop_cycle_converged"] is False
    cworld.call("get_outputs", session_id="pc", names=["perf.Fn"])
    assert store["prop_cycle_key"] == key_a and not dp.engine_is_current()       # nothing captured for B
    cworld.cycle_mode = "ok"
    cworld.call("run_cycle", session_id="pc")
    assert dp.engine_is_current()


def test_l4_a_failed_run_after_a_good_one_unmarks_the_run(cworld):
    _start_on_A(cworld)
    cworld.morph("B")
    cworld.call("estimate_mass", cpacs_file_path=str(cworld.cpacs))
    cworld.call("set_inputs", session_id="pc", values={})
    cworld.call("run_cycle", session_id="pc")
    assert dp._design_state.data_store["cycle_sizing"]["ran"] is True
    cworld.cycle_mode = "error"
    cworld.call("run_cycle", session_id="pc")
    assert dp._design_state.data_store["cycle_sizing"]["ran"] is False


def test_l4_wrong_output_names_still_count_as_a_run_b129(cworld):
    _start_on_A(cworld)
    cworld.morph("B")
    cworld.call("estimate_mass", cpacs_file_path=str(cworld.cpacs))
    cworld.call("set_inputs", session_id="pc", values={})
    cworld.cycle_mode = "wrong_names"
    cworld.call("run_cycle", session_id="pc")
    assert not dp.engine_is_current()
    cworld.call("get_outputs", session_id="pc", names=["perf.Fn", "perf.TSFC"])
    assert dp.engine_is_current()
    assert coupling.get_var(dp._design_state, "prop.fn_lbf") == pytest.approx(_expected(cworld))


# ---------------------------------------------------------------- L5 / L8
def test_l5_trajectory_and_constraints_without_flown_are_held_only(plain):
    store = plain.data_store
    for tool in ("get_trajectory", "check_constraints"):
        store.pop("aviary_fingerprint_ambiguous", None)
        _intercept(tool, {"success": True, "session_id": "av", "design_fingerprint": f"H-{tool}"}, sid="av")
        assert dp.aviary_held_fingerprint("av") == f"H-{tool}"
        assert "aviary_fingerprint_ambiguous" not in store
        assert "av" not in (store.get("aviary_flown_fingerprint_by_session") or {})


def test_l5_with_flown_both_are_recorded_and_get_results_without_it_stays_ambiguous(plain):
    store = plain.data_store
    _intercept("get_trajectory", {"success": True, "session_id": "av", "design_fingerprint": "Y",
                                  "flown_design_fingerprint": "X"}, sid="av")
    assert dp.aviary_held_fingerprint("av") == "Y" and store["aviary_flown_fingerprint_by_session"]["av"] == "X"
    _intercept("get_results", {"success": True, "session_id": "av", "design_fingerprint": "Y"}, sid="av")
    assert dp.aviary_held_fingerprint("av") is None and store.get("aviary_fingerprint_ambiguous")


def test_l8_create_session_is_filed_under_its_own_session(plain):
    _intercept("set_aircraft_parameters", {"success": True, "design_fingerprint": "A"}, sid="av")
    _intercept("create_session", {"success": True, "session_id": "new1", "design_fingerprint": "N"}, sid="av")
    assert dp.aviary_held_fingerprint("av") == "A" and dp.aviary_held_fingerprint("new1") == "N"


def test_l8_error_and_unconverged_replies_are_not_flown(plain):
    store = plain.data_store
    _intercept("run_simulation", {"success": False, "error_code": "INVALID_TIMEOUT", "error": "1 to 1500 s",
                                  "design_fingerprint": "A"}, sid="av")
    _intercept("run_simulation", {"success": False, "error_code": "RUN_STILL_STOPPING", "error": "wait",
                                  "design_fingerprint": "A"}, sid="av")
    assert "av" not in (store.get("aviary_flown_fingerprint_by_session") or {})
    _intercept("run_simulation", {"success": False, "error_code": "MISSION_NOT_CONVERGED", "error": "no valid result",
                                  "session_id": "av", "converged": False, "design_fingerprint": "A",
                                  "flown_design_fingerprint": "A"}, sid="av")
    assert "av" not in (store.get("aviary_flown_fingerprint_by_session") or {})
    assert store["aviary_unconverged_or_failed_fingerprint_by_session"]["av"] == "A"
    assert dp.aviary_held_fingerprint("av") == "A"
    _intercept("run_simulation", {"success": True, "session_id": "av", "converged": True, "design_fingerprint": "A",
                                  "flown_design_fingerprint": "A"}, sid="av")
    assert store["aviary_flown_fingerprint_by_session"]["av"] == "A"


# ---------------------------------------------------------------- L6
def _flown(link_dir, mtom):
    link_dir.mkdir(parents=True, exist_ok=True)
    (link_dir / "design_flown.json").write_text(json.dumps({
        "structures": {"sizing_converged": True, "mtom_kg": mtom},
        "engine_deck": {"file": "/decks/d.deck"}, "mission_applied": {"Aircraft.Engine.SCALE_FACTOR": 1.3}}))


def test_l6_resume_counts_a_flown_link_whose_end_state_read_failed(tmp_path):
    from scripts.stat_batch_runner import replay_chain_on_resume, run_key

    combo = "mdo_f25_sequential"
    _flown(tmp_path / "repeat_000" / combo, 91000.0)
    _flown(tmp_path / "repeat_001" / combo, 92000.0)
    ckpt = {"completed": {
        run_key(0, combo): {"chain_end_params": {"Aircraft.Wing.AREA": 125.0}, "chain_cpacs_out": "/g/l1.xml",
                            "eval_classification": {"fuel_burned_kg": 16000.0}, "chain_integrator_text": "t1"},
        run_key(1, combo): {"chain_end_params": {}, "chain_cpacs_out": "/g/l2.xml",       # flew; end-state read failed
                            "eval_classification": {"fuel_burned_kg": 15800.0}, "chain_integrator_text": "t2"}},
        "failed": {run_key(2, combo): {"chain_integrator_text": "t3 (failed)"}}}
    start = {"chain_params": {"Aircraft.Wing.AREA": 122.8}, "chain_feedback": None, "chain_start_fuel": None,
             "chain_kb_seed": None, "chain_cpacs": None, "chain_integrator": None, "chain_design_space": None,
             "chain_design_dir": "", "chain_flown_dir": "", "chain_flown_cpacs": None}
    chain = replay_chain_on_resume(ckpt, combo, 4, tmp_path, start)
    assert chain["chain_flown_dir"] == str(tmp_path / "repeat_001" / combo)      # was repeat_000 before L6
    assert chain["chain_flown_cpacs"] == "/g/l2.xml" and chain["chain_cpacs"] == "/g/l2.xml"
    assert chain["chain_design_dir"] == str(tmp_path / "repeat_001" / combo)
    assert chain["chain_params"] == {"Aircraft.Wing.AREA": 125.0}                # no end-state: params not advanced
    assert chain["chain_start_fuel"] == 15800.0
    assert chain["chain_integrator"] == "t3 (failed)"                            # a failed link hands on its text


def test_l6_an_infeasible_links_fuel_is_not_the_start_fuel(tmp_path):
    from scripts.stat_batch_runner import replay_chain_on_resume, run_key

    combo = "c"
    ckpt = {"completed": {run_key(0, combo): {"chain_end_params": {"x": 1.0},
                                              "eval_classification": {"fuel_burned_kg": 15000.0}},
                          run_key(1, combo): {"chain_end_params": {"x": 2.0}, "infeasible_design": {"why": "ZERO_FUEL"},
                                              "eval_classification": {"fuel_burned_kg": 1.0}}}, "failed": {}}
    start = {k: None for k in ("chain_params", "chain_feedback", "chain_start_fuel", "chain_kb_seed", "chain_cpacs",
                               "chain_integrator", "chain_design_space", "chain_flown_cpacs")}
    start.update(chain_design_dir="", chain_flown_dir="")
    chain = replay_chain_on_resume(ckpt, combo, 2, tmp_path, start)
    assert chain["chain_start_fuel"] == 15000.0 and chain["chain_params"] == {"x": 2.0}
    assert chain["chain_flown_dir"] == ""                                        # no design_flown.json: nothing flew
