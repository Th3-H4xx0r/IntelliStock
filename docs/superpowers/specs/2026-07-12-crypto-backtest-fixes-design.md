# Crypto backtest fixes — sells, 24/7 stepping, speed, chart, cosmetics

**Date:** 2026-07-12
**Branch:** `feat/crypto-trading-platform` (PR #114)
**Status:** Approved (auto), pipeline running

## Problem

Crypto backtests (`crypto:momentum`, `crypto:allocator`) buy once and hold forever — **zero sells** — so the portfolio just tracks the coin down. Investigation of real runs (backtests 615127, 781962) surfaced five issues:

1. **No sells (primary).** Every crypto strategy degenerates to a single all-in buy then HOLD (score `0.000`) for thousands of steps. `momentum.run_once` logic is *provably correct* (a faithful multi-step repro buys the uptrend and sells the drop, buys=1/sells=1). In the real backtest it returns `0` because its `held` set is **empty** despite the emulator holding `BTC/USD` (reserved-capital and the final summary both confirm the position; `universe` and the position key are both slash-form `"BTC/USD"`). Under the deployed code this is a paradox — a runtime input we can't see from logs alone. Contributing signals: momentum emits `+1` only at t0 then never again, indicating a degenerate/sparse per-tick window (`<32` `min_bars`), consistent with poisoned `AlpacaBarsCache` crypto rows.
2. **24/7 stepping.** The backtest time-advance applies the equity NYSE/PT session gate to crypto, skipping overnight and weekends ("Skipped to next market open" ×66 in a 3-month run). Crypto must run 24/7.
3. **Slowness.** ~1.8–2.0 s/step (7,896 steps → 3.9 h). Root cause: `_get_prices_at_time` (`broker.py:6364`, called per step at `:7529`) re-scans the full bar history from `bars[0]` every step (O(n²) total), and `_bar_time_to_datetime` (`:6250`) uses `dateutil.parser.parse` (~50×slower than `fromisoformat`). Removing the session gate multiplies step count (5-min × 24/7 × 90d ≈ 26k steps), so this must drop for 24/7 runs to be usable.
4. **Running-chart truncation.** While `status=running`, `portfolio_value_history` is written as `get_portfolio_history()[-3000:]` (`broker.py:9983`) — last-N only; at finish it's replaced with the full history (`:7313`). A long/high-cadence *running* backtest therefore shows a truncated chart starting mid-run, with a wrong `portfolio_start_value` (e.g. $10,785 vs the true $9,975) and misleading "vs start" P&L. `pnl` (vs `initial_cash`) is correct. Self-corrects at finish, but is misleading live.
5. **NaN% price-change (cosmetic).** The P&L-per-stock table shows "Price Change NaN%".

## Non-goals

- Retuning the momentum/allocator strategy parameters (logic is correct).
- Any change to equity behavior — `alpaca-main` must stay byte-identical.
- Reworking live trading; only the shared price-lookup speedups touch the live path and must be output-identical.

## Design

### Fix 1 — No sells (defensive fixes + instrumentation, confirmed in one deploy)

The exact trigger for the empty `held` is a runtime paradox, so we ship safe fixes covering the known failure modes plus a temporary probe; one backtest confirms.

- **1a. `position_qty` slash-symmetry** — `backend/strategies/crypto/core.py:245`. Today it strips `/` only from the *query* symbol, so a slash-less `universe` symbol (`"BTCUSD"`) can't match a slash position (`"BTC/USD"`) → `held` empty. Fix: compare with `/` stripped (and uppercased) on **both** sides. Verified failing case: `position_qty({"BTC/USD":0.14}, "BTCUSD") == 0.0` today, must become `0.14`.
- **1b. Held positions always evaluable for exit** — a held crypto coin must receive an exit decision even when its per-tick window is missing/`<min_bars` or it's absent from `universe`. Today entry (`+1`) ignores `held` while exit (`-1`) requires `held` *and* `universe`; a held-but-blind coin is silently skipped (`momentum.py` `if not universe: return` at :118, and the `-1 if sym in held else 0` sites). Fix in the shared crypto core so it covers all four strategies: expose the held set from actual positions and ensure each strategy emits an exit for held symbols regardless of that tick's data/universe membership.
- **1c. Temporary probe** — a debug `_log` in `core.held_symbols` emitting `universe`, `positions.keys()`, and the resulting `held` whenever a crypto position exists. Read from the one verification run, then removed.
- **1d. Purge poisoned `AlpacaBarsCache` crypto rows** (operational, RethinkDB): `r.db('IntelliStock').table('AlpacaBarsCache').filter(r.row['symbol'].match('/')).delete()`. Corrected fetch repopulates; removes the degenerate-window contribution.

### Fix 2 — 24/7 stepping

`backend/broker.py:10084-10097`. Add a crypto guard before the session gate:
```python
if _is_crypto_instance_runtime():
    current_time = current_time + backtest_increment_td      # 24/7, no skip
elif _is_within_trading_session_pt(current_time):
    current_time = current_time + backtest_increment_td
else:
    next_open = _next_market_open_utc(current_time)
    ...  # equity only
```
Pure stepping change — the loop body's execution gate is already crypto-aware (`:7723-7726` forces `within_session=True`). No other main-loop code assumes session-bounded steps (progress % is time-fraction based; `_orders_today` rollover is live-only; daily-bar guards key on `1Day`).

### Fix 3 — Slowness (O(n²) → O(n)), behavior-preserving on the live path

- **3a.** Give `_get_prices_at_time` (`broker.py:6364`) a per-symbol monotonic cursor + one-time parsed-timestamp view, mirroring the tested pattern its sibling `get_price_history_up_to_current` already uses (`backend/backtest_price_history.py`). Lookup becomes O(new_bars)/step. Cursor resets on time rewind (same invariant as the sibling).
- **3b.** `_bar_time_to_datetime` (`broker.py:6250`): try `datetime.fromisoformat` first (handle trailing `Z`), fall back to `dateutil.parser.parse` for anything non-ISO. Output identical for Alpaca ISO timestamps, ~50× faster.
- **Risk:** both are on the live path. Mitigation: output-identical by construction + parity tests; cursor mirrors an already-unit-tested module.

### Fix 4 — Running-chart truncation

`broker.py:9982-9984`. Replace the `[-3000:]` tail slice with a downsample that **keeps the first snapshot + ~3000 evenly-spaced points** (and the last), so a running backtest shows the true start and full shape. Finish path (`:7313`) already writes the full history — unchanged. Cap keeps the changefeed/DB row bounded.

### Fix 5 — NaN% price-change

Locate the per-stock `stock_price_change` / price-change-% computation (candidate: `backend/interactive_utils.py` summarize path). Guard the divide (missing/zero first price → `null`/`0.0`, rendered as `—` not `NaN%`).

## Gating & compatibility

- 1a, 1b, 2, 4, 5 are crypto-only or backtest-only. Fix 3 is shared but output-identical.
- Equity/`alpaca-main` stays byte-identical. Crypto gate: `_is_crypto_instance_runtime()` / `"/" in symbol`.

## Testing (TDD — failing test first for each)

- `position_qty` slash-symmetry (both directions) + `held_symbols` with mismatched slash forms.
- Held-position exit when the tick window is missing/`<min_bars` (momentum + shared core).
- 24/7 stepping: crypto advances by cadence across nights/weekends; equity still skips (unchanged).
- `_get_prices_at_time` cursor: output parity vs the old full-scan across a stepped sequence + rewind reset; perf (bounded per-step work).
- `_bar_time_to_datetime` `fromisoformat` parity vs `dateutil` for ISO/`Z`/offset inputs.
- Running-history downsample keeps t0 + last + ≤ cap.
- Existing 73 crypto tests stay green.

## Verification (one deploy)

1. Purge poisoned `AlpacaBarsCache` crypto rows.
2. Deploy backend + backtest container + frontend.
3. Run a fresh paper `crypto:momentum` backtest; via API confirm: **Sells > 0 / round-trips present**, **no "Skipped to next market open"** (steps at nights/weekends), **completes in minutes not hours**, probe log shows why `held` was empty (and that it's now populated), running chart starts ~$9,975, no NaN%.
4. Run a short equity backtest to confirm `alpaca-main` behavior is unchanged.
5. Remove the temporary probe (1c) once the cause is confirmed.

## Rollout

Commit to `feat/crypto-trading-platform`; updates PR #114. Push. On-server deploy + backtest verification is the final gate (needs server/Dokploy access).
