"""The interface every source adapter implements (CLAUDE.md §6).

Adapters own everything source-specific (URLs, request/response shapes, IDs, quirks) and emit RawEvent /
RawQuote using the shared vocabulary in src.models.market. Nothing downstream knows how a source is fetched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from src.models.market import MarketType, Period, Side


class RawEvent(BaseModel):
    source: str
    source_event_id: str
    sport: str
    league: str
    home_name: str  # Source spelling; the normalizer maps it to a canonical team.
    away_name: str
    start_time_utc: datetime
    in_play: bool
    # False when the source doesn't say which team is home (e.g. Kalshi). The normalizer then only attaches
    # this event to a game another source already listed with the same date and the same two teams.
    home_away_known: bool = True
    metadata: dict = Field(default_factory=dict)


class RawQuote(BaseModel):
    source: str
    source_event_id: str
    source_market_id: str
    source_selection_id: str | None = None
    market_type: MarketType
    subject: str | None = None  # Player name as the source spells it (props only).
    period: Period
    # Either `side` directly, or `team` (source spelling) for moneyline/spread quotes from sources that
    # don't know home/away; the normalizer turns `team` into HOME/AWAY using the matched event.
    side: Side | None
    team: str | None = None
    line: float | None
    # Settlement rule as stated (or assumed) by the adapter for this source; see adapter docstring.
    overtime_included: bool
    decimal_odds: float
    american_odds: int | None
    is_open: bool
    timestamp_source: datetime | None
    timestamp_received: datetime
    liquidity: float | None = None
    metadata: dict = Field(default_factory=dict)


class AdapterHealth(StrEnum):
    STARTING = "STARTING"
    LIVE = "LIVE"
    DEGRADED = "DEGRADED"      # Recent errors, still retrying.
    BLOCKED = "BLOCKED"        # Source refused access (e.g. 403); we back off, never work around it.
    DISCONNECTED = "DISCONNECTED"


class OddsSourceAdapter(Protocol):
    name: str
    health: AdapterHealth

    async def discover_events(self) -> list[RawEvent]: ...

    async def fetch_snapshot(self, event: RawEvent) -> list[RawQuote]: ...

    def stream_updates(self) -> AsyncIterator[RawEvent | RawQuote]:
        """Run forever, yielding events as discovered and quotes as they change. Polling or streaming."""
        ...
