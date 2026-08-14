# Session summary, 2026-08-14

Ten backtests. Nothing enabled on doc 193 (still 580 keys). `doc-179`/`alpaca-main` never opened.
All runs `pit_mode=research`, so none is promotion-eligible.

## The two results that change how everything else is read

**1. The noise floor is ~10pp, not 4.94pp.**
bt 873929 (+16.41%) and bt 523085 (+6.00%) share instance, window, cash, granularity and config. The
entire gap is `AGQ` — 627 log lines in one run, zero in the other, +$1,111 = 113% of that run's P&L,
arriving as a *Discovered trend ETF* from stored Nexus state.
Consequences: the conviction-reserve **rejection is void** (its median was −0.62pp); the chained
**"+41.26% / +129%/yr" figure is withdrawn**; no single paired comparison under ~10pp means anything.
→ `result-displacement-pair-and-noise-floor.md`

**2. Discovery and promotion are aimed at different instants.**
Momentum discovery fires on a *completed* 20-day run (median **+24.2%** already booked). Breakout
promotion requires a fresh high *now* (within 1%). Measured: AAOI sits 9.7% below its own 25-bar high
when scored, LITE 13.6%, SNDK 5.5%. The qualifying highs **precede** evaluation.
→ `result-breakout-nopattern.md`

## Blocker status, all five measured

| blocker | status | evidence |
|---|---|---|
| (1) entry timing | **real, now explained** | median mover runs 19 days from +10% to peak; discovery consumes +24.2% |
| (2) sizing | **retired** | median entry **14.00% of NAV**, inside the 10–15% band (the 4.73% figure is stale) |
| (3) no trim-back | **addressed** | displacement: 24 executions, SPY core spend 95.9% → 54.0% |
| (4) execution cost | **right-sized** | 22.8 bps × notional = **5–15% of P&L**; not the next lever |
| (5) bear leg | **shown once** | W2 +24.36%, regime=bear, SQQQ bought — single window |

## The funnel is the binding constraint, and it is stable

| run | window | moved ≥30% | buy intent | bought |
|---|---|---:|---:|---:|
| bt 873929 | W0 | 103 | 18% | 7% |
| bt 553341 | W3 | 65 | 25% | 2% |
| bt 523085 | W0 ctl | 57 | 17% | 5% |
| bt 102463 | W0 trt | 53 | 20% | 1% |
| bt 718107 | W0 fallback | 53 | 18% | 3% |

79–82% of large movers never produce a buy intent, across five runs, two windows, both arms.
Capture on names actually bought is fine: VICR 39.9% of a 71% move, AMAT 81.2%, SBLK 100%.

## Displacement — the one lever that moved the real constraint

Preregistered, paired, both arms to completion. ctl +6.00% vs trt +11.12%.
**Not a return win** (+5.12pp < ~10pp dispersion). But trades 32 → 16, core gross 1.80x → 0.96x NAV,
max drawdown 7.8% → 4.6%, and `AMAT` bought. Turnover is the objective's known leak, so this is the
first change measured to move it the right way. Default-OFF; needs 3–5 repeats per arm.

## Shipped, all default-OFF, suite 19 failed / 4998 passed (19 pre-existing intentional)

`buy_order_conviction_ranked_enabled`, `satellite_displacement_enabled`, `hold_diagnostics_enabled`,
`breakout_diagnostics_enabled` (+ `BREAKOUT NOPATTERN`), `price_history_diagnostics_enabled`,
`breakout_history_fallback_enabled`.
The fallback is verified to make **all 53 movers evaluable** where none were before — a real defect
fixed — and equally verified **not** to widen the funnel, for the reason in result 2.

## What is left, ranked by measured size

1. **Discovery latency.** Names become candidates +24.2% into a move with a median 19 days left.
   The only untested lever is shortening the lookback, and it trades directly against turnover
   (~290%/mo live vs ~50% break-even). **Operator's design call — no value picked here.**
2. **Turnover.** Improved once; needs repetition to confirm.
3. **Execution cost.** 5–15% of P&L, and resting orders would worsen entry timing, which is already
   the binding problem.

## Method lessons, each paid for today

* A killed probe produces **false negatives** exactly as a stopped run produces meaningless returns.
* An **empty log reason** is indistinguishable from "not evaluated" — `"+".join([])` made 6,314 of
  7,340 skips unreadable and produced a published claim that had to be retracted.
* Test invariants **behaviourally**, not by textual adjacency.
* Eleven claims of mine were falsified by measurement. The measurements survived every check; the
  explanations mostly did not. Three drafted patches were withheld; the one lever that shipped was
  caught inert by a ~$2 probe rather than eight paired runs.
