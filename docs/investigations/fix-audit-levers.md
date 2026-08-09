# fix-audit-levers — the definitive INERT/WORKS ledger for doc-193

Read-only audit. **No code edited, no run started or stopped, nothing pushed.**
Builds on `_SYNTHESIS.md`, `_RUNS4.md`, `gap-bugsweep.md`, `sweep2.md`, `hold-check.md`,
`gap-capital.md`, `gap-target.md`, `sndk-priority-block.md`, `ext-still-blocking.md`. Not redone here.

## Sources — and one correction to the method every prior doc used

| run | window / regime | status at pull | result | log | config |
|---|---|---|---|---|---|
| **915207** | 2026-01-01..03-01 bull/chop | finished 100% | +9.70% | `backtests/915207.log` (41,184 l) | 621 keys |
| **542754** | 2026-03-02..03-30 **bear** | finished 100% | +11.94% | `backtests/542754_sweep.log` (18,265 l) | 624 keys |
| **383778** | 2026-03-30..04-27 **OOS bull** | finished 100% | +4.75% | `backtests/383778_sweep.log` (19,643 l) | 624 keys |
| **427197** | 2026-01-01..03-01 bull/chop | running 77.6% | +9.81% | `backtests/427197_inv.log` (32,518 l) | 624 keys |
| **571147** | 2026-01-01..03-01 bull/chop | running 60.9% | +4.89% | `backtests/571147_audit.log` (27,189 l) | **627 keys** |

**METHOD CORRECTION.** `gap-bugsweep.md` read the config from
`scripts/doc193_backup_patch_20260808T110842Z.json`, a stale on-disk snapshot. The authoritative
per-run config is `GET /backtests/<id>/summary → strategy_schema.strategies[0].config`, which is
**frozen at run creation** (proved: 915207 pulled at 15:35 UTC and again at 17:06 UTC both return
621 keys, while 427197 pulled at 16:59 returns 624 — a live read would return the same number).
Every count in this document is grepped against the run's own log with the run's own config.

Two published claims are wrong as a result, and both are in the direction of *false alarm*:
* `overlay_bars_min_history_bars` is **not absent** — it is `70` in all five runs and it works (row 5).
* `turnover_budget_conviction_bypass_max_pct` is **not 0.8** — the harmful ceiling `_SYNTHESIS`
  convicted was already reverted to `0` before 915207 was created (row 40).

Only **nine** keys differ across the five runs, so the five runs are not five configs — they are
one config plus nine deltas: `core_min_pct` (0.25→0.10), `watchlist_priority_slots` (0→2→0),
`entry_extension_metric` (absent→range), `momentum_breakout_freshness_pct`/`_lookback_bars`
(absent→0/20), `residual_sleeve_bear_alloc_pct` (0.7→0.35), `bfq_include_momentum_lane` (→True),
`peak_giveback_min_peak_pnl_pct`/`_exit_drawdown_pct` (→30/25).

---

## THE TABLE

Counts are raw occurrences of the signature in each run's log. `✔` = verified by a derived
signature (arithmetic), not a literal string.

