"""Odds conversions. Pure functions, no source-specific logic (CLAUDE.md §11).

Decimal odds are the internal representation everywhere; American odds are for display.
"""

from __future__ import annotations


def american_to_decimal(american: float) -> float:
    if american == 0 or -100 < american < 100:
        raise ValueError(f"invalid American odds: {american}")
    if american > 0:
        return 1 + american / 100
    return 1 + 100 / -american


def decimal_to_american(decimal: float) -> int:
    """Round to the nearest whole American price, as books display them."""
    if decimal <= 1:
        raise ValueError(f"invalid decimal odds: {decimal}")
    if decimal >= 2:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def implied_probability(decimal: float) -> float:
    """Raw implied probability, vig included."""
    if decimal <= 1:
        raise ValueError(f"invalid decimal odds: {decimal}")
    return 1 / decimal


def american_implied_probability(american: float) -> float:
    return implied_probability(american_to_decimal(american))


def probability_to_decimal(p: float) -> float:
    """Fair (no-vig) decimal odds for win probability p."""
    if not 0 < p < 1:
        raise ValueError(f"probability must be in (0, 1): {p}")
    return 1 / p


def probability_to_american(p: float) -> int:
    return decimal_to_american(probability_to_decimal(p))
