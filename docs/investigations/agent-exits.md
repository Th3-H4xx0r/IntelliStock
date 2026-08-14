# bt 102463 — Are winners sold too early? (W0 displacement arm, +11.12%)

**Log:** `/tmp/bt102463.log` (40,827 lines) · **Window:** 2026-01-01 → 2026-03-01 · **Result:** $6,000 → $6,667.41 (+11.12%)
**Scope:** log + `backend/` source only. No backtest was run. No other file was modified.

---

## 0. Verdict on the assigned aspect: **NO. Winners are not sold too early.**

The hypothesis is falsified. Nothing in this run sold a winner.

| Symbol | Entry | Exit | Days held | State at end | P&L | Exit source |
|---|---|---|---|---|---|---|
| AMAT | 2026-01-08 | — | 51 | **OPEN** | +32.37% | — |
| BALL | 2026-01-06 | — | 53 | **OPEN** | +21.10% | — |
| URA  | 2026-01-02 | — | 57 | **OPEN** | +20.35% | — |
| TSM  | 2026-01-02 | — | 57 | **OPEN** | +17.77% | — |
| CCK  | 2026-01-06 | — | 53 | **OPEN** | +8.69% | — |
| AMGN | 2026-02-06 | — | 22 | **OPEN** | +3.44% | — |
| SPY  | 2026-01-02 | — | 57 | **OPEN** (partial trims) | -0.07% | — |
| TSLA | 2026-01-02 | 2026-01-07 | 5 | CLOSED | **-4.90%** | `main_signal` |
| TPG  | 2026-01-02 | 2026-02-03 | 32 | CLOSED | **-19.69%** | `main_signal` |

**Median holding period: 53 days** (of a 57-trading-day window).

Supporting evidence:

* Only **5 FILL SELL** lines exist in the entire log (`Total Trades: 16 / Buys: 11 / Sells: 5`, log L40790-40792). Three are SPY core-rebalance partials (`source=residual_bull_refill`); the other two are TSLA and TPG.
* **Both closed names were losers.** Zero profitable positions were closed. Every one of the six profitable names appears in the terminal `Positions:` block (L40775-40781).
* **The trailing stop never fired.** `Trailing stop SUPPRESSED (trailing_stop_disabled)` appears **56 times** and `Trailing stop HIT/FIRED/TRIGGER` appears **0 times**. e.g. L26446: `Trailing stop SUPPRESSED (trailing_stop_disabled): URA drop=20.3% >= 16% — held; catastrophic stop is the floor`. URA still finished +20.35%.
* The two exits were loss-cutting rules, not profit-taking:
  * **TPG** — L24512: `Monitor decision: TPG day 32 pnl=-17.3% cp=$53.92 entry=$65.21 → SELL (Circuit breaker: -17.3% loss hit floor -15.0% (tier=MID base=-15% regime=bull))`. This is the only `SELL` among 2,975 `Monitor decision` lines; the other 2,974 are `HOLD`.
  * **TSLA** — L6192: `Sell enforcement ADD: TSLA forced_exit=True, reason=Direct general sentiment=-1`, preceded by L6165 `Deep-loser PROTECT: TSLA unrealized=-5.1% (< -5%) — sell locked, ML cannot override`.

### "P&L left on the table" and "sold while still rising" — cannot be computed from this log

**I cannot support a max-price-after-exit claim, and I will not fabricate one.** The per-symbol quote stream in this log is the `Monitor decision ... cp=$X` series, which is emitted **only for open positions** and **stops at the exit**: TSLA's last quote is L5918 (day 5, `cp=$434.94`) and TPG's last is L24512 (day 32, `cp=$53.92`). After a position closes the log carries no further price for that name. This is exactly the partial-window trap — the only post-exit datapoint is the terminal price in the summary block, and comparing that single full-window endpoint against a truncated series would be invalid.

What *is* supportable: both closed names were **lower at the end of the window than at their exit**, so the realised effect of these two exits was to *save* money, not to leave it on the table.

* TSLA: exit $434.81 → terminal $402.43 (L40818: `TSLA: $449.81 -> $402.43 (-10.53%)`)
* TPG: exit $52.37 → terminal $43.43 (L40820: `TPG: $63.86 -> $43.43 (-31.99%)`)

An intermediate higher high between exit and window end **cannot be ruled out from this log** because no post-exit quotes for closed names are recorded. Resolving that requires the price series, not this log.

**Conclusion: the exit path is not this arm's problem. The entry/funding path is.** Findings below are what the exit investigation surfaced.

---

## DEFECT 1 — The W0 displacement arm is a complete no-op: 54 requests, 24 "EXECUTE" logs, **0 fills**

