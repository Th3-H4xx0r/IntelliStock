# Local simulation of the allocation + gate chain — findings

Read-only investigation + one new tool. **No production code was edited**
(`backend/broker.py`, `backend/core_sleeve.py`, `backend/strategies/` untouched),
no backtest started or stopped, nothing pushed.

Written 2026-08-08. Evidence base: the eight runs in `docs/investigations/_RUNS.md`,
pulled with `scripts/pull_backtest_logs.py`. Code references are against
`backend/broker.py` sha256[:12] `a9fff5854de0` (16,406 lines) and
`backend/core_sleeve.py` sha256[:12] `42283e315e1f` — **broker.py was being edited
by another agent while this ran** (it grew from 16,347 to 16,406 lines mid-session),
so line numbers below are indicative and the durable anchors are the symbol names
and the log text.

Every number here is reconstructed from a log line. Where I could not prove
something I say so.

---

## 0. TL;DR

1. **`max_positions = 6` is the only broker-side gate that binds on the margin.**
   Re-running the gate chain on four logs of the reference window shows that
   changing the satellite share, the core target, the core floor, the turnover
   budget, the conviction-overflow threshold, or turning the index core off
   entirely leaves the admitted-buy count **identical** — the refused candidate
   just falls through to `MAX_POSITIONS_GATE` instead. Raising the cap 6 → 8
   takes admitted buys from 13 to 50 on bt 820236.
2. **The index core lane traded 226% of the book to net +$8.76.** In bt 820236
   SPY was 68.1% of all gross notional ($13,585 of $19,948) and produced
   +$8.76 of the run's +$739.61 — 1.2%. It also permanently occupies one of the
   six position slots.
3. **93.6% of the best run's P&L came from two names bought on day one.** WDC
   (+$450.49) and LRCX (+$238.22) of +$735.94 reconstructed. Every gate decision
   after 2026-01-02 is fighting over the remaining 6.4%.
4. **The sell-proceeds credit is measurably inert in backtest.** It fired in one
   of eight runs, twice. On the only event where it mattered the ceiling went
   $700.74 → $1,397.39, the broker sized $755.47, and the fill was $700.65 —
   the emulator re-clamped to pre-sell buying power. This confirms the
   2026-08-08 handoff's highest-value FLAG with a measurement instead of a
   prediction.
5. **The seven chop/bull arms of the same window hold near-disjoint books**
   (median pairwise Jaccard 0.11 on the ex-SPY end book; 24 distinct names, only
   7 held by more than one arm). The +6.02% → +15.04% spread is mostly which
   five names got the slots, not which lever was on.
6. **New tool: `scripts/simulate_allocation.py`** re-runs the allocation + gate
   chain of a finished log under any config, using the production functions, for
   zero credits. 46 unit tests in `backend/tests/test_simulate_allocation.py`.

---

## 1. What the best live-faithful run actually did

`bt 820236`, v2-let-run-core, 2026-01-01..2026-03-01, $6,000, 3600s, **+12.33%**
(`$6,739.61`). 38,154 log lines, **38 fills**, 634 broker ticks.

Full P&L attribution, reconstructed from the `FILL` lines plus the last
`Monitor decision: … cp=$…` mark for each name. It reconciles to **$735.94**
against the API's reported **+$739.61** (0.5%, the residual being the exact
end-of-run mark):

| name | end qty | entry | last mark | move | gross traded | P&L | share |
|---|---|---|---|---|---|---|---|
| WDC  | 4.6266 | 181.55 | 278.93 | **+53.6%** | $840 | **+$450.49** | 61% |
| LRCX | 4.6088 | 182.26 | 233.95 | **+28.4%** | $840 | **+$238.22** | 32% |
| SNDK | 0.9371 | 443.83 | 631.54 | **+42.3%** | $491 | +$100.95 | 14% |
| CPER | 30.7857 | 35.08 | 36.89 | +5.2% | $1,080 | +$55.69 | 8% |
| **SPY (core)** | 2.6499 | 682.97 | 686.16 | +0.5% | **$13,585** | **+$8.76** | **1.2%** |
| BA   | 0.4804 | 222.80 | 227.51 | +2.1% | $107 | +$2.26 | 0% |
| CORD | 0 | 34.90 | 31.28 | -10.4% | $1,407 | -$59.43 | -8% |
| OMER | 0 | 13.03 | 12.02 | -7.8% | $1,598 | -$60.99 | -8% |

`WDC + LRCX = $688.71 = 93.6%` of the reconstructed P&L. Both were bought at
$840 (14.0% of NAV) in the **opening basket on 2026-01-02**:

