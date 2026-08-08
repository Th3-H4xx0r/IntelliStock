# Capital and cash — why the book sizes a winner correctly and then cannot pay for it

Read-only investigation, 2026-08-08. No code changed, no backtest started, nothing pushed.
Runs examined: **725146** (STOPPED ~78%, negative), **820236** (+12.33%), **613166** (+9.17%).
All logs pulled with `python3 scripts/pull_backtest_logs.py <id>` and analysed offline.
Every number below is counted off the run log; every mechanism claim carries a `file:line`.

---

## 0. The one-paragraph answer

On the bars where a conviction buy was sized and then refused for cash, **the money was
already in an order.** The core had, on that same bar, submitted exactly the SPY sell that
would have paid for the buy — and backtest execution is next-event, so the sell does not
credit cash until the *top of the next bar* (`broker.py:12132-12147`). The buy gate reads
`portfolio_emulator.get_cash()` (`broker.py:15095`), which is the pre-sell balance. Across
the three runs, **41 conviction buy-gate events on 31 bars were cash-bound for a total of
$14,801.27 of refused allocation, and $14,084.02 of that (95.2%) is matched, to the dollar,
by a SPY funding sell submitted on the same tick with `accepted=True, filled=False`.** A
further, independent leak sits *below* the gate: a still-in-flight SPY **core deploy** holds
a cash reservation the gate cannot see (`portfolio_emulator.py:1497`), and
`execute_signal` re-clamps the order to `get_buying_power(reserved)`
(`portfolio_emulator.py:1414-1423`) — which is how a gate that logged
`available=$808.71 cash_to_use=$805.24 → PASS` produced a **$87.45** fill.

---

## 1. The capital path, in execution order

| # | Where | What it does |
|---|---|---|
| 1 | `graph_nexus_analysis.py:28142-28147` | Buy budget floors at 10% of **NAV**, not cash: `Buy budget: spendable=$0 (cash=$17, sells=$0, floor=$120…)` → `Buy budget floor: $17 -> $621 (floor=10% of $6209)`. Sizing off NAV is deliberate — the core is meant to be sold. |
| 2 | `broker.py:14258-14264` | `_core_funding_request` = Σ `buy_cash` over `nexus_executable_buys`. |
| 3 | `broker.py:14346-14389` | `core_funding_max_positions_aware` pre-pass drops buys `max_positions` will refuse — **except conviction names, which are force-added back** (`:14365-14375`). |
| 4 | `broker.py:14409-14432` | `_fr_room` = `satellite_design_share × NAV − satellite`; `_fr_room_conv` = same at `satellite_max_share` (`broker.py:3305-3396`, `core_sleeve.py:212-309`). `_fr_allow_plain = min(plain, _fr_room)`; `_fr_allow_conv = min(conv, _fr_ceiling − _fr_allow_plain)`. Logs `[core] funding request trimmed $X -> $Y`. |
| 5 | `broker.py:14450-14464` | `_core_tick_ok` zeroes the request on MONITOR/IDLE ticks. **Never fired in any of the three runs (0 occurrences of `[core] funding release suppressed`).** |
| 6 | `broker.py:14465-14474` → `4460-4514` → `core_sleeve.py:455-459` | Release sizes `need = max(0, funding_request − cash)`, `sell = min(need, core_value)`, submitted via `_submit_release` → `_submit_portfolio_signal` with `order_source="residual_bull_refill"` (`broker.py:4147-4187`). **Next-event: accepted, not filled.** |
| 7 | `broker.py:15056-15087` | `_cash_floor = _initial_value × cash_reserve_floor_pct` = **$120 fixed for the whole run** (2% × $6,000). Released to `_effective_floor = 0` only when `high_conv AND open_pos >= 5` (`cash_reserve_hard_min_positions`). |
| 8 | `broker.py:15095`, `:15119`, `:15143-15144` | `_cash_now = get_cash()`; `_sizing_ceiling = _cash_now`; `available = _sizing_ceiling − reserved_total − _effective_floor`; `cash_to_use = min(cash_per_trade, available)`. |
| 9 | `broker.py:15303-15322` | `cash_to_use < $50` → `SKIP BUY … insufficient_cash`. |
| 10 | `portfolio_emulator.py:1414-1423` | **Second, invisible clamp:** `amount_to_use = min(cash_per_trade, get_buying_power(reserved_cash))`, where `get_buying_power = _cash − _withheld_cash() − Σ in-flight buy reservations` (`:402-420`, `:1497`). |
| 11 | `broker.py:15847` / `:15908` → `4690-4760` | At cycle end `_residual_sleeve_deploy` buys the core back (`residual_bull_deploy`), stamping the cadence clock on the **attempt** (`broker.py:4752`). |

