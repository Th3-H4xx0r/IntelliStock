# size-vs-outcome — was bt 201039 a SIZING failure?

**Answer: no.** Size was near-uniform at 14.0% of NAV and is not the variable that
separated the winners from the losers. The allocator wanted ~14% of NAV for *every*
name it approved, in all three runs examined. What varied was (a) how much of that 14%
actually filled — a cash/ordering bug, not a conviction decision — and (b) how far into
its move the name already was when we bought it.

Scope: primary run **bt 201039** (2026-01-01..2026-03-01, `v2-let-run-core`, $6,000,
3600s, **+8.3398% / +$500.39**). Cross-checked against **bt 820236** (+12.3268%) and
**bt 613166** (+9.1651%). READ-ONLY: no code changed, no run started or stopped.

Data provenance — everything below is reconstructed from the runs, not from the code:
* fill tape: `GET /backtests/<id>/graph-data` -> `backtest_trades` (36 rows for 201039;
  matches `summary.total_trades=36`, `total_buys=19`, `total_sells=17`)
* NAV / positions / prices per bar: `summary.risk_metrics.equity_curve` (512 bars)
* per-name P&L: `summary.pnl_per_stock`, `summary.pnl_percent_per_stock`
* decisions and gates: `python3 scripts/pull_backtest_logs.py 201039` (40,323 lines)

---

## 1. Every fill in bt 201039 (36 rows), sized as % of NAV at the fill bar

`% NAV` = fill notional / portfolio value at that bar from the equity curve.
`order` is the simulator order id, which is also the execution sequence within a bar.

| order | bar (UTC) | ticker | side | qty | px | notional $ | NAV $ | % NAV | source |
|---|---|---|---|---|---|---|---|---|---|
| #01 | 2026-01-02 15:00 | NTR | buy | 13.5674 | 61.9115 | 840.00 | 5,991.75 | 14.02 | main_signal |
| #02 | 2026-01-02 15:00 | TCMD | buy | 28.9384 | 28.1593 | 814.91 | 5,991.75 | 13.60 | main_signal |
| #06 | 2026-01-02 15:00 | TCMD | buy | 8.2681 | 28.1593 | 232.83 | 5,991.75 | 3.89 | main_signal |
| #03 | 2026-01-02 15:00 | VOYA | buy | 11.1778 | 75.1467 | 840.00 | 5,991.75 | 14.02 | main_signal |
| #04 | 2026-01-02 15:00 | XOM | buy | 6.9646 | 120.2447 | 837.49 | 5,991.75 | 13.98 | main_signal |
| #05 | 2026-01-02 16:00 | SPY | buy | 3.5118 | 682.9704 | 2,398.56 | 5,984.50 | 40.08 | residual_bull_deploy |
| #07 | 2026-01-02 16:00 | BA | buy | 0.1561 | 222.7990 | 34.77 | 5,984.50 | 0.58 | main_signal |
| #08 | 2026-01-05 16:00 | SPY | sell | 1.1089 | 686.7438 | 761.48 | 6,115.05 | 12.45 | residual_bull_refill |
| #09 | 2026-01-06 16:00 | SPY | buy | 0.9213 | 690.6781 | 636.36 | 6,127.04 | 10.39 | residual_bull_deploy |
| #10 | 2026-01-06 16:00 | AAL | buy | 5.5416 | 16.0216 | 88.79 | 6,127.04 | 1.45 | main_signal |
| #11 | 2026-01-07 16:00 | SPY | sell | 0.9325 | 690.9092 | 644.26 | 6,128.41 | 10.51 | residual_bull_refill |
| #12 | 2026-01-12 16:00 | SPY | sell | 0.0111 | 692.3260 | 7.71 | 6,121.11 | 0.13 | residual_bull_refill |
| #13 | 2026-01-12 16:00 | SPY | buy | 0.8007 | 695.5041 | 556.90 | 6,121.11 | 9.10 | residual_bull_deploy |
| #14 | 2026-01-13 16:00 | SPY | sell | 0.7970 | 692.1713 | 551.62 | 6,148.94 | 8.97 | residual_bull_refill |
| #15 | 2026-01-15 16:00 | TCMD | sell | 37.2065 | 30.4501 | 1,132.91 | 6,293.99 | 18.00 | main_signal |
| #16 | 2026-01-16 16:00 | PLRZ | buy | 56.6723 | 15.4754 | 877.05 | 6,258.36 | 14.01 | main_signal |
| #17 | 2026-01-20 16:00 | SPY | buy | 1.1714 | 685.3759 | 802.86 | 6,239.96 | 12.87 | residual_bull_deploy |
| #18 | 2026-01-20 16:00 | VOYA | sell | 11.1778 | 74.5589 | 833.38 | 6,239.96 | 13.36 | main_signal |
| #19 | 2026-01-20 16:00 | AVNT | buy | 24.5132 | 35.6364 | 873.59 | 6,239.96 | 14.00 | main_signal |
| #20 | 2026-01-21 16:00 | SPY | sell | 1.0907 | 683.2019 | 745.15 | 6,192.11 | 12.03 | residual_bull_refill |
| #21 | 2026-01-26 16:00 | SPY | buy | 1.0250 | 694.5619 | 711.98 | 6,369.06 | 11.18 | residual_bull_deploy |
| #22 | 2026-01-27 16:00 | SPY | sell | 1.0203 | 694.4062 | 708.45 | 6,319.38 | 11.21 | residual_bull_refill |
| #23 | 2026-01-27 16:00 | AAL | sell | 5.5416 | 14.0478 | 77.84 | 6,319.38 | 1.23 | main_signal |
| #24 | 2026-01-27 16:00 | HL | buy | 4.6169 | 28.2345 | 130.36 | 6,319.38 | 2.06 | main_signal |
| #25 | 2026-01-29 16:00 | SPY | sell | 0.0102 | 684.4590 | 6.97 | 6,274.46 | 0.11 | residual_bull_refill |
| #27 | 2026-01-30 16:00 | SPY | sell | 0.0451 | 690.5750 | 31.17 | 6,205.97 | 0.50 | residual_bull_refill |
| #28 | 2026-01-30 16:00 | AVNT | sell | 24.5132 | 36.0373 | 883.36 | 6,205.97 | 14.23 | main_signal |
| #29 | 2026-01-30 16:00 | WDC | buy | 2.9754 | 259.3726 | 771.76 | 6,205.97 | 12.44 | main_signal |
| #26 | 2026-01-30 17:00 | PLRZ | sell | 56.6723 | 12.7507 | 722.59 | 6,142.96 | 11.76 | main_signal |
| #30 | 2026-01-30 18:00 | HL | sell | 4.6169 | 23.0022 | 106.20 | 6,091.07 | 1.74 | main_signal |
| #31 | 2026-02-02 16:00 | SPY | sell | 0.0159 | 693.1291 | 10.99 | 6,145.99 | 0.18 | residual_bull_refill |
| #34 | 2026-02-02 16:00 | SPY | buy | 0.0809 | 696.3109 | 56.35 | 6,145.99 | 0.92 | residual_bull_deploy |
| #32 | 2026-02-02 16:00 | EGO | buy | 22.1364 | 38.8688 | 860.44 | 6,145.99 | 14.00 | main_signal |
| #33 | 2026-02-02 16:00 | SNDK | buy | 1.3027 | 660.4791 | 860.44 | 6,145.99 | 14.00 | main_signal |
| #35 | 2026-02-03 16:00 | SPY | sell | 0.0463 | 691.5228 | 32.04 | 6,364.42 | 0.50 | residual_bull_refill |
| #36 | 2026-02-05 16:00 | SPY | sell | 0.0262 | 675.7889 | 17.68 | 6,028.03 | 0.29 | residual_bull_refill |

