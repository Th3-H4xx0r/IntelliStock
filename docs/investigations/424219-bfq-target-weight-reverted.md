# 424219 — `bfq_conviction_target_weight_pct` REVERTED: the pool is the wall, not the sizing rule

Run: bt **424219**, reference window `2026-01-01..2026-03-01`, v2-let-run-core, $6,000, 3600s,
cold state, with `bfq_conviction_target_weight_pct=0.14`.
Control: bt **571147**, same window, same instance, key absent. **Reverted after this run.**

---

## 1. THE PRE-DECLARED RULE, APPLIED

`sndk-100-dollars.md` §4 wrote the pass/fail table before the run. Two of its four rows fired.

**Row 1 — "line present ⇒ the lever bound":** it fired, verbatim.

```
BFQ TARGET-WEIGHT: BKR  score=1.770 >= 1.50 — sized $155 (2.5% of $6103, target 14%)
                   from the priority pool $155; the half-a-residual rule would have paid $100
BFQ TARGET-WEIGHT: TTWO score=1.800 >= 1.50 — sized $184 (3.0% of $6143, target 14%)
                   from the priority pool $184; the half-a-residual rule would have paid $100
BFQ TARGET-WEIGHT: SNDK score=1.700 >= 1.50 — sized $233 (3.8% of $6167, target 14%)
                   from the priority pool $233; the half-a-residual rule would have paid $116
```

**Row 4 — "return < +17.36% − 4.94pp ⇒ revert":** `+4.10%` vs `+17.36%`. **−13.26pp, 2.7x the
noise floor.** Reverted.

---

## 2. WHY IT COULD NEVER HAVE WORKED

Read the log line again. Every one of the three asks for **14%** and every one is capped at **the
pool**:

| name | target 14% | pool | sized | outcome |
|---|---|---|---|---|
| BKR | $854 | **$155** | $155 (2.5%) | pool-bound |
| TTWO | $860 | **$184** | $184 (3.0%) | pool-bound |
| SNDK | $863 | **$233** | $233 (3.8%) | pool-bound |

The lever changes the *formula*. It cannot change the fact that

```
    BFQ priority pool  $155-$233
    conviction clip    $840-$863
    the pool is 3-6x too small to fund ONE position
```

This is exactly what `sndk-100-dollars.md` §2 identified and then failed to draw the right
conclusion from: it treated the half-a-residual rule as the bug when the rule was only the
*symptom*. Doubling a number that is 3-6x too small produces a number that is still 2-3x too
small.

---

## 3. AND THE REAL WALL IS FURTHER UP

Following SNDK through the same bar shows the queue was never the binding constraint:

```
V31.2 total-spend cap [CONCENTRATE]: funded 3 of 4 by conviction (GLUE@$863, SNDK@$863, TPG@$863)
SATELLITE CAP: SNDK trimmed $863 -> $235 to keep the core at target
Buy gate inputs for SNDK: ... open_pos=6 ... cash_to_use=$233.13 -> PASS
SKIP BUY SNDK — cash_to_use $233.13 < min $370 (allocated $234.60)
```

The conviction lane sized SNDK **correctly, at $863**. The **satellite cap** cut it to $235. The
**min-position floor** ($370 = 6% of NAV) then refused $235 outright. SNDK was not bought on that
bar at all.

`open_pos=6` against `max_positions=6`. And the book was already full:

```
01-02  ETH $840, PANW $831, SNDR $840, SON $838   + SPY core $2,398 (40% of NAV)
01-06  AMCR $862, AMD $831
```

**Six names and 100% of the capital, committed in three sessions of a thirty-nine-session window.**
By the time SNDK's signal arrived there was no slot and no money — the core had already been drawn
down to ~14% of NAV, so the overflow band had $235 in it, and $235 is below the floor that stops
runts.

---

## 4. WHAT THIS ACTUALLY ESTABLISHES

1. **`bfq_conviction_target_weight_pct` is not the fix and is now off doc-193.** Its own log line
   proves it is pool-bound in every instance.
2. **The runt chain is: conviction sizes right → satellite cap trims → min-position floor refuses.**
   Three gates, and the name ends with nothing. This is the same chain seen ten times in bt 584886
   (`satellite-capacity-584886.md`) and it is not a queue problem.
3. **The binding constraint is capacity, and capacity is spent in the first three sessions.**
   `max_positions=6`, all six taken by day 3, 100% of capital deployed, core at its floor. Nothing
   that arrives later can be bought at size, whatever the sizing rule says.
4. So the only structural lever left that does **not** require ranking names against each other —
   which `satellite-capacity-584886.md` measured as useless (`r = −0.235`) — is **staging the
   deployment**: do not commit 6/6 slots and 100% of capital in the first 8% of the window.

---

## 5. ATTRIBUTION LIMITS — READ BEFORE QUOTING THE −13.26pp

bt 424219 differs from bt 571147 by **three** things, not one: this key, the deployed
`_max_positions` UnboundLocalError fix (`b40d2d8`, which changes behaviour on every pure-hold bar),
and the armed fresh-low gate (inert here — this window has zero bear bars).

Book overlap with the control is **2 of 9 names** (SNDK, AMCR). And the fourteen recorded runs of
this window span **+1.72% to +17.36%**, so +4.10% is inside the historical spread.

**The revert is justified by §2 — the mechanism cannot deliver a 14% clip — not by the −13.26pp.**
The return is one sample from a distribution this window has already shown to be very wide.
