# Preregistration: the hedge sells itself to rebuild cash

Written BEFORE the run. Third lever in the turnover chain; the first two were rejected and each
one narrowed where the churn actually lives.

## The chain that led here

1. **Chop stand-down** — return improved +1.17pp, **turnover ROSE** 303%→361%. Disqualified.
2. **Core rebalance band** 0.05→0.12 — **inert**: 303%→302%, 26→26 fills. The band gates only the
   *rebalance* path; the suppressed trade reappeared as a *funding* release.
3. **Funding reserve** 4→60 — **inert**: 100% overlap, 0.01pp. The value 4 was already correct
   (the unit is a refused DEPLOY decision, not a bar), and the recycle it targets does not occur
   in this window — every funding release was consumed by the satellite buy it was raised for.

Each null narrowed the search. Tracing the actual fill chronology found the churn:

```
06-29  CORE  SELL SPY  $2,272     raise cash for the bear leg
06-30  HEDGE BUY  SQQQ $1,321 + $889
06-30  HEDGE SELL SQQQ $741       <- unwinding the SAME DAY
07-02  CORE  BUY  SPY  $684
07-07  CORE  BUY  SPY  $681
```

## The mechanism, from the log

It is not the trailing stop and not the regime flipping. It is a **cash-target refill**:

```
[sleeve] parked $1322.17 in BEAR leg SQQQ @ 38.29 (regime=bear, leg=1322/4130 cap, alloc=70%)
[sleeve] parked $1322.17 in BEAR leg SQQQ @ 38.29 (leg=2641/4130 cap)
[sleeve] parked $1322.17 in BEAR leg SQQQ @ 38.29 (leg=3529/4130 cap)
[sleeve] released 20.0983 SQQQ @ 38.26 (bear-leg refill: cash 2.0% -> target 15% of NAV)
[sleeve] released  0.4408 SQQQ @ 36.95 (bear-leg refill: cash 14.7% -> target 15% of NAV)
[sleeve] release SKIPPED SQQQ: $0.55 < $5.00 minimum — the live broker would reject this order
```

Measured in the cold control (bt 790588): **3 park events totalling $3,966.51, then 48 refill
releases selling $2,230.84 straight back.** The hedge liquidates **56% of itself within days** to
rebuild a cash target — and the same 48 releases appear in bt 185794, so it is systematic, not a
one-off.

Two configs are fighting: `residual_sleeve_bear_alloc_pct = 0.70` says put 70% of NAV in the
hedge; `residual_sleeve_release_cash_pct = 0.15` says hold 15% of NAV in cash. The sleeve satisfies
the first, then immediately sells the hedge to satisfy the second. Note also that some releases are
sub-dollar — the log itself says *"the live broker would reject this order"* — so this is spraying
tiny orders that would not even execute live.

## The change

| key | control | treatment |
|---|---|---|
| `residual_sleeve_release_cash_pct` | **0.15** | **0.02** |

ONE key, set to match `residual_sleeve_buffer_pct` (0.02), which is the sleeve's own idea of the
cash it needs. Chosen for internal consistency, not tuned to a return.

Control is **bt 790588** (cold; band, chop target and funding reserve all reverted).

## Endpoints, fixed in advance

1. **Refill releases MUST fall** from 48, and SQQQ sold-back notional from $2,230.84. This is the
   mechanism. If they do not fall, inert — report and stop, whatever the return.
2. **Comparability first.** Overlap < 60% or non-cold arm ⇒ VOID.
3. **SQQQ BUY notional must NOT fall** — the hedge must still deploy. This lever should let it
   HOLD more, not deploy less.
4. **Total turnover** against the control's 303% of NAV.
5. **Return** readable at >0.5pp, secondary to 1 and 3.

## CORROBORATION from a window this document did not plan to use

Found before the treatment landed, at zero run cost, by asking the same question of the window
where the hedge WORKED. **bt 235194** (window c) is the run where SQQQ captured 77% of a +16.98%
move for **+$416.61** while SPY fell −5.29%:

