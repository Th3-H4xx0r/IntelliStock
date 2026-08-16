# The capture ratio: what the system does with the movers it now buys

Session 2026-08-16. Read off the runs' own end-of-run summaries. No backtest was launched to
produce this document — every number here already existed in a completed run and had not been read.

## 0. The headline

The conversion fixes worked. The system now BUYS the big movers. It then loses money on them.

In **bt 333727** (window d, 2026-04-01..2026-06-01, +20.54%, the best post-fix run in the project),
the three largest movers in the entire window were all bought, all at a correct size, and all
closed at a loss:

| name | the name's move | what the system captured | capture |
|---|---:|---:|---:|
| **AEHR** | **+152.43%** | **−10.93%** | **−7%** |
| **AAOI** | **+118.22%** | **−12.33%** | **−10%** |
| **AXTI** | **+90.11%** | **−11.94%** | **−13%** |
| MXL | +395.77% | +73.66% | 19% |
| AIQ | +48.74% | +14.92% | 31% |
| AIFD | +46.28% | +38.39% | 83% |
| AIOS | +37.77% | +1.86% | 5% |
| BOTZ | +22.19% | +20.99% | 95% |
| BC | +13.41% | +10.47% | 78% |
| RIVN | +12.48% | −15.01% | — |
| D | +4.48% | −4.49% | — |
| SPY | +16.66% | +14.95% | 90% |

`AAOI` and `AEHR` are two of the eight winners the OBJECTIVE names by ticker. They were found, they
were bought, they were sized at ~11% of NAV — every instruction the objective gives — and the
result was −12.33% and −10.93%.

**The objective's premise needs updating. The gap is no longer conversion. It is holding.**

## 1. Why: one sign error

Every exit in bt 333727 was a circuit-breaker fire, and every one was in `regime=bull`:

```
AAOI | tier=LOW | regime=bull | unrealized=-11.8% | floor=-10.0% | result=fired   day 3
AEHR | tier=LOW | regime=bull | unrealized=-11.8% | floor=-10.0% | result=fired   day 3
AXTI | tier=LOW | regime=bull | unrealized=-11.1% | floor=-10.0% | result=fired   day 5
RIVN | tier=MID | regime=bull | unrealized=-15.0% | floor=-15.0% | result=fired   day 42
```

`_resolve_effective_open_loss_floor` adds a POSITIVE `bull_pp` (+5.0) to a NEGATIVE floor, so
`-15 + 5 = -10`: in a bull the stop **tightens**. The code's own comment says the opposite is
intended, and `:8793-8799` already documents the defect as "INVERTED ... the source of the rally
exits clustered on that unintended -10% boundary". The correction,
`circuit_breaker_regime_adjustment_semantics_v2`, has been **default-OFF and never validated since
2026-07-28**.

Under v2 the bull LOW floor is −20% and MID is −25%. None of the four would have fired.
Cost of those four exits: **−$362.32, −6.04% of a $6,000 account.**

AAOI's own track after the stop, from the same log: stopped out at $135.69 on 04-23; quoted
**$172.92 on 05-28** and **$163.46 on 06-01**, against a $154.76 entry. Holding through the dip was
profitable. The stop converted a winner into a loser.

## 2. The counter-evidence — and it is why this is not a free win

