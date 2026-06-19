# Nexus Strategy Dashboard Cards — Design

**Date:** 2026-06-18
**Status:** Approved (brainstorming complete)
**Author:** Pranav + Claude

## Summary

Surface the `graph_nexus_analysis` strategy's live internal state on the mobile
dashboard as a new **"Strategy"** section of read-only telemetry cards. The bot
already tracks market trends, a backfill queue of pending buys, discovered
stocks, per-symbol rationale, signal outcomes, and a momentum watchlist — but
almost none of it is visible in the app. This feature exposes that state through
new read-only backend endpoints and seven Flutter cards.

## Goals

- Show **active market trends** the strategy currently considers alive, plus a
  few **recently-ended** trends, in one compact card.
- Show the cards the user explicitly asked for: **backfill queue** and
  **momentum watchlist** the bot is actively monitoring.
- Add brainstormed companions backed by already-persisted data: **discovered
  stocks**, **reversal watch**, **bot rationale ("why owned")**, and an
  **outcome scorecard**.
- Match the existing dashboard card pattern exactly (Riverpod provider → repo →
  endpoint; `GlassCard(frosted: true)`; self-hide when empty).

## Non-Goals

- No changes to the trade/decision path. Every backend addition is additive,
  read-only telemetry.
- No live Neo4j graph traversal from the API (too expensive; no persisted hop
  chain exists — see Bot Rationale below).
- No new navigation surface beyond a section on the existing dashboard.
- No backtest/strategy-tuning changes.

## Constraints (real-money guardrails — standing)

- The live instance is `alpaca-main` (Alpaca LIVE, Strategies doc 179). Never
  execute trades or move money.
- Backend edits must be additive, read-only, never on the trade path.
- The single strategy-file edit (momentum watchlist summary, card #7) MUST be
  preceded by `gitnexus_impact({target, direction:"upstream"})`; proceed only if
  risk is LOW and `affected_processes` does not include the trade/decision path.
  Report the blast radius before editing. Run `gitnexus_detect_changes()` before
  commit. The GitNexus index is stale — trust `affected_processes: []` + risk
  level over its line-drift symbol attribution.
- Mobile ships via `cd mobile && scripts/deploy.sh 1`. Backend is NOT
  auto-deployed; the operator redeploys `main` to Dockploy.

## Verified data sources

All findings below were confirmed by reading the code, not assumed.

| Data | Storage | Persisted? | Existing endpoint |
|------|---------|------------|-------------------|
| Trends (active/weakening/ended) | `GraphNexusMarketTrends` table | yes | `/trends?status=`, `/brokerages/{id}/trends` (hard-coded active) |
| Backfill queue | `NexusStrategyCache._backfill_queue` | **yes** | none |
| Discovered stocks | `GraphNexusDiscoveredStocks` table | yes | **`/brokerages/{id}/discovered` exists** |
| Trade rationale ("why") | `GraphNexusTradeContexts.reason` (+ `features.dominant_event_type`) | yes | none |
| Signal outcomes | `GraphNexusOutcomes` table (confirm exact table name in code at impl time — may be `GraphNexusTradeOutcomes`) | yes | none |
| Momentum ranked top | `NexusStrategyCache._momentum_ranked_top` | yes | `/brokerages/{id}/nexus-momentum` (existing card) |
| Momentum **watchlist** (broad set) | `NexusStrategyCache._momentum_watchlist` | **NO — regenerated in-process each run** | none |

Key gotchas the design must respect:

1. **The momentum watchlist is not persisted.** A read-only API reading
   `load_strategy_cache_from_db(...)` will not see `_momentum_watchlist`. Card #7
   therefore requires the strategy to write a small persisted summary (the one
   strategy edit). It is also gated by `momentum_watchlist_enabled` (defaults
   off) — the card self-hides when the summary is empty.
2. **"Why owned" has no persisted Neo4j hop-chain.** The richest persisted "why"
   is `GraphNexusTradeContexts.reason` (~1500-char LLM explanation) +
   `dominant_event_type`, plus discovery `source` / `source_ticker` (e.g.
   "propagation from NVDA"). The card surfaces that — not a live graph walk.
3. **`/brokerages/{id}/trends` is hard-coded to active.** It must be extended to
   accept `status` and `limit` query params (defaults preserve current behavior).
4. Instance resolution reuses `_resolve_instance_for_brokerage(conn, brokerage_id)`
   (`backend/api/main.py:3781`). Cache reads use
   `load_strategy_cache_from_db(conn, _r_auth, iid, "graph_nexus_analysis")`.

## Architecture

**Approach: granular endpoints (one per concern), with the one sensible
grouping that trends feed three cards from a single query.** This matches the
existing pattern (`/nexus-momentum`, `/holding-opens`, `/bot-activity`,
`/discovered`): each card self-hides and fails independently; no fat coupling.

```
Flutter card  ──watch──▶  Riverpod provider  ──▶  repo method  ──HTTP──▶  endpoint
(strategy_section.dart)   (nexus_strategy_        (dashboard_              (api/main.py)
                           controller.dart)         repository.dart)
```

Every endpoint: resolve brokerage → instance, read table/cache, return
`{}`/`[]` (never 500) when the instance has no nexus data, so cards self-hide on
non-nexus brokerages exactly like the current momentum card.

## Backend endpoints

All under `backend/api/main.py`, brokerage-scoped, read-only.

1. **Extend** `GET /brokerages/{id}/trends` — accept `status` (default `active`)
   and `limit` (default 50). Pass through to `action_list_trends(conn, iid,
   status)`. Feeds cards #1 (active), the recently-ended subsection (`status=ended`,
   sorted by `end_date`/`updated_at` desc, sliced to ~5), and #2 (reversal watch,
   filtered client-side from the active payload).
