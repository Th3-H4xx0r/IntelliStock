# SATELLITE CAP `continue` — what bookkeeping is lost

**Aspect:** `backend/broker.py` per-symbol execution loop. What side effects are skipped when
the SATELLITE CAP branch `continue`s instead of falling through to the end of the loop body.

**Method / constraints honoured**
- No backtest was run. No `scripts/run_validation_backtest.py`. Nothing pushed.
- Source read: `backend/broker.py`, `backend/strategies/graph_nexus_analysis.py`.
- Logs read (already on disk, not produced by me): `/tmp/bt_fb2_full.log` (primary, 48,580 lines),
  cross-checked against `/tmp/bt102463.log`, `/tmp/bt523085.log`, `/tmp/bt778288_full.log`.
- No claim below rests on an empty `reason` string being read as "not evaluated".
- All counts are whole-run counts from a single log compared against other whole-run counts from
  the *same* log. No full-window number is compared against a partial-window series.

---

## 1. The exact code sites

The enclosing loop is `for symbol in _exec_order:` at **broker.py:15476**, whose body ends at
**broker.py:17150** (line 17151 `###...` returns to the loop's own indent of 12). Every `continue`
inside it therefore discards **lines <site>..17150** for that symbol on that bar.

There are two SATELLITE CAP exits, both at indent 36 inside `if decision == 1:` (15835):

| Site | Condition | Statements executed before `continue` |
|---|---|---|
| **15907–15918** ("skipped") | `_sat_room <= _CORE_MIN_SATELLITE_TRIM_USD` (`= 25.0`, broker.py:3255) | `_log(...)` 15908–15912; `_anchor_reinforcement_block(...)` 15913–15917 *only if* `_anchor_policy`; `continue` **15918** |
| **15959–15968** (anchor min-fill after trim) | `_anchor_policy and cash_per_trade < _anchor_policy["min_fill"]` | `_anchor_reinforcement_block(...)` 15964–15967; `continue` **15968**. **No `_log` of its own.** |

The third outcome — `cash_per_trade > _sat_room` at **15954–15958** — *trims* `cash_per_trade` and
**falls through**. That is the control case used throughout this document.

Compare with the sibling gate 100 lines above (the "correct skip" template),
**15804–15827**: `_log` → `_trade_skipped_no_price = True` (15806) →
`_broker_skipped_buys.append({...})` (15810–15820) → `_log("Gate skips reported back...")` (15821)
→ `_anchor_reinforcement_block` (15822–15826) → `continue` (15827).
The SATELLITE CAP sites do **two** of those five things.

Generalising: the loop body contains **16** `continue` statements (15801, 15827, **15918**,
**15968**, 15988, 15996, 16113, 16155, 16189, 16366, 16536, 16636, 16699, 16782, 16802, 16823).
Only **3** of them (15827, 16155, 16189) are preceded by a `_broker_skipped_buys` report. The
SATELLITE CAP pair is the highest-frequency member of the unreported 13 in every log examined.

---

## 2. Complete inventory of what the `continue` discards (15919 → 17150)

Ordered by line. "Harmful" = the loss changes what the strategy does or destroys a record nothing
else reproduces. "Harmless" = the side effect is conditioned on an event (an accepted order, a sell)
that a refused buy never produces, so not doing it is correct.

### 2.1 Gates that are simply not evaluated (not bookkeeping, listed for completeness)

| Line | Thing | Verdict |
|---|---|---|
| 15977–15988 | `_fundamental_veto_blocks` | Harmless — a name already refused does not need a second refusal. |
| 15993–15996 | reserved-sleeve bear-symbol guard | Harmless, same reason. |
| 16192–16294 | cash-reserve floor / buying-power sizing | Harmless. |
| 16300–16365 | `BROKER_MAX_SINGLE_POSITION_PCT` cap | Harmless. |
| 16374–16452 | live price-sanity check | Harmless (live-only). |
| 16515–16518 | `_exec_min_position_gate` | Harmless as a *gate*, but see 2.3 — its body is where displacement lives. |
| 16716–16789 | split guard | Harmless (sell-side). |
| 16797–16802 | `max_positions_gate` | Harmless — a buy that will not be submitted cannot consume a slot. |
| 16817–16823 | `_min_hold_blocks_sell` | N/A (sell-side). |

### 2.2 Skipped-buy reports — **LOST, HARMFUL**

Three `_broker_skipped_buys` append sites are downstream of the `continue`:

- **16133–16146** `reason="turnover_budget"` (inside `if _turnover_blocked and not _tb_bypass:` 16126, `continue` 16155)
- **16175–16188** `reason="regime_cap"` (inside the `_regime_position_cap_hard` branch 16156–16189)
- **16615–16628** `reason="insufficient_cash"` (inside `if _emp_skip:` 16537)

`_broker_skipped_buys` has **two consumers**, both in `backend/strategies/graph_nexus_analysis.py`:

1. **graph_nexus_analysis.py:28253–28269** — "V7.3: Also ensure broker-skipped tickers get scored
   for this bar". Names in the list are appended to `symbols_list`. A name that never lands in the
   list can drop out of the next bar's scoring universe.
2. **graph_nexus_analysis.py:~31878–31905** — `_enqueue_backfill_candidate(...)` with
   `"broker_skip_reason": str(_bsk.get("reason") or "")`, logged as
   `Backfill queue ADD (broker-skipped): ...`. Cleared at **31907**.

So the backfill queue — the codebase's own retry mechanism for a buy the broker refused
(comment at broker.py:16614: *"V7.1: Report skipped buys to strategy cache for backfill queue"*) —
**never learns that a satellite-cap refusal happened.** The name is dropped, silently, for good.

**Verdict: HARMFUL.** This is the single largest loss.

### 2.3 Displacement request queue — **LOST, HARMFUL (currently unmeasurable)**

`_broker_displacement_requests` is appended at **broker.py:16604–16611**, and that append sits
inside `if _emp_skip:` (**16537**) → `if _displacement_enabled(...)` (**16545**) → `if _disp:`
(**16599**). All of it is downstream of the `continue`.

Structurally this is backwards: the satellite hard-skip fires precisely when the satellite has
**negative** headroom (log shows `$-1,287 room`, `$-1,370 room`), i.e. the book is already over its
design share and the only remedy is to free room by selling something. Displacement is the
mechanism built to do exactly that (`_displacement_candidate`, broker.py:3307) — and it is
unreachable from the one refusal that most needs it.

**Verdict: HARMFUL in design.** Honest caveat: I cannot measure the P&L cost, and a prior
investigation in this repo recorded that the displacement arm never actually trades because
`_sell_first` intersects the request set with `expanded_symbols`. So today the loss is *latent*,
not realised. I am not claiming a measured dollar impact.

### 2.4 Turnover ledger — **NOT lost in any harmful sense**

`_turnover_ledger_record(...)` is at **17045**, guarded by `if _mpg_submit_ok:` (**17044**).
`_mpg_submit_ok` is only true after an accepted submission (16961 / 17002). A refused buy submits
nothing, so *not* charging turnover is correct — and the comment at 16806–16809 says so explicitly:
*"a request the gate suppresses must not be booked as accepted-order request notional"*.
Same for `_min_hold_note_position` (**17056–17058**).

**Verdict: HARMLESS.** (The turnover *reporting* loss is a separate thing — see 2.2 / §3.2.)

### 2.5 Anchor state — **CORRECTLY HANDLED, not lost**

The two anchor writes downstream are `_anchor_pending["order_id"]` and
`["admission_cap_pct"]` at **17016–17021**, guarded by `_mpg_submit_ok and _anchor_order_id`
(17006) — order-accepted only, so skipping them is right. The terminal action for a *refused*
anchor buy is `_anchor_reinforcement_block` (broker.py:3686–3698), which pops the pending record
and logs `ANCHOR BLOCK`. **Both** SATELLITE CAP sites call it (15913–15917, 15964–15967).

**Verdict: HARMLESS — this is the one thing the site gets right.** Note this path was inert in all
four logs examined: `grep -c "ANCHOR BLOCK" /tmp/bt_fb2_full.log` → `0`, so the anchor lane was
switched off in these runs and I have no runtime evidence either way, only the source.

### 2.6 `max_positions` projection state — **HARMLESS**

`_mpg_new_emitted.add(...)` (**17074**) and `_mpg_full_exits.add(...)` (**17081**), guarded by
`if _mpg_cap is not None:` (17071). A buy that was never emitted must not grow the projected count.
Correct to skip.

### 2.7 Sell-proceeds recycling ledger — **N/A**

`_scp_sell_proceeds.append(...)` (**17097**) is guarded by `decision == -1` (17090). The SATELLITE
CAP sites live inside `if decision == 1:` (15835), so this is unreachable from them by construction.

### 2.8 Decision recording — **LOST, HARMFUL**

`_backtest_decisions.append({...})` at **17135–17149**, guarded at **17108** by
`mode == MODE_BACKTEST and _backtest_decisions is not None and not _trade_skipped_no_price`.

The `continue` skips it outright. The row that is lost carries `symbol`, `action` (`"buy"`),
`decision` (`1`), `normalized_score`, `strategies`, `post_decision` — i.e. the entire per-bar
rationale. Consumers:
- **broker.py:12609** → persisted as `backtest_result['backtest_decisions']`
- **broker.py:17349–17350** → live progress payload
- **broker.py:1527** → `trade_ledger_hash(decisions or [], trades or [])` inside
  `_finalize_evidence_success` (1510) — the **evidence receipt hash is computed over an incomplete
  decision set**.
- `backend/api/main.py:5036` → `summarize_decisions(rows)` for the playback UI.

Important precision, so this is not overclaimed: the *fall-through* path loses the row too, because
`_emp_skip` sets `_trade_skipped_no_price = True` at **16613** ("reuse flag to prevent recording").
So the row loss is a property of **every** refused buy, not only of the `continue`. The design
error is at 17108: it conflates *"the strategy decided buy"* with *"a trade executed"*, so a
gate-refused buy appears in the decisions artifact as **nothing at all** — not even a hold row,
because holds bypass the whole `decision != 0` block at 15769 and *are* recorded.

**Verdict: HARMFUL (shared defect, worst at the SATELLITE CAP site).**

### 2.9 `_trade_skipped_no_price` not set — **HARMLESS TODAY, LATENT**

The SATELLITE CAP `continue`s leave the flag at the `False` set at **15768**. Its only read is
**17108**, which the `continue` skips, and it is re-initialised every iteration at 15768, so
nothing leaks across symbols. But every other refusal site sets it (15782, 15806, 15982, 16535,
16613, 16635, 16698, 16778, 16801, 16822). The SATELLITE CAP sites are the only refusals that do
not. Any future edit that moves the recording block, or converts the `continue` into a fall-through,
silently starts recording these as if they had executed.

### 2.10 `cash_to_use = cash_per_trade` (16190) not executed — **HARMLESS TODAY, LATENT**

`cash_to_use` is module scope (the loop body runs at module scope). Skipping 16190 leaves the
previous symbol's value in the name. Every downstream read (16295, 16346, 16457, 16517, 16846,
16910, 16955, 16998) is preceded on the same path by the unconditional write at 16190, so no stale
read occurs today. This is exactly the failure class documented in the comment at **15931–15945**,
where an earlier version of this very block read `cash_to_use` before it was bound and raised
`NameError: name 'cash_to_use' is not defined`, killing the run (bt 311771).