| window | hedge outcome | parked | refill releases | sold back |
|---|---|---:|---:|---:|
| **c** (bt 235194) | **+$416.61, 77% capture** | $3,225.09 | **0** | **$0.00 (0%)** |
| **f** (bt 790588) | +$20.49, 0.9% capture | $3,966.51 | **48** | **$2,230.84 (56%)** |

**The window where the hedge earned its keep never refilled once. The window where it captured
almost nothing sold 56% of itself back.**

That is a stronger test than the treatment run, because it is a natural experiment on the
outcome that matters rather than on turnover: same code, same config, opposite behaviour, and the
difference tracks hedge performance exactly. It does not establish causation on its own — window c
is also a genuinely trending bear where the hedge would do well regardless — but it means the
refill is not merely a turnover cost. It is plausibly the difference between a hedge that captures
77% of its move and one that captures 0.9%.

This raises the stakes on endpoint 3: if the treatment holds the hedge intact AND SQQQ capture
improves, the lever is worth more than its turnover saving.

Cutting the cash target means the sleeve holds less dry powder in a bear. If the hedge then cannot
fund a satellite buy or a stop-out, that shows up as fewer satellite fills — endpoint 3's sibling,
and I will report it if it happens. The bear leg is the ONE validated edge this system has
(window c: SQQQ captured 77% of a +16.98% move, +$416.61), so a change that weakens it is a bad
trade even if turnover falls.

## RESULT — the mechanism moved, the outcome did not. NOT ADOPTED.

**bt 790588 (cash 0.15) vs bt 129963 (cash 0.02)**, both cold, **100% overlap**.

| endpoint | control | treatment | |
|---|---:|---:|---|
| **1a. sold back** | **$2,230.84** | **$168.42** | **−92%** ✓ |
| **1b. refill releases** | 48 | **79** | **ROSE** ✗ |
| 3. SQQQ BUY notional | $2,210 | $2,210 | unchanged ✓ |
| SQQQ P&L | $20.49 | **$8.97** | worse |
| SQQQ capture | 0.93% | **0.41%** | **worse** ✗ |
| satellite fills | 9 | 9 | unchanged ✓ |
| total fills | 26 | **18** | −31% ✓ |
| turnover | 303% | 302% | unchanged |
| return | −2.70% | **−3.05%** | −0.35pp, below the cold floor |

**Endpoint 1 is SPLIT, and the split is the finding.** The dollar value of hedge liquidation
collapsed 92% — the mechanism working. But the COUNT of refill releases *rose* 48 → 79, because
the same cash-target logic now fires more often for tiny amounts: the control sold ~$46 per
release, the treatment ~$2. Same defect at a smaller scale, and the log already flags these as
unexecutable in live — `release SKIPPED SQQQ: $0.55 < $5.00 minimum — the live broker would
reject this order`.

**Endpoint 3's letter passes and its spirit fails.** SQQQ BUY notional is identical so the hedge
still deployed, but capture FELL 0.93% → 0.41% and P&L halved. Holding the hedge did not make it
earn more, because window f's 3 bear bars never produced a sustained downtrend.

**Verdict: NOT ADOPTED.** Reverted to 0.15. Total fills −31% and liquidation −92% are real, but
the preregistered endpoint named refill COUNT and it rose, the hedge got worse on the outcome that
matters, and return did not improve. Adopting on "fills fell" alone would be picking the surviving
half of a split endpoint after seeing the result — exactly what the chop lever was rejected for.

### What this establishes about the bear sleeve

The natural experiment still stands — window c refilled **0** times and captured **77%**; window f
refilled 48 times and captured **0.9%**. But this run shows the causation does NOT run
refill → poor capture: removing 92% of the refilling did not restore capture. Both are symptoms of
one upstream condition — **window f had 3 bear bars, window c had 19.**

**That reframes the bear leg's problem as regime DETECTION, not sleeve mechanics.** The leg is
being armed on isolated bars where it cannot work, and the refill churn is a side-effect of
deploying a hedge into chop and unwinding it immediately.

