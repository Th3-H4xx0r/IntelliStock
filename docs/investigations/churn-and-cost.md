# SPY core saw-tooth — dollar cost, mechanism, and turnover budget

Read-only investigation, 2026-08-08. No code changed, no backtest started, nothing pushed.
Every number below is reconstructed from run logs pulled with
`python3 scripts/pull_backtest_logs.py <id>`, or is a line quote from the working tree.

Runs used: **820236** (+12.33%), **613166** (+9.17%), **718249** (+4.23%),
**725146** (STOPPED, +0.11% at stop), plus **342380** (bear window) for the SQQQ cross-check.
All are `v2-let-run-core`, 2026-01-01..2026-03-01, $6,000, 3600s, `pit_mode=research`.

> **Line numbers.** `backend/broker.py` and `backend/portfolio_emulator.py` were being edited
> by another session while this was written. Line numbers below are stamped to these exact
> file contents; every reference is also given as a verbatim snippet so it survives a shift.
>
> | file | sha256[:12] | lines |
> |---|---|---|
> | `backend/broker.py` | `a9fff5854de0` | 16407 |
> | `backend/core_sleeve.py` | `42283e315e1f` | 565 |
> | `backend/portfolio_emulator.py` | `0744685ac402` | 1736 |
> | `backend/simulated_execution.py` | `16f9e478a0f7` | 642 |
> | `backend/backtest_risk_metrics.py` | `6f373be0e40d` | 201 |

---

## 0. TL;DR

| question | answer |
|---|---|
| What does the post-initial SPY churn cost **as the backtest charges it**? | **$26.46 in bt 613166 = 44.1 bps of the $6,000 book = 4.81% of the +9.17%** (820236: $25.95 / 43.3 bps / 3.5%; 718249: $20.71 / 34.5 bps / 8.2%) |
| Annualised | ~265 bps/yr of drag from this one lane |
| Is the asymmetry (release cadence-EXEMPT, deploy cadence-GATED) the driver? | **CONFIRMED.** 100% of releases are `reason="funding"` (cadence-exempt), 100% of deploys are `reason="band_deploy"` (cadence-gated), in all four runs |
| Total book turnover vs the 50%/mo budget | **153–203%/month, i.e. 3.1x–4.1x over.** The exempt core lane alone is 93–111%/mo |
| Does the budget see the core? | **No.** Measured, not inferred — §3.3. The comment saying it does is false |
| **Biggest caveat** | **~87% of that 44.1 bps is a modelling artifact.** The run charges SPY 22.8 bps of half-spread; the repo's own note (`portfolio_emulator.py:70`) says *"5 bps of quoted spread describes SPY, not this book"*. At 5 bps the same churn costs **5.5 bps**, not 44.1 |

---

## 1. DOLLAR COST

### 1.1 The cost assumption, and proof it is the one the run used

`backend/simulated_execution.py:92-121`:

```
#: 2026-08-02: priced 61 of alpaca-main's 62 real live fills against the SIP
#: NBBO at each fill timestamp. Results:
#:   quoted spread   median 17.5 bps, mean 41.8, NOTIONAL-WEIGHTED 45.6, p90 109.0   <- :96
...
#: One-way = 45.6/2 + 0.1 + 0.3 = 23.2 bps.                                          <- :107
LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL = ExecutionCostModel(                           #  :116
    version="equity-measured-v3-nbbo23",
    spread_bps=45.6, slippage_bps=0.1, fee_bps=0.3, latency=timedelta(0),
)
```

and `simulated_execution.py:139-142` — the "largest unexploited cost lever" note:

```
#: This is the largest unexploited cost lever on the book. Every fill
#: currently crosses the spread BY CONSTRUCTION -- a market buy lifts the
#: ask, a market sell hits the bid -- so the measured 22.8 bps of half-spread
#: is paid on every side of every trade.
```

**Verified against the run, not the config.** All 37 fill lines in 613166 carry
`model=equity-measured-v3-nbbo23`:

```
[BROKER] [execution] FILL BUY SPY qty=3.51184282 cumulative=3.51184282 price=682.970444
         fees=0.071955 quote=2026-01-02 16:00:00+00:00 model=equity-measured-v3-nbbo23
```

The half-spread is directly measurable, because two runs traded SPY in **opposite directions
on the same quote**. 2026-01-06 16:00:00Z:

```
bt 613166: FILL BUY  SPY qty=1.07930732 price=690.678055 ... quote=2026-01-06 16:00:00+00:00
bt 718249: FILL SELL SPY qty=0.89808199 price=687.521977 ... quote=2026-01-06 16:00:00+00:00
```

