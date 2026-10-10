# pipeline/pricing.py
"""Claude list prices, used to turn response.usage into dollars.

Source: Anthropic first-party API list prices (base rates checked 2026-10-02;
cache rates 2026-10-09). A model that is not in the table gets cost None
("unknown"), never a guess.

Prompt caching (pipeline/llm.py marks the long shared prefixes of the scene
calls): `usage.input_tokens` is only the uncached remainder. Tokens read from
the cache bill at the model's cache-read rate (Opus 5.5 and Sonnet 5.5:
$0.20/MTok, 0.05x and 0.1x of base input), tokens written bill at 1.25x base
input for the 5-minute TTL and 2x for the 1-hour TTL.
"""
from __future__ import annotations

# USD per 1M tokens: (input, output, cache read)
MODEL_PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
CACHE_WRITE_5M = 1.25  # x base input
CACHE_WRITE_1H = 2.00  # x base input
WEB_SEARCH_PER_1000 = 10.00  # USD per 1,000 web searches
PRICES_CHECKED = "2026-10-09"


def price_for(model: str | None) -> tuple[float, float, float] | None:
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
              web_searches: int | None = 0, *, cache_read: int | None = 0,
              cache_write_5m: int | None = 0, cache_write_1h: int | None = 0) -> float | None:
    """Dollar cost of one call, or None when any input is unknown.

    ``input_tokens`` is the uncached input (what the API reports); cache reads
    and writes are priced separately.
    """
    p = price_for(model)
    if p is None or None in (input_tokens, output_tokens, web_searches,
                             cache_read, cache_write_5m, cache_write_1h):
        return None
    base_in, base_out, read = p
    cost = input_tokens * base_in / 1e6 + output_tokens * base_out / 1e6
    cost += cache_read * read / 1e6
    cost += cache_write_5m * base_in * CACHE_WRITE_5M / 1e6
    cost += cache_write_1h * base_in * CACHE_WRITE_1H / 1e6
    cost += web_searches * WEB_SEARCH_PER_1000 / 1000
    return round(cost, 6)
