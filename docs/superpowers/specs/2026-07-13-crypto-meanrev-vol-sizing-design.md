# Crypto MeanRev — Volatility-Scaled Position Sizing

**Date:** 2026-07-13
**Branch:** feat/crypto-trading-platform (PR #114)
**Origin:** Investigation of backtest 775143 (crypto:meanrev, band=low, 8 coins, 3-month
bear quarter, Binance.US fees) → +12.4% P&L. Goal was higher, generalizable P&L
without curve-fitting to that window.

## Problem / motivation

Backtest 775143 already beats buy-and-hold by ~32 points (B&H −20% on the same
window). A rigorous lever sweep (9 families across the target window + 2 out-of-sample
windows, on top of the prior 229-config sweep) established:

- **The regime filter (`close > SMA200`) and the hard RSI-exit are load-bearing.**
  Every lever that loosens them (faster regime MA, "relative" regime, looser RSI entry,
  soft/laddered exit, junk-coin universe expansion) **breaks out-of-sample** — the exact
  overfit we must avoid. Robust ceiling on the bear quarter for this family is ~13–16%.
- **The one lever that generalizes: volatility-scaled position sizing.** In a full
  regime map (2023-24 bull, 2024 correction, late-2024 bull, 2025-26 bear, 775143), a
  volatility-weighted allocation **wins or ties equal-weight in every single regime**:

  | Regime | equal-wt (prod) | **vol-scaled** |
  |---|---|---|
  | 2023-24 bull | +57.5% | **+58.7%** |
  | 2024 correction (chop) | −2.6% | **0.0%** |
  | late-2024 bull | +7.1% | **+7.6%** |
  | 2025-26 bear | +24.6% | **+26.1%** |
  | 775143 (tgt bear) | +13.3% | **+13.4%** |

(Backtests run through the local fee-aware harness at Binance.US 0.02% taker;
the faithful `PortfolioEmulator` path is the acceptance gate — see Verification.)

**Honest scope note:** this does NOT turn 775143 into +30%. ~30% on a 3-month −20%
bear quarter is not reachable without overfitting; the +12–14% there is the strategy
doing its defensive job. Vol-scaling is a small, universally-robust upgrade — better in
bull, chop, AND bear — and that is exactly what we ship. The strategy remains a
bear/chop specialist that under-participates in bull markets (captures 13–32% of bull
upside); closing that gap is a separate, later "trend-participation" effort, explicitly
out of scope here.

## Goal (success criteria)

1. MeanRev sizes each dip-buy by the coin's volatility (ATR%) instead of a flat
   `pv/top_k`, deploying more capital into higher-bounce coins.
2. Faithful backtest (real `PortfolioEmulator`, Binance.US fee): **vol ≥ equal** on the
   775143 window AND the out-of-sample window (no regression anywhere).
3. Entry gate, exit rule, regime filter, top_k, universe, and the `run_once` return
   contract are **unchanged** → the bear/chop alpha (+38 to +78 pts) is preserved by
   construction.
4. **Connors strategy is unaffected** (it shares the sizing helper).

## Non-goals

- No trend/bull-participation mode (separate future spec).
- No universe expansion, no regime/RSI/exit retuning, no new coins.
- No web/mobile UI change required. (Optionally surface a `sizing` picker later; not
  in this spec.)

## Design

Single file of production change: `backend/strategies/crypto/meanrev.py` (+ its test).

### 1. Compute ATR% in the existing indicator loop

`Meanrev.run_once` already loops each universe symbol computing RSI + SMA over a
capped window (`closes = core.series(bars,"c")`, capped to `min_bars+300`). In the same
loop, also pull highs/lows and compute the latest ATR%:

```
highs = core.series(bars, "h"); lows = core.series(bars, "l")   # same cap slice
atr = talib.ATR(highs, lows, closes, timeperiod=atr_period)      # atr_period default 14
atr_pct_by[sym] = float(atr[-1]) / closes[-1]   if valid else None
```

No extra data pass; reuses the already-sliced window. `atr_period` is a config key
(default 14).

### 2. Volatility-aware sizing (backward-compatible helper)

`_apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k)` is **imported
and called by `connors.py`** (line 35 / 161, 4 positional args). To preserve Connors,
extend the signature with optional params that **default to today's equal-weight
behavior**:

```
def _apply_equal_weight_sizing(result, prices, portfolio_emulator, top_k,
                               atr_pct_by=None, sizing="equal"):
```

- `sizing="equal"` (default) or `atr_pct_by is None` → **exact current behavior**
  (`per = pv/top_k` for every buy). Connors calls this path untouched.
- `sizing="vol"` with `atr_pct_by` present → per-buy cash is volatility-weighted:

  ```
  ref = median(atr_pct across universe coins that have a valid ATR%)   # cross-sectional
  for each buy s with valid atr_pct[s]:
      weight = clamp(atr_pct[s] / ref, 0.6, 1.6)      # bounded, no over-concentration
      buy_cash[s] = round((pv / top_k) * weight, 2)
  buys with missing atr_pct fall back to pv/top_k
  ```

  This keeps the equal-weight *total* deployment but reweights slots by volatility,
  preserving the same relative ratio the backtest used (a 3%-ATR coin gets 3× a
  1%-ATR coin, subject to the clamp). Sells (`sell_fraction: 1.0`) and the
  `_cash_reserve_floor_pct` / `_buy_price_floor` markers are emitted exactly as today.

`Meanrev.run_once` computes `ref` (or passes `atr_pct_by` and lets the helper compute
it) and calls the helper with `atr_pct_by=atr_pct_by, sizing=sizing`.

### 3. Config surface

- New config key **`sizing`**: `"vol"` (new default for MeanRev) or `"equal"` (legacy /
  A-B). Read in `run_once` from merged `conditions`+`config`, like the other params.
- New config key **`atr_period`** (default 14).
- Update the `# INTELLISTOCK_SCHEMA:` comment's `config` to include `"sizing": "vol",
  "atr_period": 14` and note the meaning in `# INTELLISTOCK_DESCRIPTION`.

### Data flow (unchanged contract)

`run_once` → returns `{sym: 1|0|-1, "_nexus_position_sizes": {sym: {"buy_cash"|
"sell_fraction", "asset_class": "crypto"}, ...}}`. Broker executes sells then
`buy_cash`-sized buys via `PortfolioEmulator` exactly as before. Only the numeric
`buy_cash` per slot changes when `sizing="vol"`.

## Risk / safety

- Blast radius (manual grep; GitNexus index predates `strategies/crypto/`): callers of
  the sizing helper are `meanrev.run_once` (updated) and `connors.run_once` (preserved
  via defaults). `Meanrev.run_once` is invoked through the broker's dynamic crypto
  strategy dispatch for meanrev instances only. **Risk: LOW.**
- `run_once` still returns `{sym: 1|0|-1}` (never floats) and the same `_nexus_*` keys.
- Bounded clamp `[0.6, 1.6]` caps single-slot concentration (≤ 1.6·pv/top_k).
- Graceful degradation: short windows / NaN ATR → equal-weight fallback.

## Verification

1. **Unit test** (`backend/tests/test_crypto_meanrev_vol_sizing.py` or extend existing):
   - vol sizing assigns strictly more `buy_cash` to a higher-ATR coin than a lower-ATR
     coin when both are bought;
   - `sizing="equal"` (and missing `atr_pct_by`) reproduce equal `pv/top_k` exactly
     (Connors-path regression guard);
   - per-buy cash respects the clamp bounds; sizes are always dicts with `buy_cash` /
     `asset_class`, never bare floats; `_cash_reserve_floor_pct`/`_buy_price_floor`
     still emitted;
   - a missing-ATR coin in a mixed buy set falls back to `pv/top_k`.
2. **Faithful backtest** (adapt `scripts`/tmp `validate_prod.py`: real `PortfolioEmulator`,
   Binance.US 0.02%, cached bars): assert `vol_pnl >= equal_pnl` on the 775143 window
   AND the OOS window. Record both numbers in the PR/commit.
3. **Regression**: existing crypto test suite stays green (`test_crypto_*`), including
   Connors and the discovery-cache tests.

## Rollout

- Ships on `feat/crypto-trading-platform` (PR #114), paper-first. Default `sizing="vol"`
  applies to new/edited crypto MeanRev instances; `"equal"` remains selectable for A-B.
- No migration: existing backtests are immutable; new runs pick up the new default.
