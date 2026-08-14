# The named winners: where each one is lost

**Scope.** Log `/tmp/bt_fb2_full.log` (48,580 lines), backtest id **718107**, window
**2026-01-01 → 2026-03-01**, 37 observed decision sessions. Source read at
`backend/strategies/graph_nexus_analysis.py` and `backend/broker.py` as they stand in the
working tree. **No backtest was run for this document.** Every claim below cites a log line
number (`L####`) or a source line.

**Result being explained:** `L48529` Final Value $6,292.77, **+4.88%** on $6,000, 19 buys /
17 sells. Of the eight named winners, **one** (AMAT) was ever bought.

---

## 0. Two measurement rules I held myself to

1. **An empty reason string means "evaluated, no match", not "not evaluated."** In this log
   `BREAKOUT SKIP: <SYM> ` with nothing after the symbol is the `"+".join([])` case at
   `graph_nexus_analysis.py:6414`. Counted: **6,314 of 7,340** skips are empty (evaluated,
   no pattern) and **1,026** are `skip:bars=0<25` (genuinely not evaluated). For the seven
   named winners that appear at all, **126 of 126 breakout evaluations were the EMPTY kind** —
   they were really evaluated and really matched nothing. I therefore do *not* claim the
   breakout path was dead; see §6 for what I can and cannot conclude.
2. **Per-name returns below are PARTIAL-WINDOW.** Each is measured from the name's *first*
   priced observation in the log to its *last*, and those windows differ per name (AAOI starts
   01-02, TTMI starts 01-14). They are **not** comparable to the run summary's
   `Stock movement (start -> end)` block at `L48562+`, which is the full 01-01→03-01 window.
   Example of the trap: the summary reports AMAT `$257.02 -> $372.64 (+44.98%)`, but AMAT's
   first observation in the decision log is 01-08 at `$281.70` (`L8450`), so its
   in-universe move is `+32.3%`. Comparing the two would overstate the miss by ~13pp.

---

## 1. Per-name funnel table

Stages: **Disc** = discovered / entered the universe · **Score+** = ever produced a positive
score · **Intent** = ever produced a buy `action_intent` · **Gate** = ever reached the broker
`Buy gate inputs` line · **Fill** = `[execution] FILL BUY`.

| Name | Disc | Score+ | Intent | Gate | Fill | Lost at | Partial-window move |
|---|---|---|---|---|---|---|---|
| **AAOI** | ✅ 01-02 | ❌ | ❌ | ❌ | ❌ | **Scoring (signed negative)** | $37.52 → $70.43 **+87.7%** |
| **VICR** | ✅ 01-01 | ✅ ×7 | ✅ ×8 days | ✅ ×2 | ❌ | **Execution funding** | $109.63 → $198.72 **+81.3%** |
| **VIAV** | ✅ 01-08 | ❌ | ❌ | ❌ | ❌ | **Scoring (no signal at all)** | $18.16 → $29.23 **+61.0%** |
| **SNDK** | ✅ 01-01 | ✅ ×7 | ✅ ×15 days | ✅ ×7 | ❌ | **Execution funding** | $237.33 → $641.26 **+170.2%** |
| **LASR** | ✅ 01-05 | ❌ | ❌ | ❌ | ❌ | **Scoring (no signal at all)** | $37.55 → $57.89 **+54.2%** |
| **TTMI** | ✅ 01-14 | ❌ | ❌ | ❌ | ❌ | **Scoring (no signal at all)** | $98.18 → $104.87 **+6.8%** |
| **AMAT** | ✅ 01-08 | ✅ | ✅ | ✅ | ✅ **01-08 @ $281.51** | — (bought) | $281.70 → $372.65 **+32.3%** |
| **ADI** | ❌ **never** | — | — | — | ❌ | **Universe** | no observations |

### Evidence, name by name

**AAOI — killed by the scorer, which signed it negative 16 times while it ran +87.7%.**
- Discovered `L3533` `Discovered stock (momentum): AAOI (20d=+33.9%, 60d=+3.1%)`; re-listed in
  33 `Nexus discovered: expanding symbols` lines and 15 `Discovered stocks (N active)` lines,
  so it was continuously in the universe.
