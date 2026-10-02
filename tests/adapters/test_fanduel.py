"""FanDuel parser tests against fixtures cut from the 2026-10-01 browser capture (no live calls)."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.adapters.fanduel import parse_event_page, parse_event_search, parse_market_prices
from src.models.market import MarketType, Side

FIX = Path(__file__).parent.parent / "fixtures" / "fanduel"
NOW = datetime(2026, 10, 2, 1, 52, tzinfo=timezone.utc)
IND_WAS = "35605982"
ML, SPREAD, TOTAL = "742.168820384", "742.168820385", "742.168820386"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def page():
    return parse_event_page(load("event_page_35605982.json"), IND_WAS, NOW)


def test_event_search_lists_games_and_in_play_flag():
    events = {e.source_event_id: e for e in parse_event_search(load("event_search.json"))}
    assert len(events) == 29
    g = events[IND_WAS]
    assert (g.away_name, g.home_name) == ("Indianapolis Colts", "Washington Commanders")
    assert g.start_time_utc == datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc)
    assert not g.in_play
    assert events["36104915"].in_play  # PIT @ CLE was live during the capture.


def test_event_page_main_lines_only(page):
    metas, quotes = page
    assert set(metas) == {ML, SPREAD, TOTAL}  # non-main markets in the fixture are ignored
    got = {(q.market_type, q.side): (q.line, q.american_odds, q.is_open) for q in quotes}
    assert got == {
        (MarketType.MONEYLINE, Side.AWAY): (None, -184, True),
        (MarketType.MONEYLINE, Side.HOME): (None, 154, True),
        (MarketType.SPREAD, Side.AWAY): (-3.5, -105, True),
        (MarketType.SPREAD, Side.HOME): (3.5, -115, True),
        (MarketType.TOTAL, Side.OVER): (48.5, -105, True),
        (MarketType.TOTAL, Side.UNDER): (48.5, -115, True),
    }
    assert all(q.overtime_included and q.timestamp_received == NOW for q in quotes)


def test_market_prices_uses_exact_decimal_odds(page):
    metas, _ = page
    quotes = parse_market_prices(load("market_prices.json"), metas, NOW)
    assert len(quotes) == 6
    away_spread = next(q for q in quotes if q.market_type == MarketType.SPREAD and q.side == Side.AWAY)
    assert away_spread.decimal_odds == pytest.approx(1 + 100 / 105)


def test_market_prices_line_move_taken_from_update(page):
    metas, _ = page
    data = copy.deepcopy(load("market_prices.json"))
    spread = next(m for m in data if m["marketId"] == SPREAD)
    for rd in spread["runnerDetails"]:
        rd["handicap"] = -3.0 if rd["handicap"] < 0 else 3.0
    quotes = parse_market_prices(data, metas, NOW)
    lines = {q.side: q.line for q in quotes if q.market_type == MarketType.SPREAD}
    assert lines == {Side.AWAY: -3.0, Side.HOME: 3.0}


def test_suspended_and_in_play_markets_are_not_open(page):
    metas, _ = page
    data = copy.deepcopy(load("market_prices.json"))
    next(m for m in data if m["marketId"] == ML)["marketStatus"] = "SUSPENDED"
    next(m for m in data if m["marketId"] == TOTAL)["inplay"] = True
    quotes = parse_market_prices(data, metas, NOW)
    status = {q.market_type: q.is_open for q in quotes}
    assert status == {MarketType.MONEYLINE: False, MarketType.SPREAD: True, MarketType.TOTAL: False}


def test_untracked_markets_ignored():
    assert parse_market_prices(load("market_prices.json"), {}, NOW) == []
