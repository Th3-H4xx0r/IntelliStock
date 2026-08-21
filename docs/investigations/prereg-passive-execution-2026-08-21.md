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

## Result (appended after the runs)
_pending_
