# NAV/portfolio-value scope inside the broker execution loop (broker.py:15900–16600)

**Scope of this note.** One question: *what NAV-like value is legitimately in
scope at broker.py:15900–16600, so that a fix there can compute the execution
floor?* Plus the defects that question exposes.

**Evidence base.**
* Source: `backend/broker.py` @ 17,695 lines (working tree, unmodified), plus
  `backend/portfolio_emulator.py`, `backend/strategies/graph_nexus_analysis.py`.
* Log: `/tmp/bt_fb2_full.log` — backtest **id=718107** (`bt_fb2_full.log:3`:
  `Backtest result row (id=718107) ensured in DB, status=running`), window
  2026-01-01 → 2026-03-01, instance `v2-conv-trt`, 48,580 lines.
  **The parent task did not name a log file.** I selected this one because it is
  the most recent full-window run and is the run already characterised in the
  session memory. All log line numbers below are 1-based into that file.
* **No backtest was run.** No file outside this one was written. Nothing pushed.

---

## A. `nav` is not a name at module scope. It is a NameError landmine.

`broker.py`'s main tick body — `while not shutdown_requested:` at **line 12259**
— executes at **module scope**. Confirmed by walking the enclosing block chain
for the region of interest:

```
broker.py:12259  while not shutdown_requested:        (indent 0  → module scope)
broker.py:12260      try:
broker.py:13159          else:
broker.py:15476              for symbol in _exec_order:
broker.py:15769                  if portfolio_emulator is not None and decision != 0:
broker.py:15783                      else:
broker.py:15835                          if decision == 1:
broker.py:15906                              if _sat_room is not None:      ← the SATELLITE CAP block
```

An AST walk of `broker.py` that visits module-level statements only (skipping
`FunctionDef`/`AsyncFunctionDef`/`ClassDef`/`Lambda` bodies) collects **zero**
`Store` bindings of the name `nav`. Every `nav` in the file is function-local:

| line | enclosing `def` |
|---|---|
| 3478 | `_core_sleeve_satellite_headroom` (def @ 3434) |
| 3560 | `_core_sleeve_block_new_satellite` (def @ 3544) |
| 3661 | `_anchor_reinforcement_position_headroom` (def @ 3651) |
| 3849 | `_exec_min_position_gate` (def @ 3825) |
| 4145 | `_core_sleeve_decide` (def @ 4124) |
| 4932 | `_residual_sleeve_release` (def @ 4548) |
| 5275, 5428 | `_residual_sleeve_deploy` (def @ 4984) |

So **any patch at 15900–16600 that writes a bare `nav` raises
`NameError: name 'nav' is not defined` and takes down the tick.**

This is not hypothetical. `broker.py:15931–15945` documents the identical
failure, in this exact block, four months ago:

> `# 2026-08-04 CRASH FIX. This read 'cash_to_use', which is not assigned until`
> `# ~80 lines BELOW ('cash_to_use = cash_per_trade'). At module scope the name`
> `# survives between iterations of the 'for symbol in _exec_order' loop, so this`
> `# only worked when some EARLIER iteration had already bound it. The FIRST buy`
> `# taken while the core is armed raised NameError: name 'cash_to_use' is not`
> `# defined and killed the run (bt 311771 ...). In LIVE this kills the tick.`

Module scope makes this worse than an ordinary NameError: a name bound anywhere
in the loop body **persists across symbols and across ticks**, so a late-bound
name read early reads a *stale value from a previous symbol* instead of raising.
That is a silent wrong answer, not a crash.

---

## B. Complete enumeration of NAV sources in scope at 15900–16600

Ordered by how safe they are at **line 15954** (the SATELLITE CAP trim), which
is the earliest point in the region where a fix would need NAV.

### B1. RECOMPUTE — the only unconditionally correct option

```python
float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
```

* `portfolio_emulator` — module-scope bindings at **9798, 9810, 10747**; all
  before the loop. Proven live inside the block: used at **15904**, **16518**,
  **16568**.
