---
title: Phase η — Propagation Enrichment + Sector Cap Swap
status: APPROVED
date: 2026-05-20
target_bt: BT277953 follow-up
predecessors:
  - 2026-05-19-bt294837-phase-epsilon-execution-throughput.md
prior_pivot_from:
  - ζ.uncap (refuted by BT277953 re-trace agent: cap not the binding constraint)
---

# Phase η — Propagation Enrichment + Sector Cap Swap

## 1. Problem statement

BT277953 (commit b06e44f) produced 40.81% / +12.99% over 100+ bars but failed the
operator's stated goal: MU and LITE (high-momentum stocks) never bought; SNDK
bought briefly then evicted day 5. The user's hypothesis ("scores need to be
amplified so MU/LITE can be bought") led to Phase ζ.uncap, which was refuted by
parallel tracing agents:

1. **Aggregator math agent**: the [-1,1] cap rarely binds — single-path
   contributions (the majority) saturate at ±1.000 naturally, not via the clip.
2. **Conviction tier inflation agent**: naive uncap would inflate HIGH tier
   distribution 64.7% → 80-90%, widening sell floors on mid-quality stocks.
3. **Downstream consumer audit agent**: dormant gates (rotation_break_glass=2.75,
   gamma_winner_lock_bypass=1.8) would wake with no production telemetry.
4. **BT277953 specific re-trace agent**: 0 of 4 critical decisions changed under
   uncap. MU=1.500/PRAX=1.500/LITE=1.500 are *priority-floor* values, not
   aggregator outputs.

Subsequent Neo4j + log analysis revealed the actual structural realities:

- **MU was rotated IN successfully** on 2025-12-29 via
  `v28_hc_profitable_break_glass` (line 33384: `MU<-GOOGL`). It was then
  **demoted by V31 sector cap** because Technology was already over 40%
  (line 33387). The blocker is **post-rotation sector concentration**, not
  rotation-stage tie-breaking.
- **PRAX (Healthcare) bought and gained +13.2% on day 1**. The engine *can*
  surface and buy momentum tickers when sector cap doesn't fight it.
- **SNDK was evicted by V28.9 break-glass** via WBD pair — the ε.B grace
  tier-awareness work is bypassed by V28.9's separate code path. This is a
  closeable gap.
- **Neo4j has no sentiment edges**. Schema is purely structural (Company / ETF /
  Sector / Institution / HOLDS / COMPETES_WITH / IN_SECTOR / SUPPLIER_OF /
  ETF_HOLDS / etc.). Sentiment is runtime-only from LLM news analysis. Plans
  like "fetch sentiment from Neo4j" are non-starters at this schema.
- **MU has 262 COMPETES_WITH + 79 HOLDS edges**, LITE has 86 + 263, PRAX has
  0 + 8, SNDK has 102 + 6. The propagation engine already exploits these:
  log line 31514 shows `MU Graph(2 paths, raw=+0.300): 1-hop COMPETES_WITH(→INTC)`.

## 2. Goal

Five surgical patches that together address the *actual* BT277953 blockers,
ship in one atomic bundle, are individually kill-switchable, and rely on the
existing propagation machinery rather than parallel infrastructure.

Non-goal: ticker-specific hardcoding. Every mechanism is general — it operates
on the structural properties of any ticker that matches the momentum criteria.

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────┐
│  _compute_propagated_scores (existing)                       │
│                                                              │
│   sentiment_data (LLM-derived + trend_buy_signals at L20586) │
│        │                                                     │
│        ├─→ η.A: momentum_watchlist top-N → sentiment_data   │
│        ↓                                                     │
│   contributions[] (1-hop, sector, institutional, ...)        │
│        ↓                                                     │
│   Geometric aggregation → aggregated[ticker]                 │
│        │                                                     │
│        ├─→ η.B': low-raw momentum tickers ← sector peer aug │
└────────┼─────────────────────────────────────────────────────┘
         ↓
   raw_net_score (downstream scoring at L16766/L23865/L23976)
         │
         ├─→ η.E: floor + natural-signal differentiator
         ↓
   V28 rotation + V28.9 break-glass
         │
         ├─→ η.D: V28.9 refuses HIGH-tier-in-grace as LOSER
         ↓
   V31 sector portfolio cap
         │
         ├─→ η.G: conditional swap (sell existing weak, keep new strong)
         ↓
   Buy/sell execution
