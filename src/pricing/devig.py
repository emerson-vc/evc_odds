"""De-vigging: turn a book's raw prices for all outcomes of one market into fair probabilities.

Methods implement `DevigMethod` so the consensus engine never hard-codes one technique (CLAUDE.md §12).
"""

from __future__ import annotations

from typing import Protocol

from src.pricing.odds import implied_probability


class DevigMethod(Protocol):
    name: str

    def fair_probabilities(self, decimal_odds: dict[str, float]) -> dict[str, float]:
        """Map outcome label -> decimal odds (every outcome of the market) to outcome -> fair probability."""
        ...


class ProportionalDevig:
    """p_fair_i = p_raw_i / sum(p_raw). Simplest method; spreads the vig evenly in probability space."""

    name = "proportional"

    def fair_probabilities(self, decimal_odds: dict[str, float]) -> dict[str, float]:
        if len(decimal_odds) < 2:
            # A one-sided book quote can't be de-vigged; never treat it as fair (CLAUDE.md §12).
            raise ValueError("need every outcome of the market to de-vig")
        raw = {k: implied_probability(d) for k, d in decimal_odds.items()}
        total = sum(raw.values())
        return {k: p / total for k, p in raw.items()}


METHODS: dict[str, DevigMethod] = {ProportionalDevig.name: ProportionalDevig()}


def get_method(name: str) -> DevigMethod:
    try:
        return METHODS[name]
    except KeyError:
        raise ValueError(f"unknown devig method {name!r}; known: {sorted(METHODS)}") from None


def overround(decimal_odds: dict[str, float]) -> float:
    """Book margin: sum of raw implied probabilities minus 1 (e.g. 0.045 for -110/-110)."""
    return sum(implied_probability(d) for d in decimal_odds.values()) - 1
