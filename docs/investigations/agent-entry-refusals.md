# Why high-conviction names are NOT bought — bt 523085 (W0 control, +6.00%)

**Scope.** Aspect assigned: *entry refusals*. Evidence base is exactly two things:
`/tmp/bt523085.log` (40,344 lines, 2026-01-01 -> 2026-02-28, 51 sessions, 634 broker ticks)
and source read under `backend/`. **No backtest was run.** Line numbers of the form
`L<n>` are 1-based line numbers in `/tmp/bt523085.log`; `file.py:<n>` are source lines.

Run outcome for reference (log tail): `Updated backtest results in database
(id=523085, status=finished, P&L=360.2209302016072)` on a $6,000 book = **+6.00%**;
`Total Trades: 32 / Buys: 16 / Sells: 16`.

---

## 1. Headline

**121 buy decisions reached the broker execution loop. 11 were executed. 110 (90.9%) were refused.**

* 121 = count of `[BROKER] <SYM> @ <ts> ($px): buy action_intent=...` lines
  (`initial_buy` 53, `backfill_queue_buy` 49, `momentum_watchlist_buy` 15,
  `winner_add_buy` 3, `momentum_watchlist_rotation` 1).
* 78 of those reached the `Buy gate inputs for ...` diagnostic; **all 78 printed `-> PASS`**;
  **67 of the 78 were then refused** by `SKIP BUY`. Only **11** survived.
* The remaining 43 decisions never reached the gate line: 36 killed by `SATELLITE CAP ... skipped`,
  7 by `TURNOVER BUDGET BLOCK`.
* Reconciliation is exact: `121 - (36 + 67 + 7) = 11`.
* The 5 remaining "Buys" in the summary are the SPY index-core legs
  (`[core] bought $2400.00 SPY @ 681.82 ...`, L1918 and 4 later).

## 2. Complete refusal ledger, ranked by blocked notional

"Blocked notional" = the dollar clip the allocator had already sized for that name
(`cash_per_trade` / `allocated=$N` / `SYM@$N`) at the moment of refusal. It is a flow
over 51 sessions on a $6,000 book, not a stock.

| # | Refusal | Events | Blocked notional | Implemented at |
|---|---------|-------:|-----------------:|----------------|
| A | `SATELLITE CAP: X trimmed $A -> $B` **then** `SKIP BUY X — ... < min $F` | 44 | **$38,493** (pre-trim intent); $8,758 post-trim | trim `broker.py:15954-15958`; refusal `broker.py:16515` -> `broker.py:16612` |
| B | `SATELLITE CAP: X skipped — satellite at its design share ($-N room)` | 36 | **$28,923** (33 of 36 matched to a same-day sized clip; 3 unmatched) | `broker.py:15903-15918`, headroom `broker.py:3434-3525` |
| C | `SKIP BUY X — ... < min $F` with **no** preceding trim (cash / fundable clamp) | 23 | **$20,252** | `broker.py:16515` -> `broker.py:16612`, clamp `broker.py:3754-3798` (`_exec_fundable_amount`) |
| D | `TURNOVER BUDGET BLOCK: X skipped` | 7 | **$5,933** | `broker.py:16126-16132` |
|   | **Total refused** | **110** | **$93,601** | |

Aggregates behind the table:
* A: 44 `SATELLITE CAP ... trimmed` lines are each followed within 25 lines by a `SKIP BUY`
  of the same symbol, and in **44/44** the trim's post value equals the `SKIP BUY`'s
  `(allocated $N)` to the dollar. Total trim across all 47 trim lines:
  $41,066 -> $9,893, i.e. **$31,173 of sizing destroyed**.
* B: all 36 rooms are negative; min $-1,426, median $-1,339, max $-306.
* C: 16 of the 23 print the `fundable $A of cash_to_use $B` variant.
* D: allocs 840+840+840+840+839+864+870 = $5,933.

### 2b. Single largest blocker

