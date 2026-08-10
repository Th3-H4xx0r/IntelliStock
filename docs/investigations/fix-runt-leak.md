# fix-runt-leak — the exec floor was measuring a number that is not the position

**Scope.** Read-only on `backend/` except `backend/broker.py` and `backend/tests/`.
No push, no backtest started or stopped. Builds on `_RUNS4.md`, `fix-audit-levers.md`
(row 17, `min_position_nav_pct`), `sweep2.md`, `gap-capital.md`. Not redone here.

Log: `backtests/676939_runt.log` (41,684 lines, pulled at status `finished`).
Run: bt **676939**, 2026-01-01..03-01, `v2-let-run-core`, $6,000 → **$6,878.82 (+14.65%)**,
21 fills, `min_position_nav_pct=0.06`, HEAD = `bd3fb2a` (adds exempt).

---

## 0. FIRST — two of the four names are not runts

Every fill in the run, with its notional computed from the fill line itself:

| fill | qty × price | **notional** | % of $6,000 | run P&L | P&L % |
|---|---|---:|---:|---:|---:|
| `FILL BUY AXTI` 02-02 | 31.09284414 × 22.090472 | **$686.86** | 11.4% | −$84.30 | −12.27% |
| `FILL BUY GLUE` 01-12 | 32.42333576 × 23.754274 | **$770.19** | 12.8% | −$46.75 | −6.07% |
| `FILL BUY AMZN` 02-04 | 0.42832162 × 238.524980 | **$102.17** | 1.7% | −$12.21 | −11.96% |
| `FILL BUY AVY` 01-06 | 0.26066930 × 181.685112 | **$47.36** | 0.8% | +$3.80 | +8.02% |

The run's own `P&L per stock` block closes it arithmetically: −$84.30 / −12.27% implies a
$686.9 basis, −$46.75 / −6.07% implies $770.2. **AXTI and GLUE were full-size 11–13%-of-NAV
positions that lost money.** `$84.26` and `$46.71` are their *losses*, not their sizes — the
two were transposed in the brief.

So the leak is **AVY + AMZN**: $149.53 of notional, **net −$8.41**, not −$139.46.
AXTI/GLUE are a separate question (a −12% and a −6% exit on properly sized positions);
nothing in this fix touches them, and nothing should.

---

## 1. THE FOUR HYPOTHESES, AGAINST THE LOG

| # | hypothesis | verdict | evidence |
|---|---|---|---|
| a | adds to a position opened earlier, so the held-exemption applies | **NO** | `AVY … buy action_intent=initial_buy`, `AMZN … buy action_intent=backfill_queue_buy`. Neither appears in any earlier `FILL BUY`. `_emp_held` was correctly `False`. |
| b | a different code path that never reaches the check | **NO — but the closest** | Both printed `Buy gate inputs for X: … → PASS`, which is emitted 3 lines *above* the floor. They reach it. The check ran, on the wrong number. |
| c | `get_positions()` empty at that point | **NO** | the same gate lines carry `open_pos=5` (AVY) and `open_pos=7` (AMZN). |
| d | the allocator's P3 undersized guard and the exec floor disagree | **NO** | both agreed. The allocator sized AVY at $860.36 and AMZN at $613.78 — both far *above* the $365/$396 floor. Neither end was wrong about the number it saw. |

**The third party neither end consults is the emulator.**

---

## 2. ROOT CAUSE — the gate reads the REQUEST, the emulator funds something else

```
19:59:19  Buy gate inputs for AVY:  cash=$1680.42 reserved=$0.00 … cash_per_trade=$860.36
                                    available=$1680.42 cash_to_use=$860.36 → PASS
19:59:19  [execution] FILL BUY SPY qty=2.24250044 price=690.678055        = $1,548.85
19:59:19  [execution] FILL BUY AVY qty=0.26066930 price=181.685112        = $47.36
```

`broker.py` decides on `cash_to_use`. What opens the position is decided ~250 lines later,
inside the emulator:

```python
# backend/portfolio_emulator.py:1489  (execute_signal, next-event path)
amount_to_use = min(cash_per_trade, self.get_buying_power(reserved_cash))
```

