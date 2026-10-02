# EVC Odds

Personal, local +EV odds scanner. Design rules live in [CLAUDE.md](CLAUDE.md); per-source status in
[docs/source_status.md](docs/source_status.md).

**Current state:** five live sources for NFL pregame: FanDuel, DraftKings (undocumented web endpoints),
BetMGM (via the passive browser extension in `browser_ext/betmgm_tap/`), Kalshi and Polymarket US (official
APIs). Game lines everywhere; player props on FanDuel, DraftKings, BetMGM, Kalshi. Consensus / +EV scan is next.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # then fill in FANDUEL_APP_KEY
```

## Run

**Odds screen (local website):**

```bash
.venv/bin/python -m src.api.app      # then open http://127.0.0.1:8000  (Ctrl+C to stop)
```

Only reachable from this computer. Tabs for Moneyline / Spread / Total / Player Props (filter by stat, game,
player), one column per connected book, best price highlighted, recent changes flash, stale prices greyed.
Link straight to a tab with `http://127.0.0.1:8000/#PROPS`. JSON at `/api/board` and `/api/health`.

**Terminal:**

```bash
.venv/bin/python -m src.main --once          # snapshot of every NFL game's FanDuel main lines
.venv/bin/python -m src.main                 # watch live; prints each price/line change
.venv/bin/python -m src.main --minutes 10    # watch for 10 minutes
.venv/bin/python -m pytest                   # tests (offline, use saved fixtures)
```

## Layout

```
config/         sources.yaml (endpoints, poll rates, weights), scanner.yaml (thresholds), aliases.yaml (team names)
src/adapters/   one module per source; all source-specific details stay here
src/models/     CanonicalEvent, CanonicalQuote, MarketKey
src/normalization/  team names -> codes, event IDs, raw -> canonical quotes
src/pricing/    odds conversion, de-vig, EV
src/state/      latest quote per market
src/scanner/    engine.py runs all adapters independently; board.py builds the odds-screen view
src/api/        FastAPI app + static/index.html (the odds screen)
tests/fixtures/ redacted responses cut from browser captures
captures/raw/   raw HAR captures (git-ignored: may contain cookies/tokens)
```
