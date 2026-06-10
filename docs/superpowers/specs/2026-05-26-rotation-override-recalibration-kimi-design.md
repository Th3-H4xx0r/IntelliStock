# Rotation override recalibration for kimi-k2.5 — design

**Date:** 2026-05-26
**Status:** Draft for review
**Author:** forensic session (983687 regression)
**Model constraint:** production is locked to **kimi-k2.5 / Bedrock**. The fix must make kimi competitive; switching models is not an option.

## Problem

Successive backtests have regressed as config was tuned: 357345 = **+266%**, 404780 = +152%, 522929 = +131%, 983687 = **+113%** (all cold $7,000). The operator's read was "every iteration makes it worse."

A 12-agent forensic sweep of 983687 (+113%) vs 357345 (+266%) found two distinct causes, and showed the last four iterations were tuning the wrong knobs.

### Cause 1 — model confound (dominant, ~60–70% of the gap, NOT fixable here)
357345 ran on **gpt-5.4-mini-MEDIUM / Azure** for the decision roles (sentiment, overlay, event_maintenance, default). Every regressed run ran on **kimi-k2.5 / Bedrock**. Cross-model comparison is invalid; the high-water mark is partly a model (and possibly warm-start) artifact. Since prod is locked to kimi, this is a ceiling we measure, not a lever we pull. The chosen diagnostic backtest (983687 config on gpt-5.4-mini) will quantify it.

### Cause 2 — the V28 rotation lock-trap + dead override gates (fixable, the target of this spec)
On a cold $7k book, kimi fills the early slate with mediocre names (GILD +7%, SOXX +4%, AVGO +14%). Those positions lock the slate, and the eventual big winners — which kimi **does** surface — can never get capital:

- **SNDK** (357345's #1 winner, **+$1,877**) was discovered and ranked #1 repeatedly in 983687 but **never bought**. 8 of 357345's top-15 winners (**$8,533** total) were uncaptured by 983687; 5 of them (**$4,933**: SNDK, SIMO, RAMP, TWST, MRVL) were blocked-from-entry, not undersized and not unseen.
- Rotations fired **0/4 in 93% of cycles**. The block reasons in 983687: `profitable_min_hold` 2,504 (**52%**), `min_hold` 1,814, `profitable_hold` 1,051, `winner_lock` 677.

**Why the existing overrides don't help — the root mechanism:**
Conviction `raw_score` is **capped at 1.800** for both models (log `raw=` distribution: p90 = p95 = p99 = max = 1.800; 0 of ~17,000 scores exceed 1.8). The rotation override floors were calibrated to a distribution that reaches higher, so they are **mathematically dead**:

| Override gate | Config key | Floor | Reachable at 1.8 cap? |
|---|---|---|---|
| Ultimate break-glass | `rotation_break_glass_raw_score` | 3.50 (prod) / 2.75 (schema) | **No — dead** |
| `profitable_hold` final gate | `rotation_profitable_min_incoming_raw_score` | 2.0 | **No — dead** |
| γ.4 winner_lock bypass | `rotation_winner_lock_bypass_min_raw_score` | 1.8 | Only at the exact ceiling |
| top-momentum break-glass | `top_momentum_break_glass_raw_score` | 1.5 | Yes (needs `is_top_momentum`) |

And the **dominant blocker** — `profitable_min_hold` (52%; held < `rotation_profitable_full_exit_min_hold_days`=20 in bull, pnl ≥ 0, not winner-locked) — has **no conviction override at all**, only a peak-drop release that is **disabled by default** (`profitable_min_hold_release_enabled=false`). The winner_lock overrides (top_momentum / γ.4 / break_glass) live inside the `if winner_lock_active:` block at `graph_nexus_analysis.py:7617–7649` and are never evaluated for a `profitable_min_hold` hold.

gpt-5.4-mini won despite the same gates because it put its winners in **early** (via `backfill_rotation_buy`, before the slate locked) and rarely needed to repair a locked slate. kimi needs slate-repair and the override stack is too weak to do it.

### What the last four iterations got wrong (out of scope — do not touch)
- **Propagation tightening (A):** ~$0 impact. Counterfactual: every propagation winner scored ≥0.9, so the 40→20 slot / 0.50→0.56 score / seedcap-8 limits would have cut zero of them. The "HOOD flood" was GS (29 peers @ 0.375, none bought).
- **Winner-add depth (A):** near-dud — both runs pyramided trivially ($312 vs $2,054 on $7k); the loosened drawdown/multiple caps never fire as skip reasons (real blocker is no-cash). 
- **ETF allocation:** not a drag (~2.7% of capital, outperformed the stock book).
- **Buy-funnel width / concentration knobs:** inert (~161 buys regardless).
- **Exit logic:** fine — 357345 sold *more* and won; damage is failure to re-enter, i.e. Cause 2.

## Goal

Let kimi act on the high-conviction signal it already produces, by recalibrating the rotation override stack to the 1.8 score ceiling and giving `profitable_min_hold` a conviction escape — so a clearly-superior challenger can displace a marginally-profitable, merely time-locked incumbent. Recover a meaningful share of the **$4,933** in blocked-from-entry winners.

**Target:** > +130% on kimi as a first gate (clears 522929/404780); stretch +150–160%. The ~$2.6k of *never-discovered* names (VOYG/ICHR/SPIR) is model-bound and likely unrecoverable on kimi — 160%+ is at the edge of kimi's ceiling, which the diagnostic will confirm.

## Non-goals
- No model change (prod locked to kimi).
- No changes to propagation, winner-add, ETF, or funnel-width knobs.
- No ticker hardcoding — all changes are regime-/score-scoped and generalize.
- No new behavior for non-kimi deployments: the code override is flag-gated (default off).

## Design

Two coordinated parts, validated together in one backtest (operator's choice of scope).

### Part 1 — config knob recalibration (doc 179, merge-only, applied only after a passing backtest)

| Knob | From | To | Rationale |
|---|---|---|---|
| `rotation_winner_lock_bypass_min_raw_score` | 1.8 | **1.5** | Lets kimi's 1.5–1.8 conviction names (SNDK 1.65) pierce `winner_lock` vs sub-10%-pnl incumbents. The direct SNDK fix. |
| `rotation_break_glass_raw_score` | 3.50 | **1.5** | Revives the dead ultimate-override (0 scores ever reached 3.50). |
| `rotation_break_glass_delta` | 2.50 | **1.0** | Matches the achievable delta range once raw is reachable. |
| `rotation_profitable_min_incoming_raw_score` | 2.0 | **1.5** | Revives the dead `profitable_hold` final gate (0 scores ever reached 2.0). |
| `profitable_min_hold_release_enabled` | false | **true** | Activates the only existing escape from the 52% blocker (for *faded* holds). |
| `profitable_min_hold_release_peak_drop_pct` | 12.0 | **8.0** | Aligns the release with the `profitable_hold` peak-drop gate (8%). |
| `profitable_min_hold_conviction_override_enabled` (NEW) | absent | **true** | Enables the Part 2 code path (default off in code). |

All are existing knobs except the last (new, introduced by Part 2). Writes to doc 179 are merge-only (preserve all other keys + plaintext secrets), via an apply script mirroring `scripts/apply_doc179_winner_depth_fix.py`.

### Part 2 — `profitable_min_hold` conviction override (code)

**File:** `backend/strategies/graph_nexus_analysis.py`, function `_rotation_candidate_allowed`, inside the `if held_days is not None and held_days < profitable_full_exit_min_hold_days:` block (currently lines ~7676–7694), **before** the `return False, delta, "profitable_min_hold"`.

Add a flag-gated conviction path mirroring the γ.4 winner_lock bypass, for the *time-locked, not-winner-locked* branch:

```python
# Kimi-recalibration: the dominant rotation blocker (profitable_min_hold, 52%
# of rejections) had no conviction path — a max-conviction challenger (raw at
# the 1.8 model ceiling) could not displace a merely time-locked, marginally-
# profitable incumbent. Mirror the γ.4 winner_lock bypass for this branch.
# Flag-gated (default off) so non-kimi deployments are unaffected. No ticker
# hardcoding — score/pnl-scoped only.
if bool(config.get("profitable_min_hold_conviction_override_enabled", False)):
    _pmh_raw = float(config.get("profitable_min_hold_conviction_min_raw_score", 1.5) or 1.5)
    _pmh_delta = float(config.get("profitable_min_hold_conviction_min_delta", 1.0) or 1.0)
    _pmh_max_pnl = float(config.get("profitable_min_hold_conviction_max_held_pnl_pct", 10.0) or 10.0)
    if (
        float(incoming_raw_score or 0.0) >= _pmh_raw
        and delta >= _pmh_delta
        and float(held_pnl_pct or 0.0) < _pmh_max_pnl
    ):
        return True, delta, "profitable_min_hold_conviction_override"
```

**Guardrails baked into the path:** only fires for challengers at/above 1.5 raw with a full-point rotation-score delta, and only against incumbents below 10% pnl — genuine winners (pnl ≥ 10%) stay protected, exactly as the γ.4 bypass protects them. The default-off flag means the change is a no-op everywhere it isn't explicitly enabled.

### Data flow
No change to data flow. `_rotation_candidate_allowed` already receives `incoming_raw_score`, `delta` (incoming − held rotation score), and `held_pnl_pct`. The new branch reads three new config keys and returns the existing `(allow, delta, reason)` tuple with a new reason string for log attribution.

### Error handling
All new keys use `config.get(..., default)` with the same `or <default>` idioms as surrounding code; absent keys fall back to safe defaults (override disabled). No new external calls, no new failure modes.

## Testing (TDD — write tests first)

New file `backend/tests/test_rotation_override_recalibration.py`, exercising `_rotation_candidate_allowed` directly:

1. **SNDK-class case, override ON:** incumbent pnl +7%, held 8d (< 20d → `profitable_min_hold` branch), not winner-locked; challenger raw 1.65, delta 1.3 → expect `(True, _, "profitable_min_hold_conviction_override")`.
2. **Override OFF (default):** same inputs, flag absent → expect `(False, _, "profitable_min_hold")` (preserves current behavior — regression guard for non-kimi deployments).
3. **Genuine-winner protection:** incumbent pnl +15% (≥ max_held_pnl) → expect `(False, _, "profitable_min_hold")` even with the flag on and a high-conviction challenger.
4. **Sub-threshold challenger:** raw 1.4 (< 1.5) or delta 0.5 (< 1.0) → no override.
5. **Revived break_glass:** winner-locked incumbent, challenger raw 1.6 ≥ new 1.5 floor, delta 1.1 ≥ new 1.0 → `break_glass_trim` (or γ.4 bypass) fires; with old 3.50 floor it would not.
6. **`profitable_hold` gate revived:** held ≥ 20d, peak_drop ≥ 8%, challenger raw 1.6 ≥ new 1.5 `profitable_min_incoming` floor → allowed; at old 2.0 floor → blocked.

Run the full suite to confirm no new failures against the documented baseline (21 pre-existing failures = success):
`python3 -m pytest backend/tests/ -q --ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`

## Validation / backtest gate

1. **Implement** Part 1 (config values) + Part 2 (code) on branch `claude-code-integration`; unit tests green; full suite shows 0 new failures.
2. **Operator runs a cold backtest** of the candidate config on **kimi-k2.5**, same setup as 983687 (2025-11-01 → 2026-05-26, $7k, clean start). Compare vs 983687's +113%.
3. **Decompose the result:** Did the blocked winners (SNDK/SIMO/RAMP/TWST/MRVL) get bought this time? Grep the new log for `profitable_min_hold_conviction_override` and `break_glass_trim`/`gamma_winner_lock_bypass` firings, and confirm `profitable_min_hold` rejection count dropped from ~2,504.
4. **Churn guardrails — fail the gate if any trip:** rotations/day spikes implausibly; net winners *sold then re-bought higher* increases; or total return drops below +113%. The heavily-tuned anti-churn fixes (V31.7, γ.4, V28.4, V32 Phase 3 B-5) exist for a reason; the override must not reintroduce the churn they prevented.
5. **Apply to doc 179 only on a pass** (merge-only apply script). doc 179 is shared with nexus-live (real money) — no live apply without a green backtest, per operator instruction.

## Rollback

- **Code:** revert the Part 2 commit, or leave it in place and set `profitable_min_hold_conviction_override_enabled=false` (no-op).
- **Config (doc 179):** restore `rotation_winner_lock_bypass_min_raw_score=1.8`, `rotation_break_glass_raw_score=3.50`, `rotation_break_glass_delta=2.50`, `rotation_profitable_min_incoming_raw_score=2.0`, `profitable_min_hold_release_enabled=false`, `profitable_min_hold_release_peak_drop_pct=12.0`, and delete the three `profitable_min_hold_conviction_*` keys. Re-run the apply script with reversed values.

## Risks

- **Churn re-introduction (primary).** Lowering four conviction floors and adding a fifth override path could re-create the rotation churn that prior fixes suppressed. Mitigated by the held-pnl < 10% guardrail (protects real winners), the backtest churn guardrails above, and the default-off flag. If the backtest shows churn, narrow to gamma 1.8→1.5 alone.
- **Attribution.** Bundling config + code in one backtest (operator's choice) means a single number can't isolate which lever moved it. The log-grep decomposition (step 3) partially recovers attribution by counting which override path fired.
- **Ceiling.** Even a perfect slate-repair cannot recover the ~$2.6k of names kimi never discovered. +160% may be unreachable on kimi; the diagnostic backtest will bound the realistic target.
- **Heavily-layered code.** `_rotation_candidate_allowed` carries ~10 prior tuning fixes. The new branch is additive and flag-gated to minimize interaction risk, but the implementer must run `gitnexus_impact({target: "_rotation_candidate_allowed", direction: "upstream"})` before editing (per CLAUDE.md) and report the blast radius.

## Open operational question

How does the operator backtest a *candidate* config without mutating live doc 179? The prior session applied A+B to doc 179 *then* backtested (doc 179 is the backtest's config source). If backtesting requires writing the candidate knobs to doc 179 first, that conflicts with "backtest-gate before live." Resolve before step 2: use a separate test strategy doc / nexus-testing instance, or a backtest config override. (Operator-owned; SSH denied to assistant.)
