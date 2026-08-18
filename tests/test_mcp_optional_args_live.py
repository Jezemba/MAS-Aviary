"""Live test for B64 — optional MCP arguments must stay optional.

`mcpadapt.smolagents_adapter` builds each smolagents `Tool` with
`inputs=input_schema["properties"]` and discards the JSON-Schema `required`
array. `smolagents.tools.validate_tool_arguments` then treats every input
without `nullable` as mandatory and raises
`ValueError: Argument <x> is required` for arguments the server declared
optional. Measured before the fix: 85 optional arguments across 60 tools on
five servers were presented as mandatory, worst being `generate_volume_mesh`
at 1 truly required argument shown as 11.

This test deliberately drives the LIVE servers through the real
`MCPConnector`. It must not mock `ToolCollection.from_mcp` — a mocked schema
would restate the fix instead of exercising the adapter that actually runs.

Each server is connected on its own connector. Three servers each expose a
`ping` with a DIFFERENT schema (tigl takes an optional `message`, su2 requires
`args`, pycycle takes an optional `args`), and `MCPConnector` keys its
tool→server map by tool name alone, so a single merged connector cannot say
which `ping` came from where.

Run with:
    pytest tests/test_mcp_optional_args_live.py -m live_mcp -v

Requires all five MCP servers up (8500/8200/8400/8600/8700).
"""

from __future__ import annotations

import asyncio

import pytest

from smolagents.tools import validate_tool_arguments
from src.config.loader import MCPConfig, MCPServerConfig
from src.tools.mcp_connector import MCPConnector

pytestmark = pytest.mark.live_mcp

SERVERS = [
    ("tigl", "http://127.0.0.1:8500/mcp"),
    ("su2", "http://127.0.0.1:8200/mcp"),
    ("mass", "http://127.0.0.1:8700/mcp"),
    ("pycycle", "http://127.0.0.1:8400/mcp"),
    ("aviary", "http://127.0.0.1:8600/mcp"),
]


def _declared_required(url: str) -> dict[str, set[str]]:
    """Ground truth straight from the server: the `required` array per tool."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def collect() -> dict[str, set[str]]:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
        return {
            tool.name: set((tool.inputSchema or {}).get("required", []) or [])
            for tool in listing.tools
        }

    return asyncio.run(collect())


def _connect(name: str, url: str) -> MCPConnector:
    connector = MCPConnector(
        MCPConfig(
            mode="real",
            servers=[
                MCPServerConfig(name=name, url=url, transport="streamable-http")
            ],
        )
    )
    connector.connect()
    if connector.failed_servers:
        pytest.skip(f"MCP server {name} unavailable at {url}")
    return connector


@pytest.mark.parametrize("name,url", SERVERS, ids=[s[0] for s in SERVERS])
def test_enforced_required_matches_declared_required(name: str, url: str) -> None:
    """What smolagents enforces must equal what the server declared."""
    declared = _declared_required(url)
    assert declared, f"{name} advertised no tools"
    connector = _connect(name, url)
    try:
        mismatched = {}
        checked = 0
        for tool in connector.tools:
            if tool.name not in declared:
                continue
            checked += 1
            enforced = {
                arg
                for arg, schema in tool.inputs.items()
                if not schema.get("nullable", False)
            }
            if enforced != declared[tool.name]:
                mismatched[tool.name] = {
                    "enforced": sorted(enforced),
                    "declared": sorted(declared[tool.name]),
                }
        assert checked, f"no {name} tool was compared - the test verified nothing"
        assert not mismatched, (
            f"{name} tools presenting optional args as required: {mismatched}"
        )
    finally:
        connector.disconnect()


@pytest.fixture(scope="module")
def tigl() -> MCPConnector:
    connector = _connect("tigl", "http://127.0.0.1:8500/mcp")
    yield connector
    connector.disconnect()


def test_volume_mesh_accepts_session_id_alone(tigl: MCPConnector) -> None:
    """The exact B64 reproduction: session_id alone must validate."""
    tool = next(t for t in tigl.tools if t.name == "generate_volume_mesh")
    validate_tool_arguments(tool, {"session_id": "probe-session"})


def test_volume_mesh_accepts_boundary_layer_disabled(tigl: MCPConnector) -> None:
    """The call the agent kept retrying, verbatim from its captured reasoning."""
    tool = next(t for t in tigl.tools if t.name == "generate_volume_mesh")
    validate_tool_arguments(
        tool,
        {
            "session_id": "probe-session",
            "component_uid": "Wing1",
            "boundary_layer_enabled": False,
        },
    )


def test_genuinely_required_args_still_enforced(tigl: MCPConnector) -> None:
    """The fix must not make everything optional — session_id has no default."""
    tool = next(t for t in tigl.tools if t.name == "generate_volume_mesh")
    with pytest.raises(ValueError, match="session_id"):
        validate_tool_arguments(tool, {"component_uid": "Wing1"})
