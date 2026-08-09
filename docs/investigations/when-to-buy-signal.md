# When To Buy — entry-timing signal design (bt 201039)

Investigator note. READ-ONLY investigation. No code was edited, nothing pushed, no
backtest started or stopped. Everything below is measured against the RUN and against
the daily bars the run itself had in its overlay cache.

Primary run: **bt 201039** — 2026-01-01..2026-03-01, `v2-let-run-core`, $6,000, 3600s,
`finished`, **+8.34%** ($500.39). 40,323 log lines pulled to
`backtests/201039_full.log` (`python3 scripts/pull_backtest_logs.py 201039`).
Comparison runs: **820236** (+12.33%), **613166** (+9.17%).

## Data provenance — what "the bars the strategy already has" means here

The strategy caches split-adjusted **daily** OHLCV per symbol in RethinkDB table
`IntelliStock.GraphNexusOverlayBarsCache`, written by
`backend/strategies/graph_nexus_analysis.py:21040` (`Overlay bars: fetching ...`) and
read at `graph_nexus_analysis.py:20672`. I read that table directly (read-only) for
2,483 symbols. Every row carries `fetch_start=2025-08-19 fetch_end=2026-08-08`,
`adjustment=split` — the exact range bt 201039 logged:

    [L18..] 2026-01-01  Overlay bars: fetching 144 symbol(s) (2025-08-19 to 2026-08-08)

Sanity check against the run's own summary (`stock_price_change`): daily closes from the
cache reproduce every start/end price to within the hourly-mark rounding (SNDK
237.33→631.54 vs cache 237.33→635.94 at the 02-27 daily close; XOM 120.33→152.59 vs
120.33→152.59). **Same data. No new data source.** All rule tests below use only:
daily O/H/L/C/V, r20/r60 (already computed by the strategy), and nothing else.

---

# PART 1 — WHAT ACTUALLY HAPPENED

## 1.1 The run's own entries, and where they sat in the move

`entry=$` is the fill price the run's own monitor reports
(`Monitor decision: <SYM> day N pnl=..% cp=$.. entry=$..`). `%thru` = where the entry
price sat between the 2025-12-31 close and the window's highest daily close.

| sym | move | maxrun | run entry date | run entry | %thru | our P&L% |
|---|---|---|---|---|---|---|
| SNDK | +168.0% | +193.1% | 2026-02-02 | $660.48 | **92.3%** | **-4.4%** |
| PLRZ | +61.8% | +97.9% | 2026-01-15 | $15.48 | **92.8%** | **-17.6%** |
| WDC  | +62.4% | +72.2% | 2026-01-30 | $259.37 | **70.1%** | +7.5% |
| HL   | +29.7% | +65.8% | 2026-01-27 | $28.23 | **71.6%** | **-18.5%** |
| AVNT | +31.4% | +38.6% | 2026-01-20 | $35.64 | 36.4% | +1.1% |
| EGO  | +29.3% | +38.5% | 2026-02-02 | $38.87 | 21.3% | +19.4% |
| XOM  | +26.8% | +29.4% | 2026-01-01 | $120.24 | -0.3% | +26.9% |
| NTR  | +21.6% | +21.6% | 2026-01-01 | $61.91 | 1.4% | +21.2% |
| BA   | +4.8%  | +16.1% | 2026-01-02 | $222.80 | 16.3% | +2.1% |
| TCMD | +1.1%  | +13.9% | 2026-01-01 | $28.16 | -19.9% | +8.1% |
| SPY  | +0.6%  | +2.0%  | 2026-01-01 | $682.97 | 8.4% | +0.1% |
| VOYA | -10.2% | +6.0%  | 2026-01-01 | $75.15 | 14.1% | -0.8% |
| AAL  | -14.6% | +4.4%  | 2026-01-06 | $16.02 | 102.9% | -12.3% |

Seven names moved >=25%. Mean move **+58.1%**, mean realized **+2.1%** — the parent's
number, confirmed from `pnl_percent_per_stock`. Mean entry-%-through-move on those
seven: **54.9%**. On the four names we got right (XOM/NTR/BA/TCMD) the mean entry was
**1.4% through**. That is the whole story in one number pair: *we buy the names we win
on at the start of their move and the names we lose on at the end of theirs.*

## 1.2 The SNDK timeline — discovery was 20 sessions late, not the gate

SNDK's daily bars were in the strategy's hands on **day 1**:

    [L351] bar 2026-01-01  [BROKER] Fetched chunk 1/1 for SNDK: 244 bars (2025-08-19 to 2026-08-08)

SNDK then went 237.33 (12-31) → 275.29 (01-02) → 349.59 (01-06) → 501.21 (01-21).
The first time SNDK is named as a *candidate* anywhere in 40,323 log lines is
**2026-01-30**, 19 sessions later, and not by the momentum scanner — by news propagation:

    [L21995] bar 2026-01-30  Propagation scoring expansion: 40 ticker(s) added: AAPL, INCY, KLAC, PLX, RBLX, RCL, SNDK, TER, USARE, VSEC

