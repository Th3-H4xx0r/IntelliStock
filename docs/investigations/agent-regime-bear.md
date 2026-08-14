# Regime detection and the SQQQ bear leg — bt 624674 (bear window) / bt 523085 (control)

**Aspect:** regime calls, bear-leg sizing contract, the −10% leg stop / trailing bank,
and whether the index core is correctly OFF in a bear.

## Evidence base

| | bt 624674 | bt 523085 |
|---|---|---|
| log | `/tmp/bt624674.log` (18,274 lines) | `/tmp/bt523085.log` (40,344 lines) |
| window | 2026-03-02 → 2026-03-30 (L18274) | 2026-01-01 → 2026-03-01 (L40344) |
| instance | `v2-conv-ctl\|4f430a0ae8cdd108951ff2c3` (L57) | same scope (L19) |
| status | `finished, P&L=1461.3937…` (L18272) | `finished, P&L=360.2209…` (L40342) |
| result | $6,000 → $7,461.39 (**+24.36%**), L18245-18247 | +6.00% |
| regime bars | 21 (`closes` 133→153) | 43 evaluations, `closes` 94→132 |
| bear bars | 16 of 21 confirmed bear | **zero** bear bars — bull/chop only |

`L<n>` = 1-based line in the named log. `file.py:<n>` = source line under `backend/`.
No backtest was run for this investigation; every number below is read out of the two
log files or the source.

### What I could NOT determine (stated explicitly)

* **The sleeve/core config values are never printed.** The `Effective config` line
  (624674 L56) covers the nexus strategy levers only — it contains no
  `residual_sleeve_*`, `core_sleeve_*` or `regime_*` key. Everything I assert about
  configuration below is *inferred from behaviour that the log does print*, and I say
  so at each point. This is itself a finding (see D6).
* **Whether `bear_leg_trail_activation_pct` / `bear_leg_trail_pct` are non-zero.** The
  trailing bank emits nothing when disabled *and* nothing when armed-but-not-tripped.
  The absence of a `leg trailing stop` line therefore proves only that **no trail exit
  fired** — it does not prove the trail is off. I cannot separate the two from this log.
* **The `turnover_budget_monthly_pct` threshold.** The lowest observed *binding* value
  is 93% of NAV (624674), so the threshold is ≤ 93%; the exact number is not logged.
* **Any counterfactual.** I did not re-run anything, so no "this would have earned X"
  claim appears below. Where I give a cost I give the observed round-trip prices only.

### Two comparison traps I deliberately avoided

* bt 523085 (2026-01-01→03-01) and bt 624674 (2026-03-02→03-30) are **adjacent, not
  overlapping** windows (`closes` 94→132 vs 133→153 — they abut exactly). They are not
  a treatment/control pair and I never difference their headline returns.
* 523085 contains **no bear bar at all**, so it cannot corroborate or refute anything
  about the bear leg. It is used here only as the source for the regime-proxy defect
  (D4), which it exhibits independently.

---

## Verified CORRECT — the things that are not broken

These were checked and are working. Reporting them matters as much as the defects.

### ✅ The index core is correctly OFF on every bear bar

`core_sleeve.py:163-215` (`core_sleeve_armed_for_bar`) returns `False` when the regime
is known and has no profile — "doc-193 has no `bear` profile" (`core_sleeve.py:210-212`).
The chain holds end-to-end: `_apply_regime_profile` returns the config **unchanged**
when no overlay matches (`broker.py:6110-6111`), so `core_sleeve_enabled` stays absent,
and `_core_sleeve_cfg` returns `None` (`broker.py:3053-3054`).

Measured: bt 624674 emits exactly **7** `[core]` lines, and **all 7 land on a `chop`
bar** — none on a bear bar.

| line | confirmed regime | `closes` | action |
|---|---|---|---|
| L5895-5909 | chop | 138 | bear-leg-skip notice, `band_deploy` 0.0%→40.0% |
| L11729-11742 | chop | 145 | `band_deploy` 0.0%→40.0% |

**The core does not buy in a bear. That part of the contract is honoured.**

