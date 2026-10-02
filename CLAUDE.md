# CLAUDE.md — Personal Real-Time +EV Odds Scanner

## 1. Project Mission

Build a **local-first, personal-use sports betting market scanner** that continuously collects live odds from a fixed set of sportsbooks and prediction/exchange markets, normalizes equivalent markets, estimates a robust fair probability from the rest of the market, and surfaces **positive expected value (+EV) outliers** in real time.

This is **not** a commercial SaaS product and is **not** intended to recreate every OddsJam feature. The goal is to replace the specific OddsJam workflow I personally use: spotting when one book — especially a recreational sportsbook such as BetMGM or Fanatics — is materially mispriced relative to the broader market.

The project should optimize for:

1. **Freshness** of odds.
2. **Correct matching** of equivalent markets across sources.
3. **Reliable outlier detection**.
4. **Transparency** into how fair value was calculated.
5. **Low operating cost**.
6. **Maintainability by one person**.

Do not optimize for public scale, multi-user support, billing, mobile distribution, or enterprise architecture.

---

## 2. Target Sources

The system should be designed around exactly these 11 sources:

### Traditional sportsbooks

1. FanDuel
2. DraftKings
3. BetMGM
4. Fanatics Sportsbook
5. BetRivers
6. Pinnacle
7. Circa Sports

### Prediction markets / exchanges

8. Polymarket
9. Kalshi
10. Novig
11. ProphetX

These sources form the core comparison universe. Do not add additional books unless explicitly requested.

The ingestion layer must treat every source as an independent adapter. Never write source-specific logic into the consensus, EV, UI, or matching layers.

---

## 3. What the Product Should Do

For each exact betting market, the system should maintain the latest usable quote from every available source.

Example conceptual market:

- Event: Philadelphia 76ers at Boston Celtics
- Player: Tyrese Maxey
- Market: Player Points
- Period: Full Game
- Side: Over
- Line: 25.5

Possible prices:

- BetMGM: +125
- FanDuel: -110
- DraftKings: -115
- Fanatics: -108
- BetRivers: -112
- Pinnacle: -115
- Circa: -110
- Polymarket: 53¢ equivalent probability
- Kalshi: 54¢ equivalent probability
- Novig: -108 equivalent
- ProphetX: -111 equivalent

If BetMGM is the candidate opportunity, **BetMGM must be excluded from the fair-value calculation**. The remaining matching sources should be used to estimate a fair probability.

If that fair probability implies fair odds around -110 while BetMGM is offering +125, the system should flag the BetMGM quote as a high-EV outlier.

The central product idea is therefore:

> **Detect source-level pricing disagreements against a leave-one-out market consensus.**

---

## 4. Non-Goals

Do not build the following unless explicitly requested later:

- Automated wager placement.
- A public sportsbook API.
- User accounts.
- Stripe or subscriptions.
- Multi-user support.
- Social features.
- Mobile apps.
- Affiliate links.
- Automated bet execution.
- Browser automation whose purpose is placing bets.
- An ML model that predicts sports outcomes from statistics.
- A generic sports prediction model.

The core signal comes from **cross-market pricing disagreement**, not from predicting games independently.

---

## 5. Data Acquisition Principles

The hardest part of this project is obtaining fresh, structured odds data from the 11 sources.

For each source, prefer ingestion methods in this order:

1. Official documented API.
2. Official documented streaming interface.
3. Publicly accessible structured web/application data endpoint that can be used without bypassing technical controls.
4. Browser-rendered page parsing only as a last resort.

Prefer structured JSON, GraphQL, REST, WebSocket, Server-Sent Events, or equivalent machine-readable data over HTML scraping.

### Hard constraints

Do **not**:

- bypass CAPTCHAs;
- defeat anti-bot protections;
- bypass authentication or authorization;
- automate MFA circumvention;
- steal or reuse another user's session;
- spoof or defeat geolocation restrictions;
- rotate proxies to evade access controls;
- fingerprint-spoof to bypass restrictions;
- exploit private credentials;
- claim an undocumented endpoint is stable or supported when it is not.

If a source requires approved API access, login, or other permission that is not available, clearly mark that adapter as blocked or partial rather than inventing a workaround.

When investigating a sportsbook's web application, it is acceptable to inspect ordinary browser network traffic to understand what data the frontend itself receives. Do not turn that investigation into a plan for bypassing technical restrictions.

---

## 6. Source Adapter Architecture

