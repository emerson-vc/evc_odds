"""Entry point.

    python -m src.main --once            # one snapshot: print every NFL game's main lines from FanDuel
    python -m src.main                   # keep polling; print each price/line change as it happens
    python -m src.main --minutes 10      # same, stop after 10 minutes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from src.adapters.base import AdapterHealth
from src.adapters.fanduel import FanDuelAdapter
from src.adapters.registry import build_enabled
from src.config import require_env, source_config
from src.models.market import MarketType, Side
from src.models.quote import CanonicalQuote
from src.normalization.events import EASTERN
from src.normalization.markets import Normalizer
from src.pricing.devig import get_method, overround
from src.pricing.odds import probability_to_american
from src.scanner.engine import Scanner
from src.state.market_store import MarketStore

log = logging.getLogger("evc")


def build_fanduel() -> FanDuelAdapter:
    return FanDuelAdapter(source_config("fanduel"), require_env("FANDUEL_APP_KEY"))


def fmt_price(q: CanonicalQuote | None) -> str:
    if q is None:
        return "   --"
    if not q.is_open:
        return " SUSP"
    return f"{q.american_odds:+5d}" if q.american_odds is not None else f"{q.decimal_odds:.3f}"


def print_board(store: MarketStore, normalizer: Normalizer) -> None:
    devig = get_method("proportional")
    by_event: dict[str, dict[tuple, CanonicalQuote]] = defaultdict(dict)
    for group in store.by_group().values():
        for q in group:
            by_event[q.event_id][(q.market_type, q.side)] = q

    events = sorted({ev.event_id: ev for ev in normalizer.events.values()}.values(), key=lambda e: e.start_time_utc)
    print(f"\nFanDuel PA — NFL pregame main lines   ({datetime.now(EASTERN):%a %b %d %I:%M:%S %p} ET)\n")
    for ev in events:
        rows = by_event.get(ev.event_id)
        if not rows:
            continue
        kick = ev.start_time_utc.astimezone(EASTERN)
        print(f"{ev.away_team} @ {ev.home_team}   {kick:%a %m/%d %I:%M %p} ET   [{ev.event_id}]")
        a, h = rows.get((MarketType.MONEYLINE, Side.AWAY)), rows.get((MarketType.MONEYLINE, Side.HOME))
        fair = ""
        if a and h and a.is_open and h.is_open:
            p = devig.fair_probabilities({"A": a.decimal_odds, "H": h.decimal_odds})
            vig = overround({"A": a.decimal_odds, "H": h.decimal_odds})
            fair = (f"   no-vig: {ev.away_team} {probability_to_american(p['A']):+d} / "
                    f"{ev.home_team} {probability_to_american(p['H']):+d}  (vig {vig:.1%})")
        print(f"   Moneyline  {ev.away_team:>3} {fmt_price(a)}   {ev.home_team:>3} {fmt_price(h)}{fair}")
        a, h = rows.get((MarketType.SPREAD, Side.AWAY)), rows.get((MarketType.SPREAD, Side.HOME))
        if a and h:
            print(f"   Spread     {ev.away_team:>3} {a.line:+5g} {fmt_price(a)}   {ev.home_team:>3} {h.line:+5g} {fmt_price(h)}")
        o, u = rows.get((MarketType.TOTAL, Side.OVER)), rows.get((MarketType.TOTAL, Side.UNDER))
        if o and u:
            print(f"   Total      O {o.line:<5g} {fmt_price(o)}   U {u.line:<5g} {fmt_price(u)}")
    print()


def describe(q: CanonicalQuote, normalizer: Normalizer) -> str:
    ev = next((e for e in normalizer.events.values() if e.event_id == q.event_id), None)
    game = f"{ev.away_team}@{ev.home_team}" if ev else q.event_id
    team = {Side.AWAY: ev.away_team, Side.HOME: ev.home_team}.get(q.side, q.side.value) if ev else q.side.value
    line = "" if q.line is None else (f" {q.line:+g}" if q.market_type == MarketType.SPREAD else f" {q.line:g}")
    return f"{game:<9} {q.market_type.value:<9} {team}{line}"


async def run_once() -> None:
    adapter, normalizer, store = build_fanduel(), Normalizer(), MarketStore()
    try:
        events = [e for e in await adapter.discover_events() if not e.in_play]
        for ev in events:
            if normalizer.register_event(ev):
                for raw in await adapter.fetch_snapshot(ev):
                    if q := normalizer.normalize(raw):
                        store.update(q)
        print_board(store, normalizer)
        print(f"{len(events)} pregame games, {len(store)} quotes. Normalization failures: {dict(normalizer.failures) or 'none'}")
    finally:
        await adapter.aclose()


async def run_live(minutes: float | None) -> None:
    changes = 0

    def on_change(q: CanonicalQuote, prev: CanonicalQuote) -> None:
        nonlocal changes
        changes += 1
        old = f"{prev.line:g} {fmt_price(prev).strip()}" if prev.line is not None else fmt_price(prev).strip()
        new = f"{q.line:g} {fmt_price(q).strip()}" if q.line is not None else fmt_price(q).strip()
        print(f"[{datetime.now(EASTERN):%H:%M:%S}] {q.source:<9} {describe(q, scanner.normalizer):<32} {old:>12} -> {new}")

    scanner = Scanner(build_enabled(), on_change=on_change)
    task = asyncio.create_task(scanner.run())
    deadline = time.monotonic() + minutes * 60 if minutes else None
    board_printed = False
    try:
        while not (deadline and time.monotonic() >= deadline):
            await asyncio.sleep(1 if not board_printed else 60)
            if not board_printed and len(scanner.store) and all(a.health == AdapterHealth.LIVE for a in scanner.adapters):
                print_board(scanner.store, scanner.normalizer)
                print("Watching for changes... (Ctrl+C to stop)\n")
                board_printed = True
            elif board_printed:
                for a in scanner.adapters:
                    age = time.time() - a.last_success if getattr(a, "last_success", None) else float("nan")
                    print(f"[{datetime.now(EASTERN):%H:%M:%S}] {a.name} {a.health}  {len(scanner.store)} quotes, "
                          f"{changes} changes, last response {age:.1f}s ago")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await scanner.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="EVC odds scanner")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    parser.add_argument("--minutes", type=float, help="stop live mode after N minutes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run_once() if args.once else run_live(args.minutes))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
