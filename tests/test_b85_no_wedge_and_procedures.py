"""B85: nothing wedges a run, and the design sequence is written down where an agent can read it.

validate8_net_7960 stopped progressing for 48 minutes with 92 threads asleep, 0% GPU and no
in-flight MCP request, after two of three peers hit "Reached max steps". These tests cover
the replacement: every wait is bounded, a link whose peers all exhaust their budget finishes
and is recorded, and the tool sequence an agent needs is queryable instead of folklore.

Stubbed tools and models only -- no MCP server, no weights, no .env.
"""

import json
import threading
import time

import pytest
from smolagents import Tool

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import call_watchdog
from src.tools import coupling_contract as cc
from src.tools import knowledge_base as kbm
from src.tools import procedures
from src.tools.agent_context import agent_scope
from src.tools.type_coercion import wrap_tool_with_middleware

SERVERS = {"slow_tool": "su2", "run_su2_solver": "su2", "create_su2_session": "su2",
           "run_simulation": "aviary", "set_aircraft_parameters": "aviary",
           "generate_volume_mesh": "tigl", "estimate_mass": "mass", "run_cycle": "pycycle"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", dict(SERVERS))
    kbm.configure_run(None, 0, 1, None)
    call_watchdog.reset_stats()
    yield
    call_watchdog.configure(default_seconds=900.0)


def _tool(name, fn, args=("x",)):
    """A fake MCP tool wrapped by the real middleware (same shape as the B81 tests use)."""
    import inspect

    class T(Tool):
        description = "fake"
        output_type = "string"

        def forward(self, **kw):
            return fn(**kw)

    T.name = name
    T.inputs = {k: {"type": "any", "description": k, "nullable": True} for k in args}
    params = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)] + [
        inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, default=None) for k in args]
    T.forward.__signature__ = inspect.Signature(params)
    return wrap_tool_with_middleware(T())


# ---- B85: a call that never returns must not wedge the run --------------------------------

def test_a_hanging_tool_call_becomes_a_failed_call_not_a_hang():
    """mcpadapt waits on .result() with no timeout; a stalled loop parked every thread."""
    release = threading.Event()
    call_watchdog.configure(default_seconds=0.5)

    tool = _tool("slow_tool", lambda **kw: release.wait(30) and "never")
    started = time.monotonic()
    with pytest.raises(call_watchdog.ToolCallTimeout) as exc:
        tool.forward(x="go")
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"the watchdog did not bound the call ({elapsed:.1f}s)"
    assert "did not return within" in str(exc.value)
    assert call_watchdog.stats()["tool_call_timeouts"]["slow_tool"] == 1
    release.set()


def test_a_timed_out_call_does_not_block_the_next_one():
    """The abandoned call keeps its thread, so the pool must still serve the next caller."""
    release = threading.Event()
    call_watchdog.configure(default_seconds=0.5)
    slow = _tool("slow_tool", lambda **kw: release.wait(30) and "never")
    with pytest.raises(call_watchdog.ToolCallTimeout):
        slow.forward(x="1")

    quick = _tool("generate_volume_mesh", lambda **kw: json.dumps({"success": True}))
    assert json.loads(quick.forward(x="2"))["success"] is True
    release.set()


def test_the_budget_is_generous_for_the_real_solvers():
    """These bound a deadlock, not performance: SU2 solves ran 150-900 s in validate8."""
    assert call_watchdog.timeout_for("run_su2_solver") >= 3600
    assert call_watchdog.timeout_for("run_simulation") >= 1800
    assert call_watchdog.timeout_for("anything_else") >= 600


def test_the_generation_wait_is_bounded_well_below_the_observed_wedge():
    from src.llm import batch_generation as bg

    assert bg._MAX_WAIT_SECONDS <= 1800, "48 minutes of wedge must not be survivable"


# ---- B85: a link whose peers all hit max steps must finish ------------------------------------

