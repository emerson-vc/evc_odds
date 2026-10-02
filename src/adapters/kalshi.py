"""Kalshi adapter: NFL game lines + player props from Kalshi's official public API.

Status: OFFICIAL, documented. Market data needs no account or key:
  "No authentication headers are required" — https://docs.kalshi.com/getting_started/quick_start_market_data
Endpoint used: GET {base}/events?series_ticker=..&status=open&with_nested_markets=true  (cursor-paginated)

How Kalshi contracts map to our markets (from each market's settlement rules text, seen 2026-10-01):
  KXNFLGAME    "<Team> wins"                      YES = that team's moneyline.
               Rule: "If the game ends in a tie, the market will resolve to $0.50 for each team."
               Sportsbooks refund ties instead. NFL ties are ~0.3% of games, so the price difference is
               ~0.1 percentage points; we treat it as the same bet and record the rule in metadata.
  KXNFLSPREAD  "<Team> wins by over X points"     YES = Team -X, NO = Opponent +X   (X always .5: no pushes)
  KXNFLTOTAL   "over X points scored"             YES = Over X,  NO = Under X
  KXNFL<STAT>  "<Player>: N+ <stat>"              YES = Over N-0.5, NO = Under N-0.5 (floor_strike = N-0.5)
               Rule: if the player is active but never plays a snap, settles at the pre-game fair price
               (sportsbooks void). Treated as equivalent; recorded in metadata.
  Anytime TD (KXNFLANYTD) had no open markets on 2026-10-01, so it isn't mapped yet (format unseen).

Kalshi doesn't say which team is home, so events are emitted with home_away_known=False and the
normalizer attaches them to the game another source listed (same date + teams). ML/spread quotes carry
`team` and the normalizer turns it into HOME/AWAY.

Prices: an order book, not a sportsbook line. We use the ASK (what you can buy at now) for each side,
plus Kalshi's taker fee, so decimal odds are what you'd actually get:
    cost per $1 contract = ask + fee_coefficient * ask * (1 - ask)       decimal = 1 / cost
fee_coefficient 0.07 per Kalshi's fee schedule as reported by multiple sources (kalshi.com/fee-schedule
rate-limited our fetch; TODO verify on the page). The real fee is rounded up to the cent per order, so
small orders pay slightly more than this.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import httpx

from src.adapters.base import AdapterHealth, RawEvent, RawQuote
from src.models.market import MarketType, Period, Side
from src.pricing.fees import effective_decimal as fee_adjusted
from src.pricing.odds import decimal_to_american

log = logging.getLogger(__name__)

SOURCE = "kalshi"
USER_AGENT = "evc-odds/0.1 (personal, read-only odds scanner)"

GAME_SERIES = "KXNFLGAME"
SERIES: dict[str, MarketType] = {  # GAME must come first: it defines teams for the others
    GAME_SERIES: MarketType.MONEYLINE,
    "KXNFLSPREAD": MarketType.SPREAD,
    "KXNFLTOTAL": MarketType.TOTAL,
    "KXNFLPASSYDS": MarketType.PLAYER_PASSING_YARDS,
    "KXNFLPASSTDS": MarketType.PLAYER_PASSING_TDS,
    "KXNFLRSHYDS": MarketType.PLAYER_RUSHING_YARDS,
    "KXNFLRECYDS": MarketType.PLAYER_RECEIVING_YARDS,
    "KXNFLREC": MarketType.PLAYER_RECEPTIONS,
    "KXNFLRRYDS": MarketType.PLAYER_RUSH_REC_YARDS,
}
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                       "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_GAME_KEY = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$")  # 26OCT04INDWAS


def game_key(event_ticker: str) -> str:
    """'KXNFLSPREAD-26OCT04INDWAS' -> '26OCT04INDWAS' (shared by every series for one game)."""
    return event_ticker.split("-", 1)[1]


def game_date_noon_utc(key: str) -> datetime | None:
    """Kalshi's ticker date, as noon US/Eastern (16:00 UTC) so its Eastern calendar date is preserved."""
    m = _GAME_KEY.match(key)
    if not m or m.group(2) not in _MONTHS:
        return None
    return datetime(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)), 16, tzinfo=timezone.utc)


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def effective_decimal(ask: float, fee_coefficient: float) -> float:
    return fee_adjusted(ask, fee_coefficient)