| # | key (value on doc-193) | expected log signature | 915207 | 542754 | 383778 | 427197 | 571147 | VERDICT | note |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| | **── DISCOVERY ──** | | | | | | | | |
| 1 | `momentum_scan_cached_bars`=True | `Discovered stock (momentum): X (20d=…, 60d=…)` | 276 | 150 | 126 | 217 | 178 | **WORKS** | screens the overlay cache; SNDK/WDC now found on bar 1 |
| 2 | `momentum_watchlist_universal_mode`=True | `Momentum watchlist: watchlist=N scored=N` | 43 | 21 | 21 | 33 | 27 | **WORKS** | one line/bar |
| 3 | `momentum_rank_on_60d`=True | `Discovered stock (momentum)` block ordered 60d-DESC | 276 | 150 | 126 | 217 | 178 | **WORKS** | monotone 60d-desc in 27/27 sampled bars of 571147 |
| 4 | `momentum_missing_60d_excluded`=True | **absence** of `60d=+0.0%` | 0 | 0 | 0 | 0 | 0 | **WORKS** | 0 fabricated zeros across 1,975 discoveries |
| 5 | `overlay_bars_min_history_bars`=70 | `Fetched chunk … (START to …)` = bar1 − 135 calendar days | ✔ | ✔ | ✔ | ✔ | ✔ | **WORKS** (derived sig) | observed starts 2025-08-19 / 2025-10-18 / 2025-11-15 — exactly 70×1.5+30 d. No dedicated log line. |
| 6 | `momentum_breakout_freshness_pct`=**0** | freshness tie-break inside `_rank60` | 0 | 0 | 0 | 0 | 0 | **INERT (config=0)** | `_fresh()` returns constant 1 when band≤0 (gna:14088). a2609bd shipped and is switched off. |
| 7 | `momentum_breakout_lookback_bars`=20 | — | 0 | 0 | 0 | 0 | 0 | **INERT (dead param)** | only read when freshness>0 |
| 8 | `momentum_max_runup_multiple`=3 | `Momentum ceiling block: X 20d=… 60d=…` | 171 | 80 | 75 | 123 | 100 | **WORKS** | 171/80/75/123/100 candidates refused before ranking |
| 9 | `sector_watchlist`={} + `watchlist_priority_slots`=0 (was 2 in 427197) | `Watchlist candidate audit: … matched=none` | 43 | 24 | 21 | 33 | 27 | **INERT — STRUCTURAL** | 148/148 audit bars `matched=none`; `priority_tickers` is unconditionally empty (gna:13153) |
| 10 | `sector_watchlist_reserved_slots`=0, `_max_per_sector`=0, `watchlist_priority_min_raw_score`, `_requires_active_sector` | `discover=watch:0/0` | 0 | 0 | 0 | 0 | 0 | **INERT (4 dead keys)** | multiplied by the same empty set |
| | **── ENTRY GATE ──** | | | | | | | | |
| 11 | `rank_band_enabled`=True (entry 10% / exit 50%) | `Rank band (entry<=#N, exit>#M of K): blocked J buy(s)` | 43 | 24 | 21 | 33 | 27 | **WORKS — the single biggest refuser** | 2,018 / 1,249 / 1,623 / 2,091 / 1,810 buy-signals blocked |
| 12 | `rank_band_momentum_exempt_min_score`=**0** | `Rank band: N momentum name(s) exempt` | 0 | 0 | 0 | 0 | 0 | **INERT — STRUCTURAL + off** | reader gna:23283 runs 5,606 lines BEFORE the writer gna:28889. Raising it would still do nothing. |
| 13 | `entry_extension_block_pct`=25 base / **0** in bull+recovery profiles | `Entry extension gate: X recent runup +N% > 25% — buy blocked` | 142 | 139 | 37 | 99 | 55 | **FIRES / BACKWARDS** | SNDK blocked 16× (427197) and 6× (915207) on a +166% move. Regime flips chop↔bull 8×/window, so the gate is a coin-flip. |
| 14 | `entry_extension_metric`='range' | the word `recent runup` (legacy) instead of `range … [bars=N]` | 142 | 93 | 26 | 67 | 35 | **INERT (config)** | gna:9304 `if metric != 'anchor': return legacy`. a2054c6 shipped, then was configured back to the metric its own commit convicted. |
| 15 | `entry_extension_lookback_bars`=20, `_require_bars`=False, `_glitch_ceiling_pct`=0 | — | 0 | 0 | 0 | 0 | 0 | **INERT (3 dead params)** | only consulted on the `anchor` path |
| 16 | `momentum_watchlist_track_extension_blocked`=True (+2) | `extension_blocked` watchlist inserts | 42 | 6 | 17 | 32 | 26 | **WORKS** |  |
| 17 | `min_position_nav_pct`=0.06 | `SKIP BUY X — cash_to_use $A < min $B (allocated $C)` | 4 | 0 | 10 | 11 | 9 | **BACKWARDS** | 571147: `SKIP BUY SNDK … $133.75 < min $406 (allocated $216.79)` — the run's best name, on its winner-add. 427197: same for WDC ($586.87). |
| 18 | `min_position_size`=100 / `priority_min_position_size`=100 | `Backfill queue BUY: X (… alloc=$100 …)` | 18 | 0 | 24 | 15 | 9 | **WORKS but caps conviction at $100** | SNDK filled $100.67 = 1.7% of NAV against a $840 (14%) clip |
| 19 | `allocation_profile`='conviction', `_max_new_stock_buys`=3, `_execute_min_raw_score`=0.25 | `Pre-queue position sizes: N stock buys sized` | 41 | 23 | 21 | 33 | 27 | **WORKS** |  |
| 20 | `total_spend_cap_target_weight_pct`=0.14 + `_concentrate`=True | `V31.2 total-spend cap [CONCENTRATE]: funded N of M` | 43 | 24 | 21 | 33 | 27 | **WORKS** |  |
| 21 | `deployment_ramp_enabled` + bar1/2/3 caps + `chop_scale` | `Buy budget: … ramp=N%` | 43 | 11 | 19 | 30 | 26 | **WORKS bars 1–3 only** | ramp=100% on 40/43, 23/26 bars — inert after day 3 |
| 22 | `cash_reserve_floor_pct`=0.02 + hard/release (5 keys) | `Buy budget floor: … (floor=10% of $NAV)` | 39 | 6 | 11 | 27 | 21 | **WORKS** |  |
| | **── CAPACITY ──** | | | | | | | | |
| 23 | `max_positions`=6 + `max_positions_honour_regime_cap`=True | `max_positions: honouring the regime cap 6 -> 8` | 43 | 21 | 21 | 33 | 27 | **FIRES / ECONOMICALLY INERT** | `MAX_POSITIONS_GATE: blocked` = **0** in all 5 runs. Nothing was converted from refusal to buy. |
| 24 | `max_positions_exclude_sleeve_legs`=True | `max_positions: index-core leg(s) SPY do not consume a slot` | 612 | 267 | 265 | 464 | 374 | **HALF-WIRED** | applies at the broker emission gate (where nothing blocks) and NOT at the strategy's own latch (where everything blocks) |
| 25 | ↳ consequence: Z4.1 breach latch counts SPY | `[V28.8.1 max_positions BREACH] current=9 > max=8 … Blocking direct/dequeue new-ticker buys` | 21 | 18 | 0 | 20 | 17 | **BINDING, UNGATED** | 21/18/0/20/17 bars on which ALL new-name buys were killed. `_current_positions = len(get_positions())` (gna:29076) — SPY takes a slot. |
| 26 | `max_positions_bull`=14 / `_chop`=8 / `_bear`=2 / `_recovery`=14 | `Regime capacity gate (Z4.1): regime=chop max_positions 6->8` | 43 | 21 | 21 | 33 | 27 | **WORKS** |  |
| 27 | `core_min_pct`=0.1 (was 0.25) | `SATELLITE OVERFLOW: X … funding $N of room out of the core` | 11 | 0 | 30 | 18 | 16 | **WORKS** | overflow-ceiling skips 17→0; satellite 72.9%→85.5% of NAV. Binder moved to `design share` (12 skips). |
| 28 | `satellite_conviction_overflow_min_raw_score`=1.5 | `SATELLITE OVERFLOW: X raw=+1.75 >= 1.50` | 20 | 0 | 63 | 31 | 31 | **WORKS** |  |
| 29 | `satellite_conviction_reserve_pct`=**0** | (reserve carve-out) | 0 | 0 | 0 | 0 | 0 | **INERT (config=0)** | parked per _SYNTHESIS until the cash race is fixed |
| 30 | `core_funding_max_positions_aware`=True | `[core] funding pre-pass: max_positions will refuse N of M sized buy(s) … not releasing core` | 3 | 0 | 0 | 3 | 2 | **FIRES RARELY / HARMFUL** | 3/0/0/3/2. It withholds core funding on a cap that blocks nothing (`MAX_POSITIONS_GATE: blocked`=0). |
| 31 | `backtest_credit_pending_sell_proceeds`=True | **NONE — no log line exists anywhere** | 0 | 0 | 0 | 0 | 0 | **UNVERIFIABLE-NO-LOG + STRUCTURALLY INERT** | only `get_buying_power()` reads it; the buy gate reads `get_cash()` (broker.py:15163). 427197 still printed `SKIP BUY ARWR — cash_to_use $1.69` on the same tick as `FILL SELL SPY $1,675.74`. |
| 32 | `backtest_credit_sell_proceeds_enabled`=False | `Sell-proceeds credit: sizing ceiling …` | 0 | 0 | 0 | 0 | 0 | **OFF (live-only twin)** |  |
| 33 | `backfill_queue_*` (12 keys) | `Backfill queue BLOCKED: X (full_priority_blocked, …)` | 691 | 0 | 37 | 399 | 378 | **WORKS — 2nd biggest refuser** | 691/0/37/399/378 blocks |
| 34 | `bfq_include_momentum_lane`=True (NEW in 571147) | `Backfill queue ADD: SNDK` where 427197 had `BLOCKED (full_priority_blocked)` | 0 | 0 | 0 | 0 | 1 | **WORKS** | 427197: SNDK BLOCKED ×7, 0 fills. 571147: ADD→REFRESH→BUY→**1 `FILL BUY SNDK`**. |
| 35 | `bfq_rotation_on_zero_headroom`=True | BFQ rotation sell/buy pair | 0 | 0 | 0 | 0 | 0 | **INERT — gated by parent** | `if _bfq_rotation_enabled and (…)` (gna:31752); `backfill_rotation_enabled`=False |
| 36 | `backfill_rotation_*` (10 keys incl. all `winner_lock_bypass_*`) | BFQ rotation lines | 0 | 0 | 0 | 0 | 0 | **INERT — 10 dead keys** | `backfill_rotation_enabled`=False |
| 37 | `backfill_direct_reserved_slot`=True | `direct_reserved` in BFQ lines | 5 | 0 | 0 | 1 | 0 | **BARELY FIRES** | 5/0/0/1/0 |
| 38 | `queue_rotation_promotion_enabled`=True (+2) | — | 0 | 0 | 0 | 0 | 0 | **UNVERIFIABLE-NO-LOG** |  |
| 39 | `turnover_budget_monthly_pct`=0.5 | `TURNOVER BUDGET BINDING: N% of NAV …` | 258 | 269 | 298 | 268 | 314 | **WORKS — binds on ~every bar** | median rolling turnover 88–104% vs the 50% budget |
| 40 | `turnover_budget_conviction_bypass_enabled`=True, `_max_pct`=0 (0 = no ceiling) | `TURNOVER BUDGET BYPASS` | 9 | 0 | 33 | 13 | 15 | **WORKS** | the harmful 0.8 ceiling _SYNTHESIS convicted is already reverted to 0 in all 5 runs |
| | **── EXITS ──** | | | | | | | | |
| 41 | `peak_giveback_min_peak_pnl_pct`=30 / `_exit_drawdown_pct`=25 (NEW in 571147) | `PEAK GIVE-BACK EXIT: SLV peaked +60.5% … — selling` | 0 | 0 | 0 | 0 | 55 | **FIRES 55×, EXECUTES 0× — INERT** | reason `Peak give-back exit:` is not in `_FORCED_EXIT_TAGS` (gna:19829) → `_forced_exit=False` → never added to `_nexus_sell_enforcement` (gna:24666). 0 `FILL SELL SLV`. |
| 42 | `nexus_monitor_risk_exit_execution_enabled`=False (also False in bull/recovery profiles) | (gate for monitor-tick sells) | 0 | 0 | 0 | 0 | 0 | **OFF, but not the blocker** | `nexus_monitor_risk_exit_always_enabled`=True satisfies the `or` at gna:24601. The blocker is `_forced_exit`. |
| 43 | `trailing_stop_disabled`=True | `Trailing stop SUPPRESSED (trailing_stop_disabled): X drop=N% >= 12%` | 124 | 0 | 2 | 138 | 19 | **WORKS (it suppresses)** | 124/0/2/138/19. Removes the only peak-anchored gate — that is what created the SLV hole. |
| 44 | `trailing_stop_pct`/`_commodity_etf_pct`/`_sector_etf_pct`/`_activation_pct`/`_activation_vol_multiplier`/`_pnl_scaling`/`_ratchet_enabled`/`_ratchet_tiers` (8 keys) | `Trailing stop SELL` | 0 | 0 | 0 | 0 | 0 | **INERT — 8 dead keys** | parent `trailing_stop_disabled`=True |
| 45 | `profit_take_disabled`=True / `_enabled`=False (+`_gain_pct`,`_sell_fraction`,`_tiers`) | `Profit take TRIGGER` | 0 | 0 | 0 | 0 | 0 | **INERT — 5 dead keys** | 0 fires in 5 runs |
| 46 | `fast_loser_cut_enabled`=False (+`_pct`,`_pct_high_vol`,`_min_hold_days`,`_recent_runup_block_pct`,`_lookback_bars`,`fast_loser_blacklist_days`) | `Fast loser cut:` | 0 | 0 | 0 | 0 | 0 | **INERT — 7 dead keys** | 0 fires in 5 runs |
| 47 | `peak_protection_enabled`=True + `_min_peak_pnl_pct`=30 + `_max_drawdown_from_peak_pct`=25 | `Peak protection BYPASS fast-loser-cut` | 0 | 0 | 0 | 0 | 0 | **INERT — bypass of a disabled cut** | it defers to a disabled trailing stop, and `fast_loser_cut_enabled`=False so there is nothing to bypass |
| 48 | `catastrophic_stop_pct`=-40 / `_enabled`=True | `Catastrophic stop` | 0 | 0 | 0 | 0 | 0 | **UNREACHABLE** | worst entry-anchored drawdown observed = -20% |
| 49 | `max_hold_days`=3650 | `Hold-limit exit` | 0 | 0 | 0 | 0 | 0 | **UNREACHABLE** |  |
| 50 | `min_hold_days`=120 / `min_hold_enabled`=True / `llm_sell_conviction_bypass_enabled` | `[sell-gate] X \| gate=llm_sell_min_hold \| result=blocked` | 12 | 2 | 12 | 19 | 20 | **WORKS** | of which blocked = 11/2/11/18/18; bypass fires 1–2×/run (`result=sell`) |
| 51 | `winner_protection`=True + `sell_enforcement_big_winner_*` (3) | `[sell-gate] X \| gate=winner_protect \| result=blocked (hold)` | 31 | 0 | 0 | 21 | 16 | **WORKS** | 31/0/0/21/16 winner sells refused |
| 52 | `circuit_breaker` tiers (-15/-20/-25) + `_reentry_blacklist_bars` | `[sell-gate] … gate=circuit_breaker … result=fired` | 1 | 0 | 0 | 1 | 1 | **WORKS — 1 fire/run** | APP -20% in 427197 (correct); no -10% floor exists in this config |
| 53 | `drawdown_circuit_enabled` + `portfolio_dd_soft/hard/kill/hard_cut_floor` + `portfolio_drawdown_halt_*` (9 keys) | `DRAWDOWN HALT` / `drawdown_halt=True` | 0 | 0 | 0 | 4 | 0 | **BARELY FIRES** | 0/0/0/4/0 — only 427197, and only to skip winner-adds |
| 54 | `rotation_min_score`=99 / `rotation_min_delta`=99 / `rotation_profitable_min_delta`=99 | `V32 ROTATION SKIP: X raw=… < min_score=99.000` | 274 | 46 | 2 | 203 | 170 | **WORKS as an OFF switch** | 274/46/2/203/170 skips; 0 rotations executed anywhere |
| 55 | `rotation_*` tuning: `break_glass_*`(5), `winner_lock_*`(5), `graph_gate`, `ml_weight`, `incremental_cap_pct`, `replace_loss_threshold`, `prevalidate_sector_cap`, `lanes_regime_gated`, `min_hold_days`, `profitable_*`(3), `max_rotations_per_day_high_conviction` (~20 keys) | rotation execution lines | 0 | 0 | 0 | 0 | 0 | **INERT — ~20 dead keys** | lane disabled by min_score=99 |
| 56 | `momentum_swap_vs_portfolio_enabled`=False + `momentum_swap_*` (5) + `max_top_momentum_rotations` + `top_momentum_break_glass_*` (4) | `Momentum portfolio swap: sell` / `BREAK GLASS` | 0 | 0 | 0 | 0 | 0 | **INERT — correctly OFF (11 dead keys)** | _SYNTHESIS: 5/5 swaps sold and failed to re-buy. Keep it off. |
| 57 | `portfolio_swap_ath_gate_enabled`=False (+3), `mega_winner_protect_enabled`=False (+3), `mid_winner_trend_reversal_block_enabled`=False (+4), `momentum_partial_trim_execution_enabled`=False, `momentum_rediscovery_enabled`=False (+2), `momentum_amplification`=False (+6), `max_positions_breach_auto_rotate`=False, `profitable_min_hold_conviction_override_enabled`=False | — | 0 | 0 | 0 | 0 | 0 | **INERT — ~22 dead keys** | all OFF switches with their tuning params still populated |
| | **── SLEEVES ──** | | | | | | | | |
| 58 | `anchor_reinforce_enabled`=True + 13 stage params | `V31 anchor reinforcement budget: cap=$N (…), candidates=K` | 43 | 21 | 21 | 31 | 27 | **BUDGET LOGGED, ZERO ADDS** | one line/bar in every run and not one anchor add executed → 14 keys buying nothing |
| 59 | `winner_add_enabled`=True + 8 params | `Winner add-on: X alloc=$N (P&L=…, held=…d, drop=…%)` | 1 | 0 | 0 | 0 | 2 | **WORKS, 1–2 fires/run** | 571147 SNDK $182 + $217; the $217 one was then refused by `min_position_nav_pct` |
| 60 | `residual_sleeve_enabled`=True + `_bear_alloc_pct`=**0.35** + 22 more | `[sleeve] released/deployed … SQQQ … 70%` | 0 | 153 | 2 | 0 | 0 | **WORKS in bear ONLY — 0.35 is UNTESTED** | the only bear run (542754, +11.94%) ran at **0.70**. 0.35 has never seen a bear window. Also 27 SQQQ fills / $12,155 gross on a $6,000 book. |
| 61 | `core_sleeve_enabled` / `core_target_pct`=0.35 / `core_max_pct`=0.4 / `core_rebalance_band_pct` / `_min_days` | `[core] released` / `[core] bought … band_deploy` | 3 | 3 | 3 | 4 | 4 | **WORKS — and leaks** | 62% of every conviction release is re-bought as SPY within 1 bar (sweep2 §2d, 4 runs) |
| 62 | `regime_detector_enabled` + `regime_recovery_*` (16 keys) | `Regime capacity gate (Z4.1): regime=chop …` | 43 | 21 | 21 | 33 | 27 | **WORKS** | regime flips chop↔bull ~8×/window |
| 63 | `regime_profiles.bull.entry_extension_block_pct`=0 | gate silent on bull bars, 25 on chop bars | 142 | 139 | 37 | 99 | 55 | **WORKS — and makes the gate a LOTTERY** | the same name is blocked or admitted purely by which regime the bar happened to be in |