```
[BROKER] [execution] FILL BUY LRCX qty=4.60875294 ... price=182.256418 quote=2026-01-02 15:00:00+00:00
[BROKER] [execution] FILL BUY WDC  qty=4.62656307 ... price=181.554815 quote=2026-01-02 15:00:00+00:00
```

Everything the allocation and gate machinery did over the following 40 sessions
is worth $47.23 net.

---

## 2. The index core lane: 226% of NAV traded for +$8.76

Same run, gross notional by symbol (26 of the 38 fills are SPY):

```
SPY   26 fills  $13,584.56 gross   net +2.6499 shares
OMER   2         $1,597.63          net  0
CORD   2         $1,406.51          net  0
CPER   2         $1,079.97          net +30.79
LRCX   1           $839.97
WDC    1           $839.97
SNDK   3           $490.83
BA     1           $107.02
TOTAL           $19,946.47
```

* SPY = **68.1% of all gross notional** and **226% of the $6,000 book**.
* Time-weighted SPY market value over the 48 sessions with a SPY mark:
  **$1,886.65** (min $1,635, max $2,598), i.e. roughly 29% of NAV parked in an
  index that moved **687.73 → 686.16 = -0.23%** over that span.
* Net contribution after all modelled execution cost: **+$8.76**.

This replicates across every chop/bull arm:

| run | result | gross | SPY gross | SPY % of gross | SPY % of book | **SPY P&L** | modelled exec cost |
|---|---|---|---|---|---|---|---|
| 455506 | +6.02% | $17,273 | $13,269 | 77% | 221% | +$15.18 | $40.07 (0.67pp) |
| 498816 | +15.04% | $16,318 | $12,212 | 75% | 204% | +$18.29 | $37.86 (0.63pp) |
| 264179 | +9.31% | $20,588 | $13,712 | 67% | 229% | +$5.43 | $47.76 (0.80pp) |
| 820236 | +12.33% | $19,948 | $13,585 | 68% | 226% | +$8.76 | $46.28 (0.77pp) |
| 718249 | +4.23% | $21,181 | $11,323 | 53% | 189% | **-$23.61** | $49.14 (0.82pp) |
| 613166 | +9.17% | $20,860 | $13,802 | 66% | 230% | +$28.04 | $48.40 (0.81pp) |
| 725146 | +0.11% (stopped) | $20,209 | $9,557 | 47% | 159% | -$8.70 | $46.88 (0.78pp) |
| 342380 | **+18.71%** (bear) | $18,617 | $3,316 | **18%** | 55% | -$29.56 | $43.19 (0.72pp) |

Execution cost is `gross × 23.2 bps` — the one-way all-in of
`LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL` (`simulated_execution.py:117-121`:
`spread_bps=45.6`, `slippage_bps=0.1`, `fee_bps=0.3`; the half spread is applied
at `simulated_execution.py:542/547` as `max(quote.ask, mid + modeled_half_spread)`).

Two caveats I will not hide:
* **The model charges SPY 22.8 bps of half-spread**, the same as every other
  symbol. SPY's real spread is under 1 bp, so the backtest **overstates** the
  cost of core churn versus live. The $31.52 charged on the SPY lane in 820236
  would be a few dollars in reality.
* Therefore the case against the core lane is **not** the spread. It is that
  ~29% of NAV sat in something that returned -0.23% while the satellite names it
  was crowding returned +28% to +54%, and that the core permanently occupies one
  of six position slots (see §3).

The bear run is the counter-example and it is the good one: `bt 342380` put
$12,657 of gross into SQQQ for **+$791.17**, plus USO +$390.78, and had **zero**
`MAX_POSITIONS_GATE` refusals and **zero** `SATELLITE CAP` lines. When the book
is allowed to hold two things at size, the gates never bind and the objective's
mechanism works.

---

## 3. `max_positions = 6` is the terminal gate

### 3a. The run is at the cap essentially always

Counted from the broker's own per-tick line
(`max_positions gate armed: held=H, cap=C`, `broker.py:14249`):

| run | ticks | ticks at `held >= cap` | SPY held |
|---|---|---|---|
| 820236 | 634 | **599 (94%)** | 612 (97%) |
| 455506 | 634 | 558 (88%) | 612 (97%) |
| 498816 | 634 | 552 (87%) | 612 (97%) |
| 264179 | 634 | 261 (41%) | 612 (97%) |

820236's held set from 2026-01-09 to the end is
`{BA, CPER, LRCX, SNDK, SPY, WDC}` — of the six slots, **SPY is the index core**
and **CPER is a copper ETF from the trend lane**. The alpha book really has
**four** slots. `broker.py:14205-14206` says the same thing in a comment
("with cap=6 the alpha sleeve really only has FIVE").

