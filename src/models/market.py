"""Canonical market vocabulary and the MarketKey that decides which quotes are the *same bet* (CLAUDE.md §9)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MarketType(StrEnum):
    MONEYLINE = "MONEYLINE"
    SPREAD = "SPREAD"
    TOTAL = "TOTAL"
    # Player props. Over/Under on a line, except ANYTIME_TD which is one-sided (YES only).
    PLAYER_PASSING_YARDS = "PLAYER_PASSING_YARDS"
    PLAYER_PASSING_TDS = "PLAYER_PASSING_TDS"
    PLAYER_RUSHING_YARDS = "PLAYER_RUSHING_YARDS"
    PLAYER_RECEIVING_YARDS = "PLAYER_RECEIVING_YARDS"
    PLAYER_RECEPTIONS = "PLAYER_RECEPTIONS"
    PLAYER_PASS_RUSH_YARDS = "PLAYER_PASS_RUSH_YARDS"
    PLAYER_RUSH_REC_YARDS = "PLAYER_RUSH_REC_YARDS"
    PLAYER_ANYTIME_TD = "PLAYER_ANYTIME_TD"

    @property
    def is_player_prop(self) -> bool:
        return self.value.startswith("PLAYER_")


GAME_LINES = (MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL)

PROP_LABELS = {
    MarketType.PLAYER_PASSING_YARDS: "Passing Yds",
    MarketType.PLAYER_PASSING_TDS: "Passing TDs",
    MarketType.PLAYER_RUSHING_YARDS: "Rushing Yds",
    MarketType.PLAYER_RECEIVING_YARDS: "Receiving Yds",
    MarketType.PLAYER_RECEPTIONS: "Receptions",
    MarketType.PLAYER_PASS_RUSH_YARDS: "Pass + Rush Yds",
    MarketType.PLAYER_RUSH_REC_YARDS: "Rush + Rec Yds",
    MarketType.PLAYER_ANYTIME_TD: "Anytime TD",
}


class Period(StrEnum):
    FULL_GAME = "FULL_GAME"


class Side(StrEnum):
    HOME = "HOME"
    AWAY = "AWAY"
    OVER = "OVER"
    UNDER = "UNDER"
    YES = "YES"  # one-sided markets (e.g. anytime TD); no opposite offered, so never de-viggable


_OPPOSITE = {Side.HOME: Side.AWAY, Side.AWAY: Side.HOME, Side.OVER: Side.UNDER, Side.UNDER: Side.OVER}


class MarketKey(BaseModel):
    """Two quotes are comparable only if their MarketKeys are equal.

    Line conventions:
      SPREAD: line is the handicap of `side` (AWAY -3.5 and HOME +3.5 are the two sides of one market).
      TOTAL:  line is the total, same for OVER and UNDER.
      MONEYLINE: line is None.
    Integer lines are kept exact: SPREAD -3.0 and -3.5 are different bets.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    market_type: MarketType
    subject: str | None = None  # Canonical player key for props (e.g. "STEFON_DIGGS"); None for game lines.
    period: Period = Period.FULL_GAME
    side: Side
    line: float | None = None
    overtime_included: bool

    def opposite(self) -> MarketKey | None:
        """The other outcome of this two-way market (needed to de-vig). None for one-sided markets."""
        if self.side not in _OPPOSITE:
            return None
        line = -self.line if self.market_type == MarketType.SPREAD and self.line is not None else self.line
        return self.model_copy(update={"side": _OPPOSITE[self.side], "line": line})

    def group(self) -> tuple:
        """Identifier shared by both outcomes of the same market (side-independent)."""
        line = self.line
        if self.market_type == MarketType.SPREAD and self.side == Side.AWAY and line is not None:
            line = -line  # Express every spread market by the HOME handicap.
        return (self.event_id, self.market_type, self.subject, self.period, line, self.overtime_included)

    def label(self) -> str:
        if self.market_type == MarketType.MONEYLINE:
            return f"{self.side} ML"
        if self.market_type == MarketType.SPREAD:
            return f"{self.side} {self.line:+g}"
        prefix = f"{self.subject} {PROP_LABELS.get(self.market_type, '')} ".lstrip() if self.subject else ""
        return f"{prefix}{self.side}" + ("" if self.line is None else f" {self.line:g}")
