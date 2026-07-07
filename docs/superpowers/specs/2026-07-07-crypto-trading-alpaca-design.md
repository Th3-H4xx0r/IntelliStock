# Crypto Trading on Alpaca — Design

**Date:** 2026-07-07
**Status:** Design — awaiting review
**Author:** Pranav + Claude (brainstorming session)
**Branch:** `feat/crypto-trading-platform`

## Goal

Add autonomous cryptocurrency trading to IntelliStock on Alpaca, across three
distinct frequency bands — **high** (fast tactical), **medium** (momentum), and
**low** (allocator) — each a genuinely different strategy, not one algorithm with
a speed knob. Paper-first.

## The reality that shapes everything

Alpaca crypto is a REST + websocket **retail** venue, not an HFT venue:

- **Fees, not commission-free.** ~0.15% maker / **0.25% taker** at the entry tier
  → **~0.3–0.5% round-trip**. Fast churn loses to fees unless each trade clears
  ~0.5%. Fees reward *lower* turnover.
- **200 requests/min** REST cap; websocket for real-time data. True sub-second HFT
  is impossible. "High frequency" here realistically means **tick-reactive on a
  1–5 min cadence**, not milliseconds.
- **Upside:** genuinely 24/7, no PDT, instant settlement, currently wash-sale
  exempt, long-only spot (no shorting/margin), fractional, **free market data**,
  same `TradingClient` as equities (symbols like `BTC/USD`).

**Honesty clause:** this spec delivers the platform and three well-constructed,
realistic strategies. It does **not** promise alpha. The high-frequency band
fights the stiffest fee headwind and is the least likely to pay; it is included
because you asked for the full three-band design, and it doubles as the strictest
test of the fee-aware execution layer.

## Architecture (Option C — approved)

A shared crypto core with three thin strategy plugins on top:

```
backend/strategies/crypto/
  __init__.py
  core.py          # shared: bars fetch, fee-aware order helper, sizing,
                   #         stablecoin risk-off, fee model, PnL tracking
  fast.py          # Band 1: fast tactical   (Phase 2)
  momentum.py      # Band 2: momentum        (Phase 2)
  allocator.py     # Band 3: allocator       (Phase 2)
  reference.py     # trivial rebalance, proves the platform end-to-end (Phase 1)
```

Each band runs as its **own** `kind="crypto"` instance with its own capital and
its own scheduler cadence — mirroring how the codebase already isolates the
Kalshi instance (`kind="kalshi"`). The three strategies stay small and legible
because the crypto plumbing lives in `core.py`.

## Phasing

This is too large for one spec. It decomposes into three sub-projects, built in
order; each phase after this one gets its own spec.

- **Phase 1 — Platform capability (THIS SPEC).** Make a `kind="crypto"` paper
  instance trade `BTC/USD` 24/7 end-to-end with a trivial reference strategy.
  Unbolts the six equity assumptions; builds the shared crypto core skeleton.
- **Phase 2 — The three bespoke strategies + crypto backtest.** Detailed signal
  logic, sizing, and risk per band; backtest to validate before paper.
- **Phase 3 — UI polish.** Asset-class picker, frequency selector, 24/7 badge.
  Minimal touches folded into P1 so the instance is visible/creatable.

---

# Phase 1 — Platform Capability (detailed)

## Scope

**In:**
1. `kind="crypto"` instance plumbing (data model + dispatch).
2. Per-instance scheduler config for 24/7 + configurable cadence.
3. Market-hours-gate bypass for crypto.
4. Crypto order placement (`gtc`, no extended-hours).
5. Crypto price-bars endpoint.
6. Symbol-validator bypass for `BTC/USD`.
7. Shared crypto core skeleton (`core.py`) + trivial `reference.py` strategy.
8. Minimal UI: let a crypto instance be created and shown (not polished).
9. Live-trading gated **OFF** (paper-only), mirroring Kalshi.

**Out (later phases):** the three bespoke strategies (P2), crypto backtest (P2),
polished asset-class UI (P3), non-USD quote pairs, portfolio-chart crypto tuning.

## The six seams (with exact integration points)

All changes **branch on `kind=="crypto"`** and leave the equity path untouched.

### 1. `kind="crypto"` instance plumbing
- Add `kind="crypto"` to the Instances row; a crypto instance = `kind="crypto"`
  + Alpaca `brokerage_id` + `stocks=["BTC/USD", …]` + `granularity_time_increment`
  + `scheduler_config` blob. RethinkDB is schemaless → no migration.
