# Exits and capture — bt 820236 / 613166 / 725146 / 342380

Read-only investigation. No code changed, no run started or stopped, nothing pushed.
Every number below comes from the run logs pulled with
`python3 scripts/pull_backtest_logs.py <id> --stdout` plus the `/backtests/<id>/summary`
sidecar written by the same script. Where I could not prove something I say so.

---

## 0. HEADLINE

**Exits are not the leak. In the best run, exits cost $0.08 and saved $303.64.**

For bt 820236 (best live-faithful, +12.33%), the five names held to the end:

| | dollars |
|---|---|
| P&L available from the first bar each name was buyable, at the dollars actually deployed | **$1,200.58** |
| minus lost to LATE ENTRY (fill price above first-buyable price) | −$352.89 (29.4%) |
| minus lost to EXIT / TRIM / STOP | **−$0.08 (0.007%)** |
| = actually kept | **$847.61** |
| **capture ratio vs first-buyable bar** | **70.6%** |
| **capture ratio vs actual entry price** | **99.99%** |

The two names that *were* sold (CORD, OMER) were both cut by the −10% circuit breaker,
and cutting them **made $303.64 more than holding them would have** (CORD alone went on
to −55.70% for the window).

The parent's hypothesis is **VERIFIED** for 820236 and largely verified for the other
three runs. The counter-examples are narrow and named below (HESM, NTR, AIFD, SQQQ).

---

## 1. WHICH EXIT MECHANISMS ACTUALLY FIRED

Counted by grepping the emitter strings that the code would write. Zero means the
mechanism exists in the tree but never fired in that run — I checked the emitters exist:
`backend/strategies/graph_nexus_analysis.py:19706-19708` (`Hold-limit EXIT`),
`:20275` (`Profit take TRIGGER`).

| mechanism | 820236 | 613166 | 725146 | 342380 |
|---|---|---|---|---|
| `Hold-limit EXIT` (max_hold_days) | 0 | 0 | 0 | 0 |
| `Profit take TRIGGER` (profit_take) | 0 | 0 | 0 | 0 |
| `Trailing stop` **fired** | **0** | **0** | **0** | **0** |
| `Trailing stop SUPPRESSED (trailing_stop_disabled)` | **25** | **158** | **138** | **10** |
| fast_loser_cut | 0 | 0 | 0 | 0 |
| v32_convert | 0 | 0 | 0 | 0 |
| backfill_rotation | 0 | 0 | 0 | 0 |
| `[sell-gate] gate=circuit_breaker … result=fired` (log lines) | 2 | 4 | 4 | 0 |
| `Momentum rotation: sell` | 0 | 1 | 1 | 0 |
| `Momentum portfolio swap: sell` | 0 | 1 | 2 | 0 |
| `ML overlay PRESERVE forced-exit` | 1 | 0 | 0 | 0 |
| `[core] released … SPY` (index-core sleeve) | 17 | 13 | 11 | 0 |
| `[sleeve] released … SQQQ` (bear-leg cash refill) | 0 | 0 | 0 | 53 |
| `[sleeve] released … SPY (regime=bear protective exit)` | 0 | 0 | 0 | 2 |

`fast_loser_cut`, `v32_convert`, `backfill_rotation`, `profit_take` and `max_hold_days`
all exist in `backend/` (49, 20, 49, 100 and 60 source references respectively) but
**produced no log line in any of the four runs**. I cannot distinguish "disabled by
config" from "condition never met" from the log alone; either way they realised $0.

`sell_override` is not an exit *cause*, it is the broker-level intent name every enforced
sell gets. All 140 sell-intent lines in 820236 read `action_intent=sell_override`; the
cause is one layer up in `[sell-gate]` / `Sell enforcement`.

---

## 2. EVERY SELL FILL, WITH CAUSE AND REALISED P&L

Derived from `[BROKER] [execution] FILL SELL …` lines (the authoritative fill ledger) and
the cause line that precedes each one. Round-trip P&L cross-checked against
`/backtests/<id>/summary` `total_round_trip_pnl`.

### bt 820236 — 19 sells (17 sleeve, **2 alpha**)

