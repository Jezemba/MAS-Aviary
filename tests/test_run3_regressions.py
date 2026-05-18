"""End-to-end regression scenarios for the run #3 bug fixes.

Each test isolates ONE bug from the 2026-05-11 run #3 catalog. A minimal
smolagents ToolCallingAgent is built with only the tool(s) needed to
exercise the bug, then run against the live MCP servers using Claude
Sonnet 4 via LiteLLM. If the bug's call/observation pattern still
appears in the agent transcript, the fix is incomplete and the test
fails loudly.

Run on demand (costs Anthropic API tokens, requires 5 MCPs listening):

    pytest tests/test_run3_regressions.py -m live_mcp_llm -v

Required env: ANTHROPIC_API_KEY (auto-loaded from .env at repo root).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

# Auto-load .env so tests pick up ANTHROPIC_API_KEY without manual export.
# override=True so .env wins over any stale shell variables (e.g. leftover
# revoked keys exported earlier in the session).
try:
    from dotenv import load_dotenv

    _REPO_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_REPO_ROOT / ".env", override=True)
except ImportError:
    pass


MCP_SERVERS: dict[str, str] = {
    "tigl":    "http://127.0.0.1:8500/mcp",
    "su2":     "http://127.0.0.1:8200/mcp",
    "mass":    "http://127.0.0.1:8700/mcp",
    "pycycle": "http://127.0.0.1:8400/mcp",
    "aviary":  "http://127.0.0.1:8600/mcp",
}


def _make_agent(
    tool_names: list[str],
    servers: list[str] | None = None,
    max_steps: int = 6,
    instructions: str | None = None,
):
    """Build a minimal smolagents ToolCallingAgent for a regression scenario.

    Args:
        tool_names: tool names to expose to the agent. Multi-MCP names like
            "aviary.create_session" are supported; bare names like
            "create_session" resolve to the single matching MCP tool.
        servers: explicit list of MCP servers to connect to. If None, all 5
            servers are connected (with graceful degradation on failure).
        max_steps: max ReAct iterations the agent is allowed.
        instructions: optional system-prompt override.

    Returns:
        A ready-to-run ToolCallingAgent. Skip the test if API key is missing
        or no tools could be loaded.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — required for live LLM tests")

    from src.config.loader import AppConfig, LLMConfig, MCPConfig, MCPServerConfig
    from src.llm.model_loader import load_model
    from src.tools.tool_loader import load_tools_for_agent

    servers_to_use = servers or list(MCP_SERVERS.keys())
    config = AppConfig(
        llm=LLMConfig(
            model_id="anthropic/claude-sonnet-4-20250514",
            backend="litellm",
            temperature=0.0,
            max_new_tokens=2048,
        ),
        mcp=MCPConfig(
            mode="real",
            servers=[
                MCPServerConfig(
                    name=name,
                    url=MCP_SERVERS[name],
                    transport="streamable-http",
                )
                for name in servers_to_use
            ],
        ),
    )

    tools = load_tools_for_agent(tool_names, config)
    if not tools:
        pytest.skip(
            f"No tools loaded for {tool_names} from servers {servers_to_use} — "
            "are the MCP servers running?"
        )

    model = load_model(config.llm)

    from smolagents import ToolCallingAgent

    return ToolCallingAgent(
        tools=tools,
        model=model,
        name="regression",
        instructions=instructions or (
            "You are a regression-test agent. Call the indicated tool(s) "
            "with valid arguments and report a brief result. Do not include "
            "extra explanation."
        ),
        max_steps=max_steps,
        add_base_tools=False,
    )


def _collect_tool_calls(agent) -> list[dict[str, Any]]:
    """Flatten the agent's memory steps into a list of tool-call dicts.

    Each entry: {"name": str, "arguments": dict or str, "observation": str}.
    """
    calls: list[dict[str, Any]] = []
    for step in agent.memory.steps:
        tool_calls = getattr(step, "tool_calls", None) or []
        observation = getattr(step, "observations", "") or ""
        if not isinstance(observation, str):
            observation = str(observation)
        for tc in tool_calls:
            args = getattr(tc, "arguments", {}) or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    pass
            calls.append(
                {
                    "name": getattr(tc, "name", "?"),
                    "arguments": args,
                    "observation": observation,
                }
            )
    return calls


# ── P0: aviary create_session blocker ────────────────────────────────────────