```

## 4. Components

### 4.1 η.A — Momentum-watchlist seeding into sentiment_data

**Location**: `backend/strategies/graph_nexus_analysis.py` ~L20593 (immediately
after the existing trend_buy_signals → sentiment_data merge).

**Mechanism**: bridge the momentum_watchlist pipeline (technical) into the
sentiment-seeding pipeline (currently only LLM-news-derived). MU/LITE/PRAX are
in momentum_watchlist but not in trend_buy_signals; this fixes the gap.

**Calibration**:
- Seed top N=5 of momentum_watchlist per bar (configurable)
- Threshold: min mw_score ≥ 0.05 (skips noise picks)
- Synthetic sentiment value: +1 (mirrors how existing trend_buy_signals seed)
- Event type: `"momentum_breakout"` (new event tag, distinct from
  `trend_momentum` so the `_EVENT_AMPLIFIERS` table can be tuned separately if
  needed)
- Skip if ticker already in sentiment_data with non-zero sentiment

```python
# After L20593, before propagation runs
if config.get("eta_momentum_seeding_enabled", True):
    mw_top_n = int(config.get("eta_momentum_seed_top_n", 5) or 5)
    mw_min_score = float(config.get("eta_momentum_seed_min_score", 0.05) or 0.05)
    mw = strategy_cache.get("_momentum_watchlist", {}) if isinstance(strategy_cache, dict) else {}
    if isinstance(mw, dict) and mw:
        sorted_mw = sorted(
            mw.items(),
            key=lambda kv: -float(kv[1].get("score", 0.0) or 0.0),
        )[:mw_top_n]
        seeded = 0
        for ticker, data in sorted_mw:
            if not isinstance(ticker, str) or not ticker:
                continue
            existing = sentiment_data.get(ticker)
            if existing and int(existing.get("sentiment", 0) or 0) != 0:
                continue
            score = float(data.get("score", 0.0) or 0.0)
            if score < mw_min_score:
                continue
            sentiment_data[ticker] = {"sentiment": 1, "event": "momentum_breakout"}
            mentioned.add(ticker)
            seeded += 1
        if seeded:
            _log(f"[ETA.A] seeded {seeded} momentum_watchlist tickers (top_n={mw_top_n}, min={mw_min_score})", "cyan")
```

**Downstream effect**: tickers in MU/LITE/PRAX-class get an additional direct
contribution `(+1.0, "direct: sentiment=+1 event=momentum_breakout")` plus they
become a SOURCE for 1-hop / sector / institutional propagation to their
neighbors.

### 4.2 η.B' — Post-aggregation sector-peer augmentation

**Location**: `graph_nexus_analysis.py` immediately after the aggregation loop
at L15413-L15423.

**Mechanism**: defensive belt-and-suspenders. For any ticker in
momentum_watchlist top-N whose post-aggregation `raw_score < threshold` but
whose best in-sector peer has `raw_score ≥ threshold`, augment the ticker's
raw_score with `peer_score × augment_factor`. NOT a parallel propagation source
— a post-aggregation additive that flat-bumps the final value with a distinct
reason tag.

**Why this is NOT double-counting**: existing IN_SECTOR / COMPETES_WITH
propagation feeds into `contributions[]` and goes through the geometric sum.
η.B' fires AFTER the sum, only when the geometric sum landed below threshold.
The augmentation is additive (not multiplicative), tagged with reason
`"eta_b_aug(peer=X)"`, and only adds a synthetic path count of 1.

**Calibration**:
- Threshold: `eta_augment_below_raw=0.5` — only tickers with low aggregated raw
- Augment factor: 0.3 — matches the `recurrence_bonus=0.5` scale
- Cap: result is clamped to `[-1.0, 1.0]` to preserve the existing aggregator
  contract

```python
# After the aggregation loop at L15423
if config.get("eta_sector_augmentation_enabled", True):
    augment_threshold = float(config.get("eta_augment_below_raw", 0.5) or 0.5)
    augment_factor = float(config.get("eta_augment_peer_factor", 0.3) or 0.3)
    aug_top_n = int(config.get("eta_augment_top_n", 5) or 5)
    mw = strategy_cache.get("_momentum_watchlist", {}) if isinstance(strategy_cache, dict) else {}
    if isinstance(mw, dict) and mw:
        mw_top = {
            t for t, _ in sorted(mw.items(), key=lambda kv: -float(kv[1].get("score", 0.0) or 0.0))[:aug_top_n]
            if isinstance(t, str)
        }
        sector_map = _build_sector_map_for_aug(driver, mw_top, session)  # NEW HELPER - cached in strategy_cache
        augmented = 0
        for ticker in mw_top:
            existing = aggregated.get(ticker, {"raw_score": 0.0, "reasons": [], "n_paths": 0})
            if float(existing.get("raw_score", 0.0)) >= augment_threshold:
                continue
            peer_score = _max_peer_raw_in_sector(ticker, aggregated, sector_map)  # NEW HELPER
            if peer_score < augment_threshold:
                continue
            augmented_raw = float(existing.get("raw_score", 0.0)) + peer_score * augment_factor
            aggregated[ticker] = {
                "raw_score": max(-1.0, min(1.0, augmented_raw)),
                "reasons": (list(existing.get("reasons", [])) +
                            [f"eta_b_aug(peer={peer_score:.2f}*{augment_factor})"])[:5],
                "n_paths": int(existing.get("n_paths", 0)) + 1,
            }
            augmented += 1
        if augmented:
            _log(f"[ETA.B] augmented {augmented} momentum tickers (threshold={augment_threshold}, factor={augment_factor})", "cyan")
