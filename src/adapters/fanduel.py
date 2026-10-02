"""FanDuel Sportsbook adapter (PA), pregame NFL: game lines + player props.

Status: works, UNDOCUMENTED. Built from a browser capture of sportsbook.fanduel.com on 2026-10-01
(see docs/source_status.md). FanDuel publishes no API; these are the JSON endpoints its own website
calls. They can change or start refusing us at any time.

Access rules we follow (CLAUDE.md §5):
  * Only data a logged-out visitor's browser receives. No cookies, no login.
  * No anti-bot tokens. The site sends a PerimeterX `x-px-context` header; we never copy or generate
    one. A live test on 2026-10-01 showed these endpoints answer without it. If that changes (403/429),
    the adapter marks itself BLOCKED and backs off. It does not work around it.
  * Gentle polling (config `poll_seconds` / `props_poll_seconds`; the site itself polls ~5s).

Flow observed in the capture:
  1. event_search  POST {competitionIds:[NFL]}             -> list of games (+ which are in play)
  2. event_page    GET  ?eventId=..&tab=same-game-parlay-   -> markets for one game: game lines, player O/U
                                                              props, anytime TD; runner names and tags
  3. market_prices POST {"marketIds": [...]}               -> current prices + current line for those markets

Market shapes handled (all observed in the capture):
  * Game lines (MONEY_LINE, MATCH_HANDICAP_(2-WAY), TOTAL_POINTS_(OVER/UNDER)): 2 runners tagged
    result.type HOME/AWAY or OVER/UNDER.
  * Player O/U props (marketType PLAYER_X_<STAT>[_LOW|_MEDIUM|_HIGH], marketName "<Player> - <Stat>"):
    2 runners "<Player> Over"/"<Player> Under" tagged OVER/UNDER, handicap = line. The _LOW/_MEDIUM/_HIGH
    suffix distinguishes players, not line types (each player+stat appears once per game).
  * ANY_TIME_TOUCHDOWN_SCORER: one runner per player, untagged, yes-only.
Alternate ladders ("X+ Yards") and other specials are ignored for now.

Known coverage gap: the `same-game-parlay-` tab is the only tab whose request we have observed. FanDuel
also lists Passing/Receiving/Rushing/TD Scorer Props tabs that may include more players. Their request
parameter is unknown; TODO: capture a click on each tab before adding them.

Settlement assumption (NOT verified from FanDuel's house rules yet): NFL full-game lines and player
stats include overtime. TODO: confirm in FanDuel's published rules before trusting cross-book matches.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from src.adapters.base import AdapterHealth, RawEvent, RawQuote
from src.models.market import MarketType, Period, Side

log = logging.getLogger(__name__)

SOURCE = "fanduel"
USER_AGENT = "evc-odds/0.1 (personal, read-only odds scanner)"
OVERTIME_INCLUDED = True  # Assumption, see module docstring.

GAME_LINE_TYPES = {
    "MONEY_LINE": MarketType.MONEYLINE,
    "MATCH_HANDICAP_(2-WAY)": MarketType.SPREAD,
    "TOTAL_POINTS_(OVER/UNDER)": MarketType.TOTAL,
}
PROP_OU_TYPES = {  # after stripping the _LOW/_MEDIUM/_HIGH suffix
    "PLAYER_X_PASSING_YARDS": MarketType.PLAYER_PASSING_YARDS,
    "PLAYER_X_PASSING_TOUCHDOWNS": MarketType.PLAYER_PASSING_TDS,
    "PLAYER_X_RUSHING_YARDS": MarketType.PLAYER_RUSHING_YARDS,
    "PLAYER_X_RECEIVING_YARDS": MarketType.PLAYER_RECEIVING_YARDS,
    "PLAYER_X_RECEPTIONS": MarketType.PLAYER_RECEPTIONS,
    "PLAYER_X_PASSING_+_RUSHING_YARDS": MarketType.PLAYER_PASS_RUSH_YARDS,
    "PLAYER_X_RUSHING_+_RECEIVING_YARDS": MarketType.PLAYER_RUSH_REC_YARDS,
}
YES_ONLY_TYPES = {"ANY_TIME_TOUCHDOWN_SCORER": MarketType.PLAYER_ANYTIME_TD}
TAGS = {"HOME": Side.HOME, "AWAY": Side.AWAY, "OVER": Side.OVER, "UNDER": Side.UNDER}
_TIER = re.compile(r"_(LOW|MEDIUM|HIGH)$")

PRICE_BATCH = 70  # The site requested ~70 market ids per call.


@dataclass
class MarketMeta:
    source_event_id: str
    market_type: MarketType
    selections: dict[str, tuple[Side, str | None]] = field(default_factory=dict)  # selectionId -> (side, player)


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _line(market_type: MarketType, handicap) -> float | None:
    if market_type in (MarketType.MONEYLINE, MarketType.PLAYER_ANYTIME_TD):
        return None
    return float(handicap)


def _odds(win_runner_odds: dict) -> tuple[float, int | None]:
    decimal = float(win_runner_odds["trueOdds"]["decimalOdds"]["decimalOdds"])
    american = win_runner_odds.get("americanDisplayOdds", {}).get("americanOddsInt")
    return decimal, american


def _classify(m: dict) -> MarketMeta | None:
    """Work out market type and each runner's (side, player) from an event-page market, or None to skip."""
    mtype_raw = m.get("marketType", "")
    sid = str(m.get("eventId"))
    runners = m.get("runners", [])

    if mtype_raw in YES_ONLY_TYPES:
        meta = MarketMeta(sid, YES_ONLY_TYPES[mtype_raw])
        for r in runners:
            meta.selections[str(r["selectionId"])] = (Side.YES, r["runnerName"].strip())
        return meta if meta.selections else None

    if mtype_raw in GAME_LINE_TYPES:
        mtype, player = GAME_LINE_TYPES[mtype_raw], None
    elif _TIER.sub("", mtype_raw) in PROP_OU_TYPES:
        mtype = PROP_OU_TYPES[_TIER.sub("", mtype_raw)]
        player = m.get("marketName", "").split(" - ")[0].strip()
        # Refuse anything that doesn't look exactly like the observed shape (CLAUDE.md §10: don't guess).
        if not player or not all(r.get("runnerName", "").startswith(player) for r in runners):
            log.warning("fanduel: unexpected prop shape %s %r", m.get("marketId"), m.get("marketName"))
            return None
    else:
        return None

    meta = MarketMeta(sid, mtype)
    for r in runners:
        side = TAGS.get((r.get("result") or {}).get("type"))
        if side is None:
            log.warning("fanduel: untagged runner in %s %s: %r", m.get("marketId"), mtype_raw, r.get("runnerName"))
            return None
        meta.selections[str(r["selectionId"])] = (side, player)
    if len(meta.selections) != 2 or len({s for s, _ in meta.selections.values()}) != 2:
        log.warning("fanduel: market %s is not a clean two-way market, skipping", m.get("marketId"))
        return None
    return meta