Fourth distinct mechanism ruled out in this chain, after the chop allocation, the rebalance band
and the funding path.

---

# The dwell gate — code change, and the first lever that did what it claimed

`residual_sleeve_bear_deploy_min_dwell_days = 2` (new code, shipped 59798b2), window f,
bt 790588 (control) vs bt 310460 (treatment), both cold, 89% overlap.

| | control | treatment | |
|---|---:|---:|---|
| gate fired | — | **3×** | "held 1 day(s), needs 2" |
| SQQQ BUY notional | $2,210 | **$0** | hedge never opened |
| bear-leg refills | 48 | **0** | the churn is gone with it |
| total fills | 26 | **14** | −46% |
| **turnover** | **303% of NAV** | **228%** | **−75pp** |
| return | −2.70% | −2.94% | −0.24pp, BELOW the 0.5pp cold floor |

**Mechanism confirmed**: the gate fired exactly where predicted, on the one-day bear bars that
window f is made of, and the entire 48-release refill cycle disappeared with the position that
caused it. Turnover fell 25% — the first movement on this project's known leak from any of the
five levers tested.

**Return is unchanged within noise** (−0.24pp against a 0.5pp floor). The hedge earned +$20.49 in
this window, so removing it costs about that and saves the spread on 12 fills.

**This is not yet an adoption.** Suppressing the hedge is correct ONLY where no sustained bear
arrives. The decisive question is whether the same gate also suppresses it in **window c**, where
the leg captured 77% of a +16.98% move for **+$416.61** — that window has 19 confirmed bear bars,
so the dwell should clear 2 easily, but "should" is what preregistration exists to stop me
asserting. bt 324360 is running it.

**Pre-committed rule:** if the gate reduces SQQQ deployment in window c, it is REJECTED regardless
of what it does to turnover. The bear leg is the one validated edge this system has, and trading
it away for a churn saving is a bad trade.

## DECISIVE TEST PASSED — the dwell gate is ADOPTED

**bt 324360**, window c with `dwell=2`, cold:

```
bear leg DEFERRED  ->  0 occurrences
```

**The gate fired ZERO times in the window where the hedge earns its keep.** 19 confirmed bear
bars clear a 2-day dwell trivially, so the gate is inert there *by construction* — which also
means the run is identical to what `dwell=0` would produce, and no separate control was needed.

| window c | pre-fix (bt 235194) | current code + dwell=2 (bt 324360) |
|---|---:|---:|
| deferrals | — | **0** |
| SQQQ BUY notional | $3,179 | **$3,745** |
| SQQQ P&L | $416.61 | **$490.84** |
| turnover | 574% | **402%** |
| fills | 33 | **15** |
| return | +0.46% | **+6.11%** (SPY −5.29%) |

Hedge deployment ROSE rather than fell, so the preregistered disqualifier is satisfied with
margin. **The +6.11% is NOT attributable to the dwell gate** — the gate did nothing here — it is
the accumulated fixes against an old pre-fix run, a multi-variable comparison.

### Verdict across both windows

| endpoint | window f | window c |
|---|---|---|
| mechanism fired where predicted | ✓ 3× on one-day bears | ✓ 0× (correctly silent) |
| SQQQ deployment must not fall | n/a (no confirmed bear) | ✓ **rose** $3,179 → $3,745 |
| turnover | ✓ 303% → **228%** | unchanged (gate inert) |
| return | −0.24pp, below the 0.5pp floor | unchanged (gate inert) |

**ADOPTED at `residual_sleeve_bear_deploy_min_dwell_days = 2`.** It is the only lever of the five
tested tonight that did what it claimed, moved the known leak, and could not harm the one
validated edge. It is not claimed to improve returns — its measured return effect is inside the
noise floor. It is claimed to stop the book opening a −3x leveraged short on a one-day dip, which
is a risk decision, and to cut turnover 25% while doing it.
