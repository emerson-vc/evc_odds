"""Polymarket US adapter: NFL game lines from Polymarket US's official public API.

Status: OFFICIAL, documented. "Public endpoints like market data and events don't need [an API key]."
  https://docs.polymarket.us/api-reference/authentication   base: https://gateway.polymarket.us
Endpoint: GET /v2/leagues/nfl/events?limit=100&type=sport  -> every NFL game with all its markets and the
best bid/ask inline (~35 MB raw, ~1 MB gzipped), so one request per cycle covers everything.

This is Polymarket **US** (CFTC-regulated, open to PA residents) — prices you can actually trade. The
international polymarket.com is US-geoblocked and is not used.

Markets used (sportsMarketType; wording from each market's description, seen 2026-10-02):
  football_team_full_game_winner  one contract: long = team A, short = team B. Overtime included.
                                  "If the game ends in a tie, the market will settle to $0.50" (books refund;
                                  ~0.3% of NFL games; treated as the same bet, recorded in metadata).
  football_team_full_game_spread  long = "<team> covers a <+/-X> point spread", short = other team at -line.
                                  (The `title` field reads like the short side; the description and the
                                  side labels agree with each other, so those are used.)
  football_team_full_game_total   GAME total despite the name: "combine for over X". Over = long.
  Skipped: football_team_points_full_game_total (TEAM totals), halves/quarters. No player props are listed.

Prices: each side has `quote` = the price to BUY that side now (long = best ask; short = 1 - best bid; checked
against bestBid/bestAsk). Plus the documented taker fee  theta*p*(1-p), theta = the market's feeCoefficient
(0.0695; https://docs.polymarket.us/fees). Order-book depth isn't in this payload, so liquidity is unknown.

Polymarket US doesn't mark home/away, so events are home_away_known=False and attach to another source's game
by date + teams; ML/spread quotes carry `team`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from src.adapters.base import AdapterHealth, RawEvent, RawQuote
from src.models.market import MarketType, Period, Side
from src.pricing.fees import effective_decimal
from src.pricing.odds import decimal_to_american

log = logging.getLogger(__name__)

SOURCE = "polymarket"
USER_AGENT = "evc-odds/0.1 (personal, read-only odds scanner)"
WINNER, SPREAD, TOTAL = "football_team_full_game_winner", "football_team_full_game_spread", "football_team_full_game_total"
_COVERS = re.compile(r"covers a ([+-]?\d+(?:\.\d+)?) point spread")


def _f(x) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _amount(q) -> float | None:
    return _f((q or {}).get("value")) if isinstance(q, dict) else None


def _team(side: dict) -> str | None:
    t = side.get("team") or {}
    return t.get("abbreviation", "").upper() or None


def _buy_price(m: dict, side: dict) -> float | None:
    """Price to buy this side now: the side's own quote, else derived from the market's best bid/ask."""
    p = _amount(side.get("quote"))
    if p is None:
        bid, ask = _amount(m.get("bestBidQuote")), _amount(m.get("bestAskQuote"))
        p = ask if side.get("long") else (1 - bid if bid is not None else None)
    return p if p is not None and 0 < p < 1 else None


def parse_events(data: dict, received_at: datetime, default_theta: float = 0.0695
                 ) -> tuple[list[RawEvent], list[RawQuote]]:
    events, quotes = [], []
    for e in data.get("events", []):
        if not e.get("active") or e.get("closed") or e.get("archived") or e.get("period") != "NS":
            continue  # pregame only ("NS" = not started)
        teams = [t.get("abbreviation", "").upper() for t in e.get("teams", [])]
        if len(teams) != 2 or not all(teams):
            continue
        key = e["slug"]
        events.append(RawEvent(
            source=SOURCE, source_event_id=key, sport="american_football", league="NFL",
            away_name=teams[0], home_name=teams[1],
            start_time_utc=datetime.fromisoformat((e.get("startTime") or e["startDate"]).replace("Z", "+00:00")),
            in_play=False, home_away_known=False, metadata={"title": e.get("title")},
        ))

        for m in e.get("markets", []):
            mtype_raw = m.get("sportsMarketType")
            if mtype_raw not in (WINNER, SPREAD, TOTAL):
                continue
            sides = m.get("marketSides") or []
            if len(sides) != 2 or {bool(s.get("long")) for s in sides} != {True, False}:
                continue
            long_side = next(s for s in sides if s.get("long"))
            short_side = next(s for s in sides if not s.get("long"))
            desc = m.get("description") or ""
            is_open = m.get("status") == "MARKET_STATUS_OPEN" and m.get("active") and not m.get("closed")
            theta = _f(m.get("feeCoefficient")) or default_theta

            if mtype_raw == WINNER:
                legs = [(s, MarketType.MONEYLINE, None, _team(s), None) for s in (long_side, short_side)]
                if {leg[3] for leg in legs} != set(teams):
                    continue
            elif mtype_raw == SPREAD:
                long_line, short_line = _f(long_side.get("description")), _f(short_side.get("description"))
                said = _COVERS.search(desc)
                lt, st = _team(long_side), _team(short_side)
                if (long_line is None or short_line != -long_line or not said or float(said.group(1)) != long_line
                        or lt not in teams or st not in teams or lt == st):
                    log.warning("polymarket: spread %s doesn't read consistently; skipped", m.get("id"))
                    continue
                legs = [(long_side, MarketType.SPREAD, None, lt, long_line),
                        (short_side, MarketType.SPREAD, None, st, short_line)]
            else:  # game total
                line = _f(m.get("line"))
                if line is None or "combine for over" not in desc:
                    continue
                legs = [(long_side, MarketType.TOTAL, Side.OVER, None, line),
                        (short_side, MarketType.TOTAL, Side.UNDER, None, line)]

            for side_obj, mtype, side, team, line in legs:
                price = _buy_price(m, side_obj)
                if price is None:
                    continue
                dec = effective_decimal(price, theta)
                quotes.append(RawQuote(
                    source=SOURCE, source_event_id=key, source_market_id=str(m["id"]),
                    source_selection_id=str(side_obj.get("id")), market_type=mtype, period=Period.FULL_GAME,
                    side=side, team=team, line=line, overtime_included=True,
                    decimal_odds=dec, american_odds=decimal_to_american(dec), is_open=bool(is_open),
                    timestamp_source=None, timestamp_received=received_at, liquidity=None,
                    metadata={"price": price, "fee_coefficient": theta, "note": f"buy {price * 100:.1f}¢ + fee",
                              **({"tie_rule": "half_payout"} if mtype == MarketType.MONEYLINE else {})},
                ))
    return events, quotes


class SourceBlocked(Exception):
    pass


class PolymarketAdapter:
    name = SOURCE

    def __init__(self, cfg: dict, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.client = client or httpx.AsyncClient(
            base_url=cfg["base_url"], timeout=cfg.get("timeout_seconds", 60),
            headers={"user-agent": USER_AGENT, "accept": "application/json"},
        )
        self.health = AdapterHealth.STARTING
        self.error_count = 0
        self.last_success: float | None = None
        self.request_count = 0

    async def _get(self, path: str, params: dict) -> dict:
        self.request_count += 1
        resp = await self.client.get(path, params=params)
        if resp.status_code in (401, 403, 429):
            raise SourceBlocked(f"{resp.status_code} from {path}")
        resp.raise_for_status()
        self.last_success = time.time()
        return resp.json()

    async def fetch_all(self) -> dict:
        limit, offset, events = 100, 0, []
        for _ in range(5):  # hard stop on pagination
            data = await self._get(f"/v2/leagues/{self.cfg.get('league', 'nfl')}/events",
                                   {"limit": limit, "offset": offset, "type": "sport"})
            page = data.get("events", [])
            events += page
            if len(page) < limit:
                break
            offset += limit
            await asyncio.sleep(2)
        return {"events": events}

    async def discover_events(self) -> list[RawEvent]:
        return parse_events(await self.fetch_all(), datetime.now(timezone.utc))[0]

    async def fetch_snapshot(self, event: RawEvent) -> list[RawQuote]:
        raise NotImplementedError("Polymarket US is fetched for all games at once; use stream_updates()")

    async def stream_updates(self) -> AsyncIterator[RawEvent | RawQuote]:
        poll = self.cfg["poll_seconds"]
        backoff = poll
        while True:
            try:
                events, quotes = parse_events(await self.fetch_all(), datetime.now(timezone.utc),
                                              self.cfg.get("default_fee_coefficient", 0.0695))
                self.health, self.error_count, backoff = AdapterHealth.LIVE, 0, poll
                for ev in events:
                    yield ev
                for q in quotes:
                    yield q
                await asyncio.sleep(poll)
            except SourceBlocked as e:
                self.health, self.error_count = AdapterHealth.BLOCKED, self.error_count + 1
                log.error("polymarket: refused (%s). Backing off 5 min.", e)
                await asyncio.sleep(300)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                self.health, self.error_count = AdapterHealth.DEGRADED, self.error_count + 1
                backoff = min(backoff * 2, 600)
                log.warning("polymarket: %s: %s (retry in %ss)", type(e).__name__, e, backoff)
                await asyncio.sleep(backoff)

    async def aclose(self) -> None:
        await self.client.aclose()