Two things that are **not** the problem, verified:

* **`reserved` in the gate is not the emulator's reservation.** `reserved_total`
  (`broker.py:13488-13556`) is the strategy `capital_pct` reservation. It logged `$0.00` on
  all 105 buy gates in all three runs. The reservation that actually binds is
  `PortfolioEmulator._execution_cash_reservations` and the gate never reads it.
* **The core is exempt from the turnover budget**, both read and write
  (`broker.py:3143-3196`, and `_turnover_ledger_record` is only called
  `if _turnover_is_governed(...)` at `broker.py:4184-4186`). The SPY churn described below
  therefore does *not* consume the 50%/mo budget. The `TURNOVER BUDGET BINDING: 112% of NAV`
  readings in 725146 are satellite-only.

---

## 2. The canonical bar, reconstructed line by line (bt 725146, 2026-02-05 15:00)

```
22929 Momentum portfolio swap: sell AIFD (pnl=-6.2%) → buy SNDK (score=1.382, $828)
22955 V31.2 total-spend cap [CONCENTRATE]: funded 1 of 1 by conviction (SNDK@$828) out of $2,838; dropped 0 to the queue
23037 [core] funding $828 of conviction overflow out of the core (design room $-577, floor-bounded room $902)
23038 [core] released 1.0249 SPY @ 686.10 (core rebalance: funding (40.1% -> 40.0% of NAV),
        ok=SimulationSubmission(order_id='sim-000000000027-SPY', symbol='SPY', side='sell',
        source='residual_bull_refill', accepted=True, filled=False))
23046 SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $902 of room out of the core (floor-bounded)
23047 TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy through a 80% budget
23048 Buy gate inputs for SNDK: cash=$124.64 reserved=$0.00 floor=$120.00 effective_floor=$120.00
        high_conv=True open_pos=4 cash_per_trade=$827.86 available=$4.64 cash_to_use=$4.64 → SKIP
23049 SKIP BUY SNDK — cash_to_use $4.64 < min $50 (allocated $827.86)
23050 Gate skips reported back: SNDK (insufficient_cash)
--- next bar ---
23289 [execution] FILL SELL SPY qty=1.02494573 price=675.788907 quote=2026-02-05 16:00:00+00:00
23314 [core] hold (release) — cadence_hold: core 28.1% vs target 40.0% of NAV
```

The arithmetic is all internally consistent and all correct **except for the clock**:

* Allocator sized SNDK at **$827.86** = 14.0% of the $5,913 NAV. That is an objective-sized clip.
* `need = 827.86 − 124.64 = $703.22`; the core released `1.0249 SPY @ 686.10 = $703.18`
  (`core_sleeve.py:455-457`). The funder was sized to the cent.
* The gate then read `cash=$124.64` — **the same pre-sell number** — and subtracted the
  $120 floor. `available = $4.64`.
* The sell filled at the **16:00** bar for **$692.65**.

Three independent things each had to be true for this to fail, and all three were:

1. **Timing ($703.18).** Next-event execution: the funding sell fills at the start of the
   next tick (`broker.py:12132-12147`).
2. **Floor ($120.00).** `open_pos=4 < cash_reserve_hard_min_positions=5`, so the hard floor
   did **not** release (`broker.py:15083-15087`). Even with the sell settled, the buy would
   have been $697.29, not $827.86.
3. **Min clip ($50).** `cash_to_use=$4.64 < $50` turns a partial buy into a zero
   (`broker.py:15305`).

**What would have had to happen for the cash to be there:** the $703.18 SPY sell submitted at
line 23038 would have had to be spendable at line 23048. With it, `available = (124.64 +
692.65) − 120 = $697.29` and SNDK enters at **11.8% of NAV** instead of 0%.

