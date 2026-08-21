# Preregistration: passive_execution_enabled (cold pair, window f)

Date: 2026-08-21. Registered BEFORE the runs.

## Why re-test a rejected lever
`result-execution-cost-sizing.md` rejected passive execution by REASONING, not measurement, and
its stated reason for not measuring was that the effect "would be invisible in a return
comparison that sits inside the ~10pp dispersion". The dispersion is now 0.5pp (cold protocol),
and the stated risk — entries pushed later — is now directly measurable with `entry_lag.py`.
This does not touch any DO-NOT-RETRY entry.

## Lever
`passive_execution_enabled: absent → true` (treatment arm only), `passive_expire_quotes` left at
default 8. Doc 195. Orders rest at the decision price instead of crossing a measured 22.8 bps
half-spread. Window f (chop, 2026-06-15..2026-08-01) — the turnover-heaviest window (303%/6wk)
where crossing cost is largest and the strategy currently loses cold (−2.70% vs SPY +0.69%).

## Protocol
`scripts/run_paired_experiment.py --instance v2-conv-trt --doc 195 --start 2026-06-15
--end 2026-08-01 --cash 6000 --granularity 3600 --treatment passive_execution_enabled=true`
then RESTORE: `--unset passive_execution_enabled --apply`.

## Endpoints (primary decides)
1. **PRIMARY (mechanism):** the treatment log shows the passive path actually engaged — the
   `[passive] limit execution ENABLED` line at start AND ≥1 resting fill or visible expiry.
   Crossing cost avoided (passive fills × 22.8 bps × notional) ≥ 20% of control's crossing cost.
2. **SECONDARY:** return delta vs 0.5pp cold floor.
3. **GUARDS (any breach = not adopted):**
   - Entry lag: treatment median entry lag not worse than control by >1 day.
   - Unfilled/expired limits: if >25% of buy attempts expire unfilled, the "position you wanted
     and did not get" risk is confirmed — reject regardless of return.
   - SQQQ BUY notional not reduced.

## Decision rule
Adopt iff PRIMARY fires, no guard breached, secondary ≥ −0.5pp. Else reject with the measured
mechanism recorded.

## Result (bt 980932 control / bt 871653 treatment)
**REJECTED — and the first-pass reading of this pair was WRONG and is corrected here.**

First pass (now retracted): "the lever is inert in the backtest harness" — based on the absence
of any `[passive]` log line. The deep path analysis falsified that with fill forensics: the
CONTROL pays a constant ±22.9 bps against next-bar mid on 14/14 fills (marketable), while the
TREATMENT fills at exactly the anchor price, zero spread, with orders resting ACROSS BARS (one
never filled). The flag rewrites the entire fill model in backtests; it simply has no log line
on the backtest path (the `[passive]` banner is wired only into the live boot). The
scope-perturbation hypothesis is also ruled out: identical scope ids, 100% discovery overlap —
the 45% book divergence cascades entirely from ONE unfilled day-1 SPY order (control was 40%
SPY-core from 15:00; treatment held 0% core for a full session).

Verdict against the preregistered endpoints:
- PRIMARY: the passive path engaged (resting fills confirmed) — fires, but with no banner
  (an unlogged lever, the documented anti-pattern).
- GUARDS: **breached, decisively.**
  1. **Risk exits rest unfilled.** COPX's circuit breaker fired on 11 consecutive bars
     (6/24 15:00 → 6/25 13:00) before filling — a stop-loss that cannot stop. Unbounded risk
     in a fast market. This alone kills the lever as implemented.
  2. Day-1 entries cost MORE, not less (~$15 worse: anchor sat above next-bar mid).
  3. Resting orders double-count the turnover budget (superseded order + reissue both count):
     budget-binding ticks 55% → 77%, further starving an already-starved book.
- SECONDARY: −0.08pp nominal; irrelevant next to the guard breaches.

**Disposition: passive execution stays OFF. Do not re-test until (a) risk exits are exempted
from passive routing, (b) the backtest path logs the banner, (c) resting orders stop
double-counting the turnover budget.** The 22.8 bps crossing saving is real but this
implementation spends more than it saves.

Additional path findings from the pair (both arms): the entire −3% loss is the day-1 copper
entry at its top (−$193/−$199) plus zero give-back protection (control's CVLT round-tripped
+22.9% → −6.1%); the turnover governor blocked buys on 55%/77% of ticks and `funded 0 of N` on
~30 of 35 batches — in this window the book is STARVED, not churned. Same-config cross-time
drift confirmed separately: bt 209809 (morning) vs bt 980932 (evening), identical config, cold,
different books (−1.22% vs −2.94%) — arms must be back-to-back.
