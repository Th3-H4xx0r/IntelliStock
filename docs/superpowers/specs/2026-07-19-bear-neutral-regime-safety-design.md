# Bear & Neutral Regime Safety — Root Cause + Fix Design

**Date:** 2026-07-19 · **Branch:** feat/controlled-benchmark-alpha · **Status:** awaiting user approval
**Goal (user mandate):** positive P&L even in bear windows, maximum P&L overall.

## 1. What the forensics found (this session, log + prod-DB evidence)

### RC1 — The regime pipeline was data-starved. The "bear problem" was never a strategy problem.

Proven chain, each link verified:

1. **Prod DB rows are poisoned** (`GraphNexusOverlayBarsCache`, inspected live over Tailscale):
   SPY = **0 bars since 2026-04-25**; QQQ/GLD/XLF/ITA/SMH/TLT = 53 bars starting **2026-04-30**; only VOO healthy (156 bars from 2025-11-17).
2. `_ensure_overlay_bars_cached` (graph_nexus_analysis.py ~17945) treats an **empty cached row as valid** (only `None` triggers refetch) and its staleness check validates only the **end** of the range — never whether the row covers the *requested start*. Rows are keyed by symbol only, so any run's fetch range overwrites everyone's.
3. `_detect_market_regime` (~6029) picks the first proxy in (SPY, QQQ, VOO) **with any bars at all** — QQQ's non-empty-but-starts-04-30 row shadows healthy VOO — then its point-in-time filter yields <21 closes for any pre-May-2026 sim date and it **silently returns "bull"** (fail-open to the most aggressive regime, zero logging).
4. Downstream, everything keyed to regime saw permanent bull: Z4.1 capacity caps (bear log shows `held=14` ×205, `held=15` ×37), `_spy_20d_return` → None (backcompat path → bull), sleeve bull-only deploy gate (deployed all month), bull/chop-only fast-cut suppression (active all month), momentum lanes ungated.
5. **Tell in the logs:** `Sector price context: no significant movers` on *every* cycle of a −7.5% SPY month (21× in F3, 106× in baseline bear) — the sector context and price-trend features read the same dead cache.

