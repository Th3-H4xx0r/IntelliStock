# Kalshi Prediction-Markets Trading — Design Document

**Status:** Draft v1 · Pre-build
**Date:** 2026-06-22
**Scope:** Add fully-autonomous Kalshi soccer event-contract trading to IntelliStock as **lean, Kalshi-only instances**, surfaced through a dedicated web tab, a dashboard portfolio card, and a first-class mobile tab. One overarching design; built as three sub-projects (A → B → C) in one continuous push.

> Adapts the external "Kalshi Soccer Trading System" draft to IntelliStock's real architecture. The thesis is unchanged — **de-vig the sharpest book (Pinnacle), compare to Kalshi net of fees, trade the edge, and judge success by closing-line value (CLV), not backtest ROI** — but nearly all the infrastructure the original plan said to build already exists here and is reused instead.

---

## 0. TL;DR

A Kalshi bot is just another IntelliStock **instance**: an autonomous loop bound to a **brokerage** (a Kalshi account), started/stopped through the existing instance lifecycle, with the existing **kill switch** as its stop. What's new is the *decision engine* (sharp-odds → de-vig → edge → ¼-Kelly → order) and the *Kalshi client* (RSA-PSS-signed v2 REST + WebSocket). Everything cross-cutting — credentials, kill switch, scheduler, notifications, RethinkDB, telemetry→cards, backtest infra, and the Vue/Flutter shells — is reused.

