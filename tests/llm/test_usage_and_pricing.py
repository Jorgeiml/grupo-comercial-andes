"""Cost accounting is exact, never underreports, and enforces the per-request token budget."""

from decimal import Decimal

import pytest
from app.llm.pricing import chat_cost_usd, embedding_cost_usd
from app.llm.usage import TokenBudgetExceeded, UsageTracker


def test_chat_cost_uses_input_and_output_prices():
    # gpt-5.6-luna: 0.20 in / 1.20 out per 1M tokens
    assert chat_cost_usd("gpt-5.6-luna", 1_000_000, 1_000_000) == Decimal("1.40")
    assert chat_cost_usd("gpt-5.6-luna", 187, 55) == Decimal("0.0001034")


def test_unknown_model_cost_is_none_not_zero():
    assert chat_cost_usd("some-new-model", 100, 100) is None
    assert embedding_cost_usd("some-new-embedding", 100) is None


def test_embedding_cost():
    assert embedding_cost_usd("text-embedding-3-small", 2592) == Decimal("0.00005184")


def test_tracker_sums_steps():
    tracker = UsageTracker()
    tracker.record_chat("classify", "gpt-5.6-luna", 100, 20, latency_ms=300)
    tracker.record_chat("draft", "gpt-5.4-mini", 400, 150, latency_ms=900)
    tracker.record_embedding("retrieve", "text-embedding-3-small", 30, latency_ms=120)

    assert tracker.total_tokens == 100 + 20 + 400 + 150 + 30
    expected = chat_cost_usd("gpt-5.6-luna", 100, 20) + chat_cost_usd("gpt-5.4-mini", 400, 150) + embedding_cost_usd("text-embedding-3-small", 30)
    assert tracker.total_cost_usd == expected
    assert tracker.cost_complete
    assert [s["step"] for s in tracker.summary()["steps"]] == ["classify", "draft", "retrieve"]


def test_tracker_flags_incomplete_cost():
    tracker = UsageTracker()
    tracker.record_chat("classify", "gpt-5.6-luna", 100, 20, latency_ms=1)
    tracker.record_chat("draft", "unpriced-model", 100, 20, latency_ms=1)
    assert not tracker.cost_complete
    assert tracker.summary()["steps"][1]["cost_usd"] is None


def test_budget_stops_the_request():
    tracker = UsageTracker(max_tokens=1000)
    tracker.record_chat("step1", "gpt-5.6-luna", 500, 100, latency_ms=1)
    with pytest.raises(TokenBudgetExceeded):
        tracker.record_chat("step2", "gpt-5.6-luna", 400, 100, latency_ms=1)
    with pytest.raises(TokenBudgetExceeded):
        tracker.ensure_budget()  # no further calls are allowed