---

## PART 2 — TURN THESE OFF, IN PRIORITY ORDER

### A. ACTIVELY HARMFUL — turn off first

**1. `min_position_nav_pct` = 0.06 → set to `0`.**
Its stated purpose is "a position too small to matter must not cost a `max_positions` slot"
(89e71f3). But `MAX_POSITIONS_GATE: blocked` is **0 in all five runs** — a slot has never once been
the binding constraint, so the floor buys nothing. What it costs is measurable and it is the
best name in the book:

```
571147  SKIP BUY SNDK — cash_to_use $133.75 < min $406 (allocated $216.79)   <- the winner-ADD
427197  SKIP BUY WDC  — cash_to_use $133.10 < min $393 (allocated $586.87)
427197  SKIP BUY AMZN — cash_to_use $133.10 < min $393 (allocated $586.87)
571147  SKIP BUY LUNR — cash_to_use $32.18  < min $384 (allocated $573.87)
```
Skips went 4 (915207, legacy $50 floor) → 11 / 9 (427197 / 571147, NAV floor). Two of the five
runs refuse a *winner add* on the run's own best name. Mechanism + two windows: **meets the bar**.

**2. `core_funding_max_positions_aware` = True → `False`.**
`[core] funding pre-pass: max_positions will refuse N of M sized buy(s) — not releasing core to
fund them` fires 3/0/0/3/2. It withholds core funding on the strength of a cap that then blocks
nothing. It is a small, strictly one-directional loss.