- Bars loaded: `L4047` `loaded 703 1Hour bars for AAOI`. Breakout-evaluated **26×**, all EMPTY.
- Its 33 decision records are **17 `hold` + 16 `sell_override`** and **zero** buy intents.
- The negative votes are explicit and repeated, e.g. `L4889` `Sell: AAOI (Direct macro_economic
  sentiment=-1 (raw=-1.000, 2 paths))`, `L10507` `supply_disruption sentiment=-1`, `L18269`
  `regulatory sentiment=-1`, `L45731` `earnings sentiment=-1`. Counted: **16 sell-listings, 0
  buy-listings.** The topic label rotates (macro_economic / regulatory / earnings / competition /
  geopolitical / supply_disruption) while the sign never does.
- It never reached `Buy gate inputs`. **This is a scoring-sign failure, not a capital failure.**

**VIAV, LASR, TTMI — killed by the scorer returning nothing at all.**
- Discovered: `L7992` VIAV `(20d=-0.4%, 60d=+50.9%)`, `L4623` LASR `(20d=+12.4%, 60d=+28.6%)`,
  `L12441` TTMI `(20d=+26.9%, 60d=+59.3%)`. Bars loaded for all three (`L8431`, `L5124`, `L12853`).
- Decision records: **VIAV 28/28 `hold`, LASR 32/32 `hold`, TTMI 24/24 `hold`.** Not one buy,
  not one sell.
- LLM/graph signal count: **0 sell-listings and 0 buy-listings for each.** Breakout evaluated
  16 / 13 / 14 times, every one EMPTY. All three of the scorer's independent promotion paths
  (`_finalize_scores` Priority 1 direct sentiment `:21136`, Priority 2 graph propagation `:21145`,
  breakout boost `:21159`) returned nothing, so `fresh_score` stayed at its initialised `0`
  (`:21132`) and the name sat at `hold` for the entire run.
- LASR's **only** entry-stage event in the whole log is `L47887`, dated **2026-02-27 — the last
  session**: `Entry extension gate: LASR recent runup +43.3% > 25% — buy blocked`. It was
  discovered on 01-05 at $37.55, ignored for 37 sessions, and then refused for having risen.

**SNDK — the single worst loss. Everything upstream worked; execution refused it seven times.**
- Discovered `L2431` on 01-01 at `$237.33`. Scored to the very top of the queue:
  `L28426` `BFQ DRAIN ENTRY: ... top10=[VRTX(score=2.300,age=4d), SNDK(score=2.207,age=0d), ...]`.
- **15 distinct sessions with an unfunded/attempted buy** (01-09 … 02-05), of which **7 reached
  the broker gate and 7 were refused**:

  | Date | Price | Gate line | Refusal |
  |---|---|---|---|
  | 01-09 | $363.01 | `L9549` cash_to_use $167.04 → **PASS** | `L9551` `< min $369` |
  | 01-12 | $388.46 | `L10675` $167.17 → PASS | `L10677` `< min $371` |
  | 01-27 | $496.45 | `L22977` $885.69 → PASS | `L22979` `fundable $87.00 … < min $380` |
  | 01-29 | $533.41 | `L25194` $896.31 → PASS | `L25196` `fundable $36.21 … < min $384` |
  | 02-02 | $617.38 | `L27430` $133.76 → PASS | `L27432` `< min $371` |
  | 02-03 | $655.38 | `L28520` $148.31 → PASS | `L28522` `< min $374` |
  | 02-04 | $644.90 | `L29596` $824.34 → PASS | `L29598` `fundable $102.39 … < min $365` |

- Every one is reported back as `Gate skips reported back: SNDK (insufficient_cash)`. On 01-09
  actual cash was **$181.68** and on 01-27 **$1,063.47**; the binding constraint was the minimum
  position floor, not cash. **The reported reason is wrong**, which is why this survived earlier
  passes.
- Cost of the refusals in-window: SNDK went **$363 → $600** across the 15 days it was wanted, and
  **$237 → $683** peak over the run (`L43935`).

**VICR — same execution failure, plus one turnover refusal at the cheapest entry.**
- `L240` discovered 01-01 at `$109.625` `(20d=+20.4%, 60d=+119.6%)`.
- **01-02 @ $115.92** — the cheapest entry available all run — `L4083` `buy
  action_intent=initial_buy`, then `L4085` `TURNOVER BUDGET BLOCK: VICR skipped — 56% of NAV in
  accepted-order request notional over 21 sessions`, `L4086` `(turnover_budget)`. Its score that
  day was 1.300, below the `>= 1.50` conviction bypass that `L11783` later grants it.
