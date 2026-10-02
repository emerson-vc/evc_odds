from datetime import datetime, timedelta, timezone

from src.adapters.base import RawEvent, RawQuote
from src.models.market import MarketType, Period, Side
from src.normalization.entities import TeamResolver
from src.pricing.devig import ProportionalDevig
from src.pricing.odds import american_to_decimal
from src.scanner.board import build_board
from src.scanner.engine import Scanner

NOW = datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc)
KICK = datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc)


def ev(source, start=KICK, sid="1"):
    return RawEvent(source=source, source_event_id=sid, sport="american_football", league="NFL",
                    away_name="Indianapolis Colts", home_name="Washington Commanders",
                    start_time_utc=start, in_play=False)


def q(source, side, american, line=None, mtype=MarketType.MONEYLINE, age=0, is_open=True, sid="1", mid="m"):
    return RawQuote(source=source, source_event_id=sid, source_market_id=f"{source}-{mid}-{mtype}",
                    market_type=mtype, period=Period.FULL_GAME, side=side, line=line, overtime_included=True,
                    decimal_odds=american_to_decimal(american), american_odds=american, is_open=is_open,
                    timestamp_source=None, timestamp_received=NOW - timedelta(seconds=age))


def board_for(items):
    s = Scanner([])
    for it in items:
        s.ingest(it)
    return build_board(s.store.all(), list(s.normalizer.events.values()), [], TeamResolver(),
                       ProportionalDevig(), stale_seconds=30, changed_at=s.changed_at, slot_of=s.store.slot, now=NOW)


def test_best_price_and_no_vig_moneyline():
    b = board_for([ev("fanduel"), ev("draftkings"),
                   q("fanduel", Side.AWAY, -184), q("fanduel", Side.HOME, 154),
                   q("draftkings", Side.AWAY, -175), q("draftkings", Side.HOME, 145)])
    g = b["games"][0]
    assert (g["away"], g["home"], g["home_name"]) == ("IND", "WAS", "Washington Commanders")
    ml = g["markets"]["MONEYLINE"]
    assert ml["AWAY"]["draftkings"]["best"] and not ml["AWAY"]["fanduel"]["best"]
    assert ml["HOME"]["fanduel"]["best"] and not ml["HOME"]["draftkings"]["best"]
    # FanDuel -184/+154 de-vigged ~ -167/+167
    assert ml["AWAY"]["fanduel"]["no_vig"] == -ml["HOME"]["fanduel"]["no_vig"]


def test_best_is_per_line_and_ignores_stale():
    b = board_for([ev("fanduel"), ev("draftkings"), ev("betmgm"),
                   q("fanduel", Side.AWAY, -105, -3.5, MarketType.SPREAD),
                   q("draftkings", Side.AWAY, +100, -3.0, MarketType.SPREAD),       # different line
                   q("betmgm", Side.AWAY, +110, -3.5, MarketType.SPREAD, age=120)])  # stale
    sp = b["games"][0]["markets"]["SPREAD"]["AWAY"]
    assert sp["betmgm"]["stale"] and not sp["betmgm"]["best"]
    assert sp["fanduel"]["best"] and sp["draftkings"]["best"]  # each is best on its own line


def test_single_source_marks_nothing_best_and_one_sided_has_no_no_vig():
    b = board_for([ev("fanduel"), q("fanduel", Side.AWAY, -184)])
    c = b["games"][0]["markets"]["MONEYLINE"]["AWAY"]["fanduel"]
    assert not c["best"] and c["no_vig"] is None


def test_props_listed_per_player_with_no_vig_only_when_two_sided():
    def prop(source, side, american, line, mtype, player):
        return q(source, side, american, line, mtype, mid=player).model_copy(update={"subject": player})
    b = board_for([ev("fanduel"),
                   q("fanduel", Side.AWAY, -184), q("fanduel", Side.HOME, 154),
                   prop("fanduel", Side.OVER, 116, 4.5, MarketType.PLAYER_RECEPTIONS, "Stefon Diggs"),
                   prop("fanduel", Side.UNDER, -154, 4.5, MarketType.PLAYER_RECEPTIONS, "Stefon Diggs"),
                   prop("fanduel", Side.YES, -310, None, MarketType.PLAYER_ANYTIME_TD, "Jonathan Taylor")])
    g = b["games"][0]
    assert set(g["markets"]) == {"MONEYLINE", "SPREAD", "TOTAL"}  # props don't leak into game lines
    props = {(p["label"], p["player_name"]): p for p in g["props"]}
    assert list(props) == [("Receptions", "Stefon Diggs"), ("Anytime TD", "Jonathan Taylor")]
    rec = props[("Receptions", "Stefon Diggs")]["sides"]
    assert rec["OVER"]["fanduel"]["line"] == 4.5 and rec["OVER"]["fanduel"]["no_vig"] is not None
    assert props[("Anytime TD", "Jonathan Taylor")]["sides"]["YES"]["fanduel"]["no_vig"] is None


def test_started_games_hidden():
    b = board_for([ev("fanduel", start=NOW - timedelta(minutes=5)), q("fanduel", Side.AWAY, -184)])
    assert b["games"] == []


def test_scanner_records_changes_not_first_sightings_or_repeats():
    seen = []
    s = Scanner([], on_change=lambda new, prev: seen.append((prev.american_odds, new.american_odds)))
    s.ingest(ev("fanduel"))
    s.ingest(q("fanduel", Side.AWAY, -184))
    s.ingest(q("fanduel", Side.AWAY, -184))
    s.ingest(q("fanduel", Side.AWAY, -190))
    assert seen == [(-184, -190)] and len(s.changed_at) == 1