Cost check: spread $46.71 + slippage $0.20 + fees $0.61 = **$47.53 (0.79% of starting
NAV)** on $20,499.23 of gross traded notional (342% of NAV in 58 days). $8,680.52 of
that gross — **42%** — was the SPY sleeve trading against itself (section 6).

---

## 2. Position size at entry, per name

`size %NAV` is total buy notional for the name / NAV at its first buy bar.
`capture` = our return / the stock's window move.

| ticker | entry (UTC) | intent | size $ | size %NAV | exit | days | stock move % | our ret % | capture | P&L $ |
|---|---|---|---|---|---|---|---|---|---|---|
| XOM | 2026-01-02 15:00 | initial_buy | 837.49 | 13.98 | held to end | 56.4 | +26.81 | +26.90 | 1.00 | +225.25 |
| NTR | 2026-01-02 15:00 | initial_buy | 840.00 | 14.02 | held to end | 56.4 | +21.59 | +21.23 | 0.98 | +178.30 |
| EGO | 2026-02-02 16:00 | initial_buy | 860.44 | 14.00 | held to end | 25.3 | +29.26 | +19.45 | 0.66 | +167.35 |
| TCMD | 2026-01-02 15:00 | momentum_watchlist_buy | 1,047.74 | 17.49 | 2026-01-15 16:00 | 13.0 | +1.12 | +8.13 | 7.24 | +85.17 |
| WDC | 2026-01-30 16:00 | momentum_watchlist_portfolio_swap | 771.76 | 12.44 | held to end | 28.3 | +61.91 | +7.54 | 0.12 | +58.17 |
| AVNT | 2026-01-20 16:00 | initial_buy | 873.59 | 14.00 | 2026-01-30 16:00 | 10.0 | +31.40 | +1.12 | 0.04 | +9.77 |
| BA | 2026-01-02 16:00 | initial_buy | 34.77 | 0.58 | held to end | 56.3 | +4.79 | +2.11 | 0.44 | +0.73 |
| VOYA | 2026-01-02 15:00 | initial_buy | 840.00 | 14.02 | 2026-01-20 16:00 | 18.0 | -10.22 | -0.79 | 0.08 | -6.62 |
| AAL | 2026-01-06 16:00 | initial_buy | 88.79 | 1.45 | 2026-01-27 16:00 | 21.0 | -14.59 | -12.33 | 0.84 | -10.94 |
| HL | 2026-01-27 16:00 | momentum_watchlist_buy | 130.36 | 2.06 | 2026-01-30 18:00 | 3.1 | +29.70 | -18.54 | -0.62 | -24.16 |
| SNDK | 2026-02-02 16:00 | momentum_watchlist_buy | 860.44 | 14.00 | held to end | 25.3 | +166.10 | -4.38 | -0.03 | -37.73 |
| PLRZ | 2026-01-16 16:00 | momentum_watchlist_rotation | 877.05 | 14.01 | 2026-01-30 17:00 | 14.0 | +61.84 | -17.61 | -0.28 | -154.46 |

