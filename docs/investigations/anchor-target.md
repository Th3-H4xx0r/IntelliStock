# anchor-target — the winner-add lane cannot add, and it is the same arithmetic for the fourth time

Found in bt 571147 (+17.36%, the best run on record) — **for zero runs**, by grepping a log we
already had.

---

## 0. THE EVIDENCE

```
V31 anchor reinforcement budget: cap=$1007 (40% of stock_budget=$2518), candidates=0
V31 anchor reinforcement budget: cap=$186  (40% of stock_budget=$465),  candidates=4
V31 anchor reinforcement budget: cap=$178  (40% of stock_budget=$444),  candidates=6
...                                                                     (25 such bars)
```

**Twenty-five bars. Four to six qualified winners on nearly every one. Zero adds funded.**

The book on those bars held AMAT (+45%), SBLK (+37%), SLV (+32%), XOM (+27%) and SNDK (**+166%**).
The lane whose entire job is *"size so one winner matters"* watched all of them and never bought
a share.

Nothing in the log said so, because only the BUDGET was printed. Fixed in the same commit — see §4.

---

## 1. THE CAUSE: A TARGET SMALLER THAN THE ENTRY

```
    anchor_reinforce_target_pct        12    ->  target = 0.12 x NAV = $720
    total_spend_cap_target_weight_pct  0.14  ->  entry  = 0.14 x NAV = $840
```

`_plan_anchor_reinforcement` computes `additional_needed = max(0, target_total − current_value)`.
A position **entered at the clip is already worth more than its reinforcement target before it has
gained a cent**, and it only gets further above it as it wins:

| stage | fires at | position value | target | add |
|---|---|---|---|---|
| 1 | +15%, 7d | $966 | $720 | **$0** |
| 2 | +30%, 14d | $1,092 | $886 | **$0** |
| 3 | +50%, 21d | $1,260 | $1,108 | **$0** |

**The better the winner, the further out of reach its own target.** The lane is mathematically
incapable of firing, for any name, in any window.

## 2. THIS IS THE FOURTH INSTANCE OF ONE PATTERN

| # | lane | budget/target | one clip | outcome |
|---|---|---|---|---|
| 1 | conviction overflow band | $600 | $840 | fixed 2026-08-09; window +9.70% -> +17.36% |
| 2 | BFQ priority pool | $155-$385 | $840 | found + reverted 2026-08-10 (`424219-...md`) |
| 3 | satellite cap remainder | $235 | $370 floor | SNDK refused outright (`424219-...md` §3) |
| 4 | **anchor reinforcement** | **$720 target** | **$840 entry** | **this document** |

Every lane that is supposed to put size on a winner is calibrated below the size of one position.
**Any new sizing lane must be checked against `0.14 x NAV` before it is believed.**

## 3. THE FIX AND ITS BOUNDARY

`anchor_reinforce_target_pct` **12 -> 20**.

| target_pct | stage 1 | stage 2 | stage 3 | final weight |
|---|---|---|---|---|
| 12 (live) | $0 | $0 | $0 | 16-21% by drift only |
| **20** | **$234** | **$385** | **$586** | 20% / 24.6% / 30.8%* |

\* clipped by `single_position_max_pct = 25`.

**The boundary is not tuned.** The lane switches on where the target clears the position's value at
the stage-1 trigger (`0.14 x 1.15 = 16.1%` of NAV) plus `min_position_size` ($100 = 1.67%), i.e.
**~17.8%**. Tests assert `16 -> no`, `17 -> no`, `18 -> yes`. 20 is the first round number with
margin. (The real function corrected the paper arithmetic here: 17 looked fundable and is not.)

The averaging-up guard is untouched — a name 18% off its peak is still refused, which is what
stops an add at a top.

## 4. THE LOG SIGNATURE, ADDED FIRST

```
ANCHOR ADD: AMAT stage=1 +$234 (held 9d, pnl +18.2%, drop_from_peak 1.4%, entry $846, raw 1.800)
ANCHOR ADD: none funded from 6 candidate(s) on a $178 budget — check
            anchor_reinforce_target_pct against the entry clip
```

The second line is the one that would have caught this bug 25 times in a single run.

## 5. PASS / FAIL, DECLARED BEFORE THE RUN

Reference window `2026-01-01..2026-03-01`, cold, `anchor_reinforce_target_pct=20`.