- Dispatch on `kind` at the two existing switch points: `backend/instance.py:389`
  and `backend/server.py:405` (mirror Kalshi at `backend/kalshi/instance_config.py:154-162`).
- Extend the `asset_class` hint (`backend/nexus_broker_utils.py:207`) with a
  `"crypto"` value for sizing/floor behavior.

### 2. 24/7 scheduler + cadence (single load-bearing wiring gap)
- Today the broker calls the scheduler with `config=None` (`backend/broker.py:6939`),
  which forces the equity `DEFAULT_CONFIG` (5AM–5PM PT, weekdays-only,
  `backend/scheduler.py:50-58`).
- The scheduler **already supports** 24/7 via `_resolve_config` (`scheduler.py:70-108`).
  Fix = load a per-instance `scheduler_config` and pass it at `broker.py:6939`
  instead of `None`, with `weekdays_only=False`, `open_pt_min=0`, `close_pt_min=1440`,
  and a `monitor_interval_min` that sets cadence (P1 reference: e.g. 20 min).
- This is exactly how Kalshi injects `kalshi_config`.

### 3. Market-hours-gate bypass
Branch these two gates so crypto never gets skipped off-hours:
- The live-session gate `backend/broker.py:7558-7577` (via
  `broker_session.is_within_live_session` → `live_calendar.is_nyse_open_extended`).
- `AlpacaAdapter.is_market_open` `backend/broker_adapters/alpaca.py:1241` →
  return `True` for crypto.

### 4. Crypto order placement
- The auto-execute shims hardcode `tif="day"` (`alpaca.py:1611,1642`) and
  `_order_style_for_now` (`alpaca.py:1558-1591`) flips to a marketable
  `LIMIT + extended_hours=True` outside RTH. Alpaca **rejects both** for crypto.
- Crypto path: force `tif="gtc"` (or `ioc`), `extended_hours=False`, and bypass
  `_order_style_for_now`. `tif_map` already contains `gtc` (`alpaca.py:694-701`).
- Symbol passes through verbatim — `submit_order` already forwards `BTC/USD`
  untouched (`alpaca.py:741`); upstream `.upper()` preserves it.

### 5. Crypto price-bars endpoint
- Bars are fetched via raw HTTP at `f"{ALPACA_DATA_BASE}/stocks/{sym}/bars"`
  (`backend/broker.py:1196`; base `:874`), plus `/stocks/quotes` (`:2119`) and
  `/stocks/trades` (`:2099`), with `feed=iex|sip`.
- Two problems for crypto: the `/` in `BTC/USD` makes a malformed
  `.../stocks/BTC/USD/bars`, and crypto has no iex/sip feed.
- Fix: branch the URL/param builder in `_do_fetch_one_chunk`
  (`backend/broker.py:1194-1208`) to
  `https://data.alpaca.markets/v1beta3/crypto/us/bars?symbols=BTC/USD&timeframe=…`
  (symbols as a **query param**, no `feed`); mirror for quotes/trades.

### 6. Symbol-validator bypass
- The US-equity ticker regex `^[A-Z]{1,5}([.\-][A-Z])?$` (`backend/ticker_universe.py:53`,
  `is_valid_us_ticker:128`) hard-rejects `BTC/USD`, and equity discovery
  (`backend/discover.py`, Nasdaq screener) is meaningless for crypto.
- Gate `ticker_universe`/`discover` behind `kind != "crypto"`; crypto uses the
  static `stocks` list as its universe.

## Shared crypto core skeleton (`backend/strategies/crypto/core.py`)

Phase-1 responsibilities (thin, well-tested):
- **Bars/quotes fetch** wrapper over the new crypto endpoint (seam 5).
- **Fee-aware order helper** — prefers maker (post-only limit) placement to pay
  0.15% over 0.25%; falls back to marketable limit; centralizes `gtc` /
  `extended_hours=False`.
- **Fee model** — 0.15%/0.25% tiered schedule, used for cost-aware sizing now and
  backtest later.
- **Long-only spot sizing** — vol-targeted fixed-fraction with a per-band
  max-exposure cap; risk-off = rotate to USD/USDC.
- **PnL / position tracking** in USD terms.