**And the retry does not save it.** The skip is reported back (`broker.py:15311`), the
strategy re-scores it next bar — `23717 Broker-skipped scoring net: 1 ticker(s) added: SNDK`
— and is then refused by a *different* gate: `23815 V32 mw_buy extension-block: SNDK recent
runup +108.0% > 25% — no conviction bypass`, `23834 Backfill queue BLOCKED (broker-skipped):
SNDK (full_priority_blocked)`. **725146 never bought SNDK.** (Honest note: 725146 was stopped
on 02-17, and SNDK went $592 → $601 over that stub, so the P&L cost *of this one bar in this
one run* is ~$7. The loss is structural, not this bar.)

---

## 3. Count and dollars — the answer to "how many bars, how many dollars"

### 3a. Strict definition: `SKIP BUY … insufficient_cash`

**7 bars, 9 symbol-events, $5,207.13 allocated, $5,018.62 refused** ($3,605.69 if you
de-duplicate the two bars where two names were each offered the *same* pot).

| run | bar | symbol | allocated | cash | cash_to_use | refused | same-tick core release | filled next bar |
|---|---|---|---|---|---|---|---|---|
| 725146 | 02-05 15:00 | SNDK | $827.86 | $124.64 | $4.64 | $823.22 | $703.18 | $692.65 |
| 820236 | 01-05 15:00 | ARWR | $776.46 | $1.69 | $1.69 | $774.77 | $774.76 | $778.65 |
| 820236 | 01-05 15:00 | BKR | $776.46 | $1.69 | $1.69 | $774.77 | (same order) | (same) |
| 820236 | 02-25 15:00 | ABBV | $673.54 | $35.38 | $35.38 | $638.16 | $638.15 | $639.90 |
| 820236 | 02-25 15:00 | ELAN | $673.54 | $35.38 | $35.38 | $638.16 | (same order) | (same) |
| 613166 | 01-27 15:00 | PLRZ | $623.17 | $3.53 | $0.00 | $623.17 | $619.60 | $621.11 |
| 613166 | 01-30 15:00 | WDC | $82.83 | $42.85 | $42.85 | $39.98 | $39.97 | $39.78 |
| 613166 | 02-03 15:00 | SNDK | $31.13 | $17.49 | $17.49 | $13.64 | $13.63 | $13.56 |
| 613166 | 02-09 15:00 | BIIB | $742.14 | $49.39 | $49.39 | $692.75 | $692.72 | $694.69 |

**On 9 of 9 the shortfall equals the same-tick, unfilled core release to within $4.**

### 3b. Broader definition: the gate was cash-bound (`available < cash_per_trade`)

This is the number that matters, because the usual outcome is not a SKIP — it is a **silent
truncation** that still logs `→ PASS`.

| run | events | bars | intended | refused |
|---|---|---|---|---|
| 725146 | 4 | 4 | $3,554.60 | $2,079.41 |
| 820236 | 24 | 17 | $19,144.01 | $8,870.27 |
| 613166 | 13 | 10 | $6,426.16 | $3,851.59 |
| **total** | **41** | **31** | **$29,124.77** | **$14,801.27** |

All 41 were `high_conv=True`. Attribution of the $14,801.27:

| cause | $ | share |
|---|---|---|
| same-tick core funding sell submitted, not yet filled | **$14,084.02** | 95.2% |
| `effective_floor` ($120 fixed, not released because `open_pos < 5`) | $713.29 | 4.8% |
| everything else | $3.96 | 0.03% |

The floor cost lands entirely in 725146 (4 bars × ~$120) and 613166 (3 bars) — the two runs
whose alpha book habitually held 4 names. In 820236 the book held 5, so
`effective_floor=$0.00` on 20 of its 24 cash-bound gates.

### 3c. What actually got bought