| # | date | sym | cause (quoted) | realised |
|---|---|---|---|---|
| 17 | various | SPY | `[core] released N SPY @ … (core rebalance: funding (X% -> Y% of NAV) … source='residual_bull_refill')` | +$13.81 |
| 1 | 2026-01-08 | CORD | `[sell-gate] CORD \| gate=circuit_breaker \| tier=LOW \| regime=bull \| unrealized=-10.4% \| floor=-10.0% (base=-10%) \| result=fired` | **−$59.43** |
| 1 | 2026-01-20 | OMER | `[sell-gate] OMER \| gate=circuit_breaker … unrealized=-10.4% \| floor=-10.0% \| result=fired` → `ML overlay PRESERVE forced-exit: OMER score=-1 reason=Circuit breaker` → `Sell enforcement ADD: OMER forced_exit=True` | **−$60.99** |

`summary.total_round_trip_pnl = -120.42`, `round_trips = 2` — exactly CORD + OMER.
**Confirms the parent: 2 non-sleeve sells, both losers.**

### bt 613166 — 16 sells (13 sleeve, **3 alpha**)

| date | sym | cause | exit P&L% | realised |
|---|---|---|---|---|
| various ×13 | SPY | `[core] released … residual_bull_refill` | +0.49% | +$29.17 |
| 2026-01-15 | HESM | `Momentum rotation: sell HESM (score=0.036) → buy RVMD (score=0.862, $869)` | **+1.23% (a winner)** | +$10.19 |
| 2026-02-04 | NVDA | `Momentum portfolio swap: sell NVDA (pnl=-6.2%) → buy SNDK (score=1.936, $817)` | −7.43% | −$62.46 |
| 2026-01-30 | PLRZ | `[sell-gate] PLRZ \| gate=circuit_breaker … result=fired` | −21.52% | −$18.47 |

`summary.total_round_trip_pnl = -70.73` ✓

### bt 725146 — 15 sells (11 sleeve, **4 alpha**)

| date | sym | cause | exit P&L% | realised |
|---|---|---|---|---|
| various ×11 | SPY | `[core] released … residual_bull_refill` | +0.02% | +$0.64 |
| 2026-01-13 | NTR | `Momentum portfolio swap: sell NTR (pnl=-2.3%) → buy RVMD (score=0.822, $869)` | −1.62% | −$13.63 |
| 2026-01-15 | HESM | `Momentum rotation: sell HESM (score=0.036) → buy RVMD (score=0.862, $1024)` | **+1.23% (a winner)** | +$12.38 |
| 2026-02-05 | AIFD | `Momentum portfolio swap: sell AIFD (pnl=-6.2%) → buy SNDK (score=1.382, $828)` | −5.10% | −$43.07 |
| 2026-01-30 | PLRZ | `[sell-gate] PLRZ \| gate=circuit_breaker … result=fired` | −21.95% | −$144.68 |

`summary.total_round_trip_pnl = -189.01`, `win_rate_percent = 25.0` ✓

### bt 342380 — 17 sells (**all sleeve**, 0 alpha)

| date | sym | cause | realised |
|---|---|---|---|
| ×15 | SQQQ | `[sleeve] released N SQQQ @ P (bear-leg refill: cash X% -> target 15% of NAV)` | +$88.41 realised (position left open, +$703 unrealised) |
| 2026-03-13, 03-19 | SPY | `[sleeve] released 1.2266 SPY @ 665.16 (regime=bear protective exit)` | −$29.56 |

`summary.total_round_trip_pnl = -29.56`, `round_trips = 2` ✓
**No alpha name was ever sold in 342380.** IQM and USO were bought once and held to the end.

### Total realised by non-sleeve exits, all four runs

| run | alpha sells | realised |
|---|---|---|
| 820236 | 2 | **−$120.42** |
| 613166 | 3 | **−$70.74** |
| 725146 | 4 | **−$189.01** |
| 342380 | 0 | **$0.00** |
| **total** | **9** | **−$380.17** |

Every dollar these four runs made came from positions that were **never sold**.

---

## 3. WERE WINNERS HELD? YES — AND MEASURABLY SO

`trailing_stop_disabled` is doing real work. In all four runs the trailing stop fired
**zero** times, and every large winner had a 15–20% peak-to-trough drawdown that the
suppression held through:

| run | symbol | max `drop=` suppressed | how the name finished |
|---|---|---|---|
| 820236 | WDC | `drop=17.1% >= 15%` (9 lines) | **+53.63%, +$450.49** |
| 820236 | LRCX | `drop=16.2% >= 15%` (9 lines) | **+28.36%, +$238.22** |
| 820236 | SNDK | `drop=18.7% >= 15%` (7 lines) | **+42.3% on lot 1** |
| 613166 | AGMI | `drop=20.3% >= 15%` (158 lines) | **+31.50%, +$341.72** (biggest winner in the run) |
| 725146 | AGMI | `drop=20.3% >= 15%` (138 lines) | +11.2% at the 2026-02-17 stop |
| 342380 | USO | `drop=20.1% >= 15%` (10 lines) | **+43.51%, +$390.78** |

Quote, `820236` 2026-01-08:
`Trailing stop SUPPRESSED (trailing_stop_disabled): WDC drop=17.1% >= 15% — held; catastrophic stop is the floor`

A live trailing stop at 15% would have exited the four largest winners in this whole
investigation. It did not. Re-arming it would destroy the runs.

`winner_protect` also blocked 6 BA sells in 820236 and 13 in 613166, e.g.
`[sell-gate] BA | gate=winner_protect | pnl=+8.3% | drop_from_peak=4.9% < 8.0% | result=blocked (hold)`.

**Was any winner trimmed or stopped?** Three cases, all narrow:

1. **HESM (613166 and 725146)** — sold at **+1.23%** by `Momentum rotation`, then went to
   +12.19% for the window. This is a genuine winner trim. Cost below.
2. **SQQQ (342380)** — the bear leg was released 15 times at an average $73.96 while it
   ended at $89.80. Volume-weighted, every share released went out **17.6% below the
   exit price**. Cost below.
3. **SNDK third lot (820236)** — `winner_add_buy` on 2026-02-23 at $679.70, which is
   **above** the $635.94 end price: −$7.29. That is an add, not a trim, but it is a
   capture loss on a winner.

No other position in profit was reduced in any of the four runs.

---

## 4. PER-POSITION CAPTURE

`capture vs window` = position P&L% ÷ the name's `Stock movement (start -> end)` % from the
run's own summary block. `capture vs first-buyable` uses the price at the first bar the
name emitted a fundable buy intent (`SYM @ ts ($price): buy action_intent=…`).
`capture vs entry` = P&L% ÷ (window-end price ÷ actual average fill − 1).

### bt 820236 — window 2026-01-01→03-01, +12.33% ($6,000 → $6,747.71)

| sym | entry | avg fill | exit | end px | P&L | P&L% | window move | cap vs window | first buyable | cap vs first-buyable | **cap vs entry** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WDC | 01-02 | $181.55 | **held to end** | $279.79 | +$450.49 | +53.63% | 172.27→278.93 **+61.91%** | 86.6% | 01-01 @172.27 | 86.6% | **100.0%** |
| LRCX | 01-02 | $182.26 | **held to end** | $233.95 | +$238.22 | +28.36% | 171.13→233.95 **+36.71%** | 77.3% | 01-01 @171.13 | 77.3% | **100.0%** |
| SNDK | 01-20 | $523.80 (3 lots) | **held to end** | $635.94 | +$100.95 | +20.57% | 237.33→631.54 **+166.10%** | **12.4%** | 01-12 @388.46 | **32.9%** | **100.0%** |
| CPER | 01-02 | $35.08 | **held to end** | $36.89 | +$55.69 | +5.16% | 34.94→36.89 +5.58% | 92.5% | 01-01 @34.94 | 92.5% | **100.0%** |
| BA | 01-02 | $222.80 | **held to end** | $227.51 | +$2.26 | +2.11% | 217.10→227.51 +4.79% | 44.1% | 01-02 @217.87 | 47.7% | **99.8%** |
| CORD | 01-02 | $34.90 | **01-08 circuit_breaker** | $17.68 | −$59.43 | −8.11% | 39.91→17.68 −55.70% | — | 01-01 @39.91 | — | exit **saved $302.27** |
| OMER | 01-09 | $13.03 | **01-20 circuit_breaker** | $12.05 | −$60.99 | −7.35% | 17.18→12.05 −29.83% | — | 01-09 @13.53 | — | exit saved $1.37 |
| SPY | sleeve | $688.07 | 17 core releases | $686.16 | +$8.76 | +0.11% | +0.64% | — | — | — | — |

