"""B90: waiting is a state a peer can enter, not idling and re-claiming.

validate10: agent_3 spent four steps and 1,394 s losing claims and doing no work, while
`mission`, `simulation` and `evaluation` sat unclaimed but BLOCKED -- their dependencies were
still being worked by the other two peers. A peer had no way to say "there is nothing I can
start yet"; the only options were to retry a claim or to stop.

Waiting costs no generation (the peer is inside a tool call, not thinking), so the other peers
get the whole batch, and it is bounded well under the B85 tool watchdog so it can never wedge.

Stubbed only -- no MCP server, no model, no .env.
"""

import json
import threading
import time

import pytest

import src.tools.data_plane as dp
from src.coordination.blackboard import Blackboard
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm
from src.tools import work_claims as wc
from src.tools.networked_tools import NetworkedContext, WaitForBoard

# mission depends on aero and mass, the shape that stranded agent_3.
SEED = [("geometry", "mesh it"), ("aero", "solve it", ["geometry"]), ("mass", "size it", ["geometry"]),
        ("mission", "fly it", ["aero", "mass"])]


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


def _waiter(board, agent, **kw):
    context = NetworkedContext(blackboard=board, agents={}, model=None, all_tools=[], peer_prompt="")
    tool = WaitForBoard(context, agent_name=agent)
    tool._POLL_SECONDS = 0.05
    return tool


def _wait(board, agent, **kw):
    return json.loads(_waiter(board, agent).forward(**kw))


# ---- it never wastes time -----------------------------------------------------------------------

def test_it_returns_at_once_when_there_is_work_to_take():
    board = _board()
    out = _wait(board, "agent_3", seconds=30)

    assert out["waited_seconds"] == 0.0
    assert "nothing to wait for" in out["message"]
    assert out["claimed"] == "geometry", "and it takes the work rather than costing another step"


def test_it_returns_at_once_when_nobody_else_is_working():
    """Nothing can change, so waiting would be pure latency."""
    board = _board(seed=[("geometry", "g")])
    board.claim_todo("geometry", "agent_3")
    board.complete_todo("geometry", "agent_3", "mesh 660k cells")

    out = _wait(board, "agent_3", seconds=30)
    assert out["waited_seconds"] == 0.0
    assert "Nobody else is working" in out["message"]
    assert "write_blackboard" in out["message"], "give it an exit that is not a retry"


def test_a_peer_holding_work_is_told_to_do_it():
    board = _board()
    board.claim_todo("geometry", "agent_1")

    out = _wait(board, "agent_1", seconds=30)
    assert out["waited_seconds"] == 0.0
    # B93 kept the behaviour and sharpened the wording: a holder whose work is STARTABLE is still
    # sent straight back to it, and is now also told which step to take (validate11: the geometry
    # holder knew it held geometry and still could not work out that the mesh was next).
    assert "instead of waiting" in out["message"]
    assert "YOUR NEXT STEP IS" in out["message"]


# ---- the case that stranded agent_3 ---------------------------------------------------------------

def test_a_peer_whose_only_options_are_blocked_waits_and_is_handed_the_work():
    board = _board()
    board.claim_todo("geometry", "agent_1")            # agent_3 can take nothing: aero/mass blocked
    assert wc.claimable_now("agent_3") == []

    def finish_geometry():
        time.sleep(0.3)
        board.complete_todo("geometry", "agent_1", "mesh 660k cells, ref generate_volume_mesh__mesh_base64")

    threading.Thread(target=finish_geometry, daemon=True).start()
    out = _wait(board, "agent_3", seconds=10)

    assert out["board_changed"] is True
    assert out["claimed"] in ("aero", "mass"), out
    assert out["waited_seconds"] < 5


def test_blocked_work_is_reported_with_what_it_waits_on():
    board = _board()
    board.claim_todo("geometry", "agent_1")

    out = _wait(board, "agent_3", seconds=0.2)
    blocked = {b["name"]: b["waiting_on"] for b in out["blocked"]}
    assert blocked["aero"] == ["geometry"] and blocked["mass"] == ["geometry"]
    assert set(blocked["mission"]) == {"aero", "mass"}


def test_it_can_look_without_claiming():
    board = _board()
    out = _wait(board, "agent_3", seconds=5, claim_when_free=False)
    assert out["claimed"] is None
    assert "geometry" in out["claimable_now"]


# ---- bounded, always (B85's lesson) -----------------------------------------------------------------

def test_a_wait_that_sees_nothing_ends_on_its_own():
    board = _board()
    board.claim_todo("geometry", "agent_1")

    started = time.monotonic()
    out = _wait(board, "agent_3", seconds=1)
    assert 0.5 < time.monotonic() - started < 6
    assert out["board_changed"] is False
    assert "Nothing moved" in out["message"]