Of **133 `SATELLITE OVERFLOW` events** (the conviction band's whole reason to exist),
**116 (87%) produced no fill at all**, for $46,465.82 of sized-but-unbought conviction:

| outcome | events |
|---|---|
| never reached the buy gate (`TURNOVER BYPASS CEILING` / `TURNOVER BUDGET BLOCK` upstream) | 53 |
| reached the gate, then `MAX_POSITIONS_GATE: blocked` | 50 |
| reached the gate, cash-bound, filled $0 | 8 |
| reached the gate at full size, still no fill | 5 |
| **filled (any amount)** | **17** |

---

## 4. The second cash bug: an in-flight core deploy reserves the cash, and the gate cannot see it

This one does not appear in the `insufficient_cash` counter at all, because the gate says
**PASS**.

**bt 613166, 2026-02-05 15:00 (log lines 24041-24100, fills at 24339-24340):**

```
24041 V31.2 total-spend cap [CONCENTRATE]: funded 1 of 1 by conviction (SNDK@$859) out of $3,867
24091 [core] funding request trimmed $859 -> $805 — satellite headroom will refuse the remainder
24097 SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $805 of room out of the core (floor-bounded)
24098 SATELLITE CAP: SNDK trimmed $859 -> $805 to keep the core at target
24100 Buy gate inputs for SNDK: cash=$808.71 reserved=$0.00 floor=$120.00 effective_floor=$0.00
        high_conv=True open_pos=5 cash_per_trade=$805.24 available=$808.71 cash_to_use=$805.24 → PASS
--- next bar ---
      [execution] FILL BUY SPY  qty=0.99010162 price=678.891124 quote=2026-02-05 16:00:00+00:00   ($672.17)
      [execution] FILL BUY SNDK qty=0.14771364 price=592.012625 quote=2026-02-05 16:00:00+00:00   ($87.45)
```

The gate approved **$805.24**. The fill was **$87.45** — 10.9%. Reconstructed exactly:

```
cash                                              $808.71
− in-flight SPY core deploy reservation           −$680.88   ([core] bought $680.88 SPY, submitted 2026-02-05T01:00, still pending)
− T+1 withheld (5% of the 02-04 NVDA sale $777.56) −$38.88   (portfolio_emulator.py:118-121, 422-436)
= get_buying_power()                               $88.95
FILL BUY SNDK $87.45 + fees ≈ $88.7               ✔
```

Same mechanism, three more times, each arithmetic-exact:

| run | bar | symbol | gate `cash_to_use` | fill | why |
|---|---|---|---|---|---|
| 820236 | 01-02 15:00 | BA | $838.87 | **$107.02** | $2,400 SPY opening deploy still pending; `2507.03 − 2400.00 = 107.03` |
| 820236 | 01-20 15:00 | SNDK | $562.10 | **$127.74** | $448.13 SPY deploy pending; `577.08 − 448.13 = 128.95` |
| 613166 | 01-28 15:00 | PLRZ | $504.69 | **$85.78** | sibling AMZN buy submitted first in the same tick reserved $504.69 |
| 613166 | 02-05 15:00 | SNDK | $805.24 | **$87.45** | above |

Total gate-approved size that evaporated below the gate on these four bars: **$2,302.79 →
$408.00 (17.7%)**.

Note the direction of the live/backtest divergence flagged in
`docs/handoffs/2026-08-08-production-readiness-research.md` §4a: on the *live* Alpaca equity
path there is **no** second clamp — `_build_strategy_stock_intent`
(`broker.py:8326-8342`) computes `quantity = cash_to_use / price` with no cash check at all —
so live would have placed the $805 order. These four bars are exactly the divergence that doc
predicted, measured. (The handoff cites `broker.py:8308-8311` for this; the actual quantity
computation in the tree today is `broker.py:8339-8342`.)

**`backtest_credit_sell_proceeds_enabled` cannot fix any of this.** There are **0**
`Sell-proceeds credit` lines in all three runs, and by construction there could not be many:
`_scp_sell_proceeds` is only appended inside the per-symbol execution loop for `decision ==
-1` (`broker.py:15742-15749`), and the **core release never goes through that loop** — it is
submitted at `broker.py:14465`, above it. And even if it were credited, it lifts only
`_sizing_ceiling` (`broker.py:15133-15142`); the emulator clamp at
`portfolio_emulator.py:1420-1423` is untouched.

---

## 5. `[core] funding request trimmed $X -> $Y` — how often is Y near zero, and why

**98 trim lines across the three runs. $172,147 requested → $72,235 allowed; $99,912 (58%)
trimmed away.**

| run | lines | X | Y | Y = 0 | Y < $50 | Y < 10% of X |
|---|---|---|---|---|---|---|
| 725146 | 24 | $43,929 | $18,761 | 7 | 8 | 13 |
| 820236 | 39 | $64,706 | $27,938 | 2 | 4 | 4 |
| 613166 | 35 | $63,512 | $25,536 | 4 | 8 | 11 |
| **total** | **98** | **$172,147** | **$72,235** | **13 (13%)** | **20 (20%)** | **28 (29%)** |

**Why Y is zero: it is correct, and it is a symptom of a different problem.** On all 13
`Y = 0` ticks the satellite headroom was already **negative** and every buy in the batch was
plain (non-conviction), so `_fr_allow_plain = min(plain, _fr_room ≤ 0) = 0` and `_fr_conv = 0`
(`broker.py:14417-14421`). Example (725146, 2026-01-09, lines 7026-7035):

```
7026 [core] funding request trimmed $1,678 -> $0 — satellite headroom will refuse the remainder
7032 SATELLITE CAP: RVLV skipped — satellite at its design share ($-694 room)
7035 SATELLITE CAP: SLVP skipped — satellite at its design share ($-694 room)
```

The two buys the $1,678 was for were refused nine lines later. Not releasing core for them is
the right call — it is precisely the `$2,600-of-notional-for-zero-allocation` churn loop the
2026-08-03 sweep removed. Measured headroom on the 13 zero ticks: `-$1,404, -$1,219, -$721,
-$694, -$620, -$612, -$606, -$60, -$50, -$30, -$20, -$13`, plus one with no `SATELLITE CAP`
line.

**Y = 0 is therefore not the bug. `Y > 0` is where the bug is:** 725146 has 7 of its 24 trims
at zero because `satellite_conviction_reserve_pct=0.15` narrowed the design share from 0.63 to
0.48 (derived from the run: tick 2 logs `design room $2,880` = 0.48 × $6,000 in 725146 versus
`$3,780` = 0.63 × $6,000 in 613166; `floor-bounded room $4,380` = 0.73 × $6,000 ⇒
`core_min_pct = 0.25`, `cash_reserve_floor_pct = 0.02`). The reserve moved plain buys out of
the way but did **not** widen the conviction band, so the conviction name still had to be
funded out of a release that arrives a bar late.

**A near-zero `Y` with a conviction name in it is the worst case** (613166, 2026-02-03,
lines 22288-22302):

```
22288 [core] funding pre-pass: max_positions will refuse 2 of 3 sized buy(s) (PYPL, VOYA)
22289 [core] funding request trimmed $2,608 -> $31 — satellite headroom will refuse the remainder
22290 [core] released 0.0196 SPY @ 695.39 (core rebalance: funding (27.2% -> 25.5% of NAV) …)
22299 SATELLITE OVERFLOW: SNDK raw=+2.107 >= 1.50 — funding $31 of room out of the core (floor-bounded)
22300 SATELLITE CAP: SNDK trimmed $869 -> $31 to keep the core at target
22301 Buy gate inputs for SNDK: cash=$17.49 … cash_per_trade=$31.13 available=$17.49 → SKIP
```

The core was already at **27.2% against a 25.5% target and a 25% floor**, so the whole
"floor-bounded room" the conviction band could offer the system's highest-scoring name
(raw=+2.107) was **$31**. 14 of the 102 `SATELLITE CAP: … trimmed` lines cut a name to under
$100; 28 of 102 cut it to under a quarter of its sized value.

---

## 6. The churn the funding path still produces

`core_funding_max_positions_aware` deliberately exempts conviction names from the
max_positions pre-pass ("A conviction name is NEVER starved", `broker.py:14340-14345`,
`14365-14375`). The consequence is measurable: the core sells to fund a conviction buy that
`MAX_POSITIONS_GATE` then refuses **eleven lines later**.

bt 820236, 2026-01-07 15:00 (lines 5860-5882):

```
5859 max_positions gate armed: held=6, cap=6
5860 [core] funding request trimmed $1,728 -> $760
5861 [core] released 0.9159 SPY @ 691.81 (core rebalance: funding …, accepted=True, filled=False)   ($633.63)
5871 SATELLITE OVERFLOW: AIR raw=+1.737 >= 1.50 — funding $760 of room out of the core
5874 Buy gate inputs for AIR:  … open_pos=5 … cash_to_use=$126.35 → PASS
5875 MAX_POSITIONS_GATE: blocked AIR (held=6, cap=6)
5881 Buy gate inputs for GBDC: … open_pos=5 … cash_to_use=$126.35 → PASS
5882 MAX_POSITIONS_GATE: blocked GBDC (held=6, cap=6)
```

(Note also the counter desync in one screen: the pre-pass and the gate read `open_pos=5`,
`MAX_POSITIONS_GATE` reads `held=6`.)

**19 ticks across 820236 + 613166 released $6,176.19 of SPY to fund buys that
`MAX_POSITIONS_GATE` blocked on the same tick** ($4,470.85 in 820236 across 15 ticks,
$1,705.34 in 613166 across 4). 26 of the 41 cash-bound gate events were also MPG-blocked.

The saw-tooth is visible end to end:

| run | funding releases | $ released | post-opening confirmed deploys | $ re-deployed | ratio |
|---|---|---|---|---|---|
| 725146 | 11 | $3,989.92 | 4 | $3,192.77 | 0.80 |
| 820236 | 17 | $5,883.77 | 8 | $5,313.99 | 0.90 |
| 613166 | 13 | $6,040.86 | 8 | $5,653.90 | 0.94 |

613166 reads: `REL 779.06 → DEP 769.27 → REL 773.51 → DEP 687.41 → REL 660.21 → DEP 839.35 →
REL 683.01 → DEP 896.21 → REL 692.72 → DEP 616.88 → REL 591.89 → DEP 591.36 → REL 570.04 →
DEP 572.54`.

**Share of all executed notional that was SPY:** 725146 **47.3%** ($9,556.65 of $20,209.40),
820236 **68.1%** ($13,584.56 of $19,946.47), 613166 **66.2%** ($13,802.17 of $20,859.04).
At the run's own cost model (`simulated_execution.py:117-120`, `equity-measured-v3-nbbo23`,
spread 45.6bp ⇒ 22.8bp half-spread + 0.1bp slippage + 0.3bp fee ≈ 23.2bp one-way) that SPY
gross costs roughly **$22 / $32 / $32** per run — small in dollars. **The cost is not the
spread; it is that two thirds of the book's trading activity moved no exposure.**