Both back out to the **same mid, 689.100000**, under
`fill = mid*(1 +/- 45.6/20000)*(1 +/- 0.1/10000)` — `portfolio_emulator.py:497-521`
(`_equity_fill`), specifically `:507`:

```python
half_spread = mid * model.spread_bps / 20_000.0
```

Round trip `(690.678055 - 687.521977)/689.10 = 45.80 bps` = 45.6 spread + 2 x 0.1 slippage,
exactly. Passive execution is OFF (no `passive_execution*` key in the run's `strategy_schema`;
`limit_price=None` default at `simulated_execution.py:145`).

**One model is applied to every symbol.** `portfolio_emulator.py:244-252` sets a single
`self._equity_cost_model` per emulator and `:507` uses `model.spread_bps` for SPY, NVDA and
PLRZ alike. No per-symbol spread exists anywhere in `backend/` (grep: no `symbol_spread` /
`spread_by_symbol` / `spread_override`).

### 1.2 The arithmetic

Reconstructed fill-by-fill from the log (each fill's mid backed out, then
`spread = mid*45.6/20000*qty`, `slippage = touch*0.1/10000*qty`, `fee = notional*0.3/10000`).
The reconstruction reproduces the logged `fees=` field to $5e-7, so it is the emulator's own
arithmetic, not an approximation.

**bt 613166, SPY post-initial (21 fills; excludes the $2,398.48 opening deploy):**

```
gross notional     $11,403.69      (matches sleeve_churn.post_initial_gross_notional = 11403.67)
  spread cost      $    26.0042
  slippage cost    $     0.1140
  fees             $     0.3421
  TOTAL            $    26.4603    = 23.203 bps one-way, i.e. 11,403.69 x 0.00232
```

**In bps of the run:**

| | 820236 | 613166 | 718249 | 725146 |
|---|---|---|---|---|
| return | +12.33% | +9.17% | +4.23% | +0.11% (stopped) |
| $ P&L | $739.61 | $549.91 | $253.84 | $6.73 |
| SPY post-initial gross | $11,186 | $11,404 | $8,924 | $7,158 |
| **cost of that churn** | **$25.95** | **$26.46** | **$20.71** | **$16.61** |
| **bps of the $6,000 book** | **43.3** | **44.1** | **34.5** | **27.7** |
| **% of the run's gain** | **3.5%** | **4.81%** | **8.2%** | 247% |
| whole-book execution cost | $46.25 | $48.37 | $49.12 | $46.86 |
| whole-book bps of NAV | 77.1 | 80.6 | 81.9 | 78.1 |

Annualised (42 weekday sessions ~ 2 months, x6): the core saw-tooth is **~265 bps/yr**, the
whole book **~484 bps/yr**.

Materiality check: in 613166 the SPY sleeve's **entire** contribution was
`pnl_per_stock["SPY"] = +$28.04` while it paid $32.01 of execution cost across all 22 fills.
820236: +$8.76 P&L against $31.51 of cost. 718249: **-$23.61** against $26.26. In two of the
three finished runs the lane's whole gross P&L is smaller than its own trading cost.

### 1.3 The caveat that changes the ranking — 87% of that number is model, not money

The 45.6 bps is the **notional-weighted** spread of alpaca-main's 61 real fills
(`simulated_execution.py:96`), i.e. dominated by the microcaps. Applying it to SPY is
contradicted by the repo's own text, `backend/portfolio_emulator.py:70-71`:

```
# nominal model is itself optimistic: 5 bps of quoted spread describes SPY,
# not this book.
```

Re-pricing 613166 with a per-symbol model — SPY at the repo's own 5 bps, satellite at the
measured **p90 109 bps** (`simulated_execution.py:96`) — changing nothing else:

| lane | notional | charged now | at the repo's own per-symbol figures | delta |
|---|---|---|---|---|
| SPY (all 22 fills) | $13,802 | $32.01 @ 23.2 bps one-way | **$4.00** @ 2.9 bps one-way | **-$28.01** |
| satellite (15 fills) | $7,057 | $16.35 @ 23.2 bps | **$38.74** @ 54.9 bps | **+$22.39** |
| total | $20,859 | $48.37 | $42.74 | -$5.63 |

Same pattern in the other three (820236: $31.51 -> $3.94 / $14.74 -> $34.93;
718249: $26.26 -> $3.28 / $22.85 -> $54.13; 725146: $22.16 -> $2.77 / $24.69 -> $58.48).