2. **New** `GET /brokerages/{id}/backfill-queue` — read `_backfill_queue` from
   cache; return `{queue: [{ticker, raw_net_score, n_paths, source, priority,
   timestamp_bar}], count}`.
3. **Reuse** `GET /brokerages/{id}/discovered` (already exists) for card #4.
4. **New** `GET /brokerages/{id}/trade-contexts` — latest `GraphNexusTradeContexts`
   per symbol (optionally limited to current holdings/watchlist); return
   `{contexts: [{symbol, reason, dominant_event_type, source, source_ticker,
   score, date}]}`. Truncate `reason` to a card-friendly length server-side.
5. **New** `GET /brokerages/{id}/nexus-outcomes` — aggregate the outcomes table
   server-side: `correct = (intent=="buy" & latest_return>0) or (intent=="sell"
   & latest_return<0)`; return `{hit_rate, n, n_correct, avg_return,
   recent: [{symbol, intent, latest_return, dominant_event_type, entry_date}]}`.
6. **New** `GET /brokerages/{id}/momentum-watchlist` — read the persisted summary
   written by the strategy (card #7); return `{enabled, count, newest:[{symbol,
   first_seen_bar, ret_20d}]}` or `{}` when absent.

### The one strategy edit (card #7)

In `backend/strategies/graph_nexus_analysis.py`, at the end of each run, write a
compact `_momentum_watchlist_summary` into the persisted strategy cache:

```python
strategy_cache["_momentum_watchlist_summary"] = {
    "enabled": bool(momentum_watchlist_enabled),
    "count": len(watchlist),
    "newest": [ ...top N by first_seen_bar desc: {symbol, first_seen_bar, ret_20d}... ],
}
```

Additive, telemetry-only, off the decision/trade path. **Gated by
`gitnexus_impact` before editing and `gitnexus_detect_changes` before commit.**
Confirm `_momentum_watchlist_summary` is not blacklisted in
`strategy_cache_persistence.py` (add to persist set if needed).

## Mobile (Flutter)

- **`features/dashboard/application/nexus_strategy_controller.dart`** (new) —
  providers, all `FutureProvider.autoDispose.family<…, String>` keyed by
  brokerageId, each catching errors → empty (mirrors `nexusMomentumProvider`):
  `nexusTrendsProvider`, `backfillQueueProvider`, `discoveredStocksProvider`,
  `tradeContextsProvider`, `nexusOutcomesProvider`, `momentumWatchlistProvider`.
  Reversal watch derives from `nexusTrendsProvider` (no separate provider).
- **Models** (plain classes + `fromJson`, alongside existing DTOs in
  `dashboard_repository.dart`): `MarketTrend`, `BackfillItem`, `DiscoveredStock`,
  `TradeRationale`, `OutcomeStats`, `WatchlistSummary`.
- **Repo methods** on `dashboard_repository.dart` calling the endpoints above.
- **`features/dashboard/presentation/strategy_section.dart`** (new) —
  `StrategySection(brokerageId)` plus seven private card widgets, each
  `GlassCard(frosted: true)` + `_tileLabel(...)` + compact rows, tap →
  `/stock/{symbol}` where a symbol applies. Every card returns
  `SizedBox.shrink()` when empty.
- **`dashboard_screen.dart`** — insert `StrategySection(brokerageId: id)` after
  `InsightsSection`, under a "Strategy" `AppTextStyles.h3` header.

### Card-by-card

1. **Market Trends** — active trends as compact rows (direction arrow, name,
   strength bar, top affected tickers + "+N"); a "recently ended" subsection
   (~5, with "ended Nd ago"). Bullish = `AppColors.success`, bearish =
   `AppColors.danger`.
2. **Reversal Watch** — active trends with `status=="weakening"` or non-empty
   `reversal_articles`; shows trend name + count of reversal signals. Hidden when
   none.
3. **Backfill Queue** — pending buy candidates: ticker, score bar, #paths,
   priority flag, source. Header shows queue depth.
4. **Discovered Stocks** — recently discovered names with source badge (e.g.
   "propagation · via NVDA", "momentum +18% 20d") and date.
5. **Bot Rationale** — per holding/watched symbol: a truncated `reason` line +
   `dominant_event_type` chip + discovery origin. Tap → stock screen.
6. **Outcome Scorecard** — headline hit-rate (%), n signals, avg return, and a
   few recent signal→outcome rows (green/red by correctness).
7. **Momentum Watchlist** — "monitoring N names", newest additions with age and
   20d return. Hidden when `enabled` is false or summary empty. Distinct from the
   existing top-10 "Nexus Momentum" card (which stays as-is).

## Error / empty / loading

Follows the existing convention: `valueOrNull` + `Skeleton` while loading,
`SizedBox.shrink()` when empty, providers catch and return empty. No error
banners — these are optional telemetry cards; a failed fetch simply hides the
card.

## Testing

- **Backend** (pytest, mirrors `tests/test_holding_opens.py`): pure unit tests
  for the outcome hit-rate aggregation, trend active/ended/reversal slicing, and
  backfill-queue shaping. Endpoint shape tests where feasible.
- **Mobile**: `fromJson` tests for each model; a widget/golden test for the
  Market Trends card (active + recently-ended states) using the bundled-fonts
  test harness (`test/flutter_test_config.dart`).

## Rollout / deploy

1. Backend endpoints + strategy summary write land on `main`; operator redeploys
   to Dockploy (backend is not auto-deployed).
2. Mobile ships via `cd mobile && scripts/deploy.sh 1`.
3. On-device verify: the Strategy section appears with populated cards on the
   `alpaca-main` brokerage; cards self-hide where data is absent (e.g. momentum
   watchlist if `momentum_watchlist_enabled` is off).

## Open items (resolve during implementation)

- Confirm the exact outcomes table name in code (`GraphNexusOutcomes` vs
  `GraphNexusTradeOutcomes`) before writing the aggregation endpoint.
- Confirm `_backfill_queue` item keys against the live cache (`raw_net_score` vs
  `score`, presence of `priority`/`n_paths`) and degrade gracefully on missing
  keys.
- Decide whether Bot Rationale (#5) lists all trade-context symbols or only
  current holdings (default: current holdings + active discovered, capped).
```

