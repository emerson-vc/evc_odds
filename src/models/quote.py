from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from src.models.market import MarketKey, MarketType, Period, Side


class CanonicalQuote(BaseModel):
    """One source's current price for one outcome, after normalization (CLAUDE.md §8)."""

    source: str
    source_event_id: str
    source_market_id: str | None
    event_id: str
    sport: str
    league: str
    market_type: MarketType
    subject: str | None = None
    period: Period
    side: Side
    line: float | None
    overtime_included: bool
    decimal_odds: float
    american_odds: int | None
    implied_probability_raw: float
    is_open: bool  # False when the source has suspended/closed the market.
    timestamp_source: datetime | None
    timestamp_received: datetime
    liquidity: float | None = None
    metadata: dict = Field(default_factory=dict)

    @property
    def market_key(self) -> MarketKey:
        return MarketKey(
            event_id=self.event_id,
            market_type=self.market_type,
            subject=self.subject,
            period=self.period,
            side=self.side,
            line=self.line,
            overtime_included=self.overtime_included,
        )

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.timestamp_received).total_seconds()

    def same_price(self, other: CanonicalQuote) -> bool:
        return (self.decimal_odds, self.line, self.is_open) == (other.decimal_odds, other.line, other.is_open)