### 2.11 Diagnostics — **LOST, HARMFUL (observability)**

`Buy gate inputs for {symbol}: ... → PASS/SKIP` at **16459–16472** — the comment at 16453 calls it
the line that makes *"skipped buys self-explain"*. Never emitted for a satellite-cap refusal.
`SKIP BUY {symbol} — ...` at **16612** likewise.

---

## 3. Log evidence

All from `/tmp/bt_fb2_full.log` unless stated.

### 3.1 The reason histogram never contains `satellite_cap`

```
$ grep -o "Gate skips reported back: [A-Z.]* ([a-z_]*)" /tmp/bt_fb2_full.log | sed 's/.*(//' | sort | uniq -c
  68 insufficient_cash)
   8 turnover_budget)
   1 buy_price_floor)
```
40 `SATELLITE CAP: X skipped` lines in the same file, 0 reports. Same shape in three other runs:

| log | buy decisions | SAT CAP skipped | SAT CAP trimmed | reasons reported | distinct sat-skipped names never reported anywhere in the run |
|---|---|---|---|---|---|
| bt_fb2_full | 130 | 40 | 53 | insufficient_cash 68 / turnover_budget 8 / buy_price_floor 1 | **28 of 38** |
| bt102463 | 127 | 54 | 56 | 54 / 9 / 1 | **34 of 45** |
| bt523085 | 121 | 36 | 47 | 67 / 7 / 0 | **24 of 33** |
| bt778288_full | 73 | 22 | 35 | 34 / 8 / 1 | **16 of 20** |

