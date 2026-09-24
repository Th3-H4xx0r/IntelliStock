# swing-trader (tmasters2876) — port research

**Date:** 2026-09-24 · **Branch:** `research/swing-trader-port` · **Status:** research only, nothing ported
**Source:** https://github.com/tmasters2876/swing-trader (162 commits, first 2026-04-26)
**Method:** 3 read-only code audits (swing strategy, wheel + AI layer, IntelliStock integration surface) and 1 empirical replication (local yfinance, 503 names, 2010→2026-06).

## Verdict

**Do not port either strategy.**

- The swing strategy trails SPY after honest execution, both in and out of sample.
- Its RSI/MACD/ADX entry does no better than random entries that pass the same trend filter and use the same exits.
- The drawdown advantage comes from the SPY-200d/VIX regime filter, and that filter applied to SPY alone did as well or better.
- The options wheel has no backtest and five paper trades. It cannot run on IntelliStock (no options support, OPRA agreement not signed) and does not fit a $6k account.

## What the repo contains

| Piece | What it is | Evidence in the repo |
|---|---|---|
| Swing strategy (`paper_trader.py`) | Daily S&P 500 scan. Entry when RSI(14)<50 and rising, MACD histogram rising 1 bar, close>SMA200, volume>20d average, ADX>15, SPY>SMA200×1.03, VIX≤25, AI score≥50. Exits: +9%/−6% GTC bracket or RSI crossing 70. 12.5% per slot, max 8, one position per sector. After 10 blocked days, "bear mode" trades XLP/XLU/XLV/GLD/SHY. | Backtest +48.7% (2021-06→2026-06), no costs, no SPY comparison. Live record: AMGN (approved by hand) and NFLX (stopped out). |
| Options wheel (`wheel_trader.py`) | Weekly ~0.25Δ cash-secured puts on S&P names with ATR%≥3 (the effective screen). Sold at bid×0.95. Bought back at market if in the money. Covered-call leg is dry-run only. | No backtest. 5 paper puts: 2 expired worthless, 3 ITM or assigned; 1 order never filled. Premiums not logged for the losers. |
| AI analyst (`ai_analyst.py`) | Claude conviction score 0–100 from RSI/MACD, sector-ETF RSI, earnings date and web-search news. ≥75 trades automatically, 50–74 waits for dashboard approval. | Never calibrated: the gate needs 20 closed trades, zero recorded. Cannot be backtested point-in-time (news replay, model training cutoff). |

The repo's own CLAUDE.md (May 2026) says all trading runs `paper=True`, with a live account "ready for live switch when paper results justify it". It refers to a "$100k account".

## How the +48.7% headline was produced

- **No transaction costs** (`backtester.py:216-247`). Turnover is about 12× equity per year.
- **Survivorship:** the May-2026 S&P list is applied from 2021. On the reproduced path, 20 trades in names not yet in the index produced 46% of realised P&L (APP, ARES, XYZ, DECK, ...).
- **Variant chosen on the test half.** 18 configurations were swept, plus hand tuning. Three others (A, D, N) were also positive in both halves; L won on test-half return (`combo_results.json`).
- **Lucky ordering.** Over 100 random scan orders the same rules give a median of +30.5% (p5 +8.6, p95 +48.3). The committed number sits near the 95th percentile.
- **The backtest does not match live.** SPY buffer 1.02 vs 1.03; no position cap (it held up to 29 names); sizing off starting capital; the sector map covers 16 names, so the cap never binds.
- **Warm-up counted as protection.** The SMA200 warm-up starts at the window start, so about 9.5 of the "5 years" are 0% invested. Most "regime-blocked days" are warm-up.

## Replication (scratchpad `replication/`, fja05680 point-in-time S&P membership)

Reproduction: the repo code on current data gives +41.6% (291 trades; 257/289 match the committed run). The harness matches the repo engine exactly (291/291 trades).

