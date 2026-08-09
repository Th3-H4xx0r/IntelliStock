# SNDK `full_priority_blocked` — the priority-slot rule, and why 1.700 loses

Run: bt **427197** (2026-01-01..03-01, v2-let-run-core, $6,000, status=running @70.1%).
Comparison: 915207 (+9.70%), 542754 (bear, +11.94%), 383778 (OOS bull, +4.75%).
Log: `backtests/427197_inv.log` (32,518 lines), `backtests/915207_inv.log`.
READ-ONLY investigation. No edits made.

---

## 1. WHERE IT IS EMITTED

`full_priority_blocked` is returned from exactly **one** place:

    graph_nexus_analysis.py:11479   return "full_priority_blocked", None

inside `_enqueue_backfill_candidate()` (gna:11403-11479). It is logged at
gna:31237 (candidate lane) and gna:31309 (broker-skipped lane), and it is
re-surfaced as `NEXUS_PRIORITY_BLOCKED` / `deferred_unfunded_buy` at gna:32404-32433.

---

## 2. THE EXACT RULE

Call site (gna:31207-31222), effective params this run (`Effective config`,
`bfq=10%/1g/15pg q=60/20 displace=0.3/rec=0.1`):

    max_size = backfill_queue_max_size            = 60
    reserved_priority_slots                        = 20
    min_displacement_delta                         = 0.30
    recurrence_bonus                               = 0.10

Priority class (`_queue_priority_rank`, gna:10007-10021):

    rank 0 : is_watchlist_priority / signal_source in {watchlist_priority, sector_watchlist}
    rank 1 : signal_source == propagation_expansion            <- NO score bar, NO cap
    rank 1 : signal_source == direct AND raw_net_score >= 1.0   (gna:10017, "HM fix")
    rank 2 : direct, score < 1.0
    rank 3 : everything else

Sort key (`_queue_sort_key`, gna:10024-10031) = `(rank, -raw_net_score, -n_paths, ticker)`.

Admission, in order:

1. duplicate -> `updated` / `duplicate` (gna:11417-11434)
2. **`len(queue) < max_size` -> plain `append`, NOTHING is evicted** (gna:11436-11439)
3. queue full, entrant rank<=1, `len(priority_entries) < reserved_priority_slots`
   AND `general_entries` non-empty -> `priority_evicted_general` (gna:11448-11455)
4. queue full, entrant rank<=1, otherwise (gna:11467-11479):
   `candidate_pool = priority_entries`; `victim = worst(candidate_pool)`.
   Admit only if **BOTH**
     a. `_queue_sort_key(new) < _queue_sort_key(victim)`, and
     b. `_can_displace` (gna:11457-11465):
        `victim_score <= 1.0`  (weak victim, free)  **OR**
        `new_score >= victim_score + 0.30 + 0.10 * victim_recurrence_count`
   else -> **`full_priority_blocked`**

### What the three caps actually do

| key | value | what it really is |
|---|---|---|
| `backfill_queue_reserved_priority_slots` | 20 | **a FLOOR for step 3 and a CEILING on GENERAL** (`general_capacity = 60-20 = 40`, gna:11481). It never caps the priority class. |
| `watchlist_priority_slots` | 2 | **different subsystem entirely.** Read at gna:28584/28598 to reorder `stock_buys_scored` for *sizing*. It never reaches `_enqueue_backfill_candidate`. |
| `propagation_expansion_reserved_slots` | 10 | also sizing-order only (gna:28586, 28602). Does **not** cap how many propagation names get queue rank 1. |

**Nothing caps the priority class.** At the first SNDK block the 60-deep queue held
**51 propagation_expansion + 12 direct rank<=1 entries; only 2 general entries.**
Step 3 is therefore permanently dead (63 >= 20), and general slots are unreachable.

---

## 3. WHY score=1.700 direct LOSES — measured to the decimal

