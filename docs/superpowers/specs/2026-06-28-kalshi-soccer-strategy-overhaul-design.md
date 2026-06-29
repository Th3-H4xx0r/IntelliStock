# Kalshi Soccer Strategy Overhaul — Design (v2, post adversarial review)

**Date:** 2026-06-28
**Status:** DRAFT v2 — revised after a 6-lens adversarial review (verdict on v1: *NO-GO as written*). Pending user approval.
**Scope:** Soccer (World Cup now, season-agnostic after). **Paper-only first**; live only after a statistically-valid CLV gate passes.

> **What the adversarial review changed (v1 → v2):** the deliverable is reframed from "a trading strategy that goes live" to **"a paper-only measurement harness with a statistically-valid go-live trigger."** Six blockers were fixed: aggregate risk caps are not in the live code path (B1), settlement reconcile double-counts P&L (B2), CLV-vs-sharp isn't measurable with current data (B3), the go-live gate was a point estimate that passes a losing strategy ~40% of the time (B4), the headline counterfactual is overfit (B5), and maker-first is impossible without a `post_only` order flag (B6). Plus 11 majors (sizing ambiguity, fee model, liquidity plumbing, phasing). Details in §7.

---

## 0. The honest reframe (read this first)

The current bot lost 63% of a **paper** account in 4 days (no real money lost). The strategy *is* flawed — but the data + adversarially-verified research point to an uncomfortable truth:

> **There is no easy money here.** Realistic edge for a small retail Kalshi soccer account is **~0–3% ROI, often zero**. Near-kickoff soccer prices are roughly *efficient*; a homegrown "team wins" model is **slightly *less* calibrated than the market**; momentum/"sentiment" trading mostly **reverses**; standing tail-hedge "lottery tickets" are the **exact trap** that lost the money. A 21-game sample tells us **nothing** about edge — even our own best-looking filter is two lucky bets from break-even.

**Therefore the deliverable is a measurement harness, not a money printer.** Phase 1 ships: (a) the safety rails that stop the bleeding, and (b) the feedback loop the bot never had, so we can **measure real skill (closing-line value vs a sharp book) on paper**. **Live trading stays OFF** until that measurement clears a real statistical bar. **NO-GO is the default.**

**One-line strategy:** *Be a selective, well-calibrated, maker-first trader that avoids the longshot/draw tax, clears a fee+spread hurdle before every order, sizes at fractional Kelly, holds to free settlement, and grades itself on CLV vs a sharp book over enough bets to be significant — not on short-run P&L.*

---

## 1. Diagnosis — from authoritative data (corrected, deduped)

Source: prod RethinkDB `kalshi_decisions` (59,590 rows) + Kalshi settlement API (ground truth). **Deduped to one net position per (fixture, side)** — re-entries collapsed (the raw fill counts were inflated up to 6× by churn):

| Bucket (deduped) | Positions | Wins | Net P&L |
|---|---|---|---|
| Draws (`-TIE`) | 8 | **0** | **−$58** |
| Longshots (ask <15¢) | ~7 | **0** | **−$33** |
| Mid (15–45¢) | ~10 | 5 | +$… |
| Favorites (ask ≥45¢) | 4 | 4 | +$44 |
| **All sides** | **29** | 7 | **−$30** |

**⚠️ The counterfactual is in-sample and NOT predictive.** Filtering out draws + sub-15¢ longshots yields +$62 (per-side) / +$82 (one-bet-per-match) on these 21 games — **but ~$75 of that is two lucky mid-priced underdogs that happened to win (RSA +$36, TUR +$39).** Strip those two outcomes and the "winning" filter is ≈ **−$7**. **Do not read this as proof of edge.** It is the single best illustration of why we must measure on a large forward sample, not retrofit rules to 21 known results.

