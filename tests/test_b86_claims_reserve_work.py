"""B86: a claim reserves the work until its holder releases it with a summary.

validate9's first networked link made 0 claim_todo / 0 read_todos / 0 read_procedure calls,
all three peers spent step 1 on the identical read-only get_design_space (166/278/382 s),
then all three wrote conflicting wing areas into one aviary session in one round. Two peers
later created their own SU2 session for the same design three minutes apart, and neither was
refused, because nothing in the tool path ever consulted the board.

The claim machinery itself is unchanged: claim_todo is still the atomic primitive and
mark_todo_done(name, result) is still the release. These tests cover what was missing --
the tool path honouring it.

Stubbed tools only -- no MCP server, no model, no .env.
"""

import json

import pytest
from smolagents import Tool

import src.tools.data_plane as dp
from src.coordination.blackboard import Blackboard
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm
from src.tools import procedures
from src.tools import work_claims as wc
from src.tools.agent_context import agent_scope
from src.tools.type_coercion import wrap_tool_with_middleware

SERVERS = {"generate_volume_mesh": "tigl", "open_cpacs": "tigl", "export_cpacs": "tigl",
           "create_su2_session": "su2", "set_mesh": "su2", "run_su2_solver": "su2",
           "estimate_mass": "mass", "run_cycle": "pycycle", "set_inputs": "pycycle",
           "create_cycle_model": "pycycle", "set_aircraft_parameters": "aviary",
           "run_simulation": "aviary", "get_results": "aviary"}

SEED = [("geometry", "mesh it"), ("aero", "solve it"), ("mass", "size it"),
        ("propulsion", "cycle it"), ("mission", "fly it")]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", dict(SERVERS))
    kbm.configure_run(None, 0, 1, None)
    wc.reset()
    yield
    wc.reset()


def _board(seed=SEED):
    board = Blackboard()
    board.seed_todos(list(seed))
    wc.register_blackboard(board)
    wc.set_structure("networked")
    return board


def _tool(name, response=None):
    import inspect

    class T(Tool):
        description = "fake"
        output_type = "string"

        def forward(self, **kw):
            return json.dumps(response if response is not None else {"success": True})

    T.name = name
    T.inputs = {"session_id": {"type": "any", "description": "s", "nullable": True}}
    T.forward.__signature__ = inspect.Signature([
        inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("session_id", inspect.Parameter.KEYWORD_ONLY, default=None)])
    return wrap_tool_with_middleware(T())


def _call(tool, agent, **kw):
    with agent_scope(agent, "peer"):
        return json.loads(tool.forward(**kw))


# ---- the tool path now honours the board -------------------------------------------------

def test_a_tool_belongs_to_the_todo_whose_work_it_performs():
    """The mapping comes from the procedure table, so refusal and reference cannot drift."""
    assert wc.todo_for_tool("run_su2_solver") == "aero"
    assert wc.todo_for_tool("generate_volume_mesh") == "geometry"
    assert wc.todo_for_tool("estimate_mass") == "mass"
    assert wc.todo_for_tool("run_cycle") == "propulsion"
    assert wc.todo_for_tool("set_aircraft_parameters") == "mission"
    assert wc.todo_for_tool("read_design_knowledge") is None      # discovery tools are free


def test_another_peers_claimed_work_is_refused():
    """validate9: two peers created their own SU2 session for one design, neither refused."""
    board = _board()
    assert board.claim_todo("aero", "agent_1")[0] is True

    solver = _tool("create_su2_session")
    _call(solver, "agent_1")                                   # the holder proceeds
    out = _call(solver, "agent_2")

    assert out["success"] is False
    assert out["error_code"] == "CLAIMED_BY_ANOTHER_AGENT"
    assert out["claimed_by"] == "agent_1" and out["todo"] == "aero"
    assert "mark_todo_done" in out["error"] and "mark_todo_failed" in out["error"]
    assert "Unclaimed right now" in out["error"]


def test_the_holder_is_never_refused_its_own_work():
    board = _board()
    board.claim_todo("aero", "agent_1")
    for tool in ("create_su2_session", "set_mesh", "run_su2_solver"):
        assert _call(_tool(tool), "agent_1")["success"] is True