`reserved_cash` is the sum of `_execution_cash_reservations` — **buy orders already
in flight on this same tick**. The index-core SPY leg is emitted first, so by the time the
alpha name submits, the cash it was sized against is spoken for. `get_buying_power` then
*also* nets out `_withheld_cash()`, the unsettled 5% (T+1) slice of recent sells. The buy
gate reads `get_cash()` (broker.py:15163) and sees **neither**.

Reconstructed to the dollar off the log — both ticks, no free parameters:

```
AVY   2026-01-06 15:00
  cash at gate                                          $1,680.42
  − unsettled: 5% of FILL SELL SPY 01-05 ($1,678.76)     −$83.94
  − in-flight: FILL BUY SPY this tick                 −$1,548.85
  = buying power                                          $47.63   → FILL $47.36  ✓

AMZN  2026-02-04 15:00
  cash at gate                                            $618.21
  − unsettled: 5% of FILL SELL AXTI 02-03 ($602.60)       −$30.13
  − in-flight: FILL BUY SPY this tick                    −$485.40
  = buying power                                         $102.68   → FILL $102.17 ✓
```

(the residual few cents is `affordable_buy_quantity`'s cost haircut between the decision
price and the fill price — $182.17 vs $181.685 on AVY.)

**This is a known clamp, already written down in the code, from the other side.**
`portfolio_emulator.py:1480`:

> *"This clamp is why crediting the release at the BROKER gate alone was inert — the gate
> said PASS and the emulator cut the order anyway (bt 613166 02-05: gate $805.24 → fill
> $87.45)."*

That comment is about a buy the clamp made *too small to be worth making*. This is the same
clamp seen from the floor's side: a buy the clamp made too small **to be allowed to take a
`max_positions` slot** — and the floor whose whole job is to catch that was looking at the
pre-clamp number.

Why the two earlier fixes missed it: `89e71f3` raised the floor, `bd3fb2a` exempted adds.
Both changed the *threshold* and the *exemption*. Neither changed the *measurement*. And the
only test was `test_exec_min_position_floor.py`, a hand-written **mirror** of the expression —
a mirror agrees with itself no matter what the real path does.

---

## 3. THE FIX

`backend/broker.py` only. Three small functions plus one composed gate, all module-level so
tests call the code the run calls:

| symbol | what it is |
|---|---|
| `_EXEC_MIN_POSITION_USD = 50.0` | the historical floor, used when the key is absent |
| `_exec_min_position_floor(cfg, nav)` | dollar minimum or NAV share, larger wins (unchanged rule) |
| `_exec_fundable_amount(pe, cash_to_use)` | **new** — `min(cash_to_use, pe.get_buying_power(in-flight reservations))`, i.e. exactly what `execute_signal` will spend |
| `_exec_min_position_skips(...)` | the decision, now taken on `fundable` instead of `cash_to_use` |
| `_exec_min_position_gate(...)` | composes all four + the held-name lookup; the buy block does nothing but call it and log |

The buy block collapsed from ~55 lines of inline expression to one call.

**A NEW name below the floor cannot open.** `fundable < floor` and `held is False` → skip,
reported back to the backfill queue as `insufficient_cash` exactly as before, so the name
re-queues rather than vanishing.

**An ADD to a held name still can.** `held` short-circuits `_exec_min_position_skips` before
the floor is consulted — `bd3fb2a`'s property, preserved verbatim and now tested on the same
clamped emulator state that refuses a new name.

**Default-OFF is unchanged, and it is a property of the gate, not of the call site.** With
`min_position_nav_pct` absent: `pct == 0` → `floor == $50` → **`_exec_fundable_amount` is
never called** → `fundable is cash_to_use` → the decision is the pre-2026-08-09 rule
bit-for-bit, including the historical "only refuse what was truncated" hole. Asserted
directly, on the real gate, over the whole truth table.

**Live is untouched.** `get_buying_power` exists only on `PortfolioEmulator`; every live
adapter (`alpaca.py`, `binanceus.py`) has `get_cash` and not that, so
`_exec_fundable_amount` takes its identity path and returns the request unchanged. A
raising or absent emulator also returns the request — a sizing *diagnostic* must never be
able to refuse a trade it could not measure.

