# Dashboard & Stock-Screen Feature Expansion — Design

**Date:** 2026-06-18
**Status:** Approved (design); ready for implementation planning (Phase 1 first)

## Goal

Add three categories of features across the mobile dashboard and stock-detail
screens — **bot transparency**, **portfolio analytics & risk**, and
**glanceable/live polish** — reusing existing backend data wherever possible.
Delivered in two phases so Phase 1 ships value with (almost) no backend work.

## Non-Goals

- Deep stock-research features (related-stocks graph, news/sentiment, options,
  analyst data) — explicitly deferred by the user this round.
- Any change to trading behavior. All additions are read-only / telemetry.

## Architecture & Conventions (follow existing patterns)

- **State:** Riverpod `FutureProvider`/`AutoDisposeFamilyAsyncNotifier` families.
  Anything "live" uses `IntervalPoller` + `appLifecycleProvider` (pause in
  background), mirroring `AccountHoldingsNotifier` / `StockHistoryNotifier`.
- **Networking:** repository methods on `dashboardRepository` / `liveRepository`
  calling `ApiClient.get`; new typed models in each feature's `data/` dir.
- **UI:** new dashboard sections are `GlassCard`s in `dashboard_screen.dart`;
  reuse `AppColors` / `AppTextStyles`, `Skeleton`, the mini-sparkline painter,
  and the `/stock/:symbol` route (`StockScreenArgs`) for tap-through.
- **Resilience:** every new provider is never-throw → empty/hidden card on
  error, skeleton while loading. No card may block the dashboard. (Same
  convention as the bot-activity work.)

## Cross-Cutting Decision: brokerage-scoped wrappers

`/trends` and `/discovered` are **per-instance** (they take `instance_id`). The
dashboard is **per-brokerage-account**. To stay consistent with the `/orders`
and `/bot-activity` endpoints (which resolve the instance behind an account via
`_widget_brokerage_id` + the instances list), add two thin wrapper endpoints:

- `GET /brokerages/{id}/discovered` → resolves the account's instance, delegates
  to `action_list_discovered_stocks(conn, instance_id, status="active")`.
- `GET /brokerages/{id}/trends` → delegates to
  `action_list_trends(conn, instance_id, status="active")`.

This keeps every mobile call shaped `/brokerages/{id}/…`. (Alternative —
calling global `/discovered` + `/trends` with no filter — is simpler but mixes
instances when more than one account is live; rejected for correctness.)

---

## Phase 1 — high impact, (almost) all existing data

### 1. Discovered Opportunities (dashboard card)
- **Shows:** horizontally-scrollable cards — ticker, the flag reason, "Xd ago";
  tap → stock screen; dismiss (✕) → `DELETE /discovered/{instance}/{ticker}`.
- **Data:** `GET /brokerages/{id}/discovered` → `{stocks:[{id:"inst:TICKER",
  instance_id, ticker, status, discovered_at, …reason/score}]}`.
- **Model:** `DiscoveredStock { ticker, reason, discoveredAt, … }` (map exact
  reason/score field names at plan time from `_NEXUS_DISCOVERED_TABLE` writer).
- **Placement:** below the holdings list, above the Services section.

### 2. Market Trends (dashboard card)
- **Shows:** per trend — title, a strength bar, the spanning symbols as
  tappable chips (→ stock screen).
- **Data:** `GET /brokerages/{id}/trends` → `{trends:[{id, instance_id, status,
  updated_at, …title/description/symbols/strength}]}`.
- **Model:** `MarketTrend { id, title, symbols, strength, … }`.
- **Placement:** directly under Discovered Opportunities.

### 3. Sector Allocation (dashboard card)
- **Shows:** horizontal stacked bar (or mini-donut) of % of invested value by
  sector, with a legend (sector, $, %).
- **Data:** holdings (`/brokerages/{id}/positions`) + sector from cached
  `/symbols/{s}/info`. Aggregated client-side. (A backend
  `/brokerages/{id}/allocation` endpoint is a later optimization to avoid N
  info calls; for typical holding counts the cached per-symbol info is fine.)
- **Pure helper (tested):** `aggregateBySector(positions, sectorOf) → List<
  SectorSlice{sector, value, pct}>`.

### 4. Concentration / Diversification meter (dashboard tile)
- **Shows:** largest-holding %, position count, and a 0–100 "diversification
  score" derived from the Herfindahl-Hirschman Index, color-graded.
- **Data:** positions only.
- **Pure helper (tested):** `concentration(positions) → {topWeight, count, hhi,
  score}` where `hhi = Σ wᵢ²` over value weights and `score = round((1−hhi)*100)`.

### 5. Today's Movers (dashboard strip)
- **Shows:** your holdings ranked by today's % move — a compact gainer/loser
  chip row (e.g. top 3 up / top 3 down) with the % and a tiny sparkline.
- **Data:** reuses the 1D `holdingsSparklinesProvider` + positions already
  fetched on the dashboard (since-midnight ratio = `last/first`).
- **Pure helper (tested):** `todaysMovers(positions, sparks) →
  List<Mover{symbol, pct}>` sorted by pct.

### 6. Day P&L tile
- **Shows:** the selected account's *today* $ and % change, prominent near the
  hero value (distinct from the existing range-based change).
- **Data:** 1D portfolio-history (already fetched for the chart):
  `today = currentValue − openValue`.

---

## Phase 2 — light backend / compute

### 7. Agent Activity Timeline (dashboard card)
- **Shows:** recent agent runs — "tested N strategies", best result, next run.
- **Data:** `/agent/runs` (existing `AgentRunsPage` model) + `/agent/best`.

### 8. Risk Card
- **Shows:** annualized volatility + max drawdown (computed client-side from the
  equity curve) and Sharpe ratio.
- **Data:** equity curve (have it) for vol/drawdown; **Sharpe needs a small
  backend metric** endpoint (returns/risk-free) — flagged as the backend bit.
- **Pure helpers (tested):** `maxDrawdown(values)`, `volatility(returns)`.

### 9. Dividends Received (position + dashboard)
- **Shows:** YTD dividend income, overall and per position.
- **Data:** **backend wiring** of the positions endpoint's dividend fields
  (currently not surfaced) → mobile model addition.

### 10. Watchlist (new sub-feature)
- **Shows:** tracked non-held tickers with mini-sparklines → stock screen; add
  from the stock screen, remove from the list.
- **Data:** **new backend** — a `Watchlist` table + `GET/POST/DELETE
  /watchlist` (per user/instance). Own spec→plan if it grows.

### 11. Market Open/Closed + "live" chip
- **Shows:** a small "Markets open · live" / "Closed" chip, pairing with the new
  pulsing end-dot.
- **Data:** client-side US market-hours (with holiday list) — no backend.

---

## Testing Strategy

- **Pure helpers get unit tests** (Flutter `test/`): `concentration`/HHI math,
  `aggregateBySector`, `todaysMovers`, `maxDrawdown`, `volatility`.
- **Providers** follow the established never-throw pattern (return empty on
  error); covered by the helper tests + manual on-device verification.
- **Backend wrappers** (`/brokerages/{id}/discovered|trends`) mirror the tested
  `/orders` resolution path; add an action-level test if logic is non-trivial.

## Rollout

One spec, two phases. Recommended: an implementation plan for **Phase 1 first**
(no/▪minimal backend → ships immediately to device), then a separate plan for
Phase 2. Backend additions (wrappers, Sharpe, dividends, watchlist) require the
user's Dokploy deploy of `main`.
