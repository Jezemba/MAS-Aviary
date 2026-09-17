"""B87: a failed tool call is not a success. B88: a solve needs a mesh.

B87 -- MCP tool errors arrive as a PLAIN STRING, which classify_result could not parse and
therefore filed as a successful call with empty outputs. The B81 duplicate guard was then
armed by the phantom success: in validate9 agent_1's wrong-UID mesh failure was recorded as
done, agent_3 meshed CORRECTLY three minutes later and was refused with "Use its result: {}",
and all three peers were locked out of the only step that could produce aero. 28 phantom
successes across every run measured on the 7960, 19 of them generate_volume_mesh -- 12
wrong-UID and 7 cap refusals, every one a recoverable attempt that could never be retried.

B88 -- set_mesh had 0 calls in that run; the mesh existed only as a data-plane ref, both SU2
workdirs held config.cfg and nothing else, and run_su2_solver started anyway and died in 2.6 s
with "The SU2 mesh file named mesh.su2 was not found".

Stubbed tools only -- no MCP server, no model, no .env.
"""

import json

import pytest
from smolagents import Tool

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState
from src.tools import duplicate_guard as dg
from src.tools import knowledge_base as kbm
from src.tools import mesh_guard
from src.tools import work_claims as wc
from src.tools.agent_context import agent_scope
from src.tools.type_coercion import wrap_tool_with_middleware

# The exact string validate9 recorded as a success (B87).
TIGL_UID_ERROR = ("Error calling tool 'generate_volume_mesh': Component 'Wing' not found. "
                  "Did you mean 'Wing1'? Available UIDs: Fuselage1, Wing1, Wing2H, Wing3V. "
                  "Use one of these exactly (they are the UIDs defined in the CPACS file, "
                  "not display names).")
CAP_REFUSAL = ("Error calling tool 'generate_volume_mesh': estimated ~40,469,985 cells "
               "exceeds the 3,000,000 cap. Coarsen mesh_size_min or raise the cap.")
BIG_MESH = "U1UyIE1FU0g=" * 60

