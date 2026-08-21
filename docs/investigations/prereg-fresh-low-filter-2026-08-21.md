# Preregistration: bear-leg fresh-20d-low filter (cold pair, window c)

Date: 2026-08-21. Registered BEFORE the runs. Motivated by the Opus path-analysis of
bt 712452/808739: the −7.4% drawdown (02-10→03-19) ran its entire 27-session course with the
hedge OFF-BOOK, because `residual_sleeve_bear_block_at_fresh_low_bars=2` refuses to open a
hedge while the proxy is within 2 bars of a fresh 20d low — and in a sustained decline the
proxy makes a new 20d low every 1-2 sessions. The filter blocked deploy on 03-14/16/17/18/20/
21/23/24, the hedge bought 03-25 (4 sessions AFTER the trough), and its +$433 landed entirely
in the recovery. The same filter then blocked re-entry on 04-01.

## Hypothesis
The filter is structurally self-blocking in exactly the regime the hedge exists for. Disabling
it moves the SQQQ deploy from 03-25 to ≈03-13 (bear declaration + dwell=2 satisfied), putting
the hedge on the book during the declining leg it is designed to monetize.

## Known risk (the filter's original purpose)
Opening a short-proxy at the bottom of a range: if the tape V-bounces immediately after a fresh
low, an early hedge loses. The 10% leg stop and 10%/5% trail bound that loss. The A/B measures
whether the protection is worth the 8 sessions of hedge it costs in a real bear.

## Lever
`residual_sleeve_bear_block_at_fresh_low_bars: 2 → 0` (treatment arm only), doc 195, window c
(2026-02-01..2026-04-01), $6,000, 3600s, standard cold protocol via run_paired_experiment.py.
Restore to 2 after the pair regardless of outcome.

## Endpoints (primary decides)
1. **PRIMARY (mechanism):** SQQQ deploy date moves earlier by ≥5 sessions (control replication
   ≈03-25 → treatment ≤03-18), and the hedge is ON BOOK for ≥5 sessions of the decline phase
   (peak→trough window as measured in the control).
2. **SECONDARY:** (a) max peak-to-trough drawdown shallower in treatment by ≥1pp;
   (b) return delta vs the 0.5pp cold floor.
3. **GUARDS (any breach = not adopted):**
   - SQQQ leg net P&L not worse than control by >20% (early entry must not turn the hedge into
     a loser via stop-outs — count leg stop-out events).
   - No hedge whipsaw churn: ≤2 SQQQ round trips in the window.
   - Long-book behavior unchanged (same trims; the lever touches only the sleeve deploy gate).
4. NOTE: window c overlap was 78% cold-READABLE for the max_positions pair; if this pair goes
   VOID on overlap, fall back to mechanism endpoints only (deploy dates are deterministic).

## Decision rule
Adopt iff PRIMARY fires, no guard breached, and drawdown endpoint (2a) met. A positive return
delta with the hedge still late = reject (mechanism failed). An earlier hedge that stops out
repeatedly = reject (the filter is load-bearing; document the measured cost of keeping it).

## Result (appended after the runs)
_pending_
