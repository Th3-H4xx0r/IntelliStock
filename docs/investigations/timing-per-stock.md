# Entry timing, per stock — bt 201039

Read-only forensics on **bt 201039** (`v2-let-run-core`, 2026-01-01 → 2026-03-01,
$6,000, 3600s, `pit_mode=research`, +8.34% / +$500.39), cross-checked against
**bt 820236** (+12.33%) and **bt 613166** (+9.17%).

Every number below is read out of a run log or a `/backtests/<id>/summary`
payload. Log lines are quoted verbatim. Code references are `file:line`.
Where a number is a *counterfactual* it is labelled as such.

Primary log: `python3 scripts/pull_backtest_logs.py 201039 --stdout` → 40,323 lines,
634 hourly bars (`PIT RESEARCH MODE: no frozen snapshots for <ts>`, n=634),
43 decision bars (the strategy `Run once` cadence), 36 fills, 6 round trips.

---

## 0. Headline

**Entry price is the whole game, and in this run entry price is set by
*discovery latency*, not by the entry gates.**

Pooling every filled, up-moving (>= +4%) position across bt 201039 + 820236 +
613166 (n=21) and plotting *fraction of the window's move already elapsed at the
fill price* against *how much of the move we captured*:

| fraction of move elapsed at fill | n | mean move | mean capture | total P&L |
|---|---|---|---|---|
| < 20% | 9 | +25.6% | **0.844** | **+$1,783.43** |
| 20–50% | 3 | +30.0% | 0.455 | +$206.32 |
| 50–100% | 5 | +80.7% | 0.228 | +$165.15 |
| > 100% | 4 | +79.9% | **-0.321** | **-$234.81** |

*Every one of the 15 positions filled at ≤55% of the move made money
(+$2,093.70). Every one of the 4 positions filled above 100% of the move lost
money (-$234.81). The two sets do not overlap.*

Pearson `r` on the pooled 21:

```
frac-of-move-elapsed-at-fill  vs  capture ratio   r = -0.895  p = 0.0000
frac-of-move-elapsed-at-fill  vs  our % return    r = -0.775  p = 0.0000
frac-of-move-elapsed-at-fill  vs  MFE after fill  r = -0.472  p = 0.0307   <- NOT mechanical
frac-of-move-elapsed-at-fill  vs  MAE after fill  r = -0.759  p = 0.0001   <- NOT mechanical
```

On bt 201039 alone (9 up-movers): `r = -0.948, p = 0.0001` vs our % return;
`r = -0.946, p = 0.0001` vs capture; Spearman `rho = -0.933 / -0.950`.

And the cause of the lateness, measured on bt 201039 (n=12 non-SPY):

```
total move %  vs  trading days from window open to DISCOVERY   r = +0.578  p = 0.0488
```

**The bigger the move, the later the system saw the name at all.** That single
sentence explains the central fact.

---

## 1. Method and definitions

| term | how it is read out of the run |
|---|---|
| **DISCOVERED** | first bar with a `[GraphNexusAnalysis]` line naming the symbol as a candidate (discovery / propagation-expansion / momentum-watchlist / overlay-scoring). `[BROKER] Fetched chunk … for <sym>` is *not* discovery — it is a bulk overlay-bar fetch and does not put the name in the scored set. |
| **BUY SIGNAL** | first `[BROKER] <SYM> @ <ts> ($px): …` line whose `action_intent` is a buy intent — `initial_buy`, `momentum_watchlist_buy`, `momentum_watchlist_rotation`, `momentum_watchlist_portfolio_swap`, `deferred_unfunded_buy`, `queued_backfill`, `backfill_queue_buy`, `direct_reserved_buy`. |
| **SIZED** | first `V31.2 total-spend cap [CONCENTRATE]: funded N of M by conviction (<SYM>@$X)` line naming the symbol. |
| **FILLED** | `[BROKER] [execution] FILL BUY <SYM> qty=… price=… quote=<ts>`. |
| **price path** | `[BROKER] <SYM> @ <ts> ($px)` decision lines. bt 201039 only prices a name once it is in the broker's symbol list, so for names discovered late the pre-discovery path is taken from the *same* decision lines in bt 820236 / 613166 / 264179 / 455506 / 498816 / 806490 (same window, same market data) plus the `risk_metrics.equity_curve[].prices` snapshots in the 201039 summary. Market prices are identical across runs of the same window; strategy state is not, and no strategy state is mixed. |
| **% of move elapsed at fill** | `(fill_px − start_px) / (end_px − start_px)`, `start_px`/`end_px` from `summary.stock_price_change`. |
| **MFE / MAE after fill** | max / min of `pnl=` in `Monitor decision: <SYM> day N pnl=±X% cp=$Y entry=$Z` — the run's own hourly mark of the open position (n=2,974 lines across the 13 names). |
| **capture** | `summary.pnl_percent_per_stock[sym] / summary.stock_price_change[sym].change_percent`. |

Caveat carried through the whole document: MFE/MAE are sampled at the monitor
cadence, so they are lower bounds on the true intrabar excursion.

---

## 2. The entry timeline for all 13 names

`summary.tickers` for bt 201039 is exactly 13: AAL, AVNT, BA, EGO, HL, NTR,
PLRZ, SNDK, SPY, TCMD, VOYA, WDC, XOM.