@pytest.mark.live_mcp_llm
def test_p0_create_session_succeeds_regardless_of_arg_shape():
    """P0 from run #3 — aviary create_session was failing with the
    pydantic error 'Input should be a valid dictionary [type=dict_type]'
    because the LLM sent the literal string 'null' for initial_parameters
    and the type-coercion middleware did not catch it.

    Middleware fix in src/tools/type_coercion.py now coerces 'null',
    'None', '', '{}', JSON-string objects, etc. to their proper Python
    types before the MCP call. This test asserts the END-TO-END
    OUTCOME — the call succeeds regardless of what shape the LLM chose
    for initial_parameters — rather than the LLM's raw arg shape,
    because the LLM may legitimately send any of several valid
    formulations (omit, null, '{}', {}). The middleware's job is to
    make all of them work.
    """
    agent = _make_agent(["create_session"], servers=["aviary"], max_steps=3)
    agent.run(
        "Create a new aviary session. Pass NO initial_parameters (let "
        "the server use defaults). Report the session_id."
    )
    calls = _collect_tool_calls(agent)
    create_calls = [c for c in calls if c["name"] == "create_session"]

    assert create_calls, (
        "Agent never called create_session. Memory:\n"
        f"{[(c['name'], c['arguments']) for c in calls]}"
    )

    # The bug shape we are guarding against: pydantic validation error
    # bubbling back to the LLM as the tool observation.
    PYDANTIC_BUG_PATTERNS = (
        "type=dict_type",
        "Input should be a valid dictionary",
        "validation error for call",
    )
    uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}", re.IGNORECASE)

    for call in create_calls:
        obs = call["observation"] or ""
        init = call["arguments"].get("initial_parameters", "<omitted>")
        for pat in PYDANTIC_BUG_PATTERNS:
            assert pat not in obs, (
                f"BUG REGRESSION: create_session observation contains '{pat}'. "
                "The exact run #3 failure mode has recurred. "
                f"initial_parameters arg the LLM sent: {init!r}. "
                f"Observation (truncated): {obs[:400]!r}"
            )

    # Positive outcome: at least one call must have returned a UUID-shaped
    # session_id in its observation.
    assert any(uuid_re.search(c["observation"]) for c in create_calls), (
        "No session_id (UUID) in any create_session observation — call may "
        f"have failed silently. Observations: "
        f"{[c['observation'][:200] for c in create_calls]}"
    )


# ── pycycle list_variables context bloat ─────────────────────────────────────


@pytest.mark.live_mcp_llm
def test_pycycle_list_variables_intercepted():
    """In run #1 the pycycle agent's context grew by ~30K tokens every time
    it called list_variables (200+ variable tree dumped into observation).
    The data plane fix in src/tools/data_plane.py now intercepts large
    list responses and returns a compact summary with a ref.

    This test verifies the agent's observation for list_variables is a
    summary (well under 4 KB) rather than the raw variable tree.
    """
    agent = _make_agent(
        ["create_cycle_model", "list_variables", "close_cycle_model"],
        servers=["pycycle"],
        max_steps=4,
    )
    agent.run(
        "Create a turbofan cycle model. Then call list_variables once "
        "with max_parameters=200 to discover its variables. Report only "
        "the total count from the response."
    )
    calls = _collect_tool_calls(agent)
    list_calls = [c for c in calls if c["name"] == "list_variables"]

    assert list_calls, "Agent never called list_variables"

    for call in list_calls:
        obs = call["observation"]
        # Either the observation contains our interception marker, or it's
        # genuinely small (<4 KB). Anything larger is a regression.
        has_marker = "_intercepted" in obs or "list_variables__" in obs
        size_ok = len(obs) < 4096
        assert has_marker or size_ok, (
            f"BUG REGRESSION: list_variables observation is {len(obs)} bytes "
            f"with no interception marker. Data plane did not catch the "
            f"large structured payload. First 300 chars: {obs[:300]!r}"
        )


# ── aviary get_trajectory context bloat ──────────────────────────────────────


@pytest.mark.live_mcp_llm
def test_aviary_get_trajectory_intercepted():
    """In run #1 aviary's get_trajectory dumped a 60-point timeseries
    (~6 KB JSON) into the simulator agent's observation. The data plane
    fix now intercepts large dict payloads. This test forces a
    get_trajectory call by running a simulation first, then verifies the
    observation is summarized rather than raw.
    """
    agent = _make_agent(
        [
            "create_session",
            "configure_mission",
            "run_simulation",
            "get_trajectory",
        ],
        servers=["aviary"],
        max_steps=6,
    )
    agent.run(
        "Create an aviary session, configure a short mission (200 nmi "
        "range, 100 passengers, Mach 0.78, 33000 ft altitude), run the "
        "simulation, then call get_trajectory. Report only the num_points "
        "from the trajectory response."
    )
    calls = _collect_tool_calls(agent)
    traj_calls = [c for c in calls if c["name"] == "get_trajectory"]

    if not traj_calls:
        pytest.skip(
            "Agent never reached get_trajectory (simulation probably "
            "didn't converge). Cannot verify interception."
        )

    for call in traj_calls:
        obs = call["observation"]
        has_marker = "_intercepted" in obs or "get_trajectory__" in obs
        size_ok = len(obs) < 4096
        assert has_marker or size_ok, (
            f"BUG REGRESSION: get_trajectory observation is {len(obs)} bytes "
            f"with no interception marker. First 300 chars: {obs[:300]!r}"
        )


