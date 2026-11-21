import pytest

from app.modules.agent_runtime.llm.port import Usage
from app.modules.agent_runtime.llm.pricing import PRICE_VERSION, PRICES, UnknownPrice, cost_micro


def test_cost_is_micro_usd_from_usd_per_million_tokens():
    # claude-sonnet-5: 2$ vào, 0,2$ cache đọc, 2,5$ cache ghi, 10$ ra (mỗi triệu token)
    usage = Usage(input=1_000_000, cache_read=1_000_000, cache_write=1_000_000, output=1_000_000)
    assert (
        cost_micro("anthropic", "claude-sonnet-5", usage)
        == 2_000_000 + 200_000 + 2_500_000 + 10_000_000
    )


def test_cost_rounds_up_so_small_calls_are_never_free():
    assert cost_micro("openai", "gpt-4.1-mini", Usage(input=1, output=0)) == 1  # 0,4 micro → 1
    assert cost_micro("openai", "gpt-4.1-mini", Usage()) == 0


def test_cached_tokens_are_cheaper_than_uncached():
    cold = cost_micro("openai", "gpt-4.1", Usage(input=10_000))
    warm = cost_micro("openai", "gpt-4.1", Usage(cache_read=10_000))
    assert warm * 4 == cold  # 0,5$ so với 2$


def test_unknown_model_fails_loudly_instead_of_costing_zero():
    with pytest.raises(UnknownPrice, match="anthropic:claude-imaginary"):
        cost_micro("anthropic", "claude-imaginary", Usage(input=100))


def test_every_price_is_positive_and_versioned():
    assert PRICE_VERSION
    for key, p in PRICES.items():
        assert p.input > 0 and p.output > 0 and p.cache_read >= 0, key
        assert p.cache_read <= p.input, key  # đọc cache không bao giờ đắt hơn gửi mới
