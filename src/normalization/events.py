"""Deterministic cross-source event IDs."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def make_event_id(league: str, start_time_utc: datetime, away: str, home: str) -> str:
    """e.g. NFL_2026-10-04_IND_WAS.

    Uses the US/Eastern calendar date so a Thursday 8:15pm ET kickoff (already Friday in UTC) gets the
    same date at every source, and small start-time differences between books don't split one game in two.
    """
    day = start_time_utc.astimezone(EASTERN).date().isoformat()
    return f"{league}_{day}_{away}_{home}"