**By proximate log line:** `SKIP BUY ... < min $F` — 67 events, $29,010 of `allocated`.
Implemented by `_exec_min_position_gate` (`backend/broker.py:3825-3874`, called at
`broker.py:16515`, logged at **`backend/broker.py:16612`**).

**By root cause, which is the actionable answer:** the **satellite-headroom cap**,
`_core_sleeve_satellite_headroom` (`backend/broker.py:3434`, applied at
**`backend/broker.py:15903-15958`**). It is upstream of A (44) and is itself B (36):
**80 of 110 refusals (72.7%) and $67,416 of the $93,601 blocked notional (72.0%)**.
The pure cash/floor lane (C) is $20,252 and the turnover budget (D) is $5,933.

---

## 3. Defect 1 (largest): the satellite cap trims a buy *below* the floor that then refuses it

`broker.py:15954-15958` trims `cash_per_trade` down to satellite headroom:

```
if cash_per_trade > _sat_room:
    _log(f"SATELLITE CAP: {symbol} trimmed ...")
    cash_per_trade = _sat_room
```

~650 lines later, `broker.py:16515` calls `_exec_min_position_gate`, whose floor is
`max($50, nav * min_position_nav_pct)` (`broker.py:3732-3751`). In this run that floor
ran **$360-$383** (i.e. `min_position_nav_pct ≈ 0.06` on a ~$6.0-6.4k NAV), while trims
landed at **$191-$423**. The trim is not clamped to the floor and the floor is not
consulted before trimming, so the trim *manufactures* the refusal.

Evidence (44 identical shapes; three verbatim):

```
L6152  SATELLITE CAP: GBDC trimmed $929 -> $213 to keep the core at target
L6155  SKIP BUY GBDC — cash_to_use $90.20 < min $372 (allocated $212.63)

L9909  SATELLITE CAP: LLY trimmed $871 -> $212 to keep the core at target
L9912  SKIP BUY LLY — cash_to_use $212.42 < min $373 (allocated $212.42)

L11767 SATELLITE CAP: SNDK trimmed $890 -> $191 to keep the core at target
L11770 SKIP BUY SNDK — cash_to_use $191.40 < min $382 (allocated $191.40)
```

In **44 of 67** `SKIP BUY`s the `allocated` clip is itself below the printed floor
($8,758 of post-trim notional). The money is neither spent on the name nor released:
the strategy's `P3 undersized guard` (`strategies/graph_nexus_analysis.py:32901-32922`),
which exists precisely to drop sub-floor new entries, **fired 0 times** in this log —
because it runs on the allocator's pre-trim $840-$930 clips, which are above the floor.
The broker then shrinks them below it. `broker.py:3735` claims the two ends use
"the same number... so the two ends cannot admit what the other refuses"; the trim at
15958 falsifies that claim.

**Proposed fix (`backend/broker.py:15954`).** Make the trim floor-aware and fail loudly:

```python
_mp_floor = _exec_min_position_floor(
    _core_sleeve_cfg_raw(_cached_strategies),
    float(portfolio_emulator.get_portfolio_value(prices) or 0.0),
)
if cash_per_trade > _sat_room:
    if _sat_room + 1e-9 < _mp_floor and not _emp_held_check(symbol, portfolio_emulator):
        _log(f"SATELLITE CAP: {symbol} refused — room ${_sat_room:,.0f} is below the "
             f"${_mp_floor:.0f} execution floor; trimming would only be refused later",
             "yellow")
        continue                      # one refusal, one reason, no phantom order
    _log(f"SATELLITE CAP: {symbol} trimmed ...")
    cash_per_trade = _sat_room
```

That removes the duplicate refusal path. It does **not** by itself buy the name — see
Defect 2 for why the headroom is negative in the first place.

## 4. Defect 2: satellite headroom is born negative and never recovers

`_core_sleeve_satellite_headroom` returns `(share * nav) - satellite`
(`broker.py:3522-3525`). All 36 `SATELLITE CAP ... skipped` lines report a **negative**
room (median $-1,339). The code's own comment at `broker.py:3513-3521` predicts exactly
this: the cap's arming gate `_core_sleeve_cfg` is base-flag gated, so on tick 1 it
returns `None` and nothing bounds the opening basket.