**Seven root causes (these ARE robust — large-N literature + mechanics, not our 21 games):**
1. **Favorite-longshot bias / model overconfidence on unlikely outcomes.** Sub-15¢ YES is a robust −EV prior (Kalshi sub-10¢ contracts lose >60%). *Draws are different:* the draw losses are **model miscalibration** (overstating draw prob), not market overpricing — so recalibrate + defer to the market on draws, don't hard-ban them.
2. **No feedback loop** — `realized_pnl_cents`/`outcome`/`clv` are `None` on every row. The bot can't see or learn from results.
3. **Over-trading** — 3.2× turnover in 4 days; same match re-entered up to 7×; many tiny orders each paying the fee round-up tax (~100× costlier than one consolidated order).
4. **Tiny 2–4% edges** erased by fees (`ceil(0.07·C·p·(1−p))` ≈ 3.5¢ round-trip at mid prices; **plus a flat 0.25¢/contract maker fee on big events** like the World Cup, brutal at 15–45¢).
5. **Bad in-play discipline** — flat 50% stop crystallized recoverable noise.
6. **Broken execution** — ~half of orders failed (deprecated `/portfolio/orders` 410, `insufficient_balance`, `exchange_is_paused`).
7. **Reckless sizing** — 40% Kelly, 95% bankroll usage, a `min_stake_frac` *floor* forcing thin edges to be huge, 100-contract single-market bets.

---

## 2. Architecture

A **measurement/execution spine** + a **disciplined value core** + two **strictly-capped, default-OFF satellite sleeves**. Edge comes from *calibration + selectivity + price + execution*, never raw prediction.

```
 SPINE (Phase 1 — build first):
   1a Execution hardening: V2 endpoints, retry/backoff, balance gate,
      order validation, post_only (maker), consolidate orders
   1b Feedback loop: persist orders+fills; POSITION-LEVEL reconcile →
      outcome/PnL/CLV(vs SHARP book); calibration; statistical GO-LIVE GATE
 VALUE CORE (paper):
   A Prediction & calibration  → fair value anchored to external-sharp devig,
     model shrink + cheap-side cap; global Platt/shrink (isotonic later)
   B Selection gate (ONE shared fn) → net-EV-after-fee floor, price band,
     liquidity (fail-open until field dump), 1 leg/match + cooldown + DB lockout
   C Risk/sizing (NEW code IN THE LIVE allocate() path) → fractional Kelly,
     per-trade ceiling, per-match + tournament-day correlation caps,
     open-exposure + committed-at-risk daily caps, max-concurrent
   D In-play exits → thesis-broken exit + post-goal green-up (fresh book poll);
     price-based scale-out (paper-prove); cooldown; 1 entry/match
 SATELLITES (default OFF, enable only after CLV gate passes):
   E Maker / fade-overreaction sleeve  (≤10% bankroll)
   F Tail-hedge sleeve  (≤3% bankroll, rare, calibrated-only)
```

---

## 3. SPINE (Phase 1 — the only thing that ships before live is even considered)

### 1a — Execution hardening  (`client.py`, `engine.py`, `monitor.py`, new `reconcile.validate_order`)
- Migrate `cancel_order`/`list_open_orders`/`get_resting_orders` to V2 `/portfolio/events/orders` (**verify exact paths against `docs.kalshi.com` first**).
- `_request` retry/backoff: retry `{429,5xx}` + `exchange_is_paused` (exp backoff, ≤3 tries); raise typed `KalshiHTTPError` immediately on `{400,401,403,404,410,422}` (insufficient_balance is terminal).
- `get_exchange_status()` cached ~30s → skip the order phase when trading inactive.
- Pure `validate_order(contracts, limit_cents)` → `1≤contracts`, `1≤limit≤99`; reject → `blocked` row, no API call.
- **Pre-trade balance gate:** one balance fetch/tick; `cost = contracts·limit + fee`; track `spent_this_tick`; downsize/block if `cost > 0.97·available`.
- **`post_only` maker support (B6):** add `post_only` + explicit `limit_cents` to `submit_order` (Kalshi V2 supports it; auto-cancels on cross). This is the prerequisite for maker-first; without it "maker-or-skip" is impossible.
- **Consolidate orders:** one order per decision per market per tick (kills the per-cent fee round-up tax).

