"""B100: the unresolved-reference message must name a form that actually works.

validate15, two consecutive steps of the aero holder:

    06:08  set_mesh(mesh_base64='volume_mesh_base64_from_seq_16')
           -> UNRESOLVED_REF: "Pass one of these exactly (or the {\"ref\": \"<key>\"} form)"
    06:14  set_mesh(mesh_base64={'ref': 'generate_volume_mesh__mesh_base64'})
           -> "Argument mesh_base64 has type 'object' but should be 'string'"

The agent did exactly what the refusal told it to. smolagents validates tool inputs against the
declared schema BEFORE the wrapped forward runs, so for a string-typed parameter the dict form can
never reach the middleware that would resolve it. Two steps, ~11 minutes, spent on advice that was
wrong at the point of use.

Stubbed: no MCP server, no model, no .env.
"""

import pytest

import src.tools.data_plane as dp
from src.coordination.design_state import DesignState

KEY = "generate_volume_mesh__mesh_base64"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    state = DesignState()
    state.data_store[KEY] = "x" * 5000          # a stored payload, so a reference exists
    monkeypatch.setattr(dp, "_design_state", state)
    yield


def _refusal(value):
    return dp.unresolved_ref_error("set_mesh", {"mesh_base64": value})


def test_the_message_tells_the_caller_to_pass_a_bare_string():
    refusal = _refusal("volume_mesh_base64_from_seq_16")
    assert refusal is not None
    error = refusal["error"]
    assert "mesh_base64='" + KEY + "'" in error
    assert "a bare string, NOT a dict" in error


def test_the_message_no_longer_offers_the_dict_form():
    """The form smolagents rejects on schema validation must not be suggested."""
    refusal = _refusal("made_up_name")
    assert '{"ref"' not in refusal["error"]
    assert "'ref'" not in refusal["error"]


def test_the_available_references_are_still_listed():
    refusal = _refusal("made_up_name")
    assert KEY in refusal["error"]
    assert refusal["error_code"] == "UNRESOLVED_REF"


def test_it_still_says_the_call_was_not_sent():
    refusal = _refusal("made_up_name")
    assert "NOT sent to the server" in refusal["error"]