Read the last column. **Once the book owns a name it keeps essentially 100% of the
subsequent move.** The gap is entirely (a) the price it paid and (b) how many dollars it
put in.

The SNDK per-lot breakdown makes the point precisely:

| lot | date | price | cost | P&L to $635.94 |
|---|---|---|---|---|
| 1 | 2026-01-20 | $443.83 | $127.74 | **+$55.29 (+43.3%)** |
| 2 | 2026-01-29 | $517.69 | $249.89 | +$57.08 (+22.8%) |
| 3 | 2026-02-23 | $679.70 | $113.19 | **−$7.29 (−6.4%)** |
| | | | **$490.82** | **+$105.09** |

Lot 1 captured **100% of its post-entry move**. The blended +20.57% is an averaging-up
artefact, not an exit failure.

**Why SNDK only got $490:** the log shows five funded buy attempts refused before the
first fill —
`MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)` on 01-12, 01-13 and 01-19;
`Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700 …)` on 01-14, 01-15, 01-16;
and `SATELLITE CAP: SNDK trimmed $873 -> $591` / `$883 -> $564` / `$884 -> $562` /
`$228 -> $114` on four of those bars.

**And the one that got through was clamped by the emulator.** On 2026-01-20:

```
[BROKER] Buy gate inputs for SNDK: cash=$577.08 reserved=$0.00 floor=$120.00
         effective_floor=$0.00 high_conv=True open_pos=5 cash_per_trade=$562.10
         available=$577.08 cash_to_use=$562.10 → PASS
[BROKER] [execution] FILL BUY  SPY  qty=0.64648606 price=685.375940   ($443.05)
[BROKER] [execution] FILL SELL OMER qty=63.64538935 price=12.072291   ($768.35)
[BROKER] [execution] FILL BUY  SNDK qty=0.28781821 price=443.834068   ($127.74)
```

The broker approved **$562.10** and the fill was **$127.74 — 22.7% of it**. In the same
batch a $443.05 index-core SPY buy (queued the previous bar,
`[core] bought $448.13 SPY @ 691.58 (band_deploy …)`) filled first and consumed the cash,
and the $768.35 OMER sell that was supposed to fund the buy had not settled. $577.08 −
$443.05 = $134.03, which is exactly the ceiling the SNDK fill hit. This is the
`portfolio_emulator.py:1414-1423` re-clamp flagged in
`docs/handoffs/2026-08-08-production-readiness-research.md` §4a, observed live in the run.
Had SNDK received the approved $562.10 at $443.83, the extra $434.36 would have been worth
$622.30 at $635.94 — **+$187.94, or 25% of the run's total P&L**.

### bt 613166 — +9.17% ($6,000 → $6,550.81)

| sym | avg fill | exit | end px | P&L | P&L% | window move | cap vs window | **cap vs entry** |
|---|---|---|---|---|---|---|---|---|
| AGMI | $67.21 | **held** | $88.39 | +$341.72 | +31.50% | 66.72→88.39 +32.48% | 97.0% | **100.0%** |
| NTR | $61.91 | **held** | $75.06 | +$178.30 | +21.23% | 61.73→75.06 +21.59% | 98.3% | **100.0%** |
| PLD | $127.79 | **held** | $142.62 | +$105.29 | +11.60% | 127.66→142.62 +11.72% | 99.0% | **100.0%** |
| EGO | $38.87 | **held** | $46.43 | +$29.20 | +19.45% | 35.92→46.43 +29.26% | 66.5% | **100.0%** |
| SNDK | $616.76 | **held** | $635.94 | +$3.04 | +2.39% | 237.33→631.54 **+166.10%** | **1.4%** | **99.8%** |
| AMZN | $243.67 | **held** | $210.01 | −$68.54 | −13.81% | −9.03% | — | — |
| HESM | $34.38 | **01-15 momentum_rotation** | $38.70 | +$10.19 | +1.22% | 34.49→38.70 **+12.19%** | **10.0%** | **9.7%** |
| NVDA | $189.57 | **02-04 portfolio_swap** | $177.21 | −$62.46 | −7.44% | −4.99% | — | — |
| PLRZ | $14.68 | **01-30 circuit_breaker** | $13.12 | −$18.47 | −21.53% | 8.11→13.12 **+61.84%** | — | — |