Every source must implement the same logical interface.

Conceptually:

```python
class OddsSourceAdapter(Protocol):
    name: str

    async def discover_events(self) -> list[RawEvent]:
        ...

    async def fetch_snapshot(self, event: RawEvent) -> list[RawQuote]:
        ...

    async def stream_updates(self) -> AsyncIterator[RawQuote]:
        ...
```

Not every source will support streaming. Adapters may implement either:

- streaming;
- polling;
- or both.

The rest of the application must not care which acquisition method a source uses.

Each adapter should live in its own module, for example:

```text
src/
  adapters/
    fanduel.py
    draftkings.py
    betmgm.py
    fanatics.py
    betrivers.py
    pinnacle.py
    circa.py
    polymarket.py
    kalshi.py
    novig.py
    prophetx.py
```

Source-specific request schemas, IDs, response shapes, authentication configuration, or endpoint quirks must remain inside that adapter.

---

## 7. Do Not Fabricate Source Details

This rule is critical.

When implementing a source adapter:

- Do not invent URLs.
- Do not invent undocumented request parameters.
- Do not invent API keys.
- Do not invent authentication headers.
- Do not guess response fields and present them as factual.

If source behavior is unknown, create a clearly marked research TODO and state exactly what evidence is needed, such as:

- official API documentation;
- a captured public JSON response;
- a browser Network request/response sample;
- a WebSocket message sample;
- an API-access approval email;
- or a saved fixture supplied by the user.

It is better to leave one adapter incomplete than to create fake integration code.

---

## 8. Canonical Data Model

All source data must be transformed into a common internal representation before any cross-source comparison occurs.

Use typed models, preferably Pydantic dataclasses/models.

### Canonical event

```python
CanonicalEvent(
    event_id: str,
    sport: str,
    league: str,
    home_team: str | None,
    away_team: str | None,
    participants: tuple[str, ...],
    start_time_utc: datetime,
)
```

### Canonical quote

```python
CanonicalQuote(
    source: str,
    source_event_id: str,
    source_market_id: str | None,
    event_id: str,
    sport: str,
    league: str,
    market_type: str,
    subject: str | None,
    period: str,
    side: str,
    line: float | None,
    decimal_odds: float,
    american_odds: int | None,
    implied_probability_raw: float,
    timestamp_source: datetime | None,
    timestamp_received: datetime,
    liquidity: float | None,
    metadata: dict,
)
```

Always store decimal odds internally. American odds may be kept for display.

---

## 9. Canonical Market Key

Cross-source comparison must occur only when quotes refer to the exact same economic bet.

Create a deterministic `MarketKey` containing enough fields to prevent false matches.

Example:

```python
MarketKey(
    event_id="NBA_2026_10_27_PHI_BOS",
    market_type="PLAYER_POINTS",
    subject="TYRESE_MAXEY",
    period="FULL_GAME",
    side="OVER",
    line=25.5,
    overtime_included=True,
)
```

Possible market dimensions include:

- event;
- team/player/participant;
- market family;
- side;
- line;
- period;
- alternate vs main market;
- regulation-only vs overtime included;
- listed pitcher or similar settlement conditions;
- game segment;
- exact stat definition.

Never compare quotes just because their display strings look similar.

---

## 10. Normalization and Matching

Market normalization is one of the most important components of the project.

Different sources may represent the same thing differently:

- `J. Brunson`
- `Jalen Brunson`
- `Brunson, Jalen`

or:

- `Player Points`
- `Total Points`
- `Points O/U`

or:

- `26+ Points`
- `Over 25.5 Points`

Normalization should map source-specific representations into deterministic canonical entities.

### Rules

1. Maintain canonical dictionaries for teams, players, leagues, and common market types.
2. Prefer stable source IDs when possible.
3. Use time and participants when matching events across books.
4. Use aliases only to resolve names, not to infer settlement semantics.
5. Fuzzy matching may help propose a mapping but must not silently create a high-confidence +EV signal.
6. If market equivalence is uncertain, exclude the quote from consensus.
7. Log unmatched markets for later review.

A false positive is more harmful than missing an opportunity.

---

## 11. Odds Conversion

Implement tested utilities for:

- American to decimal odds;
- decimal to American odds;
- odds to implied probability;
- probability to fair odds.

Examples:

For positive American odds `+A`:

```text
p = 100 / (A + 100)
```

For negative American odds `-A`:

```text
p = A / (A + 100)
```

