"""Model prices in USD per 1M tokens, standard tier.

Source: https://developers.openai.com/api/docs/pricing (checked 2026-11-02).
Cached-input discounts are ignored on purpose: reported costs are an upper bound, never an underestimate.
"""

from decimal import Decimal

ONE_MILLION = Decimal(1_000_000)

# model: (input, output)
CHAT_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-5.6-luna": (Decimal("0.20"), Decimal("1.20")),
    "gpt-5.4-mini": (Decimal("0.75"), Decimal("4.50")),
    "gpt-5.4-nano": (Decimal("0.20"), Decimal("1.25")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "gpt-4.1": (Decimal("2.00"), Decimal("8.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
}

EMBEDDING_PRICES: dict[str, Decimal] = {
    "text-embedding-3-small": Decimal("0.02"),
}


def chat_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """None means "unknown price": the cost panel must show it as unknown, never as zero."""
    if model not in CHAT_PRICES:
        return None
    input_price, output_price = CHAT_PRICES[model]
    return (input_tokens * input_price + output_tokens * output_price) / ONE_MILLION


def embedding_cost_usd(model: str, tokens: int) -> Decimal | None:
    if model not in EMBEDDING_PRICES:
        return None
    return tokens * EMBEDDING_PRICES[model] / ONE_MILLION