Held-to-end names: available at first-buyable $591.65, kept **$589.01 — 99.6%**.
Exited names: kept −$70.74 where holding to window end would have been +$41.08 → the
three exits cost **$111.82**, which is **20.3% of the run's entire +$550.81**.

Note PLRZ: the window move was **+61.84%** but the book bought at **$14.68, above the
$13.12 window-end price**. That is an entry failure the circuit breaker then had to clean
up; the exit is not the problem.

### bt 725146 — stopped 2026-02-17 at 79.65%, +0.11% ($6,000 → $6,006.73)

No `Stock movement` summary block exists (the run was stopped), so window-end prices for
this run come from bt 613166, which ran the identical window on the same price data.

| sym | avg fill | exit | exit P&L% | realised | measured foregone |
|---|---|---|---|---|---|
| NTR | $61.91 | 01-13 `Momentum portfolio swap → RVMD` | −1.62% | −$13.63 | NTR quoted **$69.34 at 2026-02-17 15:00 in this run's own log** → 13.5674 × ($69.34 − $60.91) = **$114.37** |
| HESM | $34.38 | 01-15 `Momentum rotation → RVMD` | +1.23% | +$12.38 | last in-run price 2026-01-30 $35.73 → **$27.43 proven**; at the 613166 window-end $38.70 → $115.05 (indicative, run stopped early) |
| AIFD | $38.80 | 02-05 `Momentum portfolio swap → SNDK` | −5.10% | −$43.07 | **not measurable** — no AIFD price after 2026-02-05 in this log |
| PLRZ | $14.76 | 01-30 `circuit_breaker` | −21.95% | −$144.68 | −$71.4 avoided vs the $13.12 window end (exit was **correct** relative to entry, entry was wrong) |
| AGMI | $67.21 | **held** | +11.2% at stop | — | trailing stop suppressed at `drop=20.3%` × 138 |

### bt 342380 — BEAR window 2026-03-02→03-30, +18.71% ($6,000 → $7,122.67)

| sym | avg fill | exit | end px | P&L | P&L% | window move | cap vs window | **cap vs entry** |
|---|---|---|---|---|---|---|---|---|
| USO | $90.46 | **held, never sold** | $129.82 | +$390.78 | +43.51% | 81.93→129.82 +58.45% | 74.4% | **100.0%** |
| IQM | $91.13 | **held, never sold** | $89.58 | −$29.82 | −1.71% | 93.71→89.58 −4.41% | — | **100.7%** |
| SQQQ | $72.61 (10 buys) | **15 sleeve releases** | $89.80 | +$791.17 | +10.18% | 70.82→89.80 **+26.80%** | **38.0%** | **43.0%** |
| SPY | $670.87 | 2 bear protective exits | $632.02 | −$29.56 | −1.77% | −7.89% | — | — |

**SQQQ is the one real capture failure in the whole investigation.** The bear leg is
parked and then immediately un-parked to hold cash at 15% of NAV:

```
[sleeve] parked $710.63 in BEAR leg SQQQ @ 75.58 (regime=bear, leg=2428/4540 cap, …)   [3× same bar]
[sleeve] released 11.6452 SQQQ @ 76.70 (bear-leg refill: cash 1.3% -> target 15% of NAV)   [next bar]
```

The fill ledger:

| | qty | notional | avg price |
|---|---|---|---|
| 10 buys | 106.99 sh | $7,768.71 | $72.61 |
| 15 sells | 66.10 sh | $4,888.48 | $73.96 |
| left open | 40.89 sh | $3,671.78 | end $89.80 |

Two round trips are visible inside one hour: 2026-03-16 13:00 buy 22.68 sh @ $73.58
($1,668.73), 2026-03-16 14:00 sell 11.57 sh @ $72.46; 2026-03-20 13:00 buy 22.50 sh @
$76.88, 2026-03-20 14:00 sell 11.65 sh @ $78.42.

