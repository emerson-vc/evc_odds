"""Exchange taker fees -> effective decimal odds (CLAUDE.md §16: use the price you'd actually get).

Kalshi and Polymarket US both charge   fee per $1 contract = theta * p * (1 - p)   for a taker buying at p.
"""

from __future__ import annotations


def effective_decimal(price: float, theta: float) -> float:
    """Decimal odds of buying a $1-payout contract at `price` and paying the taker fee."""
    if not 0 < price < 1:
        raise ValueError(f"contract price must be in (0, 1): {price}")
    return 1 / (price + theta * price * (1 - price))
