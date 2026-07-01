# Kalshi Soccer Backtest — Design Spec

**Date:** 2026-07-01
**Branch:** `feat/kalshi-backtest`
**Status:** Approved for implementation

## 1. Goal

Add a backtesting feature to the Kalshi soccer bot, surfaced on both **web** and **mobile**,
mirroring the UX of the existing IntelliStock stock backtests: the user picks **leagues**,
**tuning settings**, and a **date range**, runs it, watches progress, and sees results
(P&L, equity curve, trade list, calibration). The replay drives the **same pure functions
the live Kalshi engine uses**, so it genuinely tests the real strategy and becomes the
validation harness for later strategy fixes.

## 2. Why this is possible now (data sources)

Historical data for a faithful replay is obtainable, essentially free:

| Data | Source | Access |
|---|---|---|
| Kalshi price history | `GET /historical/markets/{ticker}/candlesticks` | **Public, no auth**. 1m/60m/1440m OHLC + yes/no bid-ask + volume. Params: `start_ts`,`end_ts`,`period_interval`. |
| Historical sharp odds (Pinnacle) | **OddsPapi** `/v4/historical-odds` | Free tier (~250 req/mo). Full timestamped price history per fixture. Covers 2026 World Cup. |
| Fixtures + final scores | OddsPapi `/v4/fixtures` (fallback: Kalshi settlement / final candlestick) | Free tier. |

The repo already has `OddsPapiClient` (`backend/kalshi/ingest_odds.py`) with a budget guard
(`should_scan`, `fixtures_per_day_budget`, `bump_scan_budget` in `db.py`) — currently dead
code. It gets wired for the first time here.

**Prerequisite:** a free OddsPapi API key (no credit card), stored on the backtest/instance
config like the existing `odds_api_key` (new field `oddspapi_api_key`).

**Known caveat:** national-team Elo is current-only (no as-of-date), so *model-only* metrics
carry mild look-ahead and are labeled "indicative". *Strategy* P&L is sound because fair value
anchors to the real historical sharp line.

## 3. Scope (v1) — YAGNI boundaries

- **Pre-match only.** No in-play/live replay.
- **A small set of pre-kickoff decision snapshots** per fixture (default: T−3h; configurable
  list), not the full tick path. One entry per fixture per side.
- Exposes the **existing** config knobs (edge_threshold, no_sharp_edge_threshold,
  kelly_fraction, order_size_min/max, max_open_exposure_frac, per_bet_cap_frac,
  sharp_weight, devig_method, min/max price, draw_min_edge, bankroll, leagues). New
  strategy fixes (devig-Kalshi-book, per-fixture lock, calibration) are **out of scope here**
  but will be automatically backtestable once added, because the replay reuses engine functions.
- **Lightweight in-process background runner** — NOT the heavy Docker-per-backtest machinery
  the stock backtests use (per-fixture pre-match replay is cheap). Same polled-progress UX.

## 4. Architecture

```
Web/Mobile create form ──POST──▶ KalshiBacktests row (status=pending)
                                        │ (in-process worker, changefeed/drain)
                                        ▼
                          backend/kalshi/backtest.py :: run_backtest(cfg)
                              │  resolve fixtures (OddsPapi, CACHED)
                              │  per fixture: score (CACHED), Kalshi candles (CACHED),
                              │               sharp odds (CACHED), model probs
                              │  evaluate() → REUSE fusion/edge/candidates/risk/planner
                              │  settle() → REUSE reconcile
                              │  write throttled progress → KalshiBacktests.progress
                              ▼
                      KalshiBacktestResults row (equity curve, trades, calibration)
                                        ▲
Web/Mobile results ◀──GET /status,/results (poll)──┘
```

### 4.1 Backend modules
- `backend/kalshi/backtest.py` — **new**. Orchestrates the replay. Pure core
  (`run_backtest(cfg, data_provider, progress_cb) -> BacktestResult`) so it unit-tests
  without live APIs (data_provider injected/faked). No DB or HTTP inside the core.
- `backend/kalshi/backtest_data.py` — **new**. `BacktestDataProvider`: fetch-or-cache layer
  for fixtures, scores, Kalshi candlesticks, OddsPapi odds. Owns the cache tables and the
  OddsPapi budget guard. This is where "cache everything, only fetch new" lives.
- `backend/kalshi/client.py` — **extend** with `get_historical_candlesticks(ticker, start_ts,
  end_ts, period_interval)` (public endpoint; ~15 lines).
