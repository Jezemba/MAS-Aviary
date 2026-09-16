"""B81 step 3: once-only duplicate-work guard -- the handoff's section 3.1 table.

Fake MCP tools wrapped by the real middleware; each fake counts how often the
"server" was actually reached. No model, no server, no .env.
"""

import json
import os
import threading
import time

import pytest
from smolagents import Tool

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import duplicate_guard as dg
from src.tools import knowledge_base as kbm
from src.tools.agent_context import agent_scope
from src.tools.type_coercion import wrap_tool_with_middleware

BIG_MESH = "U1UyIE1FU0g=" * 60


def _coupled_inputs_available():
    """Pretend SU2, mass-mcp and pycycle have run for this design (B84).

    The aviary mission tools require their coupled inputs since B84, which is a different
    mechanism from the once-only guard under test here -- without this the calls below
    would be refused for a reason this test is not about.
    """
    from src.tools import coupling
    from src.tools.coupling_contract import note_capture

    state = dp.get_design_state()
    for name, value in (("aero.cl_cruise", 0.52), ("aero.cd_cruise", 0.0182),
                        ("mass.wing_kg", 8100.0), ("mass.mtom_kg", 72000.0),
                        ("prop.sfc_cruise", 0.58)):
        coupling.put_var(state, name, value, source_tool="test")
    for producer in ("su2", "mass", "pycycle"):
        note_capture(producer)
SERVERS = {"generate_volume_mesh": "tigl", "set_high_level_parameters": "tigl", "morph_wing": "tigl",
           "open_cpacs": "tigl", "close_cpacs": "tigl", "run_su2_solver": "su2", "create_su2_session": "su2",
           "set_mesh": "su2", "update_config_entries": "su2", "create_cycle_model": "pycycle",
           "estimate_mass": "mass", "set_aircraft_parameters": "aviary", "run_simulation": "aviary"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", dict(SERVERS))
    kbm.configure_run(None, 0, 1, None)
    dg.register_blackboard(None)
    yield


class Server:
    """Counts real calls per tool; responses are configurable per tool."""

    def __init__(self):
        self.calls = {}
        self.responses = {}
        self.delay = 0.0

    def tool(self, name, inputs):
        server = self

        class T(Tool):
            description = "fake"
            output_type = "string"

            def forward(self, **kw):
                server.calls[name] = server.calls.get(name, 0) + 1
                if server.delay:
                    time.sleep(server.delay)
                resp = server.responses.get(name, {"success": True})
                return json.dumps(resp(kw) if callable(resp) else resp)
        T.name = name
        T.inputs = {k: {"type": "any", "description": k, "nullable": True} for k in inputs}
        T.forward.__signature__ = None
        import inspect
        params = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)] + [
            inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, default=None) for k in inputs]
        T.forward.__signature__ = inspect.Signature(params)
        return wrap_tool_with_middleware(T())


@pytest.fixture
def srv():
    s = Server()
    s.responses["generate_volume_mesh"] = {"success": True, "mesh_base64": BIG_MESH}
    return s


def mesh_tool(srv):
    return srv.tool("generate_volume_mesh", ["session_id", "component_uid"])


def call(tool, agent, **kw):
    with agent_scope(agent):
        return json.loads(tool.forward(**kw))


# ---- the section 3.1 table ---------------------------------------------------

def test_row1_first_call_runs_and_is_recorded(srv):
    m = mesh_tool(srv)
    assert call(m, "agent_1", session_id="g")["success"] is True
    assert srv.calls["generate_volume_mesh"] == 1
    [e] = kbm.get_kb().entries
    assert e["agent"] == "agent_1" and e["design_fingerprint"].startswith("geo:")


def test_rows2_to_4_other_agents_get_one_refusal_each(srv):
    m = mesh_tool(srv)
    call(m, "agent_1", session_id="g")
    r = call(m, "agent_2", session_id="g")                                    # row 2
    assert r["error_code"] == "ALREADY_DONE" and r["done_by"] == "agent_1" and r["kb_seq"] == 1
    assert "agent_1 already did generate_volume_mesh" in r["error"]
    assert "generate_volume_mesh__mesh_base64" in r["error"]                  # where the result is
    assert "read_design_knowledge(tool='generate_volume_mesh')" in r["error"]
    assert srv.calls["generate_volume_mesh"] == 1                             # never reached the server
    assert call(m, "agent_2", session_id="g")["success"] is True              # row 3: goes through
    assert srv.calls["generate_volume_mesh"] == 2
    assert call(m, "agent_3", session_id="g")["error_code"] == "ALREADY_DONE"  # row 4
    met = kbm.get_kb().metrics
    assert met["duplicate_refusals"] == {"agent_2": {"generate_volume_mesh": 1}, "agent_3": {"generate_volume_mesh": 1}}
    assert met["duplicate_repeats_after_refusal"] == {"agent_2": {"generate_volume_mesh": 1}}
    assert "deliberate repeat" in kbm.get_kb().entries[2]["note"]


