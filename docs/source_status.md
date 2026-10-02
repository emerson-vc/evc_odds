# Source Status

Last updated: **2026-10-01**. User is in **Pennsylvania**. Access policy: **self-collected only** (no paid aggregators).

Legend for **Status**:
- `working-undocumented`: adapter built and verified live; relies on a site's own undocumented endpoints.
- `official-available`: documented API, self-serve access; can build from docs.
- `partner-only`: documented API exists but requires approval/affiliate key.
- `undocumented-needs-capture`: no official API; only path is the site's own frontend traffic (user must supply captures).
- `blocked`: no permitted path currently known.

Every claim below is tagged **[doc]** (read on official docs), **[search]** (from search-result summaries, not yet confirmed on a primary page), or **[assumption]**.

---

## Summary

| # | Source | Type | Status | Official API | Market-data auth | Streaming | NFL ML/spread/total | Fixture |
|---|---|---|---|---|---|---|---|---|
| 1 | FanDuel | Sportsbook | **working-undocumented** (PA) | None | None (logged out, no cookies) | Polling (10s) | yes, verified live | `tests/fixtures/fanduel/` |
| 2 | DraftKings | Sportsbook | undocumented-needs-capture | None found | n/a | unknown | yes (product) | — |
| 3 | BetMGM | Sportsbook | undocumented-needs-capture | None found | n/a | unknown | yes (product) | — |
| 4 | Fanatics | Sportsbook | undocumented-needs-capture | None found | n/a | unknown | yes (product) | — |
| 5 | BetRivers | Sportsbook | undocumented-needs-capture | None found | n/a | unknown | yes (product) | — |
| 6 | Pinnacle | Sharp book | partner-only (email application) | Yes, closed to public since 2025-07-23 | API key on approval | REST | yes | — |
| 7 | Circa | Sharp book | blocked / undocumented | None found | n/a | unknown | yes (product) | — |
| 8 | Polymarket | Exchange | official-available | Yes (two products: intl + US) | Public reads | WebSocket | TODO verify NFL on US product | — |
| 9 | Kalshi | Exchange | **working-official** | Yes | None for REST market data | Polling (60s); WS available w/ key | yes, verified live (+ player props) | `tests/fixtures/kalshi/` |
| 10 | Novig | Exchange | official-available | Yes | REST public; WS needs key | WebSocket | TODO verify | — |
| 11 | ProphetX | Exchange | partner-only (affiliate key) | Yes | Affiliate API key | REST only (documented) | TODO verify | — |

**Headline:** all 4 exchanges have official APIs (2 are self-serve with public reads). **None of the 5 big US recreational books, nor Circa, publishes an official developer API.** Pinnacle went partner-only in July 2025.

---

## PA eligibility