This is the arm the run exists to test, and it never traded.

**Evidence — the funnel collapses to zero:**

| Stage | Count | Log signature |
|---|---|---|
| Requests raised | 54 | `DISPLACEMENT: trimming ... to fund ...` |
| Survive dedup, logged as executing | 24 | `DISPLACEMENT EXECUTE: trimming N% of X ...` |
| Reached the execution order | **1** | see below |
| **Actually filled** | **0** | no matching `FILL SELL` / `FILL BUY` |

Displacement victims were `{CCK, TPG, TSLA, TSM, URA}`; targets were `{ALSN, ATI, AVNT, CAH, DTE, GILD, JPM, LLY, LRCX, MBLY, NOC, OI, PLD, RCL, SKYT, SNDK, TTWO, WMT}`. **Not one target was ever bought.** Reconciling fills against the terminal position block proves no trim ever occurred — every held name's net filled quantity equals its final share count to 4 dp:

```
sym      bought         sold          net   final_pos
AMAT   2.989160     0.000000     2.989160      2.9892  OK
BALL  15.476478     0.000000    15.476478     15.4765  OK
CCK    8.067138     0.000000     8.067138      8.0671  OK
TSM    2.639961     0.000000     2.639961      2.6400  OK
URA   18.606919     0.000000    18.606919     18.6069  OK
```

CCK was targeted for a trim **11 times** and TSM **4 times**; both ended the window with their original share count.

**Root cause — an intersection with the wrong set.** `backend/broker.py:14573` adds the trim victim to the sell set:

```python
_dhint_out["sell_fraction"] = max(...)      # broker.py:14571
_nexus_sell_set.add(_dsym)                  # broker.py:14573
_log(f"DISPLACEMENT EXECUTE: trimming ...") # broker.py:14574-14577
```

but `backend/broker.py:14582` then filters that set through the day's *discovery* universe:

```python
_sell_first = [s for s in sorted(expanded_symbols) if s in _nexus_sell_set]
```

`expanded_symbols` is seeded at `backend/broker.py:14034` as `set(symbols or [])` — the tick's strategy universe plus propagation-promoted buys. **Currently-held positions are not members.** On most ticks the strategy returns nothing (`Run-once strategy 'graph_nexus_analysis' returned scores for 0 symbols`, L40770), so `expanded_symbols` does not contain CCK/URA/TSM and the intersection is empty. The `sell_fraction` hint is written into `nexus_position_sizes` and then never iterated.

**Proof:** `backend/broker.py:14604` guards the order log with `if _sell_first:`. **23 of the 24** `DISPLACEMENT EXECUTE` lines are followed by *no* `Execution order:` line at all before the next tick — i.e. `_sell_first` was **empty**. The single exception is L24532 (CCK trim), where L24533 reads `Execution order: 1 sell(s) first, then 0 buy/hold candidate(s)` — and that one sell was **TPG's circuit-breaker exit**, not the CCK trim. CCK's trim was dropped even on the one tick where the sell path ran.

**Proposed fix** (`backend/broker.py:14582`) — union held-position sells in rather than intersecting them away:

```python
_held_syms = set((portfolio_emulator.get_positions() or {}).keys())
_sell_universe = set(expanded_symbols) | (_nexus_sell_set & _held_syms)
_sell_first = [s for s in sorted(_sell_universe) if s in _nexus_sell_set]
_buy_rest   = [s for s in sorted(expanded_symbols) if s not in _nexus_sell_set]
```

A sell of something already owned must never be gated on that name appearing in today's *discovery* universe. Add an assertion that every symbol in `_nexus_sell_set` with a `sell_fraction` either reaches `_exec_order` or is logged as dropped with a reason — this bug was silent for 24 ticks.

---

## DEFECT 2 — `pop()` + one-per-holding dedup destroys 30 of 54 displacement requests with no retry

`backend/broker.py:14540-14541` **removes** the request queue from the cache before it is known to have executed:

```python
_disp_reqs = ((_disp_cache or {}).pop(
    "_broker_displacement_requests", None) or [])
```

`backend/broker.py:14546-14558` then keeps only the highest-scoring request per holding (`_disp_best`), discarding the rest permanently. Combined with Defect 1 (the survivor does not execute either), **every displacement request is silently destroyed**: 54 raised → 24 kept → 0 filled. There is no backlog, no retry, and no log line for the 30 discarded requests.

**Evidence:** on the 2026-01-05 tick, three high-conviction names each requested a trim of the *same* holding — L4457 `DISPLACEMENT: trimming TSM ($858.81) to fund ARWR raw=+1.700`, L4465 `... to fund CFG raw=+1.750`, L4473 `... to fund MBLY raw=+1.800`. Only MBLY's survived (L4734: `DISPLACEMENT EXECUTE: trimming 45% of TSM ($858.81) to free $364.96 for MBLY`). ARWR and CFG were dropped without a log line and **never bought anywhere in the run**.