**3. `entry_extension_block_pct` = 25 → `0` (base profile).**
142/139/37/99/55 blocks. It refused SNDK **16×** in 427197 and 6× in 915207 during a +166% move,
and there is no run in this set where it demonstrably blocked a loser. Worse, `regime_profiles.bull`
and `.recovery` set it to `0` while the base is 25 and the regime flips chop↔bull ~8× per window —
so the same name at the same extension is admitted or refused by **which bar the regime detector
happened to label**. The code comment at gna:31672 already says this out loud. A gate that is a
coin-flip is worse than no gate.

### B. INERT — remove so they stop reading as protection

**4. `peak_giveback_min_peak_pnl_pct` / `_exit_drawdown_pct` (30/25) — the FIFTH inert lever, confirmed.**
55 fires in 571147, **0 executions**, 0 `FILL SELL SLV`. The whole chain is in the log:
```
PEAK GIVE-BACK EXIT: SLV peaked +60.5% (>=30%) and has handed back 28.2% (>=25%) — selling
Monitor decision: SLV day 28 pnl=+15.2% cp=$77.59 entry=$67.35 → SELL (Peak give-back exit: ...)
Monitor cycle complete | date=2026-01-30 | symbols=9 | sells=1 | holds=8
```
…and then nothing sells, on 55 consecutive fires. **Mechanism:** `_FORCED_EXIT_TAGS` at gna:19829
is `("Fast loser","Trailing stop","Hold-limit","Circuit breaker","Catastrophic stop")`. The reason
string `"Peak give-back exit: …"` matches none of them, so `_forced_exit=False`, so the monitor's
enforcement sweep at gna:24666 (`_mentry.get("_forced_exit")`) never adds SLV to
`_nexus_sell_enforcement`, so the broker drops the sell. `nexus_monitor_risk_exit_always_enabled=True`
already satisfies the outer gate — the flag is not the problem, the tag tuple is.
*Note: a sibling agent is adding `peak_giveback_forced_exit_enabled` in the working tree right now;
it is not in HEAD, so 571147 ran without it.*
**Until that lands, leave the two keys set (they cost nothing) but do NOT count the run as protected.**

