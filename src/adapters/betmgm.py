"""BetMGM adapter (PA), pregame NFL: game lines + player props — fed by the user's own browser.

Why it's different: BetMGM's odds API sits behind Cloudflare bot management, so the scanner can't request it
(403 to non-browser clients, verified 2026-10-02). Defeating that is off-limits (CLAUDE.md §5). Instead a
passive personal Chrome extension (browser_ext/betmgm_tap/) copies the JSON that BetMGM's own page receives
while the user has it open, and POSTs it to the local server (/ingest/betmgm). This adapter never contacts
BetMGM; it only parses what arrives. Coverage = the BetMGM pages the user has open.

Payloads (format from the browser capture captures/raw/mgm_2026-10-02.har):
  /cds-api/bettingoffer/fixtures      {fixtures: [fixture, ...]}   game lines incl. ~40 alt spreads/totals
  /cds-api/bettingoffer/fixture-view  {fixture: fixture}           one game, everything incl. player props

Market mapping (all observed). Parameters are a list of {key, value}; only Period=FullTime is used:
  MarketType=2way,         Happening=Point, no FixtureParticipant  -> moneyline (option.fixtureParticipant = team)
  MarketType=2wayHandicap, Happening=Point, no FixtureParticipant  -> spread; DecimalHandicap is the HOME
      handicap (e.g. 3 -> "Washington Commanders +3" / "Indianapolis Colts -3"); cross-checked with option names
  MarketType=Over/Under,   Happening=Point, no FixtureParticipant  -> game total (DecimalValue)
  MarketType=Over/Under,   FixtureParticipant=<player>, no MarketSubType, Happening in PROP_HAPPENINGS
      -> player O/U prop, e.g. "Daniel Jones - Passing Yards" Over/Under 223.5
  MarketSubType=PlayerTD, Happening=Touchdown, DecimalValue 0.5 -> "to score 1+ TDs" = anytime TD (Yes only)
  Skipped: milestone "X+" YesNo markets (alternate ladders), halves/quarters, specials.

Assumptions (TODO verify): only status "Visible" was seen; anything else is treated as not bettable.
NFL lines/stats include overtime (standard US rule; not yet read in BetMGM house rules).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from src.adapters.base import AdapterHealth, RawEvent, RawQuote
from src.models.market import MarketType, Period, Side

log = logging.getLogger(__name__)

SOURCE = "betmgm"
OVERTIME_INCLUDED = True  # assumption, see docstring

PROP_HAPPENINGS = {
    "PassingYards": MarketType.PLAYER_PASSING_YARDS,
    "TouchdownPass": MarketType.PLAYER_PASSING_TDS,
    "RushingYards": MarketType.PLAYER_RUSHING_YARDS,
    "ReceivingYards": MarketType.PLAYER_RECEIVING_YARDS,
    "Reception": MarketType.PLAYER_RECEPTIONS,
    "PassingRushingYards": MarketType.PLAYER_PASS_RUSH_YARDS,
    "RushingReceivingYards": MarketType.PLAYER_RUSH_REC_YARDS,
}
_TRAILING_NUMBER = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*$")


def _params(m: dict) -> dict[str, str]:
    return {p["key"]: p["value"] for p in m.get("parameters") or []}


def _text(x) -> str:
    return (x or {}).get("value", "") if isinstance(x, dict) else (x or "")


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_fixture(fx: dict, received_at: datetime) -> tuple[RawEvent | None, list[RawQuote]]:
    """One BetMGM fixture (from either endpoint) -> (event, quotes). Non-NFL or live games -> (None, [])."""
    if _text((fx.get("competition") or {}).get("name")) != "NFL" or fx.get("fixtureType") != "PairGame":
        return None, []
    if fx.get("stage") != "PreMatch":
        return None, []  # pregame only (CLAUDE.md §30)

    sides_by_participant: dict[int, Side] = {}
    teams: dict[Side, str] = {}
    players: dict[str, str] = {}
    for p in fx.get("participants") or []:
        ptype = (p.get("properties") or {}).get("type")
        if ptype in ("HomeTeam", "AwayTeam"):
            side = Side.HOME if ptype == "HomeTeam" else Side.AWAY
            sides_by_participant[p.get("id")] = side
            teams[side] = _text(p.get("name"))
        elif ptype == "Player":
            players[str(p.get("id"))] = _text(p.get("name")).strip()
    if set(teams) != {Side.HOME, Side.AWAY}:
        return None, []

    sid = str(fx["id"])
    event = RawEvent(
        source=SOURCE, source_event_id=sid, sport="american_football", league="NFL",
        home_name=teams[Side.HOME], away_name=teams[Side.AWAY], start_time_utc=_parse_time(fx["startDate"]),
        in_play=False, metadata={"name": _text(fx.get("name"))},
    )

    quotes: list[RawQuote] = []

    def add(m: dict, o: dict, mtype: MarketType, side: Side, line: float | None, subject: str | None = None):
        price = o.get("price") or {}
        dec = price.get("odds")
        if dec is None or float(dec) <= 1:
            return
        quotes.append(RawQuote(
            source=SOURCE, source_event_id=sid, source_market_id=str(m["id"]), source_selection_id=str(o.get("id")),
            market_type=mtype, subject=subject, period=Period.FULL_GAME, side=side, line=line,
            overtime_included=OVERTIME_INCLUDED, decimal_odds=float(dec), american_odds=price.get("americanOdds"),
            is_open=m.get("status") == "Visible" and o.get("status") == "Visible",
            timestamp_source=None, timestamp_received=received_at, metadata={"market_name": _text(m.get("name"))},
        ))

    for m in fx.get("optionMarkets") or []:
        p = _params(m)
        if p.get("Period") != "FullTime":
            continue
        mtype_raw, happening, options = p.get("MarketType"), p.get("Happening"), m.get("options") or []
        player_id = p.get("FixtureParticipant")

        if happening == "Point" and not player_id:
            if mtype_raw == "2way":
                for o in options:
                    side = sides_by_participant.get((o.get("parameters") or {}).get("fixtureParticipant"))
                    if side:
                        add(m, o, MarketType.MONEYLINE, side, None)
            elif mtype_raw == "2wayHandicap" and "DecimalHandicap" in p:
                home_line = float(p["DecimalHandicap"])
                for o in options:
                    side = sides_by_participant.get((o.get("parameters") or {}).get("fixtureParticipant"))
                    if side is None:
                        continue
                    line = home_line if side == Side.HOME else -home_line
                    shown = _TRAILING_NUMBER.search(_text(o.get("name")))
                    if not shown or float(shown.group(1)) != line:
                        log.warning("betmgm: spread %s option %r doesn't match handicap %s; skipped",
                                    m.get("id"), _text(o.get("name")), home_line)
                        continue
                    add(m, o, MarketType.SPREAD, side, line)
            elif mtype_raw == "Over/Under" and "DecimalValue" in p:
                for o in options:
                    kind = ((o.get("parameters") or {}).get("optionTypes") or [None])[0]
                    if kind in ("Over", "Under"):
                        add(m, o, MarketType.TOTAL, Side.OVER if kind == "Over" else Side.UNDER, float(p["DecimalValue"]))
            continue

        if mtype_raw != "Over/Under" or not player_id or str(player_id) not in players:
            continue
        player = players[str(player_id)]
        sub = p.get("MarketSubType")
        if sub is None and happening in PROP_HAPPENINGS and "DecimalValue" in p:
            for o in options:
                kind = ((o.get("parameters") or {}).get("optionTypes") or [None])[0]
                if kind in ("Over", "Under"):
                    add(m, o, PROP_HAPPENINGS[happening], Side.OVER if kind == "Over" else Side.UNDER,
                        float(p["DecimalValue"]), player)
        elif sub == "PlayerTD" and happening == "Touchdown" and float(p.get("DecimalValue", 0)) == 0.5:
            if "D/ST" in player:
                continue
            for o in options:
                if _text(o.get("name")) == "Yes":
                    add(m, o, MarketType.PLAYER_ANYTIME_TD, Side.YES, None, player)
    return event, quotes


def parse_payload(path: str, data: dict, received_at: datetime) -> tuple[list[RawEvent], list[RawQuote]]:
    """A response captured by the extension. `path` decides the shape."""
    if path.endswith("/fixture-view"):
        fixtures = [data.get("fixture")] if data.get("fixture") else []
    elif path.endswith("/fixtures"):
        fixtures = data.get("fixtures") or []
    else:
        return [], []
    events, quotes = [], []
    for fx in fixtures:
        ev, qs = parse_fixture(fx, received_at)
        if ev:
            events.append(ev)
            quotes += qs
    return events, quotes


class BetMGMAdapter:
    """Push adapter: the local server hands it payloads; it never makes network requests."""

    name = SOURCE

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=cfg.get("max_queued_payloads", 50))
        self.health = AdapterHealth.STARTING
        self.error_count = 0
        self.last_success: float | None = None
        self.request_count = 0  # requests sent to BetMGM: always 0
        self.payloads_received = 0

    def submit(self, path: str, data: dict) -> None:
        if self.queue.full():
            self.queue.get_nowait()  # drop the oldest; newer data supersedes it
        self.queue.put_nowait((path, data, datetime.now(timezone.utc)))
        self.payloads_received += 1

    async def discover_events(self) -> list[RawEvent]:
        return []  # nothing to fetch; data arrives via submit()

    async def fetch_snapshot(self, event: RawEvent) -> list[RawQuote]:
        return []

    async def stream_updates(self) -> AsyncIterator[RawEvent | RawQuote]:
        idle = self.cfg.get("idle_seconds", 300)
        while True:
            try:
                path, data, received_at = await asyncio.wait_for(self.queue.get(), timeout=idle)
            except asyncio.TimeoutError:
                self.health = AdapterHealth.DISCONNECTED  # no BetMGM tab sending data
                continue
            try:
                events, quotes = parse_payload(path, data, received_at)
            except (KeyError, TypeError, ValueError) as e:
                self.error_count += 1
                self.health = AdapterHealth.DEGRADED
                log.warning("betmgm: couldn't parse payload from %s: %s", path, e)
                continue
            self.last_success = time.time()
            self.health = AdapterHealth.LIVE
            for ev in events:
                yield ev
            for q in quotes:
                yield q