**The allocator has one number and it is 14%.** Across the 39 `PASS` buy-gate lines in
bt 201039 the requested size (`cash_per_trade`) reads $840.00, $817.00, $758.67,
$674.39, $667.56, $690.42, $687.61, $661.79, $877.05, $871.76, $873.59, $837.59,
$824.90, $824.82, $819.56, $838.79, $786.70, $795.14, $826.41, $860.44 — i.e.
**13.9%–14.0% of live NAV every single time**. Log format at `backend/broker.py:15347`:

```
[2026-08-08 23:48:30] [BROKER] Buy gate inputs for SNDK: cash=$1766.81 reserved=$0.00
  floor=$120.00 effective_floor=$120.00 high_conv=True open_pos=4
  cash_per_trade=$860.44 available=$1646.81 cash_to_use=$860.44 -> PASS
[2026-08-08 23:48:30] [BROKER] Buy gate inputs for EGO: ... cash_per_trade=$860.44 ... -> PASS
```

SNDK — the -$37.73 name that bought the top — and EGO — the +$167.35 name — were
approved at **the identical dollar size, on the identical bar, by the identical line of
code**. There is no conviction-to-size mapping to sharpen here.

---

## 3. Were the winners sized BIGGER or SMALLER than the losers?

Using the parent's grouping:

| group | names | P&L | mean size (%NAV) | mean size ($) |
|---|---|---|---|---|
| winners | XOM, NTR, EGO, TCMD | +$656.06 | 14.87% | $896.42 |
| losers | SNDK, PLRZ, HL, AAL, VOYA | -$233.91 | 9.11% | $559.33 |
| losers **excluding HL + AAL** | SNDK, PLRZ, VOYA | -$198.81 | **14.01%** | $859.16 |

The headline "winners were bigger" is **entirely** HL (2.06% of NAV) and AAL (1.45%).
Remove those two and the winners averaged 14.87% and the losers 14.01% — a 0.86pp gap,
and even that is one name (TCMD took a $232.83 second tranche).

Nine of the twelve satellite names got a full-size fill. Their sizes span
**12.44%–17.49% of NAV (mean 14.22%, sd 1.33pp)** and their P&L spans
**-$154.46 to +$225.25**. Same size; $380 of spread in outcome.

### Correlation of size with outcome

| sample | pearson(size, return) | spearman | p (spearman) |
|---|---|---|---|
| bt 201039, all 12 satellite names | +0.479 | +0.161 | 0.616 |
| bt 201039, the 9 full-size names only | -0.039 | **-0.252** | 0.513 |
| bt 820236, 7 names | — | +0.519 | 0.233 |
| bt 613166, 9 names | — | -0.067 | 0.865 |
| **pooled, 28 satellite positions across 3 runs** | **+0.182** | **+0.226** | **0.248** |

Direction is weakly positive, the sign is unstable across runs (+0.52 / +0.16 / -0.07),
and nothing is significant. **Size does not predict outcome in this system, in either
direction.** Where the pooled correlation does look positive, it is because a cash bug
happened to starve two losers (section 5).

---

## 4. Equal-weight counterfactual — separating selection from sizing

Method: hold names, entry bars, exit bars and per-position realized return
(`pnl_percent_per_stock`) fixed; change only the dollars.
`sum(w_i·r_i) = n·w̄·r̄ + n·Cov(w,r)`, where the second term is the sizing decision.
Caveat: overlapping holding periods mean strict equal weight is not literally fundable;
this measures the sizing *decision*, not a runnable schedule.

