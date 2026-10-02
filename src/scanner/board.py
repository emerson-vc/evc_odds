"""Builds the odds-screen view (JSON-ready dict) from the store. Pure function, no I/O.

Shape:
  games[].markets[MARKET_TYPE][SIDE][source] = cell          (game lines)
  games[].props[] = {market_type, label, player, player_name, sides: {SIDE: {source: cell}}}
  cell = {american, decimal, line, open, age, stale, best, changed, no_vig, liquidity, note, alt}
  alt = this source doesn't offer the main line; the cell shows its nearest-to-even line instead.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from src.models.event import CanonicalEvent
from src.models.market import GAME_LINES, PROP_LABELS, MarketType
from src.models.quote import CanonicalQuote
from src.normalization.entities import TeamResolver
from src.pricing.devig import DevigMethod
from src.pricing.odds import probability_to_american

PROP_ORDER = {mt: i for i, mt in enumerate(PROP_LABELS)}


def _reference_line(per_source: dict[str, list[dict]]) -> float | None:
    """The main line: most common line among sources that quote exactly one line (i.e. sportsbooks)."""
    singles = [opts[0]["line"] for opts in per_source.values() if len(opts) == 1]
    if not singles:
        return None
    return Counter(singles).most_common(1)[0][0]


def _pick(options: list[dict], ref: float | None) -> dict:
    """Choose which of a source's lines to display.

    With a reference (sportsbook main) line: that line, else the nearest one (ties -> smaller |line|, which
    keeps both sides of a spread on the same pair, e.g. AWAY -2.5 / HOME +2.5). Without one: closest to even.
    """
    if len(options) == 1:
        chosen = options[0]
    elif ref is not None:
        chosen = min(options, key=lambda o: (abs((o["line"] or 0) - ref), abs(o["line"] or 0)))
    else:
        chosen = min(options, key=lambda o: abs(o["decimal"] - 2.0))
    chosen["alt"] = ref is not None and chosen["line"] != ref
    return chosen


def _mark_best(per_source: dict[str, dict]) -> None:
    """Best price per line among open, fresh quotes; only meaningful with 2+ books."""
    live = [c for c in per_source.values() if c["open"] and not c["stale"]]
    if len(live) < 2:
        return
    best: dict = {}
    for c in live:
        if c["line"] not in best or c["decimal"] > best[c["line"]]["decimal"]:
            best[c["line"]] = c
    for c in best.values():
        c["best"] = True


def build_board(
    quotes: list[CanonicalQuote],
    events: list[CanonicalEvent],
    sources: list[dict],
    teams: TeamResolver,
    devig: DevigMethod,
    stale_seconds: float,
    changed_at: dict[tuple, float] | None = None,
    slot_of=None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    changed_at = changed_at or {}
    upcoming = {e.event_id: e for e in events if e.start_time_utc > now}  # pregame only

    # (source, market group) -> {side: quote}, for each book's own no-vig price
    by_group: dict[tuple, dict] = defaultdict(dict)
    player_names: dict[str, str] = {}
    for q in quotes:
        if q.event_id in upcoming:
            by_group[(q.source, q.market_key.group())][q.side] = q
            if q.subject and "subject_name" in q.metadata:
                player_names.setdefault(q.subject, q.metadata["subject_name"])

    # event -> (market_type, subject) -> side -> source -> [candidate cells, one per line]
    cands: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    for (source, _), sides in by_group.items():
        no_vig: dict = {}
        if len(sides) == 2 and all(q.is_open for q in sides.values()):
            no_vig = devig.fair_probabilities({s: q.decimal_odds for s, q in sides.items()})
        for side, q in sides.items():
            age = (now - q.timestamp_received).total_seconds()
            cands[q.event_id][(q.market_type, q.subject)][side.value][source].append({
                "american": q.american_odds,
                "decimal": round(q.decimal_odds, 4),
                "line": q.line,
                "open": q.is_open,
                "age": round(age, 1),
                "stale": age > stale_seconds,
                "changed": changed_at.get(slot_of(q)) if slot_of else None,
                "no_vig": probability_to_american(no_vig[side]) if side in no_vig else None,
                "liquidity": q.liquidity,
                "note": q.metadata.get("note"),
                "best": False,
                "alt": False,
            })

    # Exchanges list a ladder of lines; show each source at the sportsbooks' main line when it has it.
    cells: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    for event_id, markets in cands.items():
        for mkey, sides in markets.items():
            for side, per_source in sides.items():
                ref = _reference_line(per_source)
                for source, options in per_source.items():
                    cells[event_id][mkey][side][source] = _pick(options, ref)

    for markets in cells.values():
        for sides in markets.values():
            for per_source in sides.values():
                _mark_best(per_source)

    games = []
    for ev in sorted(upcoming.values(), key=lambda e: (e.start_time_utc, e.event_id)):
        ev_cells = cells.get(ev.event_id)
        if not ev_cells:
            continue
        props = [
            {
                "market_type": mt.value,
                "label": PROP_LABELS[mt],
                "player": subject,
                "player_name": player_names.get(subject, subject),
                "sides": {s: dict(v) for s, v in sides.items()},
            }
            for (mt, subject), sides in ev_cells.items()
            if mt.is_player_prop
        ]
        props.sort(key=lambda p: (PROP_ORDER[MarketType(p["market_type"])], p["player_name"]))
        games.append({
            "event_id": ev.event_id,
            "league": ev.league,
            "start": ev.start_time_utc.isoformat(),
            "away": ev.away_team,
            "home": ev.home_team,
            "away_name": teams.display_name(ev.league, ev.away_team),
            "home_name": teams.display_name(ev.league, ev.home_team),
            "markets": {mt.value: {s: dict(v) for s, v in ev_cells.get((mt, None), {}).items()} for mt in GAME_LINES},
            "props": props,
        })
    return {
        "generated_at": now.isoformat(),
        "stale_seconds": stale_seconds,
        "prop_labels": {mt.value: label for mt, label in PROP_LABELS.items()},
        "sources": sources,
        "games": games,
    }