- **01-13 @ $134.79** `L11784` gate PASS at $165.52 → `L11786` `< min $373`.
- **01-14 @ $138.17** `L12889` gate PASS at $170.41 → `L12891` `< min $369`.
- Then 5 sessions of `action_intent=deferred_unfunded_buy` (01-15…01-21) while it ran to $157.
  Last observation `L46080` **$198.715**.

**AMAT — the control. It filled for exactly one reason: it was sized above the floor.**
- `L8450` 01-08 `buy action_intent=initial_buy` at $281.70 → `L8453` gate
  `cash=$1023.16 … cash_per_trade=$844.00 … cash_to_use=$844.00 → PASS` →
  `L8696` `FILL BUY AMAT qty=2.98916001 price=281.513199` = **$841 notional**.
- Both later adds were refused by the same sizing machinery: `L13936` `SATELLITE CAP: AMAT
  trimmed $175 -> $146` then `L13939` gate `cash_to_use=$0.00 → SKIP`; `L40506` `SATELLITE CAP:
  AMAT skipped — satellite at its design share ($-743 room)`.
- AMAT is not evidence the pipeline works. It is evidence that a buy fills **iff** it happens to
  land on a tick with a free ~$840 slot.

**ADI — never entered the universe.**
- **Zero occurrences in 48,580 log lines.** Not discovered, not scored, not skipped. The run is
  pure-discovery (`L15` `mcap pre-seed: skipped — empty universe (no operator symbols_list, no
  held positions, no BFQ candidates; pure-discovery mode with cold start)`), so nothing seeded it.
- **I cannot say why from this log.** There is no rejection record for a symbol that never enters
  a scan, so "ADI failed filter X" would be speculation. The only supportable statement is that no
  discovery lane emitted it. `ADI` appears in the repo only at `backend/strategies/earnings.py:240`
  (an earnings-calendar list), not in any discovery universe.

---

## 2. Which single stage loses the most of them

**By name count, the SCORER: 4 of 8** (AAOI, VIAV, LASR, TTMI) never produced a single buy intent
despite being discovered, bar-loaded and repeatedly evaluated. They died before capital allocation
was ever consulted.

**By magnitude, EXECUTION FUNDING: 2 of 8** (SNDK +170.2%, VICR +81.3%) sum to more missed move
(+251.5pp) than the four the scorer lost (+209.7pp), and they are the only names where *every*
upstream stage did its job correctly. That makes execution the highest-value fix: the signal was
already right and was thrown away at the last step.

Both are stated because the honest answer depends on the metric. I did not find a way to rank them
that is not a judgement call, and I am not going to present one as a measurement.

---

## 3. Defect 1 — the SATELLITE CAP trims new buys to a size the executor structurally refuses

**Evidence.** Across the run there are **69 `SKIP BUY` lines against 49 distinct symbols** — versus
**19 fills total**. 43 are the `cash_to_use $X < min $Y` form. **28 of those 43 are immediately
preceded by a `SATELLITE CAP` trim whose output is below the floor that then rejects it:**

```
SNDK  $861 -> $167   floor $369   BELOW      ATI   $887 -> $146   floor $380   BELOW
VICR  $870 -> $166   floor $373   BELOW      ORLY  $883 -> $150   floor $379   BELOW
SKYT  $891 -> $145   floor $382   BELOW      LLY   $844 -> $ 30   floor $362   BELOW
...  (28/28 below; trim output $30–$170, median $149; floor range $361–$382)
```

The trim output band (**$30–$170**) and the execution floor band (**$361–$382**) **do not overlap**.
Every buy the cap trims is dead on arrival. Total intended notional destroyed this way:
**~$20,896 across 28 refused buys.**

**Mechanism.** The refusal is `_exec_min_position_gate` (`broker.py:3825`), whose floor is
`_exec_min_position_floor` (`broker.py:3732`) = `max($50, NAV × min_position_nav_pct)` — 6% of a
~$6.2k book ≈ $370. The trim happens upstream at `broker.py:15957` (`"to keep the core at target"`).
Nothing between them checks that the trimmed size is still executable.

**The claim in the code that this cannot happen is false.** `graph_nexus_analysis.py:32628-32630`
asserts *"Both the total-spend cap and the final-pass undersized guard read `_min_pos_final`, so
raising it here closes every lane at once — including the BFQ paths."* But `_min_pos_final`
(`:32612-32637`, NAV-adjusted → ~$370) is computed **after** the BFQ allocator, which at
`:31942` uses `_bfq_min_pos = _min_position_size`, and `_min_position_size` at `:28761` is the raw
`config["min_position_size"]` = **$100** with **no NAV adjustment**. The log shows the un-adjusted
value in use: `L9426` `BFQ DRAIN ENTRY: … min_pos=$100`, and `L9428` `Backfill queue BUY: SNDK
(queued 1 bars, alloc=$100, score=1.700)`. Two floors, $100 and $370, on the same order path.