The log confirms tick 1 was uncapped: at L1678-1687, `cash=$6000.00`,
`cash_per_trade=$840.00`, four names admitted (`ROKU`, `RTX`, `SBLK`, `VICR`), and the
strategy line `V32.1 total-spend cap [CONCENTRATE]: funded 4 of 8 by conviction
(VICR@$840, SBLK@$840, ROKU@$840, RTX@$840) out of $3,780`. That is $3,360 = 56.0% of
NAV committed before any cap existed. Every subsequent tick then reads
`satellite > share * nav`, which is why the first `SATELLITE CAP` line appears on
2026-01-02 (L3383) and the room is negative from 2026-01-07 (L6159, $-1,335) onward.

**Proposed fix (`backend/broker.py:3475`).** Arm the cap on the same regime-aware
predicate the allocator already uses, so tick 1 is capped too:

```python
from core_sleeve import core_sleeve_armed_for_bar
_cfg_raw = _core_sleeve_cfg_raw(cached_strategies)
_regime = ((_strategy_cache.get("graph_nexus_analysis") or {}).get("_market_regime"))
if not core_sleeve_armed_for_bar(_cfg_raw, regime=_regime):
    return None
```

`core_sleeve_armed_for_bar` / `satellite_design_share` are already the shared source of
truth used at `strategies/graph_nexus_analysis.py:32690-32692`; this makes the broker cap
read the same predicate instead of `_core_sleeve_cfg(...) is None`.

## 5. Defect 3: the buy gate's PASS/SKIP verdict is computed against the wrong floor

`broker.py:16456` hardcodes the preview floor:

```python
_exec_min_pos_preview = 50.0
_will_skip = cash_to_use < _exec_min_pos_preview and cash_to_use < cash_per_trade
```

The real gate 59 lines later uses `_exec_min_position_gate`, floor $360-$383 here, and
measures against `fundable`, not `cash_to_use`. Consequence in this log: **78 of 78**
`Buy gate inputs` lines printed `-> PASS`, and **67 of them (85.9%) were refused on the
very next line.** The diagnostic also prints `floor=$120.00 effective_floor=$120.00`,
which is the *cash-reserve* floor, not the min-position floor, and never prints
`_exec_min_pos` or `fundable`. A reader grepping `-> PASS` concludes the buy happened.

**Proposed fix (`backend/broker.py:16453-16472`).** Move the diagnostic *below* the
`_exec_min_position_gate` call and print its real outputs:

```python
(_emp_skip, _exec_min_pos, _emp_fundable, _emp_held) = _exec_min_position_gate(...)
_log(f"Buy gate inputs for {symbol}: cash=${_cash_now:.2f} ... "
     f"cash_to_use=${cash_to_use:.2f} fundable=${_emp_fundable:.2f} "
     f"min_pos=${_exec_min_pos:.0f} held={_emp_held} "
     f"-> {'SKIP' if _emp_skip else 'PASS'}", ...)
```

One call, one verdict, no second opinion.

## 6. Defect 4: `SKIP BUY` misattributes the funding shortfall

`broker.py:16541-16543` hardcodes the attribution string:

```python
f"fundable ${_emp_fundable:.2f} of cash_to_use ${cash_to_use:.2f} "
f"(orders already in flight this tick reserve the rest)"
```

But `_exec_fundable_amount` (`broker.py:3782-3798`) subtracts **two** different things:
`sum(_execution_cash_reservations.values())` *and*, inside
`PortfolioEmulator.get_buying_power` (`portfolio_emulator.py:476`),
`self._withheld_cash()` — the unsettled slice of prior sells.

The 2026-01-19 tick proves the message is wrong. Tick boundary is L13621
(`[Pending] After strategies (2026-01-19)`); the tick's gate arms at L13624.
AMZN at L13631-L13632 is the **first** buy of the tick:

```
L13624  max_positions gate armed: held=5, cap=14
L13625  [core] funding request trimmed $3,540 -> $1,275 — satellite headroom will refuse the remainder...
L13631  Buy gate inputs for AMZN: cash=$1299.21 reserved=$0.00 ... cash_to_use=$885.03 -> PASS
L13632  SKIP BUY AMZN — fundable $133.49 of cash_to_use $885.03 (orders already in flight this tick reserve the rest) < min $379 (allocated $885.03)
```

`reserved=$0.00`, no prior buy on the tick, yet $1,165.72 of the $1,299.21 is unavailable.
SKYT (L13642) and SNDK read the *identical* $133.49, so it is not sequential consumption
by in-tick buys either. The shortfall is unsettled/pending sell proceeds, and the
same-tick core release at L13625 is a next-event SELL whose proceeds are not credited
unless `backtest_credit_pending_sell_proceeds` is set
(`portfolio_emulator.py:464-478`, `broker.py:14998-15000`).

**Proposed fix (`backend/broker.py:16537-16543`).** Return the components from
`_exec_fundable_amount` and name them:

```python
f"fundable ${_emp_fundable:.2f} of cash_to_use ${cash_to_use:.2f} "
f"(in-flight buy reservations ${_resv:.2f}, unsettled sell proceeds ${_withheld:.2f})"
```

This is 23 of the 67 refusals ($20,252) and it is currently unattributable from the log.

## 7. Defect 5: `TURNOVER BUDGET BINDING` is 611 lines of noise that refused 7 orders

Do **not** chase this one. It is by far the loudest signal in the log and nearly the
smallest blocker.

* `TURNOVER BUDGET BINDING` fires on **611 of 634 ticks (96.4%)**, on **49 of 51 sessions**
  — every session except 2026-02-27 and 2026-02-28.
* But `TURNOVER BUDGET BLOCK` (the line emitted when an actual order is refused,
  `broker.py:16126-16132`) appears **7 times**, and
  `TURNOVER BUDGET BYPASS` (`broker.py:16066-16074`) appears **71 times**. The conviction
  bypass (`raw >= 1.50`) admits ~91% of what the budget flags.
* `Gate skips reported back` breakdown: `insufficient_cash` 67, `turnover_budget` 7.

That said, there **is** a real arithmetic defect underneath, and it is the partial-window
trap. `turnover_budget_state` (`core_sleeve.py:718-722`) computes
`used = rolling_notional / nav` and compares it to a **monthly (21-session)** allowance,
with no scaling for how much of the window has actually elapsed.
`_turnover_ledger_touch` (`broker.py:3095-3133`) creates one bucket per session, so on the
first session the ledger contains exactly one bucket.

Evidence: the budget binds on the **very first session**, at L1915,
`date=2026-01-01`, with `max_positions gate armed: held=0, cap=6` on the line
immediately above (L1914) and the SPY core buy still one line *below* (L1918):

```
L1914  max_positions gate armed: held=0, cap=6
L1915  TURNOVER BUDGET BINDING: 56% of NAV in accepted-order request notional over the last 21 sessions
```

56% is not a rolling-month number. It is that morning's four opens:
`4 x $840.00 = $3,360 / $6,000 = 56.000%` exactly (L1678-1687). One day of a cold-start
deployment consumed the whole 21-session allowance. (The $2,400 SPY core buy is correctly
excluded — `_turnover_is_governed`, `broker.py:3155-3210` — which is why the figure is
exactly 56.00% and not 96%.)

**Proposed fix (`backend/core_sleeve.py:708-722`).** Pro-rate the allowance by elapsed
window, and take the number of populated sessions from the caller:

```python
def turnover_budget_state(cfg, *, rolling_notional, nav, sessions_elapsed=None,
                          window_sessions=21):
    ...
    used = max(0.0, float(rolling_notional or 0.0)) / nav
    budget = cfg.turnover_budget_monthly_pct
    if sessions_elapsed is not None and 0 < sessions_elapsed < window_sessions:
        # a 1-session-old ledger may not be judged against a 21-session allowance
        budget *= max(1, int(sessions_elapsed)) / float(window_sessions)
        budget = max(budget, cfg.turnover_budget_monthly_pct / float(window_sessions))
    return used >= budget, used
```

with `broker.py:4056` / `4065` passing `sessions_elapsed=len(_turnover_ledger_touch(current_time))`.
Note this makes the budget *tighter* on day 1, not looser — the honest fix for a cold
start is a separate ramp-in allowance, not a full-window allowance charged on day 1.

## 8. Defect 6: the displacement escape hatch is unreachable and mislabelled in the config echo

`broker.py:16545` guards the whole displacement lane on
`_displacement_enabled(_cached_strategies)`, which reads
`satellite_displacement_enabled` (`broker.py:3298-3304`).

* Grep of `backend/` outside `tests/`: `satellite_displacement_enabled` has exactly
  **one** reader (`broker.py:3302`) and **zero** writers.
* The log contains **0** lines matching `DISPLACEMENT` — not `DISPLACEMENT: trimming`,
  not `DISPLACEMENT: no candidate`, not `DISPLACEMENT ERROR` — across all 67 `SKIP BUY`
  events, so the branch never executed once.
* The run's own config echo is actively misleading: `Effective config | ... displace=0.3/rec`
  (L18) is `backfill_queue_min_displacement_delta` /
  `backfill_queue_recurrence_bonus` (`strategies/graph_nexus_analysis.py:25951`), an
  unrelated backfill-queue knob. Reading that line, an operator would reasonably believe
  displacement is on. It is not.

**Proposed fix.** Two lines. (a) In `broker.py:16545`, log once per run when the lane is
disabled, the same "an inert lever must announce itself" contract the surrounding code
already states at `broker.py:16597`. (b) Rename the echo field at
`strategies/graph_nexus_analysis.py:25951` from `displace=` to `bfq_displace_delta=`
so it cannot be read as the broker lever.

## 9. Upstream (strategy-side) suppressions — counted, not blamed

These fire before the broker and have **no dollar clip attached**, so I cannot state a
blocked notional for them. Counts only:

| Suppression | Events | Distinct tickers | Source |
|---|---:|---:|---|
| `Entry extension gate: X recent runup +N% > 25% — buy blocked` | 123 | 75 | strategies/graph_nexus_analysis.py |
| `Momentum ceiling block: X` | 139 | 17 | strategies/graph_nexus_analysis.py |
| `Backfill queue BLOCKED (full_priority_blocked)` | 284 | — | strategies/graph_nexus_analysis.py |
| `Backfill queue BLOCKED (full_general_blocked)` | 2 | — | " |
| `V32 mw_buy extension-block` | 46 | — | " |
| `Price floor: blocked N sub-floor buy(s)` | 33 | — | " |
| `ANCHOR ADD: none funded from N candidate(s)` | 38 of 41 | — | " |
| `ETF budget too low ... skipping all N ETF buys` | 8 | — | " |
| `Stock overlay: limiting to top 30` | 43 (4,680 names skipped) | — | " |
| `CONCENTRATE: funded 118 of 143; dropped 25 to the queue` | 43 | — | graph_nexus_analysis.py:32728+ |
| `Backfill budget reserve: holding $N (10-20%)` | 43 ($8,339 withheld) | — | " |
| `Macro risk scaling ... buy budget $A -> $B` | 14 ($177 total cut) | — | " |
| `[core] funding request trimmed` | 41 ($97,138 requested -> $22,697 granted) | — | broker.py |
| `hold action_intent=deferred_unfunded_buy` | 106 | — | " |

The relevant comparison is that the strategy **did** size 118 fundable new-entry clips
(CONCENTRATE, 43 ticks) at ~$840-$956 each. The bottleneck is downstream of sizing.

## 10. Case study: SNDK (+166.10% over the window, captured +2.28%)

