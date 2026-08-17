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
| `core_rebalance_band_pct` | **0.05** | **0.12** |

ONE key. A wider band means the core tolerates more drift before trading.

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

## What I will not claim

- Not that one window generalises; the drag is measured in three windows but the FIX is being
  tested in one.
- Not that lower turnover is itself a win — it is only a win if return holds or improves. A book
  that trades less and earns less has just chosen a different loss.
- Not that this closes the gap to the objective. It is worth ~2pp per window at most, which is
  the drag's whole size.