For decimal odds `d`:

```text
p = 1 / d
```

Keep these utilities independent from source adapters.

---

## 12. De-Vigging Sportsbook Markets

Traditional sportsbook prices include vig and should not normally be used as raw fair probabilities.

For a two-way market with probabilities `p1_raw` and `p2_raw`, support at minimum a simple proportional de-vig method:

```text
p1_fair = p1_raw / (p1_raw + p2_raw)
p2_fair = p2_raw / (p1_raw + p2_raw)
```

Design the interface so additional methods can be added later.

Example:

```python
class DevigMethod(Protocol):
    def fair_probabilities(self, outcomes: list[Quote]) -> dict[str, float]:
        ...
```

Do not hard-code one de-vig technique throughout the codebase.

If the opposing side required to de-vig a sportsbook market is missing or stale, either:

- exclude the source from consensus;
- or mark the resulting probability as lower confidence.

Do not silently treat a vigged one-sided sportsbook quote as a fair probability.

---

## 13. Exchange and Prediction-Market Prices

Polymarket, Kalshi, Novig, and ProphetX should be treated as market/exchange-style sources rather than identical to traditional sportsbooks.

When possible, use the **executable price** relevant to the desired side rather than a naive midpoint.

Account for:

- bid/ask spread;
- available liquidity;
- platform fees if they materially affect effective price;
- stale or thin order books.

Store exchange-specific values in metadata but normalize the actionable price into the common quote model.

Do not give a tiny illiquid prediction-market quote the same influence as a deep, fresh market without making that weighting decision explicit.

---

## 14. Fair-Value / Consensus Engine

The engine must estimate fair probability for each candidate quote using **all other valid matching sources**.

This is a leave-one-out calculation.

For candidate source `S`:

```text
fair_market(S) = consensus(all valid sources except S)
```

Do not allow the candidate source's own price to influence the fair value used to evaluate that candidate.

### Initial consensus method

Use a robust method such as a weighted median of fair probabilities.

Do not hard-code arbitrary permanent source weights. Put weights in configuration.

Example configuration:

```yaml
consensus:
  method: weighted_median
  source_weights:
    fanduel: 1.0
    draftkings: 1.0
    betmgm: 1.0
    fanatics: 1.0
    betrivers: 1.0
    pinnacle: 1.0
    circa: 1.0
    polymarket: 1.0
    kalshi: 1.0
    novig: 1.0
    prophetx: 1.0
```

Start neutral unless evidence supports different weights.

Later, weights may be learned empirically from closing-line performance, reliability, liquidity, or historical forecasting accuracy.

---

## 15. Freshness Weighting

Every quote must carry a receive timestamp.

The engine must know how old each quote is.

Add configurable stale thresholds, for example:

```yaml
freshness:
  warning_seconds: 5
  stale_seconds: 15
```

These values are placeholders and should be tuned based on observed source behavior.

A stale quote should never create a strong +EV alert.

Possible future approach:

```text
effective_weight = source_weight × freshness_weight × liquidity_weight
```

Keep the architecture compatible with this even if the MVP initially uses simpler rules.

---

## 16. EV Calculation

For candidate decimal odds `d` and fair win probability `p`:

```text
EV per $1 staked = p × (d - 1) - (1 - p)
```

Equivalent simplification:

```text
EV = p × d - 1
```

Display EV as a percentage:

```text
EV% = 100 × (p × d - 1)
```

The calculation must use the actual candidate price the user could take.

For exchange-style sources, use effective odds after relevant fees when applicable.

---

## 17. Opportunity Qualification

A quote should only be displayed as an opportunity when all configured requirements are satisfied.

Suggested configurable gates:

- minimum EV%;
- minimum number of independent comparison sources;
- maximum quote age;
- minimum exchange liquidity;
- allowed sports;
- allowed leagues;
- allowed market types;
- allowed candidate books;
- minimum and maximum candidate odds;
- pregame/live flag;
- market-match confidence.

Example:

```yaml
scanner:
  min_ev_percent: 3.0
  min_consensus_sources: 4
  max_quote_age_seconds: 10
```

Do not hard-code personal preferences into business logic.

---

## 18. Real-Time Processing Model

Use asynchronous I/O.

Recommended Python stack:

- Python 3.12+
- `asyncio`
- `httpx`
- `websockets` or source-appropriate streaming libraries
- Pydantic
- FastAPI for local API/UI backend
- SQLite initially