**bt 288424** (window f, 2026-06-15..2026-08-01, −11.26%, the project's worst window) fires the
same gate 8 times, 7 in `regime=bull` at the same −10.0% floor. But there the names kept falling:

| name | move over window | captured |
|---|---:|---:|
| AMBQ | **−24.95%** | −11.64% |
| LRCX | −19.98% | −23.97% |
| CLSK | −16.65% | −17.24% |
| ALAB | −15.28% | −12.18% |
| AGIX | −6.94% | −10.93% |

In window f the stop was doing its job: AMBQ fell −24.95% and the breaker cut the loss at −11.64%.
**Widening it would have made window f worse.**

So `semantics_v2` is not a strict improvement. It is a regime-dependent trade: it pays when
discovery has found names in a real uptrend and costs when it has not.

## 3. The deeper finding, which no lever fixes

Compare the two windows at the level of what discovery selected:

* **window d** — every name it bought rose over the window (AEHR +152%, AAOI +118%, AXTI +90%,
  MXL +396%, AIQ +49%, AIFD +46%). Discovery was excellent. The strategy still only made +20.54%
  against SPY's +16.66%, because it gave the movers back.
* **window f** — almost every name it bought fell (AMBQ −25%, LRCX −20%, CLSK −17%, ALAB −15%),
  while SPY was flat at +0.69%. Discovery actively selected losers.

The objective states "discovery already finds the winners". That is true in window d and **false in
window f**. The strategy's edge is conditional on there being strong sustained movers to find; in a
flat, trendless tape it buys names that fall and pays the turnover to do it.

That is the honest shape of this system: **a momentum strategy with a real but regime-dependent
edge**, not a general one. It is also why "beat SPY in EVERY regime" has never been met.

## 2a. Late entry is the ROOT cause; the stop is only the proximate one

Placing each entry inside the name's own start→end range for bt 333727 splits the book perfectly:

| name | start | end | entry | % through the move | captured |
|---|---:|---:|---:|---:|---:|
| AIQ | 46.67 | 69.42 | 47.25 | **2.6%** | +14.92% |
| BOTZ | 33.22 | 40.59 | 33.55 | **4.4%** | +20.99% |
| AIFD | 37.75 | 55.22 | 39.90 | **12.3%** | +38.39% |
| BC | 72.73 | 82.48 | 74.66 | **19.8%** | +10.47% |
| RIVN | 15.06 | 16.94 | 15.60 | 28.8% | −15.01% |
| MXL | 17.39 | 86.24 | 52.27 | 50.7% | +73.66% |
| AAOI | 84.60 | 184.61 | 154.76 | 70.2% | −12.33% |
| AIOS | 10.96 | 15.10 | 14.82 | 93.3% | +1.86% |
| AEHR | 37.10 | 93.65 | **97.45** | **106.7%** | −10.93% |
| AXTI | 57.05 | 108.45 | **120.75** | **123.9%** | −11.94% |
| D | 61.82 | 64.59 | **67.62** | **209.5%** | −4.49% |

* entered **early** (<40% through): n=5, **4 of 5 profitable**, mean captured **+13.95%**
* entered **late** (≥40% through): n=6, **2 of 6 profitable**, mean **+5.97%** — and that mean is
  carried entirely by MXL. Excluding MXL: **1 of 5 profitable, mean −7.6%.**

**AEHR, AXTI and D were bought ABOVE the price at which the window ended** (106.7%, 123.9% and
209.5% through their own ranges). The system did not buy them early in a move; it bought them after
the move was over.

This corrects the interpretation of §1. Widening the stop does not rescue a name bought above where
it finishes. Holding each stopped name to the window end instead of stopping it:

| name | stopped at | hold-to-end | swing |
|---|---:|---:|---:|
| AAOI | −12.33% | **+19.3%** | +$220 |
| RIVN | −15.01% | +8.6% | +$198 |
| AEHR | −10.93% | −3.9% | +$48 |
| AXTI | −11.94% | −10.2% | +$11 |

≈ **+$477, about +8% of the account** before accounting for the fact that the freed capital did get
redeployed (AIOS/BC/D, +$61 combined), so call it ~+6-7% net. Real but not transformative — and
**two of the four remain losses even when held.**

So the ordering of the remaining work is: **entry timing first, stop semantics second.** The stop
is worth one preregistered run because it is a sign error and nearly free to correct. Entry timing
is the bigger prize and is not a config flag.

## 3a. The bear leg DID profit in a bear — objective blocker #5 is closed

The objective lists as blocker #5: *"Bear leg built but never shown to profit in a bear."* It has
been shown, and nobody read it. **bt 235194** (window c, 2026-02-01..2026-04-01) ran while SPY fell
**−5.29%** and returned **+0.46%**:

| name | move | captured | P&L |
|---|---:|---:|---:|
| **SQQQ** | **+16.98%** | **+13.10%** | **+$416.61** |
| XOM | +13.58% | +15.13% | +$136.20 |
| APD | +6.14% | +6.28% | +$56.54 |
| SPY (core) | −5.29% | −1.34% | −$111.72 |

`SQQQ` captured 77% of its move and contributed **+$416.61 on a $6,000 account (+6.9%)**. The
rotation into the −3x inverse QQQ is what turned a −5.29% tape into a positive return. XOM captured
MORE than its move (>100%), i.e. the position was added to on the way up.

This is the single clearest instance in the project of the objective's stated mechanism working as
designed. Blocker #5 should be marked closed, on this evidence, with the caveat that it is one
window.

Note also that all 8 circuit-breaker fires in this window were `regime=chop`, where `semantics_v2`
makes **no adjustment at all**. The lever is inert here; window c's result stands either way.

## 3b. How much post-fix evidence actually exists

| window | dates | pre-fix run | post-fix run | status |
|---|---|---|---|---|
| a | 2025-11-10..2026-01-10 | 866880 +9.15% | 443154 +8.19% | **STOPPED at 97.65%** — no summary block, not a clean number |
| c | 2026-02-01..2026-04-01 | 235194 +0.46% | — | never re-run post-fix |
| d | 2026-04-01..2026-06-01 | 559934 +4.44% | **333727 +20.54%** | the only clean completed post-fix run |
| f | 2026-06-15..2026-08-01 | 288424 −11.26% | 325136 (this session) | in flight |

**The post-fix configuration has exactly ONE clean completed backtest.** Every claim made for it
rests on that single run, on six round trips, with 78% of the return unrealized. That is the
sentence that has to travel with any production decision.

## 3c. The conversion bug IS fixed — and fixing it did not produce an edge

This is the strongest result of the session because it does **not** rest on the ~10pp return
dispersion. Refusal counts are deterministic mechanism counts, not noisy P&L, and this is the same
window before and after:

| run | window | config | `SKIP BUY` | `insufficient_cash` | bars with book FULL | trades | return | SPY |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 288424 | f | pre-fix | **41** | 40 | 180 | 37 | −11.26% | +0.69% |
| **325136** | f | **post-fix** | **2** | **2** | 59 | 35 | **−5.09%** | +0.69% |
| 333727 | d | post-fix | 4 | 4 | 0 | 20 | +20.53% | +16.66% |

**Refusals collapsed 41 → 2 on the identical window.** The five 2026-08-15 fixes did precisely what
they claimed: they unblocked conversion. `satellite_cap_below_floor` never fires in any post-fix
run. The book is no longer sealed — it holds 4-5 of 6 slots on most bars rather than jamming at 6.

**And the run still loses 5.09% while SPY gains 0.69%.**

Window f post-fix bought a completely different, larger-cap book (AAPL, ABBV, AMZN, EQT, SPY, VVX)
and still lost, on its selections:

| name | the name's move | captured |
|---|---:|---:|
| TSLA | −23.45% | **−25.72%** (−$155.09) |
| CCL | −4.60% | **−12.53%** |
| **RCL** | **+8.10%** | **−7.50%** |
| NVDA | −2.16% | −6.37% |
| SQQQ | +10.31% | +0.22% |
| AMZN | +13.79% | +12.06% |

`RCL` rose 8.1% over the window and the system **lost 7.5% on it** — a round trip taken the wrong
way inside a rising name. `SQQQ` captured 0.22% of a +10.31% move because only 6 bars classified
bear, so the hedge that carried window c never ran here. 35 trades on a six-name book over six
weeks.

**Conclusion: conversion was real, it has been fixed, and fixing it did not create an edge in flat
tape.** The binding constraint is no longer refusal. It is that in chop the selection has no edge
and the churn costs money. That is a strategy problem, not a plumbing problem, and no config lever
in this repository addresses it.

## 4. What this changes about priorities

1. `semantics_v2` is worth ONE paired run because it is a sign correction, not a fitted threshold —
   but it must be tested where it should HURT (window f), which is preregistered in
   `prereg-circuit-breaker-inversion-2026-08-16.md`.
2. The +20.54% headline of bt 333727 is **78% unrealized** — $958.93 of the $1,232.31 is open marks
   on five positions, three of them bought 13 days before the window ended. Realized P&L was
   **+4.55%**. Any statement of that number must carry this.
3. Round trips in that run: **6**. The objective's own rule is "n=5 round trips is not evidence."
   The entire post-fix evidence base is six closed trades on one window.
4. A regime gate on the strategy itself — trade the satellite only when the tape has sustained
   movers — is a larger and better-motivated idea than any remaining config lever. It is NOT
   attempted here; it is the recommendation for the next session.