---

## 7. What it cost — the money line

Both finished runs held SNDK, which moved **+166.10%** ($237.33 → $631.54) inside the window.

| run | SNDK P&L | SNDK end position | total P&L |
|---|---|---|---|
| 820236 | **$100.95 (+20.57%)** | $595.91 = 8.8% of NAV | $739.61 (+12.33%) |
| 613166 | **$3.04 (+2.39%)** | $130.88 = 2.0% of NAV | $549.91 (+9.17%) |
| 725146 | never bought | — | negative (stopped) |

613166 sized SNDK at $872 / $869 / $859 (13–14% of NAV) on three separate bars and captured
**$3.04** of a +166% move. On each of those bars the money was in an unfilled order:
02-02 trimmed to $168 then MPG-blocked; 02-03 trimmed to $31 then `insufficient_cash`; 02-05
approved at $805.24 and filled at $87.45 against a live $680.88 SPY-deploy reservation.

**Capital-substitution estimate** (dollars that stayed in SPY instead of the named symbol ×
[symbol return to run end − SPY return to run end], using the run's own fill prices and its
own closing marks) for the three priceable reservation bars:

| run | bar | symbol | $ stuck in SPY | symbol move | SPY move | estimate |
|---|---|---|---|---|---|---|
| 820236 | 01-20 | SNDK | $434.36 | 443.83 → 635.94 (+43.3%) | 685.38 → 686.16 (+0.11%) | **+$187.51** |
| 613166 | 02-05 | SNDK | $717.79 | 592.01 → 635.94 (+7.4%) | 678.89 → 686.16 (+1.07%) | **+$45.57** |
| 820236 | 01-02 | BA | $731.85 | 222.70 → 227.51 (+2.2%) | 682.97 → 686.16 (+0.47%) | **+$12.38** |
| | | | | | | **+$245.46 ≈ 4.09pp on $6,000** |