- `backend/kalshi/ingest_odds.py` — **extend** `OddsPapiClient` with `list_fixtures(sport_id,
  date_from, date_to)`, `historical_odds(fixture_id, books)`, and normalized parsers. Confirm
  real response schema during Phase 0 (a probe call) and map into the `parse_three_way` shape.
- API endpoints — **extend** the FastAPI Kalshi surface (alongside existing
  `/brokerages/{bid}/kalshi/...` routes).
- Background worker — **new** `kalshi_backtest_worker` started at app startup: watches
  `KalshiBacktests` for `pending` (changefeed, like `backtest_engine._changefeed_worker`),
  drains on boot, runs each in a small ThreadPool, honors a `run=false` stop flag.

### 4.2 Reused engine functions (the point)
`intelligence.fusion.build_market_probs`/`fuse`, `edge.compute_edge`,
`strategy.candidates.generate_candidates`, `risk.RiskCaps` + `capital.planner.allocate`,
`reconcile.reconcile_position`/`aggregate_positions`, `fair_value` + `devig` (sharp line),
`quant`/`intelligence.pricing` (model probs), `instance_config.risk_caps_from_config`.

## 5. Data model (RethinkDB tables)

**Job/queue + status:** `KalshiBacktests` (pk `id`)
```
id, brokerage_id, instance_id?, name, status(pending|running|finished|error|stopped),
progress(0..100), run(bool, stop flag), config{...tuning...}, leagues[], start_date, end_date,
bankroll_cents, created_at, started_at, finished_at, error,
summary{ pnl_cents, roi, n_bets, n_fixtures, win_rate, clv_avg, api_calls_used, cache_hits }
```

**Heavy results:** `KalshiBacktestResults` (pk `id` = backtest id)
```
id, equity_curve[ {ts, cum_pnl_cents, bankroll_cents} ],
trades[ {fixture_key, league, home, away, ts, side, entry_cents, size, model_prob,
         sharp_prob, fused_fair, edge, outcome, realized_pnl_cents, clv} ],
calibration[ {bucket_lo, bucket_hi, predicted, actual, n} ],
per_league[ {league, n, pnl_cents, roi, clv_avg} ], logs[]
```

**Immutable historical caches (shared across ALL backtests):**
- `KalshiHistCandles` (pk `"{ticker}|{interval}"`) → `{ticker, interval, candles[], cached_at}`
- `KalshiHistOdds` (pk `"{fixture_key}|{book}"`) → `{fixture_key, book, snapshots[{ts,home,draw,away}], cached_at}`
- `KalshiHistFixtures` (pk `fixture_key`) → `{fixture_key, league, home, away, kickoff_ts,
  home_score, away_score, result(home|draw|away), settled(bool), kalshi_tickers{side:ticker}, cached_at}`

**Caching rule:** on a cache hit return stored data (0 API calls). Only fetch on miss, or when
a fixture row exists but `settled=false` (re-fetch score/odds until the match has settled). Once
`settled=true`, the row and its candles/odds are immutable and never re-fetched. OddsPapi fetches
pass through the budget guard; skips are logged, never silent (mirrors `ingest_odds` docstring).

## 6. Replay algorithm (`run_backtest`)

```
fixtures = data.fixtures(leagues, start, end)          # cached
for i, fx in enumerate(fixtures):
    score = data.final_score(fx)                       # cached; skip if not settled
    if score is None: continue
    tickers = data.kalshi_tickers(fx)                  # resolve via discovery+normalize+ticker_names
    candles = data.candles(tickers)                    # public, cached
    oddseries = data.sharp_odds(fx)                    # OddsPapi, cached
    model = model_probs(fx, as_of=fx.kickoff_ts)       # elo/dixon-coles (nat-elo caveat)
    for snap_ts in cfg.decision_snapshots(fx):         # default [kickoff - 3h]
        kalshi_ask = price_at(candles, snap_ts)        # yes_ask per side at snap
        sharp = devig(sharp_at(oddseries, snap_ts))    # reuse fair_value/devig
        cands = evaluate(cfg_caps, model, sharp, kalshi_ask)  # reuse fusion→edge→candidates→planner
        for c in cands:                                 # sized bets
            trade = settle(c, score)                    # reuse reconcile → realized_pnl, clv
            record(trade)
        if cands: break                                 # one decision snapshot per fixture (v1)
    progress_cb((i+1)/len(fixtures))
return aggregate(trades)  # equity curve (by kickoff order), roi, win_rate, clv, calibration, per-league
```