**Proposed fix** (`backend/broker.py`, at the `SATELLITE CAP` trim site ~`:15957`):

```python
# Never emit a buy the execution floor must refuse. Ask the SAME function
# the executor will use, and either fund at the floor or refuse honestly.
_, _exec_floor, _, _held = _exec_min_position_gate(
    1, symbol, trimmed, trimmed, _cached_strategies, portfolio_emulator, prices)
if not _held and trimmed < _exec_floor:
    if headroom >= _exec_floor:          # room exists, the trim was just too aggressive
        trimmed = _exec_floor
        _log(f"SATELLITE CAP: {symbol} raised ${trimmed:.0f} to the execution "
             f"floor — a trim below it can never fill", "cyan")
    else:                                # genuinely no room: say so, do not emit
        _log(f"SATELLITE CAP: {symbol} skipped — trim ${trimmed:.0f} < execution "
             f"floor ${_exec_floor:.0f} and headroom ${headroom:.0f} cannot reach it",
             "yellow")
        continue
```

and secondarily make the allocator honest by replacing `graph_nexus_analysis.py:31942` with the
NAV-adjusted floor (hoist the `:32633-32637` computation above the BFQ block and use it for
`_bfq_min_pos`), so the queue stops allocating $100 slices it knows cannot execute.

---

## 4. Defect 2 — every buy on a tick is sized against the same cash, so the tick self-cannibalises

**Evidence.** **26 of 26** multi-buy ticks in the run gave *every* sibling the **identical**
`cash_per_trade` measured against the **identical** `available`. The clearest case, 2026-01-29
(`L25181-25204`), four buys each sized $896.31 against $1,019.81 available — **$3,585 requested
against $1,020**:

| Order emitted | Symbol | raw score | cash_per_trade | Outcome |
|---|---|---|---|---|
| 1 | LRCX | +1.800 | $896.31 | **FILL $873** (`L25181`) |
| 2 | META | +1.750 | $896.31 | **FILL $888** (`L25186`) |
| 3 | **SNDK** | **+1.900** | $896.31 | `L25196` SKIP, `fundable $36.21` |
| 4 | ASML | +1.800 | $896.31 | `L25204` SKIP, `fundable $36.21` |

**The highest-conviction name on the tick was emitted third and got $36.21.** The slate is ordered
by source lane (`backfill_queue_buy` before `direct_reserved_buy`), not by conviction. The same
shape repeats on 02-04 (`SNDK raw=+2.136`, highest of three, all three skipped) and 02-03
(`SNDK raw=+2.107`, highest of three, all three skipped).

**Mechanism**, already documented in the tree at `broker.py:3757-3776`: the gate reads `get_cash()`
while `PortfolioEmulator.execute_signal` funds `min(cash_per_trade, get_buying_power(reserved_cash))`,
where `reserved_cash` is this tick's earlier in-flight BUY reservations. The gate cannot see them —
note every gate line prints `reserved=$0.00` even when ~$900 is already reserved. `_exec_fundable_amount`
(`:3754`) was added to *detect* the shortfall, which converted a runt-fill bug into a
**no-fill** bug; it did not stop the over-request.

**Proposed fix** — in `broker.py`, before the buy-emission loop, order and size sequentially:

```python
_buy_slate.sort(key=lambda d: -float(
    (nexus_position_sizes.get(d["symbol"]) or {}).get("raw_net_score", 0.0) or 0.0))
_bp_left = _exec_fundable_amount(portfolio_emulator, float("inf"))
for d in _buy_slate:
    want = min(d["cash_per_trade"], _bp_left)
    if want < _exec_min_position_floor(_core_sleeve_cfg_raw(_cached_strategies), nav):
        _log(f"SKIP BUY {d['symbol']} — ${want:.0f} left after higher-conviction "
             f"names on this tick", "yellow")
        continue
    d["cash_to_use"] = want
    _bp_left -= want
```

On 2026-01-29 this alone buys SNDK at $533.41 instead of LRCX at $244.13.

---