**Log shape preserved.** The line is still
`SKIP BUY X — … < min $N (allocated $M)`; it only widens to
`fundable $A of cash_to_use $B (orders already in flight this tick reserve the rest)`
when the two genuinely differ, so five sessions of grep-based ledgers keep working.

---

## 4. TESTS

`backend/tests/test_exec_runt_leak_fundable.py` — 16 tests. They drive **broker.py's own
`_exec_min_position_gate`** (AST-extracted, the `test_core_sleeve_wiring.py` pattern) against a
**real `PortfolioEmulator`** rebuilt into bt 676939's two ticks through `buy()`/`sell()`/
`execute_signal()` — so the T+1 tranche and the in-flight reservation are the ones the engine
really makes, not fixtures. Each runt test asserts the **delta**: `_legacy_skips(...) is False`
(the old rule admitted it) next to `skip is True`.

Verified to fail without the fix. Deleting the three lines that read the fundable amount:

```
FAILED test_AVY_a_new_name_below_the_floor_no_longer_opens
FAILED test_AMZN_a_new_name_below_the_floor_no_longer_opens
FAILED test_an_ADD_to_a_held_name_still_opens_even_when_clamped
FAILED test_a_clamp_that_still_clears_the_floor_is_funded
```

`backend/tests/test_exec_min_position_floor.py` — the mirror is deleted. Its two helpers now
delegate to the real `_exec_min_position_floor` / `_exec_min_position_skips`; all 11 original
assertions are unchanged and still pass. The file that let this through can no longer drift.

**Suite:** `4,773 passed / 13 skipped` ignoring the three known-failing adversarial files
(`test_adv_exit_discipline_findings.py`, `test_core_sleeve_adversarial.py`,
`test_zz_adversarial_sweep.py`). Those 19 failures are identical on HEAD with this change
reverted — confirmed by running both. Net: **+16 tests, 0 new failures.**

`gitnexus detect-changes`: risk **low**, 0 affected processes (the edited region is
module-level script code inside broker.py's main loop; the four new functions have no
upstream callers by construction). `gitnexus impact get_buying_power --direction upstream`:
**LOW**, 10 impacted, 0 processes — and it is only *read* here, `portfolio_emulator.py` was
not touched.

---

## 5. WHAT THIS IS AND IS NOT WORTH

**Is:** a $47 and a $102 position, each holding one of `max_positions` slots on a book whose
`[V28.8.1 max_positions BREACH]` latch killed every new-name buy on 17 bars of the comparable
run. That is the cost — the slot, not the −$8.41.

**Is not:** a P&L claim. The two runts netted **−$8.41** on a run that made **+$878.82**.
Per `_SYNTHESIS.md` the run-to-run noise floor is ≥4.94pp; this is 0.14pp. Do not attribute
anything to it, and do not expect the next run to be visibly different because of it.

**The generalisable finding is the third one, and it is bigger than this gate:**
`fix-audit-levers.md` row 31 convicted `backtest_credit_pending_sell_proceeds` for exactly
this shape — a control wired at the broker gate while the binding arithmetic lives in the
emulator. That is now **two** confirmed instances of the same class:

> **Any broker-side gate that reasons about cash is reasoning about `get_cash()`, and
> `get_cash()` is not what `execute_signal` will spend.** The spendable number is
> `get_buying_power(in-flight reservations)`, and the two diverge on precisely the ticks the
> index core is active — which, with `core_sleeve_enabled=True`, is most of them.

Worth auditing next, on the same criterion: the `Buy gate inputs` diagnostic itself (it prints
`available=$1680.42` on a tick where $47.63 was spendable, which is what made this leak look
like a mystery for two fixes), `cash_reserve_floor_pct`, and `deferred_unfunded_buy` (50 fires
in bt 571147 — how many of those were this clamp rather than genuine emptiness?).

---

## 6. FILES

```
backend/broker.py                              +134 / −8   (helpers + one call site)
backend/tests/test_exec_runt_leak_fundable.py  new, 16 tests
backend/tests/test_exec_min_position_floor.py  mirror → real functions (assertions unchanged)
docs/investigations/fix-runt-leak.md           this file
backtests/676939_runt.log/.json                pulled evidence
```
