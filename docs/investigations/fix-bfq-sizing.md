# fix-bfq-sizing — the backfill queue funds conviction at a broker minimum

Owner file: `backend/strategies/graph_nexus_analysis.py`
(`_plan_backfill_buy_allocation`, and the drain call site in `run_once`).
Tests: `backend/tests/test_bfq_conviction_target_weight.py` (26 tests).
Config key: **`bfq_conviction_target_weight_pct`** — default `0.0` = OFF.
Secondary (optional) key: `bfq_conviction_target_min_score`, defaults to
`nexus_high_conviction_threshold`, which defaults to `1.5`.

READ FIRST: `_RUNS4.md`, `_SYNTHESIS.md`, `sndk-priority-block.md`,
`entry-conversion.md` §1e, `gap-capital.md`. This document does not repeat them.

---

## 0. TL;DR

* The `$100` in `Backfill queue BUY: SNDK (alloc=$100, score=1.700 HIGH-CONV)`
  is `priority_min_position_size` — a **broker minimum acting as the position
  size**. It is reached whenever the drain pool is between 1x and 2x that
  minimum, which is most of the time, because the pool is a residual that gets
  halved three times before anyone looks at it.
* **75% of every conviction drain in the corpus (59 of 79 with live score >=
  1.5, across 17 backtests) was sized at exactly $100.** Mechanism + evidence
  on far more than 2 windows.