So: **the aggregate cost is roughly right; the attribution is inverted.** The run charges
**66%** of its execution cost to SPY — the most liquid instrument it trades — and 34% to a book
of $2.4M-$7.9M/day names whose measured p90 spread is 2.4x what they are charged.

The honest cost of the SPY saw-tooth is therefore:

* **as the backtest charges it: $26.46 = 44.1 bps of NAV = 4.81% of the gain**
* **as it would price live at SPY's own spread: ~$3.31 = 5.5 bps of NAV = 0.6% of the gain**

Everything below about the mechanism still stands. What changes is that the saw-tooth is
**not** primarily a cost problem — it is an accounting, exposure and order-count problem.

---

## 2. MECHANISM — release vs deploy asymmetry

### 2.1 The two branches

`backend/core_sleeve.py:397-547`, `core_rebalance_order`, evaluated in this order:

| # | branch | line | cadence? | band? |
|---|---|---|---|---|
| 1 | `funding` — `need = funding_request - cash`; `sell = min(need, core_value)` | `:455-459` | **bypassed** | **bypassed** |
| 2 | `bear_derisk` | `:476-499` | bypassed (sell only) | — |
| 3 | `within_band` hold | `:508-509` | — | `abs(drift) <= rebalance_band_pct` |
| 4 | `cadence_hold` | `:513-515` | **enforced** | evaluated after the band |
| 5 | `band_deploy` (buy) / `band_release` (sell) | `:542` / `:547` | gated by 4 | gated by 3 |

```python
# core_sleeve.py:455-459
need = max(0.0, float(funding_request or 0.0) - max(0.0, float(cash or 0.0)))
if need > 0.0 and core_value > 0.0:
    sell = min(need, core_value)
    if sell >= MIN_CORE_ORDER_USD:
        return RebalanceOrder(notional=-sell, reason="funding", **base)
```

The release side (`broker.py:4475` `_core_sleeve_decide` -> `_submit_release`) stamps the
cadence clock **except for `funding`** — `broker.py:4517-4518`:

```python
if _corder.reason != "funding":
    _RESIDUAL_SLEEVE_STATE[_CORE_SLEEVE_LAST_REBALANCE_KEY] = current_time
```

The deploy side (`broker.py:4730` `_core_sleeve_decide` -> `_submit_deploy`) stamps
**unconditionally** — `broker.py:4765`.

Effective config for this window, read off the run's own `strategy_schema`: base
`core_rebalance_band_pct=0.1`, `core_rebalance_min_days=20`, but the `bull`/`chop`/`recovery`
regime profiles — the only places `core_sleeve_enabled: true` appears — override to
**band 5pp / cadence 5 days**. `core_min_pct=0.25`, `core_max_pct=0.4`, `core_target_pct=0.35`.
(Reading only the base config here would have given the wrong band and the wrong cadence.)

### 2.2 Counted per run — the asymmetry is total, not partial

Parsed from `SimulationSubmission(... source='...')` echoed on every `[core]` line:

| run | SPY fills | `residual_bull_refill` (RELEASE) | `residual_bull_deploy` (DEPLOY) |
|---|---|---|---|
| 820236 | 26 | **17** | 9 |
| 613166 | 22 | **13** | 9 |
| 718249 | 22 | **14** | 8 |
| 725146 | 16 | **11** | 5 |

Reasons, parsed from the same lines:

| run | release reasons | deploy reasons |
|---|---|---|
| 820236 | `funding` x17 | `band_deploy` x9 |
| 613166 | `funding` x13 | `band_deploy` x9 |
| 718249 | `funding` x14 | `band_deploy` x9 |
| 725146 | `funding` x11 | `band_deploy` x6 |

**Zero `band_release`. Zero `bear_derisk`. Zero non-`band_deploy` deploys.** Every sell in the
core lane took the cadence-exempt branch; every buy took the cadence-gated one. The asymmetry
in the hypothesis is **confirmed at 100% of events across four runs.**

### 2.3 The saw-tooth, as the log prints it (bt 613166, decision dates)