def test_row5_the_agent_that_did_it_is_refused_once_with_reason(srv):
    m = mesh_tool(srv)
    call(m, "agent_1", session_id="g")
    r = call(m, "agent_1", session_id="g")
    assert r["error_code"] == "ALREADY_DONE"
    assert r["error"].startswith("you (agent_1) already did generate_volume_mesh for this design")
    assert call(m, "agent_1", session_id="g")["success"] is True
    assert srv.calls["generate_volume_mesh"] == 2


def test_row6_a_changed_design_runs_normally(srv):
    m, geo = mesh_tool(srv), srv.tool("set_high_level_parameters", ["session_id", "component_uid", "updates"])
    call(m, "agent_1", session_id="g")
    call(geo, "agent_1", session_id="g", updates={"span": 40})
    assert call(m, "agent_2", session_id="g")["success"] is True
    assert call(m, "agent_1", session_id="g", component_uid="Wing2")["success"] is True   # different args
    assert srv.calls["generate_volume_mesh"] == 3


def test_row7_a_failed_or_refused_prior_does_not_count(srv):
    srv.responses["generate_volume_mesh"] = {"success": False, "error": "mesh_too_large"}
    m = mesh_tool(srv)
    assert call(m, "agent_1", session_id="g")["success"] is False
    srv.responses["generate_volume_mesh"] = {"success": True, "mesh_base64": BIG_MESH}
    assert call(m, "agent_2", session_id="g")["success"] is True
    assert srv.calls["generate_volume_mesh"] == 2


def test_row8_non_once_only_tools_are_never_guarded(srv):
    sp = srv.tool("set_aircraft_parameters", ["session_id", "parameters"])
    rs = srv.tool("run_simulation", ["session_id"])
    _coupled_inputs_available()      # B84: these two now require them; this test is about B81
    for agent in ("agent_1", "agent_1", "agent_2"):
        assert call(sp, agent, session_id="a", parameters={"x": 1})["success"] is True
        assert call(rs, agent, session_id="a")["success"] is True
    assert srv.calls == {"set_aircraft_parameters": 3, "run_simulation": 3}
    assert kbm.get_kb().metrics["duplicate_refusals"] == {}


def test_the_once_only_table_is_exactly_the_decided_list():
    assert set(dg.ONCE_ONLY_TOOLS) == {"generate_volume_mesh", "export_component_mesh", "run_su2_solver",
                                       "create_su2_session", "create_cycle_model", "open_cpacs", "estimate_mass"}
    never = {"set_high_level_parameters", "set_aircraft_parameters", "set_inputs", "configure_mission",
             "run_simulation", "run_cycle", "check_constraints", "final_answer", "get_results"}
    assert not never & set(dg.ONCE_ONLY_TOOLS)


# ---- concurrency ----------------------------------------------------------------