* `prices` — module-scope bindings at **12288, 12854, 12859, 12942, 13089,
  13181, 14324, 14504**; the last (`prices = ext2`, **14504**) is still before
  the loop opens at 15476. Proven live at **15904**, **16518**.
* `get_portfolio_value(prices)` is defined on `PortfolioEmulator`
  (`portfolio_emulator.py:780`) *and* on the adapter Protocol
  (`broker_adapters/base.py:193`), Alpaca (`alpaca.py:2699`) and BinanceUS
  (`binanceus.py:319`) — so this idiom is safe in **live** as well as backtest.
* It is robust to a missing bar: `portfolio_emulator.py:802–808` falls back to
  `_last_prices` for any held ticker absent from `prices`.
* This is already the house idiom inside the very same loop body:
  **15313** (`_fr_anchor_nav`), **16084** (`_anchor_nav`), **16334**
  (`_equity`), and inside the gate at **3849**.

### B2. `_exec_min_position_floor(cfg, nav)` — module-level, gives the floor directly

`broker.py:3732`, defined long before the loop, therefore callable at 15954:

```python
floor = max(_EXEC_MIN_POSITION_USD, nav * cfg["min_position_nav_pct"])   # 3740–3750
```

`_EXEC_MIN_POSITION_USD = 50.0` (**3729**). `cfg` comes from
`_core_sleeve_cfg_raw(_cached_strategies)` (`def` @ **3580**);
`_cached_strategies` has module-scope bindings at **7659, 9523, 9557, 9574,
10813, 10860** — all before the loop, and it is already read at **15889**,
**15978**, **16210**, **16518**.

### B3. `_exec_min_position_gate(...)` — usable only AFTER `cash_to_use` exists

`broker.py:3825`. Signature requires `decision, symbol, cash_to_use,
cash_per_trade, cached_strategies, portfolio_emulator, prices`.
**`cash_to_use` is first bound at line 16190.** Calling this full gate at 15954
reintroduces exactly the 2026-08-04 crash described at 15931–15945. At 15954
only **B2** is safe.

### B4. `pv` — a real, pre-loop, per-tick NAV snapshot (stale by design)

`broker.py:14346`: `pv = portfolio_emulator.get_portfolio_value(prices)`, at
indent 16 directly under `if portfolio_emulator is not None:` (**14291**),
inside the same `else:` branch (**13159**) that contains the execution loop.
It is therefore **bound on every path that reaches 15900** when
`portfolio_emulator is not None` — which the buy block itself requires
(**15769**).

Caveats, both real:
* It is computed **before** `prices = ext2` (**14504**) and before any of this
  tick's fills, so it is a stale mark.
* `get_portfolio_value` can return a falsy value; **14347** guards it with
  `if pv is not None and pv > 0`. Any new reader must repeat that guard.
* A second `pv` binding exists at **12950** in the MODE_BACKTEST warm-boot
  branch, so the name can also carry a much older value on some paths.

Usable as a cheap NAV, but B1 is strictly better and costs one dict pass.

### B5. `_equity` — bound LATER and CONDITIONALLY. Do not read it at 15954.

`broker.py:16331–16338`, inside `if _max_single_pct > 0 and not
_is_crypto_instance_runtime():` (**16327**) inside `try:` (**16328**).
At 15954 it is either unbound (first buy of the process) or holds the value from
a previous symbol/tick. Same defect class as the documented `cash_to_use` crash.

### B6. `_anchor_nav` (16083) / `_fr_anchor_nav` (15312) — anchor-lane only

`_anchor_nav` is bound only under `if _anchor_policy:` (**16075**) and is after
15954. `_fr_anchor_nav` is bound only under `if _fr_anchor_policy:` (**15310**)
in the pre-loop core-funding block. Neither is reliably bound.

### B7. `portfolio_emulator._initial_value` — **this is not NAV**

