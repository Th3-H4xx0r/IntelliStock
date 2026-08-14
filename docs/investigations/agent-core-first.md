# Core-first funding: the SPY core leg claims tick cash ahead of the alpha book

**Scope (ASPECT):** ordering of the SPY core leg vs alpha buys within a tick.
**Evidence base:** `/tmp/bt523085.log` (40,345 lines, run id 523085, window 2026-01-01 → 2026-03-01,
$6,000 → $6,366.10, +6.10%, log L40295-40297) and source under `backend/`.
**No backtest was run for this investigation.** Every claim below cites a log line number (`L…`)
or a source line (`file:line`). Claims I could not support are marked **UNPROVEN**.

---

## 0. Methodology notes / traps avoided

* `_core_sleeve_log_hold` (broker.py:4203-4221) **deduplicates by reason**: it returns without
  logging when `order.reason` equals the previously stored reason. So the **absence** of a
  `[core] hold (deploy|release)` line on a tick does **not** mean the core was not evaluated.
  All "the core was evaluated here" claims below rest on positive evidence (a `[core] …` action
  line, or the `[core] funding request trimmed` line which is emitted unconditionally at
  broker.py:15387 whenever the trim binds), never on silence.
* Full-window vs partial-window numbers are kept apart. `Stock movement (start -> end)` (L40328-40340)
  is a full-window price series; `P&L per stock` (L40315-40327) is realised over the actual holding
  period. E.g. SNDK moved +166.10% over the window (L40338) but the book realised +2.28% (L40324)
  because it held it for 23 of the 56 days (L40247: `Monitor decision: SNDK day 23`). These two are
  **not** comparable and are never differenced here.

---

## 1. The literal emission order in broker.py (answer: the core BUY *is* last in source order)

Inside one iteration of the main `while not shutdown_requested:` tick body:

| # | broker.py line | call | what it emits |
|---|---|---|---|
| 1 | 15134 | `_residual_sleeve_state_restore()` | — |
| 2 | 15135 | `_residual_sleeve_prepare(...)` | — |
| 3 | 15146-15398 | build `_core_funding_request` (satellite-headroom + max_positions pre-pass) | log only (L…`funding request trimmed`) |
| 4 | **15429** | `_residual_sleeve_release(...)` | **core SELL** (and legacy sleeve exits) — *before* any alpha order |
| 5 | **15476** | `for symbol in _exec_order:` | **alpha sells then alpha buys** (`_exec_order = _sell_first + _buy_rest`, broker.py:14603) |
| 6 | **17256** (backtest) / 17195 (live) | `_residual_sleeve_deploy(...)` | **core BUY** — cycle end |

So the premise "the core is funded first" is **false in source order** and **true in funding order**.
The core BUY is emitted last, at 17256, *after* the alpha submit loop. The defect is that emission
order is not funding order.

---

## 2. Why last-emitted still means first-funded

`_submit_deploy` → `_submit_portfolio_signal` → `PortfolioEmulator.execute_signal`, which books a
**cash reservation that outlives the tick**:

* portfolio_emulator.py:1489-1492 — `amount_to_use = min(cash_per_trade, self.get_buying_power(reserved_cash))`
* portfolio_emulator.py:1566 — `self._execution_cash_reservations[order_id] = amount_to_use`
* portfolio_emulator.py:1266-1275 — a reservation is only popped once the order leaves
  `pending_orders`, i.e. **when it fills**.
* simulated_execution.py:150 — `expire_after_quotes: int = 0` ("never expire"), and
  `_passive_limit_for` returns `(None, 0)` unless `PASSIVE_EXECUTION_ENABLED`
  (portfolio_emulator.py:573-574), which is off in this run (all fills are
  `model=equity-measured-v3-nbbo23` marketable, e.g. L3618). **Nothing cancels an unfilled order.**

The alpha buy gate nets those reservations out — broker.py:3754-3798 `_exec_fundable_amount`
(`min(want, get_buying_power(resv))`) — and refuses the buy when the *fundable* amount is below the
min-position floor (broker.py:16537-16612). Its own docstring already names the culprit
(broker.py:3772-3776): *"The SPY core leg is submitted first on the same tick and reserves the cash
the alpha name was sized against."*

**Measured pending windows (submitted → first fill):**

