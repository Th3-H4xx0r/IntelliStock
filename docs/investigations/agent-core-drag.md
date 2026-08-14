# Is the SPY core lane helping or hurting? — bt 523085 / bt 102463

**Scope.** Read-only forensics on two finished backtests. No backtest was run; nothing outside
this file was written; nothing was pushed.

**Sources.**
* `/tmp/bt523085.log` — id 523085, instance `v2-conv-ctl`, 2026-01-01 → 2026-03-01,
  final $6,366.10, **+6.10%** (L40296, L40297).
* `/tmp/bt102463.log` — id 102463, instance `v2-conv-trt`, same window,
  final $6,667.41, **+11.12%** (L40785, L40786).
* `backend/core_sleeve.py`, `backend/broker.py`, `backend/portfolio_emulator.py`,
  `backend/simulated_execution.py`.

Both runs print an **identical** `Effective config | ...` line (523085 L18, 102463 L18), but their
graph draws diverge from the first pick (different 1-hop seed counts and different opening
baskets), so they are two *draws*, not an A/B on the core. **The 5.02pp P&L gap between them is
not attributed to anything in this document** — it is inside the ~10pp noise floor. Every finding
below is present in *both* runs.

---

## VERDICT

**Hurting.** The core lane is the largest single consumer of turnover in the book and returned
approximately zero, and its execution mechanics actively refuse the satellite buys it exists to
fund.

| | bt 523085 | bt 102463 |
|---|---|---|
| SPY one-way notional traded | **$10,791.76** (16 fills) | **$5,733.16** (6 fills) |
| …as a share of *all* traded notional in the run | **44.1%** ($24,477.63 total) | **41.3%** ($13,898.41 total) |
| …as a share of starting NAV ($6,000) | **179.9%** | **95.6%** |
| SPY lane P&L | **−$3.63 (−0.06%)** (L40326) | **−$2.12 (−0.07%)** (L40809) |
| SPY itself over the window | **+0.64%** ($681.82 → $686.16, L40339 / L40819) | same |
| Mean NAV in SPY (daily obs. from first core fill) | **13.8%**, median 11.9%, n=40 | **12.6%**, n=40 |
| Share of every SPY *share* bought that was sold back within 1–3 sessions | **86.4%** | **76.6%** |

Notional totals, fill prices, fees and quote timestamps are parsed from the `[execution] FILL …`
lines (e.g. 523085 L3618, L4540, L14816, L15710, L16662, L17625, L24080, L24976). The daily NAV
series is the `Budget split: portfolio_total=$N` line (e.g. L13501); the SPY share count is
integrated from the fills, and the mark is the nearest preceding
`Monitor decision: SPY … cp=$N` (e.g. L3769).

### The lane traded 180% of NAV to earn nothing, in an index that rose

Half-spread measured directly off the only same-bar buy/sell pair in the data —
523085 L24080 `FILL BUY SPY … price=694.697238` and L24081 `FILL SELL SPY … price=691.522794`,
both `quote=2026-02-03 16:00:00+00:00` — is **22.90 bps** one-way, which agrees with
`core_sleeve.py:77-78` ("the half spread (22.8 bps under `equity-measured-v3-nbbo23`)"). Applied to
$10,791.76 that is **$24.71 of spread plus $0.32 of fees ≈ $25.0**, i.e. 0.42% of the $6,000 book
and 6.8% of the run's entire +$366.10 P&L, spent to end the window with a −$3.63 lane while the
index it tracks rose +0.64%.

### The core never held anything like its target

The first deploy is logged at 523085 L1918 as `band_deploy: 0.0% -> 40.0% of NAV`. After
2026-01-05 the position is at **11.3%–14.0% of NAV on 36 of 40 dated observations**, with three
one-to-two-session excursions to 27–30%. From 2026-02-10 to 2026-02-27 the share count is frozen
at 1.0457 (no SPY fill after L27556, `quote=2026-02-09`) at 11.3–11.8% of NAV. So the lane is
neither a 40% core nor a funding source — it is a ~12%, ~$720 stub that is bought and sold
around.

### Was the core actually *sold to fund* conviction buys?