35 of the 38 satellite-cap-refused names in bt_fb2_full were never bought at any point in the run
(`grep "\[execution\] FILL BUY"` yields 13 distinct symbols; the intersection with the refused set
is `{AMAT, LLY, TSLA}`).

### 3.2 Same bar, both paths, side by side (2026-01-09 15:00, log lines 9540–9558)

```
9540  TURNOVER BUDGET BINDING: 109% of NAV ... new discretionary BUYS are blocked this tick
9542  MDB  @ 2026-01-09 15:00:00 ($411.73): buy action_intent=backfill_queue_buy
9543  SATELLITE CAP: MDB skipped — satellite at its design share ($-1,370 room); ...
9544  Weighted sum: 1.000 ...                     <-- next symbol. Nothing else for MDB.

9545  SNDK @ 2026-01-09 15:00:00 ($363.01): buy action_intent=backfill_queue_buy
9546  SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $167 of room out of the core
9547  SATELLITE CAP: SNDK trimmed $861 -> $167 to keep the core at target      <-- FALLS THROUGH
9548  TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 ...
9549  Buy gate inputs for SNDK: ... cash_to_use=$167.04 → PASS
9550  DISPLACEMENT: trimming URA ($973.14) to fund SNDK raw=+1.700
9551  SKIP BUY SNDK — cash_to_use $167.04 < min $369 (allocated $167.04)
9552  Gate skips reported back: SNDK (insufficient_cash)
```
MDB (`continue` at 15918) produces **one** log line and **zero** persistent side effects.
SNDK (fall-through at 15958) produces the gate diagnostic (16459), a displacement request
(16604–16611), the skip log (16612) and the backfill report (16617–16628).

