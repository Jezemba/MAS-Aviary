"""B93: say what to do next, and let a peer holding blocked work do something.

validate11 fixed the coordination (no livelock, one lost claim per peer, work spread one each)
and then stalled on execution:

  * the `geometry` holder re-claimed a TODO it already held three times (556 + 465 + 662 s) and
    re-opened the same CPACS file twice, never reaching generate_volume_mesh. Across validate10
    and validate11 peers spent 2,354 s re-claiming work they already owned;
  * the `aero` holder had its SU2 session ready, needed a mesh owned by `geometry`, and had no
    legal move: it could not wait_for_board (it holds work), could not claim `mass` (no forward
    reservation), and its only exit was to abandon a TODO it legitimately owned;
  * `read_procedure` has had 0 calls in EVERY run since B85 added it, so the reference that
    answers all of this is never consulted.

So the next step is pushed rather than offered, and "holding blocked work" becomes a state the
framework understands.

Stubbed tools only -- no MCP server, no model, no .env.
"""

import json

import pytest

import src.tools.data_plane as dp
from src.coordination.blackboard import Blackboard
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm
from src.tools import procedures
from src.tools import work_claims as wc
from src.tools.duplicate_guard import _next_step_hint
from src.tools.networked_tools import ClaimTodo, NetworkedContext, WaitForBoard

SEED = [("geometry", "mesh it"), ("aero", "solve it"), ("mass", "size it"),
        ("propulsion", "cycle it"), ("mission", "fly it")]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_tool_server_map", {})
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


def _context(board):
    return NetworkedContext(blackboard=board, agents={}, model=None, all_tools=[], peer_prompt="")


def _claim(board, agent, todo):
    return json.loads(ClaimTodo(_context(board), agent_name=agent).forward(todo_name=todo))


def _wait(board, agent, **kwargs):
    return json.loads(WaitForBoard(_context(board), agent_name=agent).forward(**kwargs))


# ---- the procedure table can say what comes next -----------------------------------------------

def test_next_step_walks_the_procedure_in_order():
    assert procedures.next_step_for("geometry", set()).tool == "open_cpacs"
    assert procedures.next_step_for("geometry", {"open_cpacs"}).tool == "set_high_level_parameters"
    assert procedures.next_step_for(
        "geometry", {"open_cpacs", "set_high_level_parameters"}).tool == "export_cpacs"
    assert procedures.next_step_for(
        "geometry", {"open_cpacs", "set_high_level_parameters", "export_cpacs"}
    ).tool == "generate_volume_mesh"


def test_a_finished_procedure_asks_for_the_todo_to_be_released():
    done = {"create_su2_session", "set_mesh", "run_su2_solver"}
    line = procedures.next_step_line("aero", done)
    assert "mark_todo_done('aero'" in line
    # read_history_csv is conditional, so it must not be demanded.
    assert "read_history_csv" not in line


def test_next_step_line_names_what_is_done_and_what_is_next():
    line = procedures.next_step_line("geometry", {"open_cpacs"})
    assert "Done so far for geometry: open_cpacs" in line
    assert "YOUR NEXT STEP IS set_high_level_parameters" in line


def test_unknown_role_is_silent_so_callers_can_append_unconditionally():
    assert procedures.next_step_line("no_such_role", set()) == ""
    assert procedures.next_step_for("no_such_role", set()) is None


# ---- ALREADY_DONE carries the next step (validate11: three steps lost to not knowing) ----------

def test_already_done_hint_names_the_next_step_not_the_refused_tool():
    hint = _next_step_hint("open_cpacs")
    assert "YOUR NEXT STEP IS set_high_level_parameters" in hint
    assert "YOUR NEXT STEP IS open_cpacs" not in hint


def test_already_done_hint_is_empty_for_a_tool_no_procedure_owns():
    assert _next_step_hint("get_design_space") == ""
    assert _next_step_hint("nonesuch") == ""


# ---- claiming what you already hold answers with the next step ---------------------------------

def test_self_claim_returns_the_next_step_instead_of_a_bare_success():
    board = _board()
    assert _claim(board, "agent_2", "geometry")["success"] is True
    again = _claim(board, "agent_2", "geometry")
    assert again["success"] is True                      # still yours; nothing is taken away
    assert "already hold" in again["message"]
    assert "YOUR NEXT STEP IS" in again["message"]


def test_self_claim_on_blocked_work_says_so_and_offers_the_alternatives():
    board = _board()
    _claim(board, "agent_1", "geometry")                 # geometry is someone else's, and not done
    assert _claim(board, "agent_3", "aero")["success"] is True
    again = _claim(board, "agent_3", "aero")
    assert "BLOCKED on geometry" in again["message"]
    assert "wait_for_board" in again["message"]


# ---- holding BLOCKED work does not strand a peer -----------------------------------------------

def test_a_blocked_holder_may_claim_independent_work():
    """validate11 01:00: agent_3 held blocked 'aero' and was refused 'mass'."""
    board = _board()
    _claim(board, "agent_1", "geometry")
    assert _claim(board, "agent_3", "aero")["success"] is True
    assert wc.blocked_holdings("agent_3") == {"aero": ("geometry",)}
    assert wc.all_holdings_blocked("agent_3") is True
    taken = _claim(board, "agent_3", "propulsion")
    assert taken["success"] is True


def test_a_holder_whose_work_is_startable_still_cannot_reserve_more():
    """B89's rule is intact for the case it was written for: agent_1 hoarding while working."""
    board = _board()
    assert _claim(board, "agent_1", "geometry")["success"] is True   # geometry needs nobody
    assert wc.all_holdings_blocked("agent_1") is False
    refused = _claim(board, "agent_1", "aero")
    assert refused["success"] is False
    assert "left on the board" in refused["message"]