* **BUT the headline claim in `_RUNS4.md` (B) is only half right, and I have to
  say so.** In bt 571147 the `$100` never reached the broker: the CONCENTRATE
  allocator rewrote it to `$899` on the same bar, the satellite cap trimmed it
  to `$674`, the buy gate PASSED at `$673.92`, and it still filled at `$100.67`
  — because of the **cash race** (`_SYNTHESIS.md` root cause #1), not because
  of the queue's clip. Details and proof in §3. This fix is real and it is
  measurable, but it is **not** what made SNDK a 1.7% position on 2026-01-16.

---

## 1. What the budget pools actually are

Chain, all in `graph_nexus_analysis.py`:

```
_available_buy_budget                                   # spendable cash this bar
  └─ _compute_backfill_budget_partition()          gna:9824
       reserve = total * backfill_budget_reserve_pct    # 0.20 (0.25 if a
       primary = total - reserve                        #  raw>=1.5 item is queued)
  └─ main slate spends `primary` -> _stock_budget_after_adds
  └─ DRAIN ENTRY                                   gna:31388-31390
       _bfq_leftover = max(0, _available_buy_budget - already_planned_spend)
       _bfq_cash     = _bfq_leftover + reserve
  └─ 50/50 SPLIT                                   gna:31513-31514
       _bfq_priority_budget = _bfq_cash * 0.50
       _bfq_standard_budget = _bfq_cash - priority
  └─ PER-NAME SIZE                                 gna:10114 (pre-fix)
       allocation = min(available, max(min_required, available * 0.5))
```

`min_required` is `priority_min_position_size` (100.0) for a priority-source
entry, else `min_position_size`.

**Nothing in that chain ever multiplies by `portfolio_total`.** The queue
cannot express a target weight; it can only express a fraction of whatever is
left over. That is the defect.

### Why the number is exactly $100

`min(available, max(100, available*0.5))` equals **100 for every `available` in
[100, 200]**, and equals 0 below 100. So the queue has three regimes and only
three: pool < $100 -> `ALLOC=0`; pool $100-$200 -> **$100 flat**; pool > $200
-> half the pool. It never lands on a weight.

### The three halvings, measured on one real bar

bt **571147**, 2026-01-16, verbatim from `backtests/571147_audit.log:12742`:

```
V28 BFQ DRAIN ENTRY: queue_size=60 headroom=7 cash=$770
    priority_budget=$385 standard_budget=$385 min_pos=$100
    top10=[TXN 2.200, WFC 2.150, SON 2.100, GLUE 2.000, SHEL 2.000,
           SNDK 1.900, TTWO 1.800, APPN 1.800, ARES 1.800, BBAI 1.800]
Backfill queue BUY: GLUE (queued 3 bars, alloc=$193, score=1.000 HIGH-CONV)
Backfill queue BUY: SNDK (queued 6 bars, alloc=$100, score=1.700 HIGH-CONV)
Backfill queue BUY: BTC  (queued 1 bars, alloc=$193, score=1.300)
Backfill queue BUY: CMPX (queued 9 bars, alloc=$100, score=0.000)
V28 BFQ ALLOC=0: MDB, NUVB, BRKR, BKR, GH, V, GDX, GLD, TDY, POOL, BITO,
    AAL, BOTZ   ... priority_budget=$93 standard_budget=$93 min_pos=$100
```

Book NAV that bar: **$6,421** (`portfolio_total=$6421` in the Budget split line).

| step | value | as % of NAV |
|---|---|---|
| cash reaching the drain | $770 | 12.0% |
| after the hard-coded 50/50 split | $385 | 6.0% |
| GLUE takes `available*0.5` | $193 | 3.0% |
| SNDK sees `available=$192`, `max(100, 96)` -> **the floor wins** | **$100** | **1.6%** |
| pool left | $93 | — |
| names then refused `ALLOC=0` | 13 | incl. MDB 1.800, NUVB 1.800, BRKR 1.727, BKR 1.720, GH 1.700 |

$770 was spent on four names — two of them at the broker minimum — and then
thirteen names scoring 1.7-1.8 were told there was no money. The residual is
not small because the account is small; it is small **because the names ahead
of it were each handed half of it.**

I reproduced this arithmetic exactly, offline, by calling the shipped
`_plan_backfill_buy_allocation` in queue order and decrementing the pools the
way the drain loop does — `$192 / $100 / $192 / $100`, pools left `$92 / $92`
(the log prints `$93` from `f"{92.5:.0f}"`). Same for four other bars in §4.

### And "HIGH-CONV" in that log line is not a conviction measure

`is_high_conviction = current>=1.5 or queued>=1.5 or is_priority_entry`
(gna:10104). `is_priority_entry` is true for **any** propagation-expansion or
watchlist name, at any score. That is why the corpus contains
`BUY: OI (alloc=$100, score=0.000 HIGH-CONV)`,
`NVDA (alloc=$100, score=0.027 HIGH-CONV)`,
`TERN (alloc=$100, score=0.000 HIGH-CONV)`,
`NUVB (alloc=$188, score=0.000 HIGH-CONV)`. The tag is a **budget-bucket
name**, not a score. My gate does not reuse it (see §5).

---

## 2. Generalization — 17 backtests, 3 regimes

Every `Backfill queue BUY:` line in `backtests/*.log`, deduped to one file per
run id. "conv" = live score >= 1.5.

| run | BFQ buys | conv drains | **at exactly $100** | ALLOC=0 events |
|---|---|---|---|---|
| 201039 | 33 | 9 | **9** | 161 |
| 915207 | 24 | 8 | 3 | 101 |
| 613166 | 31 | 7 | **6** | 214 |
| 249191 | 47 | 6 | **6** | 328 |
| 383778 (OOS bull) | 37 | 6 | **5** | 30 |
| 455506 | 22 | 6 | **5** | 288 |
| 806490 | 22 | 6 | **6** | 270 |
| 264179 | 28 | 5 | 4 | 235 |
| 427197 | 27 | 5 | 2 | 129 |
| 498816 | 18 | 5 | **5** | 373 |
| 820236 | 26 | 4 | 3 | 193 |
| 823150 | 15 | 3 | **3** | 93 |
| 983687 | 7 | 3 | 0 | 852 |
| 357345 | 2 | 2 | 0 | 1786 |
| 571147 | 14 | 2 | 1 | 108 |
| 725146 | 22 | 1 | 0 | 111 |
| 931112 | 6 | 1 | **1** | 9 |
| **total** | **381** | **79** | **59 (75%)** | **5,281** |

Modal allocation over ALL 720 `Backfill queue BUY` lines in the corpus is
`$100`. The whole distribution of conviction-drain sizes is
`{100 x59, 107, 108, 138, 150, 158, 170, 172, 185, 188, 191, 196, 306, 418,
613, 618, 976}` — i.e. either the broker minimum or half of a residual.

Bear windows do not show it (`542754`: 0 BFQ buys — the queue never saturates
in a bear, same as `sndk-priority-block.md` §6 found for admission). **This is
a bull/chop failure, which is the regime the objective targets.**

Only **one** conviction drain in the entire corpus exceeded a 14%-of-NAV clip
(AVGO $976 in the 2026-05 run 357345), so the ceiling half of the fix (§5) is
essentially never binding on this book size.

---

## 3. HONEST CORRECTION to `_RUNS4.md` defect (B)

`_RUNS4.md` reads: *"BFQ FUNDS A HIGH-CONV NAME AT A FLAT $100 ... -> FILL BUY
SNDK 0.2427 @ $414.69 = $100.67 = 1.7% of NAV, against a 14% ($840) clip."*

The two numbers are not causally linked. Same bar, in order
(`571147_audit.log:12744 -> 13120`):

```
Backfill queue BUY: SNDK (queued 6 bars, alloc=$100, score=1.700 HIGH-CONV)
V31.2 total-spend cap [CONCENTRATE]: funded 3 of 4 by conviction
    (SNDK@$899, BTC@$899, GLUE@$899) out of $4,045
[core] funding request trimmed $2,697 -> $674
SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $674 out of the core
SATELLITE CAP: SNDK trimmed $899 -> $674 to keep the core at target
TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy
Buy gate inputs for SNDK: cash=$714.66 ... cash_to_use=$673.92 -> PASS
[execution] FILL BUY SPY  qty=0.83919453 price=693.44   = $581.94
[execution] FILL BUY SNDK qty=0.24274209 price=414.69   = $100.67
```

The CONCENTRATE allocator runs **after** the drain and does
`_cc_want = max(_cc_cash, portfolio_total * total_spend_cap_target_weight_pct)`
(gna:32243), so it overwrites the queue's `$100` with the 14% target `$899`
before the broker ever sees it. Confirmed as load-bearing on another bar of
the same run: `BUY: AMAT (alloc=$108)` -> `CONCENTRATE ... AMAT@$848` ->
`FILL BUY AMAT qty=3.004 @ $281.51 = $845.72`.

So SNDK's `$100.67` is the **cash race**: an approved $673.92 met a same-tick
SPY core deploy that took $581.94 of the $714.66 on hand. That is
`_SYNTHESIS.md` root cause #1 and it is not in my file.

**What this fix therefore does and does not do:**

* With `total_spend_cap_concentrate=True` (571147, 820236, 915207, 613166,
  383778 all have it on) the *size* the queue writes is largely masked. What is
  **not** masked is **which names get a non-zero allocation at all**: a name
  with `ALLOC=0` is never written into `nexus_position_sizes`, so CONCENTRATE
  never sees it. 5,281 ALLOC=0 events across the corpus are names the
  concentrate allocator was never offered.
* With concentrate OFF (its own default is `False`) the queue's number **is**
  the position size, and 75% of conviction drains are the broker minimum.
* Second-order and real either way: each funded runt is upsized by CONCENTRATE
  into a full 14% slot the sleeve cannot pay for. On the same 2026-01-16 bar
  that produced `SATELLITE CAP: BTC skipped — satellite at its design share
  ($-931 room)` and the same for GLUE. Four names funded out of a $770 pool is
  what put the sleeve $931 over.

I am stating this plainly per the brief: **the lever is verified in arithmetic
against five real bars, but it is NOT verified end-to-end in a live run,
because I was told not to start one.** The log signature to grep for is in §6.

---

## 4. The fix

`_plan_backfill_buy_allocation` gains `portfolio_total` (keyword, default
`0.0`) and one branch before the unchanged final expression:

```python
_tw_pct = float((config or {}).get("bfq_conviction_target_weight_pct", 0.0) or 0.0)
if _tw_pct > 0.0 and float(portfolio_total or 0.0) > 0.0:
    _tw_min_score = float((config or {}).get(
        "bfq_conviction_target_min_score",
        (config or {}).get("nexus_high_conviction_threshold", 1.5) or 1.5) or 1.5)
    if float(current_score or 0.0) >= _tw_min_score:
        _tw_target = float(portfolio_total) * _tw_pct
        return max(0.0, min(available, max(min_required, _tw_target))), budget_key
allocation = min(available, max(min_required, available * 0.5))   # unchanged
```

i.e. `available * 0.5` (a fraction of a residual) becomes `pct * NAV`
(a share of the book), **bounded by the pool that actually exists** and by the
same `min_required` floor as before. The drain call site passes
`portfolio_total`, applies `_clip_to_single_position_cap` (the
`single_position_max_pct` cap every other buy lane already respects, and which
the sibling direct-reserved drain 170 lines up already calls), and logs.

**Two deliberate design choices.**

1. **The gate reads the LIVE score, not the queued one.** The drain order is
   keyed on the stale queued score, so a decayed name sits ahead of a live one:
   bt 915207 2026-01-20 funded `NUVB (alloc=$188, score=0.000 HIGH-CONV)`
   ahead of `RVMD (alloc=$100, score=1.700)`. Gating on `max(live, queued)`
   would hand NUVB the whole priority pool and starve both 1.700s. The live
   score is also the number CONCENTRATE ranks on downstream (the drain writes
   `raw_net_score = _bfq_current_score`), so this keeps the two lanes
   consistent.
2. **I did not raise `priority_min_position_size`.** It is the FLOOR. Raising
   it converts funded runts into `ALLOC=0` and changes nothing about the fact
   that the size is a fraction of a residual. It also feeds `min_required` on
   both the priority and standard paths, so raising it would move every
   non-conviction drain too.

### Measured effect, offline, on five real bars

Replayed by calling the shipped/fixed function in queue order and decrementing
the pools exactly as `run_once` does. `OFF` reproduces the shipped logs to the
dollar on all five.

| run / bar | pool | name | live | **OFF (= shipped log)** | **ON (pct=0.14)** |
|---|---|---|---|---|---|
| 571147 2026-01-16 | $770 | GLUE | 1.000 | $193 | $193 (unchanged) |
| | | **SNDK** | **1.700** | **$100** | **$193** |
| | | BTC | 1.300 | $193 | $193 |
| | | CMPX | 0.000 | $100 | $100 |
| 915207 2026-01-09 | $741 | **SNDK** | **1.700** | **$185** | **$370** |
| | | SBLK | 1.300 | $100 | $185 |
| | | UBER | 1.300 | $185 | $100 |
| | | RVLV | 1.300 | $100 | ALLOC=0 (stays queued) |
| 915207 2026-01-20 | $751 | NUVB | 0.000 | $188 | $188 (unchanged) |
| | | **RVMD** | **1.700** | **$100** | **$188** |
| | | **SNDK** | **1.700** | **$188** | **$376** |
| | | BRKR | 0.000 | $100 | ALLOC=0 |
| 820236 2026-01-13 | $252 | **LLY** | **1.706** | **$100** | **$126** |
| | | **SNDK** | **1.700** | **$100** | **$126** |
| 383778 OOS 2026-01-21 | $241 | TERN | 0.000 | $100 | $100 (unchanged) |
| | | **INTC** | **1.502** | **$100** | **$120** |

Three windows, three configs, same direction every time: the conviction name
takes what is left in its pool up to the target weight, the decayed and
below-threshold names are untouched, and the tail runts are left queued
instead of being funded at the minimum. 820236 is the honest case — the pool
is $252, so the target weight is unreachable and the fix pays $126. **Bounded
by the budget that actually exists** is not a slogan; it binds.

---

## 5. Behaviour envelope

`min(available, max(min_required, pct*NAV))` is a **floor for starved pools and
a ceiling for fat ones**:

* starved: `available=$190` -> shipped pays `$100`, fix pays `$190`.
* fat: `available=$4,562` on a $6,000 book -> shipped pays **$2,281 = 38% of
  NAV**; fix pays `$840`. The old rule had no upper bound at all. Only one
  drain in the whole corpus was in that regime (AVGO $976), so this side is
  near-inert today, but it is the correct shape and it is tested.

Guarantees, all covered by tests:

* key absent -> the identical shipped expression (proved differentially on a
  4,000-case random grid against a verbatim copy of the pre-fix body, plus
  40,000 cases in scratch).
* key present, live score below the bar -> identical to shipped.
* `portfolio_total` unresolvable (0) -> identical to shipped.
* never exceeds the pool; never below `min_required`; `headroom<=0` still
  refuses; a `single_position_max_pct` clip that lands under the floor refuses
  and leaves the item queued rather than funding a slot-eating crumb.

---

## 6. LOG SIGNATURE — how to tell it ran

Grep for `BFQ TARGET-WEIGHT`. One line per conviction drain, carrying the
counterfactual so it cannot be mistaken for a fat pool:

```
BFQ TARGET-WEIGHT: SNDK score=1.700 >= 1.50 — sized $193 (3.0% of $6421,
    target 14%) from the priority pool $192; the half-a-residual rule would
    have paid $100
```

If the key is set and this line does **not** appear, the lever is inert —
either no drain cleared the score bar, or `portfolio_total` was 0. If it
appears with `sized == would have paid`, the pool was the binding constraint,
not the rule.

Suggested first run: `bfq_conviction_target_weight_pct: 0.14` (match
`total_spend_cap_target_weight_pct`), everything else unchanged.

---

## 7. NOT FIXED HERE (flagged, evidence attached, other owners)

1. **Drain order is keyed on the stale queued score** (`_bfq.sort(_queue_sort_key)`,
   gna:31418). GLUE at live 1.000 drained ahead of SNDK at live 1.700 on
   571147 2026-01-16 and took half the priority pool first; NUVB at live 0.000
   drained ahead of RVMD at 1.700 on 915207 2026-01-20. Sorting conviction-
   grade entries by live score would give SNDK $385 instead of $193 on that
   bar. I did not do it: it changes *which* names are funded, and I cannot
   validate an ordering change without a run.
2. **The hard-coded 50/50 priority/standard split** (gna:31513) caps a single
   conviction name at 6% of NAV even when 12% is sitting in the drain. Merging
   the pools is only safe *after* (1), otherwise the first stale-scored name
   eats everything.
3. **`_bfq_cash` double-counts the reserve.** `_bfq_leftover` is computed from
   `_available_buy_budget` (the TOTAL), which already contains
   `_reserved_backfill_budget`, and then the reserve is added again
   (gna:31388-31390). When the main slate spends exactly its primary budget the
   drain believes it has 2x the reserve. Not my defect; flagging it.
4. **The cash race** (§3) — `_SYNTHESIS.md` #1, owned elsewhere. Until it is
   fixed, a larger BFQ allocation converts to a larger *approval*, not
   necessarily a larger fill.

## 8. Suite status

`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider backend/tests
  --ignore=backend/tests/test_core_sleeve_adversarial.py
  --ignore=backend/tests/test_adv_exit_discipline_findings.py
  --ignore=backend/tests/test_zz_adversarial_sweep.py`

-> **4755 passed, 13 skipped, 90 warnings in 122s. GREEN.**
(Working tree also carried a sibling agent's peak-give-back edits to the same
file and to `core_sleeve.py`; the run above includes them.)

New file `backend/tests/test_bfq_conviction_target_weight.py`, 26 tests. With
the fix reverted (`git stash` of the strategy file only) **all 26 fail**.