def _quote(meta: MarketMeta, market_id: str, sel_id: str, odds: dict, handicap, is_open: bool,
           received_at: datetime, metadata: dict | None = None) -> RawQuote:
    side, player = meta.selections[sel_id]
    decimal, american = _odds(odds)
    return RawQuote(
        source=SOURCE,
        source_event_id=meta.source_event_id,
        source_market_id=market_id,
        source_selection_id=sel_id,
        market_type=meta.market_type,
        subject=player,
        period=Period.FULL_GAME,
        side=side,
        line=_line(meta.market_type, handicap),
        overtime_included=OVERTIME_INCLUDED,
        decimal_odds=decimal,
        american_odds=american,
        is_open=is_open,
        timestamp_source=None,
        timestamp_received=received_at,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------- pure parsers (tested against fixtures)


def parse_event_search(data: dict) -> list[RawEvent]:
    in_play: dict[str, bool] = {}
    for facet in data.get("facets", []):
        if facet.get("type") != "EVENT":
            continue
        for v in facet.get("values", []):
            nxt = (v.get("next") or {}).get("values") or [{}]
            in_play[str(v["key"]["eventId"])] = nxt[0].get("value") == "true"

    events = []
    for ev in data.get("attachments", {}).get("events", {}).values():
        name = ev.get("name", "")
        teams = name.split(" @ ")
        if len(teams) != 2:  # Futures/specials aren't games.
            log.debug("fanduel: skipping non-game event %r", name)
            continue
        eid = str(ev["eventId"])
        events.append(
            RawEvent(
                source=SOURCE,
                source_event_id=eid,
                sport="american_football",
                league="NFL",
                away_name=teams[0].strip(),
                home_name=teams[1].strip(),
                start_time_utc=_parse_time(ev["openDate"]),
                in_play=in_play.get(eid, False),
                metadata={"name": name, "competition_id": ev.get("competitionId")},
            )
        )
    return events


def parse_event_page(
    data: dict, source_event_id: str, received_at: datetime
) -> tuple[dict[str, MarketMeta], list[RawQuote]]:
    metas: dict[str, MarketMeta] = {}
    quotes: list[RawQuote] = []
    for m in data.get("attachments", {}).get("markets", {}).values():
        if str(m.get("eventId")) != source_event_id:
            continue
        meta = _classify(m)
        if meta is None:
            continue
        mid = m["marketId"]
        metas[mid] = meta
        market_open = m.get("marketStatus") == "OPEN" and not m.get("inPlay")
        for r in m.get("runners", []):
            quotes.append(_quote(
                meta, mid, str(r["selectionId"]), r["winRunnerOdds"], r.get("handicap", 0),
                market_open and r.get("runnerStatus") == "ACTIVE", received_at,
                {"runner_name": r.get("runnerName"), "market_name": m.get("marketName")},
            ))
    return metas, quotes


def parse_market_prices(data: list, metas: dict[str, MarketMeta], received_at: datetime) -> list[RawQuote]:
    quotes = []
    for m in data:
        meta = metas.get(m.get("marketId"))
        if meta is None:
            continue
        market_open = m.get("marketStatus") == "OPEN" and not m.get("inplay")
        for rd in m.get("runnerDetails", []):
            sel = str(rd.get("selectionId"))
            if sel not in meta.selections:
                continue
            # Lines move; always take the line from the price update itself.
            quotes.append(_quote(
                meta, m["marketId"], sel, rd["winRunnerOdds"], rd.get("handicap", 0),
                market_open and rd.get("runnerStatus") == "ACTIVE", received_at,
            ))
    return quotes


# ---------------------------------------------------------------- network


class SourceBlocked(Exception):
    pass


class FanDuelAdapter:
    name = SOURCE

    def __init__(self, cfg: dict, app_key: str, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.region = cfg["region"]
        self.app_key = app_key
        self.endpoints = {k: v.format(region=self.region) for k, v in cfg["endpoints"].items()}
        self.client = client or httpx.AsyncClient(
            timeout=cfg.get("timeout_seconds", 15),
            headers={"user-agent": USER_AGENT, "accept": "application/json"},
        )
        self.health = AdapterHealth.STARTING
        self.error_count = 0
        self.last_success: float | None = None
        self.metas: dict[str, MarketMeta] = {}
        self.spacing = float(cfg.get("request_spacing_seconds", 1.0))
        self._last_request_at = 0.0
        self.request_count = 0

    async def _request(self, method: str, url: str, **kw) -> object:
        # Never send requests back-to-back: wait until `spacing` seconds after the previous one.
        wait = self._last_request_at + self.spacing - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = time.monotonic()
        self.request_count += 1
        resp = await self.client.request(method, url, **kw)
        if resp.status_code in (401, 403, 429):
            raise SourceBlocked(f"{resp.status_code} from {url.split('?')[0]}")
        resp.raise_for_status()
        self.last_success = time.time()
        return resp.json()

    async def discover_events(self) -> list[RawEvent]:
        body = {  # Same request body the site sent (observed).
            "filter": {
                "competitionIds": [self.cfg["nfl_competition_id"]],
                "contentGroup": {"language": "en", "regionCode": "NAMERICA"},
                "marketLevels": ["AVB_EVENT"],
                "maxResults": 0,
                "productTypes": ["SPORTSBOOK"],
                "selectBy": "FIRST_TO_START",
            },
            "facets": [{"type": "COMPETITION"}, {"type": "EVENT", "next": {"type": "IN_PLAY"}}],
            "currencyCode": "USD",
        }
        data = await self._request(
            "POST", self.endpoints["event_search"], json=body, headers={"x-application": self.app_key}
        )
        return parse_event_search(data)

    async def fetch_snapshot(self, event: RawEvent) -> list[RawQuote]:
        params = {
            "_ak": self.app_key,
            "eventId": event.source_event_id,
            "tab": self.cfg["event_page_tab"],
            "useCombinedTouchdownsVirtualMarket": "true",
            "useQuickBets": "true",
        }
        data = await self._request(
            "GET", self.endpoints["event_page"], params=params,
            headers={"x-sportsbook-region": self.region.upper()},
        )
        metas, quotes = parse_event_page(data, event.source_event_id, datetime.now(timezone.utc))
        self.metas.update(metas)
        return quotes

    async def fetch_prices(self, market_ids: list[str]) -> list[RawQuote]:
        quotes = []
        for i in range(0, len(market_ids), PRICE_BATCH):
            batch = market_ids[i : i + PRICE_BATCH]
            data = await self._request(
                "POST", self.endpoints["market_prices"], json={"marketIds": batch},
                headers={"x-application": self.app_key},
            )
            quotes += parse_market_prices(data, self.metas, datetime.now(timezone.utc))
        return quotes

    async def stream_updates(self) -> AsyncIterator[RawEvent | RawQuote]:
        """Poll forever: games every `discover_seconds`, game lines every `poll_seconds`,
        props every `props_poll_seconds` (props are ~30x more markets, so they're refreshed less often)."""
        poll = self.cfg["poll_seconds"]
        props_every = self.cfg.get("props_poll_seconds", poll)
        discover_every = self.cfg["discover_seconds"]
        next_discover = next_props = 0.0
        backoff = poll
        while True:
            try:
                now = time.monotonic()
                if now >= next_discover:
                    events = [e for e in await self.discover_events() if not e.in_play]
                    live_ids = {e.source_event_id for e in events}
                    self.metas = {k: v for k, v in self.metas.items() if v.source_event_id in live_ids}
                    for ev in events:
                        yield ev
                        for q in await self.fetch_snapshot(ev):
                            yield q
                    next_discover = time.monotonic() + discover_every
                    next_props = time.monotonic() + props_every
                else:
                    want_props = now >= next_props
                    ids = [mid for mid, m in self.metas.items() if want_props or not m.market_type.is_player_prop]
                    for q in await self.fetch_prices(ids):
                        yield q
                    if want_props:
                        next_props = now + props_every
                self.health, self.error_count, backoff = AdapterHealth.LIVE, 0, poll
                await asyncio.sleep(poll)
            except SourceBlocked as e:
                self.health, self.error_count = AdapterHealth.BLOCKED, self.error_count + 1
                log.error("fanduel: access refused (%s). Backing off 5 min; not working around it.", e)
                await asyncio.sleep(300)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                self.health, self.error_count = AdapterHealth.DEGRADED, self.error_count + 1
                backoff = min(backoff * 2, 300)
                log.warning("fanduel: %s: %s (retry in %ss)", type(e).__name__, e, backoff)
                await asyncio.sleep(backoff)

    async def aclose(self) -> None:
        await self.client.aclose()
