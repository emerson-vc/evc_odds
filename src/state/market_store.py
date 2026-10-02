"""Latest quote per (source, MarketKey), in memory. SQLite history comes later (CLAUDE.md §19)."""

from __future__ import annotations

from collections import defaultdict

from src.models.market import MarketKey
from src.models.quote import CanonicalQuote


class MarketStore:
    def __init__(self):
        # Keyed by (source, source_market_id, side): a spread market whose line moves from -3.5 to -3
        # replaces its old quote instead of leaving a stale -3.5 entry behind.
        self._latest: dict[tuple, CanonicalQuote] = {}

    @staticmethod
    def slot(q: CanonicalQuote) -> tuple:
        return (q.source, q.source_market_id, q.side)

    def all(self) -> list[CanonicalQuote]:
        return list(self._latest.values())

    def update(self, q: CanonicalQuote) -> CanonicalQuote | None:
        """Store q. Returns the previous quote if the price/line/status changed, else None.

        A first-seen quote returns itself as "previous" so callers can treat it as a change.
        Identical repeats only refresh the timestamp (they still prove the price is current).
        """
        slot = self.slot(q)
        prev = self._latest.get(slot)
        self._latest[slot] = q
        if prev is None:
            return q
        return None if prev.same_price(q) else prev

    def get(self, source: str, key: MarketKey) -> CanonicalQuote | None:
        for q in self._latest.values():
            if q.source == source and q.market_key == key:
                return q
        return None

    def by_group(self) -> dict[tuple, list[CanonicalQuote]]:
        """All quotes grouped by market (both sides, all sources)."""
        groups: dict[tuple, list[CanonicalQuote]] = defaultdict(list)
        for q in self._latest.values():
            groups[q.market_key.group()].append(q)
        return groups

    def __len__(self) -> int:
        return len(self._latest)