`portfolio_emulator.py:207`: `self._initial_value = initial_cash  # Track
original portfolio value`. It is **start cash**, frozen forever. It is read at
**16193** (`_cash_floor = portfolio_emulator._initial_value * _cash_floor_pct`)
and as a fallback at **16338**. See §D-note.

### B8. Values in scope that are NAV-*derived* but are not NAV

* `_sat_room` (**15903**) — satellite headroom in dollars, computed from NAV
  inside `_core_sleeve_satellite_headroom` at **3478**.
* `_exec_min_pos` (**16515**) — the floor itself, i.e. NAV × pct. In scope only
  for lines **> 16518**.
* `_turnover_used` (**15455/15457**) — a *fraction* of NAV, not NAV.
* `_cash_now` (**16229**) / `portfolio_emulator.get_cash()` — cash only.
* `available` (**16294**) — cash minus reservations minus the cash floor.

### B9. Summary table

| candidate | line bound | in scope @15954? | safe? |
|---|---|---|---|
| `portfolio_emulator.get_portfolio_value(prices)` | — (call) | yes | **YES — use this** |
| `_exec_min_position_floor(cfg, nav)` | def 3732 | yes | **YES** |
| `pv` | 14346 | yes | usable, stale, needs `>0` guard |
| `_exec_min_position_gate(...)` | def 3825 | no (`cash_to_use` @16190) | **NO at 15954** |
| `_equity` | 16331 | no (later + conditional) | **NO** |
| `_anchor_nav` | 16083 | no | **NO** |
| `_fr_anchor_nav` | 15312 | conditional | **NO** |
| `_initial_value` | attr | yes | **NO — start cash, not NAV** |
| bare `nav` | never | **no** | **NameError** |

---

## C. What the EXISTING floor check at ~16515 actually uses

It uses **none of the caller's locals**. It hands the emulator and the price map
to a helper that computes NAV itself:

```
broker.py:16515-16518
    (_emp_skip, _exec_min_pos, _emp_fundable,
     _emp_held) = _exec_min_position_gate(
        decision, symbol, cash_to_use, cash_per_trade,
        _cached_strategies, portfolio_emulator, prices)
```

Inside `_exec_min_position_gate` (`broker.py:3825`):

```
3846        cfg = _core_sleeve_cfg_raw(cached_strategies) or {}
3847        pct = float(cfg.get("min_position_nav_pct", 0.0) or 0.0)
3848        if pct > 0 and portfolio_emulator is not None:
3849            nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
3850            floor = _exec_min_position_floor(cfg, nav)
```

Three properties that any fix must preserve:

1. **NAV is recomputed at the gate**, from `(portfolio_emulator, prices)`. There
   is no shared NAV variable to reuse.
2. **NAV is only read when `pct > 0`** (3848). With `min_position_nav_pct`
   absent, `floor` stays `_EXEC_MIN_POSITION_USD = 50.0` (3844/3729) and the
   gate is bit-for-bit the pre-2026-08-09 rule. Default-OFF is a property of
   this function (docstring 3835–3838).
3. **The whole body is wrapped in `try/except` (3845/3851)** that resets to the
   $50 default. A diagnostic that throws would take the tick down.

**Verified against the log.** Every `SKIP BUY … < min $N` line in
`bt_fb2_full.log` reports `_exec_min_pos`. Across the 68 such lines the value
ranges $355–$384. Dividing by the `min_position_nav_pct` = 0.06 used by this
config family (`backend/tests/test_exec_min_position_floor.py:76`,
`test_exec_runt_leak_fundable.py:92`) implies NAV = $5,917–$6,400 — which
matches the book independently: `bt_fb2_full.log:2259` reads
`[core] bought $2400.00 SPY @ 681.82 (band_deploy: 0.0% -> 40.0% of NAV)`
→ NAV = $6,000 at the open of the window. The floor is tracking live NAV, as
designed.

---

## D. Defects this exposes

### D1 (HIGH). Two choke points trim a buy to a number a third choke point is guaranteed to refuse. Neither trimmer knows the floor exists.