```

**New helpers introduced for η.B'**:
- `_build_sector_map_for_aug(driver, tickers, session) -> dict[ticker, sector_name]`:
  Cypher `MATCH (c:Company) WHERE c.ticker IN $tickers MATCH (c)-[:IN_SECTOR]->(s) RETURN c.ticker, s.name`.
  Result cached in `strategy_cache["_eta_sector_map"]` for the run (does NOT
  persist across runs — sectors don't change mid-backtest).
- `_max_peer_raw_in_sector(ticker, aggregated, sector_map) -> float`:
  for the ticker's sector, find the max `raw_score` across all `aggregated`
  entries whose ticker's sector matches. Returns 0.0 if no peer found.
  Excludes the ticker itself.

### 4.3 η.D — V28.9 HIGH-tier-in-grace protection

**Location**: `graph_nexus_analysis.py` L22501-L22650 (V28.9 break-glass full
exit at cap loop).

**Mechanism**: inside the V28.9 pair iteration, before accepting `(loser,
winner)`, check whether the LOSER is HIGH-tier AND inside its grace window. If
so, refuse this pair and try the next one. Closes the gap where ε.B's grace
tier-awareness work is bypassed by V28.9's separate eviction path (SNDK got
evicted by WBD via this gap on day 5 of holding).

**Calibration**: reuses existing tier resolution
(`_resolve_conviction_tier_at_exit`) and grace check
(`_in_initial_grace_period`). No new thresholds. The protection only fires if
both conditions are simultaneously true, which is intentionally narrow.

```python
# Inside V28.9 pair loop (graph_nexus_analysis.py ~L22501-L22650),
# before accepting (loser, winner) pair:
if config.get("eta_v289_protect_high_grace_enabled", True):
    # Existing helpers (signatures already match V28.9 caller scope):
    loser_tier = _resolve_conviction_tier_at_exit(
        loser_ticker, scores, sentiment_data, propagated, config,
        portfolio_emulator=portfolio_emulator, prices=prices,
        date_key=date_key, strategy_cache=strategy_cache,
    )
    in_grace = _in_initial_grace_period(
        loser_ticker, portfolio_emulator, config,
        prices=prices, price_history=price_history,
        strategy_cache=strategy_cache, date_key=date_key,
    )
    if loser_tier == "HIGH" and in_grace:
        _log(
            f"[ETA.D] V28.9 refused pair: loser={loser_ticker} HIGH-tier in grace; trying next pair",
            "yellow",
        )
        continue  # try next pair without firing this one