| core order | submitted | filled | ticks pending |
|---|---|---|---|
| $2,400.00 (L1918) | 2026-01-01T01:00 | L3618, quote 2026-01-02 16:00 | 20 |
| $115.41 (L4745) | 2026-01-06T01:00 | L5471 | 5 |
| $1,165.72 (L13194) | 2026-01-17T01:00 | L14816, quote 2026-01-20 16:00 | 20 (**3.6 calendar days, 2 full sessions**) |
| $1,127.69 (L15955) | 2026-01-22T03:00 | L16662 | 3 |
| $965.34 (L23405) | 2026-02-03T01:00 | L24080 | 5 |

---

## 3. DEFECT 1 — 5 of 5 core deploys land on ticks where the alpha lane cannot emit at all

Every `[core] bought` line in the run:

| tick ts (UTC) | log line | notional | tick state |
|---|---|---|---|
| 2026-01-01T01:00 | L1918 | $2,400.00 (0.0% → 40.0% of NAV) | L1908 `symbols=0`; L1909 `strategy idle until next session open`; L1915 `TURNOVER BUDGET BINDING … new discretionary BUYS are blocked this tick` |
| 2026-01-06T01:00 | L4745 | $115.41 | L4734 `symbols=0`; L4735 idle; L4743 budget binding |
| 2026-01-17T01:00 | L13194 | $1,165.72 | L13184 `symbols=0`; L13185 idle; L13192 budget binding (98%) |
| 2026-01-22T03:00 | L15955 | $1,127.69 | L15945 `symbols=0`; L15953 budget binding (98%) |
| 2026-02-03T01:00 | L23405 | $965.34 | L23395 `symbols=0`; L23396 idle; L23403 budget binding (75%) |

**100% of core deployment happened on out-of-session ticks with zero scored symbols and with the
turnover budget blocking every discretionary alpha buy.** The core is explicitly exempt from that
budget (broker.py:4184-4186 forces `_blocked = False` unless `core_respects_turnover_budget`,
rationale at 4167-4183). There is therefore no tick in this run on which an alpha name competed with
the core for the same dollar *at emission time* — the core always ran unopposed and took the cash
first, then held it.

Two mechanisms produce this, both verifiable in source:

**(a) The guard that was supposed to prevent it is dead code in this run.**
broker.py:17235-17242:
```
_skip_eppi   = _dc_bt_sim and _nexus_tick_mode in ("MONITOR", "IDLE")
_skip_snapshot = _dc_bt_sim and _nexus_tick_mode == "IDLE"
```
and `_residual_sleeve_deploy(...)` (17256) sits under `if not _skip_eppi:`. `_dc_bt_sim` is set only
by `nexus_dual_cadence_backtest_simulation` (broker.py:9742-9748) and its RED banner
(broker.py:9781-9790) **does not appear anywhere in the log** (0 hits for
`nexus_dual_cadence_backtest_simulation` and 0 for `BEHAVIOR VALIDATION harness`), so `_dc_bt_sim`
is False and the whole conjunct collapses — deploy runs on **every** tick. The matching release-side
guard has the identical defect: broker.py:15414-15428 `_core_tick_ok` is only narrowed under
`if mode == MODE_LIVE:` or `elif _dc_bt_sim:`, so its log line
`[core] funding release suppressed` has **0 occurrences in the log**.

**(b) The cadence clock phase-locks the core onto the overnight hour.**
`_core_sleeve_days_since_rebalance` (broker.py:4091-4121) returns
`int((now - last).total_seconds() // 86400)` — integer *day* floor of a *timestamped* stamp — and the
stamp is written on the **attempt**, not the confirmation (broker.py:5169, deliberately, to kill the
bt 383711 retry storm). A `funding` release deliberately does **not** stamp (broker.py:4921), and all
11 releases in this run are `funding` (all `source=residual_bull_refill`, e.g. L4273, L17367). So the
only writer of the clock is the deploy itself. Consequence: once the first deploy landed at
**01:00 UTC** (L1918, the *second* tick of the whole run, when `days_since_rebalance` was `None` →
"cold book, allow immediately", broker.py:4094-4095), the next expiry is exactly +5×86400 s → again
01:00 UTC. Observed: 01-01T01:00 → 01-06T01:00 (exactly `core_rebalance_min_days=5`) → 01-17T01:00 →
01-22T03:00 → 02-03T01:00. The core can only ever re-arm at the same out-of-session hour-of-day it
first fired on.

---

## 4. DEFECT 2 — the pending core order consumed 88-91% of tick cash at every alpha buy gate it overlapped

Only **41 of the 634 ticks** in this run ever evaluated a buy gate (`Buy gate inputs for …`).
**6 of those 41 ran while a core buy was still pending — and on all 6, at least one alpha buy was
refused for insufficient fundable cash. Hit rate: 6/6.**