| run | actual satellite P&L | equal weight, same total capital | uniform 14% ($840/name) | sizing term `n·Cov(w,r)` |
|---|---|---|---|---|
| **201039** | **+$490.83** | +$220.52 (**-$270.31**) | +$275.70 (-$215.13) | **+$270.31** |
| 820236 | +$727.18 | +$663.23 (-$63.94) | +$792.62 (+$65.45) | +$63.94 |
| 613166 | +$518.27 | +$266.04 (-$252.22) | +$374.77 (-$143.49) | +$252.22 |

At equal weight **bt 201039 returns +3.78% instead of +8.34%** (satellite +$220.52 plus
SPY +$6.17 on $6,000). On the surface, "sizing skill" was worth +$270.31.

**It was not skill.** Decomposing +$270.31 by name:

| ticker | size %NAV | return | contribution to the sizing term |
|---|---|---|---|
| HL | 2.06% | -18.54% | **+$115.19** |
| AAL | 1.45% | -12.33% | **+$87.81** |
| XOM | 13.98% | +26.90% | +$40.01 |
| EGO | 14.00% | +19.45% | +$31.52 |
| NTR | 14.02% | +21.23% | +$31.09 |
| TCMD | 17.49% | +8.13% | +$20.27 |
| WDC | 12.44% | +7.54% | +$4.80 |
| BA | 0.58% | +2.11% | +$3.97 |
| AVNT | 14.00% | +1.12% | -$3.26 |
| VOYA | 14.02% | -0.79% | -$5.92 |
| SNDK | 14.00% | -4.38% | -$13.43 |
| PLRZ | 14.01% | -17.61% | -$41.75 |

**$203.00 of the $270.31 (75%) is HL and AAL being accidentally small.** Section 5 shows
those two sizes were never chosen. The same term is +$63.94 in bt 820236 (BA being small
was lucky; SNDK being small cost money) and +$252.22 in bt 613166 (PLRZ small was lucky;
EGO small cost money). It is a coin flip that landed heads twice.

**Selection, not sizing, is what carried this run.** Equal-weighted, the same twelve
names at the same times still make +$220.52 on $8,062 of satellite capital (+2.7%), and
the run is still positive.

---

## 5. Where the size variance actually comes from: three exact reconstructions

Every non-14% fill in bt 201039 is a cash-availability accident, reproducible from the
tape's `order_id` sequence and `cash_after` chain.

### BA — approved $817.00, filled $34.77 (4.3%). Cause: the SPY core jumped the queue.

```
[2026-08-08 23:12:35] [BROKER] SATELLITE CAP: BA trimmed $839 -> $817 to keep the core at target
[2026-08-08 23:12:35] [BROKER] TURNOVER BUDGET BYPASS: BA raw=+1.700 >= 1.50 — admitting a conviction buy ...
[2026-08-08 23:12:35] [BROKER] Buy gate inputs for BA: cash=$2434.77 reserved=$0.00 floor=$120.00
    effective_floor=$120.00 high_conv=True open_pos=4 cash_per_trade=$817.00
    available=$2314.77 cash_to_use=$817.00 -> PASS
```

Next bar (2026-01-02 16:00), in execution order:

| order | ticker | side | notional | cash_after | source |
|---|---|---|---|---|---|
| #05 | SPY | buy | $2,398.56 | $36.21 | residual_bull_deploy |
| #07 | BA | buy | $34.77 | $1.44 | main_signal |

$36.21 + $2,398.56 = $2,434.77 — **exactly** the cash the BA gate read. The gate printed
`reserved=$0.00` while $2,398.56 of core SPY was already in flight. Buys are sorted
`(intent_priority, -allocation, ticker)` (`backend/broker.py:13770-13778`, log line
`Execution order: 1 sell(s) first, then 114 buy/hold candidate(s) by (intent_priority,
allocation, ticker)` at `broker.py:13778`), so the largest order — always the core
deploy — executes first and eats the cash.

### AAL — approved $758.67, filled $88.79 (11.7%). Same cause.

```
[2026-08-08 23:17:40] [BROKER] Buy gate inputs for AAL: cash=$763.22 reserved=$0.00 floor=$120.00
    effective_floor=$0.00 high_conv=True open_pos=5 cash_per_trade=$758.67
    available=$763.22 cash_to_use=$758.67 -> PASS
```

| order | ticker | side | notional | cash_after |
|---|---|---|---|---|
| #09 | SPY | buy | $636.36 | $126.86 |
| #10 | AAL | buy | $88.79 | $38.08 |

### HL — approved $838.79, filled $130.36 (15.5%). Cause: the gate read PRE-SELL cash.

```
[2026-08-08 23:40:37] [BROKER] SATELLITE CAP: HL trimmed $887 -> $839 to keep the core at target
[2026-08-08 23:40:37] [BROKER] Buy gate inputs for HL: cash=$132.00 reserved=$0.00 floor=$120.00
    effective_floor=$0.00 high_conv=True open_pos=6 cash_per_trade=$838.79
    available=$132.00 cash_to_use=$132.00 -> PASS
```