### 1b — Feedback & reconcile loop  (new `reconcile.py`, `db.py`, `engine.py`, `decisions.py`)
- New `client.get_settlements()` + `get_fills()`; **persist `kalshi_orders` on submit (with the decision-row id) and `kalshi_fills` each tick**; join fills→orders→decisions on `client_order_id`.
- **POSITION-LEVEL reconcile (B2):** aggregate all `placed` rows per `(instance_id, market_ticker)` into one cost-weighted net position; reconcile **once**; write one PnL/CLV record. Until fills exist, tag PnL/CLV `"limit-price estimate"`, not ground truth. (Unit test: 6 placed rows for one ticker → counted once.)
- **CLV vs EXTERNAL SHARP (B3):** a dedicated **near-kickoff closing-line snapshot job** (Pinnacle/Betfair via `odds_api`), budgeted separately from pre-match scans. Define "closing line" precisely for an hourly feed; **discard CLV grades when the snapshot is stale >N min**; on fixtures with no sharp book emit **"no CLV grade"** explicitly. Stamp `league` on reconciled rows. Surface **"graded n" separately from "placed n"** (the gate may be unreachable for minnow-nation games — ~42% of our decisions had no sharp book).
- **Statistical GO-LIVE GATE (B4) — NO-GO by default:** require the **95% one-sided lower-confidence-bound of mean CLV (vs sharp) > a *positive* threshold** (CLV is the primary KPI; SE ≈0.4¢ at n=100). Realized EV/$ is a secondary sanity check only. Document required effect size + power; expect a **weeks-to-months** horizon, and use a **sequential/Bayesian stopping rule** rather than a hard n=100 (trade frequency under the new gate is low). **Never auto-flip `live_enabled`** — operator action only.

---

## 4. VALUE CORE (paper)

### CORE-A — Prediction & calibration  (`fair_value.py`, `intelligence/fusion.py`, `pricing.py`, new `calibration.py`)
- Anchor fair value to the **devigged external-sharp consensus**; blend in **logit space**; raise `w_sharp` 0.70 → **0.85** (defer to the sharp market; our model is not better).
- **Cheap-side overshoot cap** (model may not exceed sharp by more than {0 below 0.20, +0.02 to 0.35, +0.05 above}) and **hard draw-shrink toward sharp**.
- Calibration: **global Platt/shrink first**; defer **per-bucket isotonic until ≥30 settled obs/bucket** (M8) to avoid overfitting ~10/bucket. Devig method is *not* load-bearing on Kalshi (methods converge); apply Shin/Power on the external 3-way book.

### CORE-B — Selection gate  (new `strategy/selection.py`; wired into `candidates.py`, `engine.py`, `live_decision.py` — ALL three)
One pure `SelectionGate.passes(ctx) → (ok, reason)`; every reject writes a `skipped` row with the reason. Defensive exits bypass it.
- **(A) Net-EV-after-fee floor** (uses the *correct* fee model incl. flat maker fee): require ≥ ~2¢ net/contract over spread/2 + fee + slippage.
- **(B) Price band:** reject `<15¢` and `>90¢`; **draws require agreement with the sharp line** (no model-only draws) until recalibrated. *(Dropped the v1 "favor ≥50¢" tilt — it was 4-game noise, M9.)*
- **(C) Liquidity (M4):** require real depth/volume — but **FAIL-OPEN until a one-time live field dump confirms the fields populate** (a "missing→reject" default would halt all trading day one). Build the depth/volume/bid plumbing first; add a **zero-trade alarm**.
- **(D) One leg per match + cooldown + DB lockout:** 1 open winner/double-chance slot per fixture across ticks; reject re-open within `reentry_cooldown_s`; **DB-backed settled-match lockout** + DB-backed one-slot rule so a restart can't bypass it (M-restart).
- **(E) Maker-or-skip:** if it can only fill as a taker on a thin edge, **skip**; post a resting `post_only` bid inside our side of the spread with a requote/TTL lifecycle. Maker-first is *defensive* (avoids the −32% taker quadrant), not a profit engine; **paper-prove maker fill-rate per price-band before live**.

