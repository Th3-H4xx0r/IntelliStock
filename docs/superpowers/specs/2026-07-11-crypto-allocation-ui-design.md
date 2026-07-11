# Crypto Allocation UI + model — Design

**Date:** 2026-07-11 · **Status:** Approved, building · **Branch:** `feat/crypto-trading-platform` (extends PR #114)

## Goal
Full mobile + web UI to set up and edit `kind="crypto"` instances, centered on a **live donut + editable weight table**: pin fixed % (or $) per coin, leave the rest **Dynamic** (auto-discovered & traded). Default 100% dynamic. Plus the backend model to honor those allocations, and all deferred crypto follow-ups.

Approved mockup: `https://claude.ai/code/artifact/5d46658c-2c98-43f0-b958-7f002fc2b498`

## Allocation model
`crypto_config.allocations = [{symbol, pct}]` or `[{symbol, usd}]` per row (% is fraction 0–1 of portfolio value; `usd` resolved to pct at runtime). **Remainder = 1 − Σ fixed = the Dynamic bucket** (implicit, no explicit field). Empty/absent ⇒ 100% dynamic = today's behavior unchanged.

## Backend (contract verified against broker.py)
1. **Inject `crypto_config` into strategy config** — in `run_run_once_strategies` right after the `instance_id` injection (`broker.py:~2840`), call the cached `_instance_kind_and_crypto_config()` and set `config["crypto_config"]`/`conditions["crypto_config"]` when `kind=="crypto"`.
2. **`core.overlay_allocations(out, allocations, pv, positions, prices, dynamic_syms)`** (new, in `strategies/crypto/core.py`), called at the end of each crypto strategy's `run_once`:
   - Resolve each fixed entry to a target notional (`pct*pv` or `usd`). Current notional = `position_qty(positions, sym) * price`.
   - Rebalance to target within a tolerance band: under → `out[sym]=1` with `buy_cash = target-current`; over → `out[sym]=-1` with `sell_fraction=(current-target)/current`.
   - Add fixed syms to `_nexus_discovered`. Emit **dict-valued** `_nexus_position_sizes[sym] = {"buy_cash": …, "asset_class": "crypto", "high_conviction": True}` (dict, NEVER bare float — that was the prior TypeError bug).
   - Dynamic budget = `max(0, (1−Σfixed_frac))*pv`, split among the strategy's dynamic buys (equal or `vol_target_size`-weighted) → their `buy_cash`.
   - Top-level control keys: `_cash_reserve_floor_pct: 0.0`, `_buy_price_floor: 0.0` (so 100% deploys, no $5 floor).
3. **Bypass the live single-position cap for crypto** — gate `BROKER_MAX_SINGLE_POSITION_PCT` trimming (`broker.py:~9368`) behind `not _is_crypto_instance_runtime()` so explicit allocations (e.g. 20%, 50%) are placed as-is. (Backtest already ignores it.)
4. **Create/Edit API:**
   - `CreateInstanceBody` (`api/main.py:468`) + `api_create_instance` (`:1923`): add + forward `kind`, `crypto_config`, `stocks`. (`action_create_instance` already persists them.)
   - `EditInstanceBody` (`api/main.py:481`) + `api_edit_instance` (`:1946`) + `action_edit_instance` (`interactive_utils.py:1189`): add `crypto_config` (and `stocks`) support (writes `updates["crypto_config"]`). Edit does not exist today.

## Web (Vue 3 + Tailwind + ApexCharts)
- New route `/crypto` in `router/index.js` + nav item in `AppShell.vue`.
- `views/CryptoView.vue` (lists crypto instances via `GET /instances` filtered to `kind==='crypto'`; "New crypto instance" opens the modal).
- `components/crypto/CryptoCreateInstanceModal.vue` — clone `KalshiCreateInstanceModal.vue` structure (create + edit via `editInstance` prop; brokerage dropdown w/ live balance for $ conversion; band/risk select; symbol+weight table). Create → `POST /instances` `{id,name,granularity,kind:'crypto',crypto_config,stocks,brokerage_id,strategy_id}`; edit → `PATCH /instances/{id}` `{crypto_config,stocks,...}`.
- `components/crypto/CryptoAllocationChart.vue` — ApexCharts `type:"donut"` styled to the mockup (violet ramp, muted hatched Dynamic slice, dark theme, Inter). Live-reactive to the table.
- Extend the `InstancesView.vue:139` filter to also exclude `kind==='crypto'` (crypto lives on its own tab, like Kalshi). 24/7 badge on crypto cards.

## Mobile (Flutter + Riverpod + go_router)
- New route `/crypto` in `core/router/router.dart` + entry in `core/router/more_sheet.dart`.
- `features/crypto/presentation/crypto_screen.dart` (lists crypto instances) + a unified create/edit sheet cloned from `KalshiInstanceSheet` (nullable `editInstanceId`/`editConfig` + `_prefill`, band presets, brokerage dropdown, symbol+weight table).
- **Donut reuses the existing `Sector3DChart`** (`features/dashboard/presentation/sector_3d_chart.dart`) fed `SectorSlice(sector=symbol, pct=weight)` + a Dynamic slice. Optionally give per-coin distinct hues (only the `hiC/midC/loC` block, lines 434-442) or keep the violet ramp.
- `features/crypto/data/crypto_repository.dart` (create → `POST /instances` with `kind:'crypto'`; edit → `PATCH /instances/:id`). Add `crypto`/`kind` handling to the `Instance` model + a `kind=='crypto'` filter in `InstanceRepository.listInstances` (mirrors the Kalshi exclusion). 24/7 badge.

## Deferred items (folded in)
- **Fee-accurate crypto backtest fills** — apply `core.CRYPTO_FEES` taker on simulated fills in the backtest path for crypto instances.
- **Namespace guard** — prevent a future flat `strategies/<name>.py` from shadowing a crypto strategy (e.g. warn, or resolve crypto-first for crypto instances).
- **Band→cadence robustness** — if `crypto_config.band` absent, derive from the selected strategy's schema band; document that band drives cadence.
- **DRY** — broker crypto bars use `core.crypto_bars_url()`; extract the shared meta-scan loop.
- **24/7 badge** — web + mobile crypto cards.

## Validation
- Backend: unit tests for `overlay_allocations` (fixed rebalance up/down, dynamic split, dict shape, control keys, cap bypass), API forward tests. `_nexus_position_sizes` values are always dicts.
- Web: `npm run build` clean; donut reacts to table; create/edit round-trips.
- Mobile: `flutter analyze` clean; sheet create/edit; donut renders.
- Full crypto suite stays green; GitNexus detect_changes low-risk; code-review pass.
- On-server paper verify (post-deploy): create a crypto instance with 10% BTC / 20% ETH / rest dynamic via the UI; confirm the donut, the placed sizes (~10%/20% + dynamic), and 24/7 ticks.