| name | DISCOVERED | px | BUY SIGNAL | px | SIZED | FILLED | fill px | window start | window end | move |
|---|---|---|---|---|---|---|---|---|---|---|
| AAL | 2026-01-02 | 15.32 | 2026-01-06 | 15.86 | 2026-01-06 | 2026-01-06 | 16.02 | 15.32 | 13.09 | -14.6% |
| AVNT | 2026-01-20 | 35.42 | 2026-01-20 | 35.42 | 2026-01-20 | 2026-01-20 | 35.64 | 31.25 | 41.07 | 31.4% |
| BA | 2026-01-02 | 217.87 | 2026-01-02 | 217.87 | 2026-01-02 | 2026-01-02 | 222.80 | 217.10 | 227.51 | 4.8% |
| EGO | 2026-02-02 | 37.76 | 2026-02-02 | 37.76 | 2026-02-02 | 2026-02-02 | 38.87 | 35.92 | 46.43 | 29.3% |
| HL | 2026-01-22 | n/a | 2026-01-27 | 28.52 | 2026-01-27 | 2026-01-27 | 28.23 | 19.19 | 24.89 | 29.7% |
| NTR | 2026-01-01 | 61.73 | 2026-01-01 | 61.73 | 2026-01-01 | 2026-01-02 | 61.91 | 61.73 | 75.06 | 21.6% |
| PLRZ | 2026-01-01 | 8.11 | 2026-01-15 | 14.50 | 2026-01-15 | 2026-01-16 | 15.48 | 8.11 | 13.12 | 61.8% |
| SNDK | 2026-01-30 | 620.08 | 2026-02-02 | 617.38 | 2026-02-02 | 2026-02-02 | 660.48 | 237.33 | 631.54 | 166.1% |
| SPY | 2026-01-05 | 683.33 | n/a (core) | n/a | n/a | 2026-01-02 | 682.97 | 681.82 | 686.16 | 0.6% |
| TCMD | 2026-01-01 | 28.96 | 2026-01-01 | 28.96 | 2026-01-01 | 2026-01-02 | 28.16 | 28.96 | 29.29 | 1.1% |
| VOYA | 2026-01-01 | 74.52 | 2026-01-01 | 74.52 | 2026-01-01 | 2026-01-02 | 75.15 | 74.52 | 66.90 | -10.2% |
| WDC | 2026-01-15 | 226.08 | 2026-01-15 | 226.08 | 2026-01-30 | 2026-01-30 | 259.37 | 172.27 | 278.93 | 61.9% |
| XOM | 2026-01-01 | 120.33 | 2026-01-01 | 120.33 | 2026-01-01 | 2026-01-02 | 120.24 | 120.33 | 152.59 | 26.8% |

Notes:
* **SPY** is the index core; it has no `V31.2` sizing and no discovery lane — it
  is deployed by the core cadence (`[core] deploy of $2400.00 SPY …`).
* **HL**'s price on its discovery bar (2026-01-22) is not observable — no run in
  the repo priced HL before 2026-01-27. Marked `n/a` rather than imputed.
* **WDC** is the only name in the run with a real signal→fill lag: the buy
  signal fired on **2026-01-15 at $226.08** and did not fill until
  **2026-01-30 at $259.37** — 15 calendar days and **+14.7%** worse.
* **PLRZ** was in the candidate set on **bar 1 at $8.11** and was not bought
  until **2026-01-16 at $15.48** — +90.8% higher (§6.3).

### 2b. Full price paths, with markers
`D` = discovered, `S` = buy signal, `Z` = sized, `F` = filled, `X` = sold.