Do not use threads when async I/O is sufficient.

Each source should run as an independent task.

Conceptual runtime:

```python
await asyncio.gather(
    run_adapter(fanduel),
    run_adapter(draftkings),
    run_adapter(betmgm),
    run_adapter(fanatics),
    run_adapter(betrivers),
    run_adapter(pinnacle),
    run_adapter(circa),
    run_adapter(polymarket),
    run_adapter(kalshi),
    run_adapter(novig),
    run_adapter(prophetx),
)
```

Each incoming quote should flow approximately:

```text
source adapter
    ↓
raw quote
    ↓
normalizer
    ↓
canonical quote
    ↓
market state update
    ↓
consensus recomputation
    ↓
EV scan
    ↓
UI / alert
```

Do not recompute the entire universe after every tick if only one market changed.

---

## 19. Storage

This is a personal project, so begin simple.

Use SQLite unless measurements show it is insufficient.

Store at minimum:

### Current state

Latest quote per:

```text
(source, MarketKey)
```

### History

Optional but strongly preferred:

- quote timestamp;
- old price;
- new price;
- line changes;
- detected opportunities;
- fair probability at detection;
- candidate price;
- source composition of consensus.

Historical data will later allow analysis of:

- closing-line value;
- false positives;
- source reliability;
- average opportunity lifetime;
- source lag;
- optimal source weighting.

---

## 20. Local User Interface

This is a personal tool. Keep the UI functional and fast rather than elaborate.

A local FastAPI-backed web interface is preferred.

Primary view should be a sortable +EV table.

Columns should include:

- EV%;
- candidate source;
- sport/league;
- event;
- market;
- side;
- line;
- candidate odds;
- fair odds;
- fair probability;
- number of consensus sources;
- age of candidate quote;
- age of oldest consensus quote;
- timestamp first detected.

Clicking an opportunity should expand into a full source ladder, e.g.:

```text
Tyrese Maxey O25.5 Points

BetMGM       +125   ← candidate
FanDuel      -110
DraftKings   -115
Fanatics     -108
BetRivers    -112
Pinnacle     -115
Circa        -110
Polymarket    53¢
Kalshi        54¢
Novig        -108
ProphetX     -111

Fair probability: 52.7%
Fair odds: -111
Candidate EV: +18.6%
Consensus sources used: 10
```

The UI must make it easy to answer:

1. What is the outlier?
2. What does the rest of the market say?
3. Which sources were included?
4. How fresh are the prices?
5. Why is this considered +EV?

---

## 21. Filters

Support configurable filters for:

- sportsbook/source;
- sport;
- league;
- market family;
- player props vs game lines;
- minimum EV;
- candidate odds range;
- number of consensus sources;
- freshness;
- live vs pregame;
- minimum liquidity for exchanges.

Persist user settings locally.

---

## 22. Alerts

After the basic scanner works, support local alerts.

Examples:

- terminal beep;
- desktop notification;
- optional Discord webhook;
- optional email later.

Alerts should trigger only on meaningful state changes, not every refresh.

Track opportunity identity so an unchanged +EV opportunity does not continuously re-alert.

Possible alert conditions:

- new opportunity exceeds threshold;
- EV crosses upward through threshold;
- candidate price improves materially;
- opportunity disappears.

---

## 23. Reliability and Error Handling

Every adapter must fail independently.

If FanDuel breaks, the rest of the scanner must keep running.

Use:

- reconnect logic;
- exponential backoff where appropriate;
- structured logging;
- timeouts;
- clear adapter health state;
- source-specific error counters.

The UI should expose source status:

```text
FanDuel      LIVE
DraftKings   LIVE
BetMGM       DEGRADED
Fanatics     DISCONNECTED
...
```

Never silently use a source whose feed is stale or broken.

---

## 24. Observability

Track metrics such as:

- last successful update by source;
- average update interval;
- reconnect count;
- number of active markets;
- unmatched market count;
- normalization failures;
- stale quote count;
- opportunities detected;
- median quote age at detection.

These do not require enterprise monitoring. A simple local health page and logs are enough.

---

## 25. Testing Strategy

Do not test adapters only against live endpoints.

For each source, save representative raw responses as fixtures.

Tests should cover:

1. parsing raw source data;
2. normalization;
3. event matching;
4. market matching;
5. odds conversion;
6. de-vigging;
7. consensus calculation;
8. leave-one-out behavior;
9. stale-source exclusion;
10. EV calculation;
11. duplicate update handling;
12. false-match prevention.

