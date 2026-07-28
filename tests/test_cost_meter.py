"""Token/cost instrumentation — the fix for total_tokens=0 on networked and the
cache-blind cost estimate. Verifies extraction, cache-aware billing, and that the
meter is thread-safe (so networked peer threads aggregate correctly)."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.llm.cost_meter import CostMeter, billed_cost_usd, extract_usage  # noqa: E402


class _Details:
    cached_tokens = 8000


class _Usage:
    prompt_tokens = 10000
    completion_tokens = 500
    prompt_tokens_details = _Details()
    cache_creation_input_tokens = 1000


class _Resp:
    usage = _Usage()


def test_extract_usage_with_cache():
    assert extract_usage(_Resp()) == (10000, 500, 8000, 1000)


def test_extract_usage_missing_is_zero():
    assert extract_usage(object()) == (0, 0, 0, 0)


def test_billed_cost_cache_aware():
    m = CostMeter()
    m.record(10000, 500, cache_read=8000, cache_creation=1000)
    # non_cached=1000; (1000*3 + 8000*3*0.1 + 1000*3*1.25 + 500*15)/1e6
    expected = round((1000 * 3 + 8000 * 3 * 0.1 + 1000 * 3 * 1.25 + 500 * 15) / 1e6, 4)
    assert billed_cost_usd(m.snapshot(), "claude-sonnet-4-6") == expected


def test_billed_cost_no_cache_reduces():
    m = CostMeter()
    m.record(10000, 500)
    assert billed_cost_usd(m.snapshot(), "claude-sonnet-4-6") == round((10000 * 3 + 500 * 15) / 1e6, 4)


def test_opus_pricing_is_167pct_sonnet():
    """Current-gen Opus is $5/$25 — ~1.67x Sonnet's $3/$15, not the retired $15/$75."""
    m = CostMeter()
    m.record(10000, 500)
    assert billed_cost_usd(m.snapshot(), "claude-opus-5") == round((10000 * 5 + 500 * 25) / 1e6, 4)
    assert billed_cost_usd(m.snapshot(), "claude-opus-4-6") == round((10000 * 5 + 500 * 25) / 1e6, 4)


def test_meter_is_threadsafe():
    """8 peer threads x 100 calls — proves networked aggregation is complete."""
    m = CostMeter()

    def work():
        for _ in range(100):
            m.record(100, 10)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = m.snapshot()
    assert snap["calls"] == 800
    assert snap["input_tokens"] == 80000  # 800 calls x 100
    assert snap["output_tokens"] == 8000  # 800 calls x 10