Bar 2026-01-13, log lines 9640-9682 (`backtests/427197_inv.log`):

    Propagation expansion buys (promoted): AAPL, ALL, MET, MINO, NVO, RGA, SN, UNM,
                                           VKTX, TXN, BTC, VLO, RRC        <- 13 in ONE bar
    Backfill queue: enabled | stock_buys=22 | queue_size=60 | halt=NO
    Backfill queue REPLACE: MET displaced BKR (score=1.800, source=propagation_expansion)
    Backfill queue REPLACE: SN  displaced ABT (score=1.800, source=propagation_expansion)
    Backfill queue REPLACE: UNM displaced AMD (score=1.800, source=propagation_expansion)
    Backfill queue BLOCKED: AAPL (full_priority_blocked, score=1.750, ...)
    Backfill queue BLOCKED: ALL  (full_priority_blocked, score=1.750, ...)
    Backfill queue BLOCKED: RGA  (full_priority_blocked, score=1.750, ...)
    Backfill queue BLOCKED: NVO  (full_priority_blocked, score=1.706, ...)
    Backfill queue REFRESH: VLO  (score=1.500, recurrence=2, source=propagation_expansion)
    Backfill queue BLOCKED: MINO (full_priority_blocked, score=1.200, ...)
    Backfill queue BLOCKED: VKTX (full_priority_blocked, score=1.200, ...)
    Backfill queue BLOCKED: RRC  (full_priority_blocked, score=1.080, ...)
    Backfill queue BLOCKED: RVMD (full_priority_blocked, score=1.700, source=direct)
    Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700, source=direct)

The three victims were **BKR / ABT / AMD, all at raw_net_score = 1.000**
(`Backfill queue ADD (broker-skipped): BKR (score=1.000...)` line 7756;
`ADD: ABT (score=1.000...)` line 7754; `ADD (broker-skipped): AMD (score=1.000...)` line 6762).
`vs <= 1.0` -> the free-victim exemption at gna:11460-11461. Those were the only three
displaceable-for-free entries in the queue.

They were consumed **earlier in the same bar** by propagation names, because
`_bfq_all_candidates` is sorted at **gna:31178-31186** with a key that puts
`is_propagation_expansion` strictly ahead of `direct` — that local key is the OLD
4-tier key and does **not** carry the gna:10017 "HM fix". SNDK is processed 13th,
after 11 propagation names including three scoring 1.200 / 1.200 / 1.080.

After the three free victims are gone the worst remaining priority entry sits at
**1.500** (VLO refreshed at 1.500 on that very bar). The admission bar is therefore

    1.500 + 0.300 = **1.800**

Measured boundary that bar: **1.800 admitted, 1.750 / 1.750 / 1.750 / 1.706 / 1.700 / 1.700 blocked.**
SNDK misses by **0.100**.

Queue composition at that bar (reconstructed from ADD/REPLACE/REFRESH/BUY/EXPIRED events):
63 rank<=1 entries, **30 of them scoring above 1.700**, tail clustered at 1.450-1.500,
sources = 51 propagation_expansion / 12 direct.

Run-wide: **399 `full_priority_blocked` occurrences** across 487 bar-runs; 213 BLOCKED +
95 FORCE-BLOCKED log lines. Per-bar max blocked score is **1.80 on 14 of 22 saturated bars**
— i.e. once the queue saturates on 2026-01-13 it stays shut for the rest of the run.
SNDK blocked 7x, BTC 7x, RVMD 6x, TXN 6x, WDC 5x, PSX 5x.

---

## 4. THE ACTUAL ROOT CAUSE IS UPSTREAM — SNDK WAS NEVER OFFERED WHILE SLOTS WERE FREE

This is the finding that matters. The queue was **not** full when SNDK needed it.

**2026-01-09** (log 7745-7770) — queue went **39 -> 49 of 60. Eleven free slots.**

    Backfill queue: enabled | stock_buys=18 | queue_size=39 | halt=NO
    ADD: ARES 1.800 | BBAI 1.800 | BRKR 1.727 | TMO 1.650 | DCI 1.565 | VLO 1.250
    ADD: GH 1.800 | ABT 1.000 | ADD(broker-skipped): BKR 1.000 | AAL 0.985
    Backfill queue: 49 pending (SON, BKNG, TTWO, ARES, BBAI...)
    Deferred unfunded buys demoted to hold: GLUE, SNDK          <- SNDK, raw=1.700

**2026-01-12** (log 8700-8735) — queue went **49 -> 60 of 60. Eleven free slots.**

    ADD: AKAM 1.800 | NUVB 1.800 | VVX 1.800 | EBS 1.770 | APPN 1.750 | O 1.750
    ADD: MDLN 1.696 | APH 1.658 | RUM 1.621 | KNSA 1.500 | WGS 1.454 | REPLACE: WELL 1.300
    Backfill queue: 60 pending
    Deferred unfunded buys demoted to hold: AIFD, GLUE, SNDK    <- SNDK, raw=1.700

On both bars SNDK produced **no ADD line, no BLOCKED line at all** — it was never
handed to `_enqueue_backfill_candidate`. Reason (verified from the logs):

    2026-01-09  Buy: ABT..., BTC..., BITO...          -> SNDK ABSENT
    2026-01-12  Buy: BTC..., BITO..., AKAM...         -> SNDK ABSENT
    2026-01-13  Buy: ABT..., SNDK (Direct momentum_breakout ...)   -> SNDK PRESENT

