# Strategy X Bear System Plan — Adversarial Review

**Date:** 2026-08-25
**Reviewed:** `docs/superpowers/specs/2026-08-25-strategy-x-bear-system-design.md` and `docs/superpowers/plans/2026-08-25-strategy-x-bear-system.md`
**Verdict:** approved for implementation after revisions

## Review method

An independent read-only reviewer attacked the plan for lookahead, next-bar
timing, state resets, target accounting, ownership, broker coexistence,
off/shadow parity, search leakage, data availability, and promotion-claim risk.
The first pass rejected the plan with nine critical and ten important findings.
The design and plan were revised, then re-reviewed twice. The final focused pass
found every load-bearing defect resolved.

## Material changes caused by review

- Simulate each arm continuously from common inception; slice windows from the
  ledger so positions, pending orders, and cache state cross boundaries.
- Make close `t` visible at a defined post-close decision time and fill exactly
  once at trading row `t+1`; mutation tests prohibit future-row signal leakage.
- Authorize active targets only under `run_once(mode="backtest")`.
- Track bear-position provenance, unwind it across mode/config changes, and
  invalidate an active research run when provenance is irrecoverably lost.
- Inject a broker residual-sleeve/SQQQ collision flag so two allocators cannot
  claim the kicker.
- Preserve baseline satellite ranking and isolate shadow-only fetched prices
  from its investability view; dynamic role collisions refuse the overlay.
- Use finite numeric parsing for every lookback and counter.
- Select candidates only from a non-overlapping pre-2023 lattice, freeze the
  candidate and input digest, then compute holdout results for that candidate
  alone. Named windows remain strict post-selection gates.
- Call 2023+ a locked holdout/pseudo-out-of-sample check, not unseen validation,
  and disclose current-survivor managed-futures selection bias.
- Separate defense and SQQQ promotion verdicts so a failed kicker cannot reject
  a passing defensive sleeve.
- Retain and hash the normalized price matrix, invalidate missing required
  slices or missed fills, and align SPY to the same entry/cost convention.
- Reconcile sliced component P&L as ending market value minus starting market
  value, plus net sales, minus buys, without double-counting costs.

## Final gate

The final reviewer confirmed the four last load-bearing concerns were resolved:

1. broker residual-sleeve/SQQQ coexistence;
2. total provenance loss;
3. shadow price-universe A/A parity; and
4. sliced component-P&L identity.

This approval covers implementation readiness, not investment merit. Promotion
still depends on the frozen empirical gates, and failure leaves the subsystem
default-off.