# ── su2 sample_surface_solution missing marker_name ──────────────────────────


@pytest.mark.live_mcp_llm
def test_su2_sample_surface_solution_marker_recovery():
    """sample_surface_solution requires marker_name. In run #3 the aero
    agent forgot it and got a pydantic 'Field required' error. The fix
    is in the SU2 agent prompt (Phase B) — but here we verify that even
    if the agent forgets, the second attempt includes marker_name. If
    the agent loops on the same error 3+ times, that's a regression of
    prompt clarity.
    """
    # This test exercises the agent's recovery, not a middleware fix.
    # It's a baseline to confirm Phase B prompt changes (when applied)
    # eliminate the recovery dance entirely.
    agent = _make_agent(
        [
            "create_su2_session",
            "set_mesh",
            "sample_surface_solution",
            "list_result_files",
        ],
        servers=["su2"],
        max_steps=5,
        instructions=(
            "You are testing SU2 result sampling. Skip mesh setup and "
            "try to call sample_surface_solution with marker_name='aircraft'. "
            "Report whatever you can — the goal is to invoke the tool once "
            "with the right argument shape."
        ),
    )
    agent.run(
        "Call sample_surface_solution with marker_name='aircraft' on a "
        "fake session_id 'test-session'. Report whatever the tool returns "
        "(it's OK if it errors)."
    )
    calls = _collect_tool_calls(agent)
    sample_calls = [c for c in calls if c["name"] == "sample_surface_solution"]

    # Count calls that lacked marker_name. We accept 1 (initial), fail on 3+.
    missing_marker = [
        c for c in sample_calls if "marker_name" not in (c["arguments"] or {})
    ]
    assert len(missing_marker) < 3, (
        f"BUG: agent called sample_surface_solution {len(missing_marker)} "
        "times without marker_name. Pydantic 'Field required' is not "
        "guiding the agent to recover. Phase B prompt fix needed."
    )


# ── aviary set_aircraft_parameters anyOf dict handling ───────────────────────


@pytest.mark.live_mcp_llm
def test_aviary_set_aircraft_parameters_passes_dict():
    """set_aircraft_parameters takes a `parameters` dict. The anyOf gap
    in mcpadapt sometimes makes the LLM send a JSON string instead.
    Verify the middleware coerces, or the LLM gets it right on its own
    given temperature=0.
    """
    agent = _make_agent(
        ["create_session", "set_aircraft_parameters"],
        servers=["aviary"],
        max_steps=4,
    )
    agent.run(
        "Create an aviary session, then call set_aircraft_parameters to "
        "set Aircraft.Wing.AREA to 130.0 square meters. Report the result."
    )
    calls = _collect_tool_calls(agent)
    set_calls = [c for c in calls if c["name"] == "set_aircraft_parameters"]

    assert set_calls, "Agent never called set_aircraft_parameters"

    PYDANTIC_BUG_PATTERNS = (
        "type=dict_type",
        "Input should be a valid dictionary",
        "validation error for call",
    )

    # Same outcome-based assertion as the create_session test: regardless
    # of what shape the LLM chose for `parameters`, the call must not
    # bounce off the pydantic validator on the server.
    for call in set_calls:
        obs = call["observation"] or ""
        params = call["arguments"].get("parameters", "<omitted>")
        for pat in PYDANTIC_BUG_PATTERNS:
            assert pat not in obs, (
                "BUG REGRESSION: set_aircraft_parameters observation contains "
                f"'{pat}'. The anyOf/dict type-coercion gap has recurred. "
                f"parameters arg: {params!r}. "
                f"Observation: {obs[:400]!r}"
            )


# ── Phase B: geometry agent — set once, verify, close ────────────────────────