Determinism: fixtures processed in kickoff order; no wall-clock/random in the core.

## 7. API endpoints (mirror stock backtest shapes)

- `POST /brokerages/{bid}/kalshi/backtests` — body `KalshiBacktestBody{ name?, instance_id?,
  leagues[], start_date, end_date, bankroll_cents, config{...} }` → `{id}`. Inserts pending row.
- `GET  /brokerages/{bid}/kalshi/backtests` — list (summary rows).
- `GET  /kalshi/backtests/{id}/status` — `{status, progress, summary, error}` (poll target).
- `GET  /kalshi/backtests/{id}/results` — full `KalshiBacktestResults`.
- `POST /kalshi/backtests/{id}/stop` — set `run=false`.
- `DELETE /kalshi/backtests/{id}` — delete job + results.

## 8. Web UI (`frontend/`)

- Add a **Backtest** section + "New backtest" button on `KalshiInstanceDetailView.vue` header
  (next to Edit). New route `/kalshi/instances/:id/backtest` optional.
- **Create modal**: reuse `KalshiCreateInstanceModal.vue` building blocks — leagues multi-select
  (`LEAGUES`/`toggleLeague`), numeric tuning grid + `InfoTip`, native `<input type=date>` range,
  bankroll. Prefill from the instance's current config.
- **Results view**: equity curve via `KalshiPortfolioChart.vue` (area); trade table + calibration
  table hand-rolled; summary stat cards; poll `GET /kalshi/backtests/{id}/status` every 3s for the
  progress bar (pattern from `BacktestsView.vue`). List section shows past backtests.
- API via the existing inline `fetch` + `authHeaders()` idiom.

## 9. Mobile UI (`mobile/`)

- **`KalshiBacktestSheet`** mirroring `KalshiInstanceSheet` (leagues chips, `_field` numerics,
  date text fields, risk presets) opened via `showModalBottomSheet` from the Kalshi instance
  detail AppBar.
- **Results**: reuse `core/charts/ScrubbableAreaChart` (equity), `StatTile` grid, `DataTable`
  trades, `LinearProgressIndicator` progress, `GlassCard`/`StatusPill`/`EmptyState`.
- Repo methods on `KalshiRepository` (`POST .../kalshi/backtests`, status, results) +
  `FutureProvider.autoDispose.family` providers + `Timer.periodic` polling (existing idioms).
- A backtests list section on the Kalshi instance detail screen.

## 10. Testing

- **Unit (pytest, no network):** `run_backtest` core against a faked `BacktestDataProvider`
  (deterministic fixtures/candles/odds/scores) — asserts bet selection, sizing, settlement,
  aggregation, per-fixture single-entry, edge/no-sharp gating. Cache layer: hit returns stored,
  miss fetches once, `settled=false` re-fetches, budget-guard skip. New client methods:
  candlestick param/URL construction + response parsing; OddsPapi fixtures/historical parsing.
- **Integration (opt-in, real keys):** one small real fetch each (Kalshi candlesticks public +
  OddsPapi historical) behind an env-gated marker, to confirm live response schemas.
- **Web/mobile:** existing test patterns for the new components where present; `flutter analyze`
  clean (pre-existing infos only); web build passes.

## 11. Risks / mitigations

- **OddsPapi 250/mo budget** → aggressive immutable caching (re-runs = 0 calls); budget guard
  refuses to overspend and logs skips; surface `api_calls_used`/`cache_hits` in the summary.
- **Fixture↔Kalshi ticker mapping** may miss on name variants → reuse `normalize`/`ticker_names`
  crosswalk + fuzzy match (as `odds_api.match_event` already does); unmatched fixtures are logged
  and skipped, never guessed.
- **National-Elo look-ahead** → label model-only metrics "indicative"; headline P&L uses the
  sharp-anchored path.
- **OddsPapi response schema unknown** → Phase 0 probe call confirms and maps it before building on it.
- **Server restart mid-run** → boot drains `pending`/`running`-orphaned rows (re-queue or mark stopped).

## 12. Out of scope (future)
In-play replay; full tick-path replay; the P1/P3 strategy fixes themselves (devig-Kalshi-book,
per-fixture lock, calibration) — added later and validated *with* this harness; paid historical
tiers; leagues beyond what OddsPapi + Kalshi + a score source jointly cover.