```

**Implementation note**: `_resolve_conviction_tier_at_exit` lives at L5990-L6095;
`_in_initial_grace_period` lives at L4909-L4960. Their existing call sites in
the surrounding V28/V28.9 code can be cribbed for the exact argument tuple.

### 4.4 η.E — Priority floor differentiation

**Location**: `graph_nexus_analysis.py` L16766, L23865, L23976 (three sites
that apply `max(score, 1.500)`).

**Mechanism**: replace flat floor with `floor + min(0.20, max(0.0, score) × 0.5)`.
Items still get bumped to the floor (preserving the floor's purpose of making
momentum_watchlist picks compete with propagation HC), but the natural signal
strength is preserved as a small differentiator (max +0.20). Items that
naturally land above the floor are unaffected.

**Calibration**:
- Differentiator cap: 0.20 (keeps the differentiator below
  `nexus_high_conviction_threshold = 1.5` impact zone — 1.5 + 0.2 = 1.7 ≤
  natural HC ceiling of 1.8)
- Multiplier: 0.5 (linear scaling)
- Natural signal preserved in `raw_net_natural` for downstream consumers (η.G)

```python
# Replace at L16766 / L23865 / L23976:
# OLD: scores[ticker]["raw_net_score"] = max(score, 1.500)
# NEW:
_floor_val = 1.500  # existing constant
floored = max(score, _floor_val)
if config.get("eta_floor_differentiator_enabled", True):
    differentiator = min(0.20, max(0.0, score) * 0.5)
    scores[ticker]["raw_net_score"] = floored + differentiator
else:
    scores[ticker]["raw_net_score"] = floored
scores[ticker]["raw_net_natural"] = float(score)  # always recorded for η.G
```

**Validation against BT277953 bar 2025-12-29 (momentum_watchlist top3)**:
- MU mw_score=0.04 → floored=1.500, diff=0.020 → final=**1.520**
- PRAX mw_score=0.121 → floored=1.500, diff=0.061 → final=**1.561**
- BOIL mw_score=0.039 → floored=1.500, diff=0.020 → final=**1.520**

(Note: in BT277953 these floors are applied to the post-propagation
`raw_net_score`, not to the raw mw_score. With η.A seeding, MU's propagation
raw_score lifts from 0.300 to ~1.0; then η.E differentiator gives MU 1.5 + 0.20
= 1.700.)

### 4.5 η.G — V31 sector cap conditional swap

**Location**: `graph_nexus_analysis.py` V31 sector cap demote section
(near L5340-L5413, the `_apply_sector_cap` family).

**Mechanism**: when V31 sector cap wants to demote a new buy due to sector
saturation, check whether there's a weaker existing holding in the same sector
that we could SELL instead (1-for-1 swap, sector concentration unchanged).
This unlocks the MU case where MU's rotation was successful but V31 reverted it.

**Swap eligibility for the candidate target (existing holding to sell)**:
- Same sector as the demoted new buy
- Effective score `raw_net_score + age_boost` STRICTLY less than the new buy's
  effective score
- Hold age ≥ 3 days (prevents churn on freshly-bought positions)
- Current P&L ≤ +15% (don't sell large profits to make room)
- NOT in grace period (don't override grace protection)
- NOT HIGH-tier-in-grace (η.D-style protection extended here)

If multiple existing holdings qualify, pick the one with lowest effective score
(weakest first). If none qualify, demote the new buy as today.

**Calibration knobs**:
- `eta_v31_swap_enabled` — kill switch (default True)
- `eta_v31_swap_min_hold_days=3` — cooldown
- `eta_v31_swap_max_pnl=0.15` — profit-protection
- `eta_v31_swap_eligible_sources={momentum_watchlist, propagation_expansion}` —
  only certain buy sources qualify for swap behavior (not every new buy)

```python
# Inside V31 sector cap demote, after identifying `to_demote` list
if config.get("eta_v31_swap_enabled", True):
    swap_min_hold = int(config.get("eta_v31_swap_min_hold_days", 3) or 3)
    swap_max_pnl = float(config.get("eta_v31_swap_max_pnl", 0.15) or 0.15)
    eligible_sources = set(config.get(
        "eta_v31_swap_eligible_sources",
        ["momentum_watchlist", "propagation_expansion"],
    ))
    rescued: list[str] = []
    swap_sells: list[tuple[str, str]] = []  # (existing_to_sell, new_to_keep)
    for new_buy in list(to_demote):
        source = scores[new_buy].get("signal_source", "")
        if source not in eligible_sources:
            continue
        new_effective = (
            float(scores[new_buy].get("raw_net_score", 0.0))
            + float(scores[new_buy].get("age_boost", 0.0))
        )
        new_sector = sector_of(new_buy)
        candidates = []
        for existing in portfolio_emulator.held_tickers():
            if sector_of(existing) != new_sector:
                continue
            ex_score = scores.get(existing, {})
            ex_effective = (
                float(ex_score.get("raw_net_score", 0.0))
                + float(ex_score.get("age_boost", 0.0))
            )
            if ex_effective >= new_effective:
                continue
            ex_age = portfolio_emulator.age_days(existing)
            if ex_age < swap_min_hold:
                continue
            ex_pnl = portfolio_emulator.pnl_pct(existing)
            if ex_pnl > swap_max_pnl:
                continue
            if _in_initial_grace_period(existing, ...):
                continue
            if _resolve_conviction_tier_at_exit(existing, ...) == "HIGH" \
                    and _in_initial_grace_period(existing, ...):
                continue
            candidates.append((ex_effective, existing))
        if not candidates:
            continue
        candidates.sort()  # weakest first
        _, target = candidates[0]
        swap_sells.append((target, new_buy))
        rescued.append(new_buy)
        to_demote.remove(new_buy)
        _log(
            f"[ETA.G] V31 conditional swap: sell existing {target} (eff={candidates[0][0]:.3f}) "
            f"to keep new buy {new_buy} (eff={new_effective:.3f}) in sector={new_sector}",
            "magenta",
        )
    # Apply swap sells through existing sell-enforcement path
    for target, _ in swap_sells:
        nexus_sell_enforcement.add(target)
        scores.setdefault(target, {})["raw_net_score"] = 0.0  # neutralize hold