The dedup itself is correct in intent — its comment (broker.py:14542-14545) cites bt 511709, where three buys expected funding from one release. The defect is that the *losers* of the dedup are deleted rather than deferred.

**Proposed fix** (`backend/broker.py:14540`): read without mutating, and write back whatever did not fill.

```python
_disp_all = list((_disp_cache or {}).get("_broker_displacement_requests") or [])
# ... select _disp_best as today's winners ...
_deferred = [r for r in _disp_all if r not in _disp_best.values()]
for _r in _deferred:
    _r["age"] = int(_r.get("age", 0)) + 1
_disp_cache["_broker_displacement_requests"] = [
    _r for _r in _deferred if _r["age"] <= DISPLACEMENT_MAX_AGE_TICKS]
_log(f"DISPLACEMENT DEFER: {len(_deferred)} request(s) re-queued")
```

Only clear a request once its trim has produced a confirmed `FILL SELL`.

---

## DEFECT 3 — The buy gate prints `→ PASS` using a hardcoded $50 while the binding floor is $365

The `Buy gate inputs ... → PASS` diagnostic is measured against a **hardcoded constant that does not track the real floor**, so the gate reports PASS for orders that the very next statement refuses.

`backend/broker.py:16456-16457`:

```python
_exec_min_pos_preview = 50.0
_will_skip = cash_to_use < _exec_min_pos_preview and cash_to_use < cash_per_trade
```

`_exec_min_pos_preview` is the literal `50.0`. The binding decision is made ~60 lines later at `backend/broker.py:16515-16518` by `_exec_min_position_gate`, whose floor comes from `_exec_min_position_floor` (`backend/broker.py:3732-3751`) = `max(_EXEC_MIN_POSITION_USD, nav * min_position_nav_pct)`. In this run that evaluated to **$365**, not $50.

**Evidence — PASS and SKIP on consecutive lines for the same symbol:**

```
L4456  Buy gate inputs for ARWR: cash=$241.69 ... cash_to_use=$121.69 → PASS
L4457  DISPLACEMENT: trimming TSM ($858.81) to fund ARWR raw=+1.700 ...
L4458  SKIP BUY ARWR — cash_to_use $121.69 < min $365 (allocated $851.57)
```

Identical PASS-then-SKIP pairs occur for CFG (L4464/L4466) and MBLY (L4472/L4474). Across the run there are **55 `SKIP BUY` lines** — 48 `cash_to_use < min` and 6 `fundable ... < min` — every one of them preceded by a `→ PASS`. The run-config line (L20) advertises `min=$100`, which matches neither the $50 preview nor the $365 effective floor.

This defect is also the *upstream cause* of Defects 1 and 2: `_exec_min_position_gate` failing is what raises the displacement request in the first place (`backend/broker.py:16599-16611`).

**Proposed fix:** move the diagnostic below the real gate and report the binding number, so no order can be logged PASS while already doomed.

```python
(_emp_skip, _exec_min_pos, _emp_fundable,
 _emp_held) = _exec_min_position_gate(...)          # broker.py:16515, run FIRST
_log(f"Buy gate inputs for {symbol}: ... cash_to_use=${cash_to_use:.2f} "
     f"min_pos=${_exec_min_pos:.2f} fundable=${_emp_fundable:.2f} "
     f"→ {'SKIP' if _emp_skip else 'PASS'}")
```

Delete the `50.0` literal; a diagnostic that carries its own private copy of a threshold will drift from the real one again.

---

## Note on a latent risk (not a claim of present loss)

Both alpha exits emitted `[ghost_sell_observation] symbol=TSLA intents=[] pre_action='sell' would_block_in_phase2=True` (L6358) and the same for TPG (L24538). The **empty** `intents=[]` means *evaluated, no match* — not *not evaluated*. `would_block_in_phase2=True` indicates that under phase-2 rules these two exits would be **blocked**. Since both were loss-cutting exits (TSLA -4.90%, TPG -19.69%, TPG falling on to -31.99%), enabling phase 2 without addressing this would have suppressed the circuit breaker. I have not read the phase-2 gate and make no claim about its current status.

## Explicitly unsupported

* "N names sold while still rising" and "$X of P&L left on the table" — **not computable from this log** (no post-exit quotes for closed names; see §0).
* The +11.12% return is not attributed here. This investigation scoped the exit path and the displacement funnel only; I did not measure the counterfactual value of the 18 unbought displacement targets.
