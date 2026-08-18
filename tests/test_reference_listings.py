"""Name listings must come back whole; bulk payloads must still be intercepted.

Measured 2026-08-17 (all7, sequential_iterative_feedback): the agent stated its
intent — "First, I need to check the list_variables output to confirm the exact
names of these inputs" — received "Showing first 5 of 50 items", guessed
`fan.BPR`, and pycycle answered:

    "Variable 'fan.BPR' not found. Perhaps you meant one of the following
     variables: ['balance.hpt_PR', 'balance.lpt_PR', 'perf.OPR']"

None of those is a bypass ratio. Our own middleware hid the answer the agent was
explicitly trying to look up, then the tool replied with string-similarity noise.
"""

import pytest

from src.tools.data_plane import _is_reference_listing, _summarize_payload

VARIABLE_NAMES = [f"comp{i}.PARAM_{i}" for i in range(50)]


class TestReferenceListingsAreReturnedWhole:
    def test_a_fifty_name_listing_is_not_truncated(self):
        out = _summarize_payload(VARIABLE_NAMES, "list_variables__variables")
        assert out["total_count"] == 50
        assert len(out["items"]) == 50
        assert "preview" not in out

    def test_it_needs_no_second_call(self):
        """The ref escape hatch required a call the agent never made."""
        out = _summarize_payload(VARIABLE_NAMES, "k")
        assert out["_intercepted"] is False
        assert "ref" not in out

    def test_the_name_that_was_guessed_is_now_visible(self):
        names = ["fan.BPR", "cycle.OPR", "burner.T4"] + VARIABLE_NAMES
        out = _summarize_payload(names, "k")
        assert "fan.BPR" in out["items"]


class TestBulkPayloadsAreStillIntercepted:
    """Interception exists for a reason — this must not become a context leak."""

    def test_a_long_listing_is_still_previewed(self):
        out = _summarize_payload([f"item{i}" for i in range(500)], "k")
        assert out["_intercepted"] is True
        assert "preview" in out and out["total_count"] == 500

    def test_a_list_of_dicts_is_still_previewed(self):
        out = _summarize_payload([{"a": 1}] * 20, "k")
        assert out["_intercepted"] is True

    def test_long_strings_are_not_reference_data(self):
        """A base64 chunk list is bulk, not names."""
        assert not _is_reference_listing(["x" * 5000, "y" * 5000])


class TestTheBoundary:
    @pytest.mark.parametrize("value,expected", [
        (["a", "b", "c"], True),
        ([], False),
        ([f"n{i}" for i in range(200)], True),
        ([f"n{i}" for i in range(201)], False),
        (["ok", 123], False),
    ])
    def test_classification(self, value, expected):
        assert _is_reference_listing(value) is expected