| Source | Licensed/available in PA? | Role in scanner |
|---|---|---|
| FanDuel, DraftKings, BetMGM, Fanatics, BetRivers | Yes [search]: [LSR PA](https://www.legalsportsreport.com/sports-betting/states/pennsylvania/) | Candidate + consensus |
| Kalshi, Polymarket US, Novig, ProphetX | Active in PA [search]: [CBS](https://www.cbssports.com/prediction/news/pennsylvania/) | Candidate + consensus |
| Polymarket (international) | No (US geoblocked) | Consensus only |
| Pinnacle | No (doesn't serve US) | Consensus only, if ever accessible |
| Circa | No (NV, CO, IL, IA, KY, MO) [search]: [LSR Circa](https://www.legalsportsreport.com/sports-betting/circa-sportsbook-promo/) | Consensus only; no self-collected path known → effectively blocked |

Flags live in `config/sources.yaml` (`candidate_eligible`).

---

## Per-source detail

### 1. FanDuel — working (undocumented)
**Verified 2026-10-01** from a user browser capture (`captures/raw/`, git-ignored) and live runs from PA.

- **Observed endpoints** (undocumented, may change; configured in `config/sources.yaml`):
  - `POST scan.pa.sportsbook.fanduel.com/api/sports/navigation/facet/v1.0/search`: NFL game list + in-play flags (NFL `competitionId` 12282733).
  - `GET api.sportsbook.fanduel.com/sbapi/event-page?eventId=…`: all markets for a game; runners tagged `HOME`/`AWAY`/`OVER`/`UNDER`.
  - `POST smp.pa.sportsbook.fanduel.com/api/sports/fixedodds/readonly/v1/getMarketPrices` `{"marketIds":[…]}`: current prices **and current line** (spread/total lines move; always read from here).
- **Access:** works logged out, no cookies, honest user-agent. Requires the site's public web-client app key (`_ak` / `x-application`, identical for every visitor; stored in `.env`).
- **Anti-bot:** the site sends a PerimeterX (HUMAN) `x-px-context` token. **We do not send, copy, or generate it.** Live test 2026-10-01: endpoints answer without it. If FanDuel starts enforcing it (401/403/429), the adapter goes `BLOCKED` and backs off 5 min; no workaround.
- **Update frequency:** the site polls prices every ~5s; we poll game lines every 10s, player props every 30s. Game list/markets re-read every 5 min.
- **Coverage implemented:**
  - NFL pregame full-game moneyline, spread, total (28 games / 168 quotes on 2026-10-01).
  - Player props from the event page's `tab=same-game-parlay-` (the only tab whose request was observed): O/U passing yds, passing TDs, rushing yds, receiving yds, receptions, pass+rush yds, rush+rec yds; plus anytime TD (one-sided). 2026-10-01: 512 props across the 15 games of the coming week (FanDuel hadn't posted props for the following week yet).
  - Not mapped: alt ladders ("X+ Yards"), halves/quarters, specials.
- **Coverage gap (TODO):** FanDuel also has dedicated Passing/Receiving/Rushing/TD Scorer Props tabs that may list more players. Need a capture of clicking each tab to learn its request parameter; not guessed.
- **Assumption (TODO verify in FanDuel house rules):** NFL full-game lines and player stats include overtime.
- **Known limitation:** FanDuel's ToS likely restricts automated access; personal, low-rate, read-only use accepted by the user.

### 2–5. DraftKings, BetMGM, Fanatics, BetRivers
- **Official API:** none found. Search results and multiple aggregator vendors state that FanDuel/DraftKings/BetMGM offer no public developer API [search]: [oddspapi FanDuel](https://oddspapi.io/blog/fanduel-api-odds-access/), [sportsapis.dev](https://sportsapis.dev/sportsbook-api). No first-party statement found either way for Fanatics or BetRivers [assumption: same situation].
- **Possible paths:**
  1. **User-captured frontend traffic** (DevTools → Network → save JSON/HAR while viewing NFL odds in a legal state). Permitted under CLAUDE.md §5 as long as no anti-bot/geo/auth controls are bypassed. Undocumented, so it may break at any time.
  2. **Licensed aggregator.** The Odds API lists `fanduel`, `draftkings`, `betmgm`, `fanatics` (paid tiers only), `betrivers` in its US region [doc]: [bookmaker list](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html). Freshness/latency vs. direct capture: TODO measure.
- **Missing evidence (TODO):** one browser capture (HAR) of an NFL game page per book, made in PA; then the same no-token/no-cookie live test FanDuel passed.

### 6. Pinnacle
- Public API **closed to the general public since July 23, 2025**; bespoke access for "select high value bettors & commercial partnerships" and "academics and pregame handicapping projects", apply by email to `api@pinnacle.com` [doc]: [pinnacleapi-documentation README](https://github.com/pinnacleapi/pinnacleapi-documentation).
- The Odds API carries Pinnacle in its **EU** region only, not US [doc]: [bookmaker list](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html).
- **TODO:** decide whether to email Pinnacle describing this as a personal pregame project; note Pinnacle does not legally serve US bettors, so it is a *consensus-only* source, never a candidate.

### 7. Circa Sports
- No public API or developer tools found [search]: [sportsapis.dev/circa-api](https://sportsapis.dev/circa-api). Several paid aggregators claim Circa coverage (OpticOdds, Betstamp, SportsGameOdds, SharpAPI) [search]. The Odds API does **not** list Circa [doc].
- **TODO:** confirm whether Circa's web/app shows odds without login; otherwise mark blocked or aggregator-only.

### 8. Polymarket
- **Two distinct products:**
  - *International* (polymarket.com): CLOB REST `https://clob.polymarket.com`; public market WebSocket `wss://ws-subscriptions-clob.polymarket.com/ws/market`, no auth for public data [doc]: [WS overview](https://docs.polymarket.com/market-data/websocket/overview). Trading is geoblocked for US persons [search], so its prices are **not actionable for a US user**: consensus input only, never a candidate.
  - *Polymarket US* (CFTC-regulated, open to US residents with KYC since May 2026 [search]): separate docs at [docs.polymarket.us](https://docs.polymarket.us/llms.txt) with Events/Markets/BBO/Book endpoints, Markets WebSocket, and a Sports API supporting `GET /v2/leagues/nfl/events` [doc]: [Sports API](https://docs.polymarket.us/api-reference/sports/overview). Whether market-data reads need auth: **TODO verify** on the Authentication page.
- **TODO:** confirm Polymarket US base URL and auth for reads; confirm NFL spread/total (not just winner) markets exist; fee schedule.

### 9. Kalshi — working (official API)
**Verified 2026-10-01** with live runs. Adapter: `src/adapters/kalshi.py`.

- **Endpoint:** `GET external-api.kalshi.com/trade-api/v2/events?series_ticker=…&status=open&with_nested_markets=true`, no key [doc]. One call per series per cycle (9 series, 60s, 2s apart).
- **Series mapped:** `KXNFLGAME` (moneyline), `KXNFLSPREAD`, `KXNFLTOTAL`, `KXNFLPASSYDS`, `KXNFLPASSTDS`, `KXNFLRSHYDS`, `KXNFLRECYDS`, `KXNFLREC`, `KXNFLRRYDS`. `KXNFLANYTD` had no open markets on 2026-10-01 (format unseen, not mapped).
- **Contract → bet mapping** (from each market's rules text): "<Team> wins by over X" YES = Team -X / NO = Opp +X; "over X points" YES/NO = Over/Under X; "<Player>: N+ <stat>" YES/NO = Over/Under N-0.5. All strikes are half-points (no pushes).
- **Settlement differences vs sportsbooks (accepted, recorded in quote metadata):** moneyline ties pay $0.50 each (books refund; ~0.1pp effect); a player who is active but never plays settles at the pre-game fair price (books void).
- **Pricing:** executable ask for each side + taker fee `0.07·P·(1−P)` per contract [search; kalshi.com/fee-schedule rate-limited our fetch, TODO verify]. Asks with < 1 contract of depth are ignored. Moneyline uses the cheaper of YES on the team or NO on the opponent.
- **Events:** Kalshi doesn't mark home/away. Its games attach to the game another source listed with the same US/Eastern date and the same two teams; otherwise they wait (not shown). Team code `JAC` aliased to JAX.
- **Coverage 2026-10-01:** moneyline all 28 listed games; spreads/totals/props for the 16 games of the coming week; ~7,800 quotes (ladders of lines).

#### Original research notes
- Market-data REST: "No authentication headers are required" for market-data endpoints; base `https://external-api.kalshi.com/trade-api/v2` [doc]: [market data quickstart](https://docs.kalshi.com/getting_started/quick_start_market_data).
- WebSocket `wss://external-api-ws.kalshi.com/trade-api/ws/v2` requires API-key headers at handshake; channels include `orderbook_delta`, `ticker`, `trade`, `market_lifecycle_v2` [doc]: [WS connection](https://docs.kalshi.com/websockets/websocket-connection).
- NFL game winner, spreads, totals, and props listed as event contracts (e.g. series `KXNFLGAME`) [search]: [kalshi-nfl-board](https://github.com/saichaudhry/kalshi-nfl-board). Note: spread/total are typically *ladders of binary contracts* ("wins by more than X"), so mapping to sportsbook `MarketKey`s needs care.
- **TODO:** verify NFL series tickers against `GET /series`; fee formula; rate limits.

### 10. Novig
- Docs list **public (no key) endpoints**: List Events/Markets, Get Market, Get Order Book, List Trades, List Leagues/Sports/Market Types [doc]: [docs.novig.com/llms.txt](https://docs.novig.com/llms.txt). WebSocket requires a `trading` or `trading::read` key; keys are self-created from a Novig account profile [doc]: [docs.novig.com](https://docs.novig.com/).
- **TODO:** confirm NFL market-type coverage; whether account creation requires being in an eligible state.

### 11. ProphetX
- Official Market Data API (read-only, JSON over HTTPS); production `https://cash.api.prophetx.co/partner`, sandbox `https://api.sandbox.prophetx.dev/partner` [search]. Credentials: "Contact our Market Data team … to be issued an affiliate API key"; no streaming documented for the Market Data API [doc]: [market data integration](https://docs.prophetx.co/docs/market-data-integration).
- **TODO:** decide whether to request an affiliate key for personal use; verify NFL coverage.

---

## Implications for the go/no-go (CLAUDE.md §28)

1. **Exchanges first is the low-risk path to prove the pipeline.** Kalshi and Novig have documented, unauthenticated REST market data today.
2. **The ≥3 big-book checkpoint cannot be met from official APIs.** It requires either user-supplied frontend captures (fragile, undocumented) or a licensed aggregator (The Odds API covers 9 of 11 sources, but not Pinnacle-US or Circa).
3. **Two sources are consensus-only** for a US user: Pinnacle and international Polymarket. This needs a `candidate_eligible` flag per source in config.