Only in part. Of $5,035.48 of SPY sold in 523085, **$4,985.86 (99.0%)** was unwinding a SPY buy
the core itself had made 1–3 sessions earlier:

| core BUY | $ | next SELL | $ | % of the shares bought, sold back |
|---|---|---|---|---|
| 2026-01-02 | 2,398.48 | 2026-01-05 | 1,667.36 | 69.1% |
| 2026-01-06 | 115.41 | 2026-01-07 | 122.27 | 100.0% |
| 2026-01-20 | 1,152.59 | 2026-01-21 | 1,114.24 | 97.0% |
| 2026-01-22 | 1,127.65 | 2026-01-23 | 1,127.93 | 100.0% |
| 2026-02-03 | 962.14 | 2026-02-04 | 954.06 | 100.0% |

The residual $49.62 of SPY sold is six dust `funding` releases of $5.18–$10.59 (L9227, L20383,
L22230, L24081, L25814, L27556) — every one of them far below the ~$370 minimum the buy gate then
applied to the name they were raised for, and each paying ~23 bps. The one release that
demonstrably worked is 2026-01-05 ($1,667.36, L4540), which funded BALL + CCK on 2026-01-06
($1,703.39, L5472/L5473).

---

## DEFECT 1 — the cycle-end core deploy is blind to the satellite's funding request, and its in-flight order reserves the cash the satellite was sized against

**Source.** `_residual_sleeve_release` passes the live demand through:

```
broker.py:4879-4881    _corder = _core_sleeve_decide(
                           portfolio_emulator, prices, current_time, cached_strategies,
                           cfg, _core, funding_request=funding_request)
```

`_residual_sleeve_deploy` — the site that actually *executes* core BUYS (`broker.py:5144-5150`) —
does not:

```
broker.py:5134-5136    _corder = _core_sleeve_decide(
                           portfolio_emulator, prices, current_time, cached_strategies,
                           cfg, _core)
```

and `_core_sleeve_decide` defaults it away (`broker.py:4126  funding_request=0.0`). The deploy
branch of `core_rebalance_order` therefore sizes off raw cash with no knowledge of demand:

```
core_sleeve.py:668     _spendable = max(0.0, float(cash or 0.0) - cfg.cash_floor_pct * nav)
core_sleeve.py:675     buy = min(drift_usd, _spendable)
```

`cash` here is `portfolio_emulator.get_cash()` (`broker.py:4148`), which is neither the buying
power the emulator will fund from nor net of the satellite's queued demand.

**Consequence, measured.** Because execution is next-event
(`NextEventExecutionSimulator`, `simulated_execution.py:383`; orders sit in `_pending` until the
next quote), the core's BUY sits unfilled and its cash is booked into
`_execution_cash_reservations`, which `_exec_fundable_amount` (`broker.py:3754-3798`) then
subtracts from every satellite buy on the same tick. Its own docstring names the cause:
*"The SPY core leg is submitted first on the same tick and reserves the cash the alpha name was
sized against"* (`broker.py:3772-3776`).

**15 of the 25 `orders already in flight this tick reserve the rest` refusals in bt 523085 fall
inside an unfilled SPY-deploy window** (2 of 6 in bt 102463):

| core deploy decided | $ | filled | satellite names refused in between |
|---|---|---|---|
| 2026-01-01 (L1918) | 2,400.00 | 2026-01-02 16:00 (L3618) | BA, LMT (L3372, L3379) |
| 2026-01-06 (L4745) | 115.41 | 2026-01-06 16:00 (L5471) | OI (L5231, `fundable $0.00`) |
| 2026-01-17 (L13194) | 1,165.72 | **2026-01-20 16:00** (L14816) | AMZN, SKYT, SNDK, AVNT, CYTK, ORLY, TT (L13632–L14584) |
| 2026-01-22 (L15955) | 1,127.69 | 2026-01-22 16:00 (L16662) | SCHW, USB (L16422, L16430) |
| 2026-02-03 (L23405) | 965.34 | 2026-02-03 16:00 (L24080) | SNDK, CTRA, MOVE (L23829–L23843) |

