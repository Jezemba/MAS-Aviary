"""B363: validation 1 (2026-11-18) blockers V1 and V5, and V9 / V11, replayed from the REAL recorded records.

tests/fixtures/v1a_validation1_records.json is extracted read-only from the validation runs by
tests/fixtures/v1a_extract_validation1_records.py (ws8 /data/Jessica/final_val, ws5/ws6/ws3 copies): every
run_simulation the knowledge bases recorded as a success, the KBs of S_GR / N_GR / O_GR / O_IF links, and the server
replies (tool_calls logs) of S_GR link 1, N_GR link 1 and O_IF link 2.

V1   the graph router read run_simulation's fuel from the KB record's capped outputs; the grown reply lost `summary`,
     so not one of the recorded missions counted as flown (rule 9 looped S_GR / O_GR until the budget).
V5   the design fingerprint changed on a no-op morph (N_GR link 1) and on re-opening the same geometry (O_IF link 2),
     while the duplicate guard refused the re-run as ALREADY_DONE: contradictory refusals, a livelock.
V9   set_aircraft_parameters' reply listed the derived SPAN under `applied`; agents sent it back, aviary refused, and
     rule 5 routed that refusal to geometry (N_GR link 2 went to COMPLEX).
V11  an ALREADY_DONE refusal of run_su2_solver was read as "did not succeed" (O_GR link 1: aero visited twice).

No network, no API, no MCP servers.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import src.tools.data_plane as dp
from src.coordination import graph_router as gr
from src.coordination.design_state import DesignState
from src.tools import coupling, coupling_contract as cc, duplicate_guard as dg, knowledge_base as kbm

FIXTURE = Path(__file__).parent / "fixtures" / "v1a_validation1_records.json"
REC = json.loads(FIXTURE.read_text())
LINKS = REC["links"]


def _calls(link: str, tool: str) -> list[dict]:
    out = []
    for c in LINKS[link].get("tool_calls") or []:
        if c.get("tool") == tool:
            c = dict(c)
            for k in ("args", "result"):
                if isinstance(c.get(k), str):
                    try:
                        c[k] = json.loads(c[k])
                    except ValueError:
                        pass
            out.append(c)
    return out


def _kb_rows(link: str) -> list[dict]:
    return LINKS[link]["kb"]


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {
        "run_su2_solver": "su2", "run_cycle": "pycycle", "estimate_mass": "mass", "run_simulation": "aviary",
        "open_cpacs": "tigl", "set_high_level_parameters": "tigl", "export_cpacs": "tigl", "close_cpacs": "tigl",
        "generate_volume_mesh": "tigl", "create_su2_session": "su2", "get_results": "aviary"})
    kbm.configure_run(str(tmp_path / "kb.jsonl"), 0, 1, None)
    yield
    kbm.configure_run(None, 0, 1, None)


# ======================================================================================================================
# V1 -- every consumer that decides from a KB record reads the uncapped decision digest
# ======================================================================================================================

def test_v1_reproduced_no_recorded_mission_is_flown_from_its_capped_outputs():
    """The pinned reading (capped outputs, no digest): 0 of the 45 converged missions validation 1 recorded."""
    missions = REC["missions"]
    assert len(missions) == 45
    assert all(m["record"]["outputs"].get("converged") is True for m in missions)
    lost = [m for m in missions if "summary" not in m["record"]["outputs"]]
    assert len(lost) == 45 and all(m["record"]["outputs"].get("_truncated_keys") for m in lost)
    # A recorded entry has no digest; read as before B363 (its outputs) none of them is flown.
    assert sum(gr._flown(m["record"]) is not None for m in missions) == 0


def _record(tool: str, reply: dict, status: str = "success") -> dict:
    kb = kbm.get_kb()
    return kb.append(tool=tool, server="aviary", agent="simulation_executor", role="", status=status,
                     inputs={"session_id": reply.get("session_id")}, outputs=copy.deepcopy(reply))


def test_v1_the_real_reply_recorded_now_is_flown_by_every_consumer(tmp_path):
    replies = [c["result"] for c in _calls("val_seq_graph/repeat_000", "run_simulation")]
    assert replies and all(len(json.dumps(r)) > kbm._OUTPUTS_CAP_CHARS for r in replies)
    for reply in replies:
        entry = _record("run_simulation", reply)
        fuel = reply["summary"]["fuel_burned_kg"]
        # The digest holds the fuel whatever the cap kept.
        assert entry["result_digest"]["fuel_burned_kg"] == fuel
        assert entry["result_digest"]["converged"] is True and entry["result_digest"]["valid_result"] is True
        # graph router (sequential + orchestrated graph) and the networked board's suggestion
        assert gr._flown(entry) == fuel
        d = gr.route_after_simulation([entry], {}, None, gr.SIMPLE)
        assert (d.next_node, d.rule) == (gr.REVIEW, "flow")
    # The runner's mission metrics (B179) and measured fuel (B102) read the file the KB wrote.
    from scripts.stat_batch_runner import measured_fuel_from_kb
    from src.runners.batch_runner import _mission_metrics_from_kb

    assert measured_fuel_from_kb(tmp_path / "kb.jsonl", 1) == replies[-1]["summary"]["fuel_burned_kg"]
    metrics = _mission_metrics_from_kb()
    assert metrics["flown"] is True and metrics["fuel_burned_kg"] == replies[-1]["summary"]["fuel_burned_kg"]
    assert metrics["gtow_kg"] == replies[-1]["summary"]["gtow_kg"]


def test_v1_even_a_record_whose_cap_drops_the_summary_is_flown():
    """The digest does not depend on the cap: force the summary out of the recorded outputs."""
    reply = copy.deepcopy(_calls("val_seq_graph/repeat_000", "run_simulation")[0]["result"])
    reply = {"padding": "x" * 190, **{f"k{i}": "y" * 190 for i in range(12)}, **reply}
    entry = _record("run_simulation", reply)
    assert "summary" not in entry["outputs"] and entry["outputs"]["_truncated_keys"]
    assert gr._flown(entry) == reply["summary"]["fuel_burned_kg"]


def test_v1_the_cap_skips_a_large_key_and_keeps_the_small_ones_after_it():
    reply = _calls("val_seq_graph/repeat_000", "run_simulation")[0]["result"]
    entry = _record("run_simulation", reply)
    assert entry["outputs"]["summary"]["fuel_burned_kg"] == reply["summary"]["fuel_burned_kg"]
    assert len(json.dumps(entry["outputs"])) <= kbm._OUTPUTS_CAP_CHARS
    assert entry["outputs"]["_dropped_keys"]


def test_v1_missing_list_and_long_error_text_survive_for_rule_5():
    """N_GR link 1 seq 35: a MISSING_REQUIRED_PARAMETERS refusal; its error text is > 200 chars (recorded as '<N
    chars>' in outputs)."""
    row = next(r for r in _kb_rows("val_net_graph/repeat_000")
               if r["seq"] == 35 and r["outputs"].get("error_code") == "MISSING_REQUIRED_PARAMETERS")
    refusal = {"success": False, "error_code": "MISSING_REQUIRED_PARAMETERS", "error": "x" * 900,
               "missing": row["outputs"]["missing"] if isinstance(row["outputs"].get("missing"), list) else
               [{"parameter": "", "variable": "aero.cl_cruise", "producer": "su2", "state": "stale"}]}
    entry = _record("run_simulation", refusal, status="refused")
    assert entry["outputs"]["error"].startswith("<")           # what the capped record holds
    assert len(entry["result_digest"]["error"]) == kbm._DIGEST_ERROR_CHARS
    d = gr.route_after_simulation([entry], {}, None, gr.SIMPLE)
    assert (d.next_node, d.rule) == (gr.AERO, "5")


def test_v1_networked_board_posts_no_suggestion_after_a_real_flight(monkeypatch):
    from src.coordination.blackboard import Blackboard
    from src.coordination.board_graph import BoardGraph

    board = Blackboard(claiming_mode="hard")
    bg = BoardGraph(board, preinit=False)
    bg.start(None)
    _record("run_simulation", _calls("val_seq_graph/repeat_000", "run_simulation")[0]["result"])
    assert bg.step() is True
    assert not [e for e in bg.log if e.get("event") == "suggestion"], bg.log


def test_v1_previous_link_summary_reaches_the_next_links_digest():
    seed = {"summary": "S" * 1200, "end_state": {"Aircraft.Wing.AREA": 122.8287}}
    kbm.configure_run(kbm._run_config["path"], 1, 1, seed)
    kb = kbm.KnowledgeBase()
    assert kb.entries[0]["outputs"]["summary"] == "<1200 chars>"
    assert kb.entries[0]["result_digest"]["previous_link_summary"] == "S" * 1200
    assert "PREVIOUS LINK: " + "S" * 100 in kbm.digest(kb.entries)


# ======================================================================================================================
# V5 -- one content-based notion of "same design", shared by the coupling contract and the duplicate guard
# ======================================================================================================================

def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


def _open(source: str, reply: dict, seq: int = 1):
    args = {"source_type": "path", "source": source}
    fp = dg.fingerprint("open_cpacs", args)
    entry = kbm.get_kb().append(tool="open_cpacs", server="tigl", agent="geometry", role="", status="success",
                                inputs=args, outputs=reply, design_fingerprint=fp)
    dg.observe("open_cpacs", args, json.dumps(reply), "success", entry)
    return reply["session_id"]


def _observe(tool: str, args: dict, reply: dict):
    entry = kbm.get_kb().append(tool=tool, server="tigl", agent="geometry", role="", status="success",
                                inputs=args, outputs=reply)
    dg.observe(tool, args, json.dumps(reply), "success", entry)


def _produce(tool: str, args: dict, producer: str, var_values: dict) -> dict:
    """A producer call as the middleware runs it: guard check, start fingerprint, capture, record, release."""
    fp = dg.fingerprint(tool, args)
    assert dg.check(tool, fp, "agent") is None
    cc.note_call_start(tool)
    for var, value in var_values.items():
        coupling.put_var(dp.get_design_state(), var, value, source_tool=tool)
    cc.note_capture(producer)
    entry = kbm.get_kb().append(tool=tool, server="x", agent="agent", role="", status="success", inputs=args,
                                outputs={"status": "success", **{k.split(".")[-1]: v for k, v in var_values.items()}},
                                design_fingerprint=fp)
    dg.release(tool, fp)
    return entry


def _su2_session(sid="su2-1"):
    _observe("create_su2_session", {"initial_mesh": "M" * 400}, {"session_id": sid})
    return sid


MASS = {"mass.wing_kg": 7958.79, "mass.mtom_kg": 90298.64}
AERO = {"aero.cl_cruise": 0.6461, "aero.cd_cruise": 0.0262}


def test_v5_ngr_link1_a_no_op_morph_is_the_same_design(tmp_path):
    """N_GR link 1: seq 24 set_high_level_parameters with the wing's own values (tigl: before == after, nothing
    written, rebuilt true). Then: SU2 refused ALREADY_DONE (seq 29) while run_simulation was refused for a STALE
    SU2 solve (seq 35, 36, 45, 50, 54) -- and a redundant 3-min solve on a new session."""
    link = "val_net_graph/repeat_000"
    opened = _calls(link, "open_cpacs")[0]
    sid = _open(_write(tmp_path / "D150_simple.xml", "<cpacs>D150</cpacs>"), opened["result"])
    mass_file = _write(tmp_path / "mass" / "D150_simple.xml", "<cpacs>D150</cpacs>")
    _produce("estimate_mass", {"cpacs_file_path": mass_file}, "mass", MASS)
    su2 = _su2_session()
    _produce("run_su2_solver", {"session_id": su2}, "su2", AERO)
    before = cc.current_design_fingerprint()
    epoch_before = dg._state()["geometry_epoch"][sid]

    no_op = _calls(link, "set_high_level_parameters")[0]
    assert no_op["result"]["geometry_morph"]["before"] == no_op["result"]["geometry_morph"]["after"]
    assert no_op["result"]["geometry_morph"]["rebuilt"] is True
    _observe("set_high_level_parameters", no_op["args"], no_op["result"])

    assert dg._state()["geometry_epoch"][sid] == epoch_before + 1     # what used to make it a new design
    assert cc.current_design_fingerprint() == before
    assert cc.status(cc.AERO_SOLVE) == "ok" and cc.status(cc.WING_MASS) == "ok" and cc.status(cc.MTOM) == "ok"
    # ... and the guard agrees: SU2 and mass for this design exist, so their re-runs are refused with result_exists.
    refusal = dg.check("run_su2_solver", dg.fingerprint("run_su2_solver", {"session_id": su2}), "agent_2")
    assert refusal["error_code"] == "ALREADY_DONE" and refusal["result_exists"] is True


def test_v5_ngr_link1_a_real_morph_is_a_new_design_and_the_guard_lets_the_rerun_through(tmp_path):
    """The other half of agreement: seq 91 reshaped the wing to AR 9.5 / 125 m^2. The contract counts SU2 and mass
    stale; the duplicate guard must then let the re-runs run (it refused the SU2 re-run at seq 29 / 46)."""
    link = "val_net_graph/repeat_000"
    sid = _open(_write(tmp_path / "D150_simple.xml", "<cpacs>D150</cpacs>"), _calls(link, "open_cpacs")[0]["result"])
    mass_file = _write(tmp_path / "mass" / "D150_simple.xml", "<cpacs>D150</cpacs>")
    _produce("estimate_mass", {"cpacs_file_path": mass_file}, "mass", MASS)
    su2 = _su2_session()
    _produce("run_su2_solver", {"session_id": su2}, "su2", AERO)
    before = cc.current_design_fingerprint()
    morph = _calls(link, "set_high_level_parameters")[1]
    assert morph["result"]["geometry_morph"]["before"] != morph["result"]["geometry_morph"]["after"]
    _observe("set_high_level_parameters", dict(morph["args"], session_id=sid), morph["result"])
    assert cc.current_design_fingerprint() != before
    assert cc.status(cc.AERO_SOLVE) == "stale" and cc.status(cc.WING_MASS) == "stale"
    assert cc.check("run_simulation", {"session_id": "a"})["missing"][0]["state"] == "stale"
    # Same SU2 session and mesh, same mass file: before B363 both were refused ALREADY_DONE.
    assert dg.check("run_su2_solver", dg.fingerprint("run_su2_solver", {"session_id": su2}), "agent_1") is None
    assert dg.check("estimate_mass", dg.fingerprint("estimate_mass", {"cpacs_file_path": mass_file}), "agent_1") is None
    assert kbm.get_kb().metrics["duplicate_prior_not_current"]["agent_1"] == {"run_su2_solver": 1, "estimate_mass": 1}


def test_v5_a_morph_back_to_the_start_is_the_start_design(tmp_path):
    link = "val_net_graph/repeat_000"
    sid = _open(_write(tmp_path / "D150_simple.xml", "<cpacs>D150</cpacs>"), _calls(link, "open_cpacs")[0]["result"])
    start = cc.current_design_fingerprint()
    morph = _calls(link, "set_high_level_parameters")[1]["result"]
    _observe("set_high_level_parameters", {"session_id": sid, "component_uid": "Wing1"}, morph)
    back = copy.deepcopy(morph)
    back["geometry_morph"]["before"], back["geometry_morph"]["after"] = (morph["geometry_morph"]["after"],
                                                                         morph["geometry_morph"]["before"])
    _observe("set_high_level_parameters", {"session_id": sid, "component_uid": "Wing1"}, back)
    assert cc.current_design_fingerprint() == start


def test_v5_oif_link2_reopening_the_exported_geometry_is_the_same_design(tmp_path):
    """O_IF link 2 (ws6, >3 h): opened link 1's geometry_end.xml (seq 2), exported it (seq 8), closed the session
    (seq 9, tigl auto-export); later the session was gone, so the agent opened the EXPORT (seq 47). The fingerprint
    changed (new session, other path / mtime): run_cycle refused 4x for a mass "from an EARLIER geometry" (seq 60-63)
    while estimate_mass was refused ALREADY_DONE (seq 55; the guard hashes file CONTENT, the same)."""
    link = "val_orch_iter/repeat_001"
    opens, exports, closes = (_calls(link, t) for t in ("open_cpacs", "export_cpacs", "close_cpacs"))
    rows = {r["seq"]: r for r in _kb_rows(link)}
    assert rows[55]["outputs"]["error_code"] == "ALREADY_DONE"
    assert [rows[s]["outputs"]["error_code"] for s in (60, 61, 62, 63)] == ["MISSING_REQUIRED_PARAMETERS"] * 4
    assert rows[15]["design_fingerprint"] == rows[55]["design_fingerprint"]       # the guard: same content

    link1_geometry = _write(tmp_path / "link1" / "geometry_end.xml", "<cpacs>link 1 flown geometry</cpacs>")
    exported_text = "<cpacs>link 1 flown geometry, re-serialised by TiXI</cpacs>"
    sid1 = _open(link1_geometry, opens[0]["result"])
    export_path = _write(tmp_path / "export" / "geometry_end.xml", exported_text)
    _observe("export_cpacs", {"session_id": sid1, "output_path": export_path},
             dict(exports[0]["result"], cpacs_file_path=export_path))
    auto1 = _write(tmp_path / "auto" / f"cpacs_autoexport_{sid1}.xml", exported_text)
    mass1 = _write(tmp_path / "mass1" / f"cpacs_autoexport_{sid1}.xml", exported_text)
    _observe("close_cpacs", {"session_id": sid1}, dict(closes[0]["result"], cpacs_file_path=auto1))
    _produce("estimate_mass", {"cpacs_file_path": mass1}, "mass", MASS)                          # seq 15
    design = cc.current_design_fingerprint()

    sid2 = _open(export_path, opens[1]["result"], seq=47)                                        # seq 47
    assert sid2 != sid1
    assert cc.current_design_fingerprint() == design
    assert dg._state()["geometry_identity"][sid2]["base"] == design
    # run_cycle (seq 60-63) now runs: the mass on record is this design's.
    assert cc.check("run_cycle", {"session_id": "c"}) is None
    # estimate_mass on the new session's auto-export (seq 55) is refused -- consistently: the result exists.
    mass2 = _write(tmp_path / "mass2" / f"cpacs_autoexport_{sid2}.xml", exported_text)
    refusal = dg.check("estimate_mass", dg.fingerprint("estimate_mass", {"cpacs_file_path": mass2}), "structures")
    assert refusal["error_code"] == "ALREADY_DONE" and refusal["result_exists"] is True


def test_v5_reopening_the_same_file_in_a_new_session_is_the_same_design(tmp_path):
    src = _write(tmp_path / "D150_simple.xml", "<cpacs>D150</cpacs>")
    _open(src, {"session_id": "s1"})
    first = cc.current_design_fingerprint()
    _observe("close_cpacs", {"session_id": "s1"}, {"success": True})
    _open(src, {"session_id": "s2"})
    assert cc.current_design_fingerprint() == first
    other = _write(tmp_path / "other.xml", "<cpacs>another aircraft</cpacs>")
    _open(other, {"session_id": "s3"})
    assert cc.current_design_fingerprint() != first


def test_v5_guard_and_contract_never_both_refuse(tmp_path):
    """Agreement by construction, over the states the replays pass through: whenever the guard refuses a producer's
    re-run as a finished result, the contract counts that producer's result as current."""
    link = "val_net_graph/repeat_000"
    src = _write(tmp_path / "D150_simple.xml", "<cpacs>D150</cpacs>")
    mass_file = _write(tmp_path / "mass" / "D150_simple.xml", "<cpacs>D150</cpacs>")
    states = []
    sid = _open(src, {"session_id": "g1"})
    _produce("estimate_mass", {"cpacs_file_path": mass_file}, "mass", MASS)
    su2 = _su2_session()
    _produce("run_su2_solver", {"session_id": su2}, "su2", AERO)
    for step in ("start", "no-op", "morph", "remass", "back"):
        if step == "no-op":
            _observe("set_high_level_parameters", {"session_id": sid, "component_uid": "Wing1"},
                     _calls(link, "set_high_level_parameters")[0]["result"])
        elif step == "morph":
            _observe("set_high_level_parameters", {"session_id": sid, "component_uid": "Wing1"},
                     _calls(link, "set_high_level_parameters")[1]["result"])
        elif step == "remass":
            mass_file = _write(tmp_path / "mass" / "morphed.xml", "<cpacs>morphed</cpacs>")
            _produce("estimate_mass", {"cpacs_file_path": mass_file}, "mass", MASS)
        elif step == "back":
            m = copy.deepcopy(_calls(link, "set_high_level_parameters")[1]["result"])
            m["geometry_morph"]["before"], m["geometry_morph"]["after"] = (m["geometry_morph"]["after"],
                                                                           m["geometry_morph"]["before"])
            _observe("set_high_level_parameters", {"session_id": sid, "component_uid": "Wing1"}, m)
        for tool, args, inputs in (("estimate_mass", {"cpacs_file_path": mass_file}, (cc.WING_MASS, cc.MTOM)),
                                   ("run_su2_solver", {"session_id": su2}, (cc.AERO_SOLVE,))):
            dg._state()["refused_keys"].clear()
            out = dg.check(tool, dg.fingerprint(tool, args), "probe")
            if out is None:
                dg.release(tool, dg.fingerprint(tool, args))
            refused = bool(out and out.get("error_code") == "ALREADY_DONE" and out.get("result_exists"))
            current = all(cc.status(r) == "ok" for r in inputs)
            states.append((step, tool, refused, current))
            assert not (refused and not current), states
    # The replay passes through both kinds of state.
    assert any(r for _, _, r, _ in states) and any(not c for _, _, _, c in states)


def test_v5_the_mesh_is_refused_only_while_the_held_mesh_is_this_designs(tmp_path):
    src = _write(tmp_path / "D150_simple.xml", "<cpacs>D150</cpacs>")
    sid = _open(src, {"session_id": "g1"})
    args = {"session_id": sid, "output_format": "su2"}
    fp = dg.fingerprint("generate_volume_mesh", args)
    assert dg.check("generate_volume_mesh", fp, "geo") is None
    dp.get_design_state().data_store["generate_volume_mesh__mesh_base64"] = "A" * 500
    entry = kbm.get_kb().append(tool="generate_volume_mesh", server="tigl", agent="geo", role="", status="success",
                                inputs=args, outputs={"mesh_base64": "generate_volume_mesh__mesh_base64"},
                                design_fingerprint=fp)
    dg.release("generate_volume_mesh", fp)
    dg.observe("generate_volume_mesh", args, "{}", "success", entry)
    # The same design in a new session: the same mesh fingerprint, refused (the held mesh is this design's).
    _open(src, {"session_id": "g2"})
    again = dg.check("generate_volume_mesh", dg.fingerprint("generate_volume_mesh", dict(args, session_id="g2")), "p")
    assert again["error_code"] == "ALREADY_DONE" and again["result_exists"] is True
    # Another mesh now held (another design's): the first design's mesh is not what the ref points at -> runs.
    dp.get_design_state().data_store["generate_volume_mesh__mesh_base64"] = "B" * 500
    dg._state()["refused_keys"].clear()
    assert dg.check("generate_volume_mesh", dg.fingerprint("generate_volume_mesh", dict(args, session_id="g2")),
                    "p") is None


# ======================================================================================================================
# V11 -- ALREADY_DONE of the node's key tool means the result exists
# ======================================================================================================================

def test_v11_ogr_link1_an_already_done_su2_solve_moves_on():
    """O_GR link 1 seq 13: run_su2_solver refused ALREADY_DONE (kb_seq 8): the router sent aero to retry."""
    row = next(r for r in _kb_rows("val_orch_graph/repeat_000") if r["seq"] == 13)
    assert row["tool"] == "run_su2_solver" and row["outputs"]["error_code"] == "ALREADY_DONE"
    d = gr.route_after_node(gr.AERO, [row], "", gr.MODERATE, 0)
    assert (d.next_node, d.rule) == (gr.MASS, "flow")


def test_v11_a_call_still_running_is_not_a_result(tmp_path):
    su2 = _su2_session()
    fp = dg.fingerprint("run_su2_solver", {"session_id": su2})
    assert dg.check("run_su2_solver", fp, "aero_1") is None                  # in flight
    running = dg.check("run_su2_solver", fp, "aero_2")
    assert running["error_code"] == "ALREADY_DONE" and running["result_exists"] is False
    entry = kbm.get_kb().append(tool="run_su2_solver", server="su2", agent="aero_2", role="", status="refused",
                                inputs={"session_id": su2}, outputs=running)
    d = gr.route_after_node(gr.AERO, [entry], "", gr.MODERATE, 0)
    assert (d.next_node, d.rule) == (gr.AERO, "1")


# ======================================================================================================================
# V9 -- the derived SPAN is not offered back as settable, and its refusal does not escalate
# ======================================================================================================================

def test_v9_the_reply_keeps_span_apart_as_read_only():
    reply = next(c["result"] for c in _calls("val_seq_graph/repeat_000", "set_aircraft_parameters")
                 if c["result"].get("success") is True)
    assert any(i.get("name") == "Aircraft.Wing.SPAN" and i.get("derived") for i in reply["applied"])
    out = json.loads(dp.intercept_response("set_aircraft_parameters", json.dumps(reply)))
    assert all(i.get("name") != "Aircraft.Wing.SPAN" for i in out["applied"])
    assert out["derived_read_only"]["values"]["Aircraft.Wing.SPAN"]["new_value"] == 33.9127
    assert "Do not send" in out["derived_read_only"]["note"]
    store = dp.get_design_state().data_store
    assert store["design_params_derived"] == ["Aircraft.Wing.SPAN"]          # still captured as derived
    assert store["design_params_applied"]["Aircraft.Wing.SPAN"] == 33.9127


def test_v9_ngr_link2_a_span_refusal_does_not_route_to_geometry():
    """N_GR link 2 seq 13: READ_ONLY_PARAMETER for SPAN -> rule 5 -> geometry_engineer at COMPLEX."""
    row = next(r for r in _kb_rows("val_net_graph/repeat_001") if r["outputs"].get("error_code") == "READ_ONLY_PARAMETER")
    assert gr._upstream_from_errors([row]) is None
    d = gr.route_after_node(gr.MISSION, [row], "", gr.SIMPLE, 0)
    assert (d.next_node, d.level, d.rule) == (gr.MISSION, gr.SIMPLE, "1")
    sim = gr.route_after_simulation([row], {}, None, gr.SIMPLE)
    assert sim.next_node != gr.GEOMETRY and sim.level != gr.COMPLEX


# ======================================================================================================================
# V1 audit -- the staged pipeline's "validation exhausted" check read the 2,000-character message copy
# ======================================================================================================================

def test_v1_staged_mission_stage_is_not_validation_exhausted_by_a_cut_reply():
    """S_SP, O_SP (both links, both validation runs): result.json mission_architect metadata early_abort
    "validation_exhausted" although set_aircraft_parameters answered valid: true -- its ~3.5 kB reply was cut at
    2,000 characters in the message copy and did not parse. The check reads the stage's KB records now."""
    from src.coordination.history import ToolCallRecord
    from src.coordination.staged_pipeline_handler import StagedPipelineHandler

    reply = next(c["result"] for c in _calls("val_seq_graph/repeat_000", "set_aircraft_parameters")
                 if c["result"].get("success") is True)
    assert reply["valid"] is True and len(json.dumps(reply)) > 2000
    tc = ToolCallRecord(tool_name="set_aircraft_parameters", inputs={}, output=json.dumps(reply)[:2000],
                        duration_seconds=0.0)
    assert StagedPipelineHandler._validation_exhausted([tc]) is True            # the message copy: the old verdict
    entry = kbm.get_kb().append(tool="set_aircraft_parameters", server="aviary", agent="mission_architect", role="",
                                status="success", inputs={}, outputs=reply)
    assert StagedPipelineHandler._validation_exhausted([tc], [entry]) is False
    refused = kbm.get_kb().append(tool="set_aircraft_parameters", server="aviary", agent="mission_architect",
                                  role="", status="failed", inputs={},
                                  outputs={"success": False, "valid": False, "error_code": "INVALID"})
    assert StagedPipelineHandler._validation_exhausted([tc], [refused]) is True
