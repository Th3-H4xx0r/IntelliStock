# Preregistration: widen the core rebalance band

Written BEFORE the run. Session 2026-08-16. Follows the chop stand-down, which was disqualified
on turnover and in doing so pointed here.

## Why this lever, from two measurements that keep repeating

**1. Turnover is 4-5x break-even.** Measured on the cold window-f pair: **303% of NAV** (control)
and **361%** (treatment) over a SIX-WEEK window. The objective's break-even is ~50%/month, about
75% over this window. This is the known leak, and nothing tested so far has moved it down.

**2. The core loses to the index it tracks**, in every run measured:

| run | SPY moved | core captured | drag |
|---|---:|---:|---:|
| bt 333727 | +16.66% | +14.95% | −1.7pp |
| bt 325136 | +0.69% | −1.55% | −2.2pp |
| bt 790588 | +0.69% | −1.41% | −2.1pp |

A passive index sleeve should track its index. Losing ~2pp per six weeks — **10-15pp a year** — on
the leg that is supposed to be the safe one is a cost, not a strategy choice. The core is exempt
from the turnover budget (`broker.py:4184-86`), rebalances on a **5% band** with a **5-day**
minimum, and crosses the spread on every deploy at ~22.8 bps.

Both facts point at the same place: the core trades too often.

## The change

| key | control | treatment |
|---|---|---|
| `regime_profiles.{bull,chop,recovery}.core_rebalance_band_pct` | **0.05** | **0.12** |
| `core_rebalance_band_pct` (base) | 0.1 | 0.12 |

A wider band means the core tolerates more drift before trading.

**Correction made while applying it:** this document first said the value was 0.05. The BASE key
was actually **0.1**; the three regime profiles carried 0.05, and the profile is what binds
(`core_target_pct` and its siblings are resolved regime-aware — reading them off base config is a
documented live defect pattern, `core_sleeve.py:231-234`). So the effective band was 0.05
everywhere, and all four keys are now 0.12 — base included, so base and profiles cannot disagree
and silently reintroduce 0.1 on a regime with no profile. Recorded rather than quietly widened.

Control is **bt 790588** (already run, cold, `chop.core_target_pct=0.35`), and the document has
been reverted to 0.35, so the band is the only difference.

## Endpoints, fixed in advance

1. **Turnover MUST fall.** This is the mechanism. If notional turnover does not drop below the
   control's 303% of NAV, the lever did not do what it claims and the run says nothing —
   report inert and stop, regardless of return.
2. **Comparability first.** Overlap < 60% or a non-cold arm ⇒ VOID, no delta quoted.
3. **Core capture vs SPY should improve.** The control's core captured −1.41% against SPY +0.69%.
   Fewer rebalances should narrow that gap; if drag WORSENS while turnover falls, the band is not
   the drag's cause and I should say so.
4. **Return** is readable at >0.5pp (the cold floor), but is secondary to endpoints 1 and 3 — a
   return gain with unchanged turnover would not support the stated mechanism.
5. **SQQQ deployment must not fall** (BUY notional, not P&L — P&L is exit timing and nearly made
   me fail the chop lever for the wrong reason).

## RESULT — INERT on the endpoint that mattered

**bt 790588 (control, band 0.05) vs bt 545803 (treatment, band 0.12)**, both cold, 80% overlap.

| endpoint | control | treatment | verdict |
|---|---:|---:|---|
| 2. comparability | — | — | 80% overlap ✓ |
| **1. turnover** | **303% of NAV** | **302% of NAV** | **UNCHANGED — INERT** ✗ |
| fills | 26 | 26 | unchanged |
| 3. core capture (SPY +0.69%) | −1.41% | **−1.64%** | slightly WORSE ✗ |
| 5. SQQQ BUY notional | $2,210 | $2,212 | unchanged ✓ |
| 4. return | −2.70% | −1.72% | **not readable — endpoint 1 failed** |

By this document's own rule 1 — *"if notional turnover does not drop, the lever did not do what it
claims and the run says nothing, regardless of return"* — **the +0.98pp return gain is not
claimed.** The band is not being kept.

### Why it was inert, which is the useful part

The band DID act. It is visible in both logs and they differ exactly as predicted:

```
control    [core] released 0.7143 SPY @ 754.16 (core rebalance: band_release (49.0% -> 40.0% of NAV))
treatment  [core] hold (release) — within_band: core 49.0% vs target 40.0% of NAV
```

The wider band suppressed the *rebalance* release — and the core still made **7 SPY fills in both
arms**, because the suppressed trade was immediately replaced by a different one:

```
treatment  [core] funding $630 of conviction overflow out of the core (design room $738, ...)
           [core] released 0.8051 SPY @ 754.75 (core rebalance: funding (48.9% -> 40.0% of NAV))
```

**`core_rebalance_band_pct` gates the REBALANCE path only. The FUNDING path — selling core to
finance a satellite buy — is not band-gated at all.** So widening the band moves core turnover
from one lane to the other and changes nothing net.

That is a structural finding, not a tuning result: **the core's turnover is driven by satellite
funding demand, not by drift.** Any lever aimed at core churn has to gate the funding release, and
the levers that do that are `core_funding_release_reserve_decisions` and the conviction-overflow
sizing — not the rebalance band. It also explains why the core's ~2pp drag has survived every
band-shaped fix.

Reverted to 0.05 in all three profiles, base back to 0.1.

## What I will not claim

- Not that one window generalises; the drag is measured in three windows but the FIX is being
  tested in one.
- Not that lower turnover is itself a win — it is only a win if return holds or improves. A book
  that trades less and earns less has just chosen a different loss.
- Not that this closes the gap to the objective. It is worth ~2pp per window at most, which is
  the drag's whole size.
