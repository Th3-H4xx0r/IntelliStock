# anchor-target — planner arithmetic and the execution gate that was originally missed

> **CORRECTED 2026-08-10.** `ANCHOR ADD:` records a planner allocation, not a fill. Bt 633644's
> five plans, bt 584712's AXTI plan, and bt 615886's AAOI plan were all rejected by downstream
> `SATELLITE CAP`; no recipient quantity increased. The original result/attribution was false and is
> replaced in §6.

Found in bt 571147 (+17.36%, the best run on record) from logs already available.

---

## 0. THE EVIDENCE

```
V31 anchor reinforcement budget: cap=$1007 (40% of stock_budget=$2518), candidates=0
V31 anchor reinforcement budget: cap=$186  (40% of stock_budget=$465),  candidates=4
V31 anchor reinforcement budget: cap=$178  (40% of stock_budget=$444),  candidates=6
...                                                                     (25 such bars)
```

**Twenty-five bars. Four to six candidate documents on nearly every one. Zero planner allocations.**
(The document list is broader than stage-qualified winners; age/P&L/drawdown/gap can still reject.)

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

For a full 14%-of-NAV opening clip, **the better the winner, the further out of reach its target.**
This is not universal: a partial/runt entry can remain below the target. Bt 584712 target-12 planned
$130 for AXTI after its smaller ~$579 opening fill, then the broker rejected that plan. Thus target
12 is usually planner-inert at a full clip, but observed execution is zero for both 12 and 20.

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

| target_pct | stage 1 plan | stage 2 plan | stage 3 plan | planner target |
|---|---|---|---|---|
| 12 (full clip) | $0 | $0 | $0 | below a full entry |
| **20** | **$234** | **$385** | **$586** | 20% / 24.6% / 30.8% |

These are planner targets, not reachable position weights under the observed broker policy. The
broker independently defaults to a 15%-of-NAV single-position cap, tighter than strategy
`single_position_max_pct=25`, and the standing satellite cap rejected every observed plan first.

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

The second line exposes planner negatives, but neither line proves execution. A valid success
signature must also include a source-tagged BUY fill and a recipient quantity increase; blocked plans
must name the satellite/turnover/single-position/cash/order gate.

## 5. PASS / FAIL, DECLARED BEFORE THE RUN

Reference window `2026-01-01..2026-03-01`, cold, `anchor_reinforce_target_pct=20`.

| outcome | reading |
|---|---|
| `ANCHOR ADD: <SYM> stage=...` present | planner allocated; trace downstream, **not a pass** |
| source-tagged BUY fill + quantity increase | execution mechanism is alive |
| only `ANCHOR ADD: none funded` | no plan; target/budget/qualification reasons need separation |
| aggregate return threshold | invalid unless exposure exists and discovery/history are held identical |

**Attribution warning, written in advance:** every A/B this session overlapped its control by 2 of
9-11 held names. A single run cannot resolve this lever's P&L. What it CAN resolve is whether the
lane fires at all, and at what size — which is deterministic, greppable, and the actual claim here.


---

## 6. CORRECTED RESULT — bt 633644 and follow-up bt 615886

### Bt 633644: five plans, zero adds

The five printed allocations totalled $1,153, but every one followed the same path:

```text
ANCHOR ADD: UUUU stage=1 +$241 ...          # planner allocation
UUUU ... action_intent=winner_add_buy
SATELLITE CAP: UUUU skipped ...              # broker rejection
```

The sequence repeats for NVO, UUUU stages 2/3, and SNDK. Recipient quantities never increased:
UUUU stayed 53.07508767 shares, NVO 16.13507190 until its sell, and SNDK 1.78663492. Their reported
P&Ls — UUUU +$293.42, SNDK +$203.38 and NVO -$213.32 — belong entirely to original lots. The prior
claim that the recipients netted +$283 "on ~$1,153 of adds" was false because the $1,153 never
traded. NVO remains a useful *eligibility-risk* example (qualified at +15.2%, then reversed), not an
add-loss example.

All five plans died at satellite capacity. Even without that gate, their pre-add weights were about
15.25%-18.86%, already at/above the broker's default 15% cap. The target's stage weights
(20%/24.6%/30.8%) therefore conflict with the actual execution cap. The planner also advanced the
stage before broker feedback and deducted planned dollars from the new-entry slate despite rejection.

### Bt 615886: the defect reproduces on the OOS bull window

Final: +9.02%, max DD 5.4%, 27 fills. The run printed 19 `none funded` lines and one positive plan:

```text
ANCHOR ADD: AAOI stage=1 +$265 ... raw 1.200
AAOI ... action_intent=winner_add_buy
SATELLITE CAP: AAOI skipped ... ($-595 room)
```

AAOI's only BUY was its original 7.03193351-share fill. All 180 position snapshots through its exit
show exactly 7.031933505093401 shares, and the SELL removed the same quantity. Its +$238.63 was
original-lot P&L. Thus target 20 again changed planner state/budget but produced zero reinforcement
exposure. The run trailed SPY's +13.10% by 4.08pp, but target and salt/discovery both changed, so the
return cannot be attributed to anchor planning.

### Correct verdict and next constraint

* Planner activation is proven; execution activation is not. Across bt 633644, bt 584712 (AXTI),
  and bt 615886 (AAOI): **7 plans, 7 downstream rejects, 0 fills/quantity increases**.
* Target 12 is not universally planner-inert: a partial/runt opening fill can leave target room, as
  AXTI's $130 target-12 plan showed. It was still execution-inert.
* The hard-coded 40% budget clipped four bt 633644 plans but did not block an executed order, and
  it does not explain every `none funded` line. Raising it alone increases false budget crowd-out.
* The smallest safe prerequisite is default-OFF fill-time stage accounting and unambiguous
  PLAN/BLOCK/ORDER/FILL logs. Any executable policy must remain lane-specific, retain core and
  turnover ceilings, and cap the final position coherently; do not raise the global broker cap.
* The old bear/OOS/non-semi paired-return plan is stopped. Salts demonstrably change/inherit
  discovery, and no aggregate return comparison is causal while the treatment has zero exposure.

Detailed independent evidence: `agent-anchor-log-analysis.md` and `agent-anchor-code-audit.md`.