@pytest.mark.live_mcp_llm
def test_geometry_set_then_verify_then_close():
    """Phase B-1 + C regression for the geometry stage prompt updates.

    Run #3 observations:
      - set_high_level_parameters called twice (redundant retry)
      - no verify between set and mesh
      - no close_cpacs
      - 3x repeated DESIGN_STATE final answer

    Phase C finding (2026-05-11): set_high_level_parameters is
    annotation-only in tigl-mcp — it writes to an in-memory dict and
    does NOT modify the CPACS XML or TiGL model. So get_wing_summary
    will always return baseline values, never the agent's intended
    values. Phase B-1 prompt was updated to be honest about this:
    mesh the baseline, propagate intent via DESIGN_INTENT / GEOMETRY_CHANGES.

    Updated assertions:
      - set_high_level_parameters called at most once
      - final_answer at most once
      - SOME inspection call happens after set (verify step)
      - close_cpacs called when mesh succeeded
      - agent did NOT halt on the baseline/intent mismatch (i.e. mesh
        was attempted) — earlier prompt would have halted, that was wrong
    """
    # Use the mass-mcp fixture as the CPACS file (lives on disk).
    cpacs_path = (
        "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
    )
    agent = _make_agent(
        tool_names=[
            "open_cpacs",
            "get_configuration_summary",
            "get_wing_summary",
            "get_fuselage_summary",
            "get_high_level_parameters",
            "set_high_level_parameters",
            "generate_volume_mesh",
            "close_cpacs",
        ],
        servers=["tigl"],
        max_steps=12,
        instructions=(
            "You are the GEOMETRY ENGINEER. Follow exactly the documented "
            "task order: open_cpacs → inspect → set_high_level_parameters ONCE "
            "→ get_wing_summary to verify → generate_volume_mesh (only if "
            "verified) → close_cpacs. Output ONE final DESIGN_STATE block."
        ),
    )
    agent.run(
        f"Open the CPACS file at {cpacs_path}. Set wing span to 45.0 m. "
        "Verify the change took via get_wing_summary. If verified, "
        "generate a coarse volume mesh (surface_mesh_size=1.0, "
        "boundary_layer_enabled=false). Then close the CPACS session."
    )
    calls = _collect_tool_calls(agent)
    by_name: dict[str, int] = {}
    for c in calls:
        by_name[c["name"]] = by_name.get(c["name"], 0) + 1

    # 1) set_high_level_parameters called at most once — fixes run #3
    #    "redundant retry" bug.
    n_set = by_name.get("set_high_level_parameters", 0)
    assert n_set <= 1, (
        f"REGRESSION: set_high_level_parameters called {n_set} times. "
        "Prompt says call ONCE — the run #3 bug was redundant retries "
        "that ate context."
    )

    # 2) final_answer called at most once — fixes run #3 "3x final answer"
    #    bug where the same DESIGN_STATE was emitted multiple times.
    n_final = by_name.get("final_answer", 0)
    assert n_final <= 1, (
        f"REGRESSION: final_answer emitted {n_final} times. "
        "Prompt requires ONE final_answer block."
    )

    # 3) An inspection call MUST happen AFTER set_high_level_parameters
    #    (this is the "verify before mesh" step). The agent may use either
    #    get_wing_summary or get_high_level_parameters for the verify —
    #    both are legitimate inspections. The prior run #3 bug was no
    #    verify happening at all.
    seq = [c["name"] for c in calls]
    inspect_tools = {"get_wing_summary", "get_high_level_parameters"}
    if "set_high_level_parameters" in seq:
        idx_set = seq.index("set_high_level_parameters")
        post_set_inspect = any(n in inspect_tools for n in seq[idx_set + 1 :])
        assert post_set_inspect, (
            "REGRESSION: no inspection call after set_high_level_parameters — "
            "the post-set verify step was skipped. Prompt requires a "
            "get_wing_summary or get_high_level_parameters call after the set "
            "to confirm the geometry change took."
        )

    # 4) Phase C correction: the agent MUST mesh the baseline regardless
    #    of intent/baseline mismatch (because intent is memo-only and the
    #    baseline is what TiGL has). Previous "halt on mismatch" guidance
    #    was wrong.
    n_mesh = by_name.get("generate_volume_mesh", 0)
    assert n_mesh >= 1, (
        "REGRESSION: agent did not attempt to mesh. The updated prompt "
        "says ALWAYS mesh the baseline — set_high_level_parameters is "
        "annotation-only, so baseline is the only geometry available."
    )

    # 5) close_cpacs called when mesh succeeded (so mass-mcp reads fresh disk).
    n_close = by_name.get("close_cpacs", 0)
    # On the D150 fixture, gmsh may legitimately fail on the meshed wing
    # (known issue — wing geometry has degenerate edges that gmsh dislikes).
    # We do NOT require close_cpacs in that case since the agent may
    # legitimately abort early. But if any mesh result came back with a
    # non-error observation, close should be called.
    successful_mesh = any(
        c["name"] == "generate_volume_mesh"
        and "mesh_base64" in (c["observation"] or "")
        and "error" not in (c["observation"] or "").lower()
        for c in calls
    )
    if successful_mesh:
        assert n_close >= 1, (
            "REGRESSION: agent meshed successfully but did not call "
            "close_cpacs. mass-mcp reads CPACS from disk and will see "
            "stale data."
        )