### 3.3 The turnover brake's own counter is starved by the same `continue`

`TURNOVER BUDGET BINDING` (announced once per tick, upstream of the loop) appears **615** times.
`TURNOVER BUDGET BLOCK` (broker.py:16127, the per-symbol refusal that *does* report) appears
**8** times. Attributing each satellite-cap skip to its tick (nearest preceding `Execution order:`
line) shows **36 of the 40** satellite-cap skips happened on a tick where the budget had already
announced itself binding — i.e. those 36 buys would have reached 16126, been refused there, and
been reported as `turnover_budget`, had the satellite cap not `continue`d first at 15918.

### 3.4 A closed accounting identity

```
buy decisions logged (broker.py:15614)            130
  minus SATELLITE CAP skipped (15918)              40
  minus TURNOVER BUDGET BLOCK  (16155)              8
  minus Nexus buy price floor  (15827)              1
"Buy gate inputs for ..." lines (16459)      = 81   (observed: 81)
```
Exact. So in this run **no other gate between the decision log and the sizing diagnostic fired at
all**, and the SATELLITE CAP `continue` alone accounts for **40/49 = 82%** of all buy decisions that
never reached the sizing diagnostic, and **30.8% of every buy decision in the run**.

---

## 4. Top 3 defects and proposed fixes

### D1 — the satellite-cap refusal is invisible to the backfill queue

*Evidence:* broker.py:15908–15918 has no `_broker_skipped_buys` append, unlike its four siblings at
15810, 16135, 16177, 16617. Log: reason histogram has no `satellite_cap`; 28/38 refused names never
reported in bt_fb2_full (34/45, 24/33, 16/20 in the other three runs).

*Fix:* the append dict is byte-identical at all four existing sites except the `reason` string.
Extract it once:

```python
def _report_broker_skip(symbol, reason, allocated, price, nexus_hint):
    cache = _strategy_cache.get("graph_nexus_analysis")
    if cache is None:
        return
    cache.setdefault("_broker_skipped_buys", []).append({
        "ticker": symbol,
        "allocated": round(float(allocated or 0.0), 2),
        "reason": reason,
        "price": round(float(price), 4),
        "raw_net_score": round(float((nexus_hint or {}).get("raw_net_score", 0.0) or 0.0), 4),
        "signal_source": str((nexus_hint or {}).get("signal_source") or ""),
        "is_watchlist_member": bool((nexus_hint or {}).get("is_watchlist_member")),
        "is_watchlist_priority": bool((nexus_hint or {}).get("is_watchlist_priority")),
        "is_propagation_expansion": bool((nexus_hint or {}).get("is_propagation_expansion")),
    })
    _log(f"Gate skips reported back: {symbol} ({reason})", "magenta")
```