```
2026-01-01 DEPLOY  band_deploy   0.0% -> 40.0%   $2400.00
2026-01-05 RELEASE funding      40.0% -> 40.0%
2026-01-06 DEPLOY  band_deploy  27.2% -> 40.0%   $769.27
2026-01-07 RELEASE funding      39.9% -> 40.0%
2026-01-12 DEPLOY  band_deploy  26.9% -> 38.3%   $687.41
2026-01-13 RELEASE funding      37.8% -> 37.9%
2026-01-19 DEPLOY  band_deploy  26.5% -> 40.0%   $839.35
2026-01-21 RELEASE funding      39.0% -> 40.0%
2026-01-22 RELEASE funding      37.9% -> 40.0%
2026-01-26 DEPLOY  band_deploy  26.2% -> 40.0%   $896.21
2026-01-27 RELEASE funding      36.6% -> 34.7%
2026-01-30 RELEASE funding      27.6% -> 26.3%
2026-02-02 RELEASE funding      27.3% -> 27.7%
2026-02-03 RELEASE funding      27.2% -> 25.5%
2026-02-05 DEPLOY  band_deploy  26.7% -> 37.8%   $680.88
2026-02-09 RELEASE funding      38.3% -> 37.1%
2026-02-10 DEPLOY  band_deploy  26.9% -> 37.0%   $616.88
2026-02-11 RELEASE funding      36.5% -> 36.6%
2026-02-16 DEPLOY  band_deploy  26.8% -> 36.5%   $591.36
2026-02-18 RELEASE funding      36.2% -> 36.3%
2026-02-23 DEPLOY  band_deploy  26.6% -> 35.6%   $572.54
2026-02-25 RELEASE funding      35.2% -> 34.7%
```

Verbatim, the first full tooth:

```
[core] released 1.1401 SPY @ 683.33 (core rebalance: funding (40.0% -> 40.0% of NAV),
       ok=SimulationSubmission(... source='residual_bull_refill', accepted=True ...))
[core] bought $769.27 SPY @ 687.73 (band_deploy: 27.2% -> 40.0% of NAV,
       ok=SimulationSubmission(... source='residual_bull_deploy', accepted=True ...))
```

Two facts follow from that pair, and they are the whole mechanism:

1. **The release fires while the core is already on target.** Mean core weight at release:
   35.3% against a mean target of 35.1% (613166); 32.7% vs 37.7% (820236). `funding` is
   branch #1, evaluated **before** the band at `core_sleeve.py:508`, and is sized
   `min(need, core_value)` (`:457`) with no reference to the band or the target.
2. **The release creates the band breach the deploy then closes.** Mean gap the deploy had to
   close: **14.6pp (613166), 13.5pp (820236), 13.6pp (718249), 17.1pp (725146)** — against a
   5.0pp band. The release blows through the band by 2.7x–3.4x every single time.

Timing, from the same table:

| run | median DEPLOY -> next RELEASE | median RELEASE -> next DEPLOY |
|---|---|---|
| 820236 | **1 day** | 5 days |
| 613166 | **2 days** | 5 days |
| 718249 | **1 day** | 3 days |
| 725146 | **2 days** | 4 days |

The exempt leg reacts in 1–2 days; the gated leg takes 3–5. That is the saw-tooth's period, and
why the core lives underweight: **time-weighted mean core weight 29.0% (613166) / 28.5%
(820236) against a target averaging 35–38%**; median 27.0% in both (from the result row's
512-point `risk_metrics.equity_curve`, `positions_snapshot["SPY"] x prices["SPY"] / value`).

### 2.4 What the churn actually accomplished

| run | core released | core re-deployed | **re-deployed / released** | net core change | post-open satellite buys |
|---|---|---|---|---|---|
| 820236 | $5,888 | $5,298 | **90%** | -$589 | $1,320 |
| 613166 | $6,050 | $5,353 | **88%** | -$697 | $2,851 |
| 718249 | $4,822 | $4,102 | **85%** | -$720 | $4,272 |
| 725146 | $3,982 | $3,176 | **80%** | -$806 | $5,630 |

**$11,404 of gross to move $697 of allocation** (613166). `churn_ratio` 8.11 is the same fact:
`13,802 / 1,702`.

Sharper — for each funding release, how much satellite buying happened **before the core bought
the money back**:

* **bt 613166: 9 of 13 releases, $4,600 of $6,051 (76%), funded ZERO satellite buying.**
* **bt 820236: 12 of 17 releases, $4,478 of $5,886 (76%), funded ZERO satellite buying.**

The worst stretch in 613166 — 2026-02-09 to 2026-02-25, five core trades, $3,592 of one-way
notional, **$39.48 of satellite buying** (one SNDK clip):

```
2026-02-09 [core] released 1.0029 SPY @ 690.72 (funding (38.3% -> 37.1% of NAV))
2026-02-10 [core] bought  $616.88 SPY @ 694.00 (band_deploy: 26.9% -> 37.0% of NAV)
2026-02-11 [core] released 0.8552 SPY @ 692.11 (funding (36.5% -> 36.6% of NAV))
2026-02-16 [core] bought  $591.36 SPY @ 681.61 (band_deploy: 26.8% -> 36.5% of NAV)
2026-02-18 [core] released 0.8349 SPY @ 682.76 (funding (36.2% -> 36.3% of NAV))
```