**Measurement:** the book put $12,657 of SQQQ notional (211% of NAV) through a name that
went up monotonically +26.80%, and ended holding **13.5% fewer shares than it started
with**. Had it simply held the original 2026-03-04/05 tranche (47.2989 sh, $3,301.79) to
the end it would have made **$945.65**; it made **$791.17**. The churn cost **$154.48 =
13.8% of the run's entire +$1,122.67**, and 66.10 shares were released at an average
17.6% below the price they ended at.

---

## 5. THE ROTATION PATHOLOGY: 5 SELLS, 5 FAILED REPLACEMENTS

Every `Momentum rotation` / `Momentum portfolio swap` in these runs sold the position and
then **failed to buy the replacement at anything close to the intended size**. The sell is
not conditional on the buy filling.

| run | sold | intended buy | what happened to the buy | replacement bought? |
|---|---|---|---|---|
| 613166 | HESM ($845 out, +1.23%) | RVMD $869 | `SATELLITE CAP: RVMD trimmed $869 -> $123` → `TURNOVER BYPASS CEILING: RVMD refused despite raw=+0.862 — 82% of NAV traded` → `TURNOVER BUDGET BLOCK: RVMD skipped` → `Gate skips reported back: RVMD (turnover_budget)` | **NO — $0** |
| 613166 | NVDA ($778 out, −7.43%) | SNDK $817 | `SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($21 room)` on 02-04; **$87.45** filled on 02-05 | partially — **10.7% of $817** |
| 725146 | NTR ($826 out, −1.62%) | RVMD $869 | `Backfill queue BLOCKED: RVMD (full_priority_blocked …)` | **NO — $0** |
| 725146 | HESM ($1,027 out, +1.23%) | RVMD $1,024 | `Backfill queue FORCE-BLOCKED: RVMD` → `Nexus priority invariant: RVMD ended without executable size or queue` | **NO — $0** |
| 725146 | AIFD ($801 out, −5.10%) | SNDK $828 | `Gate skips reported back: SNDK (insufficient_cash)` | **NO — $0** |

**5 of 5 rotations destroyed a position. 4 of 5 bought nothing at all.** Combined, they
took $4,277 of alpha exposure out of the book and put $87 back.

RVMD never appears in the fill ledger of either run (`RVMD fills: []` in both).

---

## 6. TURNOVER CONTEXT (why the replacement legs are refused)

Total traded notional over the window, on $6,000:

| run | sells | of which sleeve | sell $ | buy $ | total | % of $6,000 |
|---|---|---|---|---|---|---|
| 820236 | 19 | 17 (89%) | $7,330 | $12,617 | $19,946 | **332%** |
| 613166 | 16 | 13 (81%) | $7,741 | $13,118 | $20,859 | **348%** |
| 725146 | 15 | 11 (73%) | $7,150 | $13,059 | $20,209 | **337%** |
| 342380 | 17 | 17 (100%) | $6,532 | $12,086 | $18,618 | **310%** |

**80–100% of all sell fills are sleeve legs, not alpha exits.** The index-core SPY
refill/deploy loop and the SQQQ bear-leg cash refill loop, between them, generate almost
all of the turnover — and it is that turnover that then triggers
`TURNOVER BUDGET BLOCK` / `TURNOVER BYPASS CEILING` refusals on the conviction buys
(RVMD above; also `EPRT`, `FRT` in 725146 at 90% of NAV). The sleeve is eating the
turnover budget that the alpha book needs.

---

## 7. WHAT I COULD NOT PROVE

- Whether `profit_take`, `max_hold_days`, `fast_loser_cut`, `v32_convert` and
  `backfill_rotation` are config-disabled or simply never met their condition. They emit
  nothing in any of the four logs. Do not assume they are off.
- AIFD's post-exit path in 725146 — no price after 2026-02-05 exists in that log.
- 725146 window-end values for HESM: the run was stopped on 2026-02-17, so the $115.05
  figure borrows 613166's window-end price. The $27.43 to 2026-01-30 is proven in-run.
- The SQQQ "$154.48 churn cost" is against the specific counterfactual of holding the
  first tranche. It is not a claim that the sleeve could have been sized that way given
  the 15% cash rule.
