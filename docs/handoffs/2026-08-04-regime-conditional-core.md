# Regime-conditional index core — session record 2026-08-04

Continues `2026-08-03-full-session.md`. That session ended with "do NOT enable
`core_sleeve_enabled` on doc-179" and a queued three-arm test. This session found
the three-arm test was asking the question wrongly, built the config that answers
it, and verified the mechanism.

**Live state unchanged: `alpaca-main` and `main` remain STOPPED. doc-179 UNTOUCHED.
Nothing in this session touched real money.**

---

## 1. THE CONFIGURATION

`core_sleeve_enabled` is not a global switch. It belongs in a REGIME OVERLAY.

The two measured arms each won exactly where the other lost:

| window | control doc-185 | 4-key cut doc-184 |
|---|---|---|
| bull 03-30..04-27 (SPY +12.79%) | +2.30% | **+16.02%** |
| bear 03-02..03-30 (SPY −7.89%) | **+10.07%** | −2.71% |

That split is mechanical, not statistical: `core_sleeve_enabled` routes bear
de-risk to CASH and never parks the inverse leg, so arming it globally costs the
SQQQ hedge that IS the control's entire bear result (SQQQ +$676 vs SPY −$165).

**doc-187** puts the five `core_*` levers in `regime_profiles.bull` and
`.recovery` ONLY, and the three churn levers (`min_hold`, `rank_band`,
`turnover_budget`) in the BASE. Bull arms the core; bear falls through to the
base and keeps its hedge.