The two existing suppressors are firing and are not enough. In 613166 the headroom trim ran 35
times, cutting $63,512 of requested funding to $25,536 (kept 40%); the max_positions pre-pass
refused funding 7 times:

```
[core] funding request trimmed $3,347 -> $1,898 -- satellite headroom will refuse the
       remainder; releasing core for it would only be bought back
[core] funding pre-pass: max_positions will refuse 1 of 4 sized buy(s) (LLY) -- not
       releasing core to fund them
```

The trims also show the request is the *same* stale candidate set re-priced every bar —
2026-02-09 through 02-20 capped at $742, $719, $716, $708, $700, $707, $687 — so the release
re-fires on a request that already failed.

**Verdict: CONFIRMED, with one refinement.** The cadence gate on the deploy does not *create*
the churn — it halves the buy leg (8–9 deploys vs 11–17 releases). It creates the **saw-tooth
shape and the persistent 7–11pp underweight**. The churn itself is created by an **unbounded**
`funding` release that ignores the band it is about to break.

---

## 3. BUDGET

### 3.1 Total book turnover vs the 50%/mo budget

`turnover_budget_monthly_pct = 0.5` in the run's `strategy_schema`. Measured from the fills
(one-way notional / mean NAV / months, months = weekday sessions / 21):

| run | sessions | mean NAV | total notional | **total turnover /mo** | governed (satellite) /mo | **core (exempt) /mo** | core post-initial /mo |
|---|---|---|---|---|---|---|---|
| 820236 | 42 | $6,512 | $19,946 | **153%** | 49% | **104%** | 86% |
| 613166 | 42 | $6,220 | $20,859 | **168%** | 57% | **111%** | 92% |
| 718249 | 42 | $6,105 | $21,182 | **173%** | 81% | **93%** | 73% |
| 725146 | 34 | $6,136 | $20,209 | **203%** | 107% | **96%** | 72% |

**The book runs 3.1x–4.1x the 50%/mo budget and 4.4x–8.8x the 23–35%/mo the objective says
this specific edge breaks even at** (`docs/OBJECTIVE.txt`, CONSTRAINTS). The exempt core lane
**alone** is 1.9x–2.2x the entire budget; its *post-initial* churn alone — pure saw-tooth — is
**1.4x–1.8x the entire budget**.

Also measured: **10.5 / 12.5 / 10.5 / 9.3 post-initial core rebalances per 21 sessions** across
the four runs. `broker.py:3773-3775` claims the band and cadence *"hard-caps it at 4.2
rebalances a month"*. Measured overrun **2.2x–3.0x**, because `funding` bypasses both.

### 3.2 BINDING / BLOCK / BYPASS counts

| run | BINDING lines | BLOCK (symbol-events) | BYPASS (conviction admitted) | BYPASS-CEILING (refused above the 80% ceiling) |
|---|---|---|---|---|
| 820236 | 263 | 9 | 19 | 0 |
| 613166 | 416 | **49** | 9 | **44** |
| 718249 | 605 | 33 | 18 | 28 |
| 725146 | 490 | 39 | 6 | 37 |

613166's 49 blocks hit 40 distinct names (AIQ x3, LLY x3, PLRZ x3, AIFD x2, RVLV x2, SLGN x2,
plus 34 singletons including META, TSM, AMD, PLD, CAT).

```
TURNOVER BUDGET BLOCK: SLGN skipped -- 56% of NAV traded in 21 sessions
TURNOVER BUDGET BYPASS: AGMI raw=+1.637 >= 1.50 -- admitting a conviction buy through a
    56% budget; the brake is for churn, not for the trade that matters
TURNOVER BYPASS CEILING: BKR refused despite raw=+1.770 -- 85% of NAV traded is at/over
    the 80% ceiling; conviction raises the brake, it does not remove it
```

**The budget is already exhausted on tick 1, before the run trades anything.** First BINDING
line of each run, session `2026-01-01`, decisions=0, fills=0:

```
613166: TURNOVER BUDGET BINDING: 56% of NAV traded in the last 21 sessions
820236: TURNOVER BUDGET BINDING: 70% ...
718249: TURNOVER BUDGET BINDING: 72% ...
725146: TURNOVER BUDGET BINDING: 72% ...
```