**The canonical bar.** 2026-01-16: VICR is sold for $1,077.85 (L12962). Eight lines later the
core swallows the proceeds — L13194 `[core] bought $1165.72 SPY @ 691.58 (band_deploy: 11.6% ->
30.2% of NAV)` — and the order does not fill for **two trading sessions**. On 2026-01-19 the
allocator sizes AMZN, SKYT and SNDK at $885.03 each; all three PASS the cash gate
(L13631/L13641/L13648: `cash=$1299.21 … available=$1299.21 cash_to_use=$885.03 → PASS`) and all
three are then refused: `fundable $133.49 of cash_to_use $885.03 … < min $379`
(L13632/L13642/L13649). $1,299.21 − $1,165.72 = **$133.49 to the cent.** Two sessions later the
core sells $1,114.24 of that same SPY back (L15710) to fund SKYT and SNDK — which are refused
*again* on that tick for cash (L15470, L15477).

**Opportunity cost.** SNDK traded at $413.55 on 2026-01-19 (L13645) and is marked at $635.94 in
the final portfolio (L40308); the run's own summary reports SNDK $237.33 → $631.54, +166.10%
(L40338). A $885.03 clip at $413.55 held to the end is **≈ $476 of P&L, larger than the run's
entire +$366.10.** SNDK was in fact refused for cash on **8 separate ticks** in 523085 between
2026-01-12 ($388.46) and 2026-02-03 ($655.38), and finally bought on 2026-02-04 at $617.42 for a
realised **+$18.84 (+2.28%)** (L40325). In bt 102463 SNDK was refused on **10 ticks** from $363.01
(2026-01-09, L8268) to $644.90 (2026-02-04, L25156) and **never bought at all** — it appears in
neither the final positions block (L40791-L40798) nor the per-stock P&L summary (L40804-L40813),
and by 2026-02-26 the strategy itself refuses it: L40163 `Entry extension gate: SNDK recent runup
+28.5% > 25% — buy blocked`.

*This is an estimate, not a measurement:* it assumes the refused clip would have been sized at
$885.03 and held to the window end, and ignores the monitor/exit machinery that might have cut it.

**Proposed fix.**
1. `broker.py:5134-5136` — thread the funding request into the deploy site exactly as the release
   site does. `_residual_sleeve_deploy` must accept and forward `funding_request` so the call
   becomes `_core_sleeve_decide(..., cfg, _core, funding_request=funding_request)`.
2. `core_sleeve.py:668` — reserve pending satellite demand before sizing the deploy:
   ```python
   _spendable = max(0.0, float(cash or 0.0)
                         - cfg.cash_floor_pct * nav
                         - max(0.0, float(funding_request or 0.0)))
   ```
   This is the same idea as `_FUNDING_RELEASE_RESERVE` (`core_sleeve.py:470`) but applied to
   demand that has not yet been released, which is the direction the existing reserve does not
   cover: the reserve guards *release → re-buy*, and every round trip in the table above is
   *deploy → release*.
3. `broker.py:5144` — size the core's own buy against
   `portfolio_emulator.get_buying_power(sum(_execution_cash_reservations.values()))` rather than
   `get_cash()`, so the core cannot ask for money the emulator will not fund.

---

## DEFECT 2 — a `funding` release cannot fund the buy it was sized for: it fills one bar late and its proceeds are invisible to the buy gate

**Source.** The buy gate's sizing ceiling is raw cash:

```
broker.py:16229   _cash_now = float(portfolio_emulator.get_cash() or 0.0)
broker.py:16253   _sizing_ceiling = _cash_now
broker.py:16259   if _anchor_policy and mode == MODE_BACKTEST:      # <-- only lane upgraded
broker.py:16271       _bp_cached = float(portfolio_emulator.get_buying_power(_anchor_resv) or 0.0)
broker.py:16273       _sizing_ceiling = max(_cash_now, _bp_cached)
broker.py:16294   available = max(0.0, _sizing_ceiling - reserved_total - _effective_floor)
```

So only the **anchor-reinforcement** lane sees buying power in backtest. Every ordinary satellite
buy — including every conviction/overflow buy the core released for — sizes against pre-release
cash.