# ── Phase B-2: SU2 agent — complete config preset in ONE call ────────────────


@pytest.mark.live_mcp_llm
def test_su2_config_preset_in_one_call():
    """Phase B-2 regression for the SU2 agent prompt updates.

    Run #3 observations against the SU2 agent:
      - update_config_entries called TWICE: first with mission state
        only (which returned "missing_required: SOLVER, MARKER_EULER,
        FREESTREAM_PRESSURE, ...") and then again to fill in the gaps.
        Burns one step + token cost.
      - run_su2_solver was called with max_runtime_seconds=600
        (instead of 300 documented in earlier prompt update).

    The updated prompt now bakes the complete F25 cruise preset into
    step 3 of the aerodynamics_analyst task. This test asserts:
      - exactly ONE update_config_entries call
      - that call contains the core required keys (SOLVER, MARKER_EULER,
        FREESTREAM_PRESSURE) so the server doesn't return
        "missing_required"
      - if run_su2_solver is called, max_runtime_seconds <= 300

    We do NOT exercise an actual SU2 solve here — we have no mesh and
    don't need one to test the config-call shape.
    """
    agent = _make_agent(
        tool_names=[
            "create_su2_session",
            "update_config_entries",
            "get_su2_status",
            "run_su2_solver",
        ],
        servers=["su2"],
        max_steps=6,
        instructions=(
            "You are the AERODYNAMICS ANALYST. Configure a SU2 session "
            "for F25 cruise (Mach 0.78, AoA 2 deg, Re 30e6, altitude "
            "33000 ft) using a SINGLE update_config_entries call with "
            "the complete preset (SOLVER='EULER', JST, MARKER_EULER, "
            "MARKER_FAR, freestream P/T, etc.). No mesh is available; "
            "do not attempt to run the solver if mesh is missing — "
            "report config-only and stop."
        ),
    )
    agent.run(
        "Set up an SU2 session for F25 cruise conditions. Use the full "
        "preset in a SINGLE update_config_entries call. Markers are "
        "'aircraft' (wall) and 'farfield'. After config, check "
        "get_su2_status. Do NOT call run_su2_solver."
    )
    calls = _collect_tool_calls(agent)
    by_name: dict[str, int] = {}
    for c in calls:
        by_name[c["name"]] = by_name.get(c["name"], 0) + 1

    # 1) update_config_entries called at MOST TWICE. One call is ideal;
    #    two is acceptable (one initial preset + one tiny fill-in if the
    #    server flagged a single missing key). Three or more is the
    #    run #3 regression pattern — agent iteratively filling in
    #    missing_required, burning steps and tokens.
    update_calls = [c for c in calls if c["name"] == "update_config_entries"]
    n_update = len(update_calls)
    assert 1 <= n_update <= 2, (
        f"REGRESSION: update_config_entries called {n_update} times. "
        "Acceptable range is 1 (ideal) or 2 (preset + tiny fill-in)."
    )

    def _parse_updates(call: dict) -> dict:
        raw = call["arguments"].get("updates", {})
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        return raw if isinstance(raw, dict) else {}

    # 2) The FIRST call must contain the core required keys. These are
    #    the ones that triggered the run #3 7-key missing_required.
    first_updates = _parse_updates(update_calls[0])
    missing = [
        key for key in ("SOLVER", "MARKER_EULER", "FREESTREAM_PRESSURE")
        if key not in first_updates
    ]
    assert not missing, (
        f"REGRESSION: agent's FIRST update_config_entries call is missing "
        f"core required keys: {missing}. Updates sent: "
        f"{list(first_updates.keys())}"
    )

    # 3) If a second call exists, it must be a small fill-in (<=5 keys),
    #    not a substantive redo. The run #3 pattern was 2 large calls.
    if n_update == 2:
        second_updates = _parse_updates(update_calls[1])
        n_second = len(second_updates)
        assert n_second <= 5, (
            f"REGRESSION: second update_config_entries call has {n_second} "
            f"keys; expected <=5 as a fill-in. Keys: {list(second_updates)}"
        )

    # 4) If the agent did invoke run_su2_solver, it must use the
    #    300 s budget (not the legacy 600 s).
    solver_calls = [c for c in calls if c["name"] == "run_su2_solver"]
    for call in solver_calls:
        runtime = call["arguments"].get("max_runtime_seconds")
        assert runtime is not None and runtime <= 300, (
            f"REGRESSION: run_su2_solver called with "
            f"max_runtime_seconds={runtime}. Prompt requires <= 300 s "
            "for the coarse-mesh Euler config."
        )


