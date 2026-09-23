"""B110: a tool whose reply takes longer than the MCP client's read timeout must still be answered.

geoauth_all8_7960 orchestrated iterative link 2: two run_su2_solver calls whose solves took ~420 s
(the other 60 on this machine took 234-296 s) never returned. SU2 exited successfully, the server was
idle, and the client waited until call_watchdog gave up after 3600 s -- twice. The MCP client's default
sse_read_timeout is 300 s, and when it expires the reply stream is dropped with only a DEBUG log.
"""
from unittest.mock import patch

from src.config.loader import MCPConfig, MCPServerConfig
from src.tools import call_watchdog
from src.tools.mcp_connector import MCPConnector, _sse_read_timeout


class _Collection:
    tools = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _config(transport="streamable-http"):
    return MCPConfig(mode="real", servers=[MCPServerConfig(url="http://127.0.0.1:1/mcp", transport=transport, name="su2")])


def _connect(transport="streamable-http"):
    with patch("src.tools.mcp_connector.ToolCollection.from_mcp", return_value=_Collection()) as m, \
         patch("src.tools.mcp_connector._fetch_required_arguments", return_value={}):
        MCPConnector(_config(transport)).connect()
    return m.call_args[0][0]


def test_the_reply_may_take_as_long_as_the_watchdog_allows():
    assert _sse_read_timeout() >= call_watchdog.timeout_for("run_su2_solver")
    assert _sse_read_timeout() > 300


def test_the_connection_is_opened_with_it():
    assert _connect()["sse_read_timeout"] == _sse_read_timeout()


def test_it_can_be_overridden(monkeypatch):
    monkeypatch.setenv("AVION_MCP_READ_TIMEOUT", "7200")
    assert _connect()["sse_read_timeout"] == 7200.0


def test_a_bad_override_falls_back(monkeypatch):
    monkeypatch.setenv("AVION_MCP_READ_TIMEOUT", "soon")
    assert _sse_read_timeout() >= call_watchdog.timeout_for("run_su2_solver")