### ✅ The −10% leg stop did not fire, and it was right not to

* The stop is genuinely **armed** in backtest. The deploy-side arming block is dead
  (`broker.py:5345`, guarded by `_signal_result_is_confirmed`, which is
  `bool(result) and bool(getattr(result,"filled",True))` — `broker.py:11276-11278` —
  and **all 9 parks log `accepted=True, filled=False`**). But
  `_apply_backtest_confirmed_fill_state` sets `bear_entry_px` off the real fill at
  `broker.py:11547-11551`, so the basis exists.
* First entry 71.53 (L3245) ⇒ stop level **64.38**. The lowest SQQQ print anywhere in
  the log is **69.18** (L3293). The stop was never approached.
* The window **ends in a confirmed bear** (L17568: `closes=153, ret20=-7.60`) with SQQQ
  at its high (89.80, L18253). There was no V-bottom in this window for the stop or the
  trail to catch.

**Zero** `leg stop-loss`, `leg trailing stop`, `bear over: regime=…` or
`residual_bear_full_exit` lines appear in bt 624674. Given the tape, that is correct
behaviour, not a silent failure.

### ✅ Hold-through-chop is working

The confirmed regime drops to `chop` on `closes` 138-141 and 145 while SQQQ is held, and
no protective exit fires. That means `bear_hold_through_chop` is on
(`broker.py:4724-4727`) and it did its job — the leg survived the chop interludes.

### ✅ The $5 minimum-release floor is working

56 sub-$5 refill sells were correctly refused (`broker.py:4580-4587`), keeping $74.10 of
dust orders out of the tape. (Their *logging* is a separate defect — see D5.)

---

## D1 — CRITICAL: the bear leg's first park single-handedly binds the turnover budget and starves the alpha book for the whole window

This is the largest measured effect in the run.

**Chain, every step from the log:**

1. There are **zero** order submissions before L3245 (verified: no
   `SimulationSubmission(` line with a lower index).
2. L3245: `parked $4182.96 in BEAR leg SQQQ @ 71.53 (regime=bear, leg=4183/4183 cap, alloc=70%…)`.
   NAV at that instant = 4182.96 / 0.70 = **$5,975.66**.
3. 18 lines later, L3263: `TURNOVER BUDGET BINDING: 103% of NAV … new discretionary
   BUYS are blocked this tick`. **This is the first binding line in the entire run.**

The ledger reconciles to the cent:

| booked | notional | % of $5,975.66 NAV |
|---|---|---|
| XLE buy (L1775, 15.78338462 × 56.980188) | $899.34 | 15.0% |
| USO buy (L3246, 9.88804453 × 90.456675) | $894.44 | 15.0% |
| **SQQQ park (L3245)** | **$4,182.96** | **70.0%** |
| SQQQ refill sell (L3262) | $163.76 | 2.7% |
| **total** | **$6,140.50** | **102.8% → logged 103%** ✓ |

Without the hedge leg the ledger reads **30.0%** of NAV. The bear leg contributes
**70.8%** of the notional that bound the budget.

`broker.py:3170-3173` is explicit that this is by design: *"The budget exists to throttle
DISCRETIONARY churn. The core is the low-turnover baseline … **The hedge leg and every
satellite name still are** [governed]."*

**Measured consequence:** 279 `TURNOVER BUDGET BINDING` lines, spanning **19 of the 21
regime bars (90% of the window)**, peaking at **244% of NAV** and ending at 130%
(L18240). The alpha book finishes at **2 of 6** allowed slots (L18221-18222:
`alpha book holds 2` / `cap=6`).

So the satellite engine was switched off on the third bar of the window by the hedge's
own sizing, and never switched back on.

**Proposed fix.** Exempt the sleeve/hedge leg from the *write* side of the ledger the
same way the core already is, i.e. in `_turnover_is_governed` (`broker.py:3155`) return
`False` for `_sleeve_symbols(cfg)` — the bear leg is a regime-driven allocation control,
not discretionary churn, and it is already bounded by `bear_alloc_max_pct`. Concretely,
change the guard at the three `_turnover_ledger_record` call sites
(`broker.py:5070-5072`, `4601-4603`, `4664-4666`) to skip when
`symbol == cfg["bear_symbol"]`. If the hedge must stay governed, then it needs its own
budget line rather than sharing the satellite's, because one 70%-of-NAV order
mathematically cannot coexist with a ≤93% monthly cap.