```

**Validation against BT277953 bar 2025-12-29 (approximate)**:
- MU demoted by V31 sector cap (line 33387). Tech holdings on that bar include
  NVDA, AAPL, AIQ, BOTZ, INTC, T, QCOM, etc.
- Rotation-eval log lines show `held=INTC(...,rot=0.315)`,
  `held=AAPL(...,rot=-0.960)`, `held=T(...,rot=0.025)` — these are the
  *rotation scores* (different from raw_net_score), but they're a strong proxy
  for relative strength. INTC, AAPL, T are clearly the weakest Tech holdings.
- MU effective = `raw_net_score + age_boost` = 1.500 + 0.400 = 1.900
- For the swap candidate, η.G compares against existing holdings'
  `raw_net_score + age_boost`. If any held Tech ticker's effective score is
  below 1.900 AND it passes the cooldown/profit/grace gates, η.G swaps.
- High-confidence expected outcome: INTC (or T, or AIQ — whichever has the
  lowest effective and clears the gates) is sold; MU is kept; Tech sector
  composition stays at 40%.
- The "approximate" qualifier is because the log shows `rot` not raw_net_score
  for held tickers; precise validation requires re-instrumenting the BT or
  reading the per-ticker score history. The swap-decision logic is robust to
  this — it picks whichever weakest qualifies.

## 5. Kill switches (config)

All default ON; can be disabled per-component to isolate regressions:

- `eta_momentum_seeding_enabled` (η.A)
- `eta_sector_augmentation_enabled` (η.B')
- `eta_v289_protect_high_grace_enabled` (η.D)
- `eta_floor_differentiator_enabled` (η.E)
- `eta_v31_swap_enabled` (η.G)

Plus tuning:
- `eta_momentum_seed_top_n=5`, `eta_momentum_seed_min_score=0.05`
- `eta_augment_below_raw=0.5`, `eta_augment_peer_factor=0.3`, `eta_augment_top_n=5`
- `eta_v31_swap_min_hold_days=3`, `eta_v31_swap_max_pnl=0.15`
- `eta_v31_swap_eligible_sources=[momentum_watchlist, propagation_expansion]`

## 6. Telemetry

All components emit `[ETA.X]` log prefixes (grep-able in BT logs):
- `[ETA.A] seeded N momentum_watchlist tickers (top_n=X, min=Y)`
- `[ETA.B] augmented N momentum tickers (threshold=X, factor=Y)`
- `[ETA.D] V28.9 refused pair: loser=X HIGH-tier in grace; trying next`
- `[ETA.E]` is implicit — `raw_net_natural` recorded on each score (visible in
  rotation-eval logs that print raw_net_score)
- `[ETA.G] V31 conditional swap: sell existing X (eff=Y) to keep new Z (eff=W)`

Plus per-bar summary lines:
- `[ETA] summary: seeded={A.count} augmented={B.count} v289_refused={D.count} swaps={G.count}`

## 7. Test plan (~28 unit tests)

Located at `backend/tests/test_phase_eta.py` (new file):

### 7.1 η.A tests (5)
- `test_eta_a_seeds_momentum_top_n_unseen` — top-3 momentum ticker not in
  sentiment_data → gets sentiment=1, event=momentum_breakout
- `test_eta_a_skips_already_seeded` — momentum ticker with existing LLM
  sentiment is NOT overwritten
- `test_eta_a_respects_min_score` — mw_score < threshold → no seed
- `test_eta_a_kill_switch` — disabled → no seeding, no logs
- `test_eta_a_skips_empty_watchlist` — no momentum data → no error

### 7.2 η.B' tests (5)
- `test_eta_b_augments_low_raw_with_high_peer` — mw ticker raw=0.2, peer
  raw=0.8, factor=0.3 → final=0.44, +1 n_paths
- `test_eta_b_skips_high_raw_tickers` — mw ticker raw=0.7 ≥ 0.5 threshold → no
  augment
- `test_eta_b_skips_when_no_peer_above_threshold` — best peer raw=0.3 < 0.5
  → no augment
- `test_eta_b_clamps_to_one` — already raw=0.9, peer=0.9, +0.27 → clamped to
  1.0
- `test_eta_b_kill_switch`

### 7.3 η.D tests (5)
- `test_eta_d_refuses_high_in_grace` — V28.9 pair with HIGH-tier loser day 3 →
  refused, loop continues
- `test_eta_d_allows_non_high_in_grace` — LOW-tier loser day 3 → V28.9 fires
  as today
- `test_eta_d_allows_high_post_grace` — HIGH-tier loser day 10 → V28.9 fires
- `test_eta_d_tries_next_pair_after_refuse` — first pair refused, second pair
  accepted
- `test_eta_d_kill_switch`

### 7.4 η.E tests (5)
- `test_eta_e_differentiator_zero_for_zero_score` — score=0 → final=1.500
- `test_eta_e_differentiator_below_cap` — score=0.1 → diff=0.05, final=1.55
- `test_eta_e_differentiator_at_cap` — score=0.5 → diff=0.20 (cap), final=1.70
- `test_eta_e_no_op_above_floor` — score=1.8 → final=1.8 (no floor applied)
- `test_eta_e_records_natural` — raw_net_natural always set

### 7.5 η.G tests (8)
- `test_eta_g_swaps_when_weaker_exists` — new tech buy demoted; weaker held
  tech (eff=0.3) sold instead
- `test_eta_g_does_not_swap_when_all_held_stronger` — held tickers all stronger
  → demote as today
- `test_eta_g_respects_min_hold_days` — weaker held ticker but age=2d → not
  eligible, no swap
- `test_eta_g_respects_max_pnl` — weaker held but pnl=+20% → skipped
- `test_eta_g_does_not_sell_grace_period_holding` — weaker held in grace →
  protected
- `test_eta_g_picks_weakest_first` — multiple eligible targets → weakest sold
- `test_eta_g_only_for_eligible_sources` — non-momentum new buy → no swap
- `test_eta_g_kill_switch`

## 8. BT validation plan

Single BT run (BT-η-001) on BT277953 dataset with full η bundle enabled.
Look for in log:

1. `[ETA.A] seeded N momentum_watchlist tickers` — every bar with non-empty mw
2. `MU.*Direct momentum_breakout` — confirms MU seeded as sentiment source
3. `MU (Graph(N paths, raw=X)` with N > 2 — confirms more contributions vs
   baseline N=2
4. `[ETA.D] V28.9 refused pair: loser=SNDK` somewhere in days 4-7 — confirms
   protection fired
5. SNDK in held set on day 6+ — confirms protection effective
6. `[ETA.G] V31 conditional swap: sell existing X to keep new buy MU` —
   confirms swap on MU bar
7. MU in BUY rows at 2025-12-29 — confirms end-to-end success
8. LITE in BUY rows around 2025-12-19 — same pattern
9. PRAX bought (already worked in baseline; should still buy)

Comparative metrics vs baseline BT277953:
- HIGH-tier population: should remain ~64-65% (no inflation)
- Sector concentration: Tech still ≤40% (η.G is 1-for-1 swap, no net change)
- Total rotation count: marginal increase (η.G swaps add some sells)
- P&L: target ≥+13% (baseline)

## 9. Rollback strategy

Per-component kill switches allow surgical disable. Recommended rollback order
if regression detected:

1. First disable η.G (largest behavior change, most novel) — restores V31 sector
   cap to current behavior
2. Then disable η.B' (defensive duplication; least load-bearing)
3. Then disable η.A (revert to LLM-only seeding)
4. η.D and η.E are minimal-risk; disable last

Full bundle disable: set all 5 flags to False in config — engine reverts to
pre-η behavior with zero code-path differences (the changes are all
config-gated).

## 10. Out of scope (explicitly dropped)

### 10.1 ζ.uncap (Phase ζ aggregator cap removal)
Refuted by parallel agents — cap rarely binds in practice; 0 of 4 BT277953
decisions would change. Conviction tier inflation risk made it net negative.
See `2026-05-19-bt294837-phase-epsilon-execution-throughput.md` for prior
analysis.

### 10.2 η.B — Synthetic sector inheritance (original framing)
Redundant. Existing IN_SECTOR (L15136-L15147) and COMPETES_WITH (L15060-L15119)
in `_compute_propagated_scores` already implement peer-to-target propagation.
MU's `Graph(2 paths, raw=+0.300): 1-hop COMPETES_WITH(→INTC)` (BT log L31514)
proves this path works today. Building a parallel synthetic-inheritance layer
would risk double-counting. η.B' (post-aggregation augmentation) is the
defensive version that doesn't duplicate the propagation graph traversal.

### 10.3 η.C — On-demand Neo4j sentiment fetch
Infeasible at current Neo4j schema. The graph has NO sentiment edges. Verified
via direct Neo4j probe:
- 12 labels: Commodity, Company, ETF, GovAgency, GraphBuildStatus,
  GraphEdgeInterval, Index, Institution, LEIEntity, LegalEntity, Sector, Theme
- 17 relationship types: COMPETES_WITH, CONTRACTS_WITH, CONTROLS,
  EDGE_INTERVAL_SOURCE, EDGE_INTERVAL_TARGET, ETF_HOLDS, ETF_TRACKS_SECTOR,
  ETF_TRACKS_THEME, EXPOSED_TO, HOLDS, IN_SECTOR, PARENT_OF, PARENT_OF_ENTITY,
  PATENT_PARTNER, STRATEGIC_PARTNER, SUPPLIER_OF, SUPPLIES_TO_SECTOR
- None of these store sentiment.

Sentiment is runtime-only, derived by `_enhanced_sentiment_from_llm` at
L13459. A future Phase μ (multi-session) could explore writing
LLM-derived sentiment back to Neo4j as a cached "synthetic sentiment edge"
between Company and Theme/Sector — but this is a substantial architecture
change and not a one-shot BT validation candidate.

### 10.4 η.F — Sector cap demotion tiebreak by natural signal
Dropped after BT277953 evidence showed MU's blocker was V31 sector cap demote,
not floor tiebreak. The V31 cap demote works on the ONLY new Tech buy (MU)
regardless of tie-breaking. η.G addresses this directly.

## 11. Risks & mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| η.G increases portfolio churn | M | Cooldown (3d min hold) + profit-protect (max +15%) + grace-protect |
| η.A seeds inflate sentiment_data noise | L | Only top-N with score > 0.05; mw is already filtered |
| η.B' augmentation makes mid-quality tickers HIGH-tier | L | Clamped to ≤1.0; only fires when raw < 0.5 |
| η.E differentiator nudges items into HC threshold (1.5) zone | L | Cap of 0.20 keeps below natural HC ceiling of 1.8 |
| η.D leaves more HIGH-tier losers in held set | L | Only blocks V28.9; other sell paths still active |
| All 5 components interact unexpectedly | M | Kill switches + per-component telemetry + 28 unit tests |
| Single BT run can't reveal subtle issues | M | Unit tests are cheap; comprehensive coverage of edge cases |

## 12. Decision log

- **2026-05-19 initial scope**: A+B+C+D+E+F (6 components, 850 lines).
- **2026-05-20 Neo4j probe**: confirmed no sentiment edges. η.C dropped as
  infeasible. η.B simplified to η.B' post-aggregation augmentation.
- **2026-05-20 BT log replay**: revealed MU was rotated IN, then V31 demoted.
  η.F dropped (not the real blocker), η.G added for V31 conditional swap.
- **Final**: A+B'+D+E+G (5 components, ~250 lines, 28 tests).