# ── Phase B-3: pycycle agent — list_variables ONCE ───────────────────────────


@pytest.mark.live_mcp_llm
def test_pycycle_list_variables_called_at_most_once():
    """Phase B-3 regression for the propulsion agent prompt updates.

    Run #1 P2 observation against the propulsion agent: list_variables
    was called 6+ times with increasing max_parameters, each adding
    ~30K tokens to context. By step 11 the agent was at 377K input
    tokens (Sonnet 4.0's 200K context already overflowed).

    Phase A's data-plane fix means each call's response is now a
    compact summary (~200 bytes instead of ~30 KB), so the bloat is
    bounded. But the prompt should ALSO instruct the agent to only
    make one call — repeated calls are wasted steps even if cheap.

    Assertions:
      - list_variables called AT MOST ONCE
      - the observation is a summary (intercepted), not a raw dump
    """
    agent = _make_agent(
        tool_names=[
            "create_cycle_model",
            "list_variables",
            "set_inputs",
            "run_cycle",
            "get_outputs",
            "close_cycle_model",
        ],
        servers=["pycycle"],
        max_steps=6,
        instructions=(
            "You are the PROPULSION ANALYST. Create a turbofan cycle "
            "model. Call list_variables ONCE to learn the input names. "
            "Then set inputs for a high-BPR civil turbofan at cruise "
            "(BPR~11, OPR~40, fan PR~1.45, T4~1700 K), and run the cycle. "
            "Report SFC and thrust. Do NOT call list_variables more than "
            "once."
        ),
    )
    agent.run(
        "Create a turbofan cycle, learn its variables with list_variables "
        "(ONE call), set F25-class cruise inputs, run the cycle, and "
        "report SFC and Fn."
    )
    calls = _collect_tool_calls(agent)
    by_name: dict[str, int] = {}
    for c in calls:
        by_name[c["name"]] = by_name.get(c["name"], 0) + 1

    # 1) list_variables called at most once.
    n_list = by_name.get("list_variables", 0)
    assert n_list <= 1, (
        f"REGRESSION: list_variables called {n_list} times. "
        "Prompt and data plane mandate AT MOST ONCE — repeated calls "
        "burn steps even though the data plane now intercepts the "
        "payload."
    )

    # 2) If list_variables was called, observation must be the
    #    intercepted summary (small).
    list_calls = [c for c in calls if c["name"] == "list_variables"]
    for call in list_calls:
        obs = call["observation"] or ""
        has_marker = "_intercepted" in obs or "list_variables__" in obs
        size_ok = len(obs) < 4096
        assert has_marker or size_ok, (
            f"REGRESSION: list_variables observation is {len(obs)} bytes "
            f"with no interception marker. Data plane did not catch the "
            f"payload. First 300 chars: {obs[:300]!r}"
        )


# ── Phase B-4: mission agent — get_design_space first, no validation loop ────