First momentum-watchlist appearance, three sessions after that:

    [L23023] bar 2026-02-02  Momentum watchlist: watchlist=463, scored=306, top3=[('SNDK', 1.505), ('PLRZ', 1.04), ('MU', 0.603)], held_momentum=1, new_buys=['SNDK']
    [L23216] bar 2026-02-02  SNDK @ 2026-02-02 15:00:00 ($617.375): buy action_intent=momentum_watchlist_buy
    [L23218] bar 2026-02-02  TURNOVER BUDGET BYPASS: SNDK raw=+1.705 >= 1.50 — admitting a conviction buy through a 105% budget

Signal price $617.375, **fill $660.48** — the buy executes on the next event, so the
book paid 7.0% above the price the signal was computed at. Add that to the 92.3%.

**This is a WHEN problem sitting on top of a WHERE-TO-LOOK problem.** The bars were
there from day 1; nothing in the pipeline looked at them until news dragged the name in.

## 1.3 Cross-run corroboration: the same name, three entry dates, three results

Same window, same ticker, three independent runs (entry price = each run's own
`Monitor decision ... entry=$`):

| run | SNDK entry date | fill | sessions after SNDK's first 55-day-high breakout (2026-01-06) | %thru | run's SNDK P&L% |
|---|---|---|---|---|---|
| 820236 | 2026-01-12 | $443.83 | 4 | 45.1% | **+20.6%** |
| 613166 | 2026-02-02 | $592.01 | 18 | 77.4% | +2.4% |
| 201039 | 2026-02-02 | $660.48 | 18 | 92.3% | **-4.4%** |

820236 is also the **best run of the ladder (+12.33%)** and the only one that bought
SNDK early. It also held WDC from `initial_buy` at $172.27 on day 1 (+53.6%) versus
201039's $259.37 on 01-30 (+7.5%). Monotone in entry date, across three runs. This is
evidence *outside* bt 201039 for the same mechanism.

---

# PART 2 — WHY THE "TOO EXTENDED" MACHINERY DIDN'T FIRE ON SNDK, AND DID FIRE ON A NAME THAT WAS DOWN 44%

Three separate gates exist to stop late entries. All three measure **magnitude of
trailing move**, none measures **position relative to a base**. In this run all three
produced the wrong answer.

## 2.1 The entry-extension gate measures range WIDTH, with no direction

`_recent_runup_protect` (`graph_nexus_analysis.py:9259-9281`):

```python
lo = min(closes); hi = max(closes)
runup_pct = ((hi - lo) / lo) * 100.0
return (runup_pct > _bp), runup_pct
```

`hi` and `lo` are unordered. A name that **fell** 44% inside the window scores exactly
the same as one that doubled. Evidence, from the run:

    [L649] bar 2026-01-01  V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25% — no conviction bypass

I reproduced +106.2% exactly from 20 daily closes ending 2025-12-31 (max 14.55 on
12-04, min 7.055) — the gate is reading daily bars with a correct 1-bar point-in-time
lag. But PLRZ's close on 2025-12-31 was **$8.11, i.e. 14.1% of the way up that range and
44% BELOW its 20-day high**. The gate blocked a name sitting near the bottom of its
range because the range was wide. Same for HL:

    [L17465] bar 2026-01-23  V32 mw_buy extension-block: HL recent runup +65.7% > 25% — no conviction bypass

## 2.2 The same gate never blocked SNDK, at a measured +142.7%

SNDK's 20-daily-close range-runup on 2026-02-02 was **+142.7%** (>25%, and >110% on
01-30). There is **not one** `extension-block` or `Entry extension gate` line for SNDK
anywhere in the 40,323 lines. There is also no `pass-thru`/`glitch ceiling` line
(0 occurrences in the log), so the glitch escape did not fire either.

The lookback is `_scale_bars(entry_extension_lookback_bars=20, config)` and the bars are
`_resolve_asof_bars`, which **prefers the broker's `price_history` and only falls back
to the daily overlay cache when the broker has <2 bars** (`graph_nexus_analysis.py:9299`
onward). The broker's series in this run is **1-Hour**:

    [L23097] bar 2026-02-02  Backtest symbol expansion: fetching 1Hour history for 1 discovered symbol(s): SNDK
    [L23098] bar 2026-02-02  Backtest symbol expansion: loaded 733 1Hour bars for SNDK

So on the bar SNDK was bought, the gate had 733 hourly bars and measured a **20-bar =
~3-trading-session** range; PLRZ (no broker bars at the time) fell back to the overlay
cache and got a **20-DAY** range. The code comment says so itself
(`graph_nexus_analysis.py:5537-5538`): *"scale the lookback to the run's cadence (20
bars ≈ ~3 trading days at 1h, but ~a month at 1d)"*. The unit of the window depends on
which series happens to be attached to the symbol at that moment.

Second instance of the same inconsistency inside the same run: PLRZ was blocked at
+106.2% on 01-01 and then **bought at a +111.7% 20-day runup on 01-15/01-16** with no
block line at all — its hourly series had filled in by then.

    [L1336] 2026-01-15  PLRZ @ 2026-01-15 15:00:00 ($14.50): buy action_intent=momentum_watchlist_rotation
    [L1456] 2026-01-16  PLRZ @ 2026-01-16 15:00:00 ($15.01): buy action_intent=momentum_watchlist_buy