### CORE-C — Risk, sizing & bankroll  ⚠️ **ALL aggregate caps are NEW code in the LIVE `plan_and_allocate`/`allocate` path (B1)**
The v1 caps did not exist in production: `run_instance → orchestrator.plan_and_allocate → capital/planner.allocate` only applied per-bet Kelly + per-market + reserve + the floor. `check_caps` lived only in `engine.plan_orders`, which the live loop **never calls** (only `run_once`/`replay` do). So:
- Thread **live position state** (per-match exposure, total open exposure, day realized+unrealized PnL, day deployed, open-position count) into `allocate()`; enforce every cap there with a greedy fill. **Collapse to ONE decision path** (delete or wire `plan_orders`/`run_once`/`replay`). Add an **integration test that the live loop actually trips each cap**.
- **Sizing (M2 — pin ONE representation):** `kelly_fraction` = the literal final multiple **≤ 0.125** (no separate "×0.5 haircut" prose); edge defined fee-net; one formula, unit-tested (project memory flags `edge/(1-price)` as a prior gotcha — assert it).
- **Per-trade ceiling** (replaces the floor): ~3% bankroll. **Dust handling (M7):** *skip if Kelly stake < 1 contract or < min ticket* — **no upsizing** (a % "dust floor" reintroduces the exact failure it claims to kill). Note quarter-Kelly precision is meaningless below ~$1k — integer-contract sizing is the real model at $100–500.
- **Correlation (M1/M3):** drop the NO-leg netting abstraction (all our legs are YES on mutually-exclusive tickers) → **one YES leg per fixture** is the netting mechanism; add a real **tournament-day bucket cap (~12%)**.
- **Exposure caps:** `max_open_exposure_frac` **10–12%** (M1; held-to-settlement losses realize in correlated evening waves, so the true worst-day is bounded by open exposure, not the loss cap); **committed-at-risk-opened-today** daily cap (M1); daily **deploy/turnover** cap 25%; `max_concurrent_positions`; ≥ ~30% cash.
- **Edge threshold** 0.03 → **0.04**.

### CORE-D — In-play monitoring & exits  (`live/live_decision.py`, `monitor.py`, new `live/orderbook.py`)
- **Primary exit = thesis-broken** (live fair crosses our price); catastrophe backstop only. **No flat-% / no tick-trailing stop** (soccer prices gap on goals).
- **Post-goal green-up (M6):** on a scoreboard change, **fetch the orderbook fresh and re-poll ~5–10s for up to 60s** (not the 30s stale-cache path); flatten/partial-hedge. Promote `scoreboard.py` to a trading input with **exact fixture-ID matching + tests**, or don't call green-up "deterministic."
- **Price-based scale-out (user-intent, §7 disagreement):** add a first-class take-profit/scale-out exit — green-up only catches goal-driven moves, but 16/18 of the in-play spikes had **no** scoreboard change, so a price-based scale-out is the only mechanism that serves "sell as it moves." **Paper-prove on forward data**; do NOT justify it with the back-reconstructed numbers.
- **Anti-churn:** no opens after **75'**; **1 in-play entry per fixture**; post-exit cooldown; requote/cancel stale maker orders. Wire `get_orderbook` as a maker-placement guard only.

---

## 5. SATELLITES (default OFF — enabled only after the CLV gate passes)

- **E. Maker / fade-overreaction (≤10% bankroll, ¼-Kelly, tagged `sleeve='momentum_mm'`).** Momentum-chasing is refuted (≈50% reversal); market-making isn't viable <~$10k (adverse selection ~2× worse in single-game markets). What survives = maker-first execution (in CORE-B) + fade *narrative-only* overreactions (no scoreboard change). Honest expectation: low fill rate → mostly does nothing.
- **F. Tail-hedge (≤3% total / ≤1% per bet / ≤3 positions, held-to-settlement, DEFAULT OFF).** This is the trap that lost the money. Allowed only on a specific calibrated reason a tail is underpriced vs the sharp book (rare). Hard-clamped so the whole sleeve → 0 costs ≤3%.

---

## 6. Config defaults (medium tier, $100–500)