```
AAL (45 obs): 01-01:15.32 01-02:15.325[D] 01-05:16 01-06:15.855[SZF] 01-07:16.02 01-08:15.975 01-09:16.06 01-10:16 01-12:15.58 01-13:15.785 01-14:15.275 01-15:15.565 01-16:15.46 01-17:15.365 01-19:15.365 01-20:15.13 01-21:15.105 01-22:15.45 01-23:14.77 01-24:14.67 01-26:14.675 01-27:14.28[X] 01-28:13.655 01-29:13.305 01-30:13.455 02-02:13.61 02-03:14.115 02-04:14.24 02-05:14.27 02-06:14.86 02-09:15.075 02-10:15.12 02-11:15.035 02-12:14.44 02-13:13.9 02-16:13.91 02-17:14.09 02-18:14.075 02-19:13.455 02-20:13.395 02-23:13.055 02-24:12.945 02-25:13.135 02-26:13.895 02-27:13.175

AVNT (10 obs): 01-20:35.425[DSZF] 01-21:36.515 01-22:37.28 01-23:37.5 01-24:37.41 01-26:37.4 01-27:36.925 01-29:36.145 01-30:35.825[X] 02-13:42.995

BA (46 obs): 01-02:217.87[DSZF] 01-03:227.725 01-06:228.12 01-07:230.5 01-08:231.25 01-09:232.03 01-10:234.42 01-12:236.35 01-13:243.095 01-14:241.05 01-15:246.46 01-16:247.64 01-17:247.67 01-19:247.67 01-20:245.44 01-21:247.245 01-22:251.79 01-23:251.065 01-24:252.11 01-26:248.28 01-27:243.19 01-28:241.25 01-29:240.16 01-30:232.11 01-31:233.84 02-02:232.46 02-03:234.4 02-04:231.71 02-05:238.51 02-06:242.2 02-09:243.34 02-11:242.61 02-12:241.825 02-13:243.505 02-14:242.97 02-16:242.97 02-17:242.8 02-18:241.24 02-19:236.01 02-20:233.165 02-21:231.91 02-24:230.48 02-25:230.43 02-26:227.76 02-27:225.81 02-28:227.51

EGO (21 obs): 02-02:37.76[DSZF] 02-03:41.07 02-04:39.68 02-05:38.27 02-06:38.51 02-09:40.36 02-11:43.525 02-12:46.09 02-13:46.965 02-14:47.37 02-16:47.37 02-17:43.33 02-18:46.06 02-19:46.84 02-20:43.39 02-21:42.995 02-24:43.9 02-25:44.34 02-26:43.74 02-27:45.69 02-28:46.43

HL (3 obs): 01-27:28.525[SZF] 01-29:27.19 01-30:24.275[X]

NTR (50 obs): 01-01:61.73[DSZ] 01-02:61.77[F] 01-03:63.16 01-05:62.56 01-06:61.73 01-07:59.93 01-08:60.15 01-09:60.85 01-10:59.82 01-12:60.53 01-13:60.51 01-14:64.82 01-15:66.7 01-16:66.47 01-17:66.38 01-19:66.38 01-20:66.85 01-21:67.79 01-22:68.79 01-23:70.11 01-24:70.885 01-26:71.135 01-27:71.33 01-28:71.52 01-29:72 01-30:69.87 01-31:68.885 02-02:68.895 02-03:69.76 02-04:70.95 02-05:70.05 02-06:68.52 02-09:69.76 02-10:70.06 02-11:71.68 02-12:71.85 02-13:70.21 02-14:70.82 02-16:70.82 02-17:69.34 02-18:70.24 02-19:71.14 02-20:71.645 02-21:71.15 02-23:72.05 02-24:72.785 02-25:71.61 02-26:71.9 02-27:73.08 02-28:75.055

PLRZ (25 obs): 01-01:8.11[D] 01-02:8.11 01-05:11.795 01-06:13.195 01-07:13.06 01-08:12.47 01-09:13.33 01-12:12.52 01-13:13.24 01-14:14.34 01-15:14.5[SZ] 01-16:15.01[F] 01-17:15.41 01-19:15.41 01-20:15.41 01-21:14.43 01-22:15.875 01-23:15.56 01-24:15.85 01-26:15.85 01-27:15.05 01-28:15.19 01-29:14.65 01-30:13.4[X] 02-02:12.755

SNDK (45 obs): 01-01:237.33 01-02:262.08 01-05:270.54 01-06:328.19 01-07:335.9 01-08:333.19 01-09:363.01 01-12:388.455 01-13:390.49 01-14:393.06 01-15:418.72 01-16:405.47 01-19:413.55 01-20:446.96 01-21:468.84 01-22:486.405 01-23:489.565 01-26:487.15 01-27:496.45 01-28:507.67 01-29:533.41 01-30:620.08[D] 02-02:617.375[SZF] 02-03:655.38 02-04:644.9 02-05:600.77 02-06:601.83 02-09:584.5 02-10:558 02-11:596.85 02-12:651.97 02-13:590.73 02-14:626.79 02-16:626.79 02-17:601.46 02-18:595.505 02-19:606.65 02-20:638.4 02-21:649.98 02-23:683.19 02-24:664.485 02-25:645.43 02-26:636.61 02-27:641.26 02-28:631.54

SPY (46 obs): 01-01:681.82 01-02:681.82[F] 01-03:683.33 01-06:687.73 01-07:691.81 01-08:689.64 01-09:689.44 01-10:694.005 01-12:694.005 01-13:695.185 01-14:693.71 01-15:690.62 01-16:692.18 01-17:691.58 01-19:691.58 01-20:691.58 01-21:677.66 01-22:685.34 01-23:688.96 01-24:689.2 01-26:689.2 01-27:692.755 01-29:695.39 01-30:693.965 01-31:691.8 02-02:691.8 02-03:695.39 02-04:689.435 02-05:686.1[X] 02-06:677.44 02-09:690.72 02-11:692.11 02-12:691.81 02-13:681.19 02-14:681.61 02-16:681.61 02-17:681.61 02-18:682.76 02-19:686.19 02-20:684.505 02-21:689.44 02-24:682.39 02-25:687.37 02-26:693.1 02-27:689.32 02-28:686.16

TCMD (20 obs): 01-01:28.96[DSZ] 01-02:28.095[F] 01-03:27.47 01-05:29.045 01-06:30.305 01-07:30.89 01-08:30.71 01-09:30.74 01-10:30.23 01-12:29.53 01-13:29.57 01-14:29.92 01-15:30.115[X] 01-16:30.19 01-19:30.47 01-20:30.1 01-21:29.775 01-22:30.15 01-23:29.385 01-26:29.27

VOYA (20 obs): 01-01:74.515[DSZ] 01-02:74.975[F] 01-03:75.6 01-05:76.59 01-06:76.655 01-07:77.87 01-08:79.4 01-09:78.415 01-10:78.77 01-12:78.23 01-13:77.87 01-14:76.99 01-15:77.71 01-16:77.615 01-17:74.63 01-19:74.63 01-20:74.39[X] 02-03:78.085 02-23:73.48 02-24:69.6

WDC (46 obs): 01-01:172.27 01-02:181.14 01-05:184.765 01-06:203.81 01-07:196.31 01-08:191.55 01-09:188.24 01-12:201.17 01-13:210.815 01-14:213.455 01-15:226.08[DS] 01-16:217.26 01-19:221.52 01-20:228.35 01-21:231.34 01-22:231.14 01-23:235.365 01-26:249.51 01-27:254.355 01-28:263.21 01-29:279.65 01-30:266.6[ZF] 01-31:249.82 02-02:260.665 02-03:273.18 02-04:286.615 02-05:263.63 02-06:271.64 02-09:287.53 02-10:270.83 02-11:277.77 02-12:295.97 02-13:271.9 02-14:281.67 02-16:281.67 02-17:276.36 02-18:293.04 02-19:285.81 02-20:289.775 02-21:285.33 02-23:288.78 02-24:279.31 02-25:290.66 02-26:277.1 02-27:278.13 02-28:278.93

XOM (46 obs): 01-01:120.33[DSZ] 01-02:119.97[F] 01-03:122.65 01-06:125.4 01-07:119.59 01-08:120.085 01-09:123.525 01-10:124.61 01-12:123.34 01-13:125.93 01-14:128.98 01-15:129.74 01-16:129.15 01-17:129.88 01-19:129.88 01-20:131.455 01-21:132.67 01-22:132.35 01-23:135.655 01-24:134.96 01-26:134.885 01-27:135.595 01-29:137.56 01-30:139.29 01-31:141.485 02-02:138.155 02-03:141.18 02-04:145.15 02-05:145.405 02-06:148.175 02-09:149.81 02-11:151.555 02-12:154.62 02-13:149.87 02-14:148.46 02-16:148.46 02-17:145.47 02-18:148.68 02-19:151.93 02-20:149.43 02-21:147.3 02-24:150.81 02-25:148.155 02-26:147.67 02-27:150.06 02-28:152.59
```

---

## 3. (a) % of move elapsed at fill, (b) MFE after fill, (c) drawdown after fill

| name | move | % elapsed at DISCOVERY | % elapsed at FILL | MFE after fill | MAE after fill | days held | our % | our $ | capture |
|---|---|---|---|---|---|---|---|---|---|
| AAL | -14.6% | -0.2% | -31.4% | 2.8% | -9.2% | 21 | -12.33% | $-10.94 | 0.845 |
| AVNT | 31.4% | 42.5% | 44.6% | 5.6% | -0.5% | 10 | 1.12% | $9.77 | 0.036 |
| BA | 4.8% | 7.4% | 54.7% | 13.9% | 1.2% | 56 | 2.11% | $0.73 | 0.440 |
| EGO | 29.3% | 17.5% | 28.1% | 23.0% | -4.8% | 25 | 19.45% | $167.35 | 0.665 |
| HL | 29.7% | n/a | 158.7% | 0.3% | -16.4% | 3 | -18.54% | $-24.16 | -0.624 |
| NTR | 21.6% | 0.0% | 1.4% | 22.2% | -4.8% | 56 | 21.23% | $178.30 | 0.983 |
| PLRZ | 61.8% | 0.0% | 146.9% | 3.4% | -13.4% | 14 | -17.61% | $-154.46 | -0.285 |
| SNDK | 166.1% | 97.1% | 107.3% | 5.5% | -18.1% | 25 | -4.38% | $-37.73 | -0.026 |
| SPY | 0.6% | 34.8% | 26.5% | 2.0% | -0.8% | 56 | 0.12% | $6.17 | 0.188 |
| TCMD | 1.1% | 0.0% | -246.4% | 10.4% | -2.4% | 13 | 8.13% | $85.17 | 7.243 |
| VOYA | -10.2% | -0.0% | -8.3% | 5.5% | -0.7% | 18 | -0.79% | $-6.62 | 0.077 |
| WDC | 61.9% | 50.5% | 81.7% | 18.5% | -3.7% | 28 | 7.54% | $58.17 | 0.122 |
| XOM | 26.8% | 0.0% | -0.3% | 30.1% | -1.5% | 56 | 26.90% | $225.25 | 1.003 |