Next bar (2026-01-27 16:00):

| order | ticker | side | notional | cash_after |
|---|---|---|---|---|
| #22 | SPY | sell | $708.45 | $840.46 |
| #23 | AAL | sell | $77.84 | $918.30 |
| #24 | HL | buy | $130.36 | $787.94 |

The funding arrived **before** HL executed — cash was $918.30 at the moment of the buy —
but HL had already been sized off the stale $132.00. **$787.94 then sat idle.** This is
`_SYNTHESIS` root cause #1 (the cash race), independently confirmed in bt 201039.

### Aggregate across the three runs

| run | buy-gate PASS lines | distinct names approved | $ approved | names that filled | $ filled (satellite) | $ approved on names that filled $0 |
|---|---|---|---|---|---|---|
| 201039 | 39 | 33 | $25,733.23 | 12 | $8,062.43 (31.3%) | $15,498.95 |
| 820236 | 58 | 47 | $35,743.46 | 7 | $4,920.15 (13.8%) | $27,623.98 |
| 613166 | 25 | 18 | $13,020.85 | 9 | $5,366.67 (41.2%) | $3,903.55 |

Also in bt 201039: **41 core funding requests totalling $76,321 released $18,482
(24.2%)** (`[core] funding request trimmed $X -> $Y`), and **74 `SATELLITE CAP` trims**.
The book sat at `open_pos=6` (`max_positions=6`) on 28 of the 39 PASS lines.

**Honest counterfactual on the three starved fills:** had BA/AAL/HL filled at their
approved size, bt 201039 would have made **$197.37 LESS** (BA +$16.52, AAL -$82.56,
HL -$131.32). In bt 820236 the same bug cost money: SNDK was approved six times for
$2,217.49 and filled $490.84; at its 14% target it adds **+$71.80**, and BA was approved
$838.87 and filled $107.03. **The bug's sign is a coin flip; its variance is not.**
Fixing it is a measurement/determinism argument, not a P&L argument — and its real cost
is the 21 / 40 / 9 approved names that filled $0, whose counterfactual P&L cannot be
measured from these runs (see `_SYNTHESIS` "THE PRIZE" for the 820236 upper bound).

---

## 6. How much of NAV was in SPY over time, and what did it cost?

Time-weighted from the 512-bar equity curve
(`positions_snapshot['SPY'] × prices['SPY'] / value`, weighted by bar duration):

| run | SPY %NAV (time-wtd) | avg $ in SPY | SPY P&L | SPY return on avg balance | satellite %NAV | avg $ satellite | satellite P&L | satellite return | idle cash %NAV |
|---|---|---|---|---|---|---|---|---|---|
| **201039** | **27.2%** | $1,700.58 | **+$6.17** | **+0.36%** | 63.3% | $3,963.02 | +$490.83 | **+12.39%** | 9.5% |
| 820236 | 28.4% | $1,844.35 | +$8.76 | +0.48% | 58.7% | $3,814.51 | +$727.18 | +19.06% | 12.9% |
| 613166 | 29.1% | $1,809.33 | +$28.04 | +1.55% | 56.5% | $3,510.75 | +$518.27 | +14.76% | 14.3% |

bt 201039 sleeve weights over time (daily mean, every 5th day):

| date | spy_pct | sat_pct | cash_pct | nav |
|---|---|---|---|---|
| 2026-01-01 | 0.00 | 0.00 | 100.00 | 6,000.00 |
| 2026-01-07 | 32.34 | 61.75 | 5.91 | 6,077.51 |
| 2026-01-13 | 31.14 | 61.85 | 7.01 | 6,149.68 |
| 2026-01-19 | 26.48 | 58.40 | 15.12 | 6,226.90 |
| 2026-01-24 | 26.63 | 60.15 | 13.22 | 6,380.29 |
| 2026-01-30 | 27.31 | 52.03 | 20.66 | 6,136.84 |
| 2026-02-05 | 27.11 | 72.21 | 0.68 | 5,948.23 |
| 2026-02-11 | 26.27 | 72.93 | 0.80 | 6,447.79 |
| 2026-02-17 | 25.81 | 73.39 | 0.80 | 6,290.71 |
| 2026-02-23 | 25.62 | 73.58 | 0.80 | 6,428.18 |
| 2026-02-28 | 25.41 | 73.80 | 0.80 | 6,500.39 |

After day 1 the SPY weight **never went below 25.35%** (min 25.35, p25 25.93, median
26.70, p75 27.14, max 39.90). `core_min_pct=0.25` / `core_max_pct=0.40` is a hard floor
and the run lived on it for two months.

**Opportunity cost, measured inside each run** — the SPY dollars re-priced at that same
run's own satellite rate. No hindsight, no outside data, no cross-run borrowing:

| run | avg SPY slug | at that run's own satellite rate | actual | opportunity cost | as % of starting NAV |
|---|---|---|---|---|---|
| 201039 | $1,700.58 | +$210.62 | +$6.17 | **-$204.45** | **-3.41%** |
| 820236 | $1,844.35 | +$351.60 | +$8.76 | -$342.84 | -5.71% |
| 613166 | $1,809.33 | +$267.10 | +$28.04 | -$239.06 | -3.98% |

Three for three, -3.4pp to -5.7pp per two-month window. Two secondary costs:

1. **Churn.** 17 of 36 fills in bt 201039 were SPY: 6 buys / 11 sells, $8,680.52 gross
   (42% of all traded notional), to end holding $1,651.67. SPY itself returned +0.64%
   over the window; the sleeve returned +0.36% on its average balance.
   `summary.sleeve_churn`: `churn_ratio 5.28, side_flips 11`.
2. **It starves the satellite.** The BA and AAL cases in section 5 are the SPY core
   deploy consuming cash the satellite gate had already approved: **$1,575.67 of
   approved satellite notional became $123.56 of fills** at those two bars.

Separately, an average **$594.02 (9.5% of NAV)** sat in cash earning $0; at the run's own
satellite rate that is another **-$73.57**.

---

## 7. What size does NOT explain — and the variable that does

If size is flat, why did the big movers produce nothing? Because we bought them after
the move. Entry price relative to the window's start price:

