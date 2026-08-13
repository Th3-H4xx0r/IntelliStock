# W3 loses 10pp to SPY, and it is the same cash starvation — not a new problem

Date: 2026-08-13. Runs: bt **553341** (W3 control, 2026-06-01..07-01) and bt **873929** (W0 control).
No new backtests were spent on this; both logs already existed.

## Against SPY, not against a control arm

Every earlier result in this thread was a control-vs-treatment delta, which never asks whether
either arm deserves to exist. Measured against SPY (sampled from each run's own SPY price stream):

| window | strategy | SPY | vs SPY | verdict |
|---|---:|---:|---:|---|
| W0 reference | +16.41% | +0.41% | +16.00pp | beat |
| W1 OOS bull | +11.98% | +8.54% | +3.44pp | **noise** |
| W2 bear | +24.36% | -1.77% | +26.13pp | beat (benchmark unreliable, 25 samples) |
| W3 non-semi | **-12.86%** | **-2.72%** | **-10.14pp** | **loses** |

Beats SPY in 2 of 4. The out-of-sample bull only matches the index once the 4.94pp noise floor is
applied — concentrated single-name risk earning an index return. W2's benchmark rests on 25 SPY
samples because the bear window deliberately keeps the core off, so treat it as indicative only.

## W3 is a conversion failure, and a worse one than W0

| | names with >=30% in-window range | bought | conversion |
|---|---:|---:|---:|
| W0 reference | 103 | 7 | 6.8% |
| **W3 non-semi** | **65** | **1** | **1.5%** |

The window was full of movers and the system caught essentially none:

    SDOT +522.5%   CETX +159.2%   FCEL +117.2%   DFTX +114.5%
    SPCE +110.1%   SMTK  +98.1%   NVTS  +84.3%   AXTI  +83.2%   — all missed

Discovery saw them. `SDOT` appears 49 times in the log, `SPCE` 55, `FCEL` 51, and they surface in
the ranking aggregate (`top raw: ... SDOT`, `FCEL=...`). This is not a discovery problem.

What it bought instead: `HON` -$407, `CRM` -$239, `AAPL` -$49, `HAL` -$32, `KNX` -$28. Ten round
trips, 20% win rate, and the summary line says it outright:

    CONCENTRATION: top name = +0.6% of starting NAV (NO single name moved the needle)

Against W0, where `AGQ` alone was +$1,111 and 18.5% of starting NAV. The difference between
beating SPY by 16pp and losing by 10pp is entirely whether the window's big mover got bought.
When it does not, the book defaults to large-cap mediocrity and grinds itself down — precisely the
"grinding small edges across many positions" the objective rules out as the mechanism.

## The refusal is the same one, in a different window with different names

    Buy gate inputs for NVTS: cash=$152.91 reserved=$0.00 floor=$120.00 high_conv=True open_pos=4
    SKIP BUY NVTS — cash_to_use $32.91 < min $354 (allocated $825.34)
    Backfill queue BLOCKED: ALOT (full_general_blocked, score=1.700, source=propagation)
    SATELLITE CAP: NVTS trimmed ... to keep the core at target

Compare W0's `SNDK`: `cash=$147.20 ... high_conv=True open_pos=4`, `cash_to_use $27.20 < min $410
(allocated $955.76)`. Same shape — four open positions, ~$150 of cash, a high-conviction name
correctly sized at $354-$955, and roughly $30 actually fundable.

This matters more than the W0 finding on its own. The conversion gap is **not** fitted to `SNDK` or
to one semiconductor-led window. It reproduces on independent names in a window the strategy loses.

`FCEL` was additionally refused by `Entry extension gate: recent runup > threshold`. Loosening that
gate is on the measured do-not-retry list (blocked basket -7.95%) and is not proposed here.
`full_general_blocked` is queue capacity; raising `max_positions` is also do-not-retry.

## What this does and does not license

It strengthens displacement — freeing cash by trimming the weakest holding — because the binding
constraint is now observed twice, independently, on different names. It does **not** license
claiming displacement will fix W3: the lever currently only decides and logs, nothing consumes the
request, so it cannot yet move a trade.

It also kills the framing that the reference window is representative. W0's +16pp is the outlier.
Any future claim must be measured against SPY per window, not against a sibling arm.

Caveat: all runs are `pit_mode=research` (lookahead) and not promotion-eligible.
