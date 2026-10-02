from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CanonicalEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str  # e.g. "NFL_2026-10-04_IND_WAS" (away_home, US/Eastern date)
    sport: str
    league: str
    home_team: str | None  # canonical team code
    away_team: str | None
    participants: tuple[str, ...]
    start_time_utc: datetime
