# Preregistration: bear-refill appreciation guard (COLD pair, window c)

Date: 2026-08-22. Registered BEFORE the runs. Queued behind the displacement pair.

## Motivation (measured leak)
Window-c sweep, treatment arm: `residual_bear_refill` sold 31.5 SQQQ shares at avg 75.94 INTO a
monotonically rising leg to restore the cash floor, re-bought 26.6 at avg 76.53, and arrived at
the +16.5% payoff stretch with a **7.7% smaller hedge** despite 7 extra sessions of exposure.
De-risking a working hedge into strength is a structural tax on the one validated edge.

## Lever
`residual_sleeve_bear_refill_skip_min_leg_gain_pct: 0 → 3` (treatment arm only): the cash-floor
refill stands down while the leg is ≥3% above entry; the leg trail (10%/5%) and stop (10%)
still govern risk. Default 0 = byte-identical legacy. Key whitelisted in
_residual_sleeve_config (the inert-lever trap), guard logs its fire.

## Protocol
API-only cold pair (window c is bear = readable): clear → run per arm via
run_paired_experiment (no snapshot needed; window c cold books historically overlap 67-78%).
Window c 2026-02-01..2026-04-01, $6,000, 3600s.

## Endpoints (primary decides)
1. **PRIMARY (mechanism):** treatment logs ≥1 `bear refill SKIPPED — leg appreciating` AND the
   treatment's SQQQ share count at the leg's peak-day is ≥ control's (the leak reversed).
2. **SECONDARY:** SQQQ leg net P&L delta; then return delta (floor per overlap tier).
3. **GUARDS:** no cash-floor breach cascade (deploys blocked for lack of cash on >5 extra
   bars); leg stop-outs not increased; long book untouched.

## Decision rule
Window 1 of ≥2 (window f bear-leg activity is thin; the second window can be a warm window-d
bear phase if one exists, else defer). Adopt only on mechanism + leg-P&L improvement with
guards clean in both.

## Result
_pending_