SERVERS = {"generate_volume_mesh": "tigl", "run_su2_solver": "su2", "set_mesh": "su2",
           "create_su2_session": "su2", "estimate_mass": "mass"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(dp, "_design_state", DesignState())
    monkeypatch.setattr(dp, "_registered_tools", {})
    monkeypatch.setattr(dp, "_presessions", {})
    monkeypatch.setattr(dp, "_tool_server_map", dict(SERVERS))
    kbm.configure_run(None, 0, 1, None)
    dg.register_blackboard(None)
    wc.reset()
    yield
    wc.reset()


def _tool(name, response, args=("session_id", "component_uid")):
    import inspect

    class T(Tool):
        description = "fake"
        output_type = "string"

        def forward(self, **kw):
            out = response(kw) if callable(response) else response
            return out if isinstance(out, str) else json.dumps(out)

    T.name = name
    T.inputs = {k: {"type": "any", "description": k, "nullable": True} for k in args}
    T.forward.__signature__ = inspect.Signature(
        [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
        + [inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, default=None) for k in args])
    return wrap_tool_with_middleware(T())


def _call(tool, agent, **kw):
    with agent_scope(agent, "peer"):
        return tool.forward(**kw)


# ---- B87: a string error is a failure --------------------------------------------------------

def test_the_exact_tigl_error_string_is_classified_as_failed():
    status, parsed, note = kbm.classify_result(TIGL_UID_ERROR)
    assert status == "failed"
    assert parsed == {}
    assert "Component 'Wing' not found" in note


def test_a_cap_refusal_is_also_a_failure():
    """7 of the 19 phantom mesh successes were correct calls refused by the cell cap."""
    status, _, note = kbm.classify_result(CAP_REFUSAL)
    assert status == "failed" and "3,000,000 cap" in note


@pytest.mark.parametrize("text", [
    "Error: something went wrong",
    "Traceback (most recent call last):\n  File ...",
    "Exception: boom",
])
def test_the_other_error_shapes_are_failures(text):
    assert kbm.classify_result(text)[0] == "failed"


def test_unparseable_output_is_unknown_never_success():
    """A result we cannot read is not evidence that the work was completed."""
    assert kbm.classify_result("Mesh written to /tmp/x.su2")[0] == "unknown"
    assert kbm.classify_result(12345)[0] == "unknown"
    assert kbm.classify_result(None)[0] == "unknown"


def test_real_successes_are_still_successes():
    assert kbm.classify_result(json.dumps({"success": True, "mesh_base64": "abc"}))[0] == "success"
    assert kbm.classify_result({"success": True})[0] == "success"
    assert kbm.classify_result(json.dumps({"success": False, "error": "nope"}))[0] == "failed"


# ---- B87: the guard is no longer armed by a failure --------------------------------------------

def test_a_failed_mesh_can_be_retried_immediately_by_another_peer():
    """The validate9 sequence: wrong UID fails, then a correct call must be allowed."""
    wrong = _tool("generate_volume_mesh", TIGL_UID_ERROR)
    _call(wrong, "agent_1", session_id="g", component_uid="Wing")

    entry = kbm.get_kb().entries[-1]
    assert entry["status"] == "failed", "the phantom success is what locked the peers out"

    right = _tool("generate_volume_mesh", {"success": True, "mesh_base64": BIG_MESH})
    out = json.loads(_call(right, "agent_3", session_id="g", component_uid="Wing1"))
    assert out.get("success") is True, "a correct retry must not be refused"


def test_the_same_peer_can_retry_its_own_failed_call():
    """At 21:30 agent_1 was refused for its own failed call: 'you already did'."""
    wrong = _tool("generate_volume_mesh", TIGL_UID_ERROR)
    _call(wrong, "agent_1", session_id="g", component_uid="Wing")

    right = _tool("generate_volume_mesh", {"success": True, "mesh_base64": BIG_MESH})
    out = json.loads(_call(right, "agent_1", session_id="g", component_uid="Wing1"))
    assert out.get("success") is True


def test_a_real_duplicate_is_still_refused():
    """B81 must keep working: only phantom successes stop arming it."""
    mesh = _tool("generate_volume_mesh", {"success": True, "mesh_base64": BIG_MESH})
    _call(mesh, "agent_1", session_id="g", component_uid="Wing1")
    out = json.loads(_call(mesh, "agent_2", session_id="g", component_uid="Wing1"))
    assert out.get("error_code") == "ALREADY_DONE"


def test_a_refusal_never_cites_an_empty_result():
    """'Use its result: {}' points at nothing -- if there is no result it is not a duplicate."""
    empty = _tool("generate_volume_mesh", {})                      # parsed fine, but says nothing
    _call(empty, "agent_1", session_id="g", component_uid="Wing1")

    out = _call(empty, "agent_2", session_id="g", component_uid="Wing1")
    assert "Use its result: {}" not in out
    assert json.loads(out).get("error_code") != "ALREADY_DONE"


def test_a_failed_call_never_marks_the_work_done():
    wrong = _tool("generate_volume_mesh", TIGL_UID_ERROR)
    _call(wrong, "agent_1", session_id="g", component_uid="Wing")
    assert dg.check("generate_volume_mesh",
                    dg.fingerprint("generate_volume_mesh", {"session_id": "g"}), "agent_2") is None


# ---- B88: no solve without a mesh --------------------------------------------------------------

def _su2_session(sid="36105fb1", mesh=None):
    """Put a session on the B81 guard state the way create_su2_session does."""
    gs = dg._state()
    assert gs is not None
    gs["su2"][sid] = {"mesh": mesh, "mesh_seq": None, "live": True, "config": {}}
    return sid


def test_a_solve_on_a_mesh_less_session_is_refused_before_the_server():
    sid = _su2_session()
    dp.get_design_state().data_store["generate_volume_mesh__mesh_base64"] = BIG_MESH

    refusal = mesh_guard.mesh_missing("run_su2_solver", {"session_id": sid})
    assert refusal is not None and refusal["error_code"] == "NO_MESH"
    assert "set_mesh" in refusal["error"]
    assert "generate_volume_mesh__mesh_base64" in refusal["error"], "name the ref it already holds"


def test_the_refusal_says_to_mesh_first_when_no_mesh_exists_at_all():
    sid = _su2_session()
    refusal = mesh_guard.mesh_missing("run_su2_solver", {"session_id": sid})
    assert refusal is not None
    assert "generate_volume_mesh first" in refusal["error"]
    assert "read_procedure" in refusal["error"]


def test_a_session_with_a_mesh_solves():
    sid = _su2_session(mesh="abc123")
    assert mesh_guard.mesh_missing("run_su2_solver", {"session_id": sid}) is None


def test_an_unknown_session_is_left_to_the_server():
    assert mesh_guard.mesh_missing("run_su2_solver", {"session_id": "never-seen"}) is None


def test_only_the_solver_is_gated():
    _su2_session("s1")
    for tool in ("set_mesh", "create_su2_session", "generate_volume_mesh", "estimate_mass"):
        assert mesh_guard.mesh_missing(tool, {"session_id": "s1"}) is None


def test_the_mesh_check_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AVION_REQUIRE_MESH_BEFORE_SOLVE", "0")
    sid = _su2_session()
    assert mesh_guard.mesh_missing("run_su2_solver", {"session_id": sid}) is None


def test_the_solver_refusal_reaches_the_agent_through_the_middleware():
    sid = _su2_session()
    solver = _tool("run_su2_solver", {"success": True}, args=("session_id",))
    out = json.loads(_call(solver, "agent_1", session_id=sid))
    assert out["error_code"] == "NO_MESH"


# ---- B88: a redirected session is recorded --------------------------------------------------------

def test_a_session_redirect_is_recorded_rather_than_silent():
    """agent_2's solve was silently moved onto agent_1's session; the KB and log disagreed."""
    state = dp.get_design_state()
    state.sessions["su2"] = "36105fb1"

    dp.resolve_request("run_su2_solver", {"session_id": "686966e9"})

    redirects = state.data_store.get("_session_redirects") or []
    assert redirects and redirects[-1]["asked"] == "686966e9"
    assert redirects[-1]["used"] == "36105fb1" and redirects[-1]["tool"] == "run_su2_solver"


def test_no_redirect_is_recorded_when_the_session_was_right():
    state = dp.get_design_state()
    state.sessions["su2"] = "36105fb1"
    dp.resolve_request("run_su2_solver", {"session_id": "36105fb1"})
    assert not (state.data_store.get("_session_redirects") or [])