## 2.3 The breadth scanner's parabolic cap excludes exactly the names it exists to find

`_breadth_scan_movers` (`graph_nexus_analysis.py:20898`) is enabled in this run — the
watchlist line shows `breadth_scan=70`. Its admit test is
`r5 >= breadth_scan_r5_min_pct (7%)` and `r20 >= 12%`, with caps
`r20 <= breadth_scan_r20_parabolic_cap_pct (60)` and
`r60 <= breadth_scan_r60_parabolic_cap_pct (150)`. SNDK, computed from the same bars:

| date | close | r20 | r60 | breadth-admissible? |
|---|---|---|---|---|
| 2026-01-02 | 275.29 | +41.5% | +127.8% | yes |
| 2026-01-05 | 274.20 | +28.5% | +107.8% | yes |
| **2026-01-06** | **349.59** | +53.0% | **+169.5%** | **NO (r60 cap)** |
| 2026-01-07 | 353.84 | +56.9% | +202.8% | NO |
| 2026-01-09..2026-02-05 | 377→576 | +62..+154% | +135..+242% | NO on every session |

SNDK was admissible on **3 of 24 sessions**, and the cap slammed shut on **the exact day
it made its first 55-day high**. The scanner also rotates
`breadth_scan_batch_per_bar=50` names through a 500-name universe, so any given name is
only looked at once every ~10 bars.

**All three gates share one design error: they treat "size of the recent move" as a
proxy for "late". It is not. It is a proxy for "big mover", which is the thing the
OBJECTIVE says to buy.**

---

# PART 3 — CANDIDATE ENTRY RULES, BACKTESTED ON THE BARS

## 3.0 Method

Universe: the **800 symbols the run itself fetched bars for** during bt 201039 (extracted
from `Fetched chunk .. for X` / `Bars chunk from cache for X`, intersected with the
overlay cache). Signal computed on the daily close of day *d* using only bars <= *d*.
**Entry at the next day's OPEN** (the run's own execution is next-event, and paid 7.0%
of slippage doing it, so next-open is if anything generous to the run). No exits — hold
to the last bar of the window, matching `v2-let-run-core` and the exits investigation's
finding that exits are not the leak. "First-signal-only" unless stated.

