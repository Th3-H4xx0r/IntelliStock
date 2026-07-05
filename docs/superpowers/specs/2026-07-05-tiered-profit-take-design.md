# Tiered partial profit-take + turnover-hole close — design

**Date:** 2026-07-05
**Instance:** alpaca-main / Strategies doc-179 (graph_nexus_analysis), model now `nvidia/nemotron-3-ultra-550b` (reverted from mini). Live instance STOPPED.
**Goal (user):** capture more P&L by (#3) banking a portion of a winner's gain while keeping the rest running, (#1) trading less, (#2) concentrating into winners.

## Evidence / motivation

- Across every June backtest dissected this session, **buying the day-1 basket and holding beat the actual traded result** (185254 +1.45% B&H vs +0.73% actual; 642016 +2.08% B&H vs −0.07%). Closed round-trips consistently lose ~−$4k (mini) / −$1k (Nemotron); profit comes only from unrealized winners still held at month-end.
- **The give-back is the visible symptom:** 642016 rode to +$3,052 (Jun 22) then round-tripped to ~$0 — it never banked the peak.
- **#2 is already configured aggressively** on doc-179: `allocation_profile="conviction"`, `winner_add_enabled=true` (`winner_add_max_count=8`), `nexus_portfolio_pct=0.95`, fast deployment ramp. No change this round.
- **#1 is already tight** (`rotation_min_hold_days=30`, `sell_enforcement_min_hold_days=15`, break-glass `raw_score=3.5`/`delta=2.5`). The one remaining turnover hole is `llm_sell_conviction_bypass_enabled=true`, which lets a high-conviction LLM sell bypass those min-holds. On mini it fired on everything (blanket −1.000); on Nemotron it is not needed. Close it.
- **#3 is the real gap.** Profit-take is `profit_take_enabled=false` (fully off) AND the code (`graph_nexus_analysis.py:17379-17404`) fires **once per position** (marks `_nexus_profit_take_state[sym] = entry_key`, then skips). A two-stage scale-out is impossible without a code change.

## Changes

### A — Tiered profit-take (code; default-preserving)
Extend the profit-take block (`graph_nexus_analysis.py:17379-17404`) to support a new config `profit_take_tiers`: a list of `[gain_pct, sell_fraction]` pairs, evaluated ascending. Each tier fires **at most once per entry** when `unrealized_pct >= tier.gain_pct`. On a bar, fire the **lowest un-fired tier** the current gain has crossed — one tier per bar — so scale-out is progressive (a gap-up clearing both tiers sells the +20% tier this bar, the +40% tier next bar), never dumping the whole position at once.

**`sell_fraction` semantics (unchanged from today):** the fraction is applied to the *current* remaining holding, not the original entry. So to end up **keeping ⅓ running** with two ⅓-of-original trims, the tiers are `[[20, 0.33], [40, 0.5]]`: tier 1 sells 0.33 of the full position (→ keep 0.67); tier 2 sells 0.5 of the remaining 0.67 (≈ another ⅓ of original → keep ~0.34).

- **State:** upgrade `_nexus_profit_take_state[sym]` from a bare entry-key string to `{"entry": <entry_key>, "fired": [<gain_pct>, ...]}`. A changed entry-key (re-entry) resets `fired`. Tolerate the legacy string form (treat as single-tier fired) so in-flight caches don't break.
- **Fraction plumbing:** the fired tier's `sell_fraction` must reach the existing partial-sell sites (`:17473`, `:17678`, `:17686`, which currently read the global `profit_take_sell_fraction`). Store the fired fraction per symbol for the bar (e.g. `_profit_take_fraction_by_sym[sym]`) and have those sites prefer it, falling back to the global fraction.
- **Backward compatible:** if `profit_take_tiers` is absent/empty, behaviour is exactly today's single-fire `profit_take_gain_pct`/`profit_take_sell_fraction` path. Whole block still gated by `profit_take_enabled` (default false).
- **Telemetry:** `Profit take tier TRIGGER: {sym} +{gain}% >= tier +{t}% (sell {frac})` and a SKIP line for already-fired tiers.
- **Schema:** register `profit_take_tiers` (default `[]`) and `profit_take_enabled` (default false) in the `INTELLISTOCK_SCHEMA` header (both currently absent — matches the PR-92 convention so they are editor-settable).

### B — doc-179 config (applied at next live start; validated first)
- `profit_take_enabled = true`
- `profit_take_tiers = [[20, 0.33], [40, 0.5]]`  (trim ⅓ of original at +20%, another ⅓ at +40% — sell 0.5 of the remainder — keep ⅓ running)
- `llm_sell_conviction_bypass_enabled = false`  (#1 hole)
- `#2` unchanged.

### Explicitly NOT changed
- `#2` concentration knobs (already aggressive).
- The `#1` hold/rotation knobs (already tight) beyond closing the bypass hole.
- Model (stays Nemotron).

## Validation protocol (credit-frugal — user is credit-constrained; Nemotron runs ≈ $5-6 and upstream can be flaky)

The change is judged **mechanistically first, aggregate P&L second** (aggregate has ~1.6pp run-to-run noise that swamps small effects):
1. **1 Nemotron June backtest** with config B on. PASS gates:
   - Profit-take telemetry fires at the +20% and +40% tiers on real winners (grep `Profit take tier TRIGGER`).
   - A named winner that round-tripped in a prior run (e.g. a +20%+ name) now banks a partial gain instead of giving it all back — per-trade check.
   - No regression signature: total realized loss not worse, no new churn.
2. Only if the mechanism looks right and budget allows: a 2nd run to sanity-check the distribution. Do NOT run a large A/B stack — the effect is not resolvable against the noise at that cost, and profit-take's benefit is a per-trade property we can verify directly.
3. Enable on doc-179 only after step 1 passes. Config B is a real-money change; it applies at the next live start.

## Testing (unit)
`backend/tests/test_tiered_profit_take.py`:
- Two-tier config `[[20,0.33],[40,0.5]]`: gain crosses +20% → sells 0.33, marks tier 20 fired; next bar at +25% → no further fire; at +40% → sells 0.5, marks tier 40; at +50% → no fire (both done).
- Gap-up clearing both tiers on one bar fires only the higher-un-fired tier that bar (one tier per bar).
- Re-entry (new entry_key) resets fired tiers.
- Legacy string marker in state is tolerated (no crash; treated as fired).
- `profit_take_tiers` absent → identical to current single-fire behaviour (equivalence).
- Fraction plumbing: the sell carries the fired tier's fraction, not the global.

## Risk
The code change is behind `profit_take_enabled` (default false) and `profit_take_tiers` (default empty) — zero behaviour change until doc-179 opts in. GitNexus impact before editing; full-suite bisect head-vs-base for zero new failures; parallel bug-sweep. Config B is a real-money change but the live instance is stopped, so nothing trades until the user restarts after reviewing the validation run.