On 01-09 and 01-12 SNDK entered **only** through the momentum-watchlist lane
(`Momentum watchlist: ... top3=[('SNDK', 0.78/0.951), ...], new_buys=['GLUE','SNDK']`
lines 7731 / 8693). That lane writes SNDK's score and inserts the ticker at

    gna:28839   scores[_mp_ticker]["raw_net_score"] = _mp["raw_net_score"]
    gna:28852   _new_stock_candidates.insert(0, _mp_ticker)

but the backfill-queue candidate set is collected **650 lines earlier**:

    gna:28199   _queue_stock_buys = _collect_backfill_queue_candidates(
                    _queue_buy_candidates, stock_buys, etf_set, scores, _prop_expansion_buys, ...)
    gna:31133   _bfq_candidate_syms = list(_queue_stock_buys)

**Reader gna:28199 runs before writer gna:28839/28852.** So a name that enters via the
momentum lane can never be offered to the queue on that bar. SNDK only reaches the queue
on bars where it independently happens to fire as a native `Direct momentum_breakout`
buy — which first happened on 2026-01-13, by which time the queue was 60/60 and shut.

This is the **same reader/writer ordering class** already documented in `_SYNTHESIS.md`
(rank_band reader gna:23016 vs writer gna:28622) — a third instance of it.

### Proof this is not just this run's bad luck

bt **915207** (same window, `watchlist_priority_slots=0`): SNDK fired as a native
Direct buy on 01-09, so `_collect_backfill_queue_candidates` saw it, and:

    06:15:27  Backfill queue ADD: SNDK (score=1.700, price=$333.19, source=direct)
    06:15:28  [BROKER] SNDK @ 2026-01-09 ($363.01): buy action_intent=backfill_queue_buy
    06:16:42  Backfill queue REPLACE: SNDK displaced LLY (score=1.700, source=direct)
    ... 4 backfill_queue_buy + 2 momentum_watchlist_buy fills, blended entry $510.41
    07:17:49  Monitor decision: SNDK day 30 pnl=+23.7% cp=$631.54 entry=$510.41 -> HOLD

Same code, same score, same name — bought in one run and starved in the other, purely
on whether the *direct* signal happened to fire on a bar with free queue slots.
In 427197 SNDK was bought **zero** times.

---

## 5. VERDICT ON `watchlist_priority_slots` 0 -> 2 (this run's change)

**IT DID NOTHING. Not better, not worse — completely inert.**

Evidence:
* `Priority sizing order: watchlist=none | prop_exp=...` — **30 of 30** occurrences say
  `watchlist=none` (0 exceptions).
* `Effective config ... discover=watch:0/0` — `sector_watchlist = {}`,
  `sector_watchlist_reserved_slots = 0`. Nothing is ever tagged `is_watchlist_priority`
  from the watchlist path.
* **Zero rank-0 entries** in the reconstructed 60-deep queue at any point.
* Structural: the only writer that could tag a momentum name is gna:28857
  (`_active_watchlist_priority_tickers.add(_mp_ticker)`), which runs **259 lines after**
  the reader at gna:28598. And even if it fired, gna:28584-28614 only reorders
  `stock_buys_scored` for sizing — it is never read by `_enqueue_backfill_candidate`.
* In 427197 SNDK is `V32 mw_buy extension-block`ed **11 times** (vs 5 in 915207), so it
  rarely even reaches gna:28857.

The 427197 vs 915207 P&L difference is therefore **not** attributable to this lever.
The other change in the same run (`core_min_pct 0.25 -> 0.10`) is unmeasured by this
investigation.

---

## 6. THE SMALLEST CHANGE THAT LETS THE #1 MOMENTUM NAME CLAIM A SLOT

### Primary fix — one line, inside the enqueue block, zero upstream reordering

At **gna:31133**, after `_bfq_candidate_syms = list(_queue_stock_buys) ...`, append the
momentum-lane buys that were injected at gna:28852 (`_momentum_new_buys` is defined at
gna:28636 and is in scope):

```python
_bfq_candidate_syms += [
    m["ticker"] for m in (_momentum_new_buys or [])
    if m.get("ticker") and m["ticker"] not in set(_bfq_candidate_syms)
]
```

The existing sort at gna:31178-31186 then places it, and the existing
`_bfq_score >= _bfq_min_score` gate at gna:31194 still applies. Nothing else changes.

### Proof it evicts nothing better

