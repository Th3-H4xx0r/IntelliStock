# Crypto backtest support (button + flow) — design

**Date:** 2026-07-12
**Branch:** `feat/crypto-trading-platform` (PR #114)
**Scope:** UI only. Add a "Backtest" affordance to crypto instances (web + mobile)
that runs a backtest of the instance's configured allocation and opens the
existing generic backtest result view.

## Backend — no changes required

`POST /backtests` with `{instance_id, stocks, start_date, end_date, granularity,
initial_cash}` already runs a crypto instance correctly, because the backtest
uses the SAME `broker.py` as live:

- **Historical data:** `fetch_alpaca_historical_bars` → `_do_fetch_one_chunk`
  branches to the v1beta3 crypto bars endpoint when
  `_is_crypto_instance_runtime()` is true (`broker.py:1244`), with a symbol-keyed
  response parse (`broker.py:1306`). The backtest engine calls this same function.
- **Fills:** `portfolio_emulator` applies the ~0.25% taker fee on `"/"`-symbols
  (fee-accurate; covered by `backend/tests/test_crypto_backtest_fees.py`).
- **Strategy:** `_crypto_synthetic_specs` synthesizes the run_once spec from
  `crypto_config.strategy` — no Strategies row needed.
- **24/7:** market-hours gating is bypassed for `kind=="crypto"`.

Since the broker process for a backtest is tied to the target `instance_id`, the
crypto classification and all gated branches activate automatically. No
server-side change.

## Semantics

- The crypto backtest runs **this instance's own configuration**. `stocks` sent to
  `POST /backtests` = the instance's fixed slash-pairs
  (`crypto_config.allocations[].symbol`). Empty allocations (100% dynamic) → send
  `[]` → pure auto-discovery, same as live.
- `granularity` (seconds string) is **derived from the instance's Band** — the
  single source of truth set in create/edit — and shown read-only, NOT a separate
  control (avoids duplicating the Band concept under a new "granularity" term):
  `high→300` (5m bars), `medium→900` (15m), `low→3600` (1h); fallback `900`. To
  change cadence, the user edits the instance's Band.
- `initial_cash` default `10000` (crypto paper scale), editable.
- Date range default: last 90 days → today, editable.
- On success the `POST /backtests` response's `id` is used to navigate to the
  generic result view (`/backtests/:id` — `BacktestDetailView` web /
  `backtest_detail_screen` mobile), which is asset-class-agnostic.

## Web

- `frontend/src/components/crypto/CryptoBacktestModal.vue` (new): props
  `{ instance }`, emits `close`. Read-only coin summary + date range + granularity
  pills + initial cash. Submit → `POST /backtests` → `router.push('/backtests/'+id)`.
- `frontend/src/views/CryptoView.vue`: add a "Backtest" pill button to each card's
  action row (between Edit and Start) + modal open/close state.

## Mobile

- `mobile/lib/features/crypto/data/crypto_repository.dart`: add
  `createBacktest({instanceId, stocks, startDate, endDate, granularity,
  initialCash}) → Map` returning the created row (for its `id`).
- `mobile/lib/features/crypto/presentation/crypto_screen.dart`: add a "Backtest"
  action to `_CryptoCard`; open a `_CryptoBacktestSheet` (new, in the crypto
  feature) mirroring the equity `_CreateBacktestDetailSheet` +
  `_GranPills`/`_kGrans`. Submit → `createBacktest(...)` → `context.push('/backtests/'+id)`.

## Verification

- Mobile: `flutter analyze` on the crypto feature — clean.
- Web: `npm run build` — compiles.
- Manual: create a crypto backtest from the card, confirm it queues and the
  result view opens.