def test_two_peers_at_once_exactly_one_runs_the_other_is_refused(srv):
    srv.delay = 0.3
    m = mesh_tool(srv)
    out, barrier = {}, threading.Barrier(2)

    def peer(name):
        barrier.wait()
        out[name] = call(m, name, session_id="g")

    ts = [threading.Thread(target=peer, args=(n,)) for n in ("agent_1", "agent_2")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert srv.calls["generate_volume_mesh"] == 1
    results = sorted(r.get("error_code", "ran") for r in out.values())
    assert results == ["ALREADY_DONE", "ran"]
    refused = next(r for r in out.values() if r.get("error_code"))
    assert "already running generate_volume_mesh" in refused["error"]


# ---- per-tool fingerprints ------------------------------------------------------

def test_su2_solve_is_once_only_per_mesh_and_config(srv):
    create = srv.tool("create_su2_session", ["base_name", "initial_mesh"])
    srv.responses["create_su2_session"] = {"success": True, "session_id": "su2-1"}
    setm = srv.tool("set_mesh", ["session_id", "mesh_base64"])
    upd = srv.tool("update_config_entries", ["session_id", "updates"])
    solve = srv.tool("run_su2_solver", ["session_id", "solver"])
    call(create, "agent_2", base_name="w")
    assert call(solve, "agent_2", session_id="su2-1")["success"] is True       # no mesh: not guarded
    call(setm, "agent_2", session_id="su2-1", mesh_base64=BIG_MESH)
    assert call(solve, "agent_2", session_id="su2-1")["success"] is True
    assert call(solve, "agent_3", session_id="su2-1")["error_code"] == "ALREADY_DONE"
    call(upd, "agent_3", session_id="su2-1", updates={"AOA": 3.0})             # config changed -> new work
    assert call(solve, "agent_4", session_id="su2-1")["success"] is True


def test_create_su2_session_refused_while_a_live_session_holds_this_designs_mesh(srv):
    call(mesh_tool(srv), "agent_1", session_id="g")
    create = srv.tool("create_su2_session", ["base_name", "initial_mesh"])
    srv.responses["create_su2_session"] = {"success": True, "session_id": "su2-1"}
    close = srv.tool("close_su2_session", ["session_id"])
    dp._tool_server_map["close_su2_session"] = "su2"
    assert call(create, "agent_2", base_name="w", initial_mesh=BIG_MESH)["success"] is True
    r = call(create, "agent_3", base_name="w")
    assert r["error_code"] == "ALREADY_DONE" and "su2-1" in r["error"]
    call(close, "agent_2", session_id="su2-1")
    assert call(create, "agent_4", base_name="w")["success"] is True


def test_open_cpacs_is_done_only_while_that_session_is_open(srv, tmp_path):
    f = tmp_path / "D150.xml"; f.write_text("<cpacs/>")
    op = srv.tool("open_cpacs", ["source_type", "source"])
    srv.responses["open_cpacs"] = lambda kw: {"success": True, "session_id": f"geo-{srv.calls['open_cpacs']}"}
    cl = srv.tool("close_cpacs", ["session_id"])
    call(op, "agent_1", source_type="path", source=str(f))
    r = call(op, "agent_2", source_type="path", source=str(f))
    assert r["error_code"] == "ALREADY_DONE" and "geo-1 (still open)" in r["error"]
    call(cl, "agent_1", session_id="geo-1")
    assert call(op, "agent_3", source_type="path", source=str(f))["success"] is True


def test_estimate_mass_once_per_geometry_and_mass_config_even_after_g1_write_back(srv, tmp_path):
    f = tmp_path / "D150_simple.xml"; f.write_text("<cpacs><vehicles/></cpacs>")
    mass = srv.tool("estimate_mass", ["cpacs_file_path", "wing_mass_method", "material"])

    def write_back(kw):
        with open(kw["cpacs_file_path"], "a") as h:   # mass-mcp writes its breakdown into the file it is given
            h.write("<massBreakdown/>")
        return {"status": "success"}
    srv.responses["estimate_mass"] = write_back
    assert call(mass, "agent_1", cpacs_file_path=str(f), wing_mass_method="flops")["status"] == "success"
    assert "massBreakdown" not in f.read_text()                                     # G1 held
    r = call(mass, "agent_2", cpacs_file_path=str(f), wing_mass_method="flops")    # same geometry + config
    assert r["error_code"] == "ALREADY_DONE"
    assert call(mass, "agent_3", cpacs_file_path=str(f), wing_mass_method="gasp")["status"] == "success"
    f.write_text("<cpacs><vehicles><wing span='41'/></vehicles></cpacs>")           # geometry changed
    assert call(mass, "agent_4", cpacs_file_path=str(f), wing_mass_method="flops")["status"] == "success"
    assert srv.calls["estimate_mass"] == 3


def test_cycle_model_once_per_definition(srv):
    cyc = srv.tool("create_cycle_model", ["cycle_type", "options"])
    srv.responses["create_cycle_model"] = {"success": True, "session_id": "cyc-1"}
    call(cyc, "agent_1", cycle_type="hbtf", options={"BPR": 5.1})
    assert call(cyc, "agent_2", cycle_type="hbtf", options={"BPR": 5.1})["error_code"] == "ALREADY_DONE"
    assert call(cyc, "agent_2", cycle_type="hbtf", options={"BPR": 6.0})["success"] is True


# ---- mirrors and metrics ----------------------------------------------------------

def test_completed_once_only_work_is_mirrored_to_the_blackboard(srv):
    from src.coordination.blackboard import Blackboard

    bb = Blackboard(claiming_mode="none")
    dg.register_blackboard(bb)
    call(mesh_tool(srv), "agent_1", session_id="g")
    [e] = bb.read_by_type("done")
    assert e.key.startswith("done:generate_volume_mesh:geo:") and e.author == "agent_1"
    assert "generate_volume_mesh__mesh_base64" in e.value


def test_a_refusal_is_not_a_failed_attempt_for_the_retry_loop():
    from types import SimpleNamespace as NS

    from src.coordination.feedback_extraction import extract_feedback

    refusal = json.dumps({"success": False, "error_code": "ALREADY_DONE", "error": "agent_1 already did it"})
    real_failure = json.dumps({"success": False, "error": "solver diverged"})
    msg = lambda out: NS(tool_calls=[NS(tool_name="generate_volume_mesh", inputs={}, output=out,
                                        duration_seconds=0, error=None)], content="", error=None)
    assert extract_feedback(msg(refusal)).has_tool_errors is False
    assert extract_feedback(msg(real_failure)).has_tool_errors is True


def test_metrics_reach_the_parent_through_the_state_export(srv):
    m = mesh_tool(srv)
    call(m, "agent_1", session_id="g"); call(m, "agent_2", session_id="g")
    exported = dp.export_state_summary()["_kb_metrics"]
    assert exported["kb_entries"] == 2
    assert exported["duplicate_refusals"] == {"agent_2": {"generate_volume_mesh": 1}}