The same scalar — `_core_sleeve_satellite_headroom(...)` (`def` @ **3434**) —
is consumed at three places that never compare it to the execution floor:

* **broker.py:15369–15396** — it caps how much index core is *released* to fund
  buys, logging at 15387:
  `"[core] funding request trimmed $X -> $Y — satellite headroom will refuse the remainder; releasing core for it would only be bought back"`.
* **broker.py:15903–15958** — it caps `cash_per_trade` for the buy itself
  (`cash_per_trade = _sat_room`, **15958**).
* **broker.py:16515** — the floor then refuses anything below `NAV × 0.06`.

One tick, fully reconstructed from `bt_fb2_full.log` (2026-01-09):

```
9539 | [core] funding request trimmed $2,582 -> $167 — satellite headroom will refuse
       the remainder; releasing core for it would only be bought back
9546 | SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $167 of room out of the core
9547 | SATELLITE CAP: SNDK trimmed $861 -> $167 to keep the core at target
9548 | TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy ...
9549 | Buy gate inputs for SNDK: cash=$181.68 ... cash_per_trade=$167.04
       available=$181.68 cash_to_use=$167.04 → PASS
9550 | DISPLACEMENT: trimming URA ($973.14) to fund SNDK raw=+1.700
9551 | SKIP BUY SNDK — cash_to_use $167.04 < min $369 (allocated $167.04)
9818 | DISPLACEMENT EXECUTE: trimming 40% of URA ($973.14) to free $368.86 for SNDK
```

Read the chain: the core refused to release $2,415 *because* headroom was $167;
the satellite trimmed the buy to $167; the floor refused $167 for being under
$369; and then a **real sell of 40% of URA was scheduled** (line 14571–14577 sets
`sell_fraction` and adds the symbol to `_nexus_sell_set`) to fund a buy that had
already been refused on the same tick. `allocated $167.04` in the SKIP line is
`cash_per_trade` **after** line 15958 overwrote it — direct proof the trim, not
the allocator, produced that number.

The release path's stated justification at 15389–15390 — *"releasing core for it
would only be bought back"* — is **false** in this configuration. The buy is not
refused for headroom; it is refused for being under the floor. Releasing *more*
would have made it fillable.

**Magnitude.** Parsing the whole log: 68 `SKIP BUY … < min $N` lines, 53
`SATELLITE CAP: … trimmed` lines, and only **19 `FILL BUY`** for the whole run.
Of the 68 refusals, **45** are preceded within 30 log lines by a SATELLITE CAP
trim of the same symbol, and in **32 of those the trim target was itself below
the floor that then refused it** — i.e. the trim was arithmetically guaranteed to
fail before it was written. Those 32 requests totalled **$28,272 pre-trim →
$4,555 post-trim** on a ~$6,000 book, across 23 distinct symbols including SNDK,
which was attempted 7 times and **never bought once** in the run.

**Honest qualification.** In all 32 cases the gate line also shows
`available` < floor. Cash was pinned in a $181.68–$182.10 band across **12
consecutive trading dates** (2026-01-09 → 2026-01-26, 21 of the 32 Class-A
refusals), so
the trim was not the *sole* binding constraint at the instant of refusal. The
causal claim I *can* support is the loop: the same headroom number that starved
the buy is the number that stopped the core from releasing the cash
(log 9539), and the release refused *on the grounds that the buy would be
refused*. Whether breaking the loop converts these to fills is not something a
log can prove and I did not run a backtest to test it.

### D2 (HIGH). The refuse-vs-trim threshold is a hardcoded $25 while the real floor is ~$370 — a 15× gap, for the same stated reason.

`broker.py:15907` is the only guard that chooses *refuse* over *trim*:

```
15907   if _sat_room <= _CORE_MIN_SATELLITE_TRIM_USD:
```

`_CORE_MIN_SATELLITE_TRIM_USD = 25.0` (**3255**), whose own comment (3252–3254)
gives the rationale:

> `#: Below this much satellite headroom, refuse rather than trim -- a $3 buy is`
> `#: dust that cannot be exited (Alpaca's fractional minimum is $1) and would`
> `#: occupy a max_positions slot forever.`