On 5 of the 6 the core reservation is the arithmetically dominant cause (≥88% of the gap between
`cash` and `fundable`):

| tick | `cash` (get_cash) | `fundable` (buying power) | pending core order | core share of the gap | alpha buys refused |
|---|---|---|---|---|---|
| 2026-01-02T15:00 | $2,640.00 (L3371) | $240.00 (L3372) | $2,400.00 (L1918) | **$2,400.00 / $2,400.00 = 100.0%** | BA, LMT |
| 2026-01-19T15:00 | $1,299.21 (L13631) | $133.49 (L13632) | $1,165.72 (L13194) | **$1,165.72 / $1,165.72 = 100.0%** | AMZN, SKYT, SNDK |
| 2026-01-20T15:00 | $1,299.24 (L14562) | $133.52 (L14563) | $1,165.72 | **100.0%** | AVNT, CYTK, ORLY, TT |
| 2026-01-22T15:00 | $1,260.91 (L16421) | $77.51 (L16422) | $1,127.69 (L15955) | $1,127.69 / $1,183.40 = **95.3%** | SCHW, USB |
| 2026-02-03T15:00 | $1,097.41 (L23828) | $99.20 (L23829) | $965.34 (L23405) | $965.34 / $998.21 = **96.7%** | SNDK, CTRA, MOVE |
| 2026-01-06T15:00 | $1,909.02 (L5230) | $0.00 (L5231) | $115.41 (L4745) | 6.0% — **BALL/CCK dominate here, not the core** | OI |

The first two rows reconcile **to the cent**: `$2,640.00 − $2,400.00 = $240.00` and
`$1,299.21 − $1,165.72 = $133.49`. The residual $55.71 / $32.87 on rows 4-5 is **UNPROVEN**; it is
consistent with `_withheld_cash()` (the T+1 unsettled slice, portfolio_emulator.py:476) but I have no
log line that states it.

**14 alpha buy refusals across those 5 ticks are core-attributable.** The two on 2026-01-02 are the
highest-conviction names in the entire run: BA and LMT, both `raw=+1.800`, both granted
`SATELLITE OVERFLOW … funding $1,921 of room out of the core` (L3369, L3376) *and*
`TURNOVER BUDGET BYPASS … the brake is for churn, not for the trade that matters` (L3370, L3377),
both `Buy gate … → PASS` (L3371, L3378) — and both killed by `fundable $240.00 … < min $360`
(L3372, L3379). Neither was ever bought.

**Note on the log string itself:** `orders already in flight *this tick* reserve the rest`
(broker.py:16542) is misleading. On 2026-01-02 the reserving order was submitted **20 ticks / 38
hours earlier**. The message actively directs an investigator away from the true cause.

---

## 5. DEFECT 3 — the core cannot give the cash back: `need` is computed against `get_cash()`, not spendable cash

`core_rebalance_order`'s funding branch (core_sleeve.py:587-595):
```
need = max(0.0, funding_request - max(0.0, cash))
if need > 0.0 and core_value > 0.0:
    sell = min(need, core_value)
    if sell >= MIN_CORE_ORDER_USD:   # $5, core_sleeve.py:68
        return RebalanceOrder(notional=-sell, reason="funding", ...)
```
and `cash` is supplied by broker.py:4148 as **`portfolio_emulator.get_cash()`** — raw cash, with **no**
subtraction of `_execution_cash_reservations`. This is the exact asymmetry the alpha lane already
fixed for itself in `_exec_fundable_amount` (broker.py:3754-3798).

This is confirmed to the dollar three separate times in the log
(`funding_request − get_cash() = released notional`):

* 2026-01-07: trim `$1,796 -> $213` (L6146) → released 0.1770 SPY @ 691.81 = **$122.44** (L6147);
  the very next alpha line is `SKIP BUY GBDC — cash_to_use $90.20` (L6155). $213 − $90.20 = $122.80. ✔
* 2026-02-03: trim `$3,497 -> $1,107` (L23816) → released 0.0136 SPY @ 695.39 = **$9.46** (L23817),
  against `cash=$1097.41` (L23828). $1,107 − $1,097.41 = $9.59. ✔
* 2026-01-28: trim `$2,681 -> $253` (L20117) → released 0.0075 SPY @ 695.53 = **$5.18** (L20118). ✔

So on the starved ticks the release did **nothing**, by arithmetic:

| tick | trimmed funding request | `get_cash()` | `need` = req − cash | actual release | `need` had `cash` been the *fundable* number |
|---|---|---|---|---|---|
| 2026-01-02 | $1,921 (L3361) | $2,640.00 | 0 | none | $1,921 − $240 = **$1,681** |
| 2026-01-19 | $1,275 (L13625) | $1,299.21 | 0 | none | $1,275 − $133.49 = **$1,141.51** |
| 2026-01-20 | $1,277 (L14546) | $1,299.24 | 0 | none | $1,277 − $133.52 = **$1,143.48** |
| 2026-01-22 | $1,263 (L16404) | $1,260.91 | ≈$2.09 → below the $5 floor | none | $1,263 − $77.51 = **$1,185.49** |

(The trim log prints `${:,.0f}`, broker.py:15388, so these are ±$0.50; the margins above are large
enough that the conclusion is unaffected — except row 4, where the ±$0.50 does not change the
"below $5" verdict.)

And on **2026-01-02 the release could not have fired at all**: broker.py:4853-4856 —
```
sym = cfg["symbol"]; qty = positions.get(sym, 0.0)
if qty <= 0: return          # silent early return, before the core branch at 4877
```
The core held **0 SPY shares** that tick (its first fill is L3618, later in the same tick) while
holding a **$2,400 cash reservation**. For 20 ticks the core owned neither the shares nor the cash in
any form the alpha book could reach.

---

## 6. What it cost, measured

* **Gross one-way SPY notional: $10,791.76** (5 buys $5,756.28 + 11 sells $5,035.48, from the 16
  `FILL … SPY` lines: L3618, L4540, L5471, L6401, L9227, L14816, L15710, L16662, L17625, L20383,
  L22230, L24080, L24081, L24976, L25814, L27556) on a $6,000 book, for a **net +1.0457 shares /
  $720.80**. 15.0x gross notional per net dollar allocated, on the one lane exempt from the turnover
  budget.
* The core leg's realised P&L: **−$3.63 (−0.06%)** (L40326), final position 1.0457 sh = $717.50
  (L40309), 11.3% of the $6,366.10 final NAV.
* **SNDK**: refused 9 consecutive times (L8994, L9920, L10838, L11770, L12717, L13649, L15477,
  L21048, L23829) before entering 2026-02-04 at $617.42 (L24977). Two of those refusals are
  core-reservation-caused (L13649 with $1,165.72 pending; L23829 with $965.34 pending); four are the
  separate `SATELLITE CAP` trim-below-floor defect (L8991/8994 `trimmed $860 -> $221 … < min $368`,
  and L9917, L10835, L11767). SNDK moved +166.10% over the window (L40338); the book realised +2.28%
  (L40324) over the 23 days it held it (L40247). **These two percentages are not comparable and the
  difference is not a P&L attribution** — it only shows the entry was late.

---

## 7. The minimal change that makes the core emit last (and what is unsafe about each)

### Fix A (primary, smallest, addresses §3): don't let the core buy on a tick the alpha lane never ran

broker.py:17235-17242 — introduce a flag *independent of* `_dc_bt_sim` and wrap **only** the
`_residual_sleeve_deploy(...)` call at 17256 (and its live twin at 17195):

```python
_core_deploy_ok = _nexus_tick_mode not in ("MONITOR", "IDLE")
...
if _core_deploy_ok:
    _residual_sleeve_deploy(portfolio_emulator, prices, current_time, _cached_strategies, None)
```

**Unsafe / must be flagged:**
1. **Do NOT widen `_skip_eppi` itself.** It also gates `_ensure_prices_include_positions`
   (17243-17247) and `save_portfolio_snapshot` (17260-17261). Changing it changes NAV marks, the
   equity curve, HWM and the drawdown circuit — a far larger blast radius than the sleeve.
