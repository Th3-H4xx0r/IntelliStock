# 789099 — the fresh-low gate is INERT in the real bear, and the hedge is intact

Run: bt **789099**, bear `2026-03-02..2026-03-30`, v2-let-run-core, $6,000, 3600s, cold state,
with the gate ARMED (`residual_sleeve_bear_block_at_fresh_low_bars=2`,
`regime_rally_onset_enabled=true`). This was a **safety check with its fail condition declared in
advance** (`2026-08-10-next-run-plan.md` STEP 3): *"Fail = any `bear leg SKIPPED — proxy at a
fresh 20d low` line in this log, or SQQQ P&L materially below +$900."*

## RESULT: PASS, ON BOTH CLAUSES

```
fresh-low blocks in this log ............ 0
rally-onset capacity releases ........... 0
first bear-leg park ..................... 2026-03-05  $3,216 of SQQQ @ 69.50, fill @ 70.80
SQQQ P&L ................................ +$918.78
```

**Zero.** The gate that fired 12 times in the OOS window fired **not once** here, exactly as the
offline replay predicted: 542754's first park sat at `since_20d_low = 18`, sixteen bars clear of
the N=2 threshold. And the leg opened on **the same bar at the same price** as the +$889 leg
gap-oos measured (`first park FILL $70.80 (03-05)`).

That is the property the whole design rests on — **the gate is selective, not a blanket
suppressor.**

| run | gate | SQQQ P&L | return | maxDD |
|---|---|---|---|---|
| 542754 | off | +$889.44 | +11.94% | 5.1% |
| 321638 | off | +$964.74 | +10.44% | 7.1% |
| **789099** | **on** | **+$918.78** | **+21.27%** | 7.0% |

SQQQ lands mid-range of the two control runs. The hedge is untouched.

## THE RETURN, HONESTLY

**+21.27% against SPY -7.86% is +29.1pp of alpha and the best bear result on record.** It is *not*
attributable to the gate, which never fired. It comes from the stock book:

```
789099   DBO +237, XOM +112                       stock book  +$349
321638   BDRY -158, PAVM -116, AAOI -37           stock book  -$311
542754   UHS  -93, BTC  -41                       stock book  -$134
```

A ~$500 swing on a $6,000 book is ~8pp — above the 4.94pp noise floor, but it is a **2-name book**
against 3-name and 2-name books with no overlap. `regime_rally_onset_enabled` is the only other
new key, and it released capacity **zero** times here, so it is not the explanation either. The
honest reading is name-selection variance in a book too small to average anything out.

## WHAT THIS RUN ALSO FOUND

`Run-once strategy 'graph_nexus_analysis' error: cannot access local variable '_max_positions'` —
**8 times**, each one abandoning the entire strategy invocation for that bar. Fixed in `b40d2d8`;
see that commit. Every bear result on record was measured with some fraction of its bars silently
skipped, because `max_positions_bear=2` makes most bear bars pure holds.

## THE CORE SAW-TOOTH IS WORST HERE

40 SPY fills, gross **3.72x NAV**, post-initial 3.33x — against 0.21x in the hedge-free OOS run.
Every bear-leg refill sells core and every band_deploy buys it back. This is the largest remaining
churn source in the book and it is concentrated in exactly one regime.
