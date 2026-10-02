# pipeline/pricing.py
"""Claude list prices, used to turn response.usage into dollars.

Source: Anthropic first-party API list prices (checked 2026-10-02). A model
that is not in the table gets cost None ("unknown"), never a guess. Prompt
caching is not used by the pipeline; a call that reports cache tokens also
gets None rather than an assumed cache rate.
"""
from __future__ import annotations

# USD per 1M tokens: (input, output)
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
WEB_SEARCH_PER_1000 = 10.00  # USD per 1,000 web searches
PRICES_CHECKED = "2026-10-02"


def price_for(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    if model in MODEL_PRICES:
        return MODEL_PRICES[model]
    # Dated snapshot ids (claude-haiku-4-5-20251001) share the alias's price.
    for name, p in MODEL_PRICES.items():
        if model.startswith(name + "-") and model[len(name) + 1:].isdigit():
            return p
    return None


def call_cost(model: str | None, input_tokens: int | None, output_tokens: int | None,
              web_searches: int | None = 0, cache_tokens: int = 0) -> float | None:
    """Dollar cost of one call, or None when any input is unknown."""
    p = price_for(model)
    if p is None or None in (input_tokens, output_tokens, web_searches) or cache_tokens:
        return None
    cost = input_tokens * p[0] / 1e6 + output_tokens * p[1] / 1e6
    cost += web_searches * WEB_SEARCH_PER_1000 / 1000
    return round(cost, 6)