class Pricer:
    def __init__(self, fee_coefficient: float, min_size: float):
        self.fee, self.min_size = fee_coefficient, min_size

    def side(self, market: dict, yes: bool) -> tuple[float, float] | None:
        """(ask, size) to buy YES or NO on this market, or None if nothing tradable."""
        if yes:
            ask, size = _f(market.get("yes_ask_dollars")), _f(market.get("yes_ask_size_fp"))
        else:  # buying NO at no_ask is matched against YES bids, so depth = yes_bid size
            ask, size = _f(market.get("no_ask_dollars")), _f(market.get("yes_bid_size_fp"))
        if ask is None or not 0 < ask < 1 or (size or 0) < self.min_size:
            return None
        return ask, size or 0.0

    def quote(self, *, key: str, market_id: str, mtype: MarketType, side: Side | None, team: str | None,
              subject: str | None, line: float | None, ask: float, size: float, is_open: bool,
              received_at: datetime, metadata: dict) -> RawQuote:
        dec = effective_decimal(ask, self.fee)
        return RawQuote(
            source=SOURCE, source_event_id=key, source_market_id=market_id, market_type=mtype,
            subject=subject, period=Period.FULL_GAME, side=side, team=team, line=line,
            overtime_included=True,  # rules settle on the official game result / recorded stats
            decimal_odds=dec, american_odds=decimal_to_american(dec), is_open=is_open,
            timestamp_source=None, timestamp_received=received_at, liquidity=size,
            metadata={"ask": ask, "fee_coefficient": self.fee, "note": f"ask {ask * 100:.0f}¢ + fee", **metadata},
        )


# ---------------------------------------------------------------- pure parsers (tested against fixtures)


def parse_games(events: list[dict], pricer: Pricer, received_at: datetime
                ) -> tuple[list[RawEvent], dict[str, str], dict[str, tuple[str, str]], list[RawQuote]]:
    """KXNFLGAME -> (events, team uuid -> code, game key -> (code1, code2), moneyline quotes)."""
    raw_events, uuid_to_code, teams_by_game, quotes = [], {}, {}, []
    for e in events:
        key = game_key(e["event_ticker"])
        start = game_date_noon_utc(key)
        markets = e.get("markets", [])
        codes = [m["ticker"].rsplit("-", 1)[1] for m in markets]
        if start is None or len(markets) != 2 or len(set(codes)) != 2:
            log.warning("kalshi: unexpected game event %s", e.get("event_ticker"))
            continue
        for m, code in zip(markets, codes):
            if uid := (m.get("custom_strike") or {}).get("football_team"):
                uuid_to_code[uid] = code
        teams_by_game[key] = (codes[0], codes[1])
        raw_events.append(RawEvent(
            source=SOURCE, source_event_id=key, sport="american_football", league="NFL",
            away_name=codes[0], home_name=codes[1], start_time_utc=start, in_play=False,
            home_away_known=False, metadata={"title": e.get("title")},
        ))
        # Each team's moneyline is buyable two ways: YES on "<team> wins" or NO on "<opponent> wins".
        for i, code in enumerate(codes):
            own, opp = markets[i], markets[1 - i]
            options = [o for o in (pricer.side(own, True), pricer.side(opp, False)) if o]
            if not options:
                continue
            ask, size = min(options)
            quotes.append(pricer.quote(
                key=key, market_id=f"{key}:ML", mtype=MarketType.MONEYLINE, side=None, team=code,
                subject=None, line=None, ask=ask, size=size,
                is_open=own.get("status") == "active" and opp.get("status") == "active",
                received_at=received_at, metadata={"tie_rule": "half_payout"},
            ))
    return raw_events, uuid_to_code, teams_by_game, quotes