### 3b. It refused 73% of the buys that cleared everything else

Pairing each `Buy gate inputs for X … → PASS` line with the next 5 lines:

```
buy-gate PASS then MAX_POSITIONS refused : 45 of 62 (73%)
sized cash_to_use that never emitted     : $26,388
```

The 45 refusals are 39 distinct names across 32 of the 40 sessions, all at
`held=6, cap=6`. SNDK — the objective's poster child — was refused on
2026-01-12, 2026-01-13 and 2026-01-19 before it finally entered on 01-20 only
because OMER was fully exited on the same bar:

```
2026-01-20  FILL SELL OMER qty=63.64538935 price=12.072291
2026-01-20  FILL BUY  SNDK qty=0.28781821  price=443.834068     # $127.74 = 2.1% of NAV
```

`broker.py:14230-14233` states the same thing about this exact run: *"bt 820236
is the cost of not doing it: SNDK sized at $873 (14.6% of NAV), overflow-funded,
turnover-bypassed, and refused by `blocked SNDK (held=6, cap=6)` on a line that
simultaneously read `open_pos=5`."*

### 3c. Every other broker lever is inert on the margin — measured

This is the new result, produced by `scripts/simulate_allocation.py` re-running
the real gate chain on each log. Baselines are patched from what each log
*proves* was on (`--fix-config-from-log`), and the replay reproduces 83–89% of
the run's own per-candidate verdicts.

**bt 820236** (baseline fidelity 89.0%, 73 of 82 verdicts reproduced):

| arm | admitted | satellite_skip | turnover | min_pos | **max_positions** | distinct names |
|---|---|---|---|---|---|---|
| BASELINE (= the run) | **13** | 13 | 3 | 4 | **48** | 10 |
| `max_positions=8` | **50** | 14 | 2 | 15 | **0** | 40 |
| `max_positions=10` | 50 | 14 | 2 | 15 | 0 | 40 |
| `max_positions_exclude_sleeve_legs=true` | 45 | 14 | 2 | 15 | 5 | 36 |
| `satellite_conviction_overflow_min_raw_score 1.5→1.2` | **13** | 2 | 3 | 4 | 49 | 10 |
| `core_target_pct 0.35→0.20` | **13** | 0 | 5 | 4 | 49 | 10 |
| `core_min_pct 0.25→0.10` | **13** | 13 | 3 | 4 | 48 | 10 |
| `turnover_budget_monthly_pct 0.5→1.5` | **13** | 13 | 0 | 4 | 51 | 10 |
| **core sleeve OFF entirely** | **13** | 0 | 0 | 4 | 54 | 10 |

Read the last five rows: relaxing the satellite clamp, halving the core target,
dropping the core floor, tripling the turnover budget, and deleting the index
core **all leave the admitted count at 13**. They move refusals *between* gates
and the surplus lands on `max_positions` every time (48 → 49 → 51 → 54).

Same shape on the other three arms of the window:

| run | baseline admitted | `max_positions=8` | `max_positions=10` | any other lever |
|---|---|---|---|---|
| 820236 | 13 | 50 | 50 | 13 (unchanged) |
| 455506 | 18 | 72 | 79 | 18 (unchanged) |
| 498816 | 14 | 69 | 71 | 14 (unchanged) |
| 264179 | 10 | 17 | 17 | 10, except the turnover budget (see below) |

`bt 264179` is the one exception and it is instructive: its binding gate was the
**turnover budget** (56 refusals, 470 `TURNOVER BUDGET BINDING` ticks), because
264179 predates the conviction bypass. Turning the bypass on in the replay moves
264179's binding gate from turnover (56 → 8) onto `max_positions` (12 → 36) —
which is exactly what happened in reality when 820236 shipped the bypass.

**The run series is whack-a-mole.** Each accepted lever moved the choke point one
gate down the chain, and the chain terminates at `max_positions = 6`.

### 3d. …and raising the cap has been measured to LOSE money

This is why the answer is not "set `max_positions=10`". `bt 718249` shipped
`max_positions_exclude_sleeve_legs` (plus two other levers), which is the same
thing by another route: its end book holds **7** names instead of 6. Result
**+4.23%** against **+12.33%**. Its P&L attribution says why:

| name | move | P&L |
|---|---|---|
| AGMI | +31.5% | +$340.22 |
| XOM | +26.9% | +$225.25 |
| ABBV | +3.3% | +$23.47 |
| CLH | +2.7% | +$3.05 |
| AMD / DTE / C | -5.8 / -0.3 / -7.3% | -$10.46 |
| **SPY** | +0.5% | **-$23.61** |
| ETH | -5.0% | -$32.67 |
| MSFT | -7.3% | -$58.06 |
| AMZN | -13.8% | -$104.93 |
| EFX | -16.2% | -$112.26 |

