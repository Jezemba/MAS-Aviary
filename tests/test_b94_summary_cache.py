"""B94: a summary is cached against the state it describes, not the number of records.

validate4 measured the cost of the old key: the networked link made **58 summary calls costing
2,447.8 s -- 12.3% of a 19,980 s link** -- against 7-9 calls in the sequential and orchestrated
structures. The reason is one term in the cache key, ``len(kb.entries)``: every tool call by any
peer appends a record, so every summary after any peer did anything was a fresh 42 s generation.

But the summary answers "what is DONE, what is STILL MISSING for a coupled result, and what
conflicts or failed". That changes when a tool SUCCEEDS or FAILS for a design -- not when a peer
is refused a duplicate, and not when it repeats a read-only call.

Stubbed: no MCP server, no model, no .env. The summary model is absent throughout, so summarize()
falls back to the deterministic digest and the cache behaviour is what is under test.
"""

import threading

import pytest

from src.tools import knowledge_base as kbm


@pytest.fixture(autouse=True)
def fresh():
    kbm.configure_run(None, 0, 1, None)
    kbm._inflight.clear()
    yield
    kbm._inflight.clear()


def _kb():
    return kbm.KnowledgeBase()


def _add(kb, tool, status="success", fingerprint="geo:1", agent="agent_1", server="tigl"):
    return kb.append(tool=tool, server=server, agent=agent, role="", status=status,
                     inputs={}, outputs={"ok": True} if status == "success" else {},
                     design_fingerprint=fingerprint)


# ---- the signature tracks state, not volume ----------------------------------------------------

def test_a_refusal_does_not_change_the_signature():
    """validate11 made 4 refusals in one link; none of them changes what the summary would say."""
    kb = _kb()
    _add(kb, "open_cpacs")
    before = kbm.state_signature(kb)
    _add(kb, "set_aircraft_parameters", status="refused", server="aviary")
    assert kbm.state_signature(kb) == before


def test_repeating_work_already_recorded_does_not_change_the_signature():
    kb = _kb()
    _add(kb, "open_cpacs", agent="agent_1")
    before = kbm.state_signature(kb)
    _add(kb, "open_cpacs", agent="agent_3")          # another peer, same design, same tool
    assert kbm.state_signature(kb) == before


def test_a_new_milestone_changes_the_signature():
    kb = _kb()
    _add(kb, "open_cpacs")
    before = kbm.state_signature(kb)
    _add(kb, "generate_volume_mesh")
    assert kbm.state_signature(kb) != before


def test_the_same_tool_on_a_new_design_changes_the_signature():
    """A morph makes every downstream result stale, so the summary must be regenerated."""
    kb = _kb()
    _add(kb, "generate_volume_mesh", fingerprint="geo:1")
    before = kbm.state_signature(kb)
    _add(kb, "generate_volume_mesh", fingerprint="geo:2")
    assert kbm.state_signature(kb) != before


def test_a_failure_changes_the_signature():
    """B87 made failures trustworthy; 'what failed' is part of what the summary reports."""
    kb = _kb()
    _add(kb, "open_cpacs")
    before = kbm.state_signature(kb)
    _add(kb, "generate_volume_mesh", status="failed")
    assert kbm.state_signature(kb) != before


# ---- and the cache follows it ------------------------------------------------------------------

def test_a_repeat_call_with_only_refusals_since_is_a_cache_hit():
    kb = _kb()
    _add(kb, "open_cpacs")
    first = kbm.summarize(kb)
    _add(kb, "open_cpacs", status="refused", agent="agent_2")
    _add(kb, "get_design_space", status="refused", agent="agent_3", server="aviary")
    hits_before = kb.metrics["kb_summary_cache_hits"]
    assert kbm.summarize(kb) == first
    assert kb.metrics["kb_summary_cache_hits"] == hits_before + 1


def test_new_work_invalidates_the_cache():
    kb = _kb()
    _add(kb, "open_cpacs")
    first = kbm.summarize(kb)
    _add(kb, "generate_volume_mesh")
    second = kbm.summarize(kb)
    assert second != first
    assert "generate_volume_mesh" in second


def test_a_different_question_is_cached_separately():
    kb = _kb()
    _add(kb, "open_cpacs")
    kbm.summarize(kb, question="what is missing for aero")
    hits = kb.metrics["kb_summary_cache_hits"]
    kbm.summarize(kb, question="what is missing for mass")
    assert kb.metrics["kb_summary_cache_hits"] == hits          # not served from the other key


# ---- concurrent identical requests generate once ------------------------------------------------

def test_three_peers_asking_at_once_do_not_each_generate():
    """The peers ask within the same turn; without coalescing each pays the same generation."""
    kb = _kb()
    _add(kb, "open_cpacs")
    out, errors = [], []

    def ask():
        try:
            out.append(kbm.summarize(kb))
        except Exception as exc:       # pragma: no cover - a failure here is the test failing
            errors.append(exc)

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert len(out) == 3 and len(set(out)) == 1
    assert not kbm._inflight, "an in-flight key was left behind"


def test_the_inflight_key_is_released_even_when_summarising_raises(monkeypatch):
    """A stuck key would make every later caller wait the full window and then generate anyway."""
    kb = _kb()
    _add(kb, "open_cpacs")

    def boom(*a, **k):
        raise RuntimeError("summary model exploded")

    monkeypatch.setattr(kbm, "digest", boom)
    with pytest.raises(RuntimeError):
        kbm.summarize(kb)
    assert not kbm._inflight