That is *verbatim* the rationale of the NAV floor at **3732** and **16484–16489**
(`"A position too small to matter must not cost a slot"`). Two constants encode
one invariant, and the one that runs first is 15× too low. On this book the
execution floor was $355–$384 (§C) while this gate let anything above $25
through — which is why 32 trims landed in the $30–$170 dead band.

The `_anchor_policy` branch at **15959–15968** *does* compare the trimmed value
to `_anchor_policy["min_fill"]` — but only when `_anchor_policy` is truthy. This
run has no anchor policy on those symbols (no `ANCHOR …` lines accompany the 32
Class-A refusals), so that guard never fired.

### D3 (MEDIUM, latent). The correct fix cannot be written with the obvious variable name, and the wrong one fails silently rather than loudly.

Stated in §A/§B. Worth recording as a defect and not just a gotcha because the
failure mode at module scope is a **stale value carried across symbols**, not a
crash — `_equity` (16331), `_anchor_nav` (16083) and `_exec_min_pos` (16515) are
all module-scope names bound *after* 15954 under conditions, so a naive
`if cash_per_trade < _exec_min_pos:` inserted at 15954 would silently use the
*previous symbol's* floor and would test green on any symbol that is not first.

### Note (LOW, not in the top 3, flagged for completeness)

`broker.py:16193` computes the cash-reserve floor from **start equity**:
`_cash_floor = portfolio_emulator._initial_value * _cash_floor_pct`. Its sibling
cap 140 lines later carries the opposite instruction (**16329–16330**):
*"Q4 fix: use CURRENT portfolio value (cash + positions) so the cap scales with
equity growth, not stuck at start-equity."* On this run NAV stayed within ±7% of
$6,000 so the observable impact is small, and **I have no log line isolating a
loss to it** — I am not claiming one.