2. **Measured, this fix alone would have deployed the core ZERO times in this window.** All 5 deploys
   were on idle ticks (§3). That is a *strategy change*, not a bug fix, and it strands cash — exactly
   the failure broker.py:15208-15219 documents ("`insufficient_cash` went 7 → 71 … after 01-26
   nothing traded for five weeks"). It must be paired with Fix B so the core can still deploy on a
   FULL tick.
3. **UNPROVEN:** the value of `_nexus_last_tick_mode` on these ticks. The strings `MONITOR` and
   `IDLE` have **0 occurrences** in this log, so I cannot verify from the log whether the gate would
   read `IDLE` (blocking) or something else (no-op). This must be instrumented before shipping.

### Fix B (pairs with A, addresses §4): size the core buy against buying power, not raw cash

broker.py:4148 currently:
```python
cash = float(portfolio_emulator.get_cash() or 0.0)
```
Pass buying power net of in-flight reservations into the **`cash=` argument only** of
`core_rebalance_order` (broker.py:4193), reusing the existing helper so both lanes agree:
```python
_spend = _exec_fundable_amount(portfolio_emulator, cash)   # broker.py:3754
...
core_rebalance_order(core_cfg, nav=nav, core_value=core_value,
                     satellite_value=satellite_value, cash=_spend, ...)
```

**Unsafe / must be flagged:**
1. **`satellite_value` must keep raw `get_cash()`.** broker.py:4165 computes
   `satellite_value = nav - cash - core_value - hedge_value`. Substituting the smaller number there
   *inflates* satellite by the reservation amount, which lowers `core_target_weight`
   (core_sleeve.py:372) and can trigger a spurious core **SELL** for cash that is only temporarily
   committed. Getting this wrong converts the fix into a new churn source. Change the `cash=`
   argument only.
2. **Live must be untouched.** `get_buying_power` exists only on `PortfolioEmulator`
   (broker.py:3778-3780); `_exec_fundable_amount` already takes the identity path for every live
   adapter. Do not re-implement the netting inline.
3. **Cadence interaction.** A smaller `cash` shrinks `_spendable` (core_sleeve.py:668-675) and can
   flip a deploy to `deploy_below_min` (core_sleeve.py:681). Because the cadence clock stamps on the
   **attempt** (broker.py:5169), a deploy refused for size still burns a full
   `core_rebalance_min_days` window. Relaxing that stamp re-opens the bt 383711 retry storm
   (broker.py:5153-5162). Net: this is not free, and it needs its own test.

### Fix C (addresses §5, but the most dangerous — recommend NOT shipping as-is)

Applying the same buying-power substitution to the **funding release** branch would, on 2026-01-19,
have produced `need = $1,141.51` and a release capped at `core_value` (~$735) — enough to fund
AMZN/SKYT/SNDK at the $379 floor. **But the dominant reservation on those ticks is the core's own
pending BUY.** Netting it would make the core sell ~$735 of SPY while it has $1,165.72 of SPY buy
in flight — a wash trade, and the exact inverse of the churn the funding-release reserve was written
to stop (core_sleeve.py:405-470). The correct form is either (a) net out **non-core** reservations
only, or (b) **cancel the core's own pending buy** before releasing. (b) is impossible today:
`expire_after_quotes = 0` and there is no cancel path (simulated_execution.py:150; nothing in
broker.py or portfolio_emulator.py sets `expire_after_quotes`).

### Also worth fixing (cheap, no behaviour change)

broker.py:16542 — `orders already in flight this tick reserve the rest` is factually wrong for
cross-tick reservations (proved in §4: 20 ticks). Log the reserving order ids/symbols and their
submit timestamps instead. This is the single line that made the defect invisible for this long.

---

## 8. Direct answers to the ASPECT questions

**Exact emission order in broker.py:** release 15429 → alpha `_exec_order` loop 15476 → deploy 17256.
The core BUY is emitted **last**. The premise "funded first" is nevertheless correct, because a
cycle-end core order holds a cash reservation (portfolio_emulator.py:1566, released only on fill at
1266-1275, never expires per simulated_execution.py:150) that is netted out of the *next* tick's alpha
buy gate (broker.py:3754-3798) — so funding order ≠ emission order.

**How much tick cash the core consumed before alpha names were sized:** on the 5 buy-gate ticks where
a core order was pending and dominant, the core held **$2,400.00 / $1,165.72 / $1,165.72 / $1,127.69 /
$965.34** — i.e. **100.0%, 100.0%, 100.0%, 95.3%, 96.7%** of the cash-to-fundable gap, and
**90.9%, 89.7%, 89.7%, 89.4%, 88.0%** of total tick cash. 14 alpha buys were refused on those ticks,
including the run's two highest-conviction names (BA, LMT, `raw=+1.800`).

**Minimal change to make the core emit last:** Fix A (gate the deploy call on the alpha lane having
run) + Fix B (`cash=` → `_exec_fundable_amount(...)` at broker.py:4148/4193). Fix A alone silences the
core entirely in this window; Fix B alone leaves §3 intact. Neither is safe without the caveats above,
and Fix C should not ship in its naive form.
