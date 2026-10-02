from datetime import datetime, timedelta, timezone

from src.adapters.base import RawEvent, RawQuote
from src.models.market import MarketKey, MarketType, Period, Side
from src.normalization.entities import TeamResolver
from src.normalization.events import make_event_id
from src.normalization.markets import Normalizer
from src.state.market_store import MarketStore

NOW = datetime(2026, 10, 2, 1, 52, tzinfo=timezone.utc)


def raw_event(away="Indianapolis Colts", home="Washington Commanders", start=datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc)):
    return RawEvent(source="fanduel", source_event_id="1", sport="american_football", league="NFL",
                    away_name=away, home_name=home, start_time_utc=start, in_play=False)


def raw_quote(side=Side.AWAY, line=-3.5, decimal=1.95, received=NOW, market_id="m1", mtype=MarketType.SPREAD):
    return RawQuote(source="fanduel", source_event_id="1", source_market_id=market_id, market_type=mtype,
                    period=Period.FULL_GAME, side=side, line=line, overtime_included=True,
                    decimal_odds=decimal, american_odds=None, is_open=True,
                    timestamp_source=None, timestamp_received=received)


def test_team_resolution_exact_aliases_only():
    t = TeamResolver()
    assert t.resolve("NFL", "Washington Commanders") == "WAS"
    assert t.resolve("NFL", "washington commanders") == "WAS"
    assert t.resolve("NFL", "San Francisco 49ers") == "SF"
    assert t.resolve("NFL", "Washington") is None  # ambiguous partials are not guessed
    assert t.resolve("NBA", "Washington Commanders") is None


def test_player_key():
    from src.normalization.entities import player_key
    assert player_key("Stefon Diggs") == "STEFON_DIGGS"
    assert player_key("D.J. Moore") == player_key("DJ Moore") == "DJ_MOORE"
    assert player_key("Marvin Harrison Jr.") == player_key("Marvin Harrison") == "MARVIN_HARRISON"
    assert player_key("Amon-Ra St. Brown") == "AMON_RA_ST_BROWN"
    assert player_key("Josh Allen") != player_key("Joshua Allen")  # nicknames are not guessed


def test_prop_quote_gets_canonical_subject_and_needs_a_player():
    n = Normalizer()
    n.register_event(raw_event())
    prop = raw_quote(side=Side.OVER, line=4.5, mtype=MarketType.PLAYER_RECEPTIONS).model_copy(
        update={"subject": "Stefon Diggs"})
    q = n.normalize(prop)
    assert q.subject == "STEFON_DIGGS" and q.metadata["subject_name"] == "Stefon Diggs"
    assert q.market_key.group() != q.model_copy(update={"subject": "TERRY_MCLAURIN"}).market_key.group()
    assert n.normalize(prop.model_copy(update={"subject": None})) is None
    assert n.failures["prop_without_player"] == 1


def test_event_id_uses_eastern_date():
    # Thursday 8:16pm ET kickoff is already Friday in UTC; ID must carry Thursday's date.
    tnf = datetime(2026, 10, 2, 0, 16, tzinfo=timezone.utc)
    assert make_event_id("NFL", tnf, "PIT", "CLE") == "NFL_2026-10-01_PIT_CLE"
    # A few minutes' disagreement on start time between books doesn't change the ID.
    assert make_event_id("NFL", tnf + timedelta(minutes=4), "PIT", "CLE") == "NFL_2026-10-01_PIT_CLE"


def test_normalizer_builds_canonical_quote():
    n = Normalizer()
    ev = n.register_event(raw_event())
    assert ev.event_id == "NFL_2026-10-04_IND_WAS"
    q = n.normalize(raw_quote())
    assert q.event_id == ev.event_id and q.line == -3.5
    assert q.implied_probability_raw == 1 / 1.95


def test_unknown_team_is_dropped_not_guessed():
    n = Normalizer()
    assert n.register_event(raw_event(away="Indy Colts")) is None
    assert n.normalize(raw_quote()) is None
    assert n.failures == {"unknown_team": 1, "unknown_event": 1}


def test_market_key_spread_sides_share_group_and_integer_lines_differ():
    away = MarketKey(event_id="E", market_type=MarketType.SPREAD, side=Side.AWAY, line=-3.5, overtime_included=True)
    home = away.opposite()
    assert (home.side, home.line) == (Side.HOME, 3.5)
    assert away.group() == home.group()
    minus3 = away.model_copy(update={"line": -3.0})
    assert minus3 != away and minus3.group() != away.group()


def test_market_key_overtime_rule_separates_markets():
    a = MarketKey(event_id="E", market_type=MarketType.TOTAL, side=Side.OVER, line=48.5, overtime_included=True)
    b = a.model_copy(update={"overtime_included": False})
    assert a != b and a.group() != b.group()


def test_store_dedupes_repeats_and_replaces_moved_line():
    n, store = Normalizer(), MarketStore()
    n.register_event(raw_event())
    first = n.normalize(raw_quote())
    assert store.update(first) is first  # first sighting counts as a change
    assert store.update(n.normalize(raw_quote(received=NOW + timedelta(seconds=10)))) is None  # same price
    moved = n.normalize(raw_quote(line=-3.0, decimal=2.0))
    assert store.update(moved).line == -3.5  # returns the previous quote
    assert len(store) == 1  # the stale -3.5 entry was replaced, not kept alongside