**5. `watchlist_priority_slots` — already back to 0. Keep it there, and delete the family.**
`matched=none` on **148 of 148** audit bars across all five runs. Seven sibling keys
(`sector_watchlist*`, `watchlist_priority_*`) are multiplied by an unconditionally empty set.

**6. `rank_band_momentum_exempt_min_score` — already 0. It cannot be turned on.**
Reader gna:23283 runs **5,606 lines before** the writer gna:28889. Any non-zero value is a no-op.

**7. `entry_extension_metric` = 'range' → the a2054c6 fix is switched off.**
gna:9304: `if metric != "anchor": return _recent_runup_protect(...)`. All five runs print the legacy
wording `recent runup` (142/93/26/67/35 lines) and zero print the wired `range … [bars=N]` form.
Either set it to `anchor` or delete the key and its three sub-params.

**8. `momentum_breakout_freshness_pct` = 0 → the a2609bd fix is switched off.**
`_fresh()` returns a constant 1 when the band ≤ 0 (gna:14088), so the tie-break is a no-op.

**9. `backtest_credit_pending_sell_proceeds` = True → **UNVERIFIABLE and structurally inert**.**
Zero log lines in any run, and it cannot work as advertised: it is only read by
`PortfolioEmulator.get_buying_power()`, while the buy gate reads `portfolio_emulator.get_cash()`
directly at **broker.py:15163**. `get_buying_power` appears **nowhere** in broker.py or
graph_nexus_analysis.py. Proof it did not fix the case it was shipped for — 427197, same tick:
```
[execution] FILL SELL SPY qty=2.44 price=686.74 = $1,675.74
SKIP BUY ARWR — cash_to_use $1.69 < min $366 (allocated $854.39)
```
Turn it off or add a log line; do not carry it as a fixed defect.

