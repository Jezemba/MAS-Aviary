"""B248: an agent cannot delete an SU2 session folder (Jessica 2026-11-08: the folders are kept for inspection and
archived to ws3's My Book Duo before anything is removed). close_su2_session(delete_workdir=True) is sent as False."""
from __future__ import annotations

import pytest

from src.coordination.design_state import DesignState
from src.tools import data_plane as dp


@pytest.fixture
def plane(monkeypatch):
    state = DesignState()
    state.sessions["su2"] = "s1"
    dp.init_data_plane(state, {"close_su2_session": "su2"})
    monkeypatch.setattr(dp, "_registered_tools", {})
    yield state
    dp.init_data_plane(DesignState(), {})


def test_delete_workdir_true_is_sent_as_false(plane):
    out = dp.resolve_request("close_su2_session", {"session_id": "s1", "delete_workdir": True})
    assert out["delete_workdir"] is False


def test_plain_close_is_unchanged(plane):
    out = dp.resolve_request("close_su2_session", {"session_id": "s1"})
    assert not out.get("delete_workdir")