The five extra names beyond the top two contributed **-$307.92**. The marginal
name admitted by loosening the cap was a loser. `docs/OBJECTIVE.txt:80-82`
already lists raising `max_positions` on the do-not-retry list; this is a second,
independent measurement of the same thing.

**Can I verify broker.py:14216-14219's claim that the MPG-blocked basket returned -2.6%
to -9.6% forward? No.** The log carries no price series for a name that was never
held. Of the 39 distinct names 820236's cap refused, **9** can be priced because
another arm of the same window bought them, and their entry-to-end moves were
SNDK +42.3%, GDX +32.8%, KLAC +15.0%, ABBV +3.3%, LLY -1.9%, AMD -5.9%, C -7.3%,
AMZN -13.8%, CRCD -17.0% — mean +5.3%, median -1.9%, 5 negative of 9. That is a
selected sample with different entry dates and it neither confirms nor refutes
the -2.6%..-9.6% figure. Stating it plainly rather than guessing.

### 3e. The funding pre-pass number

Replaying broker.py's own `core_funding_max_positions_aware` pre-pass with the
real `nexus_broker_utils.max_positions_admissible_buys` on 820236:

```
core funding pre-pass  requested $62,672   max_positions-admissible $9,791
                       $52,881 of the requested release was for buys the cap would refuse
```

The pre-pass is doing its job (it fired 9 times in the log, e.g.
`[core] funding pre-pass: max_positions will refuse 3 of 4 sized buy(s) (GDX, SHLS, TXN)`),
and the SPY sawtooth is what is left after it. With `max_positions=8` the
admissible share goes from $9,791 to $61,808 — i.e. the core churn is a
*symptom* of the cap, not an independent problem.

---

## 4. The sell-proceeds credit is inert in backtest — measured, not predicted

`docs/handoffs/2026-08-08-production-readiness-research.md` §4a flags this as its
highest-value finding and asks for the log. Here it is.

Across all eight runs, `Sell-proceeds credit:` appears in **exactly one**:
`bt 498816`, **twice**. Zero in 455506, 264179, 820236, 718249, 613166, 725146,
342380 — including 820236, whose on-disk config backup carries
`backtest_credit_sell_proceeds_enabled: True` and whose log contains **19 SELL
fills**. The flag was not live in that run whatever the config file says.

The one event that could have mattered, 2026-01-16 in bt 498816:

```
[BROKER] Sell-proceeds credit: booked $733.32 expected from CPER sell (cycle total $733.32; ...)
[BROKER] Sell-proceeds credit: sizing ceiling $700.74 → $1397.39 (+95% of $733.32 same-cycle submitted sells)
[BROKER] Buy gate inputs for SNDK: cash=$700.74 reserved=$0.00 floor=$120.00 effective_floor=$0.00
         high_conv=True open_pos=5 cash_per_trade=$755.47 available=$1397.39 cash_to_use=$755.47 → PASS
[BROKER] [execution] FILL SELL CPER qty=20.52381145 price=35.508500 quote=2026-01-16 16:00:00+00:00
[BROKER] [execution] FILL BUY  SNDK qty=1.68975570 price=414.687474 quote=2026-01-16 16:00:00+00:00
```

`1.68975570 × 414.687474 = $700.65`. The broker was approved for **$755.47**; the
fill was **$700.65** = the pre-sell cash. The emulator re-clamped to
`get_buying_power()` (`portfolio_emulator.py:408-420`, enforced in
`execute_signal`). The ceiling was lifted by $696.65 and **the fill grew by $0**.

The second event (2026-02-05, AVY) lifted $485.58 → $569.14 while
`cash_to_use` was only $236.68 — the credit was irrelevant there too.

**Conclusion.** None of 498816's +15.04% is attributable to this lever in
backtest. In LIVE the same bar has no such clamp
(`broker.py:8353-8356` computes `quantity = Decimal(cash_to_use)/Decimal(price)` with no
second cash check), so live would have spent $755.47 where the backtest spent $700.65.
The handoff's directional claim is confirmed and now has a dollar figure:
**$54.82 on that bar, 7.3% of the requested order.**

This case is pinned as a unit test:
`test_sell_proceeds_credit_is_inert_because_the_emulator_reclamps`.

---

## 5. The arms are sampling names, not measuring levers

End books (ex-SPY) of the seven chop/bull arms of the *same* window, instance,
granularity and cash:

```
455506  ABBV CPER LLY  SNDK SYNA
498816  AVY  KLAC NXT  SNDK WDC
264179  CPER GDX  IQM  SNDK TXN
820236  BA   CPER LRCX SNDK WDC
613166  AGMI AMZN EGO  NTR  PLD  SNDK
718249  ABBV AGMI AMD  AMZN CLH  XOM
725146  AGMI CYTK LLY  NVDA WDC
```

24 distinct names; only 7 (`SNDK`, `CPER`, `WDC`, `AGMI`, `ABBV`, `LLY`, `AMZN`)
appear in more than one arm. **Median pairwise Jaccard overlap is 0.11**
(range 0.00–0.25; three pairs share nothing at all).

And in every arm the top two names are the whole result:

| run | top 2 | their P&L | share of the run's P&L |
|---|---|---|---|
| 498816 | WDC + SNDK | $753 | 84% |
| 820236 | WDC + LRCX | $689 | 94% |
| 264179 | GDX + TXN | $442 | 80% |
| 613166 | AGMI + NTR | $520 | 95% |
| 455506 | SNDK + SYNA | $440 | 124% |
| 718249 | AGMI + XOM | $565 | 226% |
| 725146 | AGMI + CYTK | $241 | 410% |
| 342380 | SQQQ + USO | $1,182 | 105% |

A 6pp spread between two arms is two different five-name draws. **This is the
mechanical reason "config-based predictions on this codebase have been wrong
more often than right", and it is the reason `n=1 window` cannot promote a
lever.** It is also why the harness in §6 reports *admissions*, never P&L.

---

## 6. What I built: `scripts/simulate_allocation.py`

A local A/B harness for allocation and gate levers. **Costs zero backtest
credits.** Takes a finished log, reconstructs per-bar state, and re-runs the
broker-side gate chain under any config.

```
python3 scripts/simulate_allocation.py backtests/820236_*.log \
    --config scripts/doc193_backup_patch_20260808T110842Z.json \
    --fix-config-from-log \
    --set max_positions=8
```

### It imports production, it does not copy it

Called directly: `nexus_broker_utils.{max_positions_gate,
max_positions_projected_count, max_positions_admissible_buys,
planned_full_exit_symbols, resolve_max_positions_cap, buy_ceiling}`;
`portfolio_emulator.PortfolioEmulator` (NAV, cash, and the real
`get_buying_power()` re-clamp); and, AST-extracted out of the non-import-safe
`broker.py` by NAME using the pattern from
`backend/tests/test_residual_sleeve.py`:
`_core_sleeve_satellite_headroom`, `_core_turnover_state`,
`_satellite_conviction_min_raw`, `_turnover_cfg_conviction_bypass`,
`_turnover_cfg_bypass_ceiling`, `_turnover_is_governed`,
`_turnover_ledger_record/_rolling`, `_regime_position_cap_hard`,
`_max_positions_excludes_sleeve`, `_residual_sleeve_config/_universe_symbols`,
`_core_sleeve_cfg/_cfg_raw`. Extraction is asserted — a missing helper raises
rather than degrading into a fail-open that still prints a plausible number.

Reached transitively (so they also cannot drift):
`core_sleeve.satellite_design_share`, `satellite_max_share`,
`core_sleeve_config`, `turnover_budget_state`.

Because it binds by name, the parallel edits to broker.py during this session
(16,347 → 16,406 lines) did not break it; the report header prints the broker.py
sha256[:12] that produced it.

### It refuses to A/B against a config that never ran

Two features exist purely because "the config file that looks right usually is
not":

* **CONFIG FACTS THE LOG PROVES** — reads the levers back off the log and
  compares them to the supplied config, with the proving line as evidence. On
  820236 it immediately catches that every on-disk `doc193_backup_*.json` is
  missing `turnover_budget_conviction_bypass_enabled` (the backups are written
  *before* the patch), and that `backtest_credit_sell_proceeds_enabled=True` in
  the file **never fired** against 19 SELL fills. `--fix-config-from-log`
  applies the proven levers.
* **BASELINE FIDELITY** — what fraction of the run's own per-candidate verdicts
  the replay reproduces. 89.0% on 820236 with the right config, 74.4% with the
  wrong one. Below 85% it tells you to fix the config before reading anything
  underneath.
* **`--set` shadowing warning.** doc-193 defines `core_sleeve_enabled`,
  `core_target_pct`, `core_rebalance_band_pct` and `core_rebalance_min_days`
  **only inside `regime_profiles`**, and `_apply_regime_profile` merges the
  matching overlay on top before any gate reads the config. So
  `--set core_target_pct=0.20` changes nothing. The harness detects the
  shadowing and prints the profile-scoped form to use instead. This is the
  easiest way to run a lever test that silently tested nothing — on the harness
  *and* on a real backtest.