def test_work_stays_reserved_until_it_is_released():
    """Jessica: it should not run until the agent releases it with a summary."""
    board = _board()
    board.claim_todo("aero", "agent_1")
    solver = _tool("run_su2_solver")
    assert _call(solver, "agent_2")["success"] is False

    board.complete_todo("aero", "agent_1", "CL=0.589, CD=0.0303")
    assert _call(solver, "agent_2")["success"] is True, "released work must be usable again"


def test_a_stalled_peers_claim_can_be_taken_over():
    board = _board()
    board.claim_todo("aero", "agent_1")
    board.fail_todo("aero", "agent_1", "solver kept diverging")

    # agent_2 still gets its one CLAIM_FIRST, and the freed TODO is named in it.
    first = _call(_tool("run_su2_solver"), "agent_2")
    assert first["error_code"] == "CLAIM_FIRST" and "aero" in first["unclaimed"]
    assert _call(_tool("run_su2_solver"), "agent_2")["success"] is True
    assert {t.name: t.assigned_to for t in board.read_todos()}["aero"] == "agent_2"


# ---- claim first, once ----------------------------------------------------------------------

def test_the_first_domain_call_is_refused_once_so_the_peer_looks_at_the_board():
    _board()
    out = _call(_tool("generate_volume_mesh"), "agent_1")

    assert out["success"] is False and out["error_code"] == "CLAIM_FIRST"
    assert "claim_todo('geometry')" in out["error"]
    assert set(out["unclaimed"]) == {t[0] for t in SEED}
    assert "said once" in out["error"]


def test_after_that_one_refusal_doing_the_work_claims_it():
    """Auto-claim: a peer can never be blocked by its own diligence."""
    board = _board()
    _call(_tool("generate_volume_mesh"), "agent_1")            # the one refusal
    out = _call(_tool("generate_volume_mesh"), "agent_1")

    assert out["success"] is True
    owner = {t.name: t.assigned_to for t in board.read_todos()}["geometry"]
    assert owner == "agent_1", "starting the work should have claimed it"


def test_the_first_call_refusal_happens_at_most_once_per_peer():
    _board()
    for _ in range(4):
        _call(_tool("generate_volume_mesh"), "agent_1")
    assert wc.stats()["first_call_refusals"]["agent_1"] == 1


def test_a_peer_that_claimed_explicitly_is_never_refused_first():
    board = _board()
    board.claim_todo("mass", "agent_1")
    assert _call(_tool("estimate_mass"), "agent_1")["success"] is True
    assert wc.stats()["first_call_refusals"] == {}


def test_a_peer_may_hold_more_than_one_todo():
    """Jessica: no cap -- a peer flowing from geometry into meshing into solving is fine."""
    board = _board()
    _call(_tool("generate_volume_mesh"), "agent_1")            # the one refusal
    _call(_tool("generate_volume_mesh"), "agent_1")            # claims geometry
    assert _call(_tool("run_su2_solver"), "agent_1")["success"] is True

    held = {t.name for t in board.read_todos() if t.assigned_to == "agent_1"}
    assert {"geometry", "aero"} <= held


# ---- never a trap (B85's lesson) ---------------------------------------------------------------

def test_an_empty_board_never_refuses_anything():
    _board(seed=[])
    for tool in ("generate_volume_mesh", "run_su2_solver", "estimate_mass"):
        assert _call(_tool(tool), "agent_1")["success"] is True


def test_sequential_and_orchestrated_never_see_a_claim_refusal():
    board = Blackboard()
    board.seed_todos(list(SEED))
    wc.register_blackboard(board)          # board exists, but the structure is not networked
    assert _call(_tool("generate_volume_mesh"), "solo_agent")["success"] is True


def test_a_fully_claimed_board_still_lets_the_holder_work():
    board = _board()
    for name, _ in SEED:
        board.claim_todo(name, "agent_1")
    assert _call(_tool("run_su2_solver"), "agent_1")["success"] is True

    refused = _call(_tool("run_su2_solver"), "agent_2")
    assert refused["error_code"] == "CLAIMED_BY_ANOTHER_AGENT"
    assert "Everything else is claimed" in refused["error"]


def test_the_contract_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AVION_ENFORCE_CLAIMS", "0")
    _board()
    assert _call(_tool("generate_volume_mesh"), "agent_1")["success"] is True


# ---- releasing requires the summary the next peer reads ------------------------------------------