Four different values on the same window, instance and cash means the ledger is **inherited,
not computed**. It is: `turnover_ledger` is in `_RESIDUAL_SLEEVE_PERSIST_FIELDS`
(`broker.py:2985`) and rides the nexus strategy cache to RethinkDB
(`broker.py:3084-3092`, docstring: *"piggy-backs on `_RESIDUAL_SLEEVE_PERSIST_KEY` and rides
the nexus strategy cache to RethinkDB"*), and the 31-day calendar backstop
(`broker.py:3114`) does not age out buckets keyed to the same 2026-01 session dates a previous
run wrote. A re-run of the same window starts with the brake already on.

### 3.3 The budget cannot see the lane doing the churning — measured, not inferred

`_turnover_is_governed` (`broker.py:3143-3198`) returns **False** for the core symbol whenever
the core is armed in the base config or in *any* regime profile:

```python
# broker.py:3195-3196
core_sym = str(cfg.get("residual_sleeve_symbol", "SPY") or "SPY").strip().upper()
return str(symbol or "").strip().upper() != core_sym
```

and **every** ledger write is gated on it — `broker.py:4197`, `:4260`, `:4601`, `:4666`
(sleeve legs) and `:15756` (satellite). So core notional is never written.

600 lines later, `broker.py:3776-3777` says the opposite:

```
# budgeted, and core notional is still BOOKED into the ledger, so the budget
# continues to see the whole picture and throttle the satellite correctly.
```

**That comment is false, and the run proves it.** 2026-02-09 to 2026-02-18 in bt 613166: five
core trades, $3,064.50 of one-way SPY notional (~49% of the ~$6,240 NAV), **zero** non-SPY
trades. The logged ledger over exactly that span:

```
2026-02-09  TURNOVER BUDGET BINDING: 61% of NAV traded in the last 21 sessions
2026-02-09  [core] released 1.0029 SPY @ 690.72 (funding ...)
2026-02-10  [core] bought  $616.88 SPY @ 694.00 (band_deploy ...)
2026-02-10  TURNOVER BUDGET BINDING: 61% ...  (later ticks the same day: 60%)
2026-02-11  [core] released 0.8552 SPY @ 692.11 (funding ...)
2026-02-11  TURNOVER BUDGET BINDING: 60% ...
2026-02-16  [core] bought  $591.36 SPY @ 681.61 (band_deploy ...)
2026-02-17  TURNOVER BUDGET BINDING: 61% -> 60% ...
2026-02-18  [core] released 0.8349 SPY @ 682.76 (funding ...)
2026-02-18  TURNOVER BUDGET BINDING: 60% ...
```

61% -> 60%. It **fell by 1pp**. Booking $3,064 on a $6,240 NAV would have added **+49pp**.
The ledger did not see a dollar of it.

This cuts against the naive reading: because the core is invisible to the ledger, the saw-tooth
is **not** the direct cause of the 49 satellite blocks in 613166. It is worse in one way and
better in another — the budget enforces a 50% ceiling on the lane that produced 49%/mo of the
turnover while ignoring the lane that produced 111%/mo, and the number the operator reads
(`60%`) understates real book turnover by ~2.8x.

### 3.4 A dollar of budget is not a dollar of cost

The budget counts **dollars**, flat, across lanes whose true cost per dollar differs ~19x
(SPY 2.9 bps one-way at the repo's own 5 bps quote vs satellite 54.9 bps at the measured p90).
That is the deeper reason the brake binds on the wrong lane: it throttles the alpha engine by
notional while the cheap lane runs uncounted.

### 3.5 Cross-check: the same mechanism runs on SQQQ in a bear

bt 342380 (2026-03-02..03-30, +18.71%, the run flagged "do not break"): **25 SQQQ fills,
$12,657 of SQQQ gross, `churn_ratio` 5.49, 12 side-flips**, against $1,747 of IQM and $898 of
USO. The saw-tooth is not SPY-specific — it is a property of the residual sleeve, and on the
bear leg it runs on a -3x levered ETF.

---

## 4. Two claims in the tree that this run refutes

1. `broker.py:3776-3777` — *"core notional is still BOOKED into the ledger, so the budget
   continues to see the whole picture"*. **False**; §3.3. Contradicted by
   `_turnover_is_governed` (`broker.py:3143-3198`) and by the 61%->60% measurement.
2. `broker.py:3773-3775` — *"a +/-5pp weight band and a 5-day cadence, which hard-caps it at
   4.2 rebalances a month"*. **False**; measured 9.3–12.5/month, because `funding`
   (`core_sleeve.py:455-459`) is evaluated before both.

---

## 5. RANKED — what to change, expected effect, evidence

Every "expected effect" is arithmetic on the measured run, not a simulation. Per
`docs/OBJECTIVE.txt` ("Read the run. Config-based predictions have been wrong more often than
right"), items 1 and 3b are **predictions and need a paired run before they are believed**.

---

**1. Bound the `funding` release at the core's own band edge. One expression, `core_sleeve.py:457`.**

```python
sell = min(need, core_value)                                                    # today
sell = min(need, core_value,
           max(0.0, (current_w - (target_w - cfg.rebalance_band_pct)) * nav))   # proposed
```

*Expected effect:* removes the entire `band_deploy` leg. 100% of deploys in all four runs are
`band_deploy`, and every one exists only to close a breach the release itself opened
(mean 13.5–17.1pp against a 5.0pp band). Deploy leg = **$5,353 of $11,404 post-initial gross in
613166 (47%)** and **$5,298 of $11,186 in 820236 (47%)**. Charged cost saved **~$12.4 = 21 bps
of NAV**; at SPY's real spread ~$1.5. The larger win is 21 fewer core orders per 2 months and a
core that tracks its target instead of sitting 7–11pp under it.

*Evidence:* §2.3 (release fires at `40.0% -> 40.0%`, i.e. inside the band, and leaves the core
at 26.8%), §2.4 (88% / 90% of released dollars re-bought; 76% of released dollars funded
nothing).

*Risk, named:* under-funding a large rotation. This is **not** the bt 806490 failure mode —
that gated the release on a per-name verdict and dropped it to one release for the whole run
(`broker.py:14378-14384`: *"`insufficient_cash` went 7 -> 71, the core released ONCE in the
whole run, and after 01-26 nothing traded for five weeks"*). This bounds the *size* while the
release still fires on every bar it is asked to. Even so it caps one bar's funding at ~5% of
NAV (~$310 here) vs the observed mean release of $465, so the paired run must show
`insufficient_cash` does not move (baseline: 4 / 4 / 0 / 1 across 820236 / 613166 / 718249 /
725146).

---

**2. Give the core leg its own cost model. Zero behavioural risk; largest single move in the measured number.**

*Expected effect:* +$28.01 on 613166 = **+47 bps of measured return** (SPY charged
$32.01 -> $4.00). Charging the satellite its own measured p90 109 bps at the same time takes
whole-book cost $48.37 -> $42.74 — the headline barely moves (**+9 bps**) but the attribution
flips from 66% SPY / 34% satellite to 9% / 91%.

*Evidence:* `portfolio_emulator.py:70-71` (*"5 bps of quoted spread describes SPY, not this
book"*) against the 45.6 bps actually charged, proven applied to SPY by the 2026-01-06
cross-run mid reconstruction in §1.1; `simulated_execution.py:96` (p90 109.0) against the 45.6
charged to the satellite.

*Why it ranks this high:* every conclusion about which lane is expensive is currently drawn
from a model that overcharges the cheapest instrument in the book by ~9x and undercharges the
alpha lane by ~2.4x. Item 1's payoff, the passive-execution lever
(`simulated_execution.py:139-142`), and any future turnover decision are all priced off that
inversion. This is a measurement fix, not a P&L fix — but it changes what every other lever is
worth.

*Risk:* it makes past runs non-comparable and makes the satellite look worse. That is the point.

---

**3. Make the budget count what it can see, and see what it counts.**

*3a. Fix the two false comments (`broker.py:3773-3777`).* Documentation only, zero P&L. Anyone
reading `60%` off the log today is reading 36% of the book's real turnover (168%/mo measured in
613166), and the comment tells them the opposite. Evidence: §3.3, §4.

*3b. Log core notional as a separate, non-blocking counter.* The ledger write is already gated
in one place (`_turnover_is_governed`, `broker.py:3143`); a shadow accumulator that is written
but never read by `turnover_budget_state` gives the operator the true number without
re-creating the bt 152918 starvation the exemption exists to prevent (`broker.py:3766-3771`).
Evidence: §3.1 (core lane 93–111%/mo, invisible), §3.3.

*Do NOT flip `core_respects_turnover_budget=True`.* Measured: the core lane alone runs
93–111%/mo against a 50% budget, so booking-and-blocking it would exhaust the budget on the
core and block every satellite buy — exactly the bt 152918 failure documented at
`broker.py:3766-3771`.

---

**4. Reset the turnover ledger between paired runs.**

*Expected effect:* removes an uncontrolled 56–72% starting handicap that differs per run on
identical config, window and cash.

*Evidence:* §3.2 — four runs, same window, same $6,000, four different tick-1 values
(56 / 70 / 72 / 72%). `turnover_ledger` persists via `broker.py:2985` + `:3084-3092`. Any A/B
whose arms inherit different ledgers is measuring the ledger, not the lever.
`scripts/clear_backtest_state.py --backtest-id <id> --apply` clears the scoped per-instance
state (procedure: `docs/handoffs/2026-08-08-production-readiness-research.md` §2 step 6).

---

**5. Do NOT stamp the cadence clock on `funding` releases (`broker.py:4517`).**

Listed to close it off. It is the obvious reading of the asymmetry and it is the wrong fix: the
deploy leg is already the *smaller* leg (8–9 deploys vs 11–17 releases), so delaying it further
deepens the underweight (already mean 29.0% vs a 35–38% target) without touching the 13–17
releases that create the churn. The code comment at `broker.py:4505-4516` (*"resetting it there
would let one busy buy day freeze the band for five sessions"*) and the bt 806490 result
(`broker.py:14378-14384`) point the same way.

---

## Answer to the two direct questions

**What does the churn cost in bps of final return?**
As the backtest charges it: **44.1 bps of the $6,000 book in bt 613166** — $26.46 of execution
cost on $11,403.69 of post-initial SPY gross at 23.2 bps one-way — which is **4.81% of the
+9.17%**, ~265 bps/yr. 43.3 bps in 820236, 34.5 bps in 718249, 27.7 bps in 725146.
**But ~87% of that is a modelling artifact**: SPY is charged the book's notional-weighted
45.6 bps spread while `portfolio_emulator.py:70` says 5 bps describes SPY. At 5 bps the same
churn costs **5.5 bps of NAV**.

**Single smallest change that would cut it most?**
Bound the `funding` release at the core's lower band edge — one expression at
`core_sleeve.py:457`. It removes the whole `band_deploy` leg, which is **47% of the
post-initial core gross in both 613166 and 820236**, because 100% of deploys are `band_deploy`
and every one of them closes a 13–17pp breach the release opened while the core was already
inside a 5pp band. Needs a paired run (same window, own `history_scope_salt`, cleared turnover
ledger) with `insufficient_cash` as the guard metric.

If the goal is instead to cut the *measured* number as fast as possible with no behavioural
risk, item 2 is bigger and cheaper (+47 bps on 613166 from one constant) — but it moves the
backtest, not the account.

---

## Method / reproducibility

```
python3 scripts/pull_backtest_logs.py 613166 --filter '\[core\]|FILL (BUY|SELL)|TURNOVER' --stdout
python3 scripts/pull_backtest_logs.py 820236 --filter '\[core\]|FILL (BUY|SELL)|TURNOVER' --stdout
python3 scripts/pull_backtest_logs.py 718249 --filter '\[core\]|FILL (BUY|SELL)|TURNOVER' --stdout
python3 scripts/pull_backtest_logs.py 725146 --filter '\[core\]|FILL (BUY|SELL)|TURNOVER' --stdout
python3 scripts/pull_backtest_logs.py 342380 --filter 'FILL (BUY|SELL)' --stdout
```

* Fills parsed from `[execution] FILL (BUY|SELL) <SYM> qty=.. price=.. fees=.. quote=.. model=..`
* Order source parsed from the `SimulationSubmission(... source='...')` echoed on each `[core]`
  line; submission order and fill order match 1:1 on side in all four runs
  (22/22, 26/26, 22/22, 16/16), which is what licenses attributing each fill to a source
* Mid per fill inverted from `portfolio_emulator.py:497-521`; the reconstructed `fees` match
  the logged `fees=` to $5e-7, and two runs filling opposite sides of the same quote invert to
  an identical mid — that is what makes the cost decomposition exact rather than assumed
* Session dates taken from `[Pending] YYYY-MM-DD — queue_total=` lines; 42 weekday sessions in
  the finished runs, 34 in the stopped one
* Result-row fields (`sleeve_churn`, `pnl_per_stock`, `risk_metrics.equity_curve`,
  `strategy_schema`, `stock_price_change`) read from the `.json` sidecar the pull script writes
* `sleeve_churn` itself is `backend/backtest_risk_metrics.py:122-176`; the run's values
  (613166: fill_count 22, post_initial_gross 11403.67, net 1702.16, churn_ratio 8.109,
  side_flips 17) reproduce from the log independently