`SNDK: $237.33 -> $631.54 (+166.10%)` (log tail). `SNDK: P&L = $18.84 (+2.28%)`.
It was refused **nine** times before it got in on 2026-02-04:

| Date | Refusal | Line |
|---|---|---|
| 01-12 | trim $860 -> $221, then `SKIP BUY ... $212.59 < min $368` | L8991/L8994 |
| 01-13 | trim $871 -> $212, then `SKIP BUY ... $212.42 < min $373` | L9917/L9920 |
| 01-14 | trim $871 -> $211, then `SKIP BUY ... $210.94 < min $373` | L10835/L10838 |
| 01-15 | trim $890 -> $191, then `SKIP BUY ... $191.40 < min $382` | L11767/L11770 |
| 01-16 | `SATELLITE CAP: SNDK skipped — ... ($-1,393 room)` | L12717 |
| 01-19 | `SKIP BUY ... fundable $133.49 of cash_to_use $885.03 < min $379` | L13649 |
| 01-21 | `SKIP BUY ... cash_to_use $146.68 < min $377 (allocated $879.44)` | L15477 |
| 01-29 | trim $891 -> $256, then `SKIP BUY ... fundable $252.84 of cash_to_use $253.10 < min $382` | L21045/L21048 |
| 02-03 | `SKIP BUY ... fundable $99.20 of cash_to_use $874.30 < min $375` | L23829 |
| 02-04 | **executed** (`Buy gate inputs for SNDK ... cash_per_trade=$863.82`, no SKIP) | L24721 |

Every one of those ticks carries `TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50` —
the turnover budget *let it through* each time; the satellite cap and the min-position
floor are what refused it.

Meanwhile the capital the cap was protecting, `SPY`, returned
`SPY: $681.82 -> $686.16 (+0.64%)` and booked `SPY: P&L = $-3.63 (-0.06%)`.

## 11. What I could NOT establish from this evidence

* **The configured `turnover_budget_monthly_pct` value.** No config JSON for run 523085
  exists under `backtests/`, and the threshold is never printed. The lowest observed
  *binding* value is 54% (2026-01-05, 2026-01-06), so the threshold is <= 0.54; the
  design target documented at `core_sleeve.py:712` and `broker.py:4027` is 0.50. I did
  not verify 0.50 and do not assert it.
* **Why the budget stopped binding on 2026-02-27/28** after reading 65-66% on 02-26.
  The check ran (15 `max_positions gate armed` lines that day, 0 BINDING lines), so it
  returned not-blocked, but the log never prints `used` when it does not bind.
* **The exact `_withheld_cash()` vs `_execution_cash_reservations` split** for the 23
  Group-C refusals. Only the net `fundable` is logged; the components are not.
* **Blocked notional for 3 of the 36 `SATELLITE CAP ... skipped` events** (VICR 01-09 and
  two others): no same-day sized clip appears in the log for those symbols. The $28,923
  figure covers 33 of 36 and is therefore an *under*-count.
* **Whether the 110 refused buys would have been profitable.** Out of scope; the log
  records refusals, not counterfactual fills. SNDK and VICR are suggestive, not proof.

### Trap checks I ran deliberately

* **Empty reason strings.** `broker.py:15803-15804` logs
  `SKIP BUY {symbol} - {_nexus_block_reason}` only when the reason is truthy; an empty
  `_nexus_block_reason` means the nexus buy guard *was evaluated and did not block*. I
  searched for `reason=''`/`reason=""`/trailing-empty `reason=` and found **0** such
  lines, and only **1** nexus-execution-gate refusal in the whole run (`NUVB`, inside the
  CONCENTRATE line at L10733). So this lane is genuinely near-inert here — I am not
  reading its silence as "not evaluated".
* **Full-window vs partial-window.** The 56% at L1914 is a *one-session* accumulation
  compared against a *21-session* allowance. I did not compare it to any full-window
  series; the defect is precisely that the code does. See section 7.
