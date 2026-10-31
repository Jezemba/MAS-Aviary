"""Unified tool loading interface — loads tools from mock definitions or real MCP."""

from smolagents import Tool

from src.config.loader import AppConfig
from src.tools.mock_tools import MOCK_TOOLS, create_mock_tool
from src.tools.type_coercion import apply_coercion_to_tools

# Keep MCP connectors alive so tool connections aren't garbage-collected.
_active_connectors: list = []


def _with_design_state(tools: list) -> list:
    """Append the shared-state read tool, once.

    Every worker gets it, in every combination: it is a DISCOVERY tool, so it
    must not privilege one coordination structure over another. Without it the
    design state -- which already records the correct session id for each server
    -- is unreachable, and a worker's only source is the task preamble, which
    hands it the aviary session id and the aviary parameter names regardless of
    which server it is about to call (B53/B54/B55).
    """
    from src.tools.design_state_tool import GetDesignState
    from src.tools.knowledge_base_tool import ReadDesignKnowledge
    from src.tools.procedure_tool import ReadProcedure

    names = {getattr(t, "name", None) for t in tools}
    extra = []
    if GetDesignState.name not in names:
        extra.append(GetDesignState())
    # B81: the design knowledge base is a discovery tool too, for the same reason.
    if ReadDesignKnowledge.name not in names:
        extra.append(ReadDesignKnowledge())
    # B85: and so is the procedure. read_design_knowledge says what HAS been done;
    # read_procedure says what SHOULD be done, in order. Nobody called read_history_csv
    # in validate8 because the sequence was written nowhere an agent could reach.
    if ReadProcedure.name not in names:
        extra.append(ReadProcedure())
    # sequential-prompts-v2: the lessons removed from the short stage prompts, on request, and the
    # framework-written design.json the prompts point at.
    from src.tools.best_practices_tool import ReadBestPractices
    from src.tools.design_json import ReadDesignJson

    if ReadBestPractices.name not in names:
        extra.append(ReadBestPractices())
    if ReadDesignJson.name not in names:
        extra.append(ReadDesignJson())
    return list(tools) + extra


def load_tools_for_agent(tool_names: list[str], config: AppConfig) -> list[Tool]:
    """Load tool instances for an agent based on config mode.

    In 'mock' mode, tools are created from the local mock definitions.
    In 'real' mode, tools come from a shared MCPConnector instance.
    The data plane middleware is initialized with the tool→server mapping
    so that session IDs and binary payloads are managed automatically.

    Args:
        tool_names: List of tool name strings from the agent config.
        config: Application config (used to determine mock vs real mode).

    Returns:
        List of smolagents Tool instances ready for agent use.
    """
    if config.mcp.mode == "mock":
        plain_names = [n.split(".", 1)[-1] if "." in n and n.split(".", 1)[-1] != "*" else n for n in tool_names]
        return [create_mock_tool(name) for name in plain_names if name in MOCK_TOOLS]

    # If all requested tools are available as mocks, skip MCP connection.
    plain_names = [n.split(".", 1)[-1] if "." in n and n.split(".", 1)[-1] != "*" else n for n in tool_names]
    if tool_names and all(name in MOCK_TOOLS for name in plain_names if name != "*"):
        return [create_mock_tool(name) for name in plain_names if name in MOCK_TOOLS]

    # Real MCP mode — connect and filter tools by requested names.
    from src.tools.mcp_connector import MCPConnector

    connector = MCPConnector(config.mcp)
    all_tools = connector.connect()
    _active_connectors.append(connector)

    # Initialize the data plane with the tool→server mapping.
    # This enables automatic session capture/injection and binary interception.
    _init_data_plane_if_needed(connector)

    if not tool_names:
        return _register_framework_tools(all_tools, apply_coercion_to_tools(_with_design_state(all_tools)))

    # Resolve tool names, supporting server-scoped patterns.
    has_server_patterns = any("." in name for name in tool_names)

    if has_server_patterns:
        result = []
        seen = set()
        for pattern in tool_names:
            matched = connector.get_tools_by_server_pattern(pattern)
            for t in matched:
                if t.name not in seen:
                    result.append(t)
                    seen.add(t.name)
        return _register_framework_tools(all_tools, apply_coercion_to_tools(_with_design_state(result)))

    # Simple name-based lookup (backward compatible).
    tool_map = {t.name: t for t in all_tools}
    result = []
    for name in tool_names:
        if name in tool_map:
            result.append(tool_map[name])
        elif name in MOCK_TOOLS:
            result.append(create_mock_tool(name))

    return _register_framework_tools(all_tools, apply_coercion_to_tools(result))


def _register_framework_tools(all_tools: list[Tool], selected: list[Tool]) -> list[Tool]:
    """The data plane's own tools: registered for it, kept out of every agent's toolset (B3).

    generate_engine_deck / set_engine_deck turn the designed cycle into the mission engine before
    run_simulation. The data plane only knows tools that were loaded for some agent, so they are wrapped
    (full middleware: knowledge base, watchdog) and registered here. They are not handed to agents: an
    agent attaching a deck by hand would bypass the framework's record of which engine a session flies.
    A tool already wrapped in ``selected`` is registered as it is, not wrapped twice.
    """
    from src.tools.data_plane import ENGINE_DECK_TOOLS, register_tools

    have = {t.name for t in selected if getattr(t, "name", None) in ENGINE_DECK_TOOLS}
    extra = [t for t in all_tools if getattr(t, "name", None) in ENGINE_DECK_TOOLS and t.name not in have]
    if extra:
        register_tools(apply_coercion_to_tools(extra))
    return [t for t in selected if getattr(t, "name", None) not in ENGINE_DECK_TOOLS]


def _init_data_plane_if_needed(connector) -> None:
    """Initialize the data plane with DesignState and tool→server mapping.

    Creates a DesignState if one doesn't exist yet, and passes the
    tool→server mapping so the middleware knows which MCP each tool
    belongs to (for session management).
    """
    from src.tools.data_plane import get_design_state, init_data_plane
    from src.coordination.design_state import DesignState

    # Reuse existing DesignState if already initialized (e.g. multiple
    # load_tools_for_agent calls within the same pipeline run).
    ds = get_design_state()
    if ds is None:
        ds = DesignState()

    init_data_plane(ds, connector.tool_server_map)