This is an estimate, not a measurement — it assumes buy-and-hold to run end and that the
capital would have come out of SPY. It is stated so the ranking below is not adjective-driven.
The *measured* facts are the 41 refusals, the $14,801.27, and the four exact reservation
reconstructions.

---

## 8. Ranked list — what to change, expected effect, evidence

Every one of these is default-OFF-able and each should be validated as a paired A/B per
`docs/OBJECTIVE.txt:88-96`. I am not proposing anything I cannot point a measurement at.

### 1. Credit the same-tick core funding release into **`PortfolioEmulator.get_buying_power`**, not just the broker gate
**Change:** when `_residual_sleeve_release` submits a `residual_bull_refill` sell, book its
expected proceeds as a *negative* reservation (or an explicit `_pending_funding_credit`) that
`get_buying_power()` adds back, and have the buy gate read the same number. Crediting only
`_sizing_ceiling` (the existing `buy_ceiling` path, `broker.py:15133-15142`) is provably
insufficient — the emulator re-clamps at `portfolio_emulator.py:1420-1423`.
**Expected effect:** removes **$14,084.02 of the $14,801.27** refused (95.2%), and converts
the 7 `insufficient_cash` bars into funded buys. Directly recovers the canonical SNDK bar
($4.64 → ~$697).
**Evidence:** table §3b attribution; 9/9 SKIP bars match the same-tick release to within $4
(§3a); `broker.py:12132-12147` (pending fills applied next tick); `broker.py:15095`
(gate reads `get_cash()`); 0 `Sell-proceeds credit` lines in all three runs.
**Caveat that must be measured, not assumed:** this makes the backtest spend money the live
Alpaca path already spends (`broker.py:8339-8342`, no second clamp live), so it *reduces*
live/backtest divergence — but it also front-runs settlement by one bar. Cap it at the
`SETTLED_SELL_PROCEEDS_FRACTION = 0.95` the emulator already uses.