**Replaying the exact detector math on the healthy VOO row gives the true timeline:**
- Bear window (03-02..03-30): **chop from 02-23** (before the window even starts), **bear 03-20**, chop 03-23, bear 03-25 — never bull.
- Bull window (03-30..04-27): chop until **04-09**, then bull.
- Neutral window (06-03..07-07): chop 06-09 → bull 06-15 → chop 06-23 (matches the observed flips — the detector *worked* there because QQQ's row happens to cover June).

So BEAR_F3's −7.32% is **not** evidence that regime-gated levers fail; the gates never engaged. The prior Jarvis "law" ("regime-switching cannot make bull-aggression bear-safe — 3rd confirmation") is **invalidated on the equities side**. Conversely, BULL_F's +16.27% was partly *powered* by the same blindness (day-one full deployment under fallback-bull, when the true regime was chop until 04-09) — the fix may cost some bull P&L; the validation matrix measures it.

### RC2 — Bear loss attribution (BEAR_F3, −$439 on $6k)
17 buys on 03-02/03 at 14 slots + ramp 0.9 (blind-bull; true regime chop → caps 8, no sleeve); mid-month adds via momentum rotation lanes that **bypass the position cap** (ANAB in via `momentum_watchlist_rotation` 03-17, −$112.5 alone; book reached 15 held); late cuts realized −11..−13% each (CRH −64, AYI −61, CLSK −57). Selection alpha was real even here (CE +51, CVX +38, FLY +28, sleeve SPY +10.4) — throughput, not picking, lost the money.

### RC3 — Neutral loss anatomy (851037, −4.33%)
Extended-momentum entries at local tops: OUST rebought 07-01 @59.51 after +36% nine-day run-up (−$133), ASTC swap-in at top, cut 4 days later (−$135), LRCX −$101, KTOS −$72. The shipped-but-untested-here entry gates (`entry_extension_block_pct=25`, `runup_block=20`) target exactly these. Real regime flapping (bull↔chop ×5) → levers keyed to raw regime would whipsaw without hysteresis.

### RC4 — Sleeve hysteresis is self-defeating by construction
Deploy (broker.py `_residual_sleeve_deploy`) parks cash down to the 2% buffer at cycle end; release (`_residual_sleeve_release`) frees the whole sleeve whenever cash < 15% NAV at cycle start — which parking guarantees. Result: park/release round-trip nearly every bar (~53 cycles in BEAR_F3). Harmless in the backtest fee model (net +$10.4), a real slippage bleed live.

## 2. Approaches considered

**A. Data-fix + hardened detector + hysteresis + regime profiles (RECOMMENDED CORE).** Fix the cache layer, make the detector fail-safe and loud, add asymmetric hysteresis, keep the proven levers keyed to (now-real) regimes. Directly attacks the proven root cause; preserves the bull lever set as the bull profile.

**B. A + portfolio drawdown circuit (defense-in-depth; Stage C pull-forward).** The classifier is reactive (20d window) and now has one proven failure mode; an independent portfolio-level circuit catches what classification misses. Caveat: Stage C's soft8/hard12/kill15 tiers would have slept through this entire window (max portfolio DD ≈ −7.3% even in the disaster run) — tiers must be retuned (soft −5% with SPY corroboration).

**C. Route everything through benchmark-alpha Stage C after the research gate.** Rejected for now: leaves doc-179/live exposed for weeks and the user wants bear + neutral solved now. Stage C still proceeds on its own track; B pulls its circuit forward.

**Chosen: B (= A + retuned circuit), phased so A ships and validates first.**

## 3. The fix plan

### Phase 0 — Before Mon 2026-07-20 12:00Z (unchanged, now with proof)
Revert doc-179 to the bear-safe baseline (backup chain: phase2 keys → bull levers). The aggressive config demonstrably traded blind; nothing re-applies until Phase 1+2 validate. **User go required (real money).**

### Phase 1 — Data layer repair (the actual bug)
1. **Purge** the poisoned benchmark rows (SPY empty row + all 53-bar rows) from `GraphNexusOverlayBarsCache` in prod; they refetch on next use. (Touches prod DB — cache-only, self-healing; user go.)
2. `_overlay_bars_cache_set`: **never persist empty bars**; store `fetch_start`/`fetch_end` coverage metadata on each row.
3. `_ensure_overlay_bars_cached`: treat empty rows as misses; refetch when the cached row does not cover the requested start (7-day grace) — not just the end.
4. `_detect_market_regime`: choose the proxy by **usable point-in-time closes ≥21**, not "has any bars" (stops QQQ shadowing VOO); log inputs (proxy, n_closes, ret20, ma50) once per bar; when blind, log a LOUD warning and fail to **"chop"** (neutral capacity), not "bull" — allow a config-gated bull fallback only for live cold-start grace (first N bars).
5. **Backtest self-sufficiency:** seed the SPY row of the overlay cache from the engine's own backtest bar universe (SPY 1h → daily resample; SPY is already loaded for the market filter/sleeve), so backtests never depend on an external fetch being healthy.
6. Sweep the other `or "bull"` fallbacks (~5964, 7362, 17560) → "chop" where they gate capacity/aggression.

### Phase 2 — Regime hysteresis + cold-start seeding
- **Asymmetric switching:** downgrade (→chop/→bear) applies immediately; upgrade (→bull) requires K=3 consecutive daily signals. Fast to de-risk, slow to re-risk; kills the June flapping and the 03-20/23/25 borderline oscillation.
- **Cold-start seeding:** at bar 1, compute regime from the (now guaranteed) ≥21-close history so day 1 is correct without a dwell penalty — the bull window legitimately starts chop (till 04-09); the bear window starts chop, not bull.
- Emit `regime_changed` log lines with ret20/proxy for every transition (auditability).

### Phase 3 — Regime profiles (levers stay, keyed to real regimes)
- **bull:** the proven +16.27% profile — caps 14, ramp 0.9, sleeve on, extension gate 25, min-hold 2d.
- **chop:** caps 8, ramp moderate (e.g. 0.5), **no sleeve**, extension/runup gates on (they specifically kill the OUST/ASTC entries), min-hold on, **momentum rotation/swap lanes must respect the cap and the entry gates** (close the ANAB bypass).
- **bear/crash:** caps 8/0 new entries, sleeve force-exit, fast cuts immediate (no suppression), rotation lanes off.

### Phase 4 — Drawdown circuit (classifier-independent safety net)
Portfolio drawdown from rolling 20-session high: **soft −5%** (halt new buys; requires SPY 20d < 0 corroboration so bull pullbacks don't trip it) · **hard −9%** (halt + tighten cut floor to −7%) · **kill −12%** (liquidate, runCommand off, notify). Config-gated, default ON in backtests, live only after A/B. This is the layer that catches the *next* novel classifier failure.

### Phase 5 — Sleeve hysteresis fix
Release only when there is actual buy demand (pending executable buys), sized `min(needed_cash, sleeve_value)`; add min-park duration of 1 session; keep the unconditional bear/crash protective exit. (Bug is latent-live, not a backtest P&L driver.)

### Phase 6 — Validation matrix (faithful API backtests, 3 runs + 1 optional, credits-conscious)
| Run | Window | Config | Success gate |
|---|---|---|---|
| BEAR_F4 | 03-02..03-30 (SPY −8%) | full fix stack | **> 0%** (baseline blind achieved +0.87 with less alpha retention; fixed stack keeps CE/CVX/FLY-type winners, blocks the ANAB/churn losses) |
| NEUTRAL_F4 | 06-03..07-07 (SPY flat) | full fix stack | **≥ 0%** (vs −4.33) |
| BULL_F4 | 03-30..04-27 (SPY +13.6) | full fix stack | **≥ SPY** (accepting some giveback vs +16.27 from the honest chop start; if it drops below SPY, tune the chop profile's ramp/caps up, not the detector) |
| (opt) BEAR_F4b | 2nd bear window | same | > 0% (guards single-window overfit) |

### Phase 7 — Post-validation
Re-apply the winning profile to doc-179 (user go), update Jarvis memory + benchmark-alpha program docs; Stage C proceeds through its research gate with the circuit already field-tested.

## 4. Adversarial self-review (attacks attempted on this design)

1. *"Rows could have been poisoned after the runs ran."* No — `cached_at` timestamps (SPY 04-25, QQQ 07-17 13:30) predate the 07-18/19 runs; the runs read what we inspected.
2. *"Maybe the detector was just disabled."* No — the June run flips regimes, same code path.
3. *"Bear-positive may be unreachable; don't overpromise."* Baseline achieved +0.87 in the same window with *accidental* throughput friction; the fixed stack applies the same friction deliberately (chop caps from day 1, bear entry-block from 03-20) while keeping winners. Positive is credible, not guaranteed — that's what BEAR_F4 is for, and the plan does not touch live money before it.
4. *"The fix could break the bull result"* — real risk, quantified: true regime is chop until 04-09, so day-one full deployment goes away. Mitigations: chop profile still deploys 8 slots with gates; if BULL_F4 < SPY the tuning lever is the chop profile's throughput, not weakening detection. The +16.27 number was partly a blindness artifact; the honest target is ≥ SPY.
5. *"Drawdown circuit tiers from Stage C never fire here."* Correct — hence retuned soft −5 with SPY corroboration; tiers validated in BEAR_F4 rather than assumed.
6. *"Hysteresis delays bull re-entry at window starts."* Cold-start seeding computes regime from history at bar 1 — no dwell penalty on day 1; K=3 applies only to genuine mid-run upgrades.
7. *"Cap-bypassing rotation lanes could reintroduce churn even with caps."* Closed explicitly in Phase 3 (lanes respect caps + gates in non-bull).
8. *"Purging prod cache is risky."* Cache-only table, rebuilt on demand; the live instance currently runs on a 53-bar QQQ row (marginal ≥21) and heals to full history after purge+refetch. Still gated on user go.
9. *"Same bug class elsewhere?"* Phase 1.6 sweeps the `or "bull"` fallbacks; the crypto side has its own (verified-working) regime path and is untouched.

## 5. Explicitly out of scope
Live Binance.US validation, benchmark-alpha data manifest/Stage C research gate (own track), PriceHistory retention, any doc-179 mutation without user go.
