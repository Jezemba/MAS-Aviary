"""Only the known-harmless from_mcp teardown report is dropped (2026-10-08)."""
import sys
from types import SimpleNamespace

import src.tools.mcp_connector  # noqa: F401  installs the hook


def _report(exc, obj):
    seen = []
    hook = sys.unraisablehook
    prev = getattr(hook, "__closure__", None)
    # Find the hook's fallback and record what reaches it.
    fallback_cell = next(c for c in prev if callable(c.cell_contents) and c.cell_contents is not hook)
    original = fallback_cell.cell_contents
    fallback_cell.cell_contents = lambda u: seen.append(u)
    try:
        hook(SimpleNamespace(exc_type=type(exc), exc_value=exc, exc_traceback=None, err_msg=None, object=obj))
    finally:
        fallback_cell.cell_contents = original
    return seen


class _Gen:
    def __repr__(self):
        return "<generator object ToolCollection.from_mcp at 0x7cc7c691f450>"


def test_the_from_mcp_event_loop_report_is_dropped():
    assert _report(RuntimeError("Cannot close a running event loop"), _Gen()) == []


def test_other_unraisable_errors_still_reach_the_previous_hook():
    assert len(_report(RuntimeError("something else"), _Gen())) == 1
    assert len(_report(RuntimeError("Cannot close a running event loop"), object())) == 1
    assert len(_report(ValueError("Cannot close a running event loop"), _Gen())) == 1