### What it CANNOT model (also stated at the top of the file)

1. **P&L.** Admissions and sizes only. A name the log never bought has no price
   series to mark against.
2. **Fill prices, price impact, partial fills, next-event fill timing.** An
   admitted buy is assumed to fill at the price the log recorded for that bar.
3. **Downstream divergence.** One different admission changes the next bar's
   cash, held set, monitor sells, rotations, backfill queue and turnover ledger.
   `--book frozen` (default) scores every tick against the state the run really
   had — pure arithmetic, reproducible. `--book projected` carries a shadow book
   forward and is *wrong after the first divergence*; the header says so.
4. **The whole strategy side.** Discovery, ranking, the LLM overlay, the V31.2
   total-spend cap, the backfill queue, rotations and the per-name sizing that
   produces `cash_per_trade` are replayed as recorded. A key only
   `graph_nexus_analysis.py` reads (`allocation_profile`,
   `total_spend_cap_target_weight_pct`, `nexus_portfolio_pct`, …) will show no
   effect here even though it would change a real run. The report prints exactly
   which config keys it consumed and warns on a `--set` key no replayed gate
   reads.
5. **The core's own decisions.** `core_rebalance_order`, `core_target_weight`
   and `core_sleeve_armed_for_bar` are **not** called; the core's buys and
   releases are replayed verbatim. What is reported is the funding pre-pass
   (§3e). A unit test asserts the docstring does not claim otherwise.
6. **`raw_net_score` for most candidates.** The log prints it only on
   `SATELLITE OVERFLOW` / `TURNOVER BUDGET BYPASS` lines. Everything else falls
   back to the backfill-queue `signal_score` (a different number on a similar
   scale) or None. 13 of 82 candidates on 820236 have no score at all, and every
   score carries its provenance — a score-threshold lever's answer over those is
   a **lower bound**.
7. **Turnover on non-binding ticks.** The log prints
   `TURNOVER BUDGET BINDING: N%` *only* when the budget bound, so on a silent
   tick all we know is `used < the run's budget`. Raising the budget is decided
   exactly; **lowering** it is undecidable there and is reported as
   `turnover_unknown`, never guessed. `--turnover reconstructed` rebuilds the
   ledger through the real broker helpers instead, and the validation block
   prints how far that lands from the log (median 12–19pp on these runs, because
   the ledger books *submitted* notional and the log only shows *fills*).
8. **Sells and live-only gates.** Only the BUY chain is re-evaluated. Price
   sanity, the Alpaca order gate, `ordered_today`, the watchdogs and the three
   flags `live_mode_overrides` flips are live-only and out of scope.
9. **Settlement.** The real `get_buying_power` is used but
   `_unsettled_tranches` is not replayed, so the T+1 5% withhold reads as zero —
   the harness is optimistic about cash by that amount on bars funded by fresh
   sale proceeds.

### Validation built in

Printed on every run: ticks parsed; **held-count mismatches between the
reconstructed book and the broker's own `max_positions gate armed: held=`
line — 0 on all four reference logs**; the candidate-stage census; BUY fill
count; sell-proceeds events; the turnover reconstruction residual; the unscored
count; and the fidelity rate.

### Tests

`backend/tests/test_simulate_allocation.py` — 48 tests, 0.3s, no network, no DB.
They pin (a) that the real production objects are the ones being called, (b) the
log reconstruction against a fragment copied verbatim out of 820236's log,
(c) each gate's behaviour and each inline broker constant ($50 minimum, 15%
single-position cap), (d) the bt 498816 sell-proceeds bar exactly, (e) that the
docstring does not claim a function the module never calls.

```
$ python3 -m pytest backend/tests/test_simulate_allocation.py -q
48 passed in 0.31s
$ python3 -m pytest backend/tests/test_core_sleeve.py backend/tests/test_residual_sleeve.py \
      backend/tests/test_max_positions_gate.py backend/tests/test_backtest_sell_proceeds_credit.py -q
134 passed in 0.19s
```

---

## 7. Ranked list — what to change, expected effect, evidence

Ranked by evidence strength, not by hope. Nothing here is a promotion
recommendation: per `docs/OBJECTIVE.txt:88-97` each needs paired runs on ≥3
windows with isolated `history_scope_salt`.

---

