# Recovery-capture regime timing — design

**Date:** 2026-07-24
**Author:** autonomous session (Claude)
**Status:** design → offline-sim validation → implementation
**Live-safety:** doc-179 = alpaca-main LIVE config (wake OFF). Every lever here is
default-off / gated so the config is byte-identical until explicitly enabled.

## Problem

The regime AUTO-SWITCH (commit 423c2ac) overlays bull-v9 levers in a confirmed
bull and a bear-defensive base otherwise. On the **transition window bt#252937
(2026-03-02 → 04-27, +7.9%)** the strategy makes its money in the bear leg (the
SQQQ hedge = the +5.92% pure-bear baseline, bt#504479) but **under-participates in
the V-shaped recovery**.

### Root cause (faithful 0/41 replay-validated, Agent B)

- Market bottomed **2026-03-30**; bull first confirmed **2026-04-13** → **10-trading-day lag**.
- **8 td of the lag = the trailing-20-day-return window.** `raw=bull` needs
  `ret_20d > 0`; `raw=bear` fires at `ret_20d < -regime_bear_spy_drawdown_pct`
  (doc-179 = **3**, not the code default 5). The stale late-March crash losses kept
  `ret20` negative for ~8 td even as QQQ V-recovered ~+10% (by 04-02 price was already
  +4.8% off the low with `ret5` +1.6%).
- **2 td of the lag = `regime_upgrade_confirm_bars=3`** hysteresis dwell.
- Non-factors: the structural `<200d MA` bear branch **never fires** (N<200 closes);
  `regime_recovery_override_enabled` is a **no-op for V-bottoms** — its `price > 20d MA`
  reclaim guard is False on every bear bar because the 20d MA is propped up by pre-crash
  highs, and by the time price clears the 20d MA, `ret20 > -3` so the override branch
  no longer runs.

### Cost of the lag (Agents A/C/D)

- While pinned bear/chop, the Z4.1 position cap = **2 (bear) / 8 (chop)**. ~**49% of
  the whole recovery** happened while capped at 2 (03-31→04-07), ~82% in cash (~$5,470
  idle incl. the $4,069 freed from the hedge). 100 `REGIME CAP HARD BLOCK` events.
- Two harms of cap=2: (i) blocks new recovery entries (~$583 of blocked winners —
  MSTR/MARA/CLSK/META/NVDA/AIQ/BDC), and (ii) the V31.3 breach auto-heal **force-sold
  existing longs down to 2 at the bottom** (incl. a winner, AIT +0.4%; winner-lock only
  protects P&L ≥ 15%).
- Idle-cash opportunity cost ≈ **$240–$547**; deploying at the bottom plausibly makes
  the run **~+12–16% instead of +7.9%**.
- Double-edged caveat: v9's `entry_extension_block_pct=0` is what let the CAR +166%
  chase through (bought 04-21, CB-stopped −19.5%, −$144). So "just flip to bull v9
  earlier" **adds** downside — the fix must not re-arm that gate earlier.

## Goal

Capture materially more of a genuine V-recovery (target: meaningfully above +7.9% on
bt#252937, ideally low-double-digits) by lifting the position cap faster on a
**confirmed** recovery, WITHOUT:
- touching bull-v9 byte-identity (profit-take 70/120/170, `entry_extension_block_pct=0`,
  the peak-defense flags),
- regressing the pure-bear window (bt#504479, +5.92%),
- re-arming the CAR-style chase (keep the bear-defensive extension block during recovery),
- overfitting to this one window (gate on signals, not dates; validate generality).

This is a **regime-TIMING** fix (the open, unsolved lane), NOT an entry-gate / bull-alpha
fix (disproven — do not retry).

## Design (recovery → chop fast-track)

Reuse the existing `chop` regime (cap 8, bear-defensive base levers → no CAR risk, no new
regime state). Three changes, all default-off / identity when the keys are absent:

### 1. Parameterize the recovery-override reclaim MA
`_detect_market_regime` (graph_nexus_analysis.py ~L6245-6260): the reclaim guard
currently hardcodes the 20-day MA (`_ma20 = sum(closes[-20:]) / 20` and `current > _ma20`).
Replace the window with `regime_recovery_ma_bars` (default **20** = byte-identical;
set **10** to fix). Rationale: near a V-bottom the 20d MA is still elevated by pre-crash
highs so it never gets reclaimed; the 10d MA is reclaimed within a day or two of a genuine
turn. The `off_low` depth guard and `ret5` thrust guard are unchanged (the dead-cat filter).

### 2. Recovery fast-confirm (skip the dwell for bear→chop only)
`_apply_regime_hysteresis` (graph_nexus_analysis.py ~L6343): add
`regime_recovery_fast_confirm_enabled` (default **False**). When True AND the current raw
is a recovery-produced `chop` (detected via `strategy_cache["_market_regime_diag"]`
carrying a `recovery` marker) AND `cur == "bear"`, upgrade to `chop` **immediately**
(bypass the k-bar dwell). Constraints:
- ONLY bear→chop; a bull upgrade still requires the normal `regime_upgrade_confirm_bars`.
- NEVER on a blind bar (the existing blind-freeze guard runs first).
- The asymmetric **immediate downgrade on any fresh decline is unchanged** — a dead-cat
  that resumes falling drops back to bear within one bar.

### 3. Cascade (no new code, verified in sim)
Once the regime confirms `chop`: the Z4.1 cap is 8, so the breach auto-heal no longer
force-sells down to 2, and new recovery names can be held/entered on the bear-defensive
base. The SQQQ sleeve does not re-hedge (deploy is gated to bear/crash; the hedge already
exited on its peak-trailing stop and the fresh-decline gate blocks re-park).

### Optional (only if the sim shows it's needed)
- `regime_upgrade_confirm_bars` 3→2 (recovers the residual 2-td hysteresis; low risk).
- A recovery-specific deployment ramp if `deployment_ramp_chop_scale=0.6` throttles the
  cap-8 deployment too slowly to capture the leg.

## Config levers (doc-179.strategies[0].config — NOT top-level config)

| key | default | recovery setting | role |
|---|---|---|---|
| `regime_recovery_override_enabled` | False | **True** | master gate (existing) |
| `regime_recovery_ma_bars` | 20 | **10** | NEW: reclaim MA window |
| `regime_recovery_fast_confirm_enabled` | False | **True** | NEW: skip dwell bear→chop |
| `regime_recovery_ret5_min_pct` | 2 | (sim-tuned) | thrust floor (existing) |
| `regime_recovery_off_low_pct` | 0.5 | (sim-tuned) | depth guard / dead-cat filter (existing) |
| `regime_recovery_bull_ret5_pct` | 5 | keep 5 (no early bull) | thrust for recover→bull (existing) |

## Validation plan (offline sim BEFORE any backtest)

Using Agent B's faithful replay harness (`regime_B.py`, 0/41 match to the log):
1. **Recovery capture:** on the 252937 window, the candidate settings must flip
   bear→chop several td earlier than 04-08/04-13 (target ~04-02/04-03, right after the
   03-30 bottom). Report the exact cap-lift date and the extra recovery days captured.
2. **Pure-bear safety:** on 03-02→03-30, the candidate must produce **zero** flips out of
   bear (else the pure-bear +5.92% is at risk). Hard gate.
3. **Generality / dead-cat:** replay ≥1 other bear window with intra-bear bounces; the
   signal must not false-flip on a dead-cat that resumes falling.
Only settings that pass all three go to implementation + a real backtest.

## Testing

- Extend `backend/tests/test_regime_profile.py` (or a new `test_recovery_capture.py`):
  - `regime_recovery_ma_bars` absent → detector byte-identical to today.
  - `regime_recovery_fast_confirm_enabled` False → hysteresis byte-identical.
  - With the recovery settings, a synthetic V-bottom series upgrades bear→chop on the
    reclaim bar (no dwell); a synthetic dead-cat (bounce then lower low) does NOT flip,
    or flips then immediately downgrades.
- Full suite green: `test_residual_sleeve.py test_regime_profile.py
  test_conviction_bear_alloc.py test_phase_alpha_variance.py` (baseline 154 pass).

## Rollout

Default-off code to main (deploy branch); doc-179 patched with the recovery levers
(backup first via apply_doc179_config_patch.py). Verify image rebuild
(`grep -c "LLM prompt cache ENABLED (deterministic)"`), then same-range backtest
(2026-03-02→04-27) vs the +7.9% baseline. Keep pure-bear reconfirmation in reserve.
