"""Live integration test for the Phase H middleware path.

Bypasses the LLM but exercises the same code chain agents go through:
``coerce_tool_arguments → resolve_request → tool.forward → intercept_response``.
Catches contract mismatches (e.g. SU2's quoted/whitespace-padded
column headers in history.csv) that a unit test against synthetic
data won't surface.

Requires su2-mcp on :8200 and aviary-mcp on :8600. Skips if either
is missing. Marked ``live_mcp`` so it stays out of the unit suite.
"""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from src.config.loader import AppConfig, LLMConfig, MCPConfig, MCPServerConfig
from src.tools.tool_loader import load_tools_for_agent


pytestmark = pytest.mark.live_mcp


# Mimics what SU2 actually writes to history.csv — column headers include
# literal double-quote characters inside whitespace-padded cells.
SU2_LIKE_HISTORY_CSV = textwrap.dedent(
    """\
    "Time_Iter","Outer_Iter","Inner_Iter",    "rms[Rho]"    ,       "CD"       ,       "CL"       ,      "CEff"
              0,           0,           0,      -1.0000,      0.0500,      0.0500,       1.000
              0,           0,           1,      -3.0000,      0.0150,      0.1200,       8.000
              0,           0,           2,      -8.0123,      0.0128,      0.1871,      14.612
    """
)


def _load_wrapped_tools() -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Tool loader uses the LLM config for some side-effects but the
        # API key isn't actually called here — set a dummy.
        os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-used"

    cfg = AppConfig(
        llm=LLMConfig(model_id="anthropic/claude-sonnet-4-20250514",
                      backend="litellm", temperature=0.0, max_new_tokens=2048),
        mcp=MCPConfig(
            mode="real",
            servers=[
                MCPServerConfig(name="su2",
                                url="http://127.0.0.1:8200/mcp",
                                transport="streamable-http"),
                MCPServerConfig(name="aviary",
                                url="http://127.0.0.1:8600/mcp",
                                transport="streamable-http"),
            ],
        ),
    )
    tools = load_tools_for_agent(
        ["create_su2_session", "read_history_csv",
         "create_session", "set_aircraft_parameters",
         "configure_mission", "get_results"],
        cfg,
    )
    return {t.name: t for t in tools}