---

## D2 — CRITICAL: same-bar cash double-spend — the park floor is applied N times per bar against a stale `get_cash()`

`_residual_sleeve_deploy` computes two quantities from portfolio state:

```
broker.py:5278   cash = float(portfolio_emulator.get_cash() or 0.0)
broker.py:5300   idle = cash - park_floor_pct * nav
broker.py:5301   cur_val = positions[bsym] * bpx
broker.py:5322   cur_val += _sleeve_pending_qty(portfolio_emulator, bsym, "buy", order_service) * bpx
broker.py:5324   room   = max(0.0, _alloc * nav - cur_val)
broker.py:5325   deploy = min(idle, room)
```

The 2026-08-04 same-bar-stacking fix (documented at `broker.py:5302-5323`) corrects
**`cur_val`** for orders already committed this bar. It does **not** correct `cash`.
Execution is next-event, so `get_cash()` still shows the pre-order balance, and the
dual-cadence loop (MONITOR + FULL) calls deploy two or three times per bar. Each call
recomputes the *same* `idle` and grants it again.

**Signature in the log — two identical notionals then a capped remainder:**

| bar | parks |
|---|---|
| `closes=142` | L9271 **$681.43** @76.19 → L9283 **$681.43** @76.19 → L9294 $568.26 (leg 4573/**4573** cap binds) |
| `closes=146` | L12567 **$716.09** @75.58 → L12579 **$716.09** @75.58 → L12590 $534.62 (leg 4578/**4578** cap binds) |

Byte-identical repeats are only possible if `cash`, `nav` and `park_floor_pct` were all
unchanged between the calls. `room` *was* correctly decremented (3326→4006→4573), which
is exactly the asymmetry: the fix reached `cur_val` and missed `cash`.

**Measured consequence:** cash is drawn far below the park floor. The very next release
reads `cash 1.6% of NAV` (L9337) and `cash 1.3% of NAV` (L12632) against a 15% refill
target — and immediately sells the leg back:

* L9271-9294 buy **$1,931.12 @ 76.19** → L9337 sell **$855.79 @ 73.41** (−3.6%), same bar.
* L12414 sell $843.85 @76.62 → L12567-12590 buy **$1,966.80 @ 75.58** → L12632 sell
  **$902.72 @ 76.70** — sell, buy, sell inside a single regime bar (`closes=146`).

**Proposed fix.** Net same-bar committed buy notional out of `cash` exactly as line 5322
does for `cur_val`:

```python
# broker.py, immediately after line 5278
cash -= _sleeve_pending_qty(
    portfolio_emulator, bsym, "buy", order_service) * bpx
```

and keep it keyed on `current_time` so a new bar starts clean, per the existing
convention. The same one-line omission exists on the SPY/core deploy path
(`broker.py:5110-5150`), which reads `px`/NAV but never nets pending core buys.

---

## D3 — HIGH: the refill is a one-way ratchet — it sells on chop bars where deploy is structurally forbidden

Two gates disagree about which bars the leg may trade on:

* **Adds** are gated on regime: `broker.py:5179` — `if regime in ("bear","crash") and cfg.get("bear_symbol")`.
* **Trims** are not: the demand-refill block at `broker.py:4731-4801` runs whenever the
  leg is held and no exit is due — which, with `bear_hold_through_chop` on
  (`broker.py:4724-4727`), explicitly *includes* chop bars.

So on a chop bar the leg can only shrink.

**Measured over bt 624674, mapping every SQQQ trade to the confirmed regime at that line:**

| | BUY notional | SELL notional |
|---|---|---|
| bear bars | $9,166.25 | $2,766.13 |
| **chop bars** | **$0.00** | **$1,972.07** |

All 9 parks are on bear bars. $1,972.07 of the leg was sold on chop bars with no
possibility of replacement: L5485 $661.39 @76.08, L6564 $789.60 @69.24, L11315 $521.09
@72.51.

**Aggravating factor — the "deep" release floor is applied in the wrong direction.**
`broker.py:5295-5296` states the intent: *"free more cash for the scaled hedge once the
bear is sustained"*. But raising `_release_cash` raises `park_floor_pct = max(buffer,
_release_cash + buffer)` (line 5299), which **shrinks** `idle` and therefore the hedge.
The log shows the deep value is **larger**, not smaller, than the base:

* L5117 `bear-leg refill: cash 4.8% -> target **5%** of NAV`
* L5485 `bear-leg refill: cash 5.0% -> target **15%** of NAV` — a **$661.39 sale at
  76.08**, fired at the exact moment `_bdwell >= bear_scale_min_days`
  (`broker.py:4763-4764`), i.e. when conviction was highest.

The in-code comment at `broker.py:4750-4757` assumes the opposite ordering
("deploy parks cash down to deep+buffer (**7%**) … the un-deepened release_cash_pct
(**15%**)"), so base=15%/deep=5% was the design and this run has them swapped.

**Net effect on the leg.** It bought $9,166 and sold $4,868 of SQQQ — **$14,034 of gross
notional on a $6,000 book in 21 sessions (234% of NAV)** — in a 3x daily-rebalanced
inverse ETF, to carry a position worth $4,745.63 at the end. The summary block records
`SQQQ: P&L = $1,023.50 (**+11.92%**)` on basis (L18263) while
`SQQQ: $70.82 -> $89.80 (**+26.80%**)` (L18268). Measuring from the leg's own first fill
instead of the window open (71.53 → 89.80 = +25.5%) the gap is the same order:
**the leg realised roughly half the move it was positioned for.**
*(Caveat: position size varied through the window, so this is a magnitude, not an exact
attribution — I did not re-run anything to isolate it.)*

**Proposed fix.** Give the refill the same regime gate the add has, and make the band
symmetric. In `broker.py:4758-4764`, skip the demand-refill entirely when
`regime not in ("bear","crash")` (a chop bar that is being *held* through should not be
a bar the leg is trimmed on), and assert at config-parse time in
`_residual_sleeve_config` (`broker.py:2914`) that
`bear_release_cash_pct_deep <= release_cash_pct`, logging loudly and clamping otherwise —
the parameter's stated purpose is to free cash, so a larger value is always a
mis-configuration.

---

## D4 — HIGH: the regime proxy silently switches instrument mid-run, discontinuing ret20/ret5

`_detect_market_regime` picks its proxy by scanning `("SPY","QQQ","VOO")` and breaking on
the **first** one with ≥21 point-in-time closes
(`strategies/graph_nexus_analysis.py:7386-7395`). The overlay bar cache fills during the
run, so SPY goes from "absent" to "full history" mid-backtest and takes over from QQQ.
`ret20`/`ret5` are then computed on a **different index**, with no transition handling.

**bt 523085 — the switch flips the raw label:**

| L | `closes` | proxy | ret20 | ret5 | raw |
|---|---|---|---|---|---|
| 3861 | 95 | QQQ | **−1.68** | −1.74 | chop |
| 4862 | 96 | **SPY** | **+0.47** | −0.37 | **bull** |

`ret20` changes sign across the switch bar and the raw classification goes chop→bull.
A 2.15pp move in a 20-day index return in one session is an instrument change, not a
market move.

**bt 624674 — the switch moves ret5 by 2.27pp:**

| L | `closes` | proxy | ret20 | ret5 |
|---|---|---|---|---|
| 6853 | 140 | QQQ | −1.09 | **+0.99** |
| 7719 | 141 | **SPY** | −2.28 | **−1.28** |

This matters directly for the bear leg, because the fresh-decline gate keys on `ret5`:
`broker.py:5186-5193` skipped the leg four times at `ret5=+1.1%` (L2478-2607) on a QQQ
bar. Whether SPY's ret5 would have passed that gate on the same bar is **not
determinable from this log** — SPY's ret5 is not printed while QQQ is the active proxy —
so I make no claim about what the leg *would* have done. The defect is the
discontinuity itself.

**Proposed fix.** Pin the proxy for the life of a run. Cache the first successfully
resolved proxy in `strategy_cache["_regime_proxy"]` and prefer it on every later bar,
only falling through the candidate list if it stops resolving; log at WARN when the proxy
actually changes. Concretely, in `graph_nexus_analysis.py:7386`, iterate
`(pinned, "SPY", "QQQ", "VOO")` with `pinned` read from the cache, and write the cache on
first resolution.

---

## D5 — MEDIUM: rejected sub-$5 releases are logged as if they filled

`_submit_release` returns `False` for a sub-minimum order (`broker.py:4580-4587`) and
emits `release SKIPPED …`. The caller then logs `released {qty} …` **unconditionally**
(`broker.py:4815-4817`), producing adjacent contradictory pairs:

```
L3277  [sleeve] release SKIPPED SQQQ: $0.22 < $5.00 minimum (bear-leg cash refill)
L3278  [sleeve] released 0.0032 SQQQ @ 69.44 (bear-leg refill: cash 5.0% -> target 5% of NAV, ok=False)
```

56 such pairs occur in bt 624674. This also reveals a non-converging loop: the refill
demand can never be met by an order that is never submitted, so the identical decision
re-fires every tick — L7524/7545/7566/7580/7592 are five consecutive identical
`0.0101 SQQQ @ 70.61` attempts. No live order results (the floor stops it before submit),
so this is a log-fidelity and wasted-work defect, not a trading one.

**Proposed fix.** At `broker.py:4815`, gate the log on the return value —
`if _bok: _log("released …") ` — and add an early `continue`/`return` when
`_bneeded < _RESIDUAL_SLEEVE_MIN_RELEASE_USD` so the demand is not re-evaluated every
tick for an amount that can never be sent.

---

## D6 — MEDIUM: the bear-leg alloc ladder never produced an intermediate value, and the config is unauditable from the log

**All 9 parks report `alloc=70%` — including the first one.**

| L | ret20 at that bar | ret5 | alloc |
|---|---|---|---|
| 3245 (**first park**) | −3.93 | −1.03 | **70%** |
| 9271 / 9283 / 9294 | −3.72 | −2.25 | 70% |
| 10732 | −1.85 | −1.38 | 70% |
| 12567 / 12579 / 12590 | −3.59 | −2.19 | 70% |
| 14034 | −5.94 | −2.09 | 70% |

The ratchet cannot explain the first one: `bear_alloc_ratchet` is process-local, the
restore path is **live-only** (`broker.py:4337-4338` returns unless `mode == MODE_LIVE`,
and neither log contains a `[sleeve] restored …` line), so `prev_ratchet` is 0.0 at
L3245. Feeding that bar's inputs through `_conviction_bear_alloc`
(`broker.py:2792-2826`) with the **shipped defaults** — base 0.35 (2799), max 0.70
(2814), start 4.0 (2818), slope 0.10 (2822), min_days 3 (2819) — gives
`depth = max(0, 3.93 − 4.0) = 0`, so the scaling branch is skipped and the function
returns **0.35**, not 0.70.

So this run's config is not the documented 0.35→0.70 ladder. **I cannot say which
parameter causes it**, because no `residual_sleeve_*` value is printed anywhere in
either log. What is certain from the log alone: the leg opened at its ceiling on its
first park, the ladder never expressed an intermediate value, and D1 shows that a 70%
first park is precisely what bound the turnover budget where a 35% one (30% + 35% = 65%
of NAV) would have stayed under the ≤93% threshold.

Related inference, also from behaviour rather than config: the bear threshold
`regime_bear_spy_drawdown_pct` is ~3.0, not the 5.0 default
(`graph_nexus_analysis.py:7462`) — the deepest `raw=chop` bar is ret20 −2.77 (L9405) and
the shallowest `raw=bear` bar is −3.50 (L126), and the 200-day structural branch (7498)
cannot fire with only 133-153 closes. A 3.0 bear trigger sits *below* the 4.0
`bear_scale_start_pct` default, so with stock defaults the ladder can never leave base
on a freshly-triggered bear anyway.

**Proposed fix.** Emit the resolved sleeve/core/regime config once per run, next to the
existing `Effective config` line (`graph_nexus_analysis.py`, same emitter). One line
listing `bear_alloc_pct / bear_alloc_max_pct / bear_scale_start_pct / bear_scale_slope /
bear_scale_min_days / release_cash_pct / bear_release_cash_pct_deep /
bear_stop_loss_pct / bear_leg_trail_activation_pct / bear_leg_trail_pct /
regime_bear_spy_drawdown_pct` would have made D3's inverted floor, D6's ceiling-pinned
alloc and the trail question in the caveats section all directly readable instead of
inferred. Separately, add a parse-time warning in `_conviction_bear_alloc` /
`_residual_sleeve_config` when `bear_scale_start_pct > regime_bear_spy_drawdown_pct`,
since that combination makes the conviction ladder unreachable by construction.

---

## D7 — MEDIUM: the core's bounded bear de-risk is dead code; a full liquidation runs instead and does not consume the turnover cadence

`broker.py:5099-5102` states the design:

> *"A bear does not turn the core off here either — it scales the TARGET
> (core_bear_scale, to cash) inside core_target_weight, so the de-risk is **bounded and
> reversible instead of a full liquidation**."*

That is not what runs. Because doc-193 has no bear profile, `_core_sleeve_cfg` returns
`None` on a bear bar (D-chain in the "Verified correct" section), so
`_residual_sleeve_release` falls past the core branch (`broker.py:4877-4931`) into the
**legacy** branch:

```
broker.py:4934   protective = regime in ("bear", "crash")
broker.py:4957-4959   else:
                          sell_qty = qty
                          frac = 1.0
```

`core_bear_scale` (default 0.5, `core_sleeve.py:334`) is unreachable for this document.

**Log confirms full liquidation, not a halving** — the message text is the legacy
f-string from `broker.py:4960/4975`, and the source tag is `residual_bull_protective_exit`
(4970), not a `[core] released … bear_derisk` line:

* L9121 `released 1.2453 SPY @ 665.16 (regime=bear protective exit …)` — essentially the
  entire 1.2534-share core bought at L5897.
* L12415 `released 1.2739 SPY @ 657.94 (regime=bear protective exit …)`.

**Second-order defect:** the legacy branch never stamps
`_CORE_SLEEVE_LAST_REBALANCE_KEY`. `broker.py:4909-4912` says stamping the bear de-risk
*"is what caps regime round trips at ~2/yr, since the re-entry then has to wait out
core_rebalance_min_days"*. On the path that actually executes, it is not stamped.
Observed: **2 full core round trips in 21 sessions** (buy L5897 @678.30 → sell L9121
@665.16; buy L11730 @661.54 → sell L12415 @657.94), an annualised ~24/yr against a
design target of ~2/yr. Both round trips lost money; the summary records
`SPY: P&L = $-29.97 (-1.77%)` (L18262).

**Honest framing:** in *this* window going to zero SPY was the profitable choice — SPY
fell 7.89% (L18267). The defect is that the tested, tunable `core_bear_scale` lever is
unreachable and the turnover cap that bounds regime round trips is bypassed, so the
system's behaviour is not the documented behaviour and cannot be tuned toward it.

**Proposed fix.** Resolve the core config regime-aware rather than relying on the
overlay merge: have `_core_sleeve_cfg` fall back to
`config["regime_profiles"][<any enabled profile>]` for the *shape* of the core while
using `core_bear_scale` for the bear *target*, so the bear branch takes
`broker.py:4877-4931` with `_corder.reason == "bear_derisk"` instead of the legacy
liquidation. Minimally, if the full liquidation is intended, stamp
`_RESIDUAL_SLEEVE_STATE[_CORE_SLEEVE_LAST_REBALANCE_KEY] = current_time` in the
`protective` branch at `broker.py:4957-4959` so the re-entry serves the cadence and the
~2/yr cap actually binds.

---

## D8 — LOW (live-only, cannot be confirmed from these logs): the trailing-stop basis is computed differently in backtest and live

Two writers maintain `bear_entry_px` / `bear_peak_px`:

* `broker.py:5348-5365` (deploy) — weighted blend of entry **and peak**, weighting by
  `cur_val/bpx`, which *includes* the same-bar pending reservation.
* `broker.py:11542-11559` (fill handler) — weighted entry off `_bear_confirmed_qty`, and
  `bear_peak_px = max(prev_peak, price)`.

In backtest only the second runs, because `_signal_result_is_confirmed` is `False` for
every simulation receipt (all 9 parks log `filled=False`). In live, legacy adapters
"return fill-like bools" (`broker.py:11277`), so the deploy block *would* run and would
**blend the peak downward** on an average-down add (5360-5363) where backtest takes a
`max()`. The trailing bank therefore arms on a different basis in the two modes.

I cannot demonstrate a consequence from these logs — no trail exit fired in either run —
so this is flagged as a parity risk, not a measured loss.

**Proposed fix.** Delete the entry/peak arithmetic from `broker.py:5348-5365` and let
`_apply_backtest_confirmed_fill_state` (renamed to drop `backtest_`) be the single
writer in both modes, driven off confirmed fills. That is the same "one writer" argument
`broker.py:2973-2975` already makes for `_bear_confirmed_qty`.

---

## Summary table

| # | Severity | Defect | Primary evidence |
|---|---|---|---|
| D1 | CRITICAL | 70%-of-NAV first park binds the turnover budget; alpha book dead for 90% of the window | 624674 L3245 → L3263; 279 binding lines; L18221-18222 (final tick) |
| D2 | CRITICAL | Same-bar cash double-spend; park floor applied 2-3× per bar | identical $681.43/$716.09 pairs L9271-9283, L12567-12579; `broker.py:5278` vs `5322` |
| D3 | HIGH | Refill trims on chop bars where adds are forbidden; "deep" floor inverted | $0 buy / $1,972.07 sell on chop bars; L5117 vs L5485 |
| D4 | HIGH | Regime proxy switches QQQ→SPY mid-run, ret20 sign flip | 523085 L3861 vs L4862; `graph_nexus_analysis.py:7386-7395` |
| D5 | MEDIUM | Rejected releases logged as fills; non-converging refill loop | 56 pairs, e.g. L3277/L3278; `broker.py:4815` |
| D6 | MEDIUM | Alloc ladder pinned at the 0.70 ceiling from park #1; config unauditable | all 9 parks `alloc=70%`; `broker.py:2792-2826` |
| D7 | MEDIUM | `core_bear_scale` unreachable; full liquidation, cadence not consumed | L9121, L12415; `broker.py:4957-4959` |
| D8 | LOW | Trail basis differs backtest vs live | `broker.py:5348-5365` vs `11542-11559`; 9× `filled=False` |

**Direct answers to the questions asked.**

* *Are regime calls correct and stable?* Correct in form — the hysteresis, the
  blind-bar freeze and the bear/chop labels all behave as written. **Not stable**: the
  underlying proxy instrument changes mid-run (D4).
* *Does the bear leg size per its 0.35→0.70 contract?* **No.** It opened at 70% on its
  first park and never expressed an intermediate rung (D6). I cannot attribute that to a
  specific parameter because the config is not logged.
* *Does the 10% stop / leg trail behave?* Neither fired, and on this tape **neither
  should have** — SQQQ's low (69.18) never neared the 64.38 stop and the window closed
  with SQQQ at its high. The stop is genuinely armed in backtest via the fill handler. I
  **cannot confirm whether the trail was configured on at all** (D6 caveat), and the
  arming basis differs between backtest and live (D8).
* *Is the core correctly OFF in a bear?* **Yes** — all 7 `[core]` lines fall on chop
  bars. But the *exit* it takes in a bear is the legacy full liquidation, not the
  documented bounded `core_bear_scale` de-risk (D7).
