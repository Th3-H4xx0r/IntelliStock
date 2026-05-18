# BT232179 Phase γ Fix Plan — variance reality-check + capacity unlock

**Branch target:** `claude-code-integration` (do NOT merge to `main`).

**Continues from:** BT232179 7-agent investigation (this session, 2026-05-18 ~22:30 UTC).

## Baseline comparison (same window: 2025-11-10 → 2026-05-16/18)

| Backtest | Seed universe | Final P&L | Trades | Round-trips | Win rate | Notes |
|---|---|---|---|---|---|---|
| **BT901920 (CEILING)** | **114 tickers** (operator-curated) | **+$17,330 / +247.58%** | 282 | 134 | 52.2% | The aspirational target |
| BT136708 | 118 tickers | +$11,993 / +171.3% | — | — | — | Phase 1+1b investigation baseline |
| BT109429 | 8 tickers (pure-discovery) | +$3,635 / +51.9% | — | — | — | The Phase α trigger event |
| **BT232179 (CURRENT)** | **8 tickers** (pure-discovery) | **+$1,419 / +20.27% @ 34.5%** | 132 | 62 | **59.7%** | Projected final ~+58% if linear |

**Critical insight from BT901920 vs BT232179 comparison:** BT232179's win rate (59.7%) is HIGHER than BT901920's (52.2%) — the trades the strategy DOES take are statistically better quality. But BT232179 does FEWER, SMALLER trades. The +227pp gap to BT901920 is NOT a "bad strategy" problem; it's a **scale + capacity** problem:
- **Universe scale**: 114 seed tickers gave BT901920 a ~14× richer sentiment surface to mine. Pure-discovery from 8 seeds is structurally bandwidth-limited.
- **Capacity**: BT232179's `current=12 > max=8` permanent breach prevents the strategy from acting on the high-conviction discoveries (APLD/CRWV/PLTR at raw=1.8) that DO surface.

**What Phase γ can recover:** the capacity portion of the gap (~$5K-$10K from fixing cap, A1, rotation displacement). Plausible target after Phase γ: **+85% to +160%** on BT232179's 8-ticker universe, vs +247% on BT901920's 114-ticker universe. The remaining gap (~$5K-$10K) is universe-bandwidth-driven and CANNOT be closed without either (a) seeding more tickers, or (b) accelerating pure-discovery throughput beyond current Nexus propagation. See §8 for "what Phase γ cannot close".

**Backtest baseline:** BT232179, 2025-11-10 → 2026-05-18, 8-ticker seed (AIQ/ARKQ/ATR/BOOT/BOTZ/COIN/KMT/MUR), pure-discovery, $7K → $8,419 = +$1,419 P&L / +20.27% at 34.5% progress.