The repo already contains the diagnosis and the credit machinery:
`PortfolioEmulator.pending_sell_proceeds` (`portfolio_emulator.py:413-430`) says verbatim
*"The index core submits its funding SPY sell on the SAME tick as the buy it is funding, so the
buy is sized against a cash balance that does not yet contain the money raised for it… 41
cash-bound buy events, $14,801.27 of approved size refused, and $14,084.02 of that (95.2%) was a
same-tick core release that had been submitted and not filled."* But the credit is gated behind
`credit_pending_sell_proceeds`, which is **default False** (`portfolio_emulator.py:291`,
`get_buying_power` at `:477`) and is only set from `backtest_credit_pending_sell_proceeds`
(`broker.py:14998-15000`). Neither log contains a `Sell-proceeds credit:` line
(`broker.py:16287`), and the gate readings below show it was off.

**Log proof — bt 523085, 2026-01-21, one execution block:**

```
L15455  [core] funding request trimmed $2,638 -> $1,252 — satellite headroom will refuse the remainder…
L15456  [core] released 1.6309 SPY @ 677.66 (core rebalance: funding (29.6% -> 29.9% of NAV))
L15467  SATELLITE OVERFLOW: SKYT raw=+1.700 >= 1.50 — funding $1,252 of room out of the core
L15469  Buy gate inputs for SKYT: cash=$146.68 … available=$146.68 cash_to_use=$146.68
L15470  SKIP BUY SKYT — cash_to_use $146.68 < min $377 (allocated $879.44)
L15477  SKIP BUY SNDK — cash_to_use $146.68 < min $377 (allocated $879.44)
L15710  [execution] FILL SELL SPY qty=1.63091190 price=683.201892  quote=2026-01-21 16:00:00
```

The sell is submitted in the same block as the buy gate, but the gate runs on the 15:00 quote
(`SKYT @ 2026-01-21 15:00:00`, L15466) and the sell fills at 16:00. The buys it was raised for are
refused. The next session the core buys it straight back:

```
L15930  [core] hold (release) — funding_release_reserved: core 12.1% vs target 30.0% of NAV
L15952  [core] hold (release) — band_deploy: core 12.1% vs target 30.0% of NAV
L15955  [core] bought $1127.69 SPY @ 685.34 (band_deploy: 12.1% -> 30.0% of NAV)
L16662  [execution] FILL BUY SPY qty=1.63383924 price=690.186933 quote=2026-01-22 16:00:00
```

$1,127.65 bought back = **101.2%** of the $1,114.24 released the previous session. The whole cycle
repeats on 2026-01-23 (release $1,127.93 at L17625; ALSN and BABA refused for `cash_to_use
$133.29` at L17381/L17388). Same shape in bt 102463 on 2026-02-04/05 (deploy $726.07 at L24731 →
CRCD, AMZN, SNDK refused `fundable $97.09 of cash_to_use $822.40` at L25137–L25156 → release
$683.84 at L26051).