* `_enqueue_backfill_candidate` **only evicts when `len(queue) >= max_size`**
  (gna:11436-11439: `if len(queue) < max_size: queue.append(entry); return "added"`).
  On 2026-01-09 the queue was **39/60** and on 2026-01-12 **49/60**. SNDK takes a
  **free slot on both bars. Zero evictions. Zero displacement.**
* On those two bars the queue spent 22 free slots, and **10 of the 22 admits scored
  strictly below SNDK's 1.700**: MDLN 1.696, APH 1.658, RUM 1.621, KNSA 1.500,
  WGS 1.454, WELL 1.300, VLO 1.250, ABT 1.000, BKR 1.000, AAL 0.985. The queue was
  already handing free slots to worse names; this fix only stops excluding a better one.
* Once queued, SNDK sorts ~31st of 60 by `_queue_sort_key` and is protected for
  `backfill_queue_priority_grace_bars = 15` bars (`bfq=10%/1g/15pg`), which is longer
  than the 1-4 bar queue residency of every drained buy in this run.
* Independent confirmation from the drain side: the queue already funds names scoring
  **0.000 or negative** — `BUY: OI score=0.000`, `GH 0.000`, `MDB 0.000`, `NUVB 0.000`,
  `SCCO 0.000`, `TYRA 0.000`, `CAT -0.005`, `GFI 0.110`, `AMD 0.619`, `AAL 0.957`
  (10 of 27 backfill buys). A live 1.700 cannot be worse than these.

### Expected effect (dollars)

SNDK 2026-01-01 $237.33 -> window close ~$641 (+166%). Entry on 2026-01-09 at $363.01
(the price 915207 actually paid via `backfill_queue_buy`) held to $641 = **+76.6%**.
At the sizing engine's own conviction clip — the 427197 opening book is
`SLV/CPER/SBLK/TDY each $840 (14.0% of NAV)` — a single $840 SNDK position is

    840 x 0.766 = **+$643 = +10.7pp on a $6,000 account**

Upper bound if it were taken at bar-1 discovery ($237.33): +$1,429 = +23.8pp.
Lower bound at 915207's actual $185 alloc: +$142 = +2.4pp. This is *one* name; the same
gate also refused RVMD (1.700), WDC (1.700, 5 blocks), TXN, PSX on the same mechanism.

### GENERALIZABLE? YES — mechanism + 3 of 4 windows

* Mechanism is a fixed reader/writer ordering in the source (gna:28199 before
  gna:28839/28852), not a tuning artifact. It fires on **every** bar where a name enters
  via the momentum lane only.
* bt **427197** (chop/bull): 399 `full_priority_blocked`, 21 distinct tickers, SNDK 7x.
* bt **915207** (same window, prior config): **691** `full_priority_blocked` over 634
  bar-runs, SNDK 5x, TNC 20x, SBLK 20x, RGEN 12x.
* bt **383778** (OOS bull): 37 `full_priority_blocked` (VLO/TLX/CPER 3x each).
* bt **542754** (bear): **0** — the queue never saturates in a bear. This is a
  bull/chop-regime failure, which is precisely the regime the objective targets.

---

## 7. SECONDARY ITEMS (larger blast radius — flagged, not recommended as the first move)

1. **Cap the priority class.** `reserved_priority_slots` is a floor only; the priority
   class ate 63 of 65 queue entries and killed the `priority_evicted_general` branch
   (gna:11448) outright. Making it a ceiling (surplus rank<=1 entries sort as general)
   restores 40 general slots. Bigger change to a shared function.
2. **Fix the local sort at gna:31178-31186** to use `_queue_priority_rank` instead of the
   stale inline 4-tier key. Today propagation_expansion is processed ahead of *every*
   direct name regardless of score, so prop names at 1.080-1.200 get first refusal on
   free/weak victims ahead of direct names at 1.700. This is the gna:10017 "HM fix"
   applied to admission but never to processing order.
3. `backfill_queue_min_displacement_delta` 0.30 -> 0.15 would drop the 2026-01-13 bar from
   1.800 to 1.650 and admit SNDK — but it **does** evict (the 1.45-1.50 tail) and it does
   **not** help on 01-09/01-12 where SNDK is never offered. Strictly inferior to the
   primary fix.

## 8. NOT ADDRESSED HERE

Even queued and drained, SNDK's entry in 915207 blended to $510.41 — 96% of the way
through the move, the documented entry-timing leak (`_SYNTHESIS.md` "THE PRIZE",
`gna` V32 mw_buy extension-block fired 11x on SNDK in 427197). Getting the slot is
necessary, not sufficient.
