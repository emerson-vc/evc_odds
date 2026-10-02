"""FanDuel player-prop parsing against fixtures from the same-game-parlay tab (2026-10-01 capture)."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.adapters.fanduel import parse_event_page, parse_market_prices
from src.models.market import MarketType, Side

FIX = Path(__file__).parent.parent / "fixtures" / "fanduel"
NOW = datetime(2026, 10, 2, 1, 52, tzinfo=timezone.utc)
IND_WAS = "35605982"
REC_YDS, ATD, ALT = "742.189262944", "742.188826927", "742.189262945"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def page():
    return parse_event_page(load("event_page_sgp_35605982.json"), IND_WAS, NOW)


def test_tab_contains_game_lines_and_props_but_not_alt_ladders(page):
    metas, _ = page
    types = sorted(m.market_type for m in metas.values())
    assert types == sorted([
        MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL,
        MarketType.PLAYER_RECEIVING_YARDS, MarketType.PLAYER_PASSING_YARDS, MarketType.PLAYER_RECEPTIONS,
        MarketType.PLAYER_ANYTIME_TD,
    ])
    assert ALT not in metas  # "X+ Yards" ladders are skipped for now


def test_over_under_prop(page):
    _, quotes = page
    rec = {q.side: q for q in quotes if q.source_market_id == REC_YDS}
    assert set(rec) == {Side.OVER, Side.UNDER}
    assert {q.subject for q in rec.values()} == {"Antonio Williams"}
    assert rec[Side.OVER].line == rec[Side.UNDER].line == 17.5
    assert rec[Side.OVER].american_odds == -114

    diggs = [q for q in quotes if q.market_type == MarketType.PLAYER_RECEPTIONS]
    assert {(q.subject, q.side, q.line, q.american_odds) for q in diggs} == {
        ("Stefon Diggs", Side.OVER, 4.5, 116), ("Stefon Diggs", Side.UNDER, 4.5, -154)}


def test_anytime_td_is_one_sided_per_player(page):
    _, quotes = page
    atd = [q for q in quotes if q.source_market_id == ATD]
    assert len(atd) == 31
    assert all(q.side == Side.YES and q.line is None for q in atd)
    taylor = next(q for q in atd if q.subject == "Jonathan Taylor")
    assert taylor.american_odds == -310


def test_prop_line_move_and_prices_from_refresh(page):
    metas, _ = page
    data = copy.deepcopy(load("market_prices_sgp.json"))
    rec = next(m for m in data if m["marketId"] == REC_YDS)
    for rd in rec["runnerDetails"]:
        rd["handicap"] = 19.5
    quotes = parse_market_prices(data, metas, NOW)
    assert {q.line for q in quotes if q.source_market_id == REC_YDS} == {19.5}
    assert any(q.source_market_id == ATD and q.subject == "Jonathan Taylor" for q in quotes)
    assert not any(q.source_market_id == ALT for q in quotes)


def test_prop_with_unexpected_runner_names_is_refused():
    data = copy.deepcopy(load("event_page_sgp_35605982.json"))
    data["attachments"]["markets"][REC_YDS]["runners"][0]["runnerName"] = "Someone Else Over"
    metas, quotes = parse_event_page(data, IND_WAS, NOW)
    assert REC_YDS not in metas and not any(q.source_market_id == REC_YDS for q in quotes)
