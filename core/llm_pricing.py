"""Per-model $/token pricing for the admin cost dashboard (services/*_stats*).

Deliberately separate from db/models/llm_usage_log.py, which stores only raw
token counts — pricing changes shouldn't require rewriting historical rows.
Prices are USD per 1,000,000 tokens, checked against provider docs on
2026-07-19; review periodically, providers change these without notice.
"""

from __future__ import annotations

# (prompt_price_per_1m, completion_price_per_1m)
_PRICING_PER_1M_USD: dict[str, tuple[float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "deepseek-v4-flash": (0.14, 0.28),  # cache-miss input rate; cache-hit is far cheaper (~$0.0028/1M) but not tracked separately
    "claude-sonnet-5": (3.00, 15.00),
}

_DEFAULT_PRICING = (0.0, 0.0)


def estimate_cost_usd(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    prompt_price, completion_price = _PRICING_PER_1M_USD.get(model_name, _DEFAULT_PRICING)
    return (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000
