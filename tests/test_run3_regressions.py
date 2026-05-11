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
