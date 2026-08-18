"""MCP connector — discovers and loads tools from real MCP servers.

Uses smolagents.ToolCollection.from_mcp() to connect to MCP servers
and discover available tools via the Model Context Protocol.

Supports multiple named MCP servers with:
- Per-server tool tracking and routing
- Graceful degradation when individual servers are unavailable
- Tool-to-server lookup for call routing
- Backward compatibility with single-server configs
- Restoring optional-argument status that the smolagents MCP adapter drops (B64)
"""

import logging

from smolagents import Tool, ToolCollection

from src.config.loader import MCPConfig, MCPServerConfig

logger = logging.getLogger(__name__)


def _fetch_required_arguments(url: str, transport: str) -> dict[str, set[str]] | None:
    """Ask the server directly which arguments each of its tools requires.

    This is the ground truth that `mcpadapt` throws away. Returns None when it
    cannot be fetched (unsupported transport, server hiccup), so the caller can
    fall back to inferring from schema defaults.
    """
    if transport != "streamable-http":
        return None
    try:
        import asyncio

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
    except Exception as e:  # noqa: BLE001 - degrade to the heuristic, never break connect()
        logger.warning("B64: could not read required-argument schema from %s: %s", url, e)
        return None


def _restore_optional_arguments(
    tools: list[Tool],
    server_name: str,
    required_map: dict[str, set[str]] | None,
) -> int:
    """Re-mark optional arguments as optional (B64).

    `mcpadapt.smolagents_adapter` builds a smolagents `Tool` with
    `inputs=input_schema["properties"]` and discards the JSON-Schema `required`
    array. `smolagents.tools.validate_tool_arguments` then treats every input
    lacking `nullable` as mandatory, so it raises
    `ValueError: Argument <x> is required` for arguments the server declared
    optional. The agent sees a fresh "required" complaint on each retry and
    fills one argument in at a time, burning a turn per argument. Measured
    before this fix: 85 optional arguments across 60 tools on five servers were
    presented as mandatory, worst `generate_volume_mesh` at 1 truly required
    argument shown as 11.

    `required_map` is what the server itself declares and is authoritative.
    Without it, an argument carrying a `default` is treated as optional; that
    agrees with the declared `required` array on all 60 real tools, but not on
    every tool (`pycycle.ping` declares no required arguments yet gives its
    `args` no default), which is why the declared array is preferred.

    Returns the number of arguments restored, for logging.
    """
    restored = 0
    for tool in tools:
        inputs = getattr(tool, "inputs", None)
        if not isinstance(inputs, dict):
            continue
        required = None if required_map is None else required_map.get(tool.name)
        for arg_name, schema in inputs.items():
            if not isinstance(schema, dict) or schema.get("nullable", False):
                continue
            if required is None:
                optional = "default" in schema
            else:
                optional = arg_name not in required
            if optional:
                schema["nullable"] = True
                restored += 1
                logger.debug(
                    "B64: %s.%s.%s marked optional", server_name, tool.name, arg_name
                )
    return restored


class MCPConnector:
    """Manages connections to one or more MCP servers and exposes their tools."""

    def __init__(self, config: MCPConfig):
        self._config = config
        self._collections: list = []  # active context managers
        self._tools: list[Tool] = []
        # Maps tool_name → server_name for routing calls to the correct server.
        self._tool_server_map: dict[str, str] = {}
        # Maps server_name → list of tool names from that server.
        self._server_tools: dict[str, list[str]] = {}
        # Tracks which servers failed to connect.
        self._failed_servers: list[str] = []

    def connect(self) -> list[Tool]:
        """Connect to all configured MCP servers and return merged tool list.

        Each server connection is a context manager. Call disconnect() to
        cleanly close all connections when done.

        Servers that fail to connect are logged and skipped — the remaining
        servers' tools are still returned.
        """
        self._tools = []
        self._tool_server_map = {}
        self._server_tools = {}
        self._failed_servers = []

        for i, server in enumerate(self._config.servers):
            server_name = server.name or f"server_{i}"
            try:
                tools = self._connect_server(server)
                tool_names = []
                for tool in tools:
                    self._tools.append(tool)
                    self._tool_server_map[tool.name] = server_name
                    tool_names.append(tool.name)
                self._server_tools[server_name] = tool_names
                logger.info(
                    "Connected to %s (%s): %d tools",
                    server_name,
                    server.url,
                    len(tools),
                )
            except Exception as e:
                self._failed_servers.append(server_name)
                logger.warning(
                    "Failed to connect to %s (%s): %s",
                    server_name,
                    server.url,
                    e,
                )

        return list(self._tools)

    def _connect_server(self, server: MCPServerConfig) -> list[Tool]:
        """Connect to a single MCP server and return its tools."""
        mcp_config = {
            "url": server.url,
            "transport": server.transport,
        }
        collection_cm = ToolCollection.from_mcp(
            mcp_config,
            trust_remote_code=True,
        )
        collection = collection_cm.__enter__()
        self._collections.append(collection_cm)
        tools = list(collection.tools)

        server_name = server.name or server.url
        required_map = _fetch_required_arguments(server.url, server.transport)
        restored = _restore_optional_arguments(tools, server_name, required_map)
        if restored:
            logger.info(
                "B64: restored %d optional argument(s) across %d tool(s) from %s",
                restored,
                len(tools),
                server_name,
            )
        return tools

    def disconnect(self) -> None:
        """Close all MCP server connections."""
        for cm in self._collections:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass
        self._collections.clear()
        self._tools.clear()
        self._tool_server_map.clear()
        self._server_tools.clear()
        self._failed_servers.clear()

    @property
    def tools(self) -> list[Tool]:
        """Return the currently loaded tools."""
        return list(self._tools)

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Return the server name that provides a given tool."""
        return self._tool_server_map.get(tool_name)

    def get_tools_for_server(self, server_name: str) -> list[str]:
        """Return tool names provided by a specific server."""
        return list(self._server_tools.get(server_name, []))

    def get_tools_by_server_pattern(self, pattern: str) -> list[Tool]:
        """Return tools matching a server glob pattern (e.g. 'tigl.*').

        Supports:
        - 'server_name.*' — all tools from that server
        - 'server_name.tool_name' — specific tool from specific server
        - plain 'tool_name' — tool by name regardless of server
        """
        if "." in pattern:
            server_part, tool_part = pattern.split(".", 1)
            server_tools = self._server_tools.get(server_part, [])
            if tool_part == "*":
                return [t for t in self._tools if t.name in server_tools]
            return [t for t in self._tools if t.name == tool_part and t.name in server_tools]
        # Plain tool name.
        return [t for t in self._tools if t.name == pattern]

    @property
    def failed_servers(self) -> list[str]:
        """Return names of servers that failed to connect."""
        return list(self._failed_servers)

    @property
    def connected_servers(self) -> list[str]:
        """Return names of successfully connected servers."""
        return list(self._server_tools.keys())

    @property
    def tool_server_map(self) -> dict[str, str]:
        """Return the full tool_name → server_name mapping."""
        return dict(self._tool_server_map)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