This is legal because `_apply_regime_profile` merges the overlay into the shared
spec config *before* the sleeve reads it (broker.py: "before gna/the sleeve read
the SHARED spec"), and `core_sleeve_enabled` is not in
`_REGIME_PROFILE_BASE_ONLY_KEYS`. Pinned by
`backend/tests/test_regime_conditional_core.py` (8 tests), including the
bull→bear transition and the fail-closed case.

Docs/instances built: **186** = churn levers only (`v2-churn-only`),
**187** = regime-conditional core (`v2-regime-core`).

### Corrections to the prior record

- The "4-key" bundle is really **12 keys** (5 `core_*`, 2 `min_hold`,
  3 `rank_band`, 1 `turnover_budget`).
- `core_target_pct` does NOT set the core's normal weight. The target is
  `clamp(1 − cash_reserve_floor_pct − satellite, 0.30, 0.98)` and it REUSES
  `cash_reserve_floor_pct`. To raise bull beta, lower that inside the bull
  overlay — not `core_target_pct`.
- `core_sleeve.py`'s header claimed "PROTOTYPE, not wired, NO call sites". False
  since 2026-08-03 and it cost this session real time. Fixed.

---

## 2. THE METHODOLOGICAL FINDING — spliced windows are biased

**The bear and bull windows are adjacent halves of one path, and comparing a
hedged arm to an un-hedged arm across the splice is invalid.**

- The bear run **ENDS** 03-30 holding SQQQ at **69.3% of NAV @ $89.80**, on a
  ~+25% gain, trailing stop armed.
- The bull run **STARTS** 03-30 from fresh cash and **re-buys SQQQ near that same
  top**, riding it to the −10% stop for −$180.

The splice converts one winning hedge into a losing one. It penalises every
hedged arm and flatters the un-hedged core. On the **compounded** path both arms
already beat SPY (+4.23%): control 1.1007 × 1.0230 = **+12.6%**, cut
0.9729 × 1.1602 = **+12.9%** — they simply earn it in opposite regimes.

**Therefore the primary test is the continuous 2026-03-02..04-27 span (SPY
+4.23%), not the spliced pair.**

---

## 3. RESULT — bt 581982, doc-187 on the spliced bull window

Mechanism **CONFIRMED**; headline return poor, for the reason above.

| metric | doc-187 (581982) | control doc-185 (264106) |
|---|---|---|
| return | +1.61% (SPY +12.79%, **−11.18pp**) | +2.30% |
| turnover | **38.4×/yr** | 79.2×/yr |
| exec cost | **0.68%** of book | 1.41% |
| trades | 22 | 104 |
| SPY P&L | **+$419** | +$81 |
| SQQQ P&L | −$257 | −$181 |

The core **armed and held**: SPY reached 97.4% of NAV by 04-06 and stayed at
80–98% for **234/312 bars (75%)**, versus the control decaying
43.7% → 32.9% → 22.6% → 0% because its residual sleeve triggers on a CASH band
that every satellite trade drags it across.

**A prediction I got wrong:** I pre-registered +3% to +7%; actual was +1.61%,
below the control. I assumed the SQQQ transition loss would match the control's
−$181; it was −$257. The entire deficit vs control is that one leg — the core
itself did its job. Beta also ran ~90%, not the ~60% I expected, because the
churn levers suppress satellite buys so the core clamps toward `core_max_pct`.

---

## 3b. RESULT — bt 471471, doc-187 on the CONTINUOUS span (THE GOAL TEST)

2026-03-02..04-27, one book across bear → bull. **Both pre-registered pass
conditions met.**

| metric | doc-187 (471471) | benchmark |
|---|---|---|
| **return** | **+9.39%** | SPY **+4.23%** → **+5.16pp** |
| bear leg | NAV 6000 → **6494** by 03-28 (+8.2%) | positive ✓ |
| **SQQQ P&L** | **+$322** | vs −$257 on the spliced bull |
| SPY P&L | +$326 | |
| satellite | **−$88** (AIFD −47, AGIX −19, BOTZ −16, AIQ −6) | negative |
| turnover | 43.6×/yr | |
| exec cost | 1.55% of book (56 days) | |

The hedge is carried through the downtrend and **banks a gain** instead of being
re-bought at the bottom — precisely the splice artifact predicted in §2. The core
then takes over and holds SPY at 89–98% from 04-10 to the end.

It MISSED the "strong pass" bar of +10%. And it is ONE path over 8 weeks, on the
same stretch every prior config was tuned on.

## 3c. TWO SLEEVE PATHOLOGIES FOUND BY THAT RUN — both unfixed

Confirmed from `backtest_trades` (cash + market value reconciles exactly to
portfolio value), **mechanism NOT established**:

1. **Bear leg breaches its own NAV cap.** 2026-04-01T13:00, two
   `residual_bear_deploy` buys at the same timestamp: $4,395 (exactly
   0.70 × NAV, correct) then $1,669 more → **94.8% of NAV in SQQQ**, a −3x
   daily-rebalanced inverse ETF, against a documented 70% ceiling.
2. **Refill overshoots and liquidates the hedge.** 2026-04-06T13:00, fourteen
   `residual_bear_refill` SELLS in one bar, geometrically decaying (598, 537,
   482, 433, 389, 349, 313, 281, 252, 227, 203, 183, 164, 147). The FIRST one
   was correct — cash $334 against a 15%-of-NAV target of ~$928 is a ~$594 gap,
   and $598 closed it. The other thirteen fired anyway, selling **~$4,558 of a
   ~$4,700 leg — ~78% of the hedge — to raise cash it already had.**
   The harm is the OVERSHOOT, not the slicing: spread is charged per notional,
   so splitting one clip into fourteen costs little by itself.

### (2) is now PROVEN — `sell_fraction` compounding

`PortfolioEmulator.execute_signal` takes **no absolute share count on the sell
side**. Its entire sell rule is:

```python
total_shares = positions[ticker] - reserved_shares
shares = total_shares * frac          # frac < 1.0
```

The sleeve sizes the refill in dollars, converts it to
`_bfrac = _bsell_qty / bqty` against a `bqty` read earlier, and passes the
FRACTION. Re-invoked in the same bar, that same ~10.2% is applied to a shrinking
position — a geometric series. `reserved_shares` smooths it further, which is
what produces the clean 0.898 ratio. Reproduced exactly (sizes, ratio, first
clip $598, >78% of the leg) in
`backend/tests/test_sell_fraction_compounding.py`, which also shows the safe
shape: re-derive the fraction from the LIVE position and it sells once.

**(1) — the deploy cap breach — remains UNEXPLAINED.**
`backend/tests/test_residual_sleeve_same_bar_stacking.py` pins the cap invariant
but does NOT reproduce it; the stale-`cur_val` hypothesis passes unmodified.
**A green run there does not mean (1) is fixed.**

### Both are PRE-EXISTING, and therefore LIVE today

The control doc-185 — what `alpaca-main` runs now — shows the **same two clusters
at the same timestamps**: 2 `residual_bear_deploy` orders on 04-01T13:00
($4,389 + $567) and 14 `residual_bear_refill` orders on 04-06T13:00. Control
peaks at **78.1%** of NAV in SQQQ, also above the 70% cap.

So this is not a doc-187 regression — it is an existing real-money defect.
doc-187 does not introduce it but **amplifies the magnitude** (94.8% vs 78.1%),
because suppressing satellite churn leaves more idle cash for the sleeve.

⚠️ **Both must be resolved before any live use.** The +9.39% above was earned by
a book that spent bars at ~95% in a leveraged inverse ETF.

## 3d. RESULT — bt 245632, doc-187 OOS/chop 2026-07-07..08-01 — **FAILED**

Pre-registered fail condition met: worse than the baselines.

| metric | doc-187 (245632) | baselines (475798 / 383711 / 669068) |
|---|---|---|
| return | **−3.37%** (SPY −0.60%, **−2.77pp**) | −2.22 / −1.92 / −1.87 (−1.62 / −1.33 / −1.28pp) |
| turnover | **30.9×/yr** (best of any run here) | 66.5 / 56.2 / 32.3 |
| exec cost | **0.49%** of book (best) | 1.06 / 0.89 / 0.51 |

The churn levers did exactly what they were built to do — lowest turnover and
lowest execution cost of any run on this window — and the config still lost more.
Attribution says why:

| component | P&L |
|---|---|
| SPY core | **+$16** (tracked a flat market to ~zero, correct) |
| satellite (BSP, AMAT, REPL, MRNA, AIQ, PSLV) | **−$222** |

No hedge fired (0/288 bars — the window never confirmed bear), so this window is
a clean read on core + satellite alone.

Comparability caveat, pre-registered: the baselines ran at `granularity_sec=900`
(1152 bars) and this at 3600 (288). Only the **vs-SPY** column is a fair
comparison — and on that basis −2.77pp is still worse than all three.

**Verdict: doc-187 is validated through a bear→bull turn and NOT validated in
chop.** Reporting it as "goal achieved" would be the max-of-N error this project
has already made once.

## 3e. HEAD-TO-HEAD — bt 884112, the CONTROL on the same continuous span

This is the comparison a "should we change the live config?" decision needs:
doc-185 is what doc-179/`alpaca-main` does today. Same window, same build.

| | doc-187 (471471) | control doc-185 (884112) |
|---|---|---|
| **return** | **+9.39%** (**+5.16pp** vs SPY) | **+3.02%** (**−1.20pp** vs SPY) |
| trades | 41 | 112 |
| turnover | 43.6×/yr | 60.8×/yr |
| exec cost | 1.55% of book | **2.16%** |
| SQQQ hedge | +$322 | +$336 |
| **SPY core** | **+$326** | **+$69** |
| satellite | −$88 | −$224 |
| bars with core ≥40% of NAV | 189/612 | **2/612** |

**doc-187 beats today's live configuration by +6.37pp, and the control loses to
SPY.** The decomposition is unusually clean: the hedge contributed almost
identically in both arms, the satellite was negative in both, and essentially the
ENTIRE gap is the core — held at 89–98% versus peaking at 42.6% and decaying to
17.6% (`ARMED but NOT HELD`), which is exactly the cash-band churn the core sleeve
exists to replace. About 0.6pp of the gap is pure execution-cost saving.

The control ran on the WARM salt (accumulated Nexus state), which if anything
advantages it, so this margin is conservative — as pre-registered.

## 3f. A CRASH BUG THAT BLOCKS ENABLING THE CORE AT ALL (found by accident, FIXED)

bt 311771 (doc-188, OOS window) died at bar 18 with:

```
File "/app/broker.py", line 14022, in <module>
    if cash_to_use > _sat_room:
NameError: name 'cash_to_use' is not defined
```

The 2026-08-03 "standing satellite weight cap" reads `cash_to_use` about **80
lines above its first assignment** (`cash_to_use = cash_per_trade`). The block is
module scope inside `for symbol in _exec_order:`, so the name survives between
iterations — it "works" only when an EARLIER iteration already bound it.

**The first buy taken while the index core is armed raises NameError.**

- doc-187 never tripped it: its windows OPEN IN A BEAR with the core off, so
  ordinary buys bound the name long before the core armed.
- doc-188's OOS window opens in a confirmed BULL — core armed before any buy.

⚠️ **This is why `core_sleeve_enabled` cannot simply be switched on in live.** A
live broker process that restarts and then takes its first buy with the core
armed would crash the tick. It is latent today only because doc-179 has no
`core_sleeve_enabled`.

**FIXED** (local, unpushed): trim `cash_per_trade` instead — it is bound at the
top of the loop and every downstream sizing path derives from it
(`cash_to_use = cash_per_trade`, later `min(cash_per_trade, available)`), so the
cap now binds on every buy lane rather than on one assignment a later line could
overwrite. Guarded by `backend/tests/test_buy_loop_name_binding.py`, a static AST
check that the first reference to each sizing local in that loop is a STORE. The
guard was verified to FAIL against the pre-fix source.

**Consequence for tonight:** doc-188 could not be measured on the OOS window,
because the deployed engine still carries the bug and a fix requires a redeploy
(which kills in-flight runs). 188-CONTINUOUS does run, because it opens in a bear.

## 4. ATTRIBUTION — the satellite is a drag, the core is the strategy

bt 581982, cleanest split measured:

| component | P&L |
|---|---|
| SPY core | **+$419** |
| SQQQ hedge | −$257 |
| entire satellite (CAR, ETH, HAPN, LITE, AAOI) | **−$70** |

Seventh consecutive run with negative satellite gross alpha. The index core
earned everything; the graph's stock picks lost money.

**Next experiment this implies:** suppress the satellite entirely
(`allocation_execute_min_raw_score: 99` — NOT 0, which is falsy and silently
falls back to 0.35) and run core + hedge only.

---

## 5. NEW DATA SOURCE — `backend/edgar_fundamentals.py` (built, tested, unwired)

True point-in-time fundamentals from SEC EDGAR XBRL. `factor_profitability`'s own
header says the 120-day `DEFAULT_REPORTING_LAG_DAYS` is "a PROXY and a strictly
worse one than the truth" and that swapping it is a one-function change; this is
that function.

- Every XBRL fact carries `filed`, which populates `FiscalPeriod.filed_at`, which
  WINS over the lag heuristic.
- **Restatements are kept as separate vintages**, ascending by `filed`, so
  `select_period`'s stable sort resolves to the latest vintage public at `as_of`.
  Collapsing them would reintroduce the lookahead this exists to remove.
- Verified live: AAPL 0.543 / MSFT 0.313 / KO 0.282 / NVDA 0.742 GP/A, all in
  Novy-Marx's plausible range, **recovering 69–90 days of signal** the heuristic
  discarded.
- Coverage gap, failing closed: issuers with no `GrossProfit`/`CostOf*` tag (e.g.
  XOM) yield no opinion rather than an invented one.
- 10 tests, offline. **Default OFF, zero call sites.** Intended as a VETO, not a
  selector (Bessembinder; and Grinold forbids concentrating a negative-alpha
  signal).

---

## 6. VERIFICATION

- Suite: **4342 passed, 19 failed** — exactly the pre-existing red-by-design
  failures (`test_adv_exit_discipline_findings` 11, `test_core_sleeve_adversarial`
  7, `test_zz_adversarial_sweep` 1). +18 passed = this session's new tests. No
  regressions.
- `gitnexus detect_changes`: risk **low**, 0 affected processes.
- Nothing pushed — a push redeploys and kills in-flight backtests.

## 7. BUDGET

Backtests on docs 184–187 bill **Azure** (`gpt-5.4-nano`), not OpenRouter, so the
OpenRouter balance is not the constraint. Caveat: that Azure model has no pricing
configured, so `LLMUsage.total_cost_usd` records 0.0000 — unpriced, not free.
**Backtesting doc-179/alpaca-main WOULD spend OpenRouter** (Nemotron 3 Ultra).

The real constraint is wall clock: ~11.8 s/bar, ~1 h per 28-day run at
granularity 3600, and the engine SERIALIZES.
