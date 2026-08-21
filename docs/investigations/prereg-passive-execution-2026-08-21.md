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
**THE LEVER IS INERT IN THE BACKTEST HARNESS — the A/B cannot be run as a backtest at all.**
The treatment logged zero passive lines (no `[passive] limit execution ENABLED` banner, nothing).
Both `set_passive_execution` call sites (broker.py ~9689, ~10979) are in the LIVE boot path;
the backtest boot path never calls it. The 2026-08-03 commit message said "per-doc so it can be
A/B'd" — the wiring never reached the harness the A/B would run in. PRIMARY endpoint fails by
vacuity; no adoption question can be posed until the flag is wired into the backtest boot (or
judged directly in paper, where the wiring exists).

Two protocol findings the accident produced (both consequential):
1. **A semantically-identical config pair diverged to 45% overlap in chop** (control −2.94%,
   treatment −3.02%). The arms differed only by the PRESENCE of an inert key. Chop draw
   instability (and possibly config-hash→scope-id perturbation) produces VOID-scale divergence
   with no real lever at all — hard confirmation that chop pairs cannot be read on returns.
2. **Same-config cold runs hours apart differ**: bt 209809 (morning, −1.22%, CPER/EEM/COPA
   book) vs bt 980932 (evening, −2.94%, COPJ/COPX/CVLT/CSCO/GCMG book). Shared
   article/sentiment caches evolve with wall-clock time, so cold comparability requires
   BACK-TO-BACK arms — which run_paired_experiment.py provides. Never reuse a control across
   pairs (validated: tonight's fresh-control choice was load-bearing).