### 2. Make the buy gate's `reserved` the emulator's real reservation
**Change:** `broker.py:15143` currently subtracts `reserved_total`, which is the strategy
`capital_pct` figure (`broker.py:13488`) and logged `$0.00` on all 105 gates. Subtract
`Σ _execution_cash_reservations + _withheld_cash()` instead (i.e. size against
`get_buying_power()`).
**Expected effect:** the gate stops printing `→ PASS` for orders the emulator will cut by
80–90%. On its own this makes the book *honest*, not richer — combined with (1) and (3) it is
what makes the sizing real. Four measured bars: **$2,302.79 approved → $408.00 filled**.
**Evidence:** §4, four arithmetic-exact reconstructions
(`2507.03 − 2400.00 = 107.03` vs fill $107.02; `577.08 − 448.13 = 128.95` vs fill $127.74;
`808.71 − 680.88 − 38.88 = 88.95` vs fill $87.45; `624.69 − 504.69 − ~34 ≈ 86` vs fill
$85.78); `portfolio_emulator.py:1497`, `:1414-1423`, `:402-420`.

### 3. Do not let the core **deploy** run on a tick that also sized a conviction buy
**Change:** `_residual_sleeve_deploy` (`broker.py:15847` / `:15908`) parks idle cash at cycle
end into an order that is still pending on the *next* bar, where it silently reserves the cash
the next conviction buy needs. Skip the deploy for one cadence window after any
`residual_bull_refill`, or net the pending deploy against a new funding request instead of
letting both orders live.
**Expected effect:** removes cause (2)'s root rather than just its symptom; also removes the
$6,176.19 of SPY released-then-blocked round trips (§6) and drops SPY from ~67% to well under
half of executed notional.
**Evidence:** §6 release/deploy saw-tooth tables; the exact pending-deploy notionals
($2,400.00 / $448.13 / $680.88) matched to the three truncated fills in §4; deploy/release
ratio 0.80 / 0.90 / 0.94.

### 4. Stop funding conviction buys the `max_positions` gate is about to refuse
**Change:** `broker.py:14365-14375` force-adds every conviction name back into
`_fr_admissible`. Keep the "never starve conviction" intent, but make it *displace* — fund it
only when the pre-pass can also name the position it will replace (`planned_full_exit_symbols`
already exists at `broker.py:14349`). Otherwise the release is pure churn.
**Expected effect:** removes **19 ticks / $6,176.19** of SPY sold-then-bought-back for buys
blocked on the same tick, in 820236 and 613166.
**Evidence:** §6, `MAX_POSITIONS_GATE: blocked` counts (45 blocks / 31 ticks in 820236,
12 / 10 in 613166), with the release notional on the same tick; the 2026-01-07 AIR/GBDC
transcript. Note the historical warning at `broker.py:14319-14331`: the *first* version of
this pre-pass froze the book. The displacement rule is the version that does not.