The Phase-1 `reference.py` strategy uses only `core.py`: hold a fixed target
weight in `BTC/USD` (and optionally `ETH/USD`), rebalance on the monitor tick.
Its only job is to prove the platform trades 24/7 on paper.

## Data model

Instances row (schemaless, additive):
- `kind: "crypto"`
- `asset_class: "crypto"`
- `scheduler_config: { weekdays_only: false, open_pt_min: 0, close_pt_min: 1440,
  monitor_interval_min: <n>, full_anchor_pt_min: <n|null> }`
- `stocks: ["BTC/USD", …]`
- `strategy_id → crypto_reference` (P1)

Brokerage: reuse `BrokerageAccounts` (`brokerage_type="alpaca"`, `alpaca_paper=true`).
**Requires** an Alpaca **paper** account with crypto enabled; creds stored
Fernet-encrypted as today.

## Safety & isolation

- **Paper-only in Phase 1.** Live crypto trading gated OFF behind an explicit
  flag, mirroring the Kalshi live-gate. No path to real money in P1.
- Every change branches on `kind=="crypto"`; the equity live path
  (`alpaca-main`, real money) is **not modified**. Per project rules, any symbol
  edited during implementation gets `gitnexus_impact` run first.
- Reuses the existing per-instance subprocess isolation (`instance.py` supervisor
  → `broker.py` subprocess), so a crypto instance can't affect equity instances.

## Validation plan

Definition of done for Phase 1 — a paper `kind="crypto"` instance:
1. Boots, loads Alpaca paper creds, resolves `BTC/USD`.
2. Fetches crypto bars from the v1beta3 endpoint (not the equity endpoint).
3. Runs the reference strategy on the monitor cadence **overnight and on a
   weekend** (proves 24/7 — the equity gates are truly bypassed).
4. Places and fills a `gtc` crypto order on paper (no `day`/extended-hours
   rejection).
5. Reports position/PnL; equity instances keep running untouched.

Unit tests for `core.py`: fee model math, maker/taker order helper, `BTC/USD`
symbol formatting, scheduler-config resolution (24/7 window), sizing caps.
Drive the end-to-end paper run with the same read-only diagnostic pattern used
for `alpaca-main` (`scripts/diag_alpaca_open.py`).

## Defaults (confirm on review)

- **Coin universe:** P1 = `BTC/USD` (+ optional `ETH/USD`). Full basket
  (BTC, ETH + ~6 majors) arrives with the P2 strategies.
- **Sizing:** vol-targeted fixed-fraction, per-band max-exposure + daily loss
  limit; risk-off rotates to USD/USDC.
- **Cadence (P1 reference):** monitor every ~20 min (fast enough to observe,
  slow enough to be cheap).

## Open questions

1. Confirm you have (or can create) an Alpaca **paper** account with crypto
   enabled — needed to validate Phase 1. (Geo note: paper crypto works from any
   region; live crypto excludes NY and some jurisdictions — verify at go-live.)
2. Reuse the existing portfolio-chart path for crypto now, or defer chart tuning
   to P3? (P1 can ship without it.)
3. Any coin you specifically want in/out of the eventual basket?

---

# Phases 2–3 (outline — separate specs later)

**Phase 2 — Three bespoke strategies + crypto backtest:**
- **Fast tactical** (High, ~1–5 min, BTC/ETH only): short-horizon
  momentum/breakout, **maker-limit entries** (0.15%), ATR stop, take-profit must
  clear ~0.6% round-trip. The fee-fighter.
- **Momentum** (Medium, ~15 min–1 hr, ~5–8 majors): trend-following (EMA cross /
  Donchian), ADX/vol chop filter, hold top-K by momentum, vol-targeted sizing,
  stablecoin risk-off in downtrends. The sweet spot.
- **Allocator** (Low, daily/weekly, broad majors): regime/trend allocation — hold
  coins above long-term MA, inverse-vol weighting, weekly/threshold rebalance,
  all-to-stablecoin when BTC breaks trend. Most fee-efficient.
- **Backtest:** crypto bars + the `core.py` fee model, so each strategy is
  validated against fees before paper.

**Phase 3 — UI polish:** asset-class tab beside the brokerage selector (reuse the
Alpaca|Robinhood tab pattern), repurpose the granularity control as the
high/med/low picker, 24/7 status badge, and let `kind="crypto"` past the
`i.kind !== 'kalshi'` list filter (`frontend/src/views/InstancesView.vue:137-139`).