def test_blocking_roles_clear_once_the_prerequisite_is_done():
    board = _board()
    _claim(board, "agent_1", "geometry")
    _claim(board, "agent_3", "aero")
    assert wc.blocked_holdings("agent_3") == {"aero": ("geometry",)}
    board.complete_todo("geometry", "agent_1", "mesh ref generate_volume_mesh__mesh_base64")
    assert wc.blocked_holdings("agent_3") == {}
    assert wc.all_holdings_blocked("agent_3") is False


# ---- wait_for_board understands "blocked" ------------------------------------------------------

def test_a_blocked_holder_is_allowed_to_wait():
    board = _board([("geometry", "mesh it"), ("aero", "solve it")])
    _claim(board, "agent_1", "geometry")
    _claim(board, "agent_3", "aero")
    answer = _wait(board, "agent_3", seconds=1, claim_when_free=False)
    assert "do that instead of waiting" not in json.dumps(answer)


def test_a_holder_with_startable_work_is_sent_back_to_it_with_its_next_step():
    board = _board()
    _claim(board, "agent_1", "geometry")
    answer = _wait(board, "agent_1", seconds=1)
    assert "not blocked" in answer["message"]
    assert "YOUR NEXT STEP IS" in answer["message"]


# ---- B92: a FLOWN mission that burns no fuel is not a result ------------------------------------
#
# CORRECTED 2026-09-17 from validate13. The first version also covered set_aircraft_parameters,
# whose validity probe is a STATIC check that never flies the mission: 116 of 116 of its results
# across every run on this machine report fuel_burned_kg 0.0, including runs whose missions later
# flew and produced 16,645 kg. Marking that NOT VALID invented an error and the mission holder
# started chasing it. Only a tool that actually flies the mission is checked now.

def test_the_parameter_probe_is_left_alone_even_at_zero_fuel():
    """set_aircraft_parameters never flies the mission; 0.0 there is normal, not a defect."""
    payload = {"valid": True,
               "summary": "VALID -- all static checks passed and model evaluation produced finite outputs.",
               "model_eval": {"success": True, "outputs": {"fuel_burned_kg": 0.0,
                                                           "gtow_kg": 79560.101698}, "nan_outputs": []}}
    dp._flag_zero_fuel("set_aircraft_parameters", payload)
    assert payload["valid"] is True
    assert "error_code" not in payload


def test_a_flown_mission_reporting_no_fuel_is_not_valid():
    """run_simulation reports summary.fuel_burned_kg; 0.0 there means it returned nothing."""
    payload = {"success": True, "converged": True,
               "summary": {"fuel_burned_kg": 0.0, "gtow_kg": 82692.26, "wing_mass_kg": 8498.77}}
    dp._flag_zero_fuel("run_simulation", payload)
    assert payload["valid"] is False
    assert payload["error_code"] == "ZERO_FUEL"
    assert "did not actually fly" in payload["summary_note"]


def test_a_real_flight_is_left_alone():
    payload = {"success": True, "summary": {"fuel_burned_kg": 16645.646376874487,
                                            "gtow_kg": 82692.26012630484}}
    dp._flag_zero_fuel("run_simulation", payload)
    assert "error_code" not in payload
    assert payload.get("valid") is not False


def test_get_results_is_checked_too():
    payload = {"outputs": {"fuel_burned_kg": 0.0}}
    dp._flag_zero_fuel("get_results", payload)
    assert payload["valid"] is False and payload["error_code"] == "ZERO_FUEL"


def test_a_result_with_no_fuel_figure_is_untouched():
    payload = {"valid": True, "summary": {"gtow_kg": 79560.1}}
    dp._flag_zero_fuel("run_simulation", payload)
    assert payload["valid"] is True

# ---- the claim refusal also names the CALLER's own next step -----------------------------------
#
# This was written in the first B93 commit and silently did nothing: the append targeted a string
# that spans two source lines, the replace found no match, and `own_next` was computed and thrown
# away. validate14 03:30 caught it -- agent_3 held `geometry` and was refused
# set_aircraft_parameters with no word about the mesh it should have been making. Hence a test.

def test_a_claim_refusal_tells_the_caller_what_ITS_own_next_step_is():
    from src.tools.work_claims import check

    board = _board()
    _claim(board, "agent_2", "mission")
    _claim(board, "agent_3", "geometry")
    refusal = check("set_aircraft_parameters", "agent_3")      # mission work, held by agent_2
    assert refusal is not None
    assert refusal["error_code"] == "CLAIMED_BY_ANOTHER_AGENT"
    assert "You hold geometry:" in refusal["error"]
    assert "YOUR NEXT STEP IS" in refusal["error"]


def test_a_claim_refusal_to_a_peer_holding_nothing_says_nothing_about_its_own_work():
    from src.tools.work_claims import check

    board = _board()
    _claim(board, "agent_2", "mission")
    # agent_1 holds nothing, so it gets CLAIM_FIRST rather than the claimed-by refusal.
    refusal = check("set_aircraft_parameters", "agent_1")
    assert refusal is not None
    assert "You hold" not in refusal["error"]

def test_finish_yours_first_says_what_finishing_means():
    """validate14 03:37: the only actions offered were to claim a result it did not have,
    or to abandon work it should be doing. The procedure's next step must be in the message."""
    board = _board()
    _claim(board, "agent_3", "geometry")
    refused = _claim(board, "agent_3", "mass")
    assert refused["success"] is False
    assert "left on the board" in refused["message"]
    assert "YOUR NEXT STEP IS" in refused["message"]
    # and it still offers the release path, for when the work really is done
    assert "mark_todo_done('geometry'" in refused["message"]
    assert "mark_todo_failed('geometry')" in refused["message"]

