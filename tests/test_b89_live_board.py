"""B89: a peer acts on the board as it is NOW, and a lost claim names what is free.

B86/B87/B88 made claiming work -- 3 of 3 peers claimed before working in validate10, and a
peer doing another's discipline was refused. B89 is what that cost: every peer chose from a
snapshot taken once per turn, the same suggestion was handed to all three, and a rejected
claim said "pick a different TODO" without naming one. agent_3 lost three claims in a row
and did no work in 927 s; four rejections cost 1,117 s across the link.

Stubbed tools only -- no MCP server, no model, no .env.
"""

import json

import pytest

import src.tools.data_plane as dp
from src.coordination.blackboard import Blackboard
from src.coordination.design_state import DesignState
from src.tools import knowledge_base as kbm
from src.tools import work_claims as wc
from src.tools.networked_tools import ClaimTodo, NetworkedContext

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


def _claimer(board, agent):
    context = NetworkedContext(blackboard=board, agents={}, model=None, all_tools=[], peer_prompt="")
    return ClaimTodo(context, agent_name=agent)


def _claim(board, agent, todo):
    return json.loads(_claimer(board, agent).forward(todo_name=todo))


# ---- a rejected claim names what is free RIGHT NOW ---------------------------------------------

def test_a_lost_claim_lists_what_is_unclaimed_now():
    """validate10: 'pick a different TODO' named none, and agent_3 guessed again from a snapshot."""
    board = _board()
    assert _claim(board, "agent_1", "geometry")["success"] is True

    lost = _claim(board, "agent_2", "geometry")
    assert lost["success"] is False
    assert lost["current_owner"] == "agent_1"
    assert "geometry" not in lost["unclaimed"], "the one just lost must not be offered back"
    assert set(lost["unclaimed"]) == {"aero", "mass", "propulsion", "mission"}
    assert "Unclaimed RIGHT NOW" in lost["message"]
    assert "claim_todo('aero')" in lost["message"], "name one it can actually take"


def test_the_next_call_after_a_rejection_succeeds():
    """The acceptance criterion: no peer loses more than one claim."""
    board = _board()
    _claim(board, "agent_1", "geometry")
    lost = _claim(board, "agent_2", "geometry")

    took = _claim(board, "agent_2", lost["unclaimed"][0])
    assert took["success"] is True


def test_every_claim_result_carries_the_live_board():
    board = _board()
    _claim(board, "agent_1", "geometry")
    won = _claim(board, "agent_2", "mass")

    by_name = {row["name"]: row for row in won["board"]}
    assert by_name["geometry"]["owner"] == "agent_1"
    assert by_name["mass"]["owner"] == "agent_2"
    assert by_name["aero"]["owner"] is None and by_name["aero"]["status"] == "pending"


def test_a_fully_claimed_board_tells_the_peer_to_stop_trying():
    board = _board(seed=[("geometry", "g")])
    _claim(board, "agent_1", "geometry")

    lost = _claim(board, "agent_2", "geometry")
    assert lost["unclaimed"] == []
    assert "do not keep trying" in lost["message"]
    # B90 gave it something better than "post a gap and stop": a state it can sit in.
    assert "wait_for_board" in lost["message"], "give it something to do other than retry"