def parse_ladder(series: str, events: list[dict], pricer: Pricer, received_at: datetime,
                 uuid_to_code: dict[str, str], teams_by_game: dict[str, tuple[str, str]]) -> list[RawQuote]:
    """Spread / total / player-stat ladders: each strike is a YES/NO pair."""
    mtype = SERIES[series]
    quotes = []
    for e in events:
        key = game_key(e["event_ticker"])
        for m in e.get("markets", []):
            strike = _f(m.get("floor_strike"))
            if m.get("strike_type") != "greater" or strike is None:
                log.debug("kalshi: skipping non-ladder market %s", m.get("ticker"))
                continue
            is_open = m.get("status") == "active"
            for yes in (True, False):
                priced = pricer.side(m, yes)
                if priced is None:
                    continue
                ask, size = priced
                common = dict(key=key, market_id=m["ticker"], mtype=mtype, ask=ask, size=size,
                              is_open=is_open, received_at=received_at, metadata={})
                if mtype == MarketType.SPREAD:
                    team = uuid_to_code.get((m.get("custom_strike") or {}).get("football_team"))
                    pair = teams_by_game.get(key)
                    if team is None or pair is None or team not in pair:
                        continue  # can't tell whose spread this is: skip rather than guess
                    other = pair[1] if team == pair[0] else pair[0]
                    quotes.append(pricer.quote(**common, side=None, team=team if yes else other, subject=None,
                                               line=-strike if yes else strike))
                elif mtype == MarketType.TOTAL:
                    quotes.append(pricer.quote(**common, side=Side.OVER if yes else Side.UNDER, team=None,
                                               subject=None, line=strike))
                else:  # player stat: "<Player>: N+ <stat>"
                    player = (m.get("title") or "").split(":")[0].strip()
                    if not player or "+" not in (m.get("title") or ""):
                        continue
                    quotes.append(pricer.quote(**common, side=Side.OVER if yes else Side.UNDER, team=None,
                                               subject=player, line=strike))
    return quotes


# ---------------------------------------------------------------- network


class SourceBlocked(Exception):
    pass


class KalshiAdapter:
    name = SOURCE

    def __init__(self, cfg: dict, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.client = client or httpx.AsyncClient(
            base_url=cfg["base_url"], timeout=cfg.get("timeout_seconds", 20),
            headers={"user-agent": USER_AGENT, "accept": "application/json"},
        )
        self.pricer = Pricer(cfg.get("taker_fee_coefficient", 0.07), cfg.get("min_ask_contracts", 1))
        self.health = AdapterHealth.STARTING
        self.error_count = 0
        self.last_success: float | None = None
        self.spacing = float(cfg.get("request_spacing_seconds", 1.0))
        self._last_request_at = 0.0
        self.request_count = 0
        self.uuid_to_code: dict[str, str] = {}
        self.teams_by_game: dict[str, tuple[str, str]] = {}

    async def _get(self, path: str, params: dict) -> dict:
        wait = self._last_request_at + self.spacing - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = time.monotonic()
        self.request_count += 1
        resp = await self.client.get(path, params=params)
        if resp.status_code in (401, 403, 429):
            raise SourceBlocked(f"{resp.status_code} from {path}")
        resp.raise_for_status()
        self.last_success = time.time()
        return resp.json()

    async def fetch_series(self, series: str) -> list[dict]:
        events, cursor = [], None
        for _ in range(10):  # hard stop on pagination
            params = {"series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = await self._get("/events", params)
            events += data.get("events", [])
            cursor = data.get("cursor")
            if not cursor:
                break
        return events

    async def discover_events(self) -> list[RawEvent]:
        evs, self.uuid_to_code, self.teams_by_game, _ = parse_games(
            await self.fetch_series(GAME_SERIES), self.pricer, datetime.now(timezone.utc))
        return evs

    async def fetch_snapshot(self, event: RawEvent) -> list[RawQuote]:
        raise NotImplementedError("Kalshi is fetched per series for all games at once; use stream_updates()")

    async def poll_once(self) -> AsyncIterator[RawEvent | RawQuote]:
        now = datetime.now(timezone.utc)
        evs, uuid_map, teams, ml = parse_games(await self.fetch_series(GAME_SERIES), self.pricer, now)
        self.uuid_to_code, self.teams_by_game = uuid_map, teams
        for ev in evs:
            yield ev
        for q in ml:
            yield q
        for series in list(SERIES)[1:]:
            events = await self.fetch_series(series)
            for q in parse_ladder(series, events, self.pricer, datetime.now(timezone.utc), uuid_map, teams):
                yield q

    async def stream_updates(self) -> AsyncIterator[RawEvent | RawQuote]:
        poll = self.cfg["poll_seconds"]
        backoff = poll
        while True:
            try:
                async for item in self.poll_once():
                    yield item
                self.health, self.error_count, backoff = AdapterHealth.LIVE, 0, poll
                await asyncio.sleep(poll)
            except SourceBlocked as e:
                self.health, self.error_count = AdapterHealth.BLOCKED, self.error_count + 1
                log.error("kalshi: refused (%s). Backing off 5 min.", e)
                await asyncio.sleep(300)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                self.health, self.error_count = AdapterHealth.DEGRADED, self.error_count + 1
                backoff = min(backoff * 2, 300)
                log.warning("kalshi: %s: %s (retry in %ss)", type(e).__name__, e, backoff)
                await asyncio.sleep(backoff)

    async def aclose(self) -> None:
        await self.client.aclose()