Reading the table:

* **The four losers are the four late entries.** PLRZ (146.9% elapsed), HL
  (158.7%), SNDK (107.3%) and AAL (a genuine downtrend, -14.6% move) are the only
  names with negative or near-zero P&L. Every name filled below 55% elapsed made
  money.
* **`% elapsed at DISCOVERY` is most of `% elapsed at FILL`.** SNDK: 97.1 of the
  107.3 points were gone before the system had ever scored the name. WDC: 50.5 of
  81.7. AVNT: 42.5 of 44.6. EGO: 17.5 of 28.1.
  The exception is PLRZ, where discovery was 0.0% and **all** 146.9 points of
  lateness were added between discovery and fill (§6.3).
* **MFE collapses with lateness and MAE deepens.** The three latest entries
  (PLRZ, HL, SNDK) never showed more than +5.5% unrealised and drew down
  -13.4% / -16.4% / -18.1%. The three earliest (XOM, NTR, TCMD) showed
  +30.1% / +22.2% / +10.4% against -1.5% / -4.8% / -2.4%.

### The parent's "big vs modest" split, re-expressed

| bucket (bt 201039, non-SPY) | n | mean move | mean % elapsed at fill | our $ |
|---|---|---|---|---|
| BIG (move ≥ +25%) | 7 | +58.1% | **+81%** | +$244.19 |
| MODEST (move < +25%) | 5 | +0.5% | **-46%** | +$246.63 |

The modest names were bought *at or below the window-start price* (mean -46% of
"the move" elapsed, i.e. we were early or the name dipped first). The big names
were bought with 81% of the move already gone. That is the whole difference —
not sizing, not exits.

---

## 4. The hypothesis, tested directly

**Does "% of move elapsed at entry" predict our P&L?  Yes, and part of it is an
identity — which makes it stronger, not weaker.**

For a position bought at `fill` and held to the window end, with
`f = fill/start` and `M = end/start`:

```
elapsed = (f − 1)/(M − 1)          capture = (M/f − 1)/(M − 1) = (1 − elapsed)/f
```

So capture is **fully determined** by entry price for any hold-to-end position.
Checking that identity against the run:

| name | elapsed | f = fill/start | capture predicted by identity | capture actually realised | days held |
|---|---|---|---|---|---|
| XOM | -0.003 | 0.999 | 1.003 | **1.003** | 56 (held to end) |
| NTR | 0.014 | 1.003 | 0.983 | **0.983** | 56 (held to end) |
| EGO | 0.281 | 1.082 | 0.665 | **0.665** | 25 (held to end) |
| BA | 0.547 | 1.026 | 0.441 | **0.440** | 56 (held to end) |
| WDC | 0.817 | 1.506 | 0.122 | **0.122** | 28 (held to end) |
| SNDK | 1.073 | 2.783 | -0.026 | **-0.026** | 25 (held to end) |
| AVNT | 0.446 | 1.140 | 0.486 | *0.036* | 10 (SOLD 01-30) |
| PLRZ | 1.469 | 1.908 | -0.246 | *-0.285* | 14 (SOLD 01-30) |
| HL | 1.587 | 1.471 | -0.399 | *-0.624* | 3 (SOLD 01-30) |

Two conclusions:

1. **For every name the run held to the end, capture equals the identity to four
   decimal places. The exit stack contributed exactly nothing** — consistent with
   `docs/investigations/exits-and-capture.md` ("Capture vs ACTUAL entry = 99.99%").
   Entry price *is* the P&L.
2. **For the three names sold early, the exit made it worse, not better** — most
   sharply AVNT: sold 2026-01-30 at $36.04 to fund WDC, capture 0.036 against
   0.486 available from simply holding. Selling a name at +0.5% to buy a name
   already 81.7% through its move is the trade this run made.

Because the identity only binds hold-to-end positions, the **non-mechanical**
version of the test matters more: does entering late also produce a *worse
forward path* inside our own holding window? It does.

```
bt 201039 (9 up-movers):
  elapsed-at-fill  vs  MFE after fill   r = -0.848  p = 0.0038   rho = -0.917  p = 0.0005
  elapsed-at-fill  vs  MAE after fill   r = -0.777  p = 0.0138
pooled 3 runs (n=21):
  elapsed-at-fill  vs  MFE after fill   r = -0.472  p = 0.0307
  elapsed-at-fill  vs  MAE after fill   r = -0.759  p = 0.0001
```

A late entry is not merely accounting for a move we missed — the price path
*after* we bought was also worse, and the drawdown deeper.

### The pooled table (this is the evidence, ordered by lateness)

