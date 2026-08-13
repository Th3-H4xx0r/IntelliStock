# Result — conviction-reserve paired arms: REJECTED

Date: 2026-08-13
Preregistration: `docs/investigations/prereg-conviction-reserve-pair.md` (committed before launch)
Arms: doc 194 `v2-conv-ctl` (reserve=0) versus doc 195 `v2-conv-trt` (reserve=0.15).
Verified config diff between arms: exactly two keys — the treatment variable and the
protocol-required per-arm salt.

## Verdict: REJECT `satellite_conviction_reserve_pct = 0.15`

Eight runs, four preregistered windows, control and treatment each.

| window | dates | control | treatment | delta | verdict |
|---|---|---:|---:|---:|---|
| W0 reference | 2026-01-01..03-01 | +16.41% | +16.08% | -0.33pp | noise |
| W1 OOS bull | 2026-03-30..04-27 | +11.98% | +11.08% | -0.90pp | noise |
| W3 non-semi | 2026-06-01..07-01 | -12.86% | -5.19% | **+7.67pp** | material, better |
| W2 bear | 2026-03-02..03-30 | **+24.36%** | +14.57% | **-9.78pp** | material, **worse** |

median **-0.62pp**, mean **-0.84pp**, positive in **1 of 4** windows.

The preregistered rule was: the bear block is a **safety veto**. The treatment is 9.78pp worse
there. It is rejected. The single material gain (W3) does not override a veto, and a
1-of-4 hit rate with a negative median is the signature of noise, not edge.

Backtest ids: W0 `873929`/`906181`, W1 `647755`/`841172`, W3 `553341`/`406318`,
W2 `624674`/`595255`.

## The mechanism bound; the money did not follow

This is the important distinction, and it is why the mechanism check was preregistered
separately from P&L.

The lever **did** do what it was designed to do. In W0, `SNDK` went from a $29 fill
(0.48% of NAV, 0.057 shares) to a **$1,018 fill** (1.54 shares), and late-bar conviction
funding rose from 0.5% of requests to 5.1%. Average winning round trip nearly doubled
($177 -> $305) and turnover fell in 3 of 4 windows.

It still did not make more money. Fixing the allocation flaw moved size into the winners and
the result was flat-to-worse. That falsifies the assumption that starved sizing was the
binding constraint on returns in these windows.

## A false positive that the protocol caught

An earlier comparison put treatment at **+16.08% versus +9.70%**, an apparent +6.38pp win.
That control was doc 193 with a **warm** history scope, not a matched cold-salt arm. Against
the genuine control the same treatment run is **-0.33pp**.

The entire apparent edge was an artifact of unequal starting state. This is exactly the failure
the "equally warm or equally cold" rule exists to prevent, and it would have been reported as a
success without it.

## Positive finding: the bear leg profits in a bear

Objective blocker (5) was "bear leg built but never shown to profit in a bear". It is now shown.

In W2 the control returned **+24.36%**. The log confirms the mechanism rather than a lucky
long book:

```
V31 market regime: bear (raw=bear, proxy=QQQ, ret20=-3.5, ret5=-0.25)
Regime capacity gate (Z4.1): regime=bear max_positions 6->2
FILL BUY SQQQ qty=58.34 price=69.78
FILL SELL SQQQ ... price=72.03 / 73.02 / 75.91 / 78.42 / 80.28
```

Regime detected bear, long book cut to 2 positions, SQQQ accumulated at $69.78 and distributed
into $72-$80 as the market fell. This belongs to the **existing** configuration, not to the
rejected treatment.

Caveat: one bear window, `pit_mode=research`, so this is a working mechanism demonstration and
not a promotion-eligible result.

## What the giveback actually was

Treatment W0 peaked at $9,110 and closed at $6,965. The log shows the circuit is a reaction,
not the cause:

```
Drawdown circuit KILL: -18.6% from peak ($9110 -> $7415)
Drawdown circuit KILL: liquidating 5 position(s): AGQ, APP, AVNT, COPX, SPY
Drawdown circuit KILL: liquidating 3 position(s): HYMC, SNDK, SPY
```

Positions fell 18.6% first; the circuit then liquidated the whole book at the low — including
`SNDK` — and re-based the peak to $7,329.

**No threshold was tuned to preserve that peak**, deliberately. The peak is only visible in
hindsight and fitting to it would not survive out-of-sample. The legitimate open question is
structural and separate: whether a portfolio-level KILL should liquidate a high-conviction
name whose own trend is intact. That must be tested as its own preregistered change.

## Status against the objective

* +100-300%/yr, beat SPY in every regime: **not demonstrated**.
* Conversion gap: **diagnosed** (budget consumed chronologically, not by conviction) and the
  obvious fix is **measured and rejected**.
* Blocker (5) bear leg: **resolved** in one window.
* Blockers (1) entry timing, (2) sizing, (3) trim-back, (4) passive execution: open.
* All results remain `pit_mode=research` (lookahead) and are not promotion-eligible.
  Nothing here justifies real money.

## Next candidates, in priority order

1. Entry timing (blocker 1) — the log shows winners entered late; W3 shows both arms losing,
   so entry quality, not size, may be the binding constraint.
2. The KILL-versus-winner question above, as a preregistered structural change.
3. Passive execution (blocker 4), the documented largest unexploited cost lever.

Do not re-run the reserve at a different value hoping for a better window. That is fitting.