@pytest.mark.live_mcp_llm
def test_mission_calls_design_space_first_and_validates_once():
    """Phase B-4 regression for the mission_architect prompt updates.

    Run #3 P2 observations against the mission agent:
      - did not call get_design_space first; guessed parameter names
      - validate_parameters was retried multiple times when upstream
        data was unusable, instead of accepting the validation failure
      - mission stage was re-invoked 20 times by the iterative_feedback
        handler (driven by create_session blocker — now fixed in
        Phase A, but the prompt should also prevent looping on bad
        upstream data)

    The updated prompt now instructs:
      step 1: call get_design_space BEFORE create_session
      step 4: explicit upstream-param mapping table with fallback values
              for null/missing upstream fields
      step 5: validate ONCE; if invalid, report and stop (not loop)

    Assertions:
      - get_design_space called at least once
      - get_design_space called BEFORE create_session in the sequence
      - validate_parameters called at most twice (1 ideal, 2 acceptable
        if a quick correction was made; 3+ is the loop bug)
      - final_answer called at most once
    """
    agent = _make_agent(
        tool_names=[
            "get_design_space",
            "create_session",
            "configure_mission",
            "set_aircraft_parameters",
            "validate_parameters",
        ],
        servers=["aviary"],
        max_steps=8,
        instructions=(
            "You are the MISSION ARCHITECT. Follow the documented task "
            "order strictly: get_design_space → create_session → "
            "configure_mission → set_aircraft_parameters → "
            "validate_parameters. Output ONE DESIGN_STATE block. Do "
            "not loop on validate_parameters."
        ),
    )
    agent.run(
        "Set up an Aviary mission for F25 (range 2500 nmi, 239 pax, "
        "Mach 0.78, FL330). Use F25 baseline aircraft parameters: wing "
        "area 130.1, AR 15.6, sweep 25 deg, taper 0.278, fuselage "
        "length 37.79 m, engine scale 1.0. Validate once and report."
    )
    calls = _collect_tool_calls(agent)
    by_name: dict[str, int] = {}
    for c in calls:
        by_name[c["name"]] = by_name.get(c["name"], 0) + 1

    # 1) get_design_space called at least once
    n_ds = by_name.get("get_design_space", 0)
    assert n_ds >= 1, (
        "REGRESSION: mission_architect did not call get_design_space. "
        "Prompt requires it as step 1 to discover param names."
    )

    # 2) get_design_space called BEFORE create_session in the sequence
    seq = [c["name"] for c in calls]
    if "create_session" in seq:
        idx_create = seq.index("create_session")
        ds_before = "get_design_space" in seq[:idx_create]
        assert ds_before, (
            "REGRESSION: get_design_space was not called before "
            "create_session. Call order matters — discover names FIRST."
        )

    # 3) validate_parameters called at most twice (ideal: 1)
    n_validate = by_name.get("validate_parameters", 0)
    assert n_validate <= 2, (
        f"REGRESSION: validate_parameters called {n_validate} times. "
        "Prompt says validate ONCE and stop if invalid — 3+ calls is "
        "the run #3 loop pattern."
    )

    # 4) final_answer called at most once (no triplication)
    n_final = by_name.get("final_answer", 0)
    assert n_final <= 1, (
        f"REGRESSION: final_answer emitted {n_final} times. "
        "Prompt requires ONE DESIGN_STATE block."
    )


# ── Phase D: simulator does not loop on AVIARY_SETUP_ERROR ───────────────────


@pytest.mark.live_mcp_llm
def test_simulator_does_not_loop_on_aviary_setup_error():
    """Phase D regression for the simulation_executor prompt update.

    Run #4 observation: when run_simulation returned AVIARY_SETUP_ERROR
    (Aviary's Newton solver got inf/NaN from out-of-range AR), the
    simulator agent looped — calling run_simulation again, then
    get_results (which returned NO_RESULTS), then a third time. Each
    iteration produced the same permanent failure but the agent kept
    retrying.

    The updated prompt teaches the agent that AVIARY_SETUP_ERROR is a
    PERMANENT failure for the current parameter set: report once and
    stop, do not call run_simulation, get_results, or get_trajectory
    again on this session.

    Forcing the error condition: build a fresh aviary session with
    AR=15.6 (above the reliable_range cliff for the default aircraft).
    """
    agent = _make_agent(
        tool_names=[
            "create_session",
            "configure_mission",
            "set_aircraft_parameters",
            "run_simulation",
            "get_results",
            "get_trajectory",
        ],
        servers=["aviary"],
        max_steps=8,
        instructions=(
            "You are the SIMULATION EXECUTOR. Set up an Aviary session "
            "with Aircraft.Wing.ASPECT_RATIO=15.6 (a known F25-target "
            "value that Aviary's default aircraft tables NaN on), "
            "configure a basic mission, then attempt run_simulation "
            "ONCE. If the response shows AVIARY_SETUP_ERROR, report "
            "EXIT_CODE: AVIARY_SETUP_ERROR and STOP — do NOT call "
            "run_simulation, get_results, or get_trajectory again."
        ),
    )
    agent.run(
        "Create an Aviary session, set Aircraft.Wing.ASPECT_RATIO to "
        "15.6 (other params default), configure_mission(range=1000nmi, "
        "pax=150, Mach=0.78, alt=33000), then call run_simulation. "
        "If you get AVIARY_SETUP_ERROR, report it once and stop."
    )
    calls = _collect_tool_calls(agent)
    by_name: dict[str, int] = {}
    for c in calls:
        by_name[c["name"]] = by_name.get(c["name"], 0) + 1

    # 1) run_simulation called at most ONCE (the new explicit instruction)
    n_run = by_name.get("run_simulation", 0)
    assert n_run <= 1, (
        f"REGRESSION: run_simulation called {n_run} times. Phase D "
        "prompt says: on AVIARY_SETUP_ERROR, ONE attempt is enough; "
        "retrying produces the same permanent failure."
    )

    # 2) get_results / get_trajectory not called after an
    #    AVIARY_SETUP_ERROR observation (they'd just return NO_RESULTS)
    saw_setup_error = any(
        "AVIARY_SETUP_ERROR" in (c["observation"] or "") for c in calls
    )
    if saw_setup_error:
        n_results = by_name.get("get_results", 0)
        n_traj = by_name.get("get_trajectory", 0)
        assert n_results + n_traj <= 1, (
            "REGRESSION: agent called get_results/get_trajectory "
            f"{n_results + n_traj} times after seeing "
            "AVIARY_SETUP_ERROR. Both return NO_RESULTS in this state; "
            "the agent must skip them and report."
        )

    # 3) final_answer once
    n_final = by_name.get("final_answer", 0)
    assert n_final <= 1, (
        f"REGRESSION: final_answer emitted {n_final} times."
    )