| Knob | Old | New |
|---|---|---|
| `kelly_fraction` (final multiple) | 0.40 | **≤0.125** |
| `min_stake_frac` (floor) | 0.08 | **removed** (skip if <1 contract) |
| `per_bet_cap_frac` (ceiling) | — | **0.03** |
| `per_match_cap_frac` | — | **= per_trade_cap** (DB-backed, restart-safe) |
| tournament-day bucket cap | — | **0.12** |
| `max_open_exposure_frac` | 0.80 | **0.10–0.12** (≥30% cash) |
| committed-at-risk daily cap | — | **new** |
| daily deploy/turnover cap | — | **0.25/day** |
| `bankroll_usage_pct` | 95 | **≤70** |
| `edge_threshold` | 0.02 | **0.04** + net-EV-after-(correct)-fee |
| price band | none | **15–90¢** (no directional favorite tilt) |
| in-play stop | flat 0.50 | **thesis-broken + green-up + scale-out** |
| `no_add_after_min` | 80 | **75** (opens blocked too) |
| `w_sharp` | 0.70 | **0.85** (logit blend + cheap-side cap) |
| tail / momentum sleeves | on | **OFF** until CLV gate passes |
| `live_enabled` | gate | **stays OFF; manual flip after statistical gate** |

---

## 7. Adversarial review — blockers/majors incorporated + open disagreements

**Blockers (all fixed above):** B1 caps not in live path → CORE-C; B2 reconcile double-counts → position-level; B3 CLV unmeasurable → sharp snapshot job + graceful missing; B4 point-estimate gate → 95% LCB, NO-GO default; B5 overfit counterfactual → relabeled in-sample/not-predictive + honest numbers; B6 no `post_only` → added to 1a.

**Majors:** M1 daily-loss cap cosmetic → lower open-exposure + committed-at-risk cap; M2 sizing ambiguity → pin ≤0.125, one formula, assert; M3 NO-leg netting → one-YES-leg/fixture + tournament bucket; M4 liquidity no data → field dump prerequisite + fail-open + zero-trade alarm; M5 fee model wrong → implement BOTH (formula + flat 0.25¢/contract), per-fill; M6 green-up can't fire → fresh book poll + exact scoreboard matching; M7 dust floor → skip-not-upsize; M8 n=100 unreachable/isotonic overfit → sequential CLV stop + global Platt until ≥30/bucket; M9 favorite tilt is noise → dropped; M10 removing floor breaks tests → delete end-to-end + migration + rewrite `test_kalshi_planner.py`; M11 Phase 1 too big → split **1a / 1b / 1c**.

**Open disagreement (recorded, not resolved by fiat):** the User-Intent lens valued a mechanical take-profit highly (a back-reconstructed +$268 on the 4-day sample); the Stats/EV lenses call that overfit (same 21 games, price reconstructed from `fused_fair − edge`). **Resolution:** include a price-based scale-out as first-class (it serves the user's real intent), but **paper-prove it forward**; never cite the +$268.

**Corrections to the review itself:** the live path *does* call `generate_candidates` (the candidate edge filter is live; the dead-path problem is the allocator/caps). A 50+ file kalshi test suite **does** exist in `backend/tests/test_kalshi_*.py` (so "update existing tests" is accurate). And the reviewer's specific "−$37" deduped counterfactual **did not reproduce** on recompute (I get +$62/+$82) — but its *thesis* holds: that number is two lucky outcomes from break-even, i.e. noise.

---

## 8. Phasing (realistic)

- **Phase 1a** — execution hardening + balance gate + `post_only` + endpoint verification. *(Stops the broken-order bleed; small, shippable.)*
- **Phase 1b** — persist orders/fills + position-level reconcile + sharp closing-line snapshot + CLV/calibration + statistical go-live gate (measurement only). *(The feedback loop; the heart of "measure first.")*
- **Phase 1c** — `SelectionGate` + depth/volume plumbing (behind the field dump, fail-open) + CORE-C caps wired into the live `allocate()` path + sizing fixes + test rewrites.
- **Phase 2** — CORE-A calibration; CORE-D in-play exits/green-up/scale-out.
- **Phase 3** — satellites E/F behind flags, only after the CLV gate is met.

**Go-live remains a manual operator decision gated on a positive CLV lower-confidence-bound — not automatic, not this session.**
