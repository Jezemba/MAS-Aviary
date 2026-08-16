"""The agent's reasoning must be visible, or debugging is guesswork.

Jessica, 2026-08-16, watching run_simulation cycle again: "there is no reasoning
for what and why its doing this so i dont know what is wrong, we are just
guessing."

She was right. ThinkingModel strips <think>...</think> before parsing -- necessary,
since reasoning traces confuse the JSON extractor -- but the content was DISCARDED,
so every log held tool calls and observations and no motive. From the outside an
agent exploring a design space and an agent stuck in a loop are indistinguishable,
which is why six hours of run 6 could only be diagnosed by counting payloads
afterwards.

Recording it is an observability change only: the text is printed, never fed back
into the model's context, so nothing the agent sees changes.
"""

import re

import pytest

from src.llm.thinking_model import strip_think_blocks


class TestTheReasoningIsSurfaced:
    def test_it_is_printed(self, capsys):
        strip_think_blocks("<think>fuel is still 19000, try more sweep</think>DONE")
        assert "fuel is still 19000" in capsys.readouterr().out

    def test_it_is_labelled_so_it_can_be_grepped_and_charted(self, capsys):
        strip_think_blocks("<think>because CD looks wrong</think>x")
        assert "REASONING:" in capsys.readouterr().out

    def test_multiple_blocks_all_appear(self, capsys):
        strip_think_blocks("<think>first</think>mid<think>second</think>end")
        out = capsys.readouterr().out
        assert "first" in out and "second" in out


class TestItDoesNotChangeWhatTheAgentSees:
    """Purely observability. If this altered the stripped text it would change
    model behaviour and confound the coordination comparison."""

    @pytest.mark.parametrize("raw,expected", [
        ("<think>why</think>ANSWER", "ANSWER"),
        ("no think block here", "no think block here"),
        ("<think>a</think>X<think>b</think>Y", "XY"),
        ("", ""),
    ])
    def test_stripped_output_is_unchanged(self, raw, expected):
        assert strip_think_blocks(raw) == expected


class TestItIsBounded:
    def test_a_long_ramble_is_truncated_and_says_so(self, capsys):
        strip_think_blocks("<think>" + "z" * 5000 + "</think>x")
        out = capsys.readouterr().out
        assert "[truncated]" in out
        assert len(out) < 2000

    def test_empty_reasoning_prints_nothing(self, capsys):
        strip_think_blocks("<think>   </think>payload")
        assert "REASONING:" not in capsys.readouterr().out