Generalization windows, all on the same cache, all out-of-sample except W1:

    W1  2026-01-01..2026-03-01   the run's window (in-sample)
    W2  2025-11-14..2026-01-01   OOS, before the run
    W3  2026-03-02..2026-04-30   OOS, the bear/recovery window (bt 342380's regime)
    W4  2026-05-01..2026-06-30   OOS
    W5  2026-06-01..2026-08-07   OOS, a losing tape

## 3.1 Rule A — breakout to an N-day high (instead of trailing-return rank)

**Signal:** `close[d] > max(high[d-N .. d-1])`.

### On bt 201039's own 13 names (entry at next open, hold to 02-27)

| sym | run entry | run %thru | run ret | **20d-high** entry | %thru | ret | 55d-high entry | %thru | ret |
|---|---|---|---|---|---|---|---|---|---|
| SNDK | $660.48 | 92.3% | -3.7% | **$283.44 (01-05)** | **10.1%** | **+124.4%** | $341.49 (01-07) | 22.7% | +86.2% |
| WDC | $259.37 | 70.1% | +7.9% | **$211.98 (01-07)** | 31.9% | **+32.0%** | $211.98 | 31.9% | +32.0% |
| HL | $28.23 | 71.6% | -11.8% | **$21.01 (01-07)** | 14.4% | **+18.5%** | $21.01 | 14.4% | +18.5% |
| EGO | $38.87 | 21.3% | +19.4% | **$36.78 (01-07)** | 6.3% | **+26.2%** | $36.78 | 6.3% | +26.2% |
| AVNT | $35.64 | 36.4% | +15.2% | **$31.80 (01-06)** | 4.5% | **+29.2%** | $34.21 (01-12) | 24.5% | +20.1% |
| PLRZ | $15.48 | 92.8% | -15.2% | $14.50 (01-15) | 80.5% | -9.5% | *never fires* | — | — |
| XOM | $120.24 | -0.3% | **+26.9%** | $125.26 (01-05) | 14.0% | +21.8% | $125.26 | 14.0% | +21.8% |
| NTR | $61.91 | 1.4% | **+21.2%** | $66.26 (01-15) | 34.0% | +13.3% | $66.26 | 34.0% | +13.3% |
| BA | $222.80 | 16.3% | +2.1% | $229.27 (01-05) | 34.8% | -0.8% | $229.27 | 34.8% | -0.8% |
| TCMD | $28.16 | -19.9% | +4.0% | $29.76 (01-06) | 20.0% | -1.6% | $29.76 | 20.0% | -1.6% |
| VOYA | $75.15 | 14.1% | -11.0% | $77.02 (01-06) | 55.6% | -13.1% | $77.02 | 55.6% | -13.1% |
| AAL | $16.02 | 102.9% | -18.3% | *never fires* | — | — | *never fires* | — | — |
| SPY | $682.97 | 8.4% | +0.5% | $692.18 (01-07) | 75.6% | -0.9% | $692.18 | 75.6% | -0.9% |

Answering the parent's questions directly:

* **"Would it have bought SNDK near $237-$400?"** — YES. 20-day-high breakout: **01-05
  at $283.44**, 10.1% through the move, +124.4% to window end. 55-day-high: 01-07 at
  $341.49, +86.2%. The run paid $660.48 for -3.7%.
* **"What about HL?"** — YES. $21.01 on 01-07 instead of $28.23 on 01-27. **+18.5%
  instead of -11.8%.** HL topped at $31.81 on 01-23 and the run bought it on 01-27, four
  sessions after the high.
* **"What about PLRZ?"** — **NO. Honest failure.** PLRZ's entire move was one gap:
  $8.11 → $12.875 on 2026-01-02, +58.8% in a single session, with no prior high broken
  (its 20-day and 55-day prior high were both $18.145 from 12-04). A breakout rule
  cannot see this and *should not* — there was nothing to break out of. The 20-day rule
  fires only on 01-15 at $14.50 (80.5% through) for -9.5%; the run got -15.2%. Marginal
  improvement, no prize. **PLRZ is not catchable by any rule in this family.**
* **"What about the names we already get right?"** — **It costs us.** XOM +26.9% → +21.8%
  (-5.1pp), NTR +21.2% → +13.3% (-7.9pp), TCMD +4.0% → -1.6%, BA +2.1% → -0.8%. Those
  four are bought at the window open by `initial_buy` at ~0-16% through their moves;
  requiring a breakout can only delay them. **This is the reason to ADD a breakout lane,
  not to REPLACE `initial_buy`.**

Aggregate on the 13 names — mean entry-%-through-move and mean return, versus the run:

| entry rule | fired | mean %thru | mean ret | on the 7 big movers: %thru / ret | hit rate |
|---|---|---|---|---|---|
| run 201039 (actual) | 13 | 39.0% | +2.9% | **54.9% / +5.5%** | 62% |
| A: 20-day-high breakout | 12 | 29.8% | +17.2% | **23.1% / +34.7%** | 58% |
| A: 55-day-high breakout | 11 | 30.3% | +18.3% | **19.0% / +34.1%** | 64% |
| B: distance-from-MA20 <= 10% | 13 | 26.6% | +11.0% | 28.1% / +21.7% | 62% |
| C: VCP contraction + breakout | 10 | 53.5% | +7.3% | 53.4% / +12.2% | 60% |
| E: pullback to first higher-low | 6 | 45.3% | +14.3% | 29.3% / +34.6% | 50% |

### Does it generalise? Universe-wide, five windows, 800 names

`%thru` = mean entry-%-through-move restricted to names that ended the window >=25% up.
`capture` = mean realised return from entry / mean realised move of those names.

| window | breakout-55 %thru / capture | rank max(r20,r60) K=12 | rank r60 K=12 | rank r20 K=12 |
|---|---|---|---|---|
| W1 (the run) | **35% / 49%** | 52% / 31% | 50% / 31% | 69% / 13% |
| W2 OOS pre | 56% / 27% | 45% / 30% | 33% / 38% | 66% / 16% |
| W3 OOS bear | **39% / 47%** | 50% / 35% | 42% / 37% | 52% / 34% |
| W4 OOS | **33% / 37%** | 49% / 11% | 47% / 21% | 58% / 13% |
| W5 OOS | **42% / 41%** | 68% / -4% | 69% / -3% | 60% / 18% |
| mean | **41% / 40%** | 53% / 21% | 48% / 25% | 61% / 19% |

Breakout entry is earlier than the trailing-return rank in 4 of 5 windows and captures
more of the move in 4 of 5. **W2 is a genuine loss** and I am not hiding it.

Matched test (removes any selection difference — only names on which BOTH rules fire,
so the two rules trade the identical basket and differ only in WHEN):

| window | n | breakout %thru | rank %thru | breakout mean ret | rank mean ret | breakout median | rank median | breakout, big movers | rank, big movers |
|---|---|---|---|---|---|---|---|---|---|
| W1 | 77 | 44% | 55% | **+10.7%** | +7.3% | **+10.1%** | +1.0% | **+24.0%** | +17.8% |
| W2 | 48 | 57% | 38% | +6.8% | **+10.2%** | +0.7% | **+7.5%** | +20.3% | +20.2% |
| W3 | 80 | 39% | 39% | **+21.0%** | +20.6% | **+14.6%** | +13.6% | **+33.3%** | +31.0% |
| W4 | 71 | 34% | 40% | **+19.2%** | +14.1% | **+6.1%** | +3.2% | **+38.9%** | +31.8% |
| W5 | 73 | 42% | 55% | **-1.7%** | -8.8% | **-4.7%** | -5.4% | **+23.5%** | +4.4% |

Same names, only the trigger differs: breakout wins on mean in 4/5, on median in 4/5,
and by the largest margin on big movers in 5/5. **This is the strongest generalisable
result in this document.**

### Choice of N — 20 vs 55, five windows

| window | N=20 %thru | N=40 | N=55 | N=60 | | N=20 capture | N=40 | N=55 | N=60 |
|---|---|---|---|---|---|---|---|---|---|
| W1 | **28%** | 32% | 35% | 36% | | **56%** | 52% | 49% | 47% |
| W2 | **39%** | 51% | 56% | 56% | | **46%** | 32% | 27% | 27% |
| W3 | **25%** | 37% | 39% | 41% | | **66%** | 50% | 47% | 45% |
| W4 | **25%** | 31% | 33% | 34% | | **46%** | 41% | 37% | 37% |
| W5 | **22%** | 37% | 42% | 42% | | **63%** | 46% | 41% | 41% |

N=20 is earlier and captures more in **5 of 5 windows**, at a median-return cost of
0.1-0.8pp. This is a monotone, unanimous ordering, not a fitted optimum. **Use N=20.**

## 3.2 Rule B — distance from the 20/50-day MA instead of raw runup: **FAILS as a block**

Tested as a cross-sectional predictor over all 160,645 name-days in the 5 windows,
against the 20-day forward return from the next open. Mean daily Spearman rank-IC:

| measure | rank-IC vs fwd20 | t |
|---|---|---|
| `runup20` = (max-min)/min of last 20 closes — **the current gate** | **-0.0274** | -2.20 |
| `close/MA20 - 1` | **+0.0178** | +1.87 |
| `close/MA50 - 1` | **+0.0149** | +1.46 |
| `r20` | +0.0193 | +2.11 |
| `r60` | -0.0030 | -0.24 |
| `w10/w50` (range contraction) | +0.0410 | +6.34 |

Quintiles of `close/MA50 - 1`, forward 20d:

| quintile | n | mean fwd20 | median | hit |
|---|---|---|---|---|
| Q1 (furthest BELOW MA50) | 32,129 | **+5.03%** | +1.22% | 53.5% |
| Q2 | 32,129 | +2.29% | +1.66% | 56.9% |
| Q3 | 32,129 | +1.88% | +0.70% | 55.5% |
| Q4 | 32,129 | +1.73% | +1.01% | 54.8% |
| Q5 (most extended ABOVE MA50) | 32,129 | +1.96% | +0.44% | 51.8% |

The most-extended quintile returns +1.96%, indistinguishable from the middle. There is
**no threshold of MA-distance at which forward returns fall off a cliff**. Distance from
the MA does not identify "too late to buy". **Do not replace the runup gate with an
MA-distance block — it will not work.** (It is a fine *sizing* input; it is not a gate.)

The corresponding evidence *against the current gate as a gate*:

| current gate's verdict | n | mean fwd20 | median | hit |
|---|---|---|---|---|
| `runup20 > 25%` → **BLOCKED** | 40,930 | **+4.39%** | +0.07% | 50.2% |
| `runup20 <= 25%` → admitted | 119,715 | +1.96% | +1.15% | 56.0% |

The gate blocks the half of the distribution with **2.2x the mean forward return** and
buys a better median and hit rate with it. That is a rational trade for a
median-maximising strategy and an irrational one for the stated objective
(`docs/OBJECTIVE.txt`: *"The mechanism is NOT grinding small edges across many
positions. It is catching the big movers."*). Note this is **consistent** with the
DO-NOT-RETRY entry in OBJECTIVE.txt — the blocked basket's *median* is +0.07%, so
equal-weighting everything the gate blocks returns roughly nothing (measured -7.95%
previously). The fix is not "loosen the gate"; it is "stop using range-width as the
lateness measure at all".

## 3.3 Rule C — consolidation-then-breakout (volatility contraction): **FAILS**

`contract = (10-day high-low range) / (50-day high-low range)`; low = contracted.

On the 13 names (Section 3.1 table): requiring `contract <= 0.35` at the breakout **delays
every big entry** — SNDK $535.84 on 01-29 (65.1% through) instead of $283.44, WDC $263.23
(73.2%), XOM $153.91 on 02-11 (**95.1% through**), NTR $72.96 (84.2%). Mean big-mover
entry 53.4% through, worse than plain breakout's 23.1% and no better than the run's 54.9%.

Universe-wide it is neutral-to-negative as an add-on filter (W1: n 497→253, mean return
+5.4%→+5.7%, big-mover count 131→86). And the cross-sectional IC has the *wrong sign* for
the VCP hypothesis: `contract` IC is **+0.041 (t=6.34)** — *expansion*, not contraction,
precedes higher forward returns. Contraction quintile Q1 does have the fattest mean
(+4.31%) but the worst median (+0.55%) and worst hit rate (52.4%).

**Verdict: do not build this.** The mechanism is not present in this data.

## 3.4 Rule D — first breakout only, refuse later ones: **partly supported, with an
important honest correction**

The tempting version of this claim is wrong. Here is the unbiased test — **fixed 20-day
forward return** from the entry (not return-to-window-end, which mechanically favours
early entries because they are held longer). Pooled across all 5 windows, 800 names,
every 55-day-high breakout, bucketed by bars elapsed since that name's FIRST breakout:

| bars since first breakout | n | fwd10 | **fwd20** | fwd40 | hit(20d) | mean %thru |
|---|---|---|---|---|---|---|
| 0 (the first breakout) | 748 | +1.12% | **+2.82%** | +4.79% | 58.6% | 38% |
| 1-3 | 644 | +0.78% | **+2.95%** | +4.89% | 61.0% | 47% |
| 4-7 | 468 | +1.75% | **+3.86%** | +6.55% | 62.2% | 55% |
| 8-14 | 549 | +1.84% | **+4.35%** | +5.30% | 60.7% | 66% |
| 15-25 | 836 | +1.95% | **+3.52%** | +4.90% | 61.7% | 74% |
| **>25** | 5,388 | +0.92% | **+1.73%** | +2.52% | **50.2%** | 82% |

**Honest reading:** per-trade expectancy over a fixed horizon is FLAT from 0 to 25 bars
(+2.8% to +4.4%, hit 59-62%). Buying the first breakout does **not** earn a higher
forward return than buying the fourth. What it buys is **position in the move for free**:
the same expected forward return, entered ~36 percentage points earlier
(38% vs 74% through). For a strategy whose P&L is "own the big move at size for its
duration", that is the entire difference — but it is a *capture* argument, not an
*expectancy* argument, and I will not dress it up as one.

There **is** a real cliff, but it is at **>25 bars**, not at bar 1: +1.73% and a 50.2%
hit rate versus +2.8-4.4% and ~60%. SNDK was bought 18 bars stale, WDC 17, EGO 17, HL 14
— **all inside the 15-25 band, so a >25-bar freshness cut would not have stopped a single
one of them.** A freshness gate tight enough to matter (<=7 bars) is not supported by
forward-return evidence; it is supported only by the capture argument.

I also tested the freshness constraint **as a pre-filter on the r60 ranker** (only rank
names within B bars of their first breakout). It is **not robust**: W5 improves sharply
(mean -13.6% → +2.2%, big-mover return -2.5% → +10.3%), W1 improves on entry timing
(%thru 50 → 44), but **W3 gets much worse** (+20.4% → +2.3%) and W4 worse at B<=7. The
filter changes which names are in the top-12, which is a confound. **Do not ship the
freshness constraint as a ranker pre-filter.**

## 3.5 Rule E — pullback entry (buy the first higher-low after a breakout): **FAILS**

Definition: after the first 55-day-high breakout, wait for at least one down close, then
buy the first day with `low > prior low` AND `close > prior close`, within 15 bars.

| window | n matched | immediate entry: %thru / mean ret / median | pullback: %thru / mean ret / median | pullback got a cheaper entry |
|---|---|---|---|---|
| W1 | 251 | 55% / **+3.1%** / **+3.4%** | 56% / +1.9% / +1.7% | 33% of the time |
| W2 | 282 | 62% / **+0.5%** / -0.9% | 51% / -0.3% / -0.7% | 48% |
| W3 | 65 | 59% / **+4.8%** / **+3.4%** | 60% / +1.6% / +0.3% | 32% |
| W4 | 66 | 53% / -1.4% / -4.2% | 36% / -1.7% / **-1.0%** | 62% |
| W5 | 44 | 14% / +4.3% / +5.0% | -27% / **+8.6%** / **+7.2%** | 61% |

Worse mean return in 4 of 5 windows, and it **misses names entirely** — on the 13 names
it never triggers for EGO, HL, NTR, WDC or PLRZ (no qualifying higher-low inside 15 bars)
and enters SNDK at $374.54 instead of $283.44. A strong trend does not give you a
higher-low; that is what makes it a strong trend. **Do not build this.**

---

# PART 4 — SIZE OF THE PRIZE, AND WHAT ELSE HAS TO BE TRUE

## 4.1 Bounded counterfactual under the run's OWN constraints

The run sized conviction buys at `cash_to_use=$860.44` (`Buy gate inputs for SNDK:
... cash_per_trade=$860.44 ... → PASS`, L23219) and enforced `held=6, cap=6` on **553 of
634 bars (87.2%)**. So: 6 slots, $860.44 each = $5,163 of a $6,000 book, filled
first-come-first-served by 55-day-high breakout date, held to 02-27, restricted to the
run's own 13 tickers:

| sym | entry date | entry | exit | P&L |
|---|---|---|---|---|
| XOM | 2026-01-05 | $125.26 | $152.59 | +$187.74 |
| BA | 2026-01-05 | $229.27 | $227.51 | -$6.61 |
| TCMD | 2026-01-06 | $29.76 | $29.28 | -$13.88 |
| VOYA | 2026-01-06 | $77.02 | $66.90 | -$113.06 |
| SNDK | 2026-01-07 | $341.49 | $635.94 | **+$741.92** |
| WDC | 2026-01-07 | $211.98 | $279.79 | **+$275.22** |
| **total** | | | | **+$1,071.33 on $5,163 (+20.8%)** |

versus the run's actual **+$500.39 (+8.34%)**. The single SNDK line is +$741.92 against
the run's realised **-$37.73**.

**Caveats I will not paper over.** (i) The 13-name list is itself an output of the run,
so this basket is contaminated by which names got traded; treat it as an illustration of
magnitude, not as a backtest. (ii) Running the same 6-slot procedure on the full 800-name
universe gives +$1,230 in W1, but **-$439 in W2, +$1,851 in W3, +$920 in W4, -$1,358 in
W5** — n=6 per window is far too few to promote anything on. The statistics in Part 3,
not this table, are the evidence.

## 4.2 The trigger is necessary but not sufficient — the plumbing refuses it

On 2026-01-06 and 2026-01-07, the two days a 20/55-day-high trigger fires on
SNDK/WDC/HL/EGO simultaneously, the run's book was already full and the brake was on:

    [L5672] 2026-01-06  max_positions gate armed: held=6, cap=6
    2026-01-06: 15 × "TURNOVER BUDGET BINDING: ...% of NAV traded in the last 21 sessions — new discretionary BUYS are blocked this tick"
    2026-01-07: 15 × the same, and 15 × held=6, cap=6

Over the run: `held=6,cap=6` on 87.2% of bars, `TURNOVER BUDGET BINDING` on 569 lines,
25 `MAX_POSITIONS_GATE: blocked`. Shipping a better trigger into that book buys nothing —
it produces a better signal that is refused on the same tick. This is the same conclusion
`_SYNTHESIS.md` reaches from the capital side (cash race, max_positions plumbing
mismatch). **Entry timing and slot availability have to ship together or neither is
testable.**

---

# RANKED LIST — WHAT TO CHANGE

Ordered by (evidence strength × dollars). Every item is default-OFF, per-document, per
`docs/OBJECTIVE.txt`. None of these is validated for promotion; each needs the paired-run
protocol (3 windows, >=1 OOS, >=1 non-semiconductor leadership, own `history_scope_salt`).

### 1. Add a breakout-triggered entry lane on the overlay-bar pool. **N=20-day high.**
*Change.* At each bar, for every symbol already in `_overlay_bars_raw` (144→919 symbols
in this run), evaluate `close[d] > max(high[d-20..d-1])`. Names that trigger enter the
momentum watchlist that bar with `signal_source=breakout`, and are then ranked and gated
by the existing conviction machinery. This is a **new lane, additive** — do not remove
`initial_buy` (Section 3.1: it is what gets XOM +26.9% and NTR +21.2%, and a breakout
requirement costs those two 5.1pp and 7.9pp).

*Expected effect.* Entry-%-through-move on names that end up moving >=25% drops from
**53% (trailing-return rank) to 41% (55d) / ~30% (20d)**; capture of the realised move
rises from **21% to 40-55%**. On bt 201039's own names: SNDK entered at **$283.44 instead
of $660.48**, HL at **$21.01 instead of $28.23**, WDC at **$211.98 instead of $259.37**,
EGO at **$36.78 instead of $38.87**, AVNT at **$31.80 instead of $35.64**.

*Evidence.* Matched test, same names, only the trigger differs — breakout beats
rank-on-trailing-return on mean return in **4/5 windows** (W1 +10.7 vs +7.3, W3 +21.0 vs
+20.6, W4 +19.2 vs +14.1, W5 -1.7 vs -8.8; **W2 loses, +6.8 vs +10.2**), on median in
4/5, and on the big-mover subset in **5/5**. N=20 beats N=40/55/60 on both entry timing
and capture in **5/5 windows**. Cross-run: 820236 bought SNDK 4 sessions after its first
breakout for **+20.6%**; 201039 bought it 18 sessions after for **-4.4%**.

*Risk.* The trigger fires on 62% of the universe in a 2-month window. It is a **timing
gate, not a selector** — it must sit downstream of the ranker or it will simply buy the
tape. Standalone 6-slot simulations swing +36% to -26% across windows.

### 2. Replace the range-width extension measure with a signed, base-relative one.
*Change.* `_recent_runup_protect` (`graph_nexus_analysis.py:9259-9281`) returns
`(max-min)/min` over the last 20 bars, which is direction-blind. Replace the *measure*
with `close / max(high over prior N) - 1` (distance above the breakout level) or
equivalently "bars since first N-day-high breakout". Keep the gate's threshold behaviour;
change what it measures.

*Expected effect.* Removes the two documented misfires in this run in opposite
directions: PLRZ blocked at +106.2% while sitting **44% below its 20-day high and 14.1%
up its own range** (L649), and SNDK admitted at a measured **+142.7%** 20-day range-runup
with **zero** block lines in 40,323 lines.

*Evidence.* Section 2.1-2.2, quoted log lines plus exact reproduction of +106.2% from 20
daily closes. Cross-sectional: `runup20` IC = -0.027 (t=-2.20) — real but weak, and the
blocked set has **+4.39% mean fwd20 vs +1.96% admitted**, i.e. the gate is a right-tail
filter aimed at exactly the distribution the objective needs.

*Do NOT do the obvious alternative.* Distance-from-MA20/MA50 is a **failed** replacement:
IC **+0.018 / +0.015** (wrong sign for a block), and the most-extended quintile returns
+1.96% fwd20, indistinguishable from the middle. There is no MA-distance threshold at
which forward returns collapse (Section 3.2).

### 3. Fix the bar-unit ambiguity in every price-extension gate.
*Change.* `_resolve_asof_bars` prefers the broker's `price_history` (1-Hour in a 3600s
backtest) and falls back to the daily overlay cache only when the broker has <2 bars, so
`entry_extension_lookback_bars=20` means **~3 sessions for one symbol and ~a month for
another, in the same run, on the same bar**. Pin the extension gates to the **daily**
overlay series unconditionally, or express the lookback in calendar days and resample.

*Expected effect.* Makes the gate deterministic. In this run it is the proximate reason
SNDK was never blocked (hourly series attached on 2026-02-02, L23097-23098) while PLRZ
was (daily fallback, L649), and the reason PLRZ was blocked at +106.2% on 01-01 and then
**bought at +111.7% on 01-15** once its hourly series had filled in.

*Evidence.* Sections 2.1-2.2; the code comment at `graph_nexus_analysis.py:5537-5538`
states the hazard explicitly. Zero `pass-thru` / glitch-ceiling lines in the log, so the
glitch escape is not the explanation.

### 4. Change the breadth scanner's parabolic cap from a trailing-return cap to a
distance-above-breakout cap, and raise the scan rate.
*Change.* `breadth_scan_r60_parabolic_cap_pct=150` / `r20_parabolic_cap_pct=60`
(`graph_nexus_analysis.py:20898`) reject on the magnitude of the trailing move. Replace
with "close is <= X% above the N-day high it just cleared". Separately,
`breadth_scan_batch_per_bar=50` over a 500-name universe means each name is examined once
per ~10 bars.

*Expected effect.* SNDK was breadth-admissible on **3 of 24 sessions** and became
inadmissible on **2026-01-06 — the exact day of its first 55-day-high breakout** — because
r60 hit +169.5% > 150. Under a distance-above-breakout cap it stays admissible through the
whole base-to-breakout transition.

*Evidence.* Section 2.3 table, computed from the run's own cached bars.

### 5. Do NOT build: VCP / consolidation-then-breakout.
`contract = w10/w50` has IC **+0.041 (t=6.34)** — *expansion* precedes higher returns, the
opposite of the hypothesis. As a filter it delays every large entry (SNDK 10.1% → 65.1%
through; XOM 14.0% → **95.1%** through) and does not improve universe-wide returns
(W1 mean +5.4% → +5.7% while dropping big-mover coverage 131 → 86). Section 3.3.

### 6. Do NOT build: pullback / first-higher-low entry.
Worse mean return in 4 of 5 windows, gets a cheaper entry only 33-62% of the time, and
never triggers at all for EGO/HL/NTR/WDC/PLRZ in this window. Section 3.5.

### 7. Do NOT build (as specified): a tight first-breakout-only / freshness gate.
The unbiased fixed-horizon test shows forward return is **flat from 0 to 25 bars** past
the first breakout (+2.8% to +4.4%, hit 59-62%). The only real degradation is beyond **25
bars** (+1.73%, hit 50.2%) — and SNDK/WDC/EGO/HL were bought 14-18 bars stale, so a
>25-bar cut catches none of them. As a pre-filter on the r60 ranker it is **not robust**
(W5 and W1 improve, W3 and W4 get materially worse). Section 3.4. A `>25 bars since first
breakout` staleness *penalty on the score* (not a hard block) is the only defensible
version, and it is worth little on this window.

### 8. Prerequisite, not optional: a slot has to exist on the day the trigger fires.
On 2026-01-06 and 2026-01-07 the book was already `held=6, cap=6` and
`TURNOVER BUDGET BINDING` fired 15 times per day. `held=6,cap=6` held on **87.2% of 634
bars**. Any of items 1-4 shipped alone will produce a better signal that is refused on the
same tick. Pair with the capital/max_positions findings in `_SYNTHESIS.md` (§1, §2), or
the A/B will read as "no effect" for reasons that have nothing to do with entry timing.

---

## Things I could not establish

* **PLRZ is not reachable by any rule tested.** Its whole move was a single +58.8% gap on
  2026-01-02 from a price 44% below its 20-day high. No breakout, no consolidation, no
  pullback. The run lost $154.46 on it; the best rule here loses less but does not win.
* **AAL** never triggers any rule (it fell -14.6%). The run bought it at 102.9% through
  its move for -$10.94 — correctly refused by every rule tested, which is a point in
  favour, but n=1.
* **W2 (2025-11-14..2026-01-01) is a genuine counterexample** for the breakout rule on
  both mean return (+6.8% vs the ranker's +10.2%, matched names) and entry timing (57%
  vs 38% through). One losing window in five. If this rule is promoted it should be
  re-checked on a window where leadership is not semiconductors — W1/W3/W4 all have
  semiconductor or semi-adjacent leaders (SNDK, WDC, LRCX, TER, MU, AXTI, AAOI, MXL,
  SOXL), which is exactly the fitting risk `docs/OBJECTIVE.txt` warns about.
* The 6-slot dollar counterfactuals are illustrations, not backtests: n=6 per window,
  and the 13-name basket is selected by the run's own trading.