and call it at **15913** with `reason="satellite_cap"` and at **15964** with
`reason="satellite_cap_min_fill"`, then replace the four inline copies. Also set
`_trade_skipped_no_price = True` at both sites so they match every other refusal (2.9).
`graph_nexus_analysis.py:31893` already stores `broker_skip_reason`, so the new reason string needs
no consumer change.

### D2 — the refusal that means "no room" cannot ask for room

*Evidence:* `_broker_displacement_requests` is appended only at broker.py:16604–16611, nested inside
`if _emp_skip:` (16537), which is unreachable after the `continue` at 15918. The skip fires on
*negative* headroom (`$-1,287`, `$-1,370` in the log), which is exactly a capacity shortfall.
Same-bar contrast at log lines 9543 vs 9550.

*Fix:* at 15907, before `continue`, run the same candidate search with the shortfall set to the
amount needed rather than `_exec_min_pos`:

```python
if _displacement_enabled(_cached_strategies):
    _disp = _displacement_candidate(_disp_held, _sat_raw,
                                    abs(float(_sat_room)) + _CORE_MIN_SATELLITE_TRIM_USD)
    if _disp:
        _strategy_cache["graph_nexus_analysis"].setdefault(
            "_broker_displacement_requests", []).append(
            {"sell": _disp[0], "fund": symbol, "value": _disp[1],
             "score": _sat_raw, "need": abs(float(_sat_room))})
```
Hoist the `_disp_held` / `_disp_rank` construction (16548–16586) into a helper so both call sites
share it. **Caveat, stated plainly:** a prior investigation in this repo found the displacement arm
never converts to a trade because the request set is intersected with `expanded_symbols` in
`_sell_first`. This fix restores the *request*; it will not produce trades until that intersection
is fixed too. I did not verify that intersection myself in this pass.

### D3 — refused buys leave no decision row at all

*Evidence:* `_backtest_decisions.append` at broker.py:17135 is guarded by
`not _trade_skipped_no_price` (17108). The `continue` at 15918 skips it; the fall-through path is
excluded by the flag set at 16613. Holds *are* recorded (they never enter the `decision != 0` block
at 15769), so the decisions artifact contains holds and fills but **nothing** for a refused buy.
That artifact feeds `trade_ledger_hash(decisions, trades)` in `_finalize_evidence_success`
(broker.py:1527) and `summarize_decisions` (api/main.py:5036).

*Fix:* stop using `_trade_skipped_no_price` to suppress the row. Rename the concept: add
`"executed": bool` and `"skip_reason": str` to the dict at 17135–17149 and change 17108 to
`if mode == MODE_BACKTEST and _backtest_decisions is not None:`. To make the record survive the
**16** `continue` statements in the loop body (15801, 15827, **15918**, **15968**, 15988, 15996,
16113, 16155, 16189, 16366, 16536, 16636, 16699, 16782, 16802, 16823 — of which only **3** are
preceded by a `_broker_skipped_buys` report), extract lines 15477–17149 into a per-symbol function
returning the row, so `continue` becomes `return row` and the caller always appends — that also
removes the module-scope-variable hazard described in 2.10 and documented at 15931–15945.

---

## 5. What I could not establish

- **No dollar attribution.** I did not run a backtest, so I cannot say what the 40 dropped buys
  would have earned. The 35-of-38-never-bought figure shows they were dropped, not that buying them
  would have helped.
- **Anchor lane.** `ANCHOR BLOCK` appears 0 times in all four logs, so the `continue` at 15968 and
  the anchor handling at 15913/15964 are unexercised in this evidence. My verdict there (2.5) is
  from source reading only.
- **Whether reordering is safe.** Moving the satellite cap below the turnover/regime gates would
  change which gate wins and therefore the run's behaviour. I list it as an observation (3.3), not
  a recommendation.
- **Displacement conversion.** See the caveat in D2.