**1. Stop tuning anything below `max_positions`. It is inert.**
*Expected effect:* zero P&L, large saving of credits and calendar.
*Evidence:* §3c. On bt 820236 the admitted count is **13 under every one of**
`satellite_conviction_overflow_min_raw_score 1.5→1.2`, `core_target_pct
0.35→0.20`, `core_min_pct 0.25→0.10`, `turnover_budget_monthly_pct 0.5→1.5`, and
**core sleeve off entirely** — the surplus lands on `MAX_POSITIONS_GATE` every
time (48 → 49 → 51 → 54). Replicated on 455506 and 498816. Reproduce with
`python3 /tmp/ab2.py` style calls to `scripts/simulate_allocation.py`.

**2. Do NOT simply raise `max_positions`. Measured to lose.**
*Expected effect:* +32 to +55 more admitted buys per window, and −8.1pp of return
on the one arm that tried it.
*Evidence:* §3d. `bt 718249` ended with 7 names instead of 6 and returned
**+4.23%** vs 820236's **+12.33%**; the five names beyond its top two contributed
**-$307.92** (EFX -$112, AMZN -$105, MSFT -$58, ETH -$33, C -$8).
`docs/OBJECTIVE.txt:80-82` lists this as already-measured. The harness now makes
the *mechanism* free to check; the *P&L* still needs a run.

**3. Free the two non-alpha slots — the index core and the trend ETF — rather
than widening the cap.**
*Expected effect:* the alpha book goes from 4 slots to 6 with the *same* total
position count, so nothing new is admitted at the margin except by displacing a
non-alpha holding. This is the only version of "more slots" the evidence
supports.
*Evidence:* bt 820236 held `{BA, CPER, LRCX, SNDK, SPY, WDC}` from 2026-01-09 to
the end: SPY is the core (+$8.76 on $13,585 of gross) and CPER is a copper ETF
(+$55.69). SPY is held on **612 of 634 ticks (97%)** and the book is at the cap
on **599 of 634 (94%)**. `broker.py:14204-14222` documents that a one-line
exclusion at the gate is a regression because `_z41_held_now`,
`_count_open_positions` and `_mw_open_set` also count the legs — all four
counters have to move together, which `max_positions_exclude_sleeve_legs` claims
to do. **That flag is exactly what 718249 ran, and it lost.** So the correct
scoped change is narrower: exclude the *core leg only*, not the whole sleeve
universe, and keep the cap at 6 so the total position count does not grow.
The harness measures the mechanism today
(`--set max_positions_exclude_sleeve_legs=true` → 45 admitted vs 13, and cap
refusals 48 → 5); the P&L still needs a paired run.

**4. Size the winner, do not just admit it. SNDK at the day-one clip is worth
+4.2pp on this window, arithmetically.**
*Expected effect:* +$254 on bt 820236 = **+4.24pp** of the $6,000 book, from one
name, with no change to which names are admitted.
*Evidence:* measured prices only. 820236 bought SNDK three times for a total of
**$490.84** (0.9371 shares, average $523.76) and ended at $631.54 → **+$100.95**.
The same first entry (2026-01-20, $443.83) at the **$840 clip WDC and LRCX got on
day one** is 1.8926 shares → $1,195.26 → **+$355.26**. Delta **+$254.31**. The
15% broker single-position cap (`broker.py:15213`, `BROKER_MAX_SINGLE_POSITION_PCT` default 0.15 → $900 at NAV 6,000) does not
bind on $840. This is pure arithmetic on recorded prices — it is not a claim that
the allocator would have sized it there.

**5. Trim-back, not entry-only caps — the objective's blocker #3, now with a
measured cost.**
*Expected effect:* lets a better name displace a mediocre one inside a fixed cap,
which is the only way #3 and #4 can both happen at cap 6.
*Evidence:* on 2026-01-02 the satellite clamp trimmed SYNA and TXN from $839 to
**$290** and the cap then refused them outright
(`[core] funding pre-pass: max_positions will refuse 2 of 4 sized buy(s) (SYNA, TXN)`).
In `bt 264179`, run on the same window, SYNA returned **+12.5% (+$111.12)** and
TXN **+19.8% (+$166.51)**. Meanwhile 820236's slots were held by CORD
(**-$59.43**) and OMER (**-$60.99**) until they were stopped out. A cap that
cannot evict a loser to admit a winner is the binding structure, and
`test_A11` already fails on purpose for exactly this.

**6. Treat single-window P&L deltas under ~6pp as noise.**
*Expected effect:* stops promoting levers on a name draw.
*Evidence:* §5. Seven arms of the identical window/instance/cash hold books with
**median pairwise Jaccard 0.11**; 24 distinct names, 7 shared. Top-2 names are
80–226% of each arm's P&L. The 820236-vs-613166 gap (+12.33% vs +9.17%) is WDC
+53.6% / LRCX +28.4% versus AGMI +31.5% / NTR +21.2% — different draws, not a
lever.