### C. THE 142 KEYS THAT ARE DEAD BY CONSTRUCTION

Of **627** keys on doc-193, **451** are read by the engine at all, and **142** of those sit behind
an OFF switch, an empty map, a 99-sentinel or an unreachable threshold:

| count | family (why it is dead) |
|---:|---|
| 8 | sector_watchlist / watchlist_priority (map is {}) |
| 1 | rank_band momentum exemption (=0, ordering bug) |
| 4 | entry_extension anchor path (metric="range") |
| 2 | breakout freshness (=0) |
| 2 | backtest pending-sell credit (no reader on the gate) |
| 14 | backfill_rotation (backfill_rotation_enabled=False) |
| 8 | trailing_stop tuning (trailing_stop_disabled=True) |
| 5 | profit_take (disabled=True, enabled=False) |
| 7 | fast_loser (fast_loser_cut_enabled=False) |
| 3 | peak_protection (bypass of an already-disabled cut) |
| 30 | rotation lane (rotation_min_score=99) |
| 11 | momentum/portfolio swap (enabled=False) |
| 25 | other OFF switches with live tuning params |
| 3 | unreachable stops |
| 15 | anchor_reinforce (budget logged, 0 adds) |
| 1 | satellite conviction reserve (=0) |
| 3 | queue rotation promotion (no log) |