def _mark_done_tool(board, agent):
    from src.tools.networked_tools import MarkTodoDone, NetworkedContext

    context = NetworkedContext(blackboard=board, agents={}, model=None, all_tools=[], peer_prompt="")
    return MarkTodoDone(context, agent_name=agent)


def test_releasing_without_a_summary_is_refused_and_keeps_the_claim():
    board = _board()
    board.claim_todo("aero", "agent_1")

    out = json.loads(_mark_done_tool(board, "agent_1").forward(todo_name="aero", result=""))
    assert out["success"] is False and out["error_code"] == "SUMMARY_REQUIRED"
    assert "CL=0.589" in out["error"], "it must show what a good summary looks like"

    owner = {t.name: t.assigned_to for t in board.read_todos()}["aero"]
    assert owner == "agent_1", "a refused release must not drop the claim"
    assert _call(_tool("run_su2_solver"), "agent_2")["success"] is False


def test_releasing_with_a_summary_posts_it_for_the_other_peers():
    board = _board()
    board.claim_todo("aero", "agent_1")

    out = json.loads(_mark_done_tool(board, "agent_1").forward(
        todo_name="aero", result="CL=0.589, CD=0.0303, L/D=19.4"))
    assert out["success"] is True

    done = {t.name: t for t in board.read_todos()}["aero"]
    assert done.status == "done" and "CL=0.589" in done.result


def test_only_the_holder_can_release():
    board = _board()
    board.claim_todo("aero", "agent_1")
    out = json.loads(_mark_done_tool(board, "agent_2").forward(
        todo_name="aero", result="CL=0.5, CD=0.03"))
    assert out["success"] is False and "claimed by" in out["message"]


# ---- two peers claiming at once: exactly one winner (unchanged, re-asserted) -------------------------

def test_exactly_one_peer_wins_a_contested_claim():
    import threading

    board = _board()
    wins = []

    def claim():
        ok, _ = board.claim_todo("aero", threading.current_thread().name)
        if ok:
            wins.append(threading.current_thread().name)

    threads = [threading.Thread(target=claim, name=f"agent_{i}") for i in range(8)]
    [t.start() for t in threads]
    [t.join(timeout=10) for t in threads]
    assert len(wins) == 1, wins


# ---- metrics -----------------------------------------------------------------------------------------

def test_claims_and_violations_are_counted():
    board = _board()
    board.claim_todo("aero", "agent_1")
    _call(_tool("run_su2_solver"), "agent_2")
    _call(_tool("run_su2_solver"), "agent_2")
    _call(_tool("estimate_mass"), "agent_3")          # first-call refusal
    _call(_tool("estimate_mass"), "agent_3")          # auto-claims mass

    stats = wc.stats()
    assert stats["claim_violations"]["agent_2"]["run_su2_solver"] == 2
    assert stats["claim_violations_total"] == 2
    assert stats["first_call_refusals_total"] >= 1
    assert stats["claims_made"]["mass"] == "agent_3"


# ---- the component UIDs, so nobody guesses 'Wing' -------------------------------------------------------

def test_the_baseline_uids_are_named_before_any_call():
    block = procedures.component_uid_block()
    assert "Wing1" in block and "Fuselage1" in block
    assert "not 'Wing'" in block


def test_the_geometry_procedure_warns_about_the_uid_trap():
    text = procedures.render("geometry")
    assert "Wing1" in text and "not a" in text.lower()


def test_live_uids_are_captured_and_preferred_over_the_baseline():
    dp._capture_component_uids("list_geometric_components", {"components": [
        {"uid": "FuselageX", "name": "f"}, {"uid": "WingX", "name": "w"}]})
    assert procedures.component_uids() == ("FuselageX", "WingX")
    assert "WingX" in procedures.component_uid_block()


def test_uids_are_recovered_from_tigls_own_error_message():
    """tigl already names them; capturing the error means one wrong guess teaches everyone."""
    dp._capture_component_uids("generate_volume_mesh", {
        "success": False,
        "error": ("Component 'Wing' not found. Did you mean 'Wing1'? Available UIDs: Fuselage1, "
                  "Wing1, Wing2H, Wing3V. Use one of these exactly."),
    })
    assert procedures.component_uids() == ("Fuselage1", "Wing1", "Wing2H", "Wing3V")