- Everything here is `pit_mode=research` (`PIT RESEARCH MODE: no frozen snapshots for …`
  appears on every bar), i.e. it carries the declared lookahead bias and is not
  promotion-eligible.

---

## 8. RANKED: WHAT TO CHANGE

**1. Make a rotation/swap sell conditional on its replacement buy actually filling.**
*Expected effect:* recovers the 5-of-5 failure above. Directly measured cost in these two
runs: **$111.82 in 613166 (20.3% of that run's P&L)** and, in 725146, $114.37 proven on NTR
alone plus $27.43 on HESM, against a run that finished at +0.11%. 820236 has zero
rotations and is the best run — that is the paired evidence.
*Evidence:* `Momentum rotation: sell HESM (score=0.036) → buy RVMD (score=0.862, $869)`
followed by `TURNOVER BUDGET BLOCK: RVMD skipped — 82% of NAV traded in 21 sessions` and
`Gate skips reported back: RVMD (turnover_budget)`; RVMD never appears in either fill
ledger. Same shape three more times in 725146.

**2. Stop the SQQQ park→refill loop (do not release the bear leg to top up a cash target).**
*Expected effect:* +$154.48 on bt 342380 (13.8% of that run's P&L), and removes ~$12,657 of
notional — 211% of NAV in one name — from the turnover budget that is currently refusing
conviction buys.
*Evidence:* 10 buys / 15 sells of SQQQ; `[sleeve] parked $710.63 in BEAR leg SQQQ @ 75.58`
immediately followed the next bar by `[sleeve] released 11.6452 SQQQ @ 76.70 (bear-leg
refill: cash 1.3% -> target 15% of NAV)`; released 66.10 sh at avg $73.96 vs a $89.80 end;
buy-and-hold of the first tranche = $945.65 vs actual $791.17.

**3. Fix the intra-bar cash race between the index-core deploy and the conviction buy.**
*Expected effect:* on bt 820236 the single approved-but-clamped SNDK order was worth
**+$187.94 (25% of the run's P&L)**.
*Evidence:* `Buy gate inputs for SNDK: … cash_to_use=$562.10 → PASS` on 2026-01-20,
followed in the same execution batch by `FILL BUY SPY … ($443.05)` then
`FILL BUY SNDK qty=0.28781821 price=443.834068` = $127.74 (22.7% of approved).
$577.08 − $443.05 = $134.03. Matches the `portfolio_emulator.py:1414-1423` re-clamp
flagged in the 2026-08-08 production-readiness handoff §4a.

**4. Do NOT touch the exit stack. Specifically, do not re-arm the trailing stop.**
*Expected effect:* protecting what already works.
*Evidence:* trailing stop fired 0 times in 4 runs; `Trailing stop SUPPRESSED
(trailing_stop_disabled)` fired 331 times and held WDC through `drop=17.1%` (finished
+53.63%), LRCX through 16.2% (+28.36%), SNDK through 18.7% (+42.3% lot 1), AGMI through
20.3% (+31.50%, the biggest single winner in 613166) and USO through 20.1% (+43.51%). Every
one of those would have been stopped out at 15%.

**5. Do NOT weaken the −10% circuit breaker.**
*Expected effect:* protecting what already works.
*Evidence:* in bt 820236 the two circuit-breaker exits **made $303.64 more than holding**
(CORD cut at −8.11% went on to −55.70% for the window; exit saved $302.27). Total exit
cost across the five held-to-end names in that run: **$0.08**.

**6. Everything left is entry price and entry size, not exits.**
*Expected effect:* this is where the remaining 29.4% of 820236's available P&L sits.
*Evidence:* capture-vs-actual-entry is 99.8–100.0% on every held position in every run
(only exceptions: HESM 9.7% and SQQQ 43.0%, both above). SNDK moved +166.10% over the
window; the book got $490.82 into it after `MAX_POSITIONS_GATE: blocked SNDK (held=6,
cap=6)` ×3, `Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700)` ×3 and
four `SATELLITE CAP` trims, and captured 12.4% of the move. PLRZ moved +61.84% and was
bought at $14.68 — above its own $13.12 window-end price.
