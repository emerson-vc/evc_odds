"""DraftKings Sportsbook adapter (PA), pregame NFL: game lines + player props.

Status: works, UNDOCUMENTED. Built from a browser capture of sportsbook.draftkings.com on 2026-10-01 plus a
3-request live test the same night (see docs/source_status.md). DraftKings publishes no API; these are the
JSON endpoints its own website calls and can change at any time.

Access (CLAUDE.md §5): logged out, no cookies, honest user-agent. The site sends no anti-bot token; the
`x-pe-*` / `x-client-name` headers are plain labels (observed values "web", "SB", "US-PA") and we send the
same labels. If DraftKings refuses (401/403/429) the adapter goes BLOCKED and backs off; no workaround.

Endpoints observed (site "US-PA-SB"):
  nav      GET .../sportscontent/navigation/dkuspa/v2/nav/leagues/88808   -> NFL games, home/away, status
  markets  GET .../sportscontent/controldata/event/eventSubcategory/v1/markets
           ?isBatchable=false&templateVars=<eventId>&entity=markets
           &marketsQuery=$filter=eventId eq '<id>' AND clientMetadata/subCategoryId eq '<sub>' AND tags/all(t: t ne 'SportcastBetBuilder')
  The subcategory ids below come from DraftKings' own page-layout response (captured). One request per
  game per subcategory, so this adapter polls slowly (see config).

Response shape: {markets: [...], selections: [...]}. Selections carry outcomeType (Away/Home/Over/Under/
ToScoreAnyTime), points (the line), trueOdds (decimal) and participants (team or player).

Assumptions (TODO verify):
  * Suspended markets: no suspension field appeared in the capture. We honor `isSuspended` if present.
  * NFL full-game lines and player stats include overtime (standard US rule; not yet read in DK house rules).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from src.adapters.base import AdapterHealth, RawEvent, RawQuote
from src.models.market import MarketType, Period, Side
from src.pricing.odds import decimal_to_american

log = logging.getLogger(__name__)

SOURCE = "draftkings"
USER_AGENT = "evc-odds/0.1 (personal, read-only odds scanner)"
OVERTIME_INCLUDED = True  # assumption, see docstring

GAME_LINES_SUB = "4518"
ATD_SUB = "12438"
PROP_SUBS: dict[str, MarketType] = {  # "<stat> O/U" subcategories (from the captured page layout)
    "9524": MarketType.PLAYER_PASSING_YARDS,
    "9525": MarketType.PLAYER_PASSING_TDS,
    "9514": MarketType.PLAYER_RUSHING_YARDS,
    "14114": MarketType.PLAYER_RECEIVING_YARDS,
    "14115": MarketType.PLAYER_RECEPTIONS,
    "9532": MarketType.PLAYER_PASS_RUSH_YARDS,
    "9523": MarketType.PLAYER_RUSH_REC_YARDS,
}
GAME_MARKETS = {"Moneyline": MarketType.MONEYLINE, "Spread": MarketType.SPREAD, "Total": MarketType.TOTAL}
OUTCOMES = {"Away": Side.AWAY, "Home": Side.HOME, "Over": Side.OVER, "Under": Side.UNDER}


def _parse_time(s: str) -> datetime:
    # DraftKings sends 7 fractional digits ("2026-10-04T13:30:00.0000000Z"); keep at most 6.
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def _player(sel: dict) -> str | None:
    players = [p for p in sel.get("participants") or [] if p.get("type") == "Player"]
    return players[0]["name"].strip() if len(players) == 1 else None


# ---------------------------------------------------------------- pure parsers (tested against fixtures)


def parse_nav(data: dict) -> list[RawEvent]:
    events = []
    for e in data.get("events", []):
        roles = {p.get("venueRole"): p for p in e.get("participants", [])}
        home, away = roles.get("Home"), roles.get("Away")
        if not home or not away:
            continue
        name = lambda p: (p.get("metadata") or {}).get("shortName") or p.get("name")  # noqa: E731
        events.append(RawEvent(
            source=SOURCE, source_event_id=str(e["id"]), sport="american_football", league="NFL",
            home_name=name(home), away_name=name(away), start_time_utc=_parse_time(e["startEventDate"]),
            in_play=e.get("status") != "NOT_STARTED", metadata={"name": e.get("name"), "status": e.get("status")},
        ))
    return events


def parse_markets(data: dict, sub: str, source_event_id: str, received_at: datetime) -> list[RawQuote]:
    markets = {m["id"]: m for m in data.get("markets", []) if str(m.get("eventId")) == source_event_id}
    by_market: dict[str, list[dict]] = {}
    for s in data.get("selections", []):
        if s.get("marketId") in markets:
            by_market.setdefault(s["marketId"], []).append(s)

    quotes = []
    for mid, sels in by_market.items():
        m = markets[mid]
        type_name = (m.get("marketType") or {}).get("name", "")
        if sub == GAME_LINES_SUB:
            mtype = GAME_MARKETS.get(type_name)
            if mtype is None:
                continue
            if any("MainPointLine" in (s.get("tags") or []) for s in sels):
                sels = [s for s in sels if "MainPointLine" in (s.get("tags") or [])]
        elif sub == ATD_SUB:
            if type_name != "Anytime Touchdown Scorer":
                continue
            mtype = MarketType.PLAYER_ANYTIME_TD
        elif sub in PROP_SUBS:
            if not type_name.endswith("O/U"):
                continue
            mtype = PROP_SUBS[sub]
            if len({_player(s) for s in sels}) != 1 or _player(sels[0]) is None:
                log.warning("draftkings: prop market %s doesn't have exactly one player, skipping", mid)
                continue
        else:
            continue

        for s in sels:
            if mtype == MarketType.PLAYER_ANYTIME_TD:
                side, player = Side.YES, _player(s)
                if s.get("outcomeType") != "ToScoreAnyTime" or player is None:
                    continue  # e.g. "IND Colts D/ST"
            else:
                side, player = OUTCOMES.get(s.get("outcomeType")), _player(s) if mtype.is_player_prop else None
                if side is None:
                    continue
            decimal = float(s["trueOdds"])
            if decimal <= 1:
                continue
            line = None if mtype in (MarketType.MONEYLINE, MarketType.PLAYER_ANYTIME_TD) else s.get("points")
            if mtype not in (MarketType.MONEYLINE, MarketType.PLAYER_ANYTIME_TD) and line is None:
                continue
            quotes.append(RawQuote(
                source=SOURCE, source_event_id=source_event_id, source_market_id=mid,
                source_selection_id=s.get("id"), market_type=mtype, subject=player, period=Period.FULL_GAME,
                side=side, line=None if line is None else float(line), overtime_included=OVERTIME_INCLUDED,
                decimal_odds=decimal, american_odds=decimal_to_american(decimal),
                is_open=not m.get("isSuspended") and not s.get("isSuspended"),
                timestamp_source=None, timestamp_received=received_at,
                metadata={"market_name": m.get("name")},
            ))
    return quotes


# ---------------------------------------------------------------- network


class SourceBlocked(Exception):
    pass


class DraftKingsAdapter:
    name = SOURCE

    def __init__(self, cfg: dict, client: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.base = cfg["base_url"].rstrip("/")
        self.client = client or httpx.AsyncClient(
            timeout=cfg.get("timeout_seconds", 20),
            headers={
                "user-agent": USER_AGENT, "accept": "application/json",
                # Plain descriptive labels the site sends (observed); not tokens.
                "x-client-name": "web", "x-pe-cn": "web", "x-pe-ep": "SB", "x-pe-loc": cfg.get("location", "US-PA"),
            },
        )
        self.health = AdapterHealth.STARTING
        self.error_count = 0
        self.last_success: float | None = None
        self.spacing = float(cfg.get("request_spacing_seconds", 3))
        self._last_request_at = 0.0
        self.request_count = 0
        self.events: list[RawEvent] = []
        self.no_props: set[tuple[str, str]] = set()  # (event, subcategory) that returned nothing

    async def _get(self, url: str) -> dict:
        wait = self._last_request_at + self.spacing - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = time.monotonic()
        self.request_count += 1
        resp = await self.client.get(url)
        if resp.status_code in (401, 403, 429):
            raise SourceBlocked(f"{resp.status_code} from {url.split('?')[0]}")
        resp.raise_for_status()
        self.last_success = time.time()
        if self.health in (AdapterHealth.STARTING, AdapterHealth.DEGRADED):
            self.health = AdapterHealth.LIVE  # a full pass takes minutes; show LIVE as soon as data flows
        return resp.json()

    def _markets_url(self, event_id: str, sub: str) -> str:
        q = (f"$filter=eventId eq '{event_id}' AND clientMetadata/subCategoryId eq '{sub}' "
             f"AND tags/all(t: t ne 'SportcastBetBuilder')")
        return (f"{self.base}/sportscontent/controldata/event/eventSubcategory/v1/markets"
                f"?isBatchable=false&templateVars={event_id}&marketsQuery={quote(q)}&entity=markets")

    async def discover_events(self) -> list[RawEvent]:
        data = await self._get(f"{self.base}/sportscontent/navigation/dkuspa/v2/nav/leagues/{self.cfg['nfl_league_id']}")
        return parse_nav(data)

    async def fetch_subcategory(self, event: RawEvent, sub: str) -> list[RawQuote]:
        data = await self._get(self._markets_url(event.source_event_id, sub))
        return parse_markets(data, sub, event.source_event_id, datetime.now(timezone.utc))

    async def fetch_snapshot(self, event: RawEvent) -> list[RawQuote]:
        return await self.fetch_subcategory(event, GAME_LINES_SUB)

    async def stream_updates(self) -> AsyncIterator[RawEvent | RawQuote]:
        """Game list every `discover_seconds`, game lines every `poll_seconds`, props every `props_poll_seconds`."""
        next_discover = next_lines = next_props = 0.0
        backoff = 30
        while True:
            try:
                now = time.monotonic()
                if now >= next_discover:
                    self.events = [e for e in await self.discover_events() if not e.in_play]
                    self.no_props.clear()
                    for ev in self.events:
                        yield ev
                    next_discover = time.monotonic() + self.cfg["discover_seconds"]
                if now >= next_lines:
                    for ev in self.events:
                        for q in await self.fetch_subcategory(ev, GAME_LINES_SUB):
                            yield q
                    next_lines = time.monotonic() + self.cfg["poll_seconds"]
                if now >= next_props:
                    for ev in self.events:
                        for sub in [ATD_SUB, *PROP_SUBS]:
                            if (ev.source_event_id, sub) in self.no_props:
                                continue
                            quotes = await self.fetch_subcategory(ev, sub)
                            if not quotes:
                                self.no_props.add((ev.source_event_id, sub))  # not posted yet; retry after rediscovery
                            for q in quotes:
                                yield q
                    next_props = time.monotonic() + self.cfg["props_poll_seconds"]
                self.health, self.error_count, backoff = AdapterHealth.LIVE, 0, 30
                await asyncio.sleep(10)
            except SourceBlocked as e:
                self.health, self.error_count = AdapterHealth.BLOCKED, self.error_count + 1
                log.error("draftkings: access refused (%s). Backing off 5 min; not working around it.", e)
                await asyncio.sleep(300)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                self.health, self.error_count = AdapterHealth.DEGRADED, self.error_count + 1
                backoff = min(backoff * 2, 600)
                log.warning("draftkings: %s: %s (retry in %ss)", type(e).__name__, e, backoff)
                await asyncio.sleep(backoff)

    async def aclose(self) -> None:
        await self.client.aclose()