| outcome | reading |
|---|---|
| `ANCHOR ADD: <SYM> stage=...` present | the lane is alive for the first time — this is the claim |
| only `ANCHOR ADD: none funded` | still blocked; the target is not the last constraint |
| adds fire and return >= +17.36% | the mechanism the objective describes, working |
| adds fire and return < +12.42% | negative beyond the noise floor; revert to 12 |

**Attribution warning, written in advance:** every A/B this session overlapped its control by 2 of
9-11 held names. A single run cannot resolve this lever's P&L. What it CAN resolve is whether the
lane fires at all, and at what size — which is deterministic, greppable, and the actual claim here.


---

## 6. RESULT — bt 633644, `anchor_reinforce_target_pct = 20`

Reference window `2026-01-01..2026-03-01`, cold. Control: bt 571147 (+17.36%).

### Row 1 of the pass/fail table fired: THE LANE IS ALIVE, FOR THE FIRST TIME

```
ANCHOR ADD: UUUU stage=1 +$241 (held  7d, pnl +24.4%, drop_from_peak 0.0%, entry $840, raw 1.200)
ANCHOR ADD: NVO  stage=1 +$175 (held 10d, pnl +15.2%, drop_from_peak 0.1%, entry $840, raw 1.250)
ANCHOR ADD: UUUU stage=2 +$211 (held 14d, pnl +38.7%, drop_from_peak 0.6%, entry $840, raw 1.250)
ANCHOR ADD: UUUU stage=3 +$319 (held 21d, pnl +51.7%, drop_from_peak 3.6%, entry $840, raw 1.250)
ANCHOR ADD: SNDK stage=2 +$207 (held 25d, pnl +32.0%, drop_from_peak 1.9%, entry $925, raw 1.250)
```

**Five funded adds, all three stages, exactly the arithmetic §3 predicted** ($241/$211/$319 against
predicted $234/$385/$586 — stage 2 and 3 are smaller because the budget bound them, see below).

UUUU was scaled through **all three stages** while running +24% -> +39% -> +52%, and finished the
run as the **top contributor, +$293.42**. SNDK — the name the objective names — took a stage-2 add
and returned **+$203.38**, against **+$52.64** in the control where it was a $101 runt.

That is the objective's mechanism, executing, for the first time in this repo's recorded history.

### Row 4 also fired: the return is below the revert threshold

| | 633644 (target 20) | 571147 (control) |
|---|---|---|
| return | **+5.61%** | +17.36% |
| vs SPY (+0.24%) | +5.37pp | +17.12pp |
| max DD | 12.9% | 10.6% |
| top contributor | UUUU +$293 | SBLK +$293 |

`+5.61% < +12.42%`. **Reverted to 12, as declared.**

### THE HONEST VERDICT: "NOT PROVEN", NOT "HARMFUL"

* Book overlap with the control is **2 of 8** (SNDK, AMCR) — the same re-randomisation every A/B
  this session hit. The fourteen recorded runs of this window span **+1.72% to +17.36%**, and
  +5.61% sits inside that.
* The three names that received adds netted **+$283** between them (UUUU +$293, SNDK +$203,
  NVO -$213) on ~$1,153 of adds. Two of three worked.
* The single worst line in the run is **NVO -$213.32**, which took a stage-1 add at +15.2% and then
  reversed. That is the real risk of this lane and it is not dismissible at n=1.

**Reverting to 12 is not a safe default — it restores a lane that provably cannot add at all.**
The defect in §1 stands regardless of this run. What this run establishes is that the fix *works
mechanically*; what it cannot establish, at 2-of-8 overlap on one window, is the P&L.

### THE NEXT CONSTRAINT, NOW VISIBLE

The diagnostic line added in this commit fired **36 times**:

```
ANCHOR ADD: none funded from 4 candidate(s) on a $178 budget — check
            anchor_reinforce_target_pct against the entry clip
```

$178 is below the $234 a stage-1 add costs. With the target fixed, **the binding constraint moved
one step up to `_winner_add_budget_cap = _stock_budget_available * 0.40`** — a hard-coded 40% that
lands at $170-$250 on a fully-deployed book. That is the fifth instance of the same pattern, and it
is now greppable instead of silent.

**Correct next step: evaluate `anchor_reinforce_target_pct` across the bear, OOS and non-semi
windows before shipping it — not another single run on the tuning window.**
