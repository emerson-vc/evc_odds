"""Polymarket US parsing against a real public-API response saved 2026-10-02 (IND @ WAS)."""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.adapters.polymarket import parse_events
from src.models.market import MarketType, Side
from src.pricing.fees import effective_decimal

FIX = Path(__file__).parent.parent / "fixtures" / "polymarket"
NOW = datetime(2026, 10, 2, 23, 55, tzinfo=timezone.utc)


def load():
    return json.loads((FIX / "nfl_events.json").read_text())


def test_fee_formula_matches_docs_example():
    # docs.polymarket.us/fees: 1,000 contracts at $0.10 -> taker pays 0.0695*1000*0.1*0.9 = $6.26
    assert 1000 * (1 / effective_decimal(0.10, 0.0695) - 0.10) == pytest.approx(6.255)
    with pytest.raises(ValueError):
        effective_decimal(1.0, 0.0695)


def test_event_is_pregame_and_home_away_unknown():
    events, _ = parse_events(load(), NOW)
    assert len(events) == 1
    ev = events[0]
    assert ev.source_event_id == "nfl-ind-was-2026-10-04" and not ev.home_away_known
    assert {ev.home_name, ev.away_name} == {"IND", "WAS"}


def test_moneyline_uses_each_sides_buy_price_plus_fee():
    _, qs = parse_events(load(), NOW)
    ml = {q.team: q for q in qs if q.market_type == MarketType.MONEYLINE}
    assert set(ml) == {"IND", "WAS"}
    assert ml["IND"].metadata["price"] == 0.655 and ml["WAS"].metadata["price"] == 0.3475  # 1 - best bid
    assert ml["WAS"].decimal_odds == pytest.approx(effective_decimal(0.3475, 0.0695))
    assert ml["IND"].metadata["tie_rule"] == "half_payout"


def test_spreads_both_sides_mirror():
    _, qs = parse_events(load(), NOW)
    sp = [q for q in qs if q.market_type == MarketType.SPREAD]
    by_market = {}
    for q in sp:
        by_market.setdefault(q.source_market_id, []).append(q)
    assert by_market
    for legs in by_market.values():
        assert len(legs) == 2 and {q.team for q in legs} == {"IND", "WAS"}
        assert legs[0].line == -legs[1].line
    assert any(q.team == "IND" and q.line == -3.5 for q in sp) and any(q.team == "WAS" and q.line == 3.5 for q in sp)


def test_game_totals_only_not_team_totals_or_halves():
    _, qs = parse_events(load(), NOW)
    tot = [q for q in qs if q.market_type == MarketType.TOTAL]
    assert tot and {q.side for q in tot} == {Side.OVER, Side.UNDER}
    names = {str(m["id"]): m["sportsMarketType"] for m in load()["events"][0]["markets"]}
    assert {names[q.source_market_id] for q in qs} <= {
        "football_team_full_game_winner", "football_team_full_game_spread", "football_team_full_game_total"}


def test_inconsistent_spread_is_skipped():
    data = load()
    m = next(m for m in data["events"][0]["markets"] if m["sportsMarketType"] == "football_team_full_game_spread")
    m["description"] = m["description"].replace("covers a", "covers a +99 point spread, not a")
    _, qs = parse_events(data, NOW)
    assert not any(q.source_market_id == str(m["id"]) for q in qs)


def test_started_or_closed_games_skipped():
    data = load()
    started = copy.deepcopy(data); started["events"][0]["period"] = "Q1"
    closed = copy.deepcopy(data); closed["events"][0]["closed"] = True
    assert parse_events(started, NOW) == ([], []) and parse_events(closed, NOW) == ([], [])