def _get_su2_workdir(session_id: str) -> Path:
    """Locate the on-disk workdir for an SU2 session.

    su2-mcp creates sessions under /tmp/su2_session_<random> and the
    session_id maps to a workdir reachable from the filesystem. The
    test brute-force searches /tmp because su2-mcp doesn't expose the
    workdir path via the MCP API.
    """
    tmp = Path("/tmp")
    candidates = sorted(
        (p for p in tmp.iterdir() if p.is_dir() and p.name.startswith("su2_session_")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise AssertionError(
            "No /tmp/su2_session_* workdir found — is su2-mcp running?"
        )
    return candidates[0]


def test_middleware_captures_aero_and_injects_into_aviary():
    """End-to-end through wrapped tools (no LLM): read_history_csv →
    middleware captures CL/CD → set_aircraft_parameters → middleware
    injects Mission.Design.LIFT_COEFFICIENT and SUBSONIC_DRAG_COEFF_FACTOR.

    The assertion target is aviary's ``applied`` response list — if
    the middleware injected, those two keys are visible there.
    """
    tools = _load_wrapped_tools()
    for name in ("create_su2_session", "read_history_csv",
                 "create_session", "set_aircraft_parameters",
                 "configure_mission"):
        if name not in tools:
            pytest.skip(f"Tool {name!r} not loaded — required MCP not running")

    # 1. Create SU2 session and inject a realistic history.csv on disk
    create_su2 = tools["create_su2_session"]
    su2_resp = create_su2.forward()
    su2_session = (su2_resp if isinstance(su2_resp, str)
                   else json.dumps(su2_resp))
    su2_data = json.loads(su2_session) if isinstance(su2_session, str) else su2_session
    su2_session_id = su2_data["session_id"]

    workdir = _get_su2_workdir(su2_session_id)
    (workdir / "history.csv").write_text(SU2_LIKE_HISTORY_CSV)

    # 2. Read the history.csv through the wrapped tool — this should
    #    fire intercept_response → _capture_aero_coefficients.
    read_hist = tools["read_history_csv"]
    hist_resp = read_hist.forward(
        session_id=su2_session_id,
        relative_path="history.csv",
        max_rows=10,
        skip_rows=0,
    )
    # Verify the middleware actually populated the data store.
    from src.tools.data_plane import get_design_state
    ds = get_design_state()
    assert ds is not None, "data plane not initialized"
    assert ds.data_store.get("aero_cl_cruise") == pytest.approx(0.1871), (
        f"Phase H middleware did NOT capture CL from read_history_csv. "
        f"data_store keys: {sorted(ds.data_store)}. "
        f"hist_resp head: {str(hist_resp)[:300]}"
    )
    assert ds.data_store.get("aero_cd_cruise") == pytest.approx(0.0128), (
        f"Phase H middleware did NOT capture CD. Stored: "
        f"{ds.data_store.get('aero_cd_cruise')!r}"
    )

    # 3. Create an aviary session and configure mission.
    create_av = tools["create_session"]
    av_resp = create_av.forward()
    av_data = json.loads(av_resp) if isinstance(av_resp, str) else av_resp
    av_session_id = av_data["session_id"]

    configure = tools["configure_mission"]
    configure.forward(
        session_id=av_session_id,
        range_nmi=1500, num_passengers=162, cruise_mach=0.785,
        cruise_altitude_ft=35000,
    )

    # 4. Submit an 8-key parameters dict — middleware should inject
    #    the two Phase H keys before the call hits aviary-mcp.
    set_params = tools["set_aircraft_parameters"]
    params_resp = set_params.forward(
        session_id=av_session_id,
        parameters={
            "Aircraft.Wing.AREA": 130.1,
            "Aircraft.Wing.ASPECT_RATIO": 11.0,
            "Aircraft.Wing.SWEEP": 25.0,
            "Aircraft.Wing.TAPER_RATIO": 0.278,
            "Aircraft.Fuselage.LENGTH": 37.79,
            "Aircraft.Fuselage.MAX_HEIGHT": 4.06,
            "Aircraft.Fuselage.MAX_WIDTH": 3.76,
            "Aircraft.Engine.SCALE_FACTOR": 1.3,
        },
    )
    resp_data = (json.loads(params_resp)
                 if isinstance(params_resp, str) else params_resp)

    applied = resp_data.get("applied", [])
    applied_names = {entry["name"] for entry in applied
                     if isinstance(entry, dict) and "name" in entry}
    assert "Mission.Design.LIFT_COEFFICIENT" in applied_names, (
        f"Phase H middleware did NOT inject LIFT_COEFFICIENT into the "
        f"set_aircraft_parameters call. Aviary 'applied' list: "
        f"{sorted(applied_names)}"
    )
    assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" in applied_names, (
        f"Phase H middleware did NOT inject SUBSONIC_DRAG_COEFF_FACTOR. "
        f"Aviary 'applied' list: {sorted(applied_names)}"
    )

    # Spot-check the injected values landed at sensible numbers.
    lift_entry = next(e for e in applied
                      if e.get("name") == "Mission.Design.LIFT_COEFFICIENT")
    scale_entry = next(e for e in applied
                       if e.get("name") == "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR")
    assert lift_entry["new_value"] == pytest.approx(0.1871, abs=1e-3)
    # scale ≈ (0.0128 + 0.005) / (0.022 + 0.187²/(π·11·0.85)) ≈ 0.768
    assert 0.5 < float(scale_entry["new_value"]) < 1.5


@pytest.mark.live_mcp_llm
def test_middleware_fires_through_real_agent_path():
    """Same contract as the no-LLM test, but driven by a real Claude
    agent so we catch anything that breaks specifically through the
    agent's tool-call construction (e.g. JSON-string ``parameters``
    coming from mcpadapt's anyOf gap, retry loops, etc.).
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — required for live LLM tests")

    from tests.test_run3_regressions import _collect_tool_calls, _make_agent

    # Pre-create an SU2 session and stage a fake history.csv. The agent
    # only needs to *read* it — no real solve.
    raw_tools = _load_wrapped_tools()
    if "create_su2_session" not in raw_tools:
        pytest.skip("su2-mcp not running")
    su2_resp = raw_tools["create_su2_session"].forward()
    su2_data = (json.loads(su2_resp) if isinstance(su2_resp, str)
                else su2_resp)
    su2_session_id = su2_data["session_id"]
    workdir = _get_su2_workdir(su2_session_id)
    (workdir / "history.csv").write_text(SU2_LIKE_HISTORY_CSV)

    # Spin up a fresh agent with just the tools needed for the test.
    agent = _make_agent(
        tool_names=[
            "read_history_csv",
            "create_session",
            "configure_mission",
            "set_aircraft_parameters",
        ],
        servers=["su2", "aviary"],
        max_steps=8,
        instructions=(
            "You are verifying a middleware-injection contract. "
            "Step 1: call read_history_csv on the SU2 session "
            f"'{su2_session_id}' with relative_path='history.csv'. "
            "Step 2: call create_session on aviary and capture the "
            "new session_id. "
            "Step 3: call configure_mission with range_nmi=1500, "
            "num_passengers=162, cruise_mach=0.785, "
            "cruise_altitude_ft=35000. "
            "Step 4: call set_aircraft_parameters with a parameters "
            "dict containing exactly these eight keys: "
            "Aircraft.Wing.AREA=130.1, Aircraft.Wing.ASPECT_RATIO=11.0, "
            "Aircraft.Wing.SWEEP=25.0, Aircraft.Wing.TAPER_RATIO=0.278, "
            "Aircraft.Fuselage.LENGTH=37.79, "
            "Aircraft.Fuselage.MAX_HEIGHT=4.06, "
            "Aircraft.Fuselage.MAX_WIDTH=3.76, "
            "Aircraft.Engine.SCALE_FACTOR=1.3. "
            "Do NOT include any other keys. Report what was applied."
        ),
    )
    agent.run(
        "Run the four steps in order. The success criterion is that "
        "set_aircraft_parameters returns an 'applied' list — quote it "
        "verbatim in your final answer."
    )

    calls = _collect_tool_calls(agent)
    set_calls = [c for c in calls if c["name"] == "set_aircraft_parameters"]
    assert set_calls, "Agent never called set_aircraft_parameters"

    obs = set_calls[-1]["observation"] or ""
    # The injected keys must appear in aviary's 'applied' echo even
    # though the agent never put them in the request.
    assert "Mission.Design.LIFT_COEFFICIENT" in obs, (
        f"Phase H middleware did NOT inject LIFT_COEFFICIENT through "
        f"the real-agent path. set_aircraft_parameters observation: "
        f"{obs[:500]!r}"
    )
    assert "Aircraft.Design.SUBSONIC_DRAG_COEFF_FACTOR" in obs, (
        f"Phase H middleware did NOT inject SUBSONIC_DRAG_COEFF_FACTOR "
        f"through the real-agent path. Observation: {obs[:500]!r}"
    )
