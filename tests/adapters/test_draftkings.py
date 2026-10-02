"""DraftKings parsing against the 2026-10-01 browser capture + live test responses (IND @ WAS)."""

import json
from datetime import datetime, timezone
from pathlib import Path

from src.adapters.draftkings import parse_markets, parse_nav
from src.models.market import MarketType, Side
from src.normalization.markets import Normalizer
from src.state.market_store import MarketStore

FIX = Path(__file__).parent.parent / "fixtures" / "draftkings"
NOW = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
EV = "34118061"


def load(name):
    return json.loads((FIX / name).read_text())


def test_nav_lists_games_with_home_away_and_status():
    events = {e.source_event_id: e for e in parse_nav(load("nav_nfl.json"))}
    assert len(events) == 29
    g = events[EV]
    assert (g.away_name, g.home_name) == ("IND", "WAS") and g.home_away_known and not g.in_play
    assert g.start_time_utc == datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc)
    assert events["34118083"].in_play  # PIT @ CLE was STARTED in the capture


def test_game_lines():
    qs = parse_markets(load("markets_4518_34118061.json"), "4518", EV, NOW)
    got = {(q.market_type, q.side): (q.line, q.american_odds) for q in qs}
    assert got == {
        (MarketType.MONEYLINE, Side.AWAY): (None, -192),
        (MarketType.MONEYLINE, Side.HOME): (None, 160),
        (MarketType.SPREAD, Side.AWAY): (-3.5, -110),
        (MarketType.SPREAD, Side.HOME): (3.5, -110),
        (MarketType.TOTAL, Side.OVER): (48.5, got[(MarketType.TOTAL, Side.OVER)][1]),
        (MarketType.TOTAL, Side.UNDER): (48.5, got[(MarketType.TOTAL, Side.UNDER)][1]),
    }


def test_over_under_prop():
    qs = parse_markets(load("markets_9524_34118061.json"), "9524", EV, NOW)
    jones = {q.side: q for q in qs if q.subject == "Daniel Jones"}
    assert set(jones) == {Side.OVER, Side.UNDER}
    assert jones[Side.OVER].line == 228.5 and jones[Side.OVER].market_type == MarketType.PLAYER_PASSING_YARDS
    assert jones[Side.OVER].decimal_odds == 1.89285715


def test_anytime_td_players_only_and_all_kept_in_store():
    qs = parse_markets(load("markets_12438_34118061.json"), "12438", EV, NOW)
    names = {q.subject for q in qs}
    assert len(qs) == 29 and "Jonathan Taylor" in names
    assert not any("D/ST" in n for n in names)  # team defense selections skipped
    assert all(q.side == Side.YES and q.line is None for q in qs)
    # every player survives in the store even though they share one market id
    n, store = Normalizer(), MarketStore()
    n.register_event(parse_nav(load("nav_nfl.json"))[1])
    for q in qs:
        store.update(n.normalize(q))
    assert len(store) == 29


def test_wrong_subcategory_or_event_yields_nothing():
    assert parse_markets(load("markets_4518_34118061.json"), "4518", "999", NOW) == []
    assert parse_markets(load("markets_4518_34118061.json"), "9524", EV, NOW) == []  # game lines aren't O/U props