**7. Turn `backtest_credit_sell_proceeds_enabled` off, or fix the emulator side
before claiming anything from it.**
*Expected effect:* removes a lever that adds $0 in backtest while widening the
live-vs-backtest gap.
*Evidence:* §4. One firing run of eight; on its only material event the ceiling
rose $700.74 → $1,397.39, the broker sized $755.47 and the emulator filled
**$700.65**. Live has no equivalent clamp (`broker.py:8353-8356`), so the flag
makes live spend **more** than the backtest that justified it — the wrong
direction for reproducibility. Pinned as a unit test.

**8. Do not spend another credit on the SPY core lane's *cost*; it is a
modelling artefact.**
*Expected effect:* avoids optimising a $31 line item.
*Evidence:* the emulator charges every symbol the same 22.8 bps half-spread
(`simulated_execution.py:542/547`), so 820236's $13,585 of SPY gross is charged
$31.52 where the real spread is under 1 bp. The core lane's problem is the
**slot** and the **opportunity cost of ~29% of NAV at -0.23%**, not the spread.

---

## Things I could not prove

* **The forward return of the `MAX_POSITIONS_GATE`-blocked basket.**
  `broker.py:14216-14219` claims -2.6% to -9.6%. The log carries no price series for a
  name that was never held. 9 of 39 blocked names are priceable from other arms;
  their entry-to-end moves are mean +5.3%, median -1.9%, 5 negative of 9 — a
  selected, differently-dated sample that settles nothing.
* **Which doc-193 revision each run used.** The `doc193_backup_*.json` files are
  written *before* each patch, so none of them is any run's config. I derived the
  levers from the logs instead (§6), and the fidelity number is the check.
* **`bt 725146`'s stated NEGATIVE result.** The API reports `pnl_percent
  0.1121`, status `stopped`. It is the worst chop/bull arm either way.
* **Anything about P&L under a changed config.** By construction. The harness
  reports admissions.


---

## Appendix — reproducing every number in this document

All of these cost zero backtest credits.

```bash
# pull any run's log (free)
python3 scripts/pull_backtest_logs.py 820236 --out backtests/820236.log --no-meta

# P&L attribution and gross-notional tables (sections 1, 2, 5)
python3 scripts/pull_backtest_logs.py 820236 --filter 'FILL (BUY|SELL)|Monitor decision:' --stdout

# the gate census and the refusal chain (section 3)
python3 scripts/pull_backtest_logs.py 820236 \
    --filter 'MAX_POSITIONS_GATE|SATELLITE (CAP|OVERFLOW)|TURNOVER BUDGET|Buy gate inputs|max_positions gate armed' --stdout

# the sell-proceeds credit (section 4) — 1 hit in 498816, 0 everywhere else
for id in 455506 498816 264179 820236 718249 613166 725146 342380; do
  echo -n "$id "; python3 scripts/pull_backtest_logs.py $id --filter 'Sell-proceeds credit' --stdout | grep -c . ; done

# the A/B table in section 3c
python3 scripts/simulate_allocation.py backtests/820236.log \
    --config scripts/doc193_backup_patch_20260808T110842Z.json --fix-config-from-log \
    --set max_positions=8
# NOTE: doc-193 defines core_target_pct ONLY inside regime_profiles, so a
# base-level --set is SHADOWED by the overlay. The harness prints a
# "--set keys SHADOWED by a regime_profiles overlay" warning; scope it to the
# profile instead:
python3 scripts/simulate_allocation.py backtests/820236.log \
    --config scripts/doc193_backup_patch_20260808T110842Z.json --fix-config-from-log \
    --set 'regime_profiles={"bull":{"core_sleeve_enabled":true,"core_target_pct":0.20},"chop":{"core_sleeve_enabled":true,"core_target_pct":0.20}}'
    # -> satellite_skip 13 -> 0, admitted UNCHANGED at 13
python3 scripts/simulate_allocation.py backtests/820236.log \
    --config scripts/doc193_backup_patch_20260808T110842Z.json --fix-config-from-log \
    --set turnover_budget_monthly_pct=1.5 # -> admitted unchanged at 13

# tests
python3 -m pytest backend/tests/test_simulate_allocation.py -q      # 48 passed
```

Logs used, all present under `backtests/`:
`820236_20260808-142050Z.log`, `498816_20260808-024027Z.log`,
`264179_20260808-124656Z.log`, `455506_20260807-192732Z.log`,
`342380_localsim.log`; `718249`, `613166`, `725146` were read through
`pull_backtest_logs.py --stdout --filter` without writing a file.
