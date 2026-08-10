# 2026-08-10 — the benchmark was never in the picture, and capacity is spent by day three

**HEAD:** `ef96d2d` · **Tests:** 4,803 passed / 13 skipped / 19 pre-existing failures
(`test_A11` + the adversarial-findings family, unchanged all session)
**Doc:** 193 (`v2-let-run-core`) · **Deploy:** hash-verified · **Runs this session:** 4
**Goal status:** NOT achieved. Two clauses, failing in different windows — see §1.

---

## 1. THE HEADLINE: THE SCORECARD NOW HAS A BENCHMARK COLUMN

Twenty-plus investigation docs report returns. Not one stated what SPY did in the same window.
`scripts/benchmark_window.py` + `scripts/scorecard.py` read it out of the bars already cached, for
zero runs:

```
  21/23 beat SPY     10/23 at or above 1x pace     mean alpha +9.34pp
```

| window | SPY | best run | alpha | 1x bar | verdict |
|---|---|---|---|---|---|
| ref bull/chop 01-01..03-01 | **+0.24%** | +17.36% (571147) | +17.1pp | +11.6% | 4/15 at 1x |
| bear 03-02..03-30 | **-7.86%** | **+21.27% (789099)** | **+29.1pp** | +5.5% | 4/4 at 1x |
| OOS bull 03-30..04-27 | **+13.10%** | +13.35% (337615) | +0.25pp | +5.5% | **0/3 beat SPY** |
| non-semi 06-01..07-01 | **-1.71%** | +3.09% (584886) | +4.80pp | +5.9% | below 1x |

**Every window the strategy wins big is a window where SPY is flat or falling.** The one window
where SPY runs hard has now been run three times — hedge misfiring, hedge losing $514, hedge
suppressed entirely — and has **never beaten the index**. It is not a hedge problem: the hedge-free
arm also lost. It is a **participation** problem.

So the objective's two clauses fail in different places: the flat tapes give **alpha but not pace**,
the bull tape gives **pace but not alpha**. Only a lever that raises returns fixes both.

**The non-semi overfit test passed on the alpha clause** (+3.09% vs -1.71%) and failed the pace
clause. The config is not fitted to semiconductors; it is just not fast enough in a flat tape.

---

## 2. WHAT SHIPPED AND IS VERIFIED IN A RUN

### The fresh-low gate — `residual_sleeve_bear_block_at_fresh_low_bars=2` + `regime_rally_onset_enabled=true` (LIVE on doc-193)

The sleeve was the one position exempt from every extension check. Measured separation, both
proxies, **for zero runs** (`scripts/check_range_position.py` reads `AlpacaBarsCache`):

```
2026-03-05  the leg that made +$889   bars_since_20d_low = 19   (the maximum possible)
2026-03-30  the leg that lost -$257   bars_since_20d_low =  0   (it IS the low)
```

Both runs confirmed it, against **pre-declared** pass/fail:

| run | window | fresh-low blocks | outcome |
|---|---|---|---|
| 584712 | OOS bull | 12 (+4 rally-onset) | SQQQ notional **$0**; maxDD 11.4% -> **5.8%**; core gross 2.54x -> **0.21x** NAV |
| 789099 | bear | **0** | leg opened 03-05 @ 70.80 — the same bar and price as the +$889 leg; SQQQ **+$918.78** |

**The gate is selective.** That is the whole design and it is now measured, not argued.

The offline replay also **corrected `gap-oos.md`**: the detector is point-in-time, so N=1 leaks the
third bad bar, whose `rally_onset` reclaim clears by **34 cents on a $650 index**. N=2 covers it and
still clears the good park by sixteen bars.

### `fix(nexus)` `b40d2d8` — a pure-hold bar aborted the entire strategy

```
Run-once strategy 'graph_nexus_analysis' error:
  cannot access local variable '_max_positions' where it is not associated with a value
```

`_max_positions` was assigned only inside `if ... _primary_buy_budget > 0:` -> `if stock_buys or
_bfq_pending:`, while the `_nexus_max_positions` publish is unconditional. On a bar with no buys
the read raised and **the whole invocation was abandoned — no scores, no sells, no sleeve intent**.
8 occurrences in the bear window, where `max_positions_bear=2` makes most bars pure holds. **Every
bear number on record was measured with some of its bars silently skipped.**

---

## 3. WHAT WAS KILLED — DO NOT REVIVE

* **"Remove the SQQQ leg and the OOS window is +21.9%."** WRONG, measured. bt 584712 removed it
  entirely and returned **+12.34%**, *below* the control's +13.35%. The freed $2,100 bought
  different names and the book diverged to **2 of 11** overlapping. **Every "remove the loser, add
  back its loss" estimate in this repo is subject to this rebuttal.**
* **Conviction-ranked displacement.** bt 584886 refused ten top-band names; the refused basket
  returned **+2.41%** against the bought basket's **+3.46%**, and inside the band the score does not
  rank: **r = -0.235** (n=15), with NVTS (1.829) at -33% above ALAB (1.700) at +34%.
