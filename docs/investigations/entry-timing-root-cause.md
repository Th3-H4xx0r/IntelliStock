# Entry timing is the binding constraint — and it explains the reserve's null result

Date: 2026-08-13
Run: bt **873929**, W0 control (reserve=0), 2026-01-01..2026-03-01, $6,000, 3600s. 39,748 log lines.
Method: reconstructed every quoted price and every BUY fill from the log. No new run.

## Conversion rate: 6.8%

In the reference window **103 names** traded a >=30% range. **7 were bought.**

That is the objective's claim, measured: discovery is not the gap.

## Entry lateness, per bought name

| symbol | in-window move | signal date/px | fill date/px | lag | price rise before fill | entered at | captured |
|---|---:|---|---|---:|---:|---:|---:|
| AGQ | +169.7% | 01-01 $155.1 | 01-02 $168.4 | 1d | +8.6% | 5.1% through | **94.9%** |
| ARIS | +42.0% | 01-01 $16.2 | 01-02 $16.1 | 1d | -0.7% | 0.5% through | **99.5%** |
| VAL | +31.9% | 02-09 $76.0 | 02-09 $79.5 | 0d | +4.7% | 14.6% through | 85.4% |
| TER | +65.3% | 02-04 $282.2 | 02-04 $273.7 | 0d | -3.0% | 51.0% through | 49.0% |
| HYMC | +140.6% | 01-09 $26.8 | 02-02 $37.0 | **24d** | **+38.0%** | 40.9% through | 59.1% |
| MOVE | +42.6% | 02-05 $9.5 | 02-05 $9.9 | 0d | +5.1% | 68.8% through | 31.2% |
| **SNDK** | **+187.9%** | **01-19 $413.6** | **02-02 $660.5** | **14d** | **+59.7%** | **94.9% through** | **5.1%** |

`SNDK` reproduces the objective's "entered 96.7% through its move" almost exactly: signalled at
$413.60, filled 14 days later at $660.48, capturing **5.1%** of the move it was found for.

When the system enters on time it works: `AGQ` and `ARIS` filled the next bar and captured
94.9% and 99.5%. The machinery is not broken — the *queueing* is.

## Why it was late: cash, not the satellite cap

21 `SNDK` refusals appear in the control. The binding reasons are explicit:

```
TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 91% budget
SKIP BUY SNDK — cash_to_use $27.20 < min $410 (allocated $955.76)
Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700, source=direct)
SKIP BUY SNDK — fundable $86.56 of cash_to_use $949.96
                (orders already in flight this tick reserve the rest) < min $420
```

Three things are now ruled out as the binder:

* **turnover** — the conviction bypass fired and *admitted* the buy;
* **the ranking score** — raw +1.700, comfortably over the 1.50 threshold;
* **the allocator** — it sized the position correctly at $955.76.

What actually refused it: **there was $147.20 of cash and $27.20 fundable.** The book was fully
deployed and the cash had already been reserved by orders earlier in the same tick.

## The execution order is alphabetical, not by conviction

```
Execution order: 1 sell(s) first, then 73 buy/hold candidate(s)
                 by (intent_priority, allocation, ticker)
```

The final tiebreaker is **ticker**. On the tick where `SNDK` was starved, `HYMC` was evaluated
immediately before it and hit the identical `$27.20` wall — both had `high_conv=True`. Whichever
names sort earlier consume the cash; conviction is not the ordering key among equals.

## Why this makes the conviction-reserve result coherent

The four-window pair rejected `satellite_conviction_reserve_pct=0.15`
(`docs/investigations/result-conviction-reserve-pair.md`). This explains why.

The reserve widened the satellite *band*. It did not create **cash**. In the treatment run
`SNDK` was indeed funded $1,018 instead of $29 — but the fill still happened on **2026-02-02 at
$660.48**, the same late bar, 94.9% through the move.

**Fixing size without fixing timing buys more of an exhausted move.** That is precisely the
observed outcome: bigger positions, near-identical returns, one material loss in the bear.

Blockers (1) entry timing and (3) trim-back are therefore the **same** root cause: the book is
fully invested, and there is no mechanism to free cash by trimming a weaker holding when a
better name appears.

## Candidate next test, preregistered before running

A conviction-ranked funding order plus displacement, **default-OFF**:

1. order the buy queue among high-conviction candidates by raw score, not ticker;
2. when a candidate clears the overflow threshold and cash is short, free room by trimming the
   **lowest-conviction existing satellite holding**, rather than skipping the candidate.

This is a satellite-internal swap, so it should not raise gross turnover materially — but
turnover must be measured, not assumed, and a rise is disqualifying.

Acceptance, fixed in advance:
* median signal-to-fill lag for high-conviction names falls below 2 days;
* at least one >=100% mover enters below 50% through its move;
* four windows, same protocol; the bear window remains a safety veto;
* +/-4.94pp remains the noise floor.

Explicitly **not** in the measured do-not-retry list: this is neither loosening the
entry-extension gate, nor a turnover exemption, nor raising `max_positions`.

## Caveats

`pit_mode=research` (lookahead). Prices above are first/last quoted values inside the run, not
realised P&L. One window. Nothing here is promotion-eligible or justifies real money.