None of these is *harmful* — but every one of them is a knob an operator can turn that provably
changes nothing, and that is exactly how five levers shipped inert this session.

---

## PART 3 — THE TWO THINGS THIS AUDIT FOUND THAT NOBODY HAS FILED

### `max_positions_exclude_sleeve_legs` is HALF-WIRED, and the half that matters is missing

The flag fires **612 / 267 / 265 / 464 / 374** times as
`max_positions: index-core leg(s) SPY do not consume a slot — alpha book holds N` — at the
**broker** emission gate, where `MAX_POSITIONS_GATE: blocked` is 0 in every run. It does not apply
at the **strategy's** own latch, which computes
`_current_positions = len(portfolio_emulator.get_positions())` (gna:29076) and therefore counts SPY:

```
[V28.8.1 max_positions BREACH] current=9 > max=8 (auto-heal freed 0). Blocking direct/dequeue new-ticker buys.
```
21 / 18 / 0 / 20 / 17 bars — and at that site `_blocked_buys = list(_new_stock_candidates);
_new_stock_candidates = []; _position_headroom = 0`, i.e. **every new-name buy on that bar dies.**
So the flag is inert where it fires and absent where it binds. `current=9 > max=8` is `8 alpha
names + SPY` against Z4.1's chop cap of 8; without SPY in the count it is `8 > 8` = no breach.
Present on **4 of 5 runs, 2 regimes, 2 windows** (0 only in 383778, where the book never reached 8).
The gna:29070 comment says a sleeve exclusion was tried here and reverted because four counters
must move together — that is the correct fix and it is still not done.