* **`bfq_conviction_target_weight_pct`.** Fired exactly as declared, and its own log line kills it:
  every ask was capped at the **BFQ pool**, which is 3-6x too small to fund one clip. Reverted.

---

## 4. THE FINDING THAT MATTERS MOST: CAPACITY IS SPENT BY DAY THREE

Following SNDK (**+166.10%** in that window) through bt 424219:

```
V31.2 CONCENTRATE: funded 3 of 4 by conviction (SNDK@$863, ...)     <- sized CORRECTLY
SATELLITE CAP: SNDK trimmed $863 -> $235 to keep the core at target <- cut to a runt
SKIP BUY SNDK — cash_to_use $233.13 < min $370                      <- refused outright
```

`open_pos=6` against `max_positions=6`, core already drawn to ~14% of NAV. Because:

```
01-02  ETH $840, PANW $831, SNDR $840, SON $838  + SPY core $2,398 (40% of NAV)
01-06  AMCR $862, AMD $831
```

**Six slots and 100% of the capital, committed in three sessions of a thirty-nine-session window.**
The same shape in bt 584886 (7 names in 4 of 22 sessions) and bt 571147 (whole book by day 5, then
SNDK gets $101 while $582 goes into SPY on the same bar).

The runt chain is **conviction sizes right -> satellite cap trims -> min-position floor refuses**.
Three gates, name gets nothing. It is not a queue problem and not a ranking problem.

---

## 5. NEXT STEPS, IN PRIORITY ORDER

1. **Stage the deployment.** The only structural lever left that does not require ranking names
   against each other (measured useless, §3). Do not commit 6/6 slots and 100% of capital in the
   first 8% of a window. This is objective blocker #3 approached from the side that the evidence
   supports.
2. **Bull participation.** 0/3 on the OOS window with three different hedge behaviours. Find out
   what the book is doing while SPY compounds +13.10% — that window is the only one failing the
   "beat SPY" clause outright.
3. **The core saw-tooth in the bear regime.** 40 SPY fills, **3.72x NAV** gross in bt 789099
   against 0.21x in the hedge-free OOS run. Every bear-leg refill sells core and every band_deploy
   buys it back.
4. **Suppress `band_deploy` while a conviction name is queued and unfunded.** bt 571147 bought $582
   of SPY on the same bar SNDK got $101; bt 424219 bought $127 of SPY on the bar after.
5. **Production is still `RESEARCH, 0/6`.** Unchanged: zero PIT manifests, 0/60 paper days,
   `live_readiness_report` still has no writer.

---

## 6. THE TECHNIQUE WORTH KEEPING

**`AlpacaBarsCache` in RethinkDB is readable offline.** Questions that looked like they needed a
backtest did not:

* the fresh-low rule's kill test (`check_range_position.py`) — answered for **zero runs**;
* what SPY did in every validation window (`benchmark_window.py`) — the whole of §1;
* whether the refused basket beat the bought basket in bt 584886 — **zero runs**.

Runs are the scarce resource and three of this session's four findings did not need one.

**New tools, all committed:** `scorecard.py`, `benchmark_window.py`, `summarize_backtest.py`,
`set_doc_config.py` (diff + read-back), `check_range_position.py`.

---

## 7. OPERATING RULES CONFIRMED THE HARD WAY

* **Every push auto-deploys and kills a running backtest.** Batch commits; push only between runs.
  Nine commits were held through one 78-minute run this session.
* **Declare the log signature and the pass/fail BEFORE the run.** It worked four times out of four:
  the gate's skip line, its absence in the bear window, the BFQ line, and the revert threshold.
* **The noise floor is real and it is about names, not returns.** Every A/B this session overlapped
  its control by 2 of 9-11 held names. Nothing below ~5pp on a single window is evidence.


---

## 8. APPENDIX — A FIRST MEASUREMENT ON §5.2 (BULL PARTICIPATION)

Sampled at every buy-gate decision in bt 584712 (OOS bull, the window that has never beaten SPY):

```
cash at the gate    median $1,130 (18.8% of NAV)   p25 $1,032   p75 $3,382 (56.4% of NAV)
open positions      median 5 of max_positions 6
core weight         cycled 1.1% .. 27.4% of NAV, logged "deploy_below_min: core 1.2% vs target 10.0%"
```

In a month where SPY compounded **+13.10%**, the book sat on roughly a fifth of NAV in cash at the
median decision point and over half at the 75th percentile, with a core that spent part of the
window at **1.1% of NAV**.

**Caveat: this is sampled at buy-gate lines only (22 of them), so it is biased toward bars where a
buy was being considered.** It is a lead, not a conclusion — but it is the first number pointing at
*why* the strategy cannot keep up with a fully-invested index, and it is consistent with §4:
capacity and capital are committed early, and whatever is left over is not redeployed.

Next measurement to take: per-bar invested fraction from the equity curve and position values,
rather than from gate lines.