Two Kalshi accounts are first-class from day one: **Kalshi-Demo** (`demo-api.kalshi.co`, paper) and **Kalshi-Live** (real money, funded, KYC'd). Each is its own brokerage and can be bound to a different instance.

**Single success metric:** consistently positive CLV over several hundred logged fixtures. If paper trading doesn't beat Kalshi's close, the project stops at sub-project B — no scaling of live capital.

---

## 1. Goals / non-goals

**Goals**
- Connect Kalshi accounts (demo + live) as brokerages; store the RSA key securely.
- A lean, isolated Kalshi instance engine that trades soccer event contracts fully autonomously and can be stopped at any time.
- Sharp-odds-anchored fair value (de-vig Pinnacle), edge detection net of live Kalshi fees, ¼-Kelly sizing under hard risk caps.
- Surface portfolio, edge, positions, settlement, CLV, and risk on a **web Kalshi tab**, a **dashboard portfolio card**, and a **dedicated mobile Kalshi tab + screen**.
- Paper-first validation with CLV logging; gate live scaling on proven CLV.
- The 9 product features in §10.

**Non-goals (v1)**
- No ML price model. The sharp market is the model; Elo/Dixon-Coles is fallback only.
- No market making (deferred; requires a proven faster-than-market fair value).
- No human approve-to-trade step — instances trade autonomously (user decision). Safety comes from risk caps + kill switch, not a human gate.
- No cross-market (Polymarket) arbitrage in v1 (jurisdiction-limited; deprioritized).
- No multi-sport content in v1, but the data model is **sport-agnostic** so NBA/NFL is later config, not migration (feature #9).

---

## 2. Reuse map — what IntelliStock already provides

The original plan assumed greenfield (Supabase, Streamlit, APScheduler, fork a bot). None of that is needed.

| Original plan said build… | IntelliStock already has | Decision |
|---|---|---|
| Postgres / Supabase | **RethinkDB** (all instance/trade/telemetry data) | New `kalshi_*` tables in RethinkDB; drop Supabase |
| Streamlit dashboard | Vue frontend (`NexusView`, `DashboardView`, `PortfolioChart.vue`) + Flutter mobile + the nexus telemetry→cards pipeline | Surface through these; no Streamlit |
| APScheduler | `backend/scheduler.py` | Reuse for daily fixture scan + pre-kickoff polls (respect the OddsPapi budget) |
| Kill switch ("non-negotiable") | `live_kill_switch.py` `halt_live_trading(reason, cancel_open_orders, instance_id)` — **instance-scoped**, cancels orders per linked brokerage | Reuse; add a Kalshi branch to the per-brokerage cancel path |
| Fork a trading bot for auth/WS/risk | `credential_service.py`/`secret_store.py`, risk-cap patterns, `broker_adapters/_wal.py` (order WAL), instance lifecycle | Reuse; only the **RSA-PSS signer + Kalshi REST/WS** is net-new |
| Paper trading | `portfolio_emulator.py` | Reuse for the demo/paper account path |
| Backtester | `backtests/` + `backtest_*.py` | Reuse for the order-book replay sim (feature #8) |
| (absent) alerting | `notifications.py` + Discord + iOS-push routing | +EV flags / fills / settlement / risk-cap / kill-switch alerts |
| (absent) UI surfaces | `BrokeragesView`, `InstancesView`, `LiveTradingView`, mobile `dashboard`/`nexus`/`instances` features, `AppShell` 5-tab bar | New Kalshi brokerage tab, web Kalshi tab, dashboard card, mobile Kalshi tab |

**Genuinely new** (must be built): Kalshi v2 REST/WS client + RSA-PSS signing; sharp-odds ingestion (OddsPapi); fixtures/stats ingestion (football-data.org + `soccerdata`); de-vig math (Power/Shin); the edge detector; team-name crosswalk; CLV as a metric; the lean Kalshi instance engine.

---

## 3. Architecture

### 3.1 Integration model — lean Kalshi instance (chosen)

Kalshi instances are a **distinct, lean instance type with their own trimmed-down engine**, *not* the equities `broker.py` live-loop. They share only cross-cutting infrastructure. This isolates the new real-money trade path from the equities one — a change to Kalshi cannot break Alpaca/Robinhood, and vice-versa (honors the standing "keep additions off the equities trade path" constraint).

Rejected alternative: running Kalshi as a strategy inside the full equities loop. It would mechanically reuse more code but drags stock-specific data/strategy machinery (OHLCV bars, indicators, market-hours, PDT) into the Kalshi path and couples the two trade paths. Not worth it.

The equities `BrokerAdapter` ABC (`broker_adapters/base.py`) is intentionally **not** implemented for Kalshi. That ABC is equities-shaped (`submit_order(symbol, side, qty…)`, `PositionDTO(symbol, qty, avg_entry_price)`, `_positions: dict[str,float]`). Kalshi gets a native contract model instead (§3.3). The lean engine still reuses the order **WAL**, the **kill switch**, and the **credential service** directly as services.

### 3.2 Lean Kalshi instance loop

```
scheduler tick / instance run (reused lifecycle, runCommand flag)
        │
        ▼  check runCommand + kill switch (reused)   ──► halt → exit
        ▼
  odds ingest (OddsPapi: Pinnacle + Kalshi)  ──cache──►  kalshi_odds_snapshots
        ▼
  de-vig → fair P(home/draw/away)  (Power; Shin fallback)
        ▼
  edge = fair − (kalshi_implied + live_fee)   filter: edge > threshold
        ▼
  ¼-Kelly sizing + risk caps (per-market, per-league, daily-loss, max-exposure)
        ▼
  KalshiClient.submit_order (RSA-PSS v2, limit)  ──WAL──►  kalshi_orders
        ▼
  portfolio snapshot + telemetry + notify (reused)  ──►  kalshi_portfolio_snapshots, cards, Discord/push
```

No bar fetching, indicators, market-hours, PDT, or buying-power equities code. Each iteration is bounded; the loop polls `runCommand` and the kill switch like equities instances do.

### 3.3 Components (modules)

New backend package `backend/kalshi/`:

| Module | Responsibility |
|---|---|
| `kalshi/client.py` | Kalshi v2 REST + WebSocket; RSA-PSS SHA256 request signing (key id + private key, three `KALSHI-ACCESS-*` headers, sign path without query); demo vs prod base URL. |
| `kalshi/models.py` | Native DTOs: `KalshiMarket`, `KalshiContractPosition` (market_ticker, side YES/NO, contracts, avg_price_cents), `KalshiOrderRef`, `KalshiFill`, `KalshiBalance`. |
| `kalshi/engine.py` | The lean instance loop (§3.2): orchestrates ingest → fair value → edge → sizing → execute → snapshot. |
| `kalshi/devig.py` | Power + Shin de-vig for 3-way markets; proportional baseline for sanity only. |
| `kalshi/fair_value.py` | Fair-value from de-vig; optional Elo/Dixon-Coles fallback blend for thin coverage. |
| `kalshi/edge.py` | Edge calc with **live** fee pull; threshold filter. |
| `kalshi/risk.py` | ¼-Kelly + caps (per-market, per-league/category, daily-loss, max-exposure); pre-trade enforcement. |
| `kalshi/ingest_odds.py` | OddsPapi client (key as query param), budget guard, caching → `kalshi_odds_snapshots`. |
| `kalshi/ingest_fixtures.py` | football-data.org schedule/results backbone + `soccerdata` (xG/Elo) → `kalshi_fixtures`, `team_stats`. |
| `kalshi/normalize.py` | Team-ID crosswalk across sources (mirror `soccerdata`'s `teamname_replacements`). |
| `kalshi/clv.py` | Log entry price vs Kalshi close; compute CLV per fixture/league/bet-type. |
| `kalshi/telemetry.py` | Pure helpers that shape rows into card payloads (mirrors `nexus_telemetry.py`); unit-tested. |
| `kalshi/replay.py` | Order-book + odds replay simulator over recorded snapshots (feature #8). |

Reused services: `credential_service`, `secret_store`, `live_kill_switch.halt_live_trading`, `broker_adapters/_wal.py`, `scheduler`, `notifications` + `discord_sender` + `apns_sender`, `portfolio_emulator` (demo/paper accounting).

### 3.4 Brokerage type

Add `brokerage_type: 'kalshi'` to the `Brokerages` model (joins `alpaca`, `robinhood`). New `BrokeragesView` tab + form: account name, **API key id**, **RSA private key** (PEM, stored via `secret_store`), and an **environment** toggle (`demo` | `live`) that selects the base URL. `test-kalshi` probe endpoint validates the key against `/portfolio/balance`.

### 3.5 Kill-switch integration

`halt_live_trading(instance_id=…)` already flips `runCommand=False` and cancels orders on the instance's linked brokerage. Add a Kalshi branch to the per-brokerage cancel step: when the linked brokerage is `kalshi`, call `KalshiClient.cancel_all_open_orders()`. Manual (operator) and automatic (risk anomaly) halts both flow through this unchanged.

---

## 4. Data model (RethinkDB)

Sport-agnostic where it costs nothing. New tables (prefix `kalshi_` / `sports_`):

- `kalshi_brokerages` — *(reuse existing `Brokerages` with `brokerage_type='kalshi'`)*; fields: key_id, private_key_ref (secret_store), environment.
- `sports_fixtures` — fixture id, sport, league, home, away, kickoff_utc, status, result, source ids (crosswalked).
- `kalshi_markets` — market_ticker, fixture id, contract type (home/draw/away/over-under…), settlement rule, fee schedule snapshot.
- `kalshi_odds_snapshots` — **timestamped** rows per book per fixture (Pinnacle, Kalshi, …); implied probs; the de-vig reads only rows with `ts < kickoff` (leakage guard).
- `kalshi_edges` — every flagged opportunity: fixture, market, fair prob, kalshi implied, fee, edge, threshold, decision, sizing.
- `kalshi_orders` — WAL-backed order log (client_order_id, broker_order_id, market, side, contracts, limit_cents, status, fills).
- `kalshi_positions` — open contract positions (market, side, contracts, avg_price_cents, current_price, settlement_eta).
- `kalshi_portfolio_snapshots` — timestamped account value for the equity curve (feeds `PortfolioChart.vue`).
- `kalshi_clv_log` — entry price vs close, CLV, per fixture/league/bet-type.
- `team_stats` — xG / Elo cache from `soccerdata`.
- `kalshi_scan_budget` — OddsPapi request counter + window (budget guard, feature #5).

**Timestamp discipline** is the #1 anti-leakage rule: the fair-value engine may read only data available before kickoff.

---

## 5. Fair value, edge, sizing, risk

- **De-vig:** Power method for 3-way soccer (solve `k` s.t. `Σ qᵢᵏ = 1`, `pᵢ = qᵢᵏ`); Shin as cross-check. Proportional normalization is wrong for 3-way and used only as a sanity baseline.
- **Edge:** `edge = fair_prob − (yes_ask/100 + fee_as_prob)`. **Fees are pulled live** from Kalshi's schedule (cached, refreshed) and baked into every edge calc *and* backtest — never hardcoded.
- **Sizing:** `stake_fraction = 0.25 × edge / (1 − fair_prob)` (binary-contract ¼-Kelly off the *measured* edge), then clamped by risk caps.
- **Risk caps (pre-trade, hard):** edge threshold (start 3–4%), max contracts/market, max open exposure (% bankroll), per-league/category cap, daily-loss hard stop (halts the instance via the kill switch), kill switch (manual + auto on anomaly). Risk control matters more than signal — enforce before every order.

---

## 6. Execution

- **Auth:** v2 RSA-PSS SHA256 signing; sign the path without query params; three `KALSHI-ACCESS-*` headers. Build on `/trade-api/v2/...`.
- **Transport:** WebSocket for live prices/book/fills; REST for orders + portfolio.
- **Order policy:** limit orders preferred (avoid slippage); position limits enforced pre-trade; WAL every order (reuse `_wal.py`) for crash-safe reconciliation.
- **Environments:** `Kalshi-Demo` → `demo-api.kalshi.co`; `Kalshi-Live` → production. Demo is exercised end-to-end before any live order.
- **Autonomy:** fully autonomous per instance; stop = kill switch / `runCommand=False`.

---

## 7. Sub-projects (one continuous push, A → B → C)

**A · Foundation + monitoring.** Kalshi brokerage type + `kalshi/client.py` + `models.py`; connect demo + live; read-only portfolio/positions/fills/balance → `kalshi_*` tables → API endpoints → **web Kalshi tab + dashboard card + mobile Kalshi tab/screen**. End state: connect Kalshi, see both accounts everywhere. No automated trading yet (orders behind a hard gate).

**B · Signal/edge engine (paper).** Odds + fixtures ingestion, de-vig, fair value, edge detector, CLV logging, telemetry cards (Edge Radar, CLV Scorecard, Why-this-trade, Steam). Runs daily against live fixtures, logs hypothetical bets + CLV, collects order-book snapshots for the simulator. End state: daily +EV flags + CLV tracking, no money.

**C · Autonomous execution.** Wire risk manager + ¼-Kelly + the lean engine loop to **demo first**, then live behind the CLV gate; kill switch + WAL + reconciliation; backtest/replay sim. End state: instances trade autonomously under caps, stoppable, validated.

**Validation gate between B and C:** after several hundred logged fixtures, is CLV consistently positive? No CLV → stop at B; do not scale live capital.

---

## 8. API endpoints (FastAPI, brokerage-scoped, mostly read-only)

Mirror the existing brokerage-scoped nexus endpoints. All read-only except the instance start/stop (which reuse existing instance endpoints) and `test-kalshi`:

- `GET /kalshi/portfolio` — account value, day P&L, equity-curve series (ranges 1D/1W/1M/ALL).
- `GET /kalshi/positions` — open contract positions + settlement ETA.
- `GET /kalshi/edges` — Edge Radar (top mispriced now).
- `GET /kalshi/fills` — recent fills.
- `GET /kalshi/clv` — CLV scorecard (overall + per league/bet-type).
- `GET /kalshi/settlement` — settlement tracker (countdowns, likely outcome).
- `GET /kalshi/risk` — bankroll, exposure vs caps, daily-loss meter, per-league allocation.
- `GET /kalshi/rationale/:positionId` — why-this-trade math.
- `GET /kalshi/steam/:fixtureId` — sharp-line movement series.
- `GET /kalshi/scan-budget` — OddsPapi quota meter.
- `POST /brokerages/test-kalshi` — validate key against `/portfolio/balance`.

---

## 9. UI (locked via visual brainstorming)

### 9.1 Web — dedicated `⚽ Kalshi` nav tab
Card-feed layout. **Portfolio value chart is the hero** — half width on desktop (shares row 1 with an account-KPI panel), full width on mobile-web; account value + `1D/1W/1M/ALL` range toggle (reuses `PortfolioChart.vue`). Cards below: Edge Radar, Open positions, CLV-per-league, Settlement tracker, Bankroll & risk meters, Why-this-trade, Sharp-line steam, Recent fills, Kalshi instances (full-width start/stop), Odds-budget guard, Upcoming scan queue. Account selector (demo/live) + KILL button in the header bar.

### 9.2 Web — dashboard portfolio card
One compact card in a new "Kalshi" section on `DashboardView`: portfolio value + sparkline + day P&L + "N instances / M positions", tap → Kalshi tab.

### 9.3 Mobile — dedicated `⚽ Kalshi` bottom tab (its own screen)
Promote Kalshi to a **first-class bottom-bar tab, positioned right after Dashboard** (`Dashboard · ⚽ Kalshi · Instances · Strategies · More`); **Backtests moves into the More sheet**. Kalshi also appears in the More sheet, and the dashboard keeps a compact shortcut card. The Kalshi screen is a full-width stacked card feed mirroring the web tab: portfolio chart + ranges, CLV/win KPIs, Edge Radar, positions w/ settlement countdowns, instance start/stop, KILL in the header; scrolls to Settlement/Risk/Steam.

### 9.4 Mobile — live trade detail screen
Tap a position → real-time P&L, settlement countdown, the "why this trade" edge math, sharp-line steam, manual sell/reduce. Push notification fires on settlement.

Mobile changes touch `AppShell` (`StatefulShellRoute` branches + `_destinations`) and `more_sheet.dart`; new `mobile/lib/features/kalshi/` feature (DTOs, providers, screens, cards). Providers follow the existing pattern but must avoid the keep-alive stale-empty trap (add pull-to-refresh invalidate).

---

## 10. Features (all 9 included)

1. **Edge Radar** — live top mispriced contracts, sorted by edge, with kickoff countdown. (telemetry card → web + dashboard + mobile)
2. **CLV Scorecard** — the success metric, per league + bet-type over time. (reuses nexus outcome-scorecard card)
3. **Settlement Tracker** — open contracts → resolution countdown → realized P&L, push on settle. (reuses notification routing)
4. **Bankroll & Risk panel** — exposure vs caps, daily-loss meter, per-league allocation, mobile **kill-switch button**.
5. **Odds-budget guard** — OddsPapi quota meter + adaptive scan throttle so a runaway scan can't blow the ~250-req/month budget.
6. **Why-this-trade card** — fair prob, Kalshi implied, edge, fee, ¼-Kelly math, sharp anchor. (reuses nexus bot-rationale card)
7. **Sharp-line steam tracker** — Pinnacle movement vs entry; are you on the right side of the line move.
8. **Order-book replay playback** — reuse `BacktestPlaybackView` to replay recorded odds + Kalshi book and see what the bot would have done. *(the one deferrable item if scope tightens)*
9. **Sport-agnostic data model** — soccer-first content; schema generalizes so NBA/NFL is later config, not migration.

---

## 11. Notifications (reuse routing)

Per-category routing to Discord + iOS push: new +EV flag (optional, can be noisy), order fill, settlement result, daily-loss-cap hit, risk-cap block, kill-switch trip, instance halt/exit, odds-budget low. Reuses `notifications.py` + `notification_types.py` categories.

---

## 12. Roadmap & gates

- **Phase 0 — Setup & verify.** Connect demo + live brokerages. **Verify the OddsPapi free tier** actually returns Pinnacle + Kalshi for target leagues, and **pull Kalshi's current fee schedule**. Provision `kalshi_*` tables. *(Jurisdiction is already resolved — user has a funded US account.)*
- **Phase 1 (= sub-project B) — Data + fair value + edge, no money.** Daily flags + CLV log; start collecting book snapshots.
- **Phase 2 — Validate (gate).** Several hundred fixtures; CLV consistently positive? No → stop.
- **Phase 3 — Paper on demo (part of C).** Full loop wired to `demo-api.kalshi.co`; confirm execution, sizing, caps, kill switch.
- **Phase 4 — Live, small (gate: Phase 2 green).** Minimum stakes; compare live CLV/fills to paper; scale only if they match.
- **Phase 5+ — Optional.** Market making on thin markets (only with proven fast fair value); more leagues/sports.

---

## 13. Risk register / honest failure modes

| Risk | Severity | Mitigation |
|---|---|---|
| Kalshi prices already efficient (esp. marquee) | High | Target thin/lower-division markets; CLV gate kills the project if no edge |
| OddsPapi free tier insufficient / changes | High | Verify in Phase 0; cache hard; budget guard (feature #5); football-data.org fallback for fixtures |
| Scraper breakage (`soccerdata`) | Medium | Official fixtures fallback; multi-source; caching |
| Backtest leakage inflates results | High | Strict `ts < kickoff` reads; forward CLV is the true metric |
| Fees erase the edge | High | Live fee pull baked into edge + backtest |
| ¼-Kelly still over-sizes a mis-estimated edge | High | Hard caps, daily-loss stop, conservative threshold |
| Real-money bug on the live account | **Critical** | Lean engine isolated from equities path; demo-first; WAL + reconciliation; instance-scoped kill switch; risk caps pre-trade |
| RSA signing / key handling | High | Store PEM via `secret_store`; never log; sign path without query (a common 401 cause) |
| Mobile keep-alive providers cache empty (known repo gotcha) | Medium | Pull-to-refresh invalidate on Kalshi providers; don't keep-alive empty |
| Latency loss to sharps on news | Medium | Trade stale lines, not breaking news; don't compete on speed |

---

## 14. Testing

- **Backend pure helpers** (`kalshi/devig.py`, `edge.py`, `risk.py`, `clv.py`, `telemetry.py`, `normalize.py`) unit-tested in `backend/tests/` (mirrors `test_nexus_telemetry.py`). De-vig has known-answer fixtures; edge tests include fee handling and a 0%-fee promo case.
- **Client** tested against recorded fixtures + the demo API; signing has a vector test.
- **Engine** dry-run test: given canned odds + Kalshi prices, asserts the orders it *would* place (no network).
- **Replay sim** validates against recorded book snapshots; documents its known limitations (no queue position, latency, market impact, partial-fill dynamics — it's a logic check, not a forecast).
- **Mobile**: `flutter test test/features/kalshi/` + golden tests for the cards; `flutter analyze lib/features/kalshi`.
- **Web**: component render + the new endpoints' contract.

---

## 15. Open decisions / to verify in Phase 0

1. **OddsPapi free tier** — confirm it returns Pinnacle + Kalshi for the target leagues within ~250 req/month (load-bearing; verify before building B on it).
2. **Kalshi fee schedule** — pull current numbers; confirm any 0%-fee sports promos.
3. **Target leagues** — which thin leagues first (Pinnacle coverage exists but Kalshi may lag). Default: lower divisions over marquee games.
4. **Bumped mobile tab** — decided: Backtests → More (so the bar is `Dashboard · ⚽ Kalshi · Instances · Strategies · More`). Flip to Strategies → More instead if preferred — low-stakes, reversible.
5. **Kalshi private key at rest** — confirm `secret_store` is the right home and the encryption posture matches the real-money risk.

---

*Not financial advice. Event-contract / prediction-market trading involves substantial risk including total loss of capital. Verify third-party API terms, Kalshi's current fee schedule, and legal eligibility before live trading.*