Create a replay mode that can feed recorded quote updates into the system deterministically.

This is especially important because live sportsbook endpoints may change.

---

## 26. Repository Layout

Prefer a clear structure such as:

```text
project/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── config/
│   ├── sources.yaml
│   ├── scanner.yaml
│   └── aliases.yaml
├── src/
│   ├── adapters/
│   │   ├── base.py
│   │   ├── fanduel.py
│   │   ├── draftkings.py
│   │   ├── betmgm.py
│   │   ├── fanatics.py
│   │   ├── betrivers.py
│   │   ├── pinnacle.py
│   │   ├── circa.py
│   │   ├── polymarket.py
│   │   ├── kalshi.py
│   │   ├── novig.py
│   │   └── prophetx.py
│   ├── models/
│   │   ├── event.py
│   │   ├── market.py
│   │   └── quote.py
│   ├── normalization/
│   │   ├── events.py
│   │   ├── entities.py
│   │   └── markets.py
│   ├── pricing/
│   │   ├── odds.py
│   │   ├── devig.py
│   │   ├── consensus.py
│   │   └── ev.py
│   ├── state/
│   │   ├── market_store.py
│   │   └── history.py
│   ├── scanner/
│   │   └── engine.py
│   ├── api/
│   │   └── app.py
│   └── main.py
├── tests/
│   ├── fixtures/
│   ├── adapters/
│   ├── normalization/
│   └── pricing/
└── data/
```

Do not create unnecessary abstractions before they are needed.

---

## 27. Implementation Order

Build the project in stages.

### Phase 0 — Skeleton

- repository layout;
- configuration system;
- typed canonical models;
- odds conversion utilities;
- adapter interface;
- logging.

### Phase 1 — Prove Big-Book Ingestion

The first major technical milestone is **not** the UI.

Prove that at least one major sportsbook can provide fresh structured odds to the local program through a permitted, maintainable path.

Preferred first targets:

1. FanDuel
2. DraftKings

The purpose is to validate the hard part of the project early.

Success means:

- fetch or receive current odds;
- parse them reliably;
- timestamp them;
- print several current markets;
- keep them updating over time.

Do not build a large frontend before this works.

### Phase 2 — Second and Third Sportsbooks

Add enough big-book coverage to validate cross-book matching, ideally:

- FanDuel;
- DraftKings;
- BetMGM or Fanatics.

Then implement canonical event and market matching.

### Phase 3 — Core +EV Engine

Implement:

- de-vigging;
- leave-one-out consensus;
- EV calculation;
- opportunity filters;
- terminal output.

### Phase 4 — Sharp / Exchange Sources

Add:

- Pinnacle;
- Circa;
- Polymarket;
- Kalshi;
- Novig;
- ProphetX.

These should strengthen fair-value estimation and market consensus.

### Phase 5 — Remaining Sportsbooks

Complete:

- BetMGM;
- Fanatics;
- BetRivers;
- any remaining incomplete adapter.

### Phase 6 — Local Dashboard

Build the sortable +EV interface and source ladder.

### Phase 7 — Historical Evaluation

Record opportunities and evaluate:

- whether flagged bets beat the closing market;
- source reliability;
- opportunity lifetime;
- false positives;
- potential source weighting improvements.

---

## 28. Go / No-Go Checkpoint

Before investing heavily in UI or advanced modeling, determine whether fresh data from the major sportsbooks is realistically obtainable in a stable and permitted way.

A useful checkpoint is:

> Can the program obtain and continuously update structured odds from at least three major sportsbook sources with acceptable latency and reliability?

If not, pause and reassess the ingestion strategy.

Do not hide data-access limitations behind architecture work.

---

## 29. Initial Scope of Markets

The architecture should be sport-agnostic, but implementation should start narrow.

Do not attempt every sport and every prop immediately.

For the first working version, select one sport and a small set of common markets, then expand only after matching is reliable.

Suggested market families to support structurally:

- moneyline;
- spread;
- total;
- player points/stat overs and unders;
- alternate lines later.

The exact first sport can be chosen based on what is live and easiest to test when development starts.

---

## 30. Pregame Before Live Betting

Focus on **pregame markets first**.

Live/in-play betting creates substantially harder requirements around:

- latency;
- suspended markets;
- rapid state changes;
- score/game-clock synchronization;
- transient lines;
- quote invalidation.