| % elapsed at fill | run | name | move | fill bar | fill px | capture | our % | our $ | MFE | MAE |
|---|---|---|---|---|---|---|---|---|---|---|
| -2.8% | 613166 | HESM | 12.2% | 2026-01-02 | 34.38 | 0.100 | 1.22% | $10.19 | 2.2% | -3.3% |
| -0.3% | 201039 | XOM | 26.8% | 2026-01-02 | 120.24 | 1.003 | 26.90% | $225.25 | 30.1% | -1.5% |
| 0.9% | 613166 | PLD | 11.7% | 2026-01-26 | 127.79 | 0.990 | 11.60% | $105.29 | 12.5% | -1.1% |
| 1.4% | 613166 | NTR | 21.6% | 2026-01-02 | 61.91 | 0.983 | 21.23% | $178.30 | 22.2% | -4.8% |
| 1.4% | 201039 | NTR | 21.6% | 2026-01-02 | 61.91 | 0.983 | 21.23% | $178.30 | 22.2% | -4.8% |
| 2.3% | 613166 | AGMI | 32.5% | 2026-01-08 | 67.21 | 0.970 | 31.50% | $341.72 | 33.4% | -0.2% |
| 7.2% | 820236 | CPER | 5.6% | 2026-01-02 | 35.08 | 0.924 | 5.16% | $55.69 | 12.8% | -1.1% |
| 8.7% | 820236 | WDC | 61.9% | 2026-01-02 | 181.55 | 0.866 | 53.63% | $450.49 | 69.3% | 0.3% |
| 17.7% | 820236 | LRCX | 36.7% | 2026-01-02 | 182.26 | 0.773 | 28.36% | $238.22 | 37.5% | 1.5% |
| 28.1% | 201039 | EGO | 29.3% | 2026-02-02 | 38.87 | 0.665 | 19.45% | $167.35 | 23.0% | -4.8% |
| 28.1% | 613166 | EGO | 29.3% | 2026-02-02 | 38.87 | 0.665 | 19.45% | $29.20 | 23.0% | -4.8% |
| 44.6% | 201039 | AVNT | 31.4% | 2026-01-20 | 35.64 | 0.036 | 1.12% | $9.77 | 5.6% | -0.5% |
| 52.4% | 820236 | SNDK | 166.1% | 2026-01-20 | 443.83 | 0.124 | 20.57% | $100.95 | 57.0% | 2.7% |
| 54.7% | 820236 | BA | 4.8% | 2026-01-02 | 222.80 | 0.440 | 2.11% | $2.26 | 13.9% | 1.2% |
| 54.7% | 201039 | BA | 4.8% | 2026-01-02 | 222.80 | 0.440 | 2.11% | $0.73 | 13.9% | 1.2% |
| 81.7% | 201039 | WDC | 61.9% | 2026-01-30 | 259.37 | 0.122 | 7.54% | $58.17 | 18.5% | -3.7% |
| 90.0% | 613166 | SNDK | 166.1% | 2026-02-05 | 592.01 | 0.014 | 2.39% | $3.04 | 16.1% | -8.7% |
| 107.3% | 201039 | SNDK | 166.1% | 2026-02-02 | 660.48 | -0.026 | -4.38% | $-37.73 | 5.5% | -18.1% |
| 131.1% | 613166 | PLRZ | 61.8% | 2026-01-28 | 14.68 | -0.348 | -21.53% | $-18.47 | -0.2% | -13.0% |
| 146.9% | 201039 | PLRZ | 61.8% | 2026-01-16 | 15.48 | -0.285 | -17.61% | $-154.46 | 3.4% | -13.4% |
| 158.7% | 201039 | HL | 29.7% | 2026-01-27 | 28.23 | -0.624 | -18.54% | $-24.16 | 0.3% | -16.4% |

### Three natural experiments — the same stock, the same window, different entry dates

**SNDK (+166.1%), bought in all three runs:**

| run | fill | fill px | % elapsed | capture | our % | our $ | notional | MFE | MAE |
|---|---|---|---|---|---|---|---|---|---|
| 820236 | 2026-01-20 | $443.83 | 52.4% | 0.124 | +20.57% | **+$100.95** | $490.83 | +57.0% | +2.7% |
| 613166 | 2026-02-05 | $592.01 | 90.0% | 0.014 | +2.39% | +$3.04 | $126.93 | +16.1% | -8.7% |
| 201039 | 2026-02-02 | $660.48 | 107.3% | -0.026 | -4.38% | **-$37.73** | $860.41 | +5.5% | -18.1% |

Monotone in entry lateness, and *inverted* in notional — bt 201039 put the
**largest** dollar stake on the **latest** entry.

**WDC (+61.9%), bought in two runs:**

| run | fill | fill px | % elapsed | capture | our % | our $ |
|---|---|---|---|---|---|---|
| 820236 | 2026-01-02 | $181.55 | 8.7% | 0.866 | +53.63% | **+$450.49** |
| 201039 | 2026-01-30 | $259.37 | 81.7% | 0.122 | +7.54% | +$58.17 |

**PLRZ (+61.8%), bought in two runs — both late, both losses:**

| run | fill | fill px | % elapsed | capture | our $ |
|---|---|---|---|---|---|
| 201039 | 2026-01-16 | $15.48 | 146.9% | -0.285 | -$154.46 |
| 613166 | 2026-01-28 | $14.68 | 131.1% | -0.348 | -$18.47 |

---

## 5. Earliest vs latest — what distinguishes them

**EARLIEST (filled at ≤2% of the move elapsed): XOM, NTR, TCMD, VOYA.**
All four were discovered on **bar 1, 2026-01-01, by the news-trend lane**:

```
[GraphNexusAnalysis]   Discovered stock: NTR (from trends: …_us_iran_oil_supply_risk)
[GraphNexusAnalysis]   Discovered stock: XOM (from trends: …_saudi_oil_output_cut)
[GraphNexusAnalysis]   Discovered stock: VOYA (from trends: …_analyst_pt_actions_mixed_feb25)
```
`[GraphNexusAnalysis] V31.2 total-spend cap [CONCENTRATE]: funded 4 of 7 by conviction (TCMD@$840, XOM@$840, VOYA@$840, NTR@$840) out of $3,780; dropped 3 to the queue` — bar 1, filled next bar.
Their combined P&L: **+$482.09 of the run's +$500.39.**

**LATEST (filled at >100% of the move elapsed): HL (158.7%), PLRZ (146.9%),
SNDK (107.3%).** None of these three came in through the news-trend lane:
* SNDK arrived via `Propagation scoring expansion: 40 ticker(s) added: … SNDK …` on **2026-01-30, day 21 of 42**, priced at $620.08 after running from $237.33.
* HL arrived via the momentum watchlist on 2026-01-22 (`Momentum watchlist: … top3=[('PLRZ', 1.254), ('GLUE', 0.823), ('HL', 0.75)]`).
* PLRZ was on the watchlist from bar 1 but held out by a gate for 10 sessions (§6.3).

**The distinguishing property is the lane, and the lane is determined by how the
name became interesting.** A name that is in the news at the window open is
priced from bar 1 at 0% elapsed. A name whose only signature is *price* has to
be found by the momentum lane — and the momentum lane cannot see it until the
price has already moved. That is the loop that produces "we capture modest
trends fully and capture nothing of the big movers".