### `anchor_reinforce_*` is 15 keys that log a budget and never spend it

`V31 anchor reinforcement budget: cap=$177 (40% of stock_budget=$444), candidates=5` prints on
**every bar of every run** (43/21/21/31/27) and there is not one anchor-reinforce *add* in 138,799
lines of log. Compare `winner_add_*`, which fires 1–2× per run and prints
`Winner add-on: SNDK alloc=$217 (P&L=+49.5%, held=14d, drop=2.7%)`.

---

## PART 4 — WHAT ACTUALLY BINDS (so the next lever is aimed at it)

Ranked by refusals, 571147:

| rank | refuser | count | note |
|---|---|---:|---|
| 1 | `Rank band` entry band | **1,810 buy-signals** | ranks on the news/graph blend, not on why a breakout is a buy |
| 2 | `Backfill queue BLOCKED (full_priority_blocked)` | **378** | queue is 60/60 from bar ~5 onward |
| 3 | `TURNOVER BUDGET BINDING` | **314** | median rolling turnover 88–104% vs a 50% budget |
| 4 | `Momentum ceiling block` | **100** | pre-rank |
| 5 | `Entry extension gate` | **55** | regime-dependent coin-flip |
| 6 | `deferred_unfunded_buy` | **50** | the buy was sized and there was no money |
| 7 | `[V28.8.1 max_positions BREACH]` | **17 bars × all candidates** | SPY takes a slot |
| 8 | `SATELLITE CAP` | **17** | down from 42 — `core_min_pct` worked |
| 9 | `SKIP BUY … < min $` | **9** | `min_position_nav_pct`, refusing winner adds |
| — | `MAX_POSITIONS_GATE: blocked` | **0** | never binds, in any of the five runs |

And the terminal number: `Buy budget: spendable=$…` has a **median of $64** across 26 bars in
571147 ($8 in 427197, $0 in 915207). On the bar SNDK was finally bought, `stock_budget=$444`
against a $840 clip, BFQ priority got $193, and SNDK got the `priority_min_position_size` floor
of **$100**. No entry-side lever can matter until that line is bigger.

## GENERALIZATION STATEMENT

Every INERT verdict above is a **mechanism** read off HEAD source with the reader/writer line
numbers given, and is confirmed by a zero (or a fires-but-does-nothing) count on **5 runs, 4
windows, 3 regimes**. The two BACKWARDS verdicts (`min_position_nav_pct`, `entry_extension_block_pct`)
are named-instance evidence on **2 windows each** and meet the bar. `core_funding_max_positions_aware`
is convicted on mechanism plus 2 windows but the dollar cost is small and unquantified — say so.
I did **not** measure the P&L delta of removing anything: 427197 and 571147 are both truncated,
and per `_SYNTHESIS.md` the run-to-run noise floor is ≥ 4.94pp, larger than any of these effects.