Do not add live betting until pregame ingestion, matching, and +EV calculations are reliable.

---

## 31. Configuration Over Hard-Coding

Anything that may change should be configurable, including:

- enabled sources;
- source weights;
- stale thresholds;
- minimum EV;
- minimum consensus source count;
- enabled sports;
- enabled markets;
- candidate sources;
- exchange liquidity thresholds;
- polling intervals where relevant.

Use YAML/TOML or equivalent human-readable local configuration.

---

## 32. Security and Secrets

Keep credentials out of source control.

Use environment variables or a local `.env` file excluded by `.gitignore`.

Never print secrets in logs.

Never commit:

- API keys;
- auth tokens;
- cookies;
- account credentials.

If a source requires user authentication, use only supported authentication flows.

---

## 33. Performance Priorities

Optimize in this order:

1. correctness;
2. freshness;
3. reliability;
4. transparency;
5. performance;
6. visual polish.

Do not introduce distributed systems, Redis, Kafka, Kubernetes, microservices, or cloud infrastructure unless local measurements demonstrate a real need.

This should initially run comfortably on one personal computer.

---

## 34. Claude Coding Behavior

When working on this repository, Claude should:

1. Make small, testable changes.
2. Explain unfamiliar networking/web concepts briefly when they matter.
3. Assume the owner has a solid undergraduate CS background but limited production web/data-engineering experience.
4. Prefer straightforward Python over clever abstractions.
5. Never fabricate undocumented endpoint details.
6. Ask for or identify the exact missing evidence when an adapter cannot be implemented reliably.
7. Write tests for parsers and pricing logic.
8. Preserve source isolation.
9. Avoid premature frontend work.
10. Keep the project runnable after each meaningful change.
11. Update documentation when architecture or source status changes.
12. Explicitly distinguish observed source behavior from assumptions.

When debugging source integrations, reason from captured requests/responses and logs rather than guessing.

---

## 35. Source Status Documentation

Maintain a file such as:

```text
docs/source_status.md
```

For each of the 11 sources record:

- adapter status;
- access method;
- official vs undocumented;
- streaming vs polling;
- authentication requirements;
- typical observed update frequency;
- supported sports/market types;
- known limitations;
- last verified date;
- sample fixture path.

Do not rely on memory for source behavior.

---

## 36. Definition of MVP Success

The MVP is successful when the following works locally:

1. At least three major sportsbooks provide continuously updating odds.
2. At least one sharp/reference or exchange source is integrated.
3. Equivalent markets are normalized and matched reliably.
4. The candidate source is excluded from its own fair-value calculation.
5. Stale quotes are excluded.
6. Fair probability and EV are calculated correctly.
7. The scanner displays a sorted list of +EV outliers.
8. Each opportunity can be expanded to show every source price used in the calculation.
9. A user can understand exactly why an opportunity was flagged.
10. The process can run for hours without one broken adapter crashing the entire application.

---

## 37. Example Desired Output

The end product should feel roughly like this:

```text
+EV SCANNER                                      Updated: 0.4s ago

EV      SOURCE      MARKET                         PRICE    FAIR     SOURCES
--------------------------------------------------------------------------------
+8.7%   BetMGM      Maxey O25.5 Points             +125     -108     8
+6.1%   Fanatics    Knicks -3.5                    +105     -112     7
+4.8%   BetRivers   Player X U7.5 Rebounds         +115     -102     9
```

Expanded opportunity:

```text
Maxey O25.5 Points

Candidate
BetMGM        +125      age 0.7s

Consensus
FanDuel       -110      age 0.5s
DraftKings    -115      age 0.6s
Fanatics      -108      age 0.8s
BetRivers     -112      age 0.9s
Pinnacle      -115      age 0.4s
Circa         -110      age 1.1s
Polymarket     53¢      age 0.3s
Kalshi         54¢      age 0.2s

Fair probability: 52.4%
Fair odds: -110
BetMGM EV: +17.9%
```

Numbers above are illustrative only.

---

## 38. Final Design Principle

The core competitive advantage of this personal tool is not a sophisticated sports model.

It is:

> **Fast, correct, transparent comparison of the same market across the 11 sources I care about.**

The project should therefore spend most engineering effort on:

- data acquisition;
- freshness;
- normalization;
- exact market matching;
- robust consensus;
- clear visibility into source disagreement.

If a feature does not materially improve one of those goals, it is probably not important for the initial project.