**In-sample window, 2021-06-10 → 2026-06-10**

| Series | Total | CAGR | MaxDD | Sharpe |
|---|---|---|---|---|
| Strategy, repo rules, 0 bps (median of orders) | +30.5% | 5.5% | −14.5% | 0.54 |
| Strategy, 5 bps + membership | +20.9% | 3.9% | −15% | 0.41 |
| Strategy, 23 bps + membership | +7.5% | — | — | — |
| Live-faithful rules, 5 / 23 bps + membership | +15.9 / +2.9% | 3.0 / 0.6% | −14 / −16% | 0.36 / 0.11 |
| Random entries, same filters and exits, 5 bps + membership | +26 to +30% | ~5% | −14 to −16% | ~0.5 |
| **SPY total return** | **+83.4%** | **12.9%** | −24.5% | 0.80 |
| SPY with only the strategy's regime filter, else BIL | +50.4% | 8.5% | **−10.1%** | 0.92 |

**Frozen out-of-sample, 2012-01-03 → 2020-12-31** (rules never tuned here; survivorship worse — 30% of tradable names were not yet members)

| Series | Median total | CAGR | MaxDD | Sharpe |
|---|---|---|---|---|
| Frozen rules, 5 bps + membership | +165% | 11.4% | −12.7% | 1.07 |
| Frozen rules, 23 bps + membership | +116% | 8.9% | −14.7% | 0.85 |
| Live-faithful, 5 bps + membership | +139% | 10.1% | −11.6% | 1.01 |
| Random entries, 5 bps + membership | +140 to +145% | 10.2–10.5% | −14% | 0.95 |
| **SPY total return** | **+250.5%** | **15.0%** | −33.7% | 0.93 |
| SPY 83% + BIL (exposure-matched) | +191% | 12.6% | −28.7% | 0.94 |

The survivor universe still beats RSP by 3.4 pp/yr after the membership filter. A rough haircut for that puts true out-of-sample CAGR near 8.7% at 5 bps and 6.2% at 23 bps.

**Calendar years, strategy at 5 bps + membership vs SPY TR:**

| 2015 | 2018 | 2020 | 2022 (in-sample) |
|---|---|---|---|
| −3.5 vs +1.2 | −2.3 vs −4.6 | −1.2 vs +18.3 | −0.8 vs −18.2 (mostly in cash) |

## Porting feasibility in IntelliStock

- **Wheel:** impossible today. No option instrument, chain data or assignment handling; `all-regime-research.md:42` states "no options/futures".
- **Swing strategy:** possible but not faithful.
  - The Alpaca adapter supports market and limit orders only (`broker_adapters/alpaca.py:984`): no brackets or stops, and equity orders are always day orders.
  - There is no S&P constituent list, GICS map or VIX in the engine. Volume is IEX-only, and daily history starts 2020-07.
  - A port needs its own pure module, wrapper, `broker.py` universe helper, lane limits, a daily feature table for about 500 names, a lab doc and instance, and granularity `"86400"`. The SMA200 never forms at `"900"`.
- **Prior IntelliStock verdicts already cover these ideas:**
  - Short-term reversal, killed (`all-regime-research.md:67`).
  - "Do NOT build: pullback / first-higher-low entry" (`investigations/when-to-buy-signal.md:565`).
  - 200-day filters as a voter scored below the base rate (`strategy-x-design.md:70`).
  - Defensive hedge books failed (`all-regime-research.md:103`).
  - VIX-curve re-entry (VTS) killed.

## Worth borrowing, not porting

- **Counterfactual calibration for any LLM gate.** Log an outcome for every scored candidate, including rejects.
- **Re-apply LLM thresholds in code** and clamp size multipliers to an allowed set.
- **"Completed" markers** for scheduled jobs, distinct from "started" markers.
- **A pre-registered verdict rule before running variants** (`experiments/atr_verdict.py`): a variant must beat the control on expectancy and Sharpe in both halves.