def test_a_peer_that_ends_by_max_steps_releases_its_place():
    """The reported cause: check the release fires for max-steps and exceptions, not only
    final_answer. It does -- _run_one_peer_batched releases in a finally."""
    from src.llm import batch_generation as bg

    bg.shutdown()
    with bg.peers_dispatched(3) as peers:
        assert bg.live_peers() == 3
        peers.release()          # peer 1: reached max steps
        peers.release()          # peer 2: raised
        assert bg.live_peers() == 1
        peers.release()          # peer 3: final_answer
        assert bg.live_peers() == 0
        assert bg.is_active() is False
    assert bg.live_peers() == 0


def test_a_parallel_cycle_of_max_steps_peers_completes(monkeypatch):
    """The smoke test Jessica asked for: every peer exhausts its budget, the link still ends."""
    from src.coordination.coordinator import Coordinator
    from src.llm import batch_generation as bg

    bg.shutdown()

    class MaxStepsAgent:
        """What smolagents does at max_steps: it RETURNS, it does not raise."""

        def __init__(self, name):
            self.name, self.memory = name, type("M", (), {"steps": []})()

        def run(self, task):
            time.sleep(0.02)
            return "Reached max steps."

    coordinator = object.__new__(Coordinator)
    coordinator.agents = {f"peer_{i}": MaxStepsAgent(f"peer_{i}") for i in range(3)}
    coordinator._turn_counter = 0
    coordinator.history = []

    action = type("A", (), {"metadata": {"peers": list(coordinator.agents), "cycle": 1},
                            "input_context": "do the work"})()

    done = threading.Event()
    result: dict = {}

    def run_it():
        try:
            result["messages"] = Coordinator._execute_parallel_run(coordinator, action)
        except BaseException as exc:      # noqa: BLE001 - reported below
            result["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run_it, daemon=True).start()
    assert done.wait(60), "the parallel cycle wedged -- this is B85"
    assert "error" not in result, result.get("error")
    assert len(result["messages"]) == 3
    assert all("max steps" in m.content for m in result["messages"])
    assert bg.live_peers() == 0, "peer places leaked, so the next cycle would wait for ghosts"


# ---- B84 follow-up: the requirement moved off set_aircraft_parameters -------------------------

def test_applying_a_design_is_never_refused():
    """5 refusals in validate8, all on set_aircraft_parameters, and two peers died of them."""
    assert cc.check("set_aircraft_parameters", {"session_id": "s", "parameters": {"x": 1}}) is None
    assert "set_aircraft_parameters" not in cc.REQUIRED_BY_TOOL


def test_the_mission_itself_is_still_required_to_be_coupled():
    refusal = cc.check("run_simulation", {"session_id": "s"})
    assert refusal is not None and refusal["error_code"] == "MISSING_REQUIRED_PARAMETERS"


# ---- B84 follow-up: a completed solve counts, even if nobody reads the CSV -----------------------

def _history_file(tmp_path, cl=0.51, cd=0.0187):
    workdir = tmp_path / "su2_session_x"
    workdir.mkdir()
    (workdir / "history.csv").write_text(
        '   "Inner_Iter",       "CL"       ,       "CD"       \n'
        f'            0,   0.1000,   0.0500\n'
        f'           99,   {cl},   {cd}\n')
    return str(workdir)


def test_a_completed_solve_is_captured_without_read_history_csv(tmp_path):
    """The solves completed and wrote history.csv; no peer called read_history_csv, so the
    mission stayed refused for a design whose aero was solved and sitting on disk."""
    workdir = _history_file(tmp_path)
    dp._capture_su2_workdir("create_su2_session", {"session_id": "su2-1", "workdir": workdir})
    dp._capture_aero_coefficients("run_su2_solver", {
        "success": True, "session_id": "su2-1",
        "final_coefficients_note": "no CL/CD could be read from the history file",
    })

    from src.tools import coupling

    assert coupling.get_var(dp.get_design_state(), "aero.cl_cruise") == pytest.approx(0.51)
    assert coupling.get_var(dp.get_design_state(), "aero.cd_cruise") == pytest.approx(0.0187)
    assert cc.status(cc.AERO_CD) == "ok"


def test_the_solvers_own_coefficients_still_win(tmp_path):
    workdir = _history_file(tmp_path, cl=0.9, cd=0.9)
    dp._capture_su2_workdir("create_su2_session", {"session_id": "su2-1", "workdir": workdir})
    dp._capture_aero_coefficients("run_su2_solver", {
        "success": True, "session_id": "su2-1", "final_coefficients": {"CL": 0.44, "CD": 0.0155},
    })

    from src.tools import coupling

    assert coupling.get_var(dp.get_design_state(), "aero.cl_cruise") == pytest.approx(0.44)


def test_a_failed_solve_captures_nothing(tmp_path):
    workdir = _history_file(tmp_path)
    dp._capture_su2_workdir("create_su2_session", {"session_id": "su2-1", "workdir": workdir})
    dp._capture_aero_coefficients("run_su2_solver", {"success": False, "session_id": "su2-1",
                                                    "solver_error": "diverged"})

    from src.tools import coupling

    assert coupling.get_var(dp.get_design_state(), "aero.cl_cruise") is None


# ---- B85: the procedure is written down ------------------------------------------------------------

def test_every_role_has_a_procedure_naming_its_tools_in_order():
    for role in procedures.ORDER:
        text = procedures.render(role)
        assert role.upper() in text
        assert "Steps, in order:" in text and "Produces:" in text

    whole = procedures.render()
    for role in procedures.ORDER:
        assert role.upper() in whole


def test_the_aero_procedure_answers_the_question_nobody_could():
    """'Not knowing to call read_history_csv' is a documentation bug (Jessica, 2026-09-16)."""
    aero = procedures.render("aero")
    assert "run_su2_solver" in aero and "read_history_csv" in aero
    assert "captured" in aero.lower(), "it must say the coefficients are captured for you"
    assert "mass and propulsion" in aero.lower() or "no aero" in aero.lower()


def test_an_unknown_role_says_which_roles_exist():
    text = procedures.render("wings")
    assert "Known roles" in text and "geometry" in text


def test_the_procedure_tool_is_given_to_every_agent():
    from src.tools.procedure_tool import ReadProcedure
    from src.tools.tool_loader import _with_design_state

    tools = _with_design_state([])
    assert ReadProcedure.name in {t.name for t in tools}
    assert "mission" in ReadProcedure().forward(role="mission")


# ---- B85: refusals say they cost steps, after a few ---------------------------------------------------

def test_the_first_refusals_do_not_nag():
    assert procedures.budget_warning(1) == ""
    assert procedures.budget_warning(2) == ""


def test_after_three_refusals_the_agent_is_told_it_is_spending_its_budget():
    warning = procedures.budget_warning(3)
    assert "COSTS YOU ONE OF YOUR LIMITED STEPS" in warning
    assert "read_procedure" in warning and "read_todos" in warning


def test_the_warning_reaches_a_real_missing_data_refusal():
    with agent_scope("agent_1", "aero"):
        for _ in range(3):
            refusal = cc.check("run_simulation", {"session_id": "s"})
            kbm.record_tool_result("run_simulation", "aviary", {}, refusal, status="refused")
        assert "read_procedure" in refusal["error"]


def test_a_waiting_peer_is_pointed_at_unclaimed_work():
    from src.tools import duplicate_guard as dg

    class Board:
        def read_pending_todos(self):
            return [type("T", (), {"name": "structures"})(), type("T", (), {"name": "propulsion"})()]

    dg.register_blackboard(Board())
    try:
        hint = dg._unclaimed_work_hint()
    finally:
        dg.register_blackboard(None)
    assert "structures" in hint and "propulsion" in hint
    assert "claim_todo" in hint and "need no aero" in hint


def test_with_nothing_unclaimed_waiting_is_called_correct():
    from src.tools import duplicate_guard as dg

    class Empty:
        def read_pending_todos(self):
            return []

    dg.register_blackboard(Empty())
    try:
        assert "waiting is correct" in dg._unclaimed_work_hint()
    finally:
        dg.register_blackboard(None)