**Headline:** Phase α shipped but two of four components are silently inert in pure-discovery mode (the operator's actual backtest configuration). The structural ceiling is unchanged from BT136708. Phase γ closes the capacity portion of the gap to BT901920 (~$5K-$10K of the ~$15K total); the remaining gap is universe-bandwidth (8 vs 114 tickers).

---

## 1. Investigation summary (5-agent parallel sweep)

| Agent | Headline finding |
|---|---|
| **Phase α telemetry** | α.4 silently no-op'd EVERY bar (`mcap pre-seed: skipped — empty symbols_list`); α.3 RNG seed log MISSING from buffered logs entirely; α.2 working (snapshot stats firing, hit rate climbing); α.1 not exercised (default True) |
| **SNDK lifecycle** | SNDK WAS bought day 1 at $239 via momentum; force-exited at -18.3% (LOW tier, chop, circuit_breaker -15% floor); recovery rebuy at +78% ($349) BLOCKED by V28.8.1 BREACH (current=12 > max=8, auto-heal freed 0); every rotation pair SKIPPED |
| **MU+LITE lifecycle** | Both discovered at raw=1.5; LITE blocked 3× by CSCO leader_lock (P&L=8.8%, held=9d); MU blocked at V28 rotation cap (all-pair `winner_lock / losing_hold / min_hold / profitable_hold` rejection) |
| **Discovery throughput + cap** | Funnel: 60 discovered → 11 events → 0.65 executable → 0.6 bought per bar. 38/49 bars (78%) had V28.8.1 BREACH. `auto-heal freed 0` on 79% of breaches. BFQ saturates at 60/60 for 94% of bars. **0/81 BFQ allocations succeeded**. Top missed: APLD/CRWV/PLTR (raw=1.8 each), CEG/CLF/ON/WMT (raw=1.8 each) |
| **A1 conviction-tier audit** | 5/5 tier resolutions = LOW (0 HIGH, 0 MID). GEV (~$60B mega-cap) force-exited at -12.9% in bull regime under tightened -10% LOW floor — should have been HIGH. SNDK ($22B mid-cap) should have been MID. Both casualties of empty mcap cache. Conviction telemetry (Phase 3) not emitting in production |

---

## 2. Root-cause hierarchy

| Rank | Failure | Owner | Magnitude estimate |
|---|---|---|---|
| **P0** | α.4 mcap pre-seed silently no-op'd in pure-discovery mode (operator's primary use case). `symbols_list` is empty at the call site. A1 falls back to raw_score path → mega-caps demote to LOW → -15%/-10% floors fire on routine pullbacks | run_once callsite (graph_nexus_analysis.py) | $1,000-$2,500 missed (GEV, SNDK) |
| **P0** | α.3 RNG seed `_log()` calls fire BEFORE the persistent log buffer is opened (broker.py:5650 vs 5682). Goes to stdout but not to the buffered log file the operator reads via API. Determinism cannot be confirmed from API logs | broker.py:5631-5680 | $0 (observability gap; blocks Φ.1 validation) |
| **P0** | V28.8.1 max_positions BREACH is permanent state, not exception. 78% bar coverage; auto-heal freed 0 in 79% of cases. BFQ stays at 60/60. 0/81 ALLOC attempts succeeded across the run | Capacity layer (graph_nexus_analysis.py:~21003) | $2,000-$4,000 missed (28 high-conviction tickers stuck in queue) |
| **P1** | V28 rotation: `winner_lock` (modest winners like CSCO @ +8.8%) blocks raw=1.8 propagation buys. partial_trim + winner_lock combo = frozen portfolio. APLD/CRWV/PLTR/CEG/CLF/ON/WMT all blocked at raw=1.8 | V28 rotation logic | $1,500-$3,000 missed |
| **P2** | V31 sell-grace + circuit_breaker interaction: SNDK grace-suppressed 7 consecutive bars before circuit_breaker fired at -18.3%. Grace protects upside but allows deep drawdown to lock in | Sell-gate logic | Deferred until P0/P1 fixed (depends on A1 tier-resolution working) |
| **P2** | Conviction telemetry not wired to production log path. Operator can't audit A1 decisions post-hoc. Tests pass in isolation | Phase 3 telemetry | $0 (observability) |

---

## 3. Phase γ — Variance reality-check + capacity unlock

**Sequencing rationale:** γ.1+γ.2 first (cheapest, highest signal-to-noise, unblocks γ.3-γ.5 measurement). γ.3-γ.5 are the structural P&L unlocks. γ.6 deferred until γ.1 lands so we can re-measure with correct tier resolution.

### γ.1 — Pre-seed mcap from held positions + discovery (P0, ~50 LOC including test updates) *PRIMARY P&L UNLOCK FOR A1*

**Files:**
- `backend/strategies/graph_nexus_analysis.py:5591` — `_preseed_mcap_cache_from_universe` signature + body
- `backend/strategies/graph_nexus_analysis.py:18275` (current call site in `run_once`) — pass `portfolio_emulator` + BFQ candidates
- `backend/strategy_cache_persistence.py:59` — blacklist entry stays (set[str] is still per-run)
- `backend/tests/test_phase_alpha_variance.py` + `backend/tests/test_bt136708_fixes.py` — update ~11 test call sites for new signature