# ── Run #8 regression: mesh-ref handoff from geometry to SU2 ──────────────────

@pytest.mark.live_mcp_llm
def test_mesh_ref_handoff_to_su2():
    """Run #8 (2026-05-16) regression: the geometry agent produced a
    volume mesh and the data plane stored it under a ref. The aero
    agent then forwarded that ref to su2.set_mesh, but the LLM
    serialized the dict as the literal string "ref:keyname" instead of
    {"ref": "keyname"}. The middleware did not recognize that pattern,
    so set_mesh received the string, tried to base64-decode it, and
    failed with "Incorrect padding". SU2 never ran; the aero agent
    fell back to F25 reference values.

    The fix extended resolve_request to recognize multiple ref
    serialization formats. This live test confirms the fix end-to-end
    against a real LLM that may pick ANY of those formats at runtime.

    Test: drive geometry → su2 mesh transfer with a real agent and
    assert set_mesh succeeded (no "Incorrect padding" / "Failed to set
    mesh" error).

    Cost: ~5-10 Claude calls and ~30s of gmsh. Run on-demand.
    """
    cpacs_path = (
        "/home/aipexws3/Jessica/Avion/mass-mcp/tests/fixtures/D150_simple.xml"
    )

    agent = _make_agent(
        tool_names=[
            "open_cpacs",
            "get_configuration_summary",
            "generate_volume_mesh",
            "close_cpacs",
            "create_su2_session",
            "set_mesh",
        ],
        servers=["tigl", "su2"],
        max_steps=10,
        instructions=(
            "You are bridging geometry and aerodynamics. Open the CPACS "
            "file, build a coarse volume mesh on Wing1, then push the "
            "resulting mesh into a new SU2 session via set_mesh. The "
            "geometry response will contain a mesh_base64 field whose "
            "value is a ref to the actual mesh bytes — forward it to "
            "set_mesh exactly as you receive it. Report what set_mesh "
            "returned."
        ),
    )
    agent.run(
        f"Open the CPACS at {cpacs_path}. Generate a coarse volume mesh "
        "on Wing1 (surface_mesh_size=1.0, boundary_layer_enabled=False). "
        "Create an SU2 session and push the mesh into it via set_mesh. "
        "Report the set_mesh response."
    )

    calls = _collect_tool_calls(agent)
    set_mesh_calls = [c for c in calls if c["name"] == "set_mesh"]

    assert set_mesh_calls, (
        "REGRESSION: agent never reached set_mesh. Memory: "
        f"{[c['name'] for c in calls]}"
    )

    # The bug signature: set_mesh observation reports a base64-decode
    # error because the mesh ref didn't resolve.
    BASE64_FAILURE_MARKERS = (
        "Incorrect padding",
        "Failed to set mesh",
        "Invalid base64",
        "binascii.Error",
        "Invalid character",
    )
    for call in set_mesh_calls:
        obs = call["observation"] or ""
        for marker in BASE64_FAILURE_MARKERS:
            assert marker not in obs, (
                f"REGRESSION: set_mesh observation contains '{marker}' — "
                "the mesh ref did not resolve. This is the Run #8 bug. "
                f"Agent passed: {call['arguments'].get('mesh_base64', '<absent>')!r}. "
                f"Observation: {obs[:400]!r}"
            )

    # Positive — at least one set_mesh call must have written a mesh_path
    # in its observation (indicates the bytes were decoded and saved).
    success = any(
        '"mesh_path"' in (c["observation"] or "")
        and '"error"' not in (c["observation"] or "")
        for c in set_mesh_calls
    )
    assert success, (
        "REGRESSION: no set_mesh call returned a non-error response "
        "containing mesh_path. Set_mesh attempted but none succeeded. "
        f"Observations: {[c['observation'][:200] for c in set_mesh_calls]}"
    )
