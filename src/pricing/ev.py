"""Expected value of taking a price, given a fair win probability (CLAUDE.md §16)."""

from __future__ import annotations


def expected_value(fair_probability: float, decimal_odds: float) -> float:
    """EV per $1 staked: p * d - 1."""
    if not 0 < fair_probability < 1:
        raise ValueError(f"probability must be in (0, 1): {fair_probability}")
    if decimal_odds <= 1:
        raise ValueError(f"invalid decimal odds: {decimal_odds}")
    return fair_probability * decimal_odds - 1


def ev_percent(fair_probability: float, decimal_odds: float) -> float:
    return 100 * expected_value(fair_probability, decimal_odds)
