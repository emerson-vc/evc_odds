"""BetMGM parsing against the 2026-10-02 browser capture (IND @ WAS = fixture 6:43500), plus the ingest path."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from src.adapters.base import AdapterHealth
from src.adapters.betmgm import BetMGMAdapter, parse_payload
from src.models.market import MarketType, Side

FIX = Path(__file__).parent.parent / "fixtures" / "betmgm"
NOW = datetime(2026, 10, 2, 23, 30, tzinfo=timezone.utc)
VIEW = "/cds-api/bettingoffer/fixture-view"
LIST = "/cds-api/bettingoffer/fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_list_keeps_only_pregame_nfl():
    events, quotes = parse_payload(LIST, load("fixtures_football.json"), NOW)
    assert [e.source_event_id for e in events] == ["6:43500"]  # NCAAF and live games dropped
    ev = events[0]
    assert (ev.away_name, ev.home_name) == ("Indianapolis Colts", "Washington Commanders")
    assert ev.start_time_utc == datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc)
    assert {q.market_type for q in quotes} == {MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL}


def test_game_lines_including_alternates():
    _, quotes = parse_payload(LIST, load("fixtures_football.json"), NOW)
    ml = {q.side: q.american_odds for q in quotes if q.market_type == MarketType.MONEYLINE}
    assert ml == {Side.AWAY: -210, Side.HOME: 175}
    spreads = {(q.side, q.line): q.american_odds for q in quotes if q.market_type == MarketType.SPREAD}
    assert spreads[(Side.AWAY, -3.5)] == -130 and spreads[(Side.HOME, 3.5)] == -105
    assert spreads[(Side.AWAY, -3.0)] == -155 and spreads[(Side.HOME, 3.0)] == 115  # integer line kept exact
    assert spreads[(Side.AWAY, 1.5)] == -235  # "Indianapolis Colts +1.5" when home handicap is -1.5
    totals = {(q.side, q.line): q.american_odds for q in quotes if q.market_type == MarketType.TOTAL}
    assert totals[(Side.OVER, 47.5)] == -108 and totals[(Side.UNDER, 47.5)] == -110


def test_player_props_and_anytime_td():
    _, quotes = parse_payload(VIEW, load("fixture_view_6-43500.json"), NOW)
    jones = {q.side: q for q in quotes if q.subject == "Daniel Jones" and q.market_type == MarketType.PLAYER_PASSING_YARDS}
    assert jones[Side.OVER].line == 223.5 and jones[Side.OVER].american_odds == -125
    assert jones[Side.UNDER].american_odds == -105
    atd = [q for q in quotes if q.market_type == MarketType.PLAYER_ANYTIME_TD]
    assert atd and all(q.side == Side.YES and q.line is None for q in atd)
    assert not any("D/ST" in q.subject for q in atd)
    # milestone yes-bets ("Marcus Mariota to throw 300+ passing yards") are not emitted as O/U props
    import re
    milestone = re.compile(r"\bto (throw|record|rush)\b.*\d+\+")
    ou = [q for q in quotes if q.market_type.is_player_prop and q.market_type != MarketType.PLAYER_ANYTIME_TD]
    assert ou and not any(milestone.search(q.metadata["market_name"]) for q in ou)
    assert any(q.metadata["market_name"].endswith("Total Passing + Rushing Yards") for q in ou)  # a real O/U


def test_spread_option_name_must_match_handicap():
    data = load("fixtures_football.json")
    fx = data["fixtures"][0]
    m = next(m for m in fx["optionMarkets"] if {p["key"]: p["value"] for p in m["parameters"]}.get("DecimalHandicap") == "3.0000")
    m["options"][0]["name"]["value"] = "Indianapolis Colts -7"  # corrupt the label
    _, quotes = parse_payload(LIST, data, NOW)
    assert not any(q.source_market_id == str(m["id"]) and q.side == Side.AWAY for q in quotes)


def test_push_adapter_parses_submitted_payloads_and_goes_idle():
    async def run():
        a = BetMGMAdapter({"idle_seconds": 0.2})
        a.submit(LIST, load("fixtures_football.json"))
        gen = a.stream_updates()
        items = [await gen.__anext__() for _ in range(5)]
        assert items[0].source_event_id == "6:43500"
        task = asyncio.ensure_future(gen.__anext__())  # drains the rest, then waits for more
        await asyncio.sleep(0.6)
        task.cancel()
        assert a.payloads_received == 1 and a.request_count == 0
        return a
    a = asyncio.run(run())
    assert a.health in (AdapterHealth.LIVE, AdapterHealth.DISCONNECTED)