## 5. Defect 3 — the core (SPY) leg is funded first and takes ~85–90% of the tick's cash

**Evidence.** On three separate ticks the **first** alpha name gated already sees almost no
fundable cash, with `reserved=$0.00` displayed:

| Date | SPY fill that tick | avail at gate | first alpha name | fundable | outcome |
|---|---|---|---|---|---|
| 01-27 | $932.38 | $1,063.47 | GILD `L22961` | **$87.00** | all 4 skipped (`L22963/22971/22979/22987`), incl. SNDK @ $496 |
| 02-18 | $1,038.01 | $1,166.37 | CCEP `L40512` | **$76.30** | both skipped (`L40514/40523`) |
| 02-25 | $796.01 | $927.18 | RTX `L45843` | **$86.67** | all 3 skipped (`L45845/45853/45861`) |

`L22949` `max_positions: index-core leg(s) SPY do not consume a slot` confirms the core leg is
processed in the same pass and ahead of the alpha names. **8 of the 19 fills in the entire run are
SPY** (`$2,398 / $117 / $932 / $696 / $718 / $1,038 / $796` + one more), leaving 11 alpha fills in
two months.

**Consequence combined with §3/§4:** fills are bimodal. Every fill in the run is **≥ $696** except
one $117 SPY top-up (median **$840**); every refusal is at **~$370**. There is no middle. The book
can only open a name on a tick where a full ~$840 slot is free *and* the core leg did not take it.

**Proposed fix** (`backend/broker.py`, order of the tick's order slate): emit the core/residual
sleeve leg **last**, after alpha names, and compute its size from the buying power that actually
remains:

```python
_slate = [o for o in _slate if o["symbol"] not in _disp_sleeve] + \
         [o for o in _slate if o["symbol"] in _disp_sleeve]
```

The core is a **residual** by design (`graph_nexus_analysis.py:32670-32678` says so explicitly),
so funding it before the alpha book inverts its own contract.

---

## 6. What I could NOT establish (stated so nobody repeats it as fact)

- **Why the breakout promotion never fires for a name at new highs.** All 126 breakout evaluations
  of the seven named winners were the EMPTY (evaluated, no pattern) kind, including 20 for SNDK
  during a +170% run and 26 for AAOI during +87.7%. That is not credible on its face — the
  `_compute_breakout_score_boost` 5w test (`:6353-6358`) only needs `close >= 0.99 × max(closes[-25:])`.
  But the diagnostic that would answer it, `BREAKOUT NOPATTERN` (`:6406`), emits **0 lines in this
  log** — it was added *after* this run (its own comment cites "bt 718107", this run id). The
  `_breakout_opportunity_audit` (`:20887`) also emitted **0 lines**. **The fallback itself was live**
  (`L281` `BREAKOUT FALLBACK: cache=524 symbols, requested=180, supplied=137`, 43 such lines), so
  "bars never arrived" is *not* the explanation here. **Re-running with
  `breakout_diagnostics_enabled` is the cheapest next measurement** and should precede any change
  to the breakout thresholds.
- **Why AAOI's LLM sentiment is −1 on 16 days across six different topic labels.** The log records
  the verdict, not the prompt or the article set. I can show the sign is wrong against price; I
  cannot show the cause.
- **Why ADI never entered the universe.** No evaluation record exists for it at all.
- **Scoring cadence is a confound I could not separate.** The named winners are breakout-evaluated
  on only 35–70% of the 37 sessions (LASR 35%, TTMI 38%, VIAV 43%, AMAT 46%, VICR 51%, SNDK 54%,
  AAOI 70%). A gate that fires on a day the name is not scored is invisible. This is consistent
  with the previously documented ~43% coverage figure and means **§6 bullet 1 cannot be settled by
  reading this log alone.**

---

## 7. Ranked recommendation

1. **§3 SATELLITE CAP below the execution floor** — 28 provably dead-on-arrival orders,
   deterministic, and the fix is local and testable without a backtest.
2. **§4 parallel sizing / lane-ordered slate** — 26/26 ticks affected; would have bought SNDK on
   01-29 at $533 instead of LRCX at $244.
3. **§5 core leg funded first** — 3 ticks fully wasted; inverts the core's own residual contract.
4. **§6 breakout diagnostics re-run** — measurement, not a fix, but it gates any honest work on the
   four names the scorer loses.

Fixes 1–3 all sit on the path SNDK and VICR actually took; none of them affects AAOI, VIAV, LASR or
TTMI, which never reached that path.
