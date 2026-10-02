"""Kalshi parsing against real API responses saved 2026-10-01 (IND @ WAS, Oct 4)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.adapters.kalshi import Pricer, effective_decimal, game_date_noon_utc, parse_games, parse_ladder
from src.models.market import MarketType, Side
from src.normalization.events import EASTERN

FIX = Path(__file__).parent.parent / "fixtures" / "kalshi"
NOW = datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc)
KEY = "26OCT04INDWAS"
PRICER = Pricer(0.07, 1)


def events(series):
    return json.loads((FIX / f"{series}.json").read_text())["events"]


@pytest.fixture
def games():
    return parse_games(events("KXNFLGAME"), PRICER, NOW)


def test_effective_odds_include_fee():
    # 50c ask + 0.07*.5*.5 = 51.75c per $1 contract
    assert effective_decimal(0.50, 0.07) == pytest.approx(1 / 0.5175)
    assert effective_decimal(0.50, 0.0) == pytest.approx(2.0)


def test_ticker_date_keeps_eastern_calendar_day():
    d = game_date_noon_utc("26OCT01PITCLE")
    assert d.astimezone(EASTERN).date().isoformat() == "2026-10-01"
    assert game_date_noon_utc("garbage") is None


def test_game_event_and_moneyline(games):
    raw_events, uuid_map, teams, quotes = games
    ev = raw_events[0]
    assert ev.source_event_id == KEY and not ev.home_away_known
    assert {ev.home_name, ev.away_name} == {"IND", "WAS"}
    assert set(teams[KEY]) == {"IND", "WAS"} and set(uuid_map.values()) == {"IND", "WAS"}
    ml = {q.team: q for q in quotes}
    assert set(ml) == {"IND", "WAS"} and all(q.side is None for q in ml.values())
    # cheapest way to back each team: YES on its market or NO on the opponent's
    assert ml["IND"].metadata["ask"] == 0.64 or ml["IND"].metadata["ask"] == 0.65
    assert ml["IND"].decimal_odds == pytest.approx(effective_decimal(ml["IND"].metadata["ask"], 0.07))
    assert ml["IND"].metadata["tie_rule"] == "half_payout"


def test_spread_ladder_sides_and_lines(games):
    _, uuid_map, teams, _ = games
    qs = parse_ladder("KXNFLSPREAD", events("KXNFLSPREAD"), PRICER, NOW, uuid_map, teams)
    assert qs and all(q.market_type == MarketType.SPREAD and q.team in {"IND", "WAS"} for q in qs)
    # "IND wins by over 20.5": YES = IND -20.5, NO = WAS +20.5
    yes = next(q for q in qs if q.source_market_id.endswith("-IND21") and q.team == "IND")
    no = next(q for q in qs if q.source_market_id.endswith("-IND21") and q.team == "WAS")
    assert (yes.line, no.line) == (-20.5, 20.5)
    assert all(q.line % 1 == 0.5 for q in qs)  # half-point strikes only


def test_spread_skipped_when_team_unknown(games):
    _, _, teams, _ = games
    assert parse_ladder("KXNFLSPREAD", events("KXNFLSPREAD"), PRICER, NOW, {}, teams) == []


def test_total_ladder():
    qs = parse_ladder("KXNFLTOTAL", events("KXNFLTOTAL"), PRICER, NOW, {}, {})
    over = [q for q in qs if q.side == Side.OVER]
    under = [q for q in qs if q.side == Side.UNDER]
    assert over and under and {q.line for q in over} & {q.line for q in under}


def test_player_props_from_ladders():
    qs = parse_ladder("KXNFLREC", events("KXNFLREC"), PRICER, NOW, {}, {})
    diggs = {(q.side, q.line) for q in qs if q.subject == "Stefon Diggs"}
    assert (Side.OVER, 4.5) in diggs and (Side.UNDER, 4.5) in diggs  # "5+ receptions" == over 4.5
    assert all(q.market_type == MarketType.PLAYER_RECEPTIONS for q in qs)
    yds = parse_ladder("KXNFLPASSYDS", events("KXNFLPASSYDS"), PRICER, NOW, {}, {})
    assert any(q.subject == "Marcus Mariota" and q.line == 224.5 for q in yds)


def test_thin_or_empty_asks_ignored():
    m = {"yes_ask_dollars": "0.4000", "yes_ask_size_fp": "0.5", "no_ask_dollars": "1.0000", "yes_bid_size_fp": "50"}
    assert Pricer(0.07, 1).side(m, True) is None   # below min depth
    assert Pricer(0.07, 1).side(m, False) is None  # no real NO offer at $1.00