def test_two_peers_racing_one_todo_leave_exactly_one_winner():
    import threading

    board = _board()
    results = {}

    def race(agent):
        results[agent] = _claim(board, agent, "aero")

    threads = [threading.Thread(target=race, args=(f"agent_{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(timeout=10) for t in threads]

    winners = [a for a, r in results.items() if r["success"]]
    assert len(winners) == 1, winners
    for agent, result in results.items():
        if agent not in winners:
            assert result["unclaimed"], "a loser must always be told what is free"
            assert "aero" not in result["unclaimed"]


# ---- no forward reservation: a second TODO stays on the board -----------------------------------

def test_a_peer_holding_unfinished_work_cannot_reserve_another():
    """agent_1 claimed 'aero' while still doing 'geometry', so agent_3 could take neither."""
    board = _board()
    _claim(board, "agent_1", "geometry")

    second = _claim(board, "agent_1", "aero")
    assert second["success"] is False
    assert "still hold geometry" in second["message"]
    assert "mark_todo_done('geometry'" in second["message"]
    assert "aero" in second["unclaimed"], "it must stay on the board for whoever is waiting"


def test_the_second_todo_really_is_left_for_the_waiting_peer():
    board = _board()
    _claim(board, "agent_1", "geometry")
    _claim(board, "agent_1", "aero")                 # refused, stays free

    assert _claim(board, "agent_3", "aero")["success"] is True


def test_finishing_frees_the_peer_to_claim_again():
    board = _board()
    _claim(board, "agent_1", "geometry")
    board.complete_todo("geometry", "agent_1", "mesh 660k cells, ref generate_volume_mesh__mesh_base64")

    assert _claim(board, "agent_1", "aero")["success"] is True


def test_giving_up_also_frees_the_peer():
    board = _board()
    _claim(board, "agent_1", "geometry")
    board.fail_todo("geometry", "agent_1", "cell cap")

    assert _claim(board, "agent_1", "aero")["success"] is True


def test_re_claiming_what_you_already_hold_is_not_blocked():
    board = _board()
    _claim(board, "agent_1", "geometry")
    assert _claim(board, "agent_1", "geometry")["success"] is True


def test_auto_claim_on_touch_is_deliberately_not_capped():
    """A peer actually running the work is doing it now; blocking that strands it mid-flow."""
    board = _board()
    _claim(board, "agent_1", "geometry")
    wc._first_call_refused.add("agent_1")            # past its one CLAIM_FIRST

    assert wc.check("run_su2_solver", "agent_1") is None
    assert set(wc.held_unfinished("agent_1")) == {"geometry", "aero"}


# ---- a different suggestion per peer ---------------------------------------------------------------

def test_each_peer_is_suggested_a_different_todo():
    """All three read 'claim_todo('geometry')' in validate10 and two of them lost."""
    _board()
    split = wc.suggest_split(["agent_1", "agent_2", "agent_3"])

    assert len(set(split.values())) == 3, split
    assert set(split) == {"agent_1", "agent_2", "agent_3"}


def test_more_peers_than_todos_still_gives_everyone_a_name():
    _board(seed=[("geometry", "g"), ("aero", "a")])
    split = wc.suggest_split([f"agent_{i}" for i in range(5)])
    assert len(split) == 5 and set(split.values()) == {"geometry", "aero"}


def test_no_suggestion_when_nothing_is_free():
    board = _board(seed=[("geometry", "g")])
    board.claim_todo("geometry", "agent_1")
    assert wc.suggest_split(["agent_1", "agent_2"]) == {}


def test_the_peer_prompt_names_the_split_not_one_todo_for_everyone():
    from src.coordination.strategies.networked import NetworkedStrategy

    strategy = object.__new__(NetworkedStrategy)
    strategy._blackboard = _board()
    strategy._agent_order = ["agent_1", "agent_2", "agent_3"]

    text = strategy._with_board_and_uids("F25 design")
    assert "SUGGESTED SPLIT" in text
    assert "agent_1 -> geometry" in text and "agent_2 -> aero" in text
    assert "Unclaimed: " in text
    assert "the reply lists what is free at that moment" in text


def test_the_prompt_is_unchanged_when_the_board_is_empty():
    from src.coordination.strategies.networked import NetworkedStrategy

    strategy = object.__new__(NetworkedStrategy)
    strategy._blackboard = Blackboard()
    strategy._agent_order = ["agent_1"]

    text = strategy._with_board_and_uids("F25 design")
    assert "TODO BOARD" not in text and "SUGGESTED SPLIT" not in text
    assert text.startswith("F25 design")


# ---- asking for work instead of taking it ------------------------------------------------------------

def test_a_refusal_tells_the_peer_how_to_ask_the_holder():
    """agent_2 held 'mass' and needed export_cpacs, which belongs to geometry (Jessica)."""
    board = _board()
    board.claim_todo("geometry", "agent_1")
    wc._first_call_refused.add("agent_2")            # past its one CLAIM_FIRST

    refusal = wc.check("export_cpacs", "agent_2")
    assert refusal is not None and refusal["error_code"] == "CLAIMED_BY_ANOTHER_AGENT"
    assert "write_blackboard(key='request_export_cpacs'" in refusal["error"]
    assert "entry_type='gap'" in refusal["error"]
    assert "agent_1 reads the board and can run it" in refusal["error"]


def test_export_cpacs_still_belongs_to_geometry():
    """Ownership is unchanged: the peer asks for it rather than taking it over."""
    assert wc.todo_for_tool("export_cpacs") == "geometry"


# ---- B86/B87/B88 regression -------------------------------------------------------------------------

def test_the_one_claim_first_refusal_still_happens():
    _board()
    refusal = wc.check("generate_volume_mesh", "agent_1")
    assert refusal is not None and refusal["error_code"] == "CLAIM_FIRST"
    assert wc.check("generate_volume_mesh", "agent_1") is None


def test_the_holder_is_still_never_refused_its_own_work():
    board = _board()
    board.claim_todo("aero", "agent_1")
    assert wc.check("run_su2_solver", "agent_1") is None


def test_sequential_still_never_sees_a_claim_refusal():
    board = Blackboard()
    board.seed_todos(list(SEED))
    wc.register_blackboard(board)                    # board exists, structure is not networked
    assert wc.check("generate_volume_mesh", "solo") is None
