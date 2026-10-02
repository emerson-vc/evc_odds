"""RawEvent/RawQuote (source-shaped) -> CanonicalEvent/CanonicalQuote. Source-agnostic.

Anything that can't be mapped with certainty is dropped and logged, never guessed (CLAUDE.md §10).
"""

from __future__ import annotations

import logging
from collections import Counter

from src.adapters.base import RawEvent, RawQuote
from src.models.event import CanonicalEvent
from src.models.market import Side
from src.models.quote import CanonicalQuote
from src.normalization.entities import TeamResolver, player_key
from src.normalization.events import EASTERN, make_event_id
from src.pricing.odds import implied_probability

log = logging.getLogger(__name__)


class Normalizer:
    def __init__(self, teams: TeamResolver | None = None):
        self.teams = teams or TeamResolver()
        self.events: dict[tuple[str, str], CanonicalEvent] = {}  # (source, source_event_id) -> event
        self.pending: dict[tuple[str, str], RawEvent] = {}       # home/away-unknown events not yet matched
        self.failures: Counter[str] = Counter()

    # ------------------------------------------------------------------ events

    def register_event(self, raw: RawEvent) -> CanonicalEvent | None:
        home = self.teams.resolve(raw.league, raw.home_name)
        away = self.teams.resolve(raw.league, raw.away_name)
        if home is None or away is None:
            self.failures["unknown_team"] += 1
            log.warning("unmatched teams %s: %r / %r", raw.source, raw.away_name, raw.home_name)
            return None
        key = (raw.source, raw.source_event_id)

        if not raw.home_away_known:
            ev = self._match(raw.league, raw.start_time_utc, {home, away})
            if ev is None:
                if key not in self.pending:
                    log.info("%s event %s (%s/%s) has no matching game from another source yet",
                             raw.source, raw.source_event_id, away, home)
                self.pending[key] = raw
                return None
            self.pending.pop(key, None)
            self.events[key] = ev
            return ev

        ev = CanonicalEvent(
            event_id=make_event_id(raw.league, raw.start_time_utc, away, home),
            sport=raw.sport,
            league=raw.league,
            home_team=home,
            away_team=away,
            participants=(away, home),
            start_time_utc=raw.start_time_utc,
        )
        self.events[key] = ev
        for pending in list(self.pending.values()):  # a new known game may resolve waiting ones
            self.register_event(pending)
        return ev

    def _match(self, league: str, start_utc, teams: set[str]) -> CanonicalEvent | None:
        """Same league, same US/Eastern date, same two teams — and exactly one such game."""
        day = start_utc.astimezone(EASTERN).date()
        found = {
            e.event_id: e for (src, _), e in self.events.items()
            if e.league == league and {e.home_team, e.away_team} == teams
            and e.start_time_utc.astimezone(EASTERN).date() == day
        }
        return next(iter(found.values())) if len(found) == 1 else None

    @property
    def unmatched_event_count(self) -> int:
        return len(self.pending)

    # ------------------------------------------------------------------ quotes

    def normalize(self, raw: RawQuote) -> CanonicalQuote | None:
        ev = self.events.get((raw.source, raw.source_event_id))
        if ev is None:
            self.failures["unknown_event"] += 1
            return None

        side = raw.side
        if side is None:
            code = self.teams.resolve(ev.league, raw.team or "")
            side = Side.HOME if code == ev.home_team else Side.AWAY if code == ev.away_team else None
            if side is None:
                self.failures["team_not_in_game"] += 1
                return None

        subject, metadata = None, raw.metadata
        if raw.market_type.is_player_prop:
            if not raw.subject:
                self.failures["prop_without_player"] += 1
                return None
            subject = player_key(raw.subject)
            metadata = {**raw.metadata, "subject_name": raw.subject}
        return CanonicalQuote(
            source=raw.source,
            source_event_id=raw.source_event_id,
            source_market_id=raw.source_market_id,
            event_id=ev.event_id,
            sport=ev.sport,
            league=ev.league,
            market_type=raw.market_type,
            subject=subject,
            period=raw.period,
            side=side,
            line=raw.line,
            overtime_included=raw.overtime_included,
            decimal_odds=raw.decimal_odds,
            american_odds=raw.american_odds,
            implied_probability_raw=implied_probability(raw.decimal_odds),
            is_open=raw.is_open,
            timestamp_source=raw.timestamp_source,
            timestamp_received=raw.timestamp_received,
            liquidity=raw.liquidity,
            metadata=metadata,
        )
