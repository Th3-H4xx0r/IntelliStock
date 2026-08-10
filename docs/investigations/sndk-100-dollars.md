# sndk-100-dollars — the best name in the best window was bought at $100 while $582 went into SPY on the same bar

**Source:** `backtests/571147_audit.log` (bt 571147, +17.36%, the best run on record) plus its
`/summary`. Read-only. **Zero runs spent.**

---

## 0. THE ONE-LINE VERSION

```
2026-01-16  [core] bought $582.22 SPY (band_deploy: 11.3% -> 20.4% of NAV)
2026-01-16  Backfill queue BUY: SNDK (queued 6 bars, alloc=$100, score=1.700 HIGH-CONV)
2026-01-16  FILL BUY SPY  0.83919453 @ 693.44   =  $582
2026-01-16  FILL BUY SNDK 0.24274209 @ 414.69   =  $101
```

**SNDK moved +166.10% over that window.** The run put $101 into it and $582 into an index that
finished the window at **-$0.29 of P&L**. Same bar. Same quote timestamp.

---

## 1. WHAT SNDK WAS WORTH

From `/backtests/571147/summary`:

| | |
|---|---|
| `stock_price_change.SNDK.change_percent` | **+166.10%** (237.33 -> 631.54) |
| position taken | **$100.66** = **1.7% of NAV** |
| `pnl_percent_per_stock.SNDK` | +52.29% (bought late, on 01-16) |
| `pnl_per_stock.SNDK` | **+$52.64** |

Every other name in that run was bought at ~$840 (14% of NAV): SLV $840, CPER $840, SBLK $840,
XOM $837, AMAT $846. SNDK — the only name in the book that tripled — got **12% of a normal clip**.

**Counterfactual, at the same entry bar and the same capture:**

| position | P&L | window return |
|---|---|---|
| actual $101 | +$53 | +17.36% |
| the $582 that went to SPY instead | +$357 | **~+22.4%** |
| a full 14% clip, $840 | +$439 | **~+23.8%** |

The objective's 2x bar is +20%/2mo. **This one position, sized like every other position, is the
difference between 1x and 2x on the best window we have.** No new signal, no new name, no better
timing — the run already found it, already ranked it 1.700 HIGH-CONV, already bought it.

---

## 2. WHY IT GOT $100 — IT IS STRUCTURAL, NOT BAD LUCK

The backfill queue's budget is **half of available cash**, and its floor is `min_pos`:

```
V28 BFQ DRAIN ENTRY: queue_size=60 headroom=7 cash=$770
                     priority_budget=$385 standard_budget=$385 min_pos=$100
```

```
    BFQ priority budget   $385     (50% of ~$770 cash)
    conviction clip       $840     (0.14 x NAV)
    $385 < $840  ->  THE QUEUE CAN NEVER FUND ONE CONVICTION POSITION
```

This is **the same arithmetic as last session's headline bug**, one layer down. There, the
conviction overflow band ($600) was smaller than one clip ($840). Here the queue's budget ($385)
is smaller than one clip ($840). `alloc=$100` is not a decision — it is `min_pos`, the smallest
legal position, which is what a half-residual rule collapses to once several names share it.

And SNDK was not a late arrival. It sat in the drain's own top-10 at **score 1.900** for five
consecutive bars while the budget never moved:

```
01-08  ... min_pos=$100 top10=[SON(2.100,3d), SHEL(2.000,1d), SNDK(score=1.900, ...)]
01-11  ... priority_budget=$375 ... SNDK(score=1.900, age=...)
01-12  ... priority_budget=$380 ... SNDK(score=1.900, ...)
01-15  ... priority_budget=$382 ...
01-16  ... priority_budget=$385  ->  Backfill queue BUY: SNDK alloc=$100
```

Meanwhile the CONCENTRATE lane sized it correctly and was overruled:

```
V31.2 total-spend cap [CONCENTRATE]: funded 3 of 4 by conviction (SNDK@$899, BTC@$899, GLUE@$899)
SATELLITE CAP: SNDK trimmed $899 -> $674 to keep the core at target
Buy gate inputs for SNDK: ... cash_per_trade=$673.92 available=$714.66 cash_to_use=$673.92 -> PASS
FILL BUY SNDK 0.24274209 @ 414.687474                                            = $100.66
```

**The gate approved $673.92. The fill was $100.66.** The queue's number is what reached the order.

---

## 3. THE CORE ATE THE CASH THAT WAS MISSING

Across the whole window the core BOUGHT SPY four times:

```
01-02  $2,400.00   band_deploy   0.0% -> 40.0%
01-06  $1,546.65   band_deploy  12.0% -> 37.4%
01-16  $  582.22   band_deploy  11.3% -> 20.4%    <- same bar SNDK got $100
01-21  $  742.71   band_deploy   6.9% -> 18.5%    <- the day after releasing $843
                   ---------
                   $5,271.58 of SPY purchases
```

`pnl_per_stock.SPY` for the run: **-$0.29**.

The 01-20/01-21 pair is the recycle the `core_funding_release_reserve_decisions=4` lever was
supposed to stop: released $534 + $309 on 01-20, bought $743 of SPY back on 01-21. **It is still
happening with that lever set.**

---

## 4. WHAT TO DO — ONE KEY, ALREADY WRITTEN, ALREADY TESTED

`bfq_conviction_target_weight_pct` (default 0.0 = OFF) replaces the half-a-residual rule with a
share of NAV for names at or above the conviction threshold. It is implemented at
`graph_nexus_analysis.py:31843`, clips to `single_position_max_pct`, refuses rather than leaving a
sub-floor crumb, and — unusually for this repo — **already carries its own log signature**:

```
BFQ TARGET-WEIGHT: <SYM> score=1.700 >= 1.50 — sized $840 (14.0% of $6000, target 14%)
                   from the priority pool $385; the half-a-residual rule would have paid $100
```

It has 20+ tests in `backend/tests/test_bfq_conviction_target_weight.py`.

**Set `bfq_conviction_target_weight_pct = 0.14`** — the same number as
`total_spend_cap_target_weight_pct`, so the queue lane sizes like every other lane.

**Declared pass/fail for the validation run (reference window, 2026-01-01..2026-03-01):**

| outcome | reading |
|---|---|
| `BFQ TARGET-WEIGHT: SNDK ... sized $8xx ... would have paid $100` in the log | the lever bound; this is the whole claim |
| line absent | inert — do not report a return change as evidence of anything |
| line present, SNDK position still ~$100 | the cash was not there; the blocker is §3's core deploy, not the queue |
| return < +17.36% - 4.94pp | the lever is negative beyond the noise floor; revert |

**Then the second question, and only then:** suppress `band_deploy` while a conviction name is
queued and unfunded. §3 says the money for a proper SNDK position existed on the bar — it went
into SPY.

---

## 5. LIMITS

* **One name, one window.** n=1. SNDK is also the single best case in the dataset, so the +6.4pp
  is an upper bound on this window and says nothing about the others.
* The +166.10% is the FULL-WINDOW move; the run only ever captured +52.29% of it because it
  entered on 01-16. The counterfactual above holds the entry date fixed and changes only the size,
  which is the honest comparison.
* A $840 clip was not fundable from cash alone on 01-16 (`cash=$770`). The $582/$840 rows in §1
  therefore require §3's core deploy to be suppressed as well; the $582 row is the one that needs
  no extra capital at all.
* `bfq_conviction_target_weight_pct` has never run. Everything above is log arithmetic.
