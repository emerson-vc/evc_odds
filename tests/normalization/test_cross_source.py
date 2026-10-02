"""A home/away-unknown source (Kalshi) attaching to games listed by a home/away-known source (FanDuel)."""

from datetime import datetime, timezone

from src.adapters.base import RawEvent, RawQuote
from src.models.market import MarketType, Period, Side
from src.normalization.entities import TeamResolver
from src.normalization.markets import Normalizer
from src.pricing.devig import ProportionalDevig
from src.scanner.board import build_board
from src.scanner.engine import Scanner

NOW = datetime(2026, 10, 2, 2, 0, tzinfo=timezone.utc)
FD_KICK = datetime(2026, 10, 4, 13, 30, tzinfo=timezone.utc)      # 9:30am ET London game
KALSHI_DAY = datetime(2026, 10, 4, 16, 0, tzinfo=timezone.utc)    # Kalshi only knows the date


def fd_event():
    return RawEvent(source="fanduel", source_event_id="35605982", sport="american_football", league="NFL",
                    away_name="Indianapolis Colts", home_name="Washington Commanders",
                    start_time_utc=FD_KICK, in_play=False)


def k_event(a="IND", b="WAS", when=KALSHI_DAY):
    return RawEvent(source="kalshi", source_event_id="26OCT04INDWAS", sport="american_football", league="NFL",
                    away_name=a, home_name=b, start_time_utc=when, in_play=False, home_away_known=False)


def quote(source, sid, american_dec, *, side=None, team=None, line=None, mtype=MarketType.MONEYLINE, mid="m"):
    return RawQuote(source=source, source_event_id=sid, source_market_id=mid, market_type=mtype,
                    period=Period.FULL_GAME, side=side, team=team, line=line, overtime_included=True,
                    decimal_odds=american_dec, american_odds=None, is_open=True,
                    timestamp_source=None, timestamp_received=NOW)


def test_kalshi_event_waits_for_fanduel_then_attaches():
    n = Normalizer()
    assert n.register_event(k_event(b="WAS", a="IND")) is None and n.unmatched_event_count == 1
    fd = n.register_event(fd_event())
    assert n.unmatched_event_count == 0
    assert n.events[("kalshi", "26OCT04INDWAS")] is fd  # same canonical game


def test_listed_order_does_not_matter_but_date_and_teams_do():
    n = Normalizer()
    fd = n.register_event(fd_event())
    assert n.register_event(k_event(a="WAS", b="IND")) is fd
    other_day = datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc)
    n2 = Normalizer(); n2.register_event(fd_event())
    assert n2.register_event(k_event(when=other_day)) is None
    n3 = Normalizer(); n3.register_event(fd_event())
    assert n3.register_event(k_event(a="IND", b="NYG")) is None


def test_team_quotes_become_home_away():
    n = Normalizer()
    n.register_event(fd_event()); n.register_event(k_event())
    was = n.normalize(quote("kalshi", "26OCT04INDWAS", 2.7, team="WAS"))
    ind = n.normalize(quote("kalshi", "26OCT04INDWAS", 1.5, team="IND", mtype=MarketType.SPREAD, line=-3.5))
    assert was.side == Side.HOME and ind.side == Side.AWAY and ind.line == -3.5
    assert n.normalize(quote("kalshi", "26OCT04INDWAS", 2.0, team="DAL")) is None
    assert n.failures["team_not_in_game"] == 1


def test_board_shows_exchange_at_the_sportsbook_line():
    s = Scanner([])
    s.ingest(fd_event()); s.ingest(k_event())
    s.ingest(quote("fanduel", "35605982", 1.952, side=Side.AWAY, line=-3.5, mtype=MarketType.SPREAD, mid="fd"))
    for line, dec in [(-2.5, 1.7), (-3.5, 1.9), (-6.5, 2.6)]:
        s.ingest(quote("kalshi", "26OCT04INDWAS", dec, team="IND", line=line, mtype=MarketType.SPREAD, mid=f"k{line}"))
    s.ingest(quote("kalshi", "26OCT04INDWAS", 1.95, team="IND", line=None, mtype=MarketType.SPREAD, mid="x")
             .model_copy(update={"line": -9.5}))
    b = build_board(s.store.all(), list(s.normalizer.events.values()), [], TeamResolver(), ProportionalDevig(),
                    stale_seconds=180, now=NOW)
    away = b["games"][0]["markets"]["SPREAD"]["AWAY"]
    assert away["kalshi"]["line"] == -3.5 and not away["kalshi"]["alt"]
    assert away["fanduel"]["best"] and not away["kalshi"]["best"]  # 1.952 beats 1.9 on the same line


def test_board_flags_alt_line_when_exchange_lacks_main_line():
    s = Scanner([])
    s.ingest(fd_event()); s.ingest(k_event())
    s.ingest(quote("fanduel", "35605982", 1.95, side=Side.AWAY, line=-3.0, mtype=MarketType.SPREAD, mid="fd"))
    for line, dec in [(-2.5, 1.8), (-3.5, 2.05), (-6.5, 2.6)]:
        s.ingest(quote("kalshi", "26OCT04INDWAS", dec, team="IND", line=line, mtype=MarketType.SPREAD, mid=f"k{line}"))
    b = build_board(s.store.all(), list(s.normalizer.events.values()), [], TeamResolver(), ProportionalDevig(),
                    stale_seconds=180, now=NOW)
    k = b["games"][0]["markets"]["SPREAD"]["AWAY"]["kalshi"]
    assert k["alt"] and k["line"] == -2.5  # nearest to -3.0 (tie with -3.5 -> smaller |line|)


def test_alt_line_is_the_same_on_both_sides_of_a_prop():
    s = Scanner([])
    s.ingest(fd_event()); s.ingest(k_event())
    def prop(src, sid, side, line, dec, mid):
        return quote(src, sid, dec, side=side, line=line, mtype=MarketType.PLAYER_RUSHING_YARDS, mid=mid) \
            .model_copy(update={"subject": "Daniel Jones"})
    s.ingest(prop("fanduel", "35605982", Side.OVER, 11.5, 1.88, "fd"))
    s.ingest(prop("fanduel", "35605982", Side.UNDER, 11.5, 1.88, "fd"))
    for line, o, u in [(9.5, 1.7, 2.1), (14.5, 2.24, 1.6), (19.5, 3.0, 1.3)]:
        s.ingest(prop("kalshi", "26OCT04INDWAS", Side.OVER, line, o, f"k{line}"))
        s.ingest(prop("kalshi", "26OCT04INDWAS", Side.UNDER, line, u, f"k{line}"))
    b = build_board(s.store.all(), list(s.normalizer.events.values()), [], TeamResolver(), ProportionalDevig(),
                    stale_seconds=180, now=NOW)
    sides = b["games"][0]["props"][0]["sides"]
    assert sides["OVER"]["kalshi"]["line"] == sides["UNDER"]["kalshi"]["line"] == 9.5  # nearest to 11.5