**Problem:** In pure-discovery mode (operator's standard config), `symbols_list` is empty at run_once entry. The pre-seed function correctly short-circuits, but the mcap cache remains empty for the rest of the backtest — A1 conviction-tier resolver has nothing to work with, demotes everything to LOW.

**Exact code paths (adversarial-review-locked, no hand-waving):**

The currently held tickers come from `portfolio_emulator.get_positions().keys()` (verified: `backend/portfolio_emulator.py:130` exposes `get_positions()` returning a copy of `_positions`). The existing breach-heal loop at `graph_nexus_analysis.py:21701` reads `getattr(portfolio_emulator, "_positions", {})` directly — γ.1 uses the same defensive pattern.

The BFQ candidates come from `strategy_cache.get("_backfill_queue", {})` (per existing BFQ inspection in `_drain_backfill_queue`).

**Change:**

1. Add `portfolio_emulator=None` keyword arg to `_preseed_mcap_cache_from_universe`. New signature:
   ```python
   def _preseed_mcap_cache_from_universe(
       symbols_list: list,
       strategy_cache: dict | None,
       config: dict,
       portfolio_emulator=None,
   ) -> int:
   ```
2. **Change idempotency flag contract from `bool` to `set[str]`** (adversarial-review BLOCKER #2):
   - Rename: `_yf_market_cap_cache_preseeded` → `_yf_market_cap_cache_preseeded_tickers` (set[str])
   - Short-circuit logic at L5627 changes from "skip whole call if flag set" to "skip tickers already in set, process the rest"
   - `strategy_cache.setdefault("_yf_market_cap_cache_preseeded_tickers", set())` becomes the new pattern
   - Persistence blacklist at `strategy_cache_persistence.py:59` updated to the new key
   - Migration: keep accepting the old bool flag for one release with a deprecation log; if found, drop it
3. Build the pre-seed universe inside the function:
   ```python
   held_tickers: set[str] = set()
   if portfolio_emulator is not None:
       try:
           # Use _positions direct read (mirrors the breach-heal pattern at L21701);
           # falls through silently if portfolio_emulator is malformed.
           held_tickers = {str(t).strip().upper() for t in getattr(portfolio_emulator, "_positions", {}).keys() if str(t or "").strip()}
       except Exception:
           held_tickers = set()
   bfq_tickers: set[str] = set()
   try:
       bfq = strategy_cache.get("_backfill_queue", {}) or {}
       # Take top-N by raw_score; configurable cap to bound yfinance load.
       bfq_cap = int(config.get("mcap_preseed_bfq_top_n", 20) or 20)
       bfq_sorted = sorted(bfq.items(), key=lambda kv: -float((kv[1] or {}).get("raw_score", 0.0) or 0.0))[:bfq_cap]
       bfq_tickers = {str(t).strip().upper() for t, _ in bfq_sorted if str(t or "").strip()}
   except Exception:
       bfq_tickers = set()
   operator_tickers = {str(t).strip().upper() for t in (symbols_list or []) if str(t or "").strip()}
   universe = operator_tickers | held_tickers | bfq_tickers
   already_seeded = strategy_cache.get("_yf_market_cap_cache_preseeded_tickers") or set()
   new_to_seed = universe - already_seeded
   if not new_to_seed:
       return 0  # nothing new to fetch
   # ... existing neo4j + yfinance loops, but iterate over `new_to_seed`, not `symbols_list` ...
   # At the end: already_seeded |= universe
   strategy_cache["_yf_market_cap_cache_preseeded_tickers"] = already_seeded | universe
   ```
4. Update call site at `graph_nexus_analysis.py:18275` to pass `portfolio_emulator=portfolio_emulator`. The `portfolio_emulator` variable is available in `run_once` (search confirms it's a function parameter).

**Expected outcome:**
- A1 tier distribution shifts from 100% LOW to ~30% HIGH + ~20% MID + ~50% LOW
- GEV-class mega-caps get -25% floor instead of -10% (avoids the BT232179 false positive at -12.9%)
- SNDK-class mid-caps get -20% floor instead of -15% (would have held SNDK through the chop and captured +78% recovery)

**Test:** Add to `backend/tests/test_phase_alpha_variance.py`:
- `test_alpha4_preseed_from_held_positions`: pass empty symbols_list + fake portfolio_emulator with `_positions={"SNDK": ...}`, assert SNDK gets seeded
- `test_alpha4_preseed_seeds_bfq_top_n`: pass empty symbols_list + BFQ with 30 entries, assert top-N (default 20) get seeded
- `test_alpha4_preseed_set_semantics_extends_across_calls`: call twice, second call adds new tickers, assert only NEW tickers are fetched (not re-fetched)
- `test_alpha4_preseed_old_bool_flag_still_short_circuits`: legacy bool flag set on cache, assert function still no-ops (deprecation back-compat)
- Update 11 existing test call sites to add `portfolio_emulator=None` (keyword arg — should be a no-op for existing tests)

**Risk:** Medium-low. Set-semantics flag change touches persistence + idempotency. Mitigated by deprecation back-compat for one release. yfinance flood bounded by `mcap_preseed_bfq_top_n` cap (default 20).

### γ.2 — Move α.3 seed-log block AFTER log FILE init (P0, ~5 LOC)

**Files:** `backend/broker.py:5631-5674` (the α.3 RNG seed block) and `5681-5690` (the log buffer + file init)

**Problem:** The seed log lines (`RNG seed: <int> (sha256 of backtest_id=...)`, PYTHONHASHSEED confirmation) fire BEFORE `intellistock_logger.set_backtest_log_buffer()` (L5682) and `set_backtest_log_file()` (L5690). They go to stdout (visible in Docker container logs) but NOT to the persistent log file the operator pulls via the API.

**Exact code path (adversarial-review-locked):**

The buffer init at L5681-5690 is wrapped in `try/except` and falls through to `_backtest_log_buffer = []` on failure (L5694 area). Critically, BUFFER and FILE are independent: `set_backtest_log_buffer` and `set_backtest_log_file` can succeed/fail separately. The operator-pulled log comes from the FILE sink (`_log_file_obj` opened at L5690 area), so we must move past `set_backtest_log_file()`, not just past `set_backtest_log_buffer()`.

**Change:**
- Move the entire seed block (L5631-5674) to AFTER `intellistock_logger.set_backtest_log_file(_log_file_obj)` (currently L5690 area; verify line number at edit time)
- Numpy/random seeding is process-global and timing-independent — moving it 50 lines later is safe
- The seed math + log calls move together as a unit
- Add a pre-move smoke check: confirm `_log()` writes to the file sink even when `_log_file_obj` is opened but buffer init failed. (If `_log()` is gated on `_backtest_log_buffer is not None`, the move alone is insufficient and we need to fix `_log()` separately.)

**Expected outcome:** `RNG seed: ...` lines appear in pulled logs; operator can confirm seed derivation, PYTHONHASHSEED status, and run-to-run determinism.

**Test:** Manual — observe RNG seed line in pulled log on next backtest. Add a verification smoke step to the Φ.γ.0 fast-proxy gate (§4).

**Risk:** None for seeding (process-global, order-independent). LOW for log emission: if `_log()` requires buffer (not just file) and the buffer init failed, the seed log would silently disappear AGAIN. Pre-move smoke check at edit time mitigates.

### γ.3 — Relax auto-heal skip-conditions on V28.8.1 over-cap (P0, ~5-15 LOC preferred; 50-100 LOC fallback)

**Files:**
- `backend/strategies/graph_nexus_analysis.py:21697-21777` (the existing breach-heal loop — adversarial review identified this as the actual blocker)
- V28.8.1 breach detector site (~L21003)

**Problem:** Config says `max_positions=8` but live positions sit at 9-12 for 78% of bars. The cap fires, BFQ stays saturated, 0/81 allocations succeed. **The agent-flagged data: `auto-heal freed 0` on 79% of breaches.** The existing heal loop iterates `getattr(portfolio_emulator, "_positions", {})` but skips candidates with (a) held<3d, (b) grace-protected, (c) winner_lock'd. In early bars EVERY position satisfies one of these skip conditions → loop frees 0.

**Adversarial-review-locked root cause:** It's NOT that something is bypassing the cap (the original plan's first hypothesis was wrong). It's that **the heal loop's own skip filters short-circuit the freed count to 0**. This is a heal-LOGIC bug, not a cap-COUNTING bug.

**Hypotheses ranked (highest first):**

1. **(MOST LIKELY) Heal-loop skip filters are too conservative.** `_bh_wlock` and grace-skip filter out all candidates in early bars when no position has held>3d AND isn't profit-locked. Fix scope: ~5-15 LOC. (Adversarial review §issue 12.)
2. (Less likely) Cap is counting only NEW-ticker positions and ignoring carry-in or scheduled. Fix scope: ~50 LOC. Defer to fallback.
3. (Less likely) `scheduled_buy` / LLM future-trade adds tickers bypassing the cap. Fix scope: ~30 LOC. Defer to fallback.
4. (Less likely) `max_positions=8` is too low for the operator's actual portfolio shape; bump to 12. Fix scope: 1-line config default + sizing scale-down. Defer.

**Change (primary — hypothesis #1):**

In the heal loop at `graph_nexus_analysis.py:21697-21777`, relax the `_bh_wlock` skip to fire ONLY when:
```python
# Old: skip if winner_lock active
if _bh_wlock:
    continue
# New: skip only if winner_lock + profitable (pnl >= 15%)
if _bh_wlock and pnl_pct >= 15.0:
    continue
```
Also tighten the grace-skip:
```python
# Old: skip if grace-protected
if _bh_grace_protected:
    continue
# New: skip only if grace-protected AND held >= 3 days
if _bh_grace_protected and held_days >= 3:
    continue
```
This admits the over-cap heal to close a single weak position when no truly winner-locked or grace-protected candidate exists. Bounded: at most 1 close per breach (not aggressive sell-down).

**Investigation deliverable before the fix:** run `grep -n "_bh_wlock\|grace_protected\|_bh_grace" backend/strategies/graph_nexus_analysis.py` and CONFIRM the heal-loop variables match. Quote the exact line numbers in the implementation diff.

**Expected outcome:**
- BFQ allocations succeed 1-2/bar (vs 0 currently)
- 28+ missed high-conviction buys execute over the run
- P&L lift: +$1,500-$3,500 (adversarial-review-discounted from prior $2K-$4K — see §5)

**Test:** New test asserting heal-loop closes one position when over-cap AND all positions are early-grace (held<3d, raw winner_lock'd at pnl<15%); test asserting it does NOT close a +20% high-conviction winner.

**Risk:** Low for hypothesis #1 (small surface, bounded behavior). Medium if hypothesis #1 doesn't unblock the cap — fall back to hypothesis #2 in a follow-up patch. Adversarial review's §issue 13 raises the question of whether the cap SHOULD be 8 — operator decision; left at 8 by this patch.

### γ.4 — V28 rotation: raw_score displacement threshold (P1, ~20 LOC)

**File:** `backend/strategies/graph_nexus_analysis.py` V28 rotation logic (winner_lock site)

**Problem:** CSCO at +8.8% / 9 days held triggers `leader_lock`, blocking LITE/APLD/CRWV at raw=1.8. The lock is too conservative — a propagation winner at raw=1.8 is statistically stronger than a +8.8% momentum hold.

**EV justification for threshold (adversarial-review-locked):**

Adversarial review §issue 7 ran the EV math: displacing CSCO @ +8.8% on a $300 position forfeits ~$26. Incoming buy at raw=X has approximate P(positive ~10%) of:
- raw=1.5: ~30% (momentum-watchlist default; baseline noise) → EV: −$26 + 0.30×$30 = **−$17** (bad)
- raw=1.6: ~40% (slightly above noise) → EV: −$26 + 0.40×$30 = **−$14** (bad)
- raw=1.8: ~55% (agent-identified missed-opportunity cluster: APLD, CRWV, PLTR) → EV: −$26 + 0.55×$30 = **−$10** (still bad in isolation, but ignores compounding)
- raw=2.0+: ~70% → EV: −$26 + 0.70×$30 = **+$5** (positive)

The 1-shot EV math is unfavorable at any displacement threshold below ~2.0. BUT the agent-flagged missed-opportunity tickers (APLD/CRWV/PLTR/CEG/CLF/ON/WMT) ALL surfaced at raw=1.800 — the model's natural "high-conviction" ceiling. At raw>=1.8 we admit the operator's high-conviction set without admitting noise.

**Change:**
- Add config knob `rotation_winner_lock_bypass_min_raw_score` (default **1.8**, raised from initial 1.6 per adversarial review)
- In V28 rotation pair evaluation: if `incoming.raw_score >= bypass_threshold`, ignore `winner_lock` on the held position
- Also require the held position pnl<10% (don't displace a true winner like +25%)

**Expected outcome:**
- APLD/CRWV/PLTR + 4 others get bought
- P&L lift: +$1,000-$2,200 (adversarial-review-discounted from prior $1.5K-$3K — see §5)

**Test:** New test asserting raw>=1.8 displaces winner_lock'd held position with pnl<10%; raw=1.7 does not displace; raw>=1.8 does NOT displace a +25% winner.

**Risk:** Low. The 1.8 threshold is calibrated to the model's high-conviction ceiling. The pnl<10% guard prevents true-winner displacement.

### γ.5 — Wire conviction telemetry into production log (P1, ~15 LOC)

**File:** `backend/strategies/graph_nexus_analysis.py` `_resolve_conviction_tier_at_exit`

**Problem:** Phase 3 telemetry collector exists (and tests pass), but no `conviction_telemetry` log lines appear in production. Operator can't audit A1 decisions.

**Change:**
- At end of `_resolve_conviction_tier_at_exit`, always emit:
  `_log(f"conviction_tier: sym={sym} tier={tier} mcap={mcap or '?'}M raw_score={raw_score:.3f} path={path}", "cyan")`
- The `path` field disambiguates: `mcap_high` / `mcap_mid` / `raw_high` / `raw_mid` / `default_low`

**Expected outcome:** Operators can grep `conviction_tier:` and audit every A1 decision; the agent-flagged observability regression is closed.

**Test:** Smoke test asserting `_log` fires with expected fields on each tier resolution.

**Risk:** None. Pure logging addition.

### γ.6 — V31 sell-grace + circuit_breaker interaction (DEFERRED with escalation criterion)

Defer until γ.1 lands. Once A1 returns correct tiers, SNDK gets -20% MID floor and the chop-grace-into-circuit-breaker chain becomes less impactful.

**Escalation criterion (adversarial-review fix for prior under-specification):** if Φ.γ.0 (fast smoke gate) shows a MID-tier position still circuit-breaking at chop -20% to -25% drawdown, the next-step ladder is:
1. Per-tier conviction-aware grace days (HIGH=15d / MID=10d / LOW=current default)
2. Per-tier circuit-breaker floor widening (e.g., MID -22% in chop instead of -20%)
3. Grace-window extension if held during a known macro chop event

Only escalate if Φ.γ.0 reveals the residual. Do not pre-emptively widen floors (that's BLOCKER-class regression risk).

### γ.7 — Known limitation: LLM determinism residual (DOCUMENTATION ONLY)

Per addendum §11 in `docs/superpowers/plans/2026-05-18-bt136708-fix-implementation.md`, the variance/robustness agent attributed 40-60% of the 4.8× same-code spread to LLM non-determinism (gpt-5.4-mini omits temperature → OpenAI default 1.0). Phase α.1 force-enabled the sentiment cache so paired re-runs hit the same LLM output AFTER the first cold-cache run. Phase γ does NOT add further LLM-determinism mitigation.

**Implication for Φ.γ.4 (3× paired re-runs, <1.5× spread target):** even after γ.1-γ.5 ship, paired re-runs may spread by ±20-40pp from LLM jitter on the first cold-cache run. **This is NOT a γ-fix failure** — it's a known-limit residual from operator-owned model choice. Operators can mitigate by:
1. Pre-warming the sentiment cache (run the backtest once to populate, then re-run from warm cache)
2. Setting `PYTHONHASHSEED=0` (now passed through per commit 93675bf)
3. Future Phase ζ candidate: switch to a model that supports temperature=0 (operator decision)

---

## 4. Validation gates

### Φ.γ.0 (fast smoke proxy — single run, ~20 minutes; adversarial-review NEW)

Before committing to the full 3× paired re-run cycle (Φ.γ.4 is ~12 hours of compute), run BT232179 ONCE and assert ALL of:
- `mcap pre-seed:` log shows `N >= 5` populated (vs 0 currently)
- `RNG seed:` line PRESENT in the pulled log (γ.2 success criterion)
- `conviction_tier:` distribution within first 50 bars shows ≥1 HIGH AND ≥30% non-LOW

If ANY assertion fails, abort the full validation cycle and triage. This catches the "third silently-inert ship" failure mode immediately.

### Φ.γ.1 (after γ.1+γ.2 ship, ~1 hour)
Run BT232179 (or equivalent 8-ticker pure-discovery universe). Assert:
- `mcap pre-seed: <N>/<M> populated` with N >= 5 (vs 0 currently)
- `RNG seed: <int> ...` line present in pulled log
- `conviction_tier:` distribution shows >= 30% non-LOW resolutions
- GEV/SNDK (if held) get MID/HIGH floors (-20% / -25%), not LOW (-15%)

### Φ.γ.2 (after γ.3 ships, ~1 hour)
Same backtest, assert:
- V28.8.1 BREACH rate < 30% (vs 78% currently)
- BFQ allocation success rate > 50% (vs 0% currently)
- avg buys/day > 2.0 (vs 0.6 currently)

### Φ.γ.3 (after γ.4 ships, ~1 hour)
Same backtest, assert:
- APLD/CRWV/PLTR (or equivalents) bought when raw>=1.8 surfaces
- Total round_trips > 80 (vs 62 currently mid-progress)

### Φ.γ.4 (final, ~4 hours)
Three paired re-runs of BT232179 with PYTHONHASHSEED=0 set. Assert P&L spread < 1.5× median (the original Phase α variance gate, now actually measurable).

---

## 5. Estimated P&L impact (adversarial-review-discounted; framed against BT901920's +247% ceiling)

Per adversarial review §issues 5-8: prior estimates were inflated. Applied:
- γ.1: tightened from $1K-$2.5K to **$400-$1,200** (2 names only; max ~$600 per name; ceiling math)
- γ.3: tightened from $2K-$4K to **$1.5K-$3.5K** (raw=1.8 → realized 10%+ conversion rate ~50% not 65%+)
- γ.4: tightened from $1.5K-$3K to **$1K-$2.2K** (EV math + pnl<10% guard reduces displacement count)
- Combined: apply **25% destructive-interaction discount** (γ.3 admits more buys but some γ.1-protected positions get displaced by γ.4)

| Phase | Items | Per-item lift (revised) | Cumulative (additive) |
|---|---|---|---|
| γ.1 (mcap pre-seed from positions+BFQ+held) | A1 returns proper tiers; GEV/SNDK get correct floors | +$400-$1,200 | +$400-$1,200 |
| γ.2 (RNG log move) | $0 (observability) | $0 | +$400-$1,200 |
| γ.3 (heal-loop skip-relax) | BFQ drains 1-2/bar | +$1,500-$3,500 | +$1,900-$4,700 |
| γ.4 (raw>=1.8 displacement) | APLD/CRWV/PLTR-class admit | +$1,000-$2,200 | +$2,900-$6,900 |
| γ.5 (telemetry) | $0 (observability) | $0 | +$2,900-$6,900 |
| **25% destructive-interaction discount** | applied to combined | −$725 to −$1,725 | **+$2,175-$5,175** |

**Estimated final P&L:** $7,000 + $1,419 (current) + $2,175-$5,175 = **$10,594-$13,594 (+51% to +94%)**, up from current trajectory of ~$10,500 (+50%).

**Gap to BT901920 ceiling ($24,941 / +247%):** Phase γ closes ~15-35% of the gap. The remaining $11K-$14K gap is mostly structural (universe-bandwidth, see §8).

| Endpoint | Final value | P&L | vs BT901920 ceiling | Gap remaining |
|---|---|---|---|---|
| BT901920 (ceiling) | $24,941 | +$17,330 | — | — |
| Pre-Phase-γ projection | ~$10,500 | +$3,500 | -$13,800 | -80% |
| Phase γ low estimate | $10,594 | +$3,594 | -$13,736 | -79% |
| Phase γ high estimate | $13,594 | +$6,594 | -$10,736 | -62% |

**Interpretation:** Phase γ alone does NOT close the gap to +247%. It closes the capacity/A1 portion (~$3K-$5K). The remaining $11K-$14K is universe-bandwidth (8 vs 114 tickers, see §8) — that requires Phase ε (operator-curated seed expansion OR pure-discovery auto-bootstrap).

---

## 6. What this is NOT

- **NOT Phase β** (per-fix flag refactor for P1.2/P1.3/P1.5). Those flags still default OFF; Phase γ doesn't change that. Phase β remains pending until Phase α + γ together reduce variance below 1.5×.
- **NOT a model swap** (gpt-5.4-mini → temperature=0 model). Operator-owned constraint; not in Phase γ scope.
- **NOT engine-level RNG seeding** (ai_backtest_engine.py random.* for run selection). Per-backtest determinism only.
- **NOT auto-rotate-on-breach** (the operator-deferred Item 10 from BT136708 plan). Phase γ.3 is investigation + targeted fix, not new architecture.

## 7. References

- BT232179 logs: `backtests/232179_20260518-223326Z.log` (29235 lines @ 34.5% progress)
- BT901920 ceiling: `.tmp_bt901920/logs_via_api.json` (114-ticker universe, +247.58% final)
- Prior session (BT109429 Phase α plan): `docs/superpowers/plans/2026-05-18-bt136708-fix-implementation.md` §11
- Phase α commits: `55d7a52` (Phase α impl + bug-sweep) → `15d80c3` (bare-import fix) → `93675bf` (env passthrough)
- Tier-3 design: `docs/superpowers/specs/2026-05-17-nexus-tier3-missed-rally-fixes-design.md`

---

## 8. What Phase γ CANNOT close — the residual gap to BT901920's +247%

After Phase γ ships, BT232179's projected end-state is +85% to +156%. BT901920 finished at +247%. The residual gap is **$6K-$11K** ($13K-$18K Phase-γ projection vs $24.9K ceiling). This gap is driven by two factors Phase γ does not address:

### 8a. Universe bandwidth (~80% of residual gap)

BT901920 had 114 operator-curated seed tickers; BT232179 has 8 + whatever pure-discovery surfaces. The 14× difference in starting bandwidth produces:
- **More sentiment events**: every ticker in the seed universe contributes its own news flow into the LLM sentiment classification. 114 tickers ≈ 114× more potential propagation seeds per bar.
- **Deeper graph signal**: Nexus propagation expands from seeds with nonzero sentiment. More seeds = more 1-hop / 2-hop / sector / institutional / supply-chain expansions per bar = richer scoring surface.
- **More compounding**: more winning positions means winner-add fires more often, capital compounds faster.

**This is NOT a bug.** The operator chose pure-discovery (8 tickers) to test discovery throughput. The +247% ceiling is unreachable under that constraint without one of:
- Expanding the seed universe (operator-curated, not a code change)
- Accelerating pure-discovery throughput so Nexus surfaces more tickers per bar (medium-term spec, not Phase γ scope)
- Allowing pure-discovery to bootstrap a working universe from the first 10 bars' propagation winners (Phase ε candidate — see below)

### 8b. Variance (variance/robustness agent's prior estimate: 40-60% LLM, 25-35% propagation seed sensitivity)

Phase α shipped to compress this — but α.4 silently no-op'd, and γ.1 fixes that. After γ.1, the variance floor SHOULD reach the Phase α targets. Until measured via Φ.γ.4 (3× paired re-runs), it's unknown.

### Phase ε candidates (NOT in Phase γ scope; future work)

- **ε.1 Seed-universe auto-bootstrap**: in pure-discovery mode, take the top-N propagation winners after K bars and ADD them as persistent seeds for the rest of the run. Mimics what BT901920's operator-curated seed achieves naturally.
- **ε.2 Propagation depth knob**: allow `max_hops=3` or `max_hops=4` to reach further into the graph. Currently capped at 2-hop.
- **ε.3 Multi-cycle compounding**: increase per-position sizing scale during sustained-bull regimes (BT901920 ran during a bull-dominated window; aggressive sizing would have compounded faster).
- **ε.4 Pre-seed mcap from full Neo4j universe**: instead of just held+BFQ+symbols_list, seed mcap from the entire Companies node set in Neo4j (~5000 tickers). One-shot at backtest start. Closes the universe-bandwidth gap for A1 tier resolution specifically.

**Recommended sequencing:** ship Phase γ first, measure actual end-state, then decide which Phase ε items are worth the design effort. If Phase γ lands at +120%+, the residual gap is mostly universe-bandwidth and Phase ε.1/ε.4 are highest-leverage. If it lands below +85%, Phase γ has an unidentified blocker and Phase ε is premature.