### 5. Make the $120 cash floor release on conviction regardless of position count
**Change:** `broker.py:15083-15087` — `_can_bypass_floor` requires `open_pos >= 5`. It is a
flat $120 (`_initial_value × 0.02`, `broker.py:15059`, frozen at the opening NAV for the whole
run).
**Expected effect:** recovers **$713.29** across 7 bars in 725146 and 613166 — small, but it
is the difference between `available=$4.64` and `available=$124.64` on the canonical bar,
i.e. between a SKIP and a partial fill.
**Evidence:** §3b attribution row; the 7 bars are 725146 01-27 / 01-28 / 01-29 / 02-05 and
613166 01-27 / 01-28 ×2.

### 6. Widen the conviction band instead of narrowing the design share
**Change:** `satellite_conviction_reserve_pct = 0.15` (725146) shrinks
`satellite_design_share` from 0.63 to 0.48 (`core_sleeve.py:276-278`) but leaves
`satellite_max_share = 1 − core_min_pct − cash_floor = 0.73` untouched
(`core_sleeve.py:305-309`). The band widened by 15pp — but the *funding* still has to come
through the same one-bar-late release, so 725146's conviction names were no better off, and
7 of its 24 trims went to $0.
**Expected effect:** unknown until (1) lands — **do not re-run the 0.15 reserve until the
funding-timing fix is in.** 725146 is the run that went negative with it on.
**Evidence:** derived from the runs, not the config: 725146 tick-2 `design room $2,880`
(= 0.48 × $6,000) vs 613166 `design room $3,780` (= 0.63 × $6,000), both with
`floor-bounded room $4,380` (= 0.73 × $6,000). 725146: 13 of 24 trims cut to under 10% of the
request, versus 4 of 39 in 820236.

### 7. Do **not** rely on `backtest_credit_sell_proceeds_enabled` for this
**Evidence:** 0 `Sell-proceeds credit` lines in 725146 / 820236 / 613166;
`_scp_sell_proceeds` is only written inside the per-symbol execution loop for `decision == -1`
(`broker.py:15742-15749`), and the core release is submitted above that loop at
`broker.py:14465`; and the credit only raises `_sizing_ceiling` (`broker.py:15142`), which the
emulator clamp at `portfolio_emulator.py:1420-1423` overrides anyway.

---

## 9. Things I checked and could not prove

* **`_core_tick_ok` / tick-mode suppression is not implicated.** 0 occurrences of
  `[core] funding release suppressed` in any of the three runs.
* **The $5 minimum release floor is not implicated.** 0 occurrences of
  `[sleeve] release SKIPPED` and 0 of `deploy_below_min`.
* **The broker 15% single-position cap is not implicated.** 0 occurrences of
  `Broker single-position cap` — no gate was cap-trimmed in any run (`cap_trim` = 0 of 105).
* **T+1 settlement is a minor term, not the cause.** Only 5% is withheld
  (`portfolio_emulator.py:118-121`); it contributed $38.88 of the $717.79 shortfall on the one
  bar where I could reconstruct it exactly, and ~$34 on another.
* **The SPY churn does not consume the turnover budget.** `_turnover_is_governed`
  (`broker.py:3143-3196`) exempts the core symbol at both the read and the write
  (`broker.py:4184-4186`). The `TURNOVER BUDGET BINDING: 112%` readings are satellite-only. I
  cannot claim the core churn caused the turnover blocks.
* **The live-vs-backtest divergence direction is code-derived, not run-derived.** Live has no
  `execute_signal` clamp on the Alpaca equity path (`broker.py:8339-8342`), so live would
  place the $805 order the backtest cut to $87. I have no live log to confirm that; treat it
  as a code reading, exactly as `docs/handoffs/2026-08-08-production-readiness-research.md`
  §4a stated it.
* **P&L counterfactuals are estimates.** §7's $245.46 assumes buy-and-hold to run end and SPY
  as the funding source. The 41 refusals and $14,801.27 are counts; the $245.46 is not.

---

### Reproduce

```bash
python3 scripts/pull_backtest_logs.py 725146 --filter 'Buy gate inputs|SKIP BUY|\[core\]|SATELLITE|MAX_POSITIONS_GATE|FILL ' --stdout
python3 scripts/pull_backtest_logs.py 820236 --filter 'Buy gate inputs|SKIP BUY|\[core\]|SATELLITE|MAX_POSITIONS_GATE|FILL ' --stdout
python3 scripts/pull_backtest_logs.py 613166 --filter 'Buy gate inputs|SKIP BUY|\[core\]|SATELLITE|MAX_POSITIONS_GATE|FILL ' --stdout
```