| ticker | window move | entry px vs start px | our return | capture |
|---|---|---|---|---|
| SNDK | +166.10% | **+178.30%** (bought ABOVE the window's end price) | -4.38% | -0.03 |
| PLRZ | +61.84% | **+90.82%** | -17.61% | -0.28 |
| HL | +29.70% | **+47.13%** | -18.54% | -0.62 |
| WDC | +61.91% | **+50.56%** | +7.54% | 0.12 |
| AVNT | +31.40% | +14.02% | +1.12% | 0.04 |
| EGO | +29.26% | +8.21% | +19.45% | 0.66 |
| NTR | +21.59% | +0.29% | +21.23% | 0.98 |
| XOM | +26.81% | -0.07% | +26.90% | 1.00 |

Pooled over the 18 positions across the 3 runs whose stock moved >= +10%:

* `spearman(pre-entry move, capture) = -0.765, p = 0.0002`
* `spearman(size, capture) = +0.274, p = 0.272`
* entered <= +10% into the move (n=10): mean capture **0.80**, total **+$1,924.30**
* entered  > +10% into the move (n=8):  mean capture **-0.12**, total **-$62.89**
* Mann-Whitney (early capture > late capture): **p = 0.00009**. Same test on size: p = 0.107.

**Caveat, stated because it matters:** "pre-entry move" is measured against the
*backtest window's* start price, which is an artifact of where the window was cut and is
~0 by construction for anything bought on day 1. Restricting to non-day-1 entries only
(n=12 positive movers across the 3 runs), the split survives — entries at <=+10% into
the move (EGO ×2, AGMI, PLD) captured 0.66–0.99; entries above it (SNDK ×3, PLRZ ×2, HL,
WDC, AVNT) captured -0.62 to +0.12. But "how far into the move" is **not knowable ex
ante**; section 8 shows what happens when you try to proxy it.

Mechanism detail visible in the log (bt 201039):

* **The extension gate is a ~3-trading-day window.** `entry_extension_block_pct=25`,
  `entry_extension_lookback_bars=20`, scaled by `_scale_bars`
  (`backend/strategies/graph_nexus_analysis.py:257-286`) and evaluated as
  `(max-min)/min` over the last N bars (`_recent_runup_protect`,
  `graph_nexus_analysis.py:9259-9281`; call sites `graph_nexus_analysis.py:23232-23295`
  discovery lane, `:5532-5548` momentum lane). It cannot see a 30-day parabola. Measured
  on this run's own bars at SNDK's entry: 20-bar runup **+28.9%**, 60-bar **+43.0%**,
  120-bar **+110.2%**. SNDK never appears in a single extension-gate line.
* **The momentum lane re-admits gate-blocked names.** PLRZ was blocked twice —
  `Entry extension gate: PLRZ recent runup +106.2% > 25% — buy blocked` and later
  `+83.6% > 25%` — then bought 2026-01-16 via `action_intent=momentum_watchlist_rotation`
  for -$154.46. `momentum_watchlist_track_extension_blocked=True` in this config is
  exactly the switch that keeps blocked names alive for that lane.
* **Decision-bar to fill-bar drift on this run's entries.** SNDK's decision bar printed
  `SNDK @ 2026-02-02 15:00:00 ($617.375)`; the fill landed at **$660.4791** the next bar,
  **+6.98%**, costing **$60.07** on an $860.44 order. Run total on satellite buys:
  +$77.25 (0.96% of buy notional). **This does not generalize** — the same measurement is
  **-$24.78 (-0.50%, favourable)** in bt 820236 and +$12.88 (+0.24%) in bt 613166. Noted
  as a bt-201039 fact, not a lever.

---

## 8. Negative result #1: do NOT convert this into a runup block

The obvious "fix" — block or shrink an entry whose trailing runup is high — was tested on
all three runs. Rule: block a new entry whose trailing 60-bar (~9 trading days) runup
exceeds a threshold, measured on each run's own price bars. `$` is the P&L the rule
**deletes** (positive = the rule destroys profit):

| threshold | 201039 | 820236 | 613166 | total |
|---|---|---|---|---|
| 20% | +$9.17 | -$19.47 | +$13.77 | **+$3.47** |
| 25% | +$9.17 | +$39.96 | +$13.77 | **+$62.90** |
| 30% | +$9.17 | +$39.96 | +$13.77 | **+$62.90** |
| 35% | -$216.35 | +$100.95 | -$15.43 | -$130.83 |
| 40% | -$216.35 | +$100.95 | +$3.04 | -$112.36 |
| 50% | -$154.46 | $0.00 | $0.00 | -$154.46 |

No threshold helps all three runs, and the sign flips between 201039 and 820236 at every
threshold from 25% up. At 25% the rule deletes EGO (+$167.35 in 201039, +$29.20 in
613166) and SNDK (+$100.95 in 820236) alongside PLRZ and HL. The gate cannot tell EGO
from SNDK because the feature it measures — recent runup — is the same for both.
Consistent with `docs/OBJECTIVE.txt` ("Loosening the entry-extension gate: blocked basket
returned -7.95%") and `_SYNTHESIS` ("trailing_stop ... re-arming it would have exited ALL
FIVE big winners"). **Do not tune the extension gate on this evidence.**

## 9. Negative result #2: do NOT down-size the `momentum_watchlist_*` lane

In bt 201039 the momentum lane looks like the villain. Across three runs it is not:

| run | `momentum_watchlist_*` entries | lane P&L | `initial_buy` P&L |
|---|---|---|---|
| 201039 | TCMD +85.17, WDC +58.17, HL -24.16, SNDK -37.73, PLRZ -154.46 | **-$73.01** | +$563.84 |
| 820236 | SNDK +100.95, OMER -60.99 | +$39.96 | +$687.23 |
| 613166 | AGMI +341.72, HESM +10.19, SNDK +3.04, PLRZ -18.47 | **+$336.48** | +$181.79 |

In bt 613166 the lane produced **AGMI (+$341.72), the largest single-position P&L in any
of the three runs**, and out-earned the `initial_buy` basket. Halving every lane entry is
**+$36.51 / -$19.98 / -$168.24 = -$151.71 net**. The lane is not the discriminator;
*entry lateness* is, and the lane also produced the two earliest, best entries in 613166.

---

## 10. Ranked list — what to change

Ordered by (evidence strength × dollars). Each item states a mechanism and is checked on
bt 201039 **and** at least one of bt 820236 / bt 613166, per the generalizability
constraint. Items 4–6 are things NOT to do.

### 1. Reserve in-flight order cash before the buy gate reads it; settle same-bar sells before same-bar buys

* **Mechanism.** `Buy gate inputs` prints `reserved=$0.00` while $2,398.56 of SPY is in
  flight (BA case), and reads pre-sell cash while the funding sell is queued ahead of the
  buy in the same bar (HL case). Make `available` net of accepted-but-unfilled orders,
  and order same-bar settlement so credits precede debits.
  `backend/broker.py:15347` (gate), `backend/broker.py:13770-13778` (`_buy_sort_key`).
* **Expected effect.** An approved 14% buy fills 14%. Removes a variance term worth
  **+$270.31 / +$63.94 / +$252.22** across the three runs — currently the single largest
  driver of the apparent size↔outcome relationship, and it is pure noise. It does **not**
  by itself add P&L: filling the three starved names in bt 201039 is **-$197.37**;
  filling SNDK's approvals in bt 820236 is **+$71.80**. Ship it so every later
  measurement is interpretable, not because it is expected to make money.
* **Generalizes: yes.** A clock/accounting bug, independent of window, regime and name.
  Evidence: 3/3 runs; exact `cash_after` chains in section 5; approved-vs-filled table.
* **Risk.** It raises effective exposure. Validate with a paired run per
  `docs/OBJECTIVE.txt`, not a lever sweep, and re-run bt 342380 (bear) as a control.

### 2. Lower the SPY core floor in bull/chop only, and stop the core front-running satellite fills

* **Mechanism.** `core_min_pct=0.25` pinned SPY at a time-weighted 27.2% / 28.4% / 29.1%
  of NAV, never below 25.35% after day 1, returning +0.36% / +0.48% / +1.55% on its
  average balance while the satellite sleeve returned +12.39% / +19.06% / +14.76%.
  Separately `_buy_sort_key` sorts by `-allocation`, so the core deploy — always the
  largest order — executes first and eats satellite cash.
* **Expected effect.** **-3.41% / -5.71% / -3.98% of starting NAV** is what the slug
  costs today at each run's own satellite rate (3/3, mean 4.4pp). A 25% -> 15% floor
  recovers roughly 40% of that, i.e. **+1.4 to +2.3pp per two-month window**, plus the
  two starved fills in section 5. Do **not** add this to item 3's estimate; they overlap.
* **Generalizes: the mechanism yes, the magnitude no.** A hard floor on a near-zero-return
  asset that also wins the execution queue is structural. But it is worth this much only
  when SPY is flat, and these three runs share one window (SPY +0.64%). In a window where
  SPY leads, the same change is negative. Test on >= 3 windows including one where SPY
  outperforms, per `docs/OBJECTIVE.txt`.
* **Hard constraint.** Scope to bull/chop. `docs/OBJECTIVE.txt` is explicit that doc-193
  has no bear profile on purpose and that the core being OFF in a bear is what lets the
  SQQQ hedge run. Do not touch `core_min_pct` globally; bt 342380 (+18.71%, bear) is the
  control that must not move.

### 3. Route residual cash to an existing satellite position before it goes to SPY

* **Mechanism.** Time-weighted idle cash was 9.5% / 12.9% / 14.3% of NAV earning $0, and
  its current sink is `residual_bull_deploy` into SPY — which is the same order that
  starves approved satellite buys (section 5). Sending residual cash to an already-held,
  already-working satellite name uses capital the book has, without opening a new
  late-entry position.
* **Expected effect.** Bounded above by idle-cash % × the satellite rate =
  **+0.9 to +2.7pp per window** (201039: $594.02 idle × 12.39% = +$73.57). Overlaps
  item 2 — count once.
* **Generalizes: mechanism yes, magnitude regime-dependent.** Must be gated to bull/chop
  for the same reason as item 2; in a bear, adding to held names is the wrong direction.
* **Supporting signal, explicitly NOT evidence (n=2).** The only two scale-ins in the
  three runs were the two best relative outcomes: TCMD (13.60% + 3.89% = 17.49% of NAV,
  +8.13% on a stock that moved +1.12%) and AGMI in bt 613166 (scaled to 18.21% of NAV,
  **+$341.72**). Per `docs/OBJECTIVE.txt`, n=2 is not a promotion case; it is a reason to
  design the test.

### 4. Do NOT tighten the entry-extension gate or size by runup

* Section 8. Net negative at 20/25/30% (-$3.47 / -$62.90 / -$62.90 across three runs) and
  sign-unstable above that. Deletes EGO (+$196.55 across two runs) and SNDK-in-820236
  (+$100.95). **Expected effect of doing it: negative.** Recorded so it is not retried.

### 5. Do NOT down-size or gate the `momentum_watchlist_*` lane

* Section 9. Lane P&L is -$73.01 / +$39.96 / **+$336.48**; halving it is -$151.71 net.
  It is negative on exactly one of three runs. Fitting to bt 201039 here would delete
  bt 613166's best name.

### 6. Do NOT pursue "size the winners bigger" as a lever

* Sections 2–3. The allocator emits one number, ~14.0% of NAV, for every approved name in
  all three runs (39 / 58 / 25 PASS lines). Among the nine full-size fills in bt 201039,
  size varies 12.44–17.49% and correlates with outcome at spearman **-0.252 (p=0.513)**;
  pooled across 28 positions in 3 runs, **+0.226 (p=0.248)**. There is no conviction→size
  mapping to sharpen, because the system has no ex-ante signal that separated XOM from
  PLRZ — it approved SNDK and EGO at the same $860.44 on the same bar.
* **Corollary the objective should absorb.** "Size so one winner matters" only pays after
  entry timing is fixed. In bt 201039 the five big movers we owned (SNDK, PLRZ, HL, AVNT,
  WDC) netted **-$148.41**; forcing all five to a uniform 14% nets **-$267.76**, i.e.
  **$119.35 worse**. At today's entry timing, more size on big movers loses more money.

---

## Appendix — reproduction

```bash
python3 scripts/pull_backtest_logs.py 201039 \
  --filter 'Buy gate inputs|SATELLITE CAP|\[core\]|Entry extension gate|action_intent=' --stdout
```

Fill tape / prices: `GET /backtests/<id>/graph-data` (`backtest_trades`,
`backtest_prices`). NAV curve and per-name P&L: `GET /backtests/<id>/summary`
(`risk_metrics.equity_curve`, `pnl_per_stock`, `pnl_percent_per_stock`,
`stock_price_change`, `sleeve_churn`). Same two endpoints for 820236 and 613166.
Position size at entry = fill notional / `equity_curve.value` at the nearest bar at or
before the fill timestamp.