---

## 6. The four mechanisms, with evidence

### 6.1 Discovery latency — the momentum scan universe is a closed graph loop
**This is the dominant term (97.1 of SNDK's 107.3 points of lateness).**

`_build_momentum_scan_universe` (`backend/strategies/graph_nexus_analysis.py:14946-14999`)
builds the pool that momentum discovery screens from exactly five sources:
benchmark ETFs, `_TREND_ETF_MAP` ETFs, Neo4j trend ETFs (10/keyword),
tickers named by active news trends, and
`MATCH (src:Company)-[:COMPETES_WITH|STRATEGIC_PARTNER]-(dst:Company) … LIMIT 30`
neighbours **of the stocks already tracked**. There is no broad equity screen.
An equity can only be discovered by momentum if it is already a graph neighbour
of something we already hold or already track.

Measured consequence on bar 1, 2026-01-01:

bt 201039 — pool `Overlay bars: cached 133/144 symbol(s)`, dominated by sector
ETFs (XLK, XLP, XLU, XLY, XME, XOP, XHB, XHE, XPH, XRT, VWO …). The 12 slots
(`momentum_discovery_max_per_day`, `mom:12` in the run's `Effective config`) went to:
```
DZZ (60d=+141.6%)  MAAS +61.5%  TLSI +50.0%  OBIO +49.5%  TNDM +46.7%  PILL +45.4%
PROF +38.0%  BBC +35.7%  SBIO +31.5%  AGMI +20.5%  COPP +19.3%  C +19.0%
[GraphNexusAnalysis] Momentum discovery: 12 new ticker(s) → symbols now 139
```
Not one semiconductor. SNDK's daily bars were fetched **on the same bar**
(`[BROKER] Fetched chunk 1/1 for SNDK: 244 bars (2025-08-19 to 2026-08-08)`,
line 350) — the data was in hand and the name was never screened.

bt 820236 — same code, same bar, different tracked set, and the pool *did*
contain the semis:
```
DZZ +141.6%  TE +138.8%  VICR +119.6%  CRCD +108.6%  SNDK +95.9%  CORD +89.2%
BTCZ +80.7%  TSEM +59.1%  MU +49.5%  VIAV +39.5%  WDC +37.5%  BBC +35.7%
```
That run bought WDC on bar 1 at 8.7% elapsed for **+$450.49** and SNDK on
2026-01-20 at 52.4% elapsed for **+$100.95**. Same names, same prices, same
window; the only difference is when they entered the candidate set.
(`Discovery source usage:` bar 1 — `momentum=43` in 201039 vs `momentum=87` in 820236.)

### 6.2 The book is full, so a new leader can only enter by displacement
`max_positions gate armed: held=N, cap=6` fires once per bar, n=634.

| run | bars armed | bars at the cap | cap value ever read | `Regime capacity gate (Z4.1)` lifts |
|---|---|---|---|---|
| 201039 | 634 | **553 (87.2%)** | 6 (634/634) | 43 |
| 820236 | 634 | 599 (94.5%) | 6 (634/634) | 43 |
| 613166 | 634 | 316 (49.8%) | 6 (634/634) | 43 |

`Regime capacity gate (Z4.1): regime=bull max_positions 6->14 (spy_20d=+3.00%, v31=bull)`
fires 43 times in bt 201039 and the broker never reads anything but 6 —
`resolve_max_positions_cap` (`backend/nexus_broker_utils.py:128-152`) resolves the
**static** `cfg["max_positions"]`. This reproduces `_SYNTHESIS` root cause #2 on a
third run.

With the book pinned at 6 on 87.2% of bars, the only routes in are an exit or a
swap. In bt 201039 there were **2** swaps in 42 sessions:
```
[GraphNexusAnalysis] Momentum portfolio swap: sell VOYA (pnl=-1.0%) → buy SOC (score=0.938, $874)
[GraphNexusAnalysis] Momentum portfolio swap: sell AVNT (pnl=+0.5%) → buy WDC (score=0.768, $878)
```
and **37** `V28 ROTATION RESULT` lines, of which **0** fired (`fired=0/4` on every one).
72 `deferred_unfunded_buy` decision lines across 21 names never converted.
This is what turned WDC's 2026-01-15 signal at $226.08 into a 2026-01-30 fill at
$259.37: on 01-15 the run logged
```
[GraphNexusAnalysis] Backfill queue BLOCKED: WDC (full_priority_blocked, score=1.750, source=direct)
[GraphNexusAnalysis] Deferred unfunded buys demoted to hold: C, GS, TSEM, WDC
[BROKER] WDC @ 2026-01-15 15:00:00 ($226.08): hold action_intent=deferred_unfunded_buy
```
and WDC only got in on 01-30 by selling AVNT.
**Cost, same dollars, hold to window end: $771.74 at $226.08 → +$180.41 vs the
actual +$58.17 = +$122.24 forgone** (counterfactual).

### 6.3 The entry-extension gate decays — it blocks at the base and clears at the top
`_recent_runup_protect` (`backend/strategies/graph_nexus_analysis.py:9259-9282`) returns
```python
lo = min(closes); hi = max(closes)
runup_pct = ((hi - lo) / lo) * 100.0
```
over `closes = price_history[sym][-lookback_bars:]`. It is a **trailing range
ratio, not anchored to the current price**. As a sustained advance carries on,
the old base rolls out of the lookback and the measured "runup" *falls while the
price rises*. The gate therefore refuses a name at the start of its move and
permits it once the move has run. Call sites:
`graph_nexus_analysis.py:23238` (`Entry extension gate`) and `:5540`
(`V32 mw_buy/mw_swap extension-block`, "no conviction bypass").

Measured, bt 201039, PLRZ — price rising, gate reading falling:

| bar | PLRZ price | gate reading | threshold | outcome |
|---|---|---|---|---|
| 2026-01-01 | **$8.11** | +106.2% | 25% | blocked |
| 2026-01-02 | $8.11 | +106.2% | 25% | blocked |
| 2026-01-05 | $11.795 | +97.2% | 25% | blocked |
| 2026-01-06 | $13.195 | +78.8% | 25% | blocked |
| 2026-01-08 | $12.47 | +83.6% | 25% | blocked |
| 2026-01-16 | **$15.48** | (silent) | 25% | **BOUGHT → -$154.46** |

```
[GraphNexusAnalysis] V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25% — no conviction bypass
[GraphNexusAnalysis] V32 mw_buy extension-block: PLRZ recent runup +78.8% > 25% — no conviction bypass
[BROKER] [execution] FILL BUY PLRZ qty=56.67227162 … price=15.475358 … quote=2026-01-16 16:00:00+00:00
```

The same pattern, same window, other runs:
* bt 820236 SNDK: blocked 2026-01-01 at $237.33 (+28.5%) and 01-02 at $262.08 (+28.5%), then 01-07 (+73.2%), 01-08 (+75.3%); **bought 2026-01-20 at $443.83**.
* bt 613166 PLRZ: blocked 01-22/01-23 (+115.3%) and 01-26 (+108.0%); **bought 2026-01-28 at $14.68 → -$18.47**.
* bt 201039 HL: blocked 01-23 (+65.7%) and 01-26 (+68.6%); **bought 01-27 at $28.23 → -$24.16**.

Names extension-blocked and then bought later in the same run:

| run | names | P&L of those positions |
|---|---|---|
| 201039 | PLRZ, HL | **-$178.62** |
| 820236 | OMER, SNDK | +$39.96 |
| 613166 | PLRZ | **-$18.47** |
| **total** | 5 positions, 4 losers | **-$157.13** |

### 6.4 Next-event fill on a fast mover
Fills settle on the following event, so the fill price is not the signal price.
Across the 12 non-SPY names in bt 201039 the median gap is +0.95%, but it scales
with the name's speed:

```
SNDK  signal 2026-02-02 15:00 $617.375  ->  fill $660.479   +6.98%
PLRZ  signal 2026-01-15 15:00 $14.500   ->  fill $15.475    +6.73%   (next session)
EGO   signal 2026-02-02 15:00 $37.760   ->  fill $38.869    +2.94%
XOM   signal 2026-01-01 00:00 $120.330  ->  fill $120.245   -0.07%
```
SNDK cost 7% of a 14.3%-of-NAV position in one hour, or ~$56 of the -$37.73 loss.
This is second-order next to §6.1, but it is real and it is the same slippage
`simulated_execution.py` flags as the largest unexploited cost lever.

---

## 7. What the lateness cost, in dollars (counterfactual, upper bounds)

Same names, same buy notional, held to the window end, varying only the fill price:

| scenario | non-SPY P&L | delta |
|---|---|---|
| **actual** | **+$490.83** | — |
| hold every position to the window end at the **actual fill price** | +$510.18 | +$19.35 (the exits) |
| fill at the price the name was **DISCOVERED** at | +$1,395.71 | **+$885.53 (conversion lag)** |
| fill at the **window-start** price | +$3,334.58 | **+$1,938.87 (discovery lag)** |

On a $6,000 account: +8.34% actual → ~+31.6% if every name had been bought at the
price it was discovered at → ~+63.9% if every name had been bought at the window-open
price. These are perfect-hindsight ceilings in the same spirit as the
`_SYNTHESIS` "prize" figure, not achievable targets. The **ratio** is what matters:
**discovery lag is ~2.2× more expensive than conversion lag, and the exits cost
essentially nothing (1.5% of the gap).**

---

## 8. Ranked list — what to change

Ordered by (evidence × dollars). Generalisability is stated explicitly for each;
where a lever only works on this window I say so and do not recommend it.

---

### 1. Give momentum discovery a universe that does not depend on what we already hold
**Change.** `_build_momentum_scan_universe` (`graph_nexus_analysis.py:14946-14999`)
should take a fixed, config-supplied breadth list (e.g. the liquid US equity
universe already implied by `min_avg_volume` / `min_market_cap`) in addition to
its five graph/trend sources, so a name's *price* alone is sufficient to get it
screened. Ship default-OFF behind one key.

**Expected effect.** Moves the leaders' discovery bar toward bar 1, which moves
`% elapsed at fill` down, which by the identity `capture = (1 − elapsed)/f`
raises capture. On the measured distribution: moving a name from the
">100% elapsed" bucket to the "<20%" bucket is a mean capture swing of
-0.321 → +0.844.

**Evidence.** bt 201039 bar 1 screened 144 symbols and admitted 12 with no semis;
bt 820236's bar-1 pool contained SNDK/WDC/VICR/VIAV/MU/TSEM and those two names
alone paid **+$551.43 of that run's +$739.61**. Same code, same window, same
prices — only the pool differed. `corr(move %, days-to-discovery) = +0.578, p=0.049`
on bt 201039.

**Generalisable?** Yes — the closed-loop defect is structural (`LIMIT 30`
neighbours of the current book), independent of window or regime. Validate on
≥3 windows including one non-semiconductor-led, per `docs/OBJECTIVE.txt`.

**Risk.** Widening the pool raises candidate count and could saturate the
backfill queue (the documented `404780` failure mode). The existing
`momentum_discovery_max_per_day`, `max_discovered_stocks` and
`_is_excluded_momentum_etf` caps already bound the downstream flow; do not raise
them at the same time.

---

### 2. Fix the `max_positions` plumbing so `Z4.1` reaches the broker
**Change.** `resolve_max_positions_cap` (`backend/nexus_broker_utils.py:128-152`)
must read the regime-adjusted cap, not the static `cfg["max_positions"]`.

**Expected effect.** Removes the displacement bottleneck that turned WDC's
2026-01-15 $226.08 signal into a 2026-01-30 $259.37 fill (+$122.24 forgone on
that one name) and that left 72 `deferred_unfunded_buy` signals across 21 names
unconverted.

**Evidence.** 634/634 `max_positions gate armed` lines read `cap=6` in **all
three** runs while `Regime capacity gate (Z4.1)` lifted the cap 43 times per run;
the book sat at the cap on 87.2% / 94.5% / 49.8% of bars.

**Generalisable?** Yes — it is a wiring bug, not a tuning. But it interacts with
the documented `DO NOT RETRY` on raising `max_positions` (latched breach
auto-heal → per-bar forced liquidation, prize diluted ~62%). **Fix the read
first and verify the breach auto-heal path does not latch**, before letting the
effective cap exceed 6 in a live-faithful config.

---

### 3. Make the entry-extension gate one-way (sticky), not decaying
**Change.** Once a symbol is extension-blocked, keep it blocked for that advance
— e.g. hold the block until the name prints a close *below* the low of the
lookback window that triggered the block. Equivalently, anchor
`_recent_runup_protect` to the *blocking* base rather than to a rolling `min()`
that forgets it (`graph_nexus_analysis.py:9259-9282`).

**Expected effect.** Removes the specific trade the gate currently manufactures:
refuse at the base, admit at the top. It **never buys anything the gate refuses
today** — it only removes late buys — so it also cuts turnover, which the
objective wants.

**Evidence.** 5 blocked-then-bought positions across the three runs, 4 of them
losers, net **-$157.13**: 201039 PLRZ -$154.46 and HL -$24.16; 613166 PLRZ
-$18.47; 820236 OMER -$60.99 and SNDK +$100.95. Improves 2 of 3 runs
(201039 +$178.62 ≈ +2.98pp, 613166 +$18.47 ≈ +0.31pp) and costs 1
(820236 -$39.96 ≈ -0.67pp). Net across the three: **+$157.13 ≈ +0.87pp of
combined capital.**

**Generalisable?** The *mechanism* is (the `(hi−lo)/lo` window forgets the base);
the *size* is not established — n=5 positions is below the "n=5 round trips is
not evidence" bar in `docs/OBJECTIVE.txt`. Treat as a hypothesis with a stated
mechanism, validate on ≥3 windows. **This is not the "loosen the extension gate"
experiment on the DO-NOT-RETRY list** — that one admitted blocked names
(-7.95%); this one refuses them for longer.

**Caveat.** Freed capital would be redeployed, so the +$157.13 is a
partial-equilibrium figure.

---

### 4. Fund the *first* signal, not the third
**Change.** When a name emits a buy signal and is refused for funding
(`deferred_unfunded_buy`, `Backfill queue BLOCKED: … full_priority_blocked`),
carry the *signal price* as the reference and re-price the decision against it;
refuse the later, worse entry rather than take it. This is the same shape as
recommendation 3 but on the funding path instead of the extension path.

**Expected effect.** WDC alone: +$122.24 on bt 201039 (counterfactual).
72 deferred signals across 21 names is the population it acts on.

**Evidence.** bt 201039 WDC signal 2026-01-15 $226.08 → fill 2026-01-30 $259.37
(+14.7%); `Deferred unfunded buys demoted to hold: C, GS, TSEM, WDC`.
`deferred_unfunded_buy` decision lines n=72, distinct names 21
(TSM 11, TSEM 11, SBLK 10, SLGN 7, RVLV 6, SHLS 6, UHS 5 …).

**Generalisable?** The measurement is a single name on a single run. Mechanism is
general; **effect size is not established.** Do not promote on this evidence —
instrument first (log the signal price on the deferral so the delta is
measurable on every run).

---

### 5. Do not sell a +0.5% winner to fund a name that is 80% through its move
**Change.** Gate `Momentum portfolio swap` and `V28 ROTATION` on the *incoming*
name's extension, not only on the outgoing name's P&L.

**Expected effect.** bt 201039's single swap sold AVNT at +0.5% (capture 0.036
against 0.486 available from holding, i.e. **$123.42 of forgone AVNT**) to buy
WDC at 81.7% elapsed (capture 0.122). Both legs were the wrong way round.

**Evidence.** `Momentum portfolio swap: sell AVNT (pnl=+0.5%) → buy WDC (score=0.768, $878)`;
AVNT identity capture 0.486 vs realised 0.036; WDC elapsed 0.817.
Corroborates `_SYNTHESIS` root cause #5 ("rotation sells without buying") from
the opposite direction — here it *did* buy, and the buy was the late one.

**Generalisable?** Mechanism yes; n=2 swaps in this run, so effect size is
anecdotal. Low priority relative to 1–2.

---

### 6. Passive / mid-price execution on the entry leg
**Change.** As already documented in `simulated_execution.py:135`.

**Expected effect.** Median +0.95% per entry in bt 201039, but +6.98% on SNDK and
+6.73% on PLRZ — i.e. the cost is concentrated exactly on the fast movers the
objective is about. ~$56 on SNDK's position alone.

**Generalisable?** Yes, but it is second-order. Summing `notional × signal→fill gap`
over the 12 non-SPY entries gives **$243.61** on $8,062.19 of buy notional (of which
SNDK alone is $56.15 and PLRZ $59.02), against the $885–$1,939 of timing cost in §7.
Do it, but not first.

---

### NOT recommended

* **Any rule keyed on "% of move elapsed"** as an entry filter. It is the best
  predictor in this data (`r = -0.895`) and it is **unusable**: it needs the
  window's end price. Every implementable proxy I tested fails to separate —
  e.g. "% above the price at which we first scored the name" is +87.0% for
  820236's SNDK (which made +$100.95) and +90.8% for 201039's PLRZ (which lost
  -$154.46). Do not build a gate on this metric.
* **Loosening the extension gate** — on the DO-NOT-RETRY list, and this
  investigation gives no reason to revisit it. The problem is that the gate
  *stops* firing, not that it fires.
* **Anything keyed to SNDK, semiconductors, or this specific window.** The
  bt 613166 replication is deliberately non-semi-led (AGMI, PLD, HESM, EGO, NTR)
  and shows the same ordering: 2.3% elapsed → capture 0.970; 90.0% elapsed →
  capture 0.014.

---

## 9. Limits of this analysis

* n = 21 pooled positions, 3 runs, 1 window. `docs/OBJECTIVE.txt` requires ≥3
  windows including ≥1 out-of-sample and ≥1 non-semiconductor-led before any of
  this is promoted. Nothing here is a promotion recommendation.
* HL's discovery price is unobservable — no run in the repo priced HL before
  2026-01-27. Its `% elapsed at discovery` is left blank rather than imputed.
* Pre-discovery price paths for names bt 201039 discovered late are taken from
  other runs' decision lines on the identical window. Prices are market data and
  identical; no strategy state is mixed. Verified where they overlap (e.g. SNDK
  2026-02-02 $617.375 appears in both 201039 and 613166).
* The counterfactual dollars in §7 hold position size and hold-to-end fixed and
  vary only the fill price. They ignore the funding constraint that produced the
  late fill in the first place — they are ceilings, not forecasts.
* MFE/MAE come from `Monitor decision` hourly marks and are therefore lower
  bounds on the true excursions.
* bt 201039 was not re-run and nothing was executed; this is log forensics only.