Also observed but outside this aspect and **not evidenced to a root cause here**:
`TURNOVER BUDGET BINDING` appears **615 times** across the 42 trading sessions
of this run (`grep -c 'Entering trading session'` = 42; e.g. `bt_fb2_full.log:9540`: *"109% of NAV in accepted-order request notional
over the last 21 sessions"*), and of 7 `DISPLACEMENT EXECUTE` target symbols
(`CAH, CCK, META, TPG, TSLA, TSM, URA`) only `META`, `TSLA` and `TSM` appear in
`Nexus sell enforcement` lines. Both deserve their own investigation.

---

## E. Proposed fixes (concrete, scope-correct)

### E1 — hoist one NAV read and one floor for the whole buy block

Insert immediately after `if decision == 1:` (**broker.py:15835**), i.e. *before*
the SATELLITE CAP block at 15903, so both the cap and the later gate read the
same number. Uses only names proven in scope (§B1/§B2), and fails open exactly
like the existing gate (3845/3851):

```python
# NAV and the execution floor, computed ONCE at the top of the buy block.
# `nav` does not exist at module scope in this file (every binding is
# function-local); recompute it here from the two names that ARE in scope.
_buy_nav = 0.0
_buy_floor = _EXEC_MIN_POSITION_USD
try:
    _buy_cfg = _core_sleeve_cfg_raw(_cached_strategies) or {}
    if float(_buy_cfg.get("min_position_nav_pct", 0.0) or 0.0) > 0:
        _buy_nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
        _buy_floor = _exec_min_position_floor(_buy_cfg, _buy_nav)
except Exception:
    _buy_nav, _buy_floor = 0.0, _EXEC_MIN_POSITION_USD
```

Default-OFF is preserved: with `min_position_nav_pct` absent, `_buy_floor`
stays $50 and every comparison below is a no-op relative to today.

### E2 — make the SATELLITE CAP refuse instead of producing a dead trim (fixes D1/D2)

Replace `broker.py:15907` and `15954–15958` so the refuse-vs-trim threshold is
the *floor*, not the $25 constant:

```python
# was: if _sat_room <= _CORE_MIN_SATELLITE_TRIM_USD:
if _sat_room < max(_CORE_MIN_SATELLITE_TRIM_USD, _buy_floor):
    _log(f"SATELLITE CAP: {symbol} skipped — room ${_sat_room:,.0f} is below "
         f"the ${_buy_floor:,.0f} execution floor; trimming to it would only "
         f"be refused at the gate", "yellow")
    ... existing _anchor_reinforcement_block(...) ...
    continue
```

and leave `15954–15958` unchanged — with the guard above, any surviving trim is
already ≥ the floor, so `cash_per_trade = _sat_room` can no longer write a
number the gate will refuse. This removes all 32 Class-A refusals *and* stops the
displacement arm from scheduling a real sell (14571–14577) to fund an
already-dead buy.

### E3 — stop the core release from starving a buy on a false premise (fixes D1's other half)

At `broker.py:15377–15396`, `_fr_capped` is the release amount. Add, inside the
existing `try` (15147) using the same in-scope recompute:

```python
_fr_nav = float(portfolio_emulator.get_portfolio_value(prices) or 0.0)
_fr_floor = _exec_min_position_floor(
    _core_sleeve_cfg_raw(_cached_strategies) or {}, _fr_nav)
if 0.0 < _fr_capped < _fr_floor:
    # The stated reason for trimming — "the buy would only be bought back" —
    # does not hold: the buy dies at the FLOOR, not at headroom. Either release
    # enough to clear the floor, or release nothing and spend no turnover.
    _fr_capped = 0.0
    _log(f"[core] funding release cancelled — capped release ${_fr_capped:,.0f} "
         f"is under the ${_fr_floor:,.0f} execution floor", "cyan")
```

(Deliberately conservative: cancel rather than round up, so this cannot increase
core turnover. Rounding *up* to `_fr_floor` when `_fr_room_conv` still has slack
is the aggressive variant and should be tested separately.)

### E4 — collapse the two constants (fixes D2 permanently)

`_CORE_MIN_SATELLITE_TRIM_USD` (**3255**) and `_EXEC_MIN_POSITION_USD` (**3729**)
encode one invariant — *"a position too small to matter must not cost a
max_positions slot"* — with two numbers 2× apart in the OFF case and 15× apart in
the ON case. Make 15907 read `_exec_min_position_floor(...)` (E2) and keep
`_CORE_MIN_SATELLITE_TRIM_USD` only as the OFF-case dust floor.

### E5 — a regression test that would have caught this

`backend/tests/test_exec_min_position_floor.py` already drives
`_exec_min_position_gate` directly. Add the missing composition test: a book with
`min_position_nav_pct=0.06`, NAV $6,000 (floor $360) and satellite headroom
$167 must **not** emit a buy request of $167 — today it does, and the gate then
refuses it. No such test exists; the two halves are tested independently and
agree with themselves.

---

## F. What I did NOT establish

* I did **not** run a backtest, so I cannot state the P&L effect of E1–E4.
* I could **not** show that the SATELLITE CAP trim was the *sole* binding
  constraint on any single refusal: in all 32 Class-A cases `available` was also
  below the floor (§D1). The evidence supports a *loop* between the core release
  and the satellite cap, not a single unilateral cause.
* I did **not** locate the config file for run 718107, so
  `min_position_nav_pct = 0.06` is **inferred** from `floor / NAV` arithmetic
  (§C) plus the value used in the two dedicated test modules. It is consistent to
  four significant figures across 21 distinct floor values but it is an
  inference, not a direct read.
* `bt_fb2_full.log` reports `PIT RESEARCH MODE: ... This result carries lookahead
  bias and is NOT promotion-eligible` (line 10). Absolute returns from this run
  are not trustworthy; the *counts and mechanics* cited above are.
* Per session memory, paired re-runs in this repo are not comparable (warm
  sentiment cache, un-cleanable pre-window DB rows), so any A/B validation of
  these fixes needs a clean-state protocol first.
