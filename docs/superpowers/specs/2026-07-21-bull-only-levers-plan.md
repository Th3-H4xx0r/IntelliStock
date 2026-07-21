# Bull-Only P&L Levers — Plan (2026-07-21)

**Goal:** raise BULL-regime P&L, keeping the bear window byte-unchanged. Every lever is regime-scoped (`_market_regime == "bull"`) so it cannot alter the validated bear (+2.29%, bt 726941). Validated one-at-a-time on the local harness (`scripts/local_backtest.py`, `--assert-bear-unchanged`), never shipped as a blind bundle (the prior entry-gate bundle regressed both windows).

## Evidence (from three forensic passes over bt 148462, the +6.60% bull run)

The bull gap vs SPY was mechanics, not selection (ex-SQQQ deployed capital returned 13.8% ≈ SPY). Three recoverable, bull-scoped sources:

1. **A −17.3% drawdown-KILL fired in *confirmed bull*** (ret20 +7.87), liquidating the whole 14-name book into the closing rally, then a 15% halt locked cash out for ~2 bars; several liquidated names (VIAV, RKLB) then rallied.
2. **Winners were starved into the BFQ drain at the $100 floor** — AMD (+66%) and RKLB got $100/$143 slots (~$170 forfeit each); the anchor-reinforce target math zeroes adds on fast winners.
3. **A #1-scored queue name (INTC, +97%) went unfunded 13 sessions** — its only displaceable holding (USL at −1.7%) sat 0.3pp above the −2.0 CONVERT threshold, so the partial-trim rotation couldn't convert to a full exit at cap.

## Ranked candidates (each bull-gated, default-neutral, validated separately)

| # | Lever | Config key(s) → value | Code site | Bear-safety | Est. |
|---|---|---|---|---|---|
| **3 (first)** | CONVERT loss threshold, bull-only | `v32_convert_min_loss_pct_bull` = −1.0 | gna.py:~25379 (`_convert_min_loss_threshold`) | **Structural**: bear losers full-exit, never reach the convert gate (0 V28.8.1 skips in bear log) | +$600 (INTC), narrow window |
| **1** | Drawdown-circuit + halt widen, bull-only | KILL 12→16, hard 9→12, soft 5→7, halt 15→18, resume 2→1 (guarded `regime=="bull"`) | gna.py:20264-20480 (`_apply_portfolio_drawdown_halt`) | Bear's 2-slot book never reaches the circuit (0 circuit events in bear log) | +2–5pp (biggest gap) |
| **2** | BFQ conviction sizing floor, bull-only | `backfill_high_conviction_min_raw_bull` 1.5→1.0, `backfill_min_alloc_high_conviction_bull` $100→~$300, `backfill_alloc_fraction_bull` 0.5→0.75 | gna.py:8163-8205 (`_plan_backfill_buy_allocation`) | regime-blind today → add `=="bull"` guard; bear buys blocked by Bear-RS gate | +1–2.5pp |
| **4** | Anchor-reinforce target %, bull-only | `anchor_reinforce_target_pct_bull` 0→8–12 | gna.py:8835-8840 | regime-blind → bull guard | +0.5–1.5pp |
| **5** | Hold windows, bull-only (free) | `rotation_min_hold_days_bull` 10→15, `rotation_profitable_full_exit_min_hold_days_bull` 20→28 | gna.py:8271-8277 | **already bull-scoped** (bear/chop take their own branches) | +0.2–0.8pp |

## Status / workflow

- **Candidate #3 SHIPPED** (bull-gated helper `_convert_min_loss_threshold`, default −2.0 = byte-identical no-op; 5 unit tests). Off until `v32_convert_min_loss_pct_bull` is set.
- Validation is **blocked on infra** (Tailscale/RethinkDB down); the harness preflights and will run when it returns.
- Each candidate ships default-neutral, then is A/B'd on the harness: **bull known + OOS windows must rise; every bear window must stay within ±0.6pp** (`--assert-bear-unchanged`). Only levers that pass advance; the rest are reverted. Order: #3 → #1 → #2 → #4 (#5 is free, folds in with any).
- Faithful-verify caveat (project memory): the harness runs the real PortfolioEmulator, so it does not over-state like the old bt_lib fast harness.