def test_the_wait_is_capped_far_below_the_tool_watchdog():
    """A wait must never be able to become a wedge, so its ceiling sits under B85's."""
    from src.tools.call_watchdog import timeout_for

    assert WaitForBoard._MAX_SECONDS < timeout_for("wait_for_board")

    board = _board()
    board.claim_todo("geometry", "agent_1")
    tool = _waiter(board, "agent_3")
    tool._MAX_SECONDS = 1.0                            # exercise the clamp, not the clock
    started = time.monotonic()
    json.loads(tool.forward(seconds=99999))            # asks for far more than the cap
    assert time.monotonic() - started < 6, "the request must be clamped to the cap"


def test_a_board_move_that_frees_nothing_still_returns():
    """agent_2 finishing its own item does not unblock aero, which still waits on geometry."""
    board = _board(seed=[("geometry", "g"), ("aero", "a", ["geometry"]), ("survey", "s")])
    board.claim_todo("geometry", "agent_1")
    board.claim_todo("survey", "agent_2")
    assert wc.claimable_now("agent_3") == []

    def churn():
        time.sleep(0.2)
        board.complete_todo("survey", "agent_2", "surveyed, nothing to report")

    threading.Thread(target=churn, daemon=True).start()
    out = _wait(board, "agent_3", seconds=10)
    assert out["board_changed"] is True and out["claimed"] is None
    assert "nothing is takeable yet" in out["message"]


# ---- the peer is told the state exists, where it needs it --------------------------------------------

def test_a_claim_rejection_with_nothing_free_points_at_the_wait():
    from src.tools.networked_tools import ClaimTodo

    board = _board(seed=[("geometry", "g")])
    board.claim_todo("geometry", "agent_1")
    context = NetworkedContext(blackboard=board, agents={}, model=None, all_tools=[], peer_prompt="")
    lost = json.loads(ClaimTodo(context, agent_name="agent_3").forward(todo_name="geometry"))

    assert "wait_for_board" in lost["message"]
    assert "do not keep trying" in lost["message"]


def test_the_tool_path_refusal_points_at_the_wait_when_nothing_is_free():
    board = _board(seed=[("geometry", "g")])
    board.claim_todo("geometry", "agent_1")
    wc._first_call_refused.add("agent_3")

    refusal = wc.check("generate_volume_mesh", "agent_3")
    assert refusal is not None and "wait_for_board" in refusal["error"]


def test_the_prompt_names_the_wait_for_blocked_work():
    from src.coordination.strategies.networked import NetworkedStrategy

    strategy = object.__new__(NetworkedStrategy)
    strategy._blackboard = _board()
    strategy._agent_order = ["agent_1", "agent_2", "agent_3"]

    text = strategy._with_board_and_uids("F25 design")
    assert "wait_for_board" in text and "BLOCKED" in text


def test_every_peer_is_given_the_tool():
    from src.tools.networked_tools import PEER_TOOL_NAMES

    assert "wait_for_board" in PEER_TOOL_NAMES


# ---- metrics ---------------------------------------------------------------------------------------

def test_waits_are_counted_so_idling_is_visible():
    board = _board()
    board.claim_todo("geometry", "agent_1")
    _wait(board, "agent_3", seconds=0.3)
    _wait(board, "agent_3", seconds=0.3)

    stats = wc.stats()
    assert stats["board_waits"]["agent_3"]["waits"] == 2
    assert stats["board_wait_seconds_total"] > 0
    assert stats["board_waits"]["agent_3"]["woken_by_change"] == 0


def test_a_wait_woken_by_a_change_is_recorded_as_such():
    board = _board()
    board.claim_todo("geometry", "agent_1")

    def finish():
        time.sleep(0.2)
        board.complete_todo("geometry", "agent_1", "mesh done, 660k cells")

    threading.Thread(target=finish, daemon=True).start()
    _wait(board, "agent_3", seconds=10)
    assert wc.stats()["board_waits"]["agent_3"]["woken_by_change"] == 1


# ---- B86/B89 regression ------------------------------------------------------------------------------

def test_claiming_and_the_live_board_are_unchanged():
    from src.tools.networked_tools import ClaimTodo

    board = _board()
    context = NetworkedContext(blackboard=board, agents={}, model=None, all_tools=[], peer_prompt="")
    assert json.loads(ClaimTodo(context, agent_name="agent_1").forward(todo_name="geometry"))["success"]
    lost = json.loads(ClaimTodo(context, agent_name="agent_2").forward(todo_name="geometry"))
    assert lost["success"] is False and "board" in lost and "unclaimed" in lost
