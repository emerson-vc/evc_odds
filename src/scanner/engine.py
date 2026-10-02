"""Runs every adapter as an independent task and feeds one shared normalizer + store (CLAUDE.md §18, §23).

A crashing adapter is logged and restarted with backoff; it never takes down the others.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from src.adapters.base import AdapterHealth, OddsSourceAdapter, RawEvent, RawQuote
from src.models.quote import CanonicalQuote
from src.normalization.markets import Normalizer
from src.state.market_store import MarketStore

log = logging.getLogger(__name__)

ChangeCallback = Callable[[CanonicalQuote, CanonicalQuote], None]  # (new, previous)


class Scanner:
    def __init__(self, adapters: list[OddsSourceAdapter], on_change: ChangeCallback | None = None):
        self.adapters = adapters
        self.normalizer = Normalizer()
        self.store = MarketStore()
        self.on_change = on_change
        self.changed_at: dict[tuple, float] = {}  # store slot -> unix time of last real price/line change
        self.restarts: dict[str, int] = {a.name: 0 for a in adapters}

    def ingest(self, item: RawEvent | RawQuote) -> None:
        if isinstance(item, RawEvent):
            self.normalizer.register_event(item)
            return
        q = self.normalizer.normalize(item)
        if q is None:
            return
        prev = self.store.update(q)
        if prev is not None and prev is not q:  # a real change, not a first sighting
            self.changed_at[self.store.slot(q)] = time.time()
            if self.on_change:
                self.on_change(q, prev)

    async def run_adapter(self, adapter: OddsSourceAdapter) -> None:
        backoff = 5
        while True:
            try:
                async for item in adapter.stream_updates():
                    self.ingest(item)
                    backoff = 5
            except asyncio.CancelledError:
                raise
            except Exception:
                adapter.health = AdapterHealth.DISCONNECTED
                self.restarts[adapter.name] += 1
                log.exception("%s adapter crashed; restarting in %ss", adapter.name, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    async def run(self) -> None:
        await asyncio.gather(*(self.run_adapter(a) for a in self.adapters))

    async def aclose(self) -> None:
        for a in self.adapters:
            close = getattr(a, "aclose", None)
            if close:
                await close()