**What I could NOT establish.** The `funding_release_reserved` holds are logged only at the
*release* call site — which discards a positive notional by design (`broker.py:4884-4889`) — while
`core_rebalance_order` has already run its deploy branch and can have burned a unit of the credit
(`core_sleeve.py:695 _consume_funding_reserve_decision()`). That *suggests* the reserve is
spending its budget on evaluations that were never going to place an order, contradicting its own
contract (`core_sleeve.py:466-468`: "A decision is consumed ONLY when the reserve changes the
outcome"). **I cannot prove it from the logs**, because `_core_sleeve_log_hold` dedupes on the
reason string across both call sites (`broker.py:4213-4216`), so a deploy-site hold carrying the
same reason would be suppressed. What *is* proven is the outcome: the reserve did not prevent the
re-buy on any of the three occasions it fired in 523085 (L12986, L15930, L23197) or the one in
102463 (L24562).

**Proposed fix.**
1. Hoist `broker.py:16259-16273` out of the `if _anchor_policy` guard, so in `MODE_BACKTEST`
   *every* buy sizes against `get_buying_power(reservations)`:
   ```python
   if mode == MODE_BACKTEST and hasattr(portfolio_emulator, "get_buying_power"):
       _bp_cached = float(portfolio_emulator.get_buying_power(_resv, prices=prices) or 0.0)
       _sizing_ceiling = max(_cash_now, _bp_cached)
   ```
   That single change makes the gate agree with `_exec_fundable_amount`, which is already the
   number the trade is actually measured against 250 lines later.
2. Set `backtest_credit_pending_sell_proceeds: true` in the run spec so
   `get_buying_power` credits the 95% settled slice of the still-pending core release
   (`portfolio_emulator.py:477-478`) — the mechanism written for exactly this bug is shipped and
   switched off.
3. Make the funding release *precede* the buy loop in fill order, or (cheaper, no execution-model
   change) have `_core_sleeve_decide`'s funding branch emit the release one bar **ahead** of the
   demand it serves rather than on the same bar.

---

## DEFECT 3 — the satellite-headroom trim produces numbers below the execution floor: it guarantees the buy is refused *and* zeroes the core's `need`, so the core stops selling entirely

**Source.** The funding request is clamped to satellite headroom:

```
broker.py:15369-15396   _fr_room = _core_sleeve_satellite_headroom(...)
                        _fr_capped = _fr_allow_plain + _fr_allow_conv
                        if _fr_capped < _core_funding_request:
                            _log("[core] funding request trimmed $A -> $B — satellite headroom
                                  will refuse the remainder; releasing core for it would only be
                                  bought back")
                        _core_funding_request = _fr_capped
```

and the core then computes its release as

```
core_sleeve.py:587      need = max(0.0, float(funding_request or 0.0) - max(0.0, float(cash or 0.0)))
core_sleeve.py:588-595  if need > 0.0 and core_value > 0.0: … return RebalanceOrder(notional=-sell, reason="funding")
```

**If the trimmed request is ≤ the cash already on hand, `need` is 0 and the core releases
nothing** — even though the cash on hand is below the buy gate's minimum clip and every satellite
name is about to be refused.

**Measured.** Pairing each `funding request trimmed` line with the first `Buy gate inputs …
cash=$N` on the same tick:

* bt 523085 — **25 of 40** trims (62%) left `need == 0`.
* bt 102463 — **22 of 24** trims (92%) left `need == 0`.

The tail of 523085 is the pure form. For **13 consecutive sessions, 2026-02-11 → 2026-02-27**, the
trimmed request is $154–$182 while cash is $196.21–$196.63:

```
L29094  [core] funding request trimmed $1,723 -> $182 …          (cash $196.24)
L29107  SKIP BUY MAR — cash_to_use $181.68 < min $369 (allocated $181.68)
…
L39792  [core] funding request trimmed $1,760 -> $163 …          (cash $196.63)
L39805  SKIP BUY ERIC — cash_to_use $162.97 < min $377 (allocated $162.97)
```

Over that stretch the core holds **$712.74–$724.76 of SPY (11.3%–11.8% of NAV)**, does not trade
once (last SPY fill L27556, `quote=2026-02-09`), and the last core hold reason logged is
`within_band` (L24999). Releasing **$180 — 0.26 shares, 2.9% of NAV** — on any one of those 13
sessions would have cleared the gate. The core refused because the trim told it nobody wanted the
money, and the trim said that because the money it would have released is what makes the buy
legal.

**The same defect on the clip side.** `SATELLITE CAP: X trimmed $A -> $B` sets
`cash_per_trade = _sat_room` with no check against the execution floor:

```
broker.py:15954-15958   if cash_per_trade > _sat_room:
                            _log(f"SATELLITE CAP: {symbol} trimmed ${cash_per_trade:,.0f} -> ${_sat_room:,.0f} …")
                            cash_per_trade = _sat_room
```

The `min_fill` guard that would catch this exists three lines below but is scoped to
`_anchor_policy` only (`broker.py:15959-15968`). Result:

* bt 523085 — **46 of 47** trims (98%) landed below the ~$360–$383 execution floor; **44 of 47**
  were followed within 15 lines by `SKIP BUY` of the same symbol (e.g. L11767 `SNDK trimmed $890
  -> $191` → L11770 `SKIP BUY SNDK — cash_to_use $191.40 < min $382`). 32 distinct names.
* bt 102463 — **43 of 56** (77%) below the floor; **49 of 56** followed by a `SKIP BUY`. 28
  distinct names (e.g. L8264 `SNDK trimmed $861 -> $167` → L8268 `SKIP BUY SNDK … < min $369`).

There is already a constant for this exact idea — `_CORE_MIN_SATELLITE_TRIM_USD = 25.0`
(`broker.py:3255`, *"Below this much satellite headroom, refuse rather than trim"*) — set roughly
15× below the floor it needs to respect.

**Proposed fix.**
1. `broker.py:15907` — compare headroom against the *execution* floor, not $25:
   ```python
   _sat_floor = max(_CORE_MIN_SATELLITE_TRIM_USD,
                    _exec_min_position_floor(_core_sleeve_cfg_raw(_cached_strategies) or {}, nav))
   if _sat_room <= _sat_floor:
       … skip …
   ```
   A trim to a size the next gate will refuse is strictly worse than a skip: it burns the
   candidate and leaves the book holding the index.
2. `broker.py:15396` — a funding request must be either **fundable or zero**. Replace
   `_core_funding_request = _fr_capped` with
   ```python
   _fr_floor = _exec_min_position_floor(cfg, nav)
   _core_funding_request = _fr_capped if _fr_capped >= _fr_floor else 0.0
   ```
   so the core either releases enough for the buy to clear the gate or does not churn at all.
   Combined with (1) this closes the deadlock in both directions.

---

## Secondary observations (evidence-backed, lower confidence / lower value)

* **Estimated capital-allocation cost.** In bt 523085 the daily mean SPY holding was $861.30
  earning −0.42% on that capital, while the satellite lane earned +$369.73 (= total +$366.10
  minus the SPY lane's −$3.63) on a daily mean satellite balance of $4,967.27, i.e. **+7.44%**.
  Re-rating the SPY capital at the satellite rate gives **≈ $64, about 1.07pp of the $6,000
  book**. *This is an estimate*: the satellite balance is `NAV − cash − SPY` with cash
  forward-filled from the buy-gate diagnostics, and 1.07pp is well inside the ~10pp noise floor,
  so it does not stand on its own. The case against the core rests on the mechanism and the
  crowd-out, not on this number.
* **Stale comment, contradicted 900 lines earlier.** `broker.py:4180` claims *"core notional is
  still BOOKED into the ledger, so the budget continues to see the whole picture"*, but
  `_turnover_is_governed` returns `False` for the core symbol (`broker.py:3207-3208`), so core
  notional is neither recorded nor read. The exemption itself looks correct here — the 56%
  `TURNOVER BUDGET BINDING` on tick 1 (L1915) equals the four $839.97 satellite opens on a $6,000
  book, with the $2,398.48 SPY deploy excluded — but the comment should be corrected before it
  costs someone a cycle.
* `_core_sleeve_satellite_headroom` calls `satellite_design_share(cfg)` / `satellite_max_share(cfg)`
  **without** `regime=` (`broker.py:3523-3524`), although both functions take a regime-aware
  path for `core_target_pct` that exists specifically because that key lives only in
  `regime_profiles` (`core_sleeve.py:218-255`). I did not find a log line that isolates the
  consequence, so I am flagging it as a code smell, not a measured defect.

## Explicit non-findings / limits

* The run's strategy config is not printed. `core_target_pct` (**inferred 0.40** from
  `band_deploy: 0.0% -> 40.0% of NAV`, L1919), `min_position_nav_pct` (**inferred ≈0.06** from
  `min $369–$384` against NAV $6,113–$6,384), `core_funding_release_reserve_decisions`,
  `satellite_conviction_reserve_pct` and `core_min_pct` were **not** read directly. Treat those
  two inferences as inferences.
* I could not attribute the reserve's decision-budget consumption to a specific call site
  (see Defect 2), because the hold log dedupes by reason across both sites.
* All counterfactual dollar figures (the ≈$476 SNDK number, the ≈$64 re-rating) are estimates
  under stated assumptions and were not produced by re-running anything.
* No backtest was run. No file outside `docs/investigations/agent-core-drag.md` was written.
