# The allocator and the executor disagree about the minimum position size

**Aspect assigned:** minimum-position-size consistency between the allocator
(`backend/strategies/graph_nexus_analysis.py`) and the executor (`backend/broker.py`).

**Evidence base.** Exactly two things: the log `/tmp/bt523085.log` (40,345 lines, backtest
id **523085**, window 2026-01-01 -> 2026-02-28, $6,000 start, final P&L
`+$360.22` = **+6.00%**, L40342) and the source under `backend/` as it stands in the
working tree. **No backtest was run.** Nothing outside this file was written. Nothing was
pushed.

Citations: `L<n>` = 1-based line number in `/tmp/bt523085.log`; `file.py:<n>` = source line.
Where a claim cannot be supported from those two sources it is listed in **section 7,
"What I could not show"**, and is not asserted anywhere else.

Two config values are *inferred*, not read from a config dump:

* `min_position_nav_pct = 0.06`. Every `SKIP BUY ... < min $F` line gives `F`; dividing by
  0.06 reproduces the allocator's own `portfolio_total=$N` from the same bar to within
  ±$8 across all 43 paired bars (the log prints the floor with `%.0f`, so ±0.5 of floor
  = ±$8.33 of NAV). Example: L7123 `min $373` -> $6,216.67; L6948 (the same bar's allocator pass) logged
  `Budget split: portfolio_total=$6210`.
* `total_spend_cap_target_weight_pct = 0.14`. L436 funds four names at `$840` on the
  `portfolio_total=$6000` of L411; 840/6000 = 0.14. This matches `_conc_target =
  portfolio_total * _conc_target_pct` at `graph_nexus_analysis.py:32760`.

---

## 1. Headline

**There are 16 distinct minimum-position numbers in `backend/`. They are not one floor;
they are four incompatible floors spread across two files, applied in an order that
guarantees the strictest one runs last.**

The binding contradiction is a three-way one, and all three numbers are shares of the
*same* NAV:

| Rule | Value on this book | Where |
|---|---|---|
| Allocator target weight a new name is funded at | **14.0% of NAV** (~$840) | `graph_nexus_analysis.py:32760`, `32841` |
| Executor minimum a new name may open at | **6.0% of NAV** (~$370) | `broker.py:3732-3752`, called at `broker.py:16515` |
| Executor satellite headroom actually available | **2.4%-4.1% of NAV** ($154-$260) | `broker.py:3434-3525`, applied `broker.py:15954-15958` |

The third is smaller than the second on 44 of the 47 occasions it bound. The satellite cap
is nevertheless allowed to trim a buy down into that band, because its own floor for
"is there enough room to bother trimming?" is `_CORE_MIN_SATELLITE_TRIM_USD = 25.0`
(`broker.py:3255`) — **$25, i.e. 0.4% of NAV, 14.8x below the floor the same file enforces
600 lines later.**

Result in bt 523085: **47 SATELLITE CAP trims, 0 fills.** 44 were refused by the execution
floor on the next diagnostic line; the other 3 were intercepted one step earlier by the
turnover budget. $38,493 of pre-trim intent and $8,757 of post-trim clip were destroyed by
two numbers inside the same file disagreeing.

---

## 2. The complete inventory of minimum-position constants and functions

Ordered by file. "Lane" = which buy path reads it. All values are as of the current
working tree.

### 2a. `backend/broker.py` — the executor

| # | Name | file:line | Value / formula | Lane that uses it |
|---|------|-----------|-----------------|-------------------|
| E1 | `_EXEC_MIN_POSITION_USD` | `broker.py:3729` | `50.0` (const) | Base of E2; also the "is the NAV floor armed?" sentinel in E3 (`hard = floor > _EXEC_MIN_POSITION_USD`) |
| E2 | `_exec_min_position_floor(cfg, nav)` | `broker.py:3732-3752` | `max(50.0, nav * cfg["min_position_nav_pct"])`; falls back to `50.0` on malformed cfg or `nav<=0` | The one execution-time floor. **~$361-$383 in this run** |
| E3 | `_exec_min_position_skips(...)` | `broker.py:3801-3822` | `decision==1 and not held and fundable < floor and (hard or cash_to_use < cash_per_trade)` | The refusal predicate |
| E4 | `_exec_min_position_gate(...)` | `broker.py:3825-3877` | Owns E2+E3; NAV from `portfolio_emulator.get_portfolio_value(prices)` (`:3848`); `held` exemption (`:3854-3864`); `fundable = _exec_fundable_amount(...)` **only if `pct > 0`** (`:3866-3867`) | Every `decision == 1` symbol, called once at `broker.py:16515-16518` |
| E5 | `_exec_fundable_amount(...)` | `broker.py:3754-3798` | `min(cash_to_use, get_buying_power(sum(_execution_cash_reservations)))` | The number E3 measures against. Identity for any adapter without `get_buying_power` |
| E6 | `_exec_min_pos_preview` | `broker.py:16456` | **hardcoded `50.0`** | *Diagnostic only.* Decides the `-> PASS` / `-> SKIP` word on the `Buy gate inputs for X` line (`broker.py:16457, 16470`) |
| E7 | `_CORE_MIN_SATELLITE_TRIM_USD` | `broker.py:3255` | `25.0` (const) | Satellite cap: `if _sat_room <= 25: skip` else `trim` (`broker.py:15907`, `15954-15958`) |
| E8 | `_anchor_policy["min_fill"]` | `broker.py:3625-3627, 3646` | `max(50.0, cfg["min_position_size"])` = **$100** | Anchor-reinforcement lane only; read at `broker.py:15319`, `15962`, `16360`, `16526` |
| E9 | `_exec_min_pos` passed to `_displacement_candidate` | `broker.py:16588` | `= E2` | Displacement search target (consistent with E2) |

### 2b. `backend/strategies/graph_nexus_analysis.py` — the allocator

| # | Name | file:line | Value / formula | Lane that uses it |
|---|------|-----------|-----------------|-------------------|
| A1 | `_min_position_size` | `gna.py:28761` | `config["min_position_size"]`, default `100.0` = **$100** | Seed for A4, A6, A7, A8 |
| A2 | `_min_pos_final` | `gna.py:32612-32637` | `max(config["min_position_size"], portfolio_total * config["min_position_nav_pct"])` = **$360** on a $6,000 book | The allocator's *only* NAV-aware floor. Read at `:32843` (concentrate drop), `:32878` (uniform-scale drop), `:32909` (P3 final-pass drop) |
| A3 | `_conc_floor` | `gna.py:32788` | `_conc_target` if `total_spend_cap_target_weight_pct > 0` else `_min_pos_final` = **$840** | Concentrate lane. Strictest floor in the system |
| A4 | `_bfq_min_pos` | `gna.py:31942` | `= _min_position_size` = **$100** | Backfill-queue drain: sizing (`:32168`, `:32211`, `:32229`), cash-starvation trigger (`:32348`), rotation budget (`:32519`, `:32543`), log (`:32054`, `:32249`) |
| A5 | `priority_min_position_size` | `gna.py:10144`, `10657`, `11145` | `config["priority_min_position_size"]`, default `100.0` = **$100** | BFQ + slate priority bypass |
| A6 | `candidate_min_position_size` | `gna.py:10683-10687` | `min(min_position_size, priority_min_position_size)` for priority names with `raw>=0.50`, else `min_position_size` | Slate *feasibility* test (`:10725`) and per-slot `min_required` (`:10740`, `:10746`) |
| A7 | `min_required` (BFQ) | `gna.py:10151` | `priority_min_position_size if is_priority_size_override else min_position_size` | `_plan_backfill_buy_allocation` admission (`:10155-10161`) |
| A8 | `_etf_min` | `gna.py:30755` | `max(_min_position_size, config["etf_min_position_size"])` = **$100** | ETF sleeve only (`:30757`, `:30845`, `:30847`) |
| A9 | `_direct_reserve_alloc(cfg, min_pos, budget)` | `gna.py:9268-9273` | `max(min_pos, budget * direct_reserve_alloc_pct)`; `min_pos` supplied as A4 = $100 | Direct-reserved backfill drain (`:31991`, `:32008`) |
| A10 | `_slot_min_notional(cfg, pv)` | `gna.py:9276-9280` | `pv * slot_min_notional_pct/100`; default `0.0` = **OFF** | BFQ path only (`:32001`, `:32256`). Called "half-dead, backfill-path only" by `broker.py:4039` |
| A11 | `min_position_size=` arg to the winner-add / anchor planners | `gna.py:30386`, `30401` | `= _min_position_size` = **$100** | ADD lanes (exempt from E3 by design) |
| A12 | `min_position_size=` arg to `_plan_executable_stock_buy_slate` | `gna.py:30682` | `= _min_position_size` = **$100** | Primary new-entry slate |

**Sixteen numbers. Four distinct values on this book: $25, $100, $360/$370, $840.**

---

## 3. Every pair that can disagree, with the numeric example

A pair "disagrees" when one site admits a dollar size the other refuses. Ordered by
measured damage in bt 523085.

### PAIR 1 — E7 ($25) vs E2 ($370). **44 refusals. The single largest entry-refusal bucket in the run.**

`broker.py:15906-15958`:

```
if _sat_room is not None:
    if _sat_room <= _CORE_MIN_SATELLITE_TRIM_USD:      # 25.0  (:15907)
        ... continue                                    # honest skip
    ...
    if cash_per_trade > _sat_room:
        _log("SATELLITE CAP: ... trimmed ...")          # (:15955)
        cash_per_trade = _sat_room                      # (:15958)
```

`cash_per_trade` is then the *whole* downstream sizing basis (`cash_to_use = cash_per_trade`
and `min(cash_per_trade, available)`; the comment at `broker.py:15947-15953` says so
explicitly). ~560 lines later, `broker.py:16515` runs E4 with floor E2 and refuses at
`broker.py:16612`.

**A trim is admitted at $25.01 and refused at $369.99. The 344.98-dollar gap between the
two constants is where 44 buys died.**

Full ledger — every trim that reached the floor. `NAV` is `floor / 0.06`:

| trim line | sym | pre-trim | post-trim | post-trim %NAV | refusal line | floor |
|---|---|---:|---:|---:|---|---:|
| L6152 | `GBDC` | $929 | $213 | 3.44% | L6155 | $372 (6.00%) |
| L7120 | `COP` | $869 | $208 | 3.35% | L7123 | $373 (6.00%) |
| L7128 | `TTWO` | $869 | $208 | 3.35% | L7131 | $373 (6.00%) |
| L8058 | `GLUE` | $872 | $205 | 3.29% | L8061 | $374 (6.00%) |
| L8991 | `SNDK` | $860 | $221 | 3.60% | L8994 | $368 (6.00%) |
| L9909 | `LLY` | $871 | $212 | 3.41% | L9912 | $373 (6.00%) |
| L9917 | `SNDK` | $871 | $212 | 3.41% | L9920 | $373 (6.00%) |
| L10827 | `GLUE` | $871 | $211 | 3.39% | L10830 | $373 (6.00%) |
| L10835 | `SNDK` | $871 | $211 | 3.39% | L10838 | $373 (6.00%) |
| L11767 | `SNDK` | $890 | $191 | 3.00% | L11770 | $382 (6.00%) |
| L11775 | `WDC` | $890 | $191 | 3.00% | L11778 | $382 (6.00%) |
| L11783 | `ON` | $890 | $191 | 3.00% | L11786 | $382 (6.00%) |
| L12721 | `ASML` | $889 | $194 | 3.06% | L12724 | $381 (6.00%) |
| L19216 | `FDX` | $893 | $250 | 3.92% | L19219 | $383 (6.00%) |
| L19227 | `AXTI` | $893 | $250 | 3.92% | L19230 | $383 (6.00%) |
| L20123 | `AMZN` | $894 | $253 | 3.96% | L20126 | $383 (6.00%) |
| L20131 | `NVDA` | $894 | $253 | 3.96% | L20134 | $383 (6.00%) |
| L20139 | `TE` | $894 | $253 | 3.96% | L20142 | $383 (6.00%) |
| L21034 | `BA` | $891 | $256 | 4.02% | L21037 | $382 (6.00%) |
| L21045 | `SNDK` | $891 | $256 | 4.02% | L21048 | $382 (6.00%) |
| L21988 | `SKYT` | $884 | $260 | 4.12% | L21991 | $379 (6.00%) |
| L22928 | `BBSI` | $881 | $260 | 4.13% | L22931 | $378 (6.00%) |
| L25562 | `LLY` | $849 | $186 | 3.07% | L25565 | $364 (6.00%) |
| L25570 | `VTYX` | $849 | $186 | 3.07% | L25573 | $364 (6.00%) |
| L27302 | `CYTK` | $843 | $196 | 3.26% | L27305 | $361 (6.00%) |
| L27310 | `BIIB` | $843 | $196 | 3.26% | L27313 | $361 (6.00%) |
| L28200 | `FDX` | $843 | $199 | 3.31% | L28203 | $361 (6.00%) |
| L29104 | `MAR` | $861 | $182 | 2.96% | L29107 | $369 (6.00%) |
| L30003 | `GFS` | $876 | $169 | 2.70% | L30006 | $375 (6.00%) |
| L30868 | `FDX` | $856 | $175 | 2.86% | L30871 | $367 (6.00%) |
| L30876 | `ABNB` | $856 | $175 | 2.86% | L30879 | $367 (6.00%) |
| L31807 | `NVDA` | $867 | $166 | 2.68% | L31810 | $372 (6.00%) |
| L32681 | `V` | $863 | $169 | 2.74% | L32684 | $370 (6.00%) |
| L32689 | `FRT` | $863 | $169 | 2.74% | L32692 | $370 (6.00%) |
| L33579 | `NVDA` | $872 | $163 | 2.61% | L33582 | $374 (6.00%) |
| L33587 | `CCEP` | $872 | $163 | 2.61% | L33590 | $374 (6.00%) |
| L34491 | `ABBV` | $870 | $168 | 2.70% | L34494 | $373 (6.00%) |
| L35375 | `ABBV` | $880 | $158 | 2.51% | L35378 | $377 (6.00%) |
| L36246 | `GILD` | $887 | $157 | 2.48% | L36249 | $380 (6.00%) |
| L37133 | `ADEA` | $882 | $154 | 2.44% | L37136 | $378 (6.00%) |
| L37987 | `LLY` | $875 | $166 | 2.66% | L37990 | $375 (6.00%) |
| L37995 | `ABBV` | $875 | $166 | 2.66% | L37998 | $375 (6.00%) |
| L38897 | `ALC` | $874 | $172 | 2.75% | L38900 | $375 (6.00%) |
| L39803 | `ERIC` | $880 | $163 | 2.59% | L39805 | $377 (6.00%) |

Summary of the 44: pre-trim intent **$38,493**, post-trim clip **$8,757**, post-trim clip
range **$154-$260** (median **$192.50**), as a share of NAV **2.44%-4.13%** (median
**3.07%**) against a **6.00%** floor. **44 of 44 were below the floor. Not one was above it.**

The 3 remaining trims never reached the floor because a different gate fired first — they
are *not* counterexamples:

* L3383 `TPG trimmed $839 -> $423`, then L3384 `TURNOVER BUDGET BLOCK: TPG skipped`.
  ($423 would have cleared that bar's floor; see §5.)
* L4281 `V trimmed $864 -> $358`, then L4282 `TURNOVER BUDGET BLOCK: V skipped`.
  ($358 < that bar's floor of $370 — L4289 — so it was doomed anyway.)
* L5213 `AIFD trimmed $870 -> $355`, then L5214 `TURNOVER BUDGET BLOCK: AIFD skipped`.
  ($355 < the $373 floor visible at L5231.)

**Zero of 47 satellite trims produced a fill in this run.** Independent confirmation from
the fill ledger: the 11 non-core BUY fills are ROKU $839.97, RTX $839.97, SBLK $839.97,
VICR $839.97 (L2905-L2908), BALL $870.13, CCK $833.25 (L5472-L5473), DTE $950.67, EFX
$956.55 (L18566-L18567), SNDK $825.10, AMZN $863.79, ETN $725.31 (L24977-L24979). **Every
single one is at or near the 14% target. The $370-$840 band is empty. Nothing trimmed ever
survived.**

### PAIR 1b — E7 ($25) vs E2, upstream: the core *sells SPY* to fund the doomed clip

`broker.py:15369-15396` caps the core's funding release to the same `_sat_room` and logs
`[core] funding request trimmed $X -> $Y`. `Y` is byte-identical to the trim value:

* L7107 `[core] funding request trimmed $3,478 -> $208`
* L7120 `SATELLITE CAP: COP trimmed $869 -> $208`
* L7123 `SKIP BUY COP — fundable $206.38 of cash_to_use $207.99 ... < min $373`

The comment at `broker.py:15354-15355` states the intent — *"not releasing core for a buy
the execution gate will refuse"* — but that reasoning is wired **only** to the anchor
turnover ceiling (`broker.py:15348-15358`). The min-position floor was never wired in.
41 `[core] funding request trimmed` lines; 11 `[core] released` executions totalling
**$5,022.06** of SPY sold; **13 of the 44 refused trims occurred on a tick where the core
had already released SPY before the refusal.** Concretely: L6147 released $122.45 of SPY,
then L6152 trimmed GBDC to $213, then L6155 refused it.

### PAIR 2 — E6 ($50, hardcoded) vs E2 ($370). **The log lies on every buy.**

`broker.py:16456-16471`:

```
_exec_min_pos_preview = 50.0
_will_skip = cash_to_use < _exec_min_pos_preview and cash_to_use < cash_per_trade
...  f"-> {'SKIP' if _will_skip else 'PASS'}"
```

Two independent divergences from the real gate (E3, `broker.py:3821-3822`):

1. it uses `50.0` instead of E2 (`$361-$383` here);
2. it keeps the `cash_to_use < cash_per_trade` truncation clause, which the real gate
   **drops** whenever `hard` (`floor > 50`) is true.

Measured: **78 `Buy gate inputs for X` lines in the run. 78 printed `-> PASS`. Zero printed
`-> SKIP`. 67 of the 78 were refused by `SKIP BUY` on the next line.** The `cash_to_use`
values on those 67 lying-PASS lines run **$90.20 to $950.58**, median **$196.21** — i.e.
every one of them is above $50 and therefore *cannot* print SKIP. Examples:

* L4288 `Buy gate inputs for BKR: ... cash_to_use=$121.69 -> PASS` / L4289 `SKIP BUY BKR — cash_to_use $121.69 < min $370`
* L6154 `... GBDC ... cash_to_use=$90.20 -> PASS` / L6155 `SKIP BUY GBDC — ... < min $372`
* L7122 `... COP ... cash_to_use=$207.99 -> PASS` / L7123 `SKIP BUY COP — ... < min $373`

This is a pure observability defect, and it is the reason the floor is easy to miss: the
only per-symbol PASS/SKIP line in the log is structurally incapable of reporting the floor
that is doing the refusing. (Note the warning in this task's brief about empty reason
strings; the failure mode here is the mirror image — a *populated, confident, wrong* verdict.)

### PAIR 3 — A1/A4/A5/A6/A8/A12 ($100) vs A2 ($360): the NAV floor is a post-hoc filter, not a sizing input

`_min_pos_final` (A2) is computed at `gna.py:32612`. Every new-entry sizing lane runs
*before* that line:

| lane | sizes against | source line | runs at |
|---|---|---|---|
| primary stock slate feasibility | A6 = $100 | `gna.py:10725` | called `gna.py:30679` |
| ETF sleeve | A8 = $100 | `gna.py:30755-30757` | `gna.py:30753` |
| backfill queue drain | A4 = $100 | `gna.py:31942`, `32168` | `gna.py:31700`-`32325` |
| direct-reserved drain | A9 (`min_pos` = $100) | `gna.py:9273`, `31991` | `gna.py:31991` |
| **NAV floor `_min_pos_final`** | **$360** | **`gna.py:32612-32637`** | **after all of the above** |

Observable consequences in the log:

* **60** `Backfill queue BUY: X (queued Nd, alloc=$Y ...)` lines. `Y` ranges **$100-$286**,
  median **$100**; **60 of 60 are below the $360-$385 floor.** 45 of them are exactly $100.
  First occurrence L4068 `Backfill queue BUY: V (queued 2 bars, alloc=$100, score=1.000 HIGH-CONV)`.
* **43** `V28 BFQ DRAIN ENTRY` lines, **all 43** printing `min_pos=$100`
  (e.g. L434). **220** `V28 BFQ ALLOC=0 ... min_pos=$100` lines.
* L419 `ETF min-size filter: funded 3 ETF(s) @ $133 each, skipped 1: ['XLE'] (min=$100)` —
  the ETF lane funds at $133 while the executor's floor for the same bar is 6% of $6,000 = $360.
* `_bfq_is_cash_starved = _bfq_cash < _bfq_min_pos` (`gna.py:32348`) uses **$100**, so with
  $150-$359 of queue cash the drain believes it is funded and does **not** trigger the
  BFQ rotation that would free a real position — the one lane that could have produced a
  fundable clip.

**The $100 numbers printed in the log are never the size that executes.** V is allocated
`$100` at L4068 and arrives at the broker on the same tick with `cash_per_trade = $864`
(L4281 `SATELLITE CAP: V trimmed $864 -> $358`). The cause is `gna.py:32841`:
`_cc_want = max(_cc_cash, _conc_target)` — the concentrate lane silently *raises* every
survivor to the 14% target, which is why the P3 undersized guard at `gna.py:32901-32922`
**never fires in this run** (0 occurrences of `P3 undersized guard` in 40,345 lines) and
why `SLOT MIN-NOTIONAL` never fires either (0 occurrences; A10 is 0 = OFF).

So in *this* configuration PAIR 3 is masked at the last moment by the concentrate lane.
That masking is load-bearing and undocumented: turn `total_spend_cap_concentrate` off
(`gna.py:32756`) and the uniform-scaling branch at `gna.py:32861-32884` takes over, which
*does* enforce `_min_pos_final`, but only *after* the slate has already chosen how many
names to fund using the $100 feasibility test at `gna.py:10725`. The selection is made at
$100 and the funding is judged at $360.

### PAIR 4 — E3's held-ADD exemption vs E7, which has no such exemption

`_exec_min_position_skips` returns `False` for a held name (`broker.py:3819`, docstring
`:3808-3812`: the exemption exists because *"an add takes no slot"*, and it was added
after it refused SNDK's $216 and WDC's $586 winner-adds). The satellite cap 600 lines
earlier has no equivalent exemption and kills the add outright:

* L7918 `ANCHOR ADD: VICR stage=1 +$172 (held 7d, pnl +20.4%, ... entry $840, raw 1.300)`
* L8047 `[BROKER] VICR @ 2026-01-09 ...: buy action_intent=winner_add_buy`
* L8048 `SATELLITE CAP: VICR skipped — satellite at its design share ($-1,353 room)`

**All 3 `winner_add_buy` intents that reached the broker in the whole run were killed by
`SATELLITE CAP ... skipped`** (of the 36 such skips: 30 `backfill_queue_buy`,
3 `winner_add_buy`, 2 `initial_buy`, 1 `momentum_watchlist_rotation`). The exemption E3
grants is unreachable for this lane.

### PAIR 5 — A3 ($840) vs E2 ($370): the executor would admit what the allocator refuses

`gna.py:32843`: `if _cc_take < _conc_floor or _cc_take < _min_pos_final: drop`. With
`_conc_floor = $840`, the allocator refuses to open **any** new name below 14% of NAV.
The executor's floor is 6%. **A $423 position is refused by the allocator and admitted by
the executor.** The only observed instance is L3383 `TPG trimmed $839 -> $423`, which was
then blocked by the turnover budget (L3384), so the fill did not occur — but the branch is
live: any `_sat_room` in `[$370, $840)` produces exactly this. In bt 523085 the concentrate
lane dropped **25 of 143** candidates across 43 bars (e.g. L436 `funded 4 of 8 ... dropped
4 to the queue`).

### PAIR 6 — E8 ($100) vs E2 ($370), latent

`_anchor_policy["min_fill"] = max(50.0, cfg["min_position_size"])` = $100
(`broker.py:3625-3646`), checked at `broker.py:15319`, `15959-15968`, `16360`, `16523-16536`
— i.e. **before** E4 at `:16515`. For a NEW name the anchor lane would admit at $100 and E4
would then refuse at $370. In bt 523085 this cannot be observed: `anchor_reinforce_execution_enabled`
is off (0 `ANCHOR PLAN` lines, 0 `ANCHOR BLOCK` lines, 0 `ANCHOR SATELLITE ADMIT` lines),
and the lane's own targets are held names, which E3 exempts. **Source-only claim.**

### PAIR 7 — A5/A6/A7 vs A1, latent

`gna.py:10151` and `gna.py:10683-10687` let a priority candidate use
`priority_min_position_size` *instead of* `min_position_size` when
`priority_budget_can_bypass_regular_min` (default `True`) and `raw >= 0.50`. Because both
keys are `100.0` in the shipped schema (`gna.py:1`), this is a no-op today. If
`min_position_size` is ever raised to match the NAV floor, this branch reopens the hole at
$100. **Source-only claim; no log evidence exists because the two values are equal here.**

---

## 4. The refusal funnel, for scale

Reconciled exactly from L-counts:

* **121** buy intents reached the broker execution loop
  (`[BROKER] <SYM> @ <ts> ($px): buy action_intent=...`):
  `initial_buy` 53, `backfill_queue_buy` 49, `momentum_watchlist_buy` 15,
  `winner_add_buy` 3, `momentum_watchlist_rotation` 1.
* **36** killed by `SATELLITE CAP: X skipped` (headroom `<= $25`; observed room values
  **-$1,426 to -$306**, median **-$1,339** — every one negative, so E7's $25 threshold was
  never the operative number on a *skip*, only on a *trim*).
* **7** killed by `TURNOVER BUDGET BLOCK`.
* **78** reached `Buy gate inputs` (all `-> PASS`, see PAIR 2); **67** then hit `SKIP BUY`.
* **11** executed. `121 - 36 - 7 - 67 = 11`.
* Of the 67 `SKIP BUY`, **44** had `allocated < floor` — and all 44 are exactly the
  satellite-trim cases of PAIR 1 (`allocated` equals the post-trim `_sat_room` to the cent:
  L7120 trim `-> $208` / L7123 `allocated $207.99`; L8058 `-> $205` / L8061 `$205.20`;
  L8991 `-> $221` / L8994 `$221.24`).

**The single largest cause of entry refusal in this run is two constants inside
`broker.py` disagreeing with each other.**

---

## 5. Why the fix is not "lower the floor"

The floor is doing its job: it is the only thing stopping a 2.5%-of-NAV position from
taking one of `max_positions` slots, which is the documented reason it exists
(`broker.py:16475-16489`, `gna.py:32613-32630`). The defect is that three *other* sites
size positions without knowing the floor exists, and one site reports on the floor using a
number that is not the floor.

The arithmetic that must hold and does not:

```
satellite_design_share*NAV - satellite   >=   max(50, min_position_nav_pct*NAV)
        ^ _sat_room (broker.py:3525)             ^ _exec_min_position_floor (broker.py:3752)
```

When it fails, **no new alpha name can open at all**, and the correct behaviour is to say
so once, not to trim 44 buys into a band where they are guaranteed to be refused, and
certainly not to sell $5,022 of SPY to fund them.

---

## 6. Proposed fixes

Ordered by measured damage. Each is a change at one site.

### FIX 1 (PAIR 1 + 1b) — make the satellite cap use the execution floor as its trim threshold

`broker.py:15903-15968`. Hoist the floor once per tick and use it in place of
`_CORE_MIN_SATELLITE_TRIM_USD` for NEW names:

```python
# once per tick, next to the other per-tick hoists
_tick_exec_floor = _exec_min_position_floor(
    _core_sleeve_cfg_raw(_cached_strategies),
    portfolio_emulator.get_portfolio_value(prices))

# broker.py:15906, replacing the `<= _CORE_MIN_SATELLITE_TRIM_USD` test
_sat_held = float((portfolio_emulator.get_positions() or {}).get(symbol, 0.0) or 0.0) > 0.0
_sat_floor = _CORE_MIN_SATELLITE_TRIM_USD if _sat_held else max(
    _CORE_MIN_SATELLITE_TRIM_USD, _tick_exec_floor)
if _sat_room <= _sat_floor:
    _log(f"SATELLITE CAP: {symbol} skipped — ${_sat_room:,.0f} room < "
         f"execution floor ${_sat_floor:,.0f}; a trim to this size would be refused",
         "yellow")
    continue
```

The `_sat_held` branch preserves E3's held-ADD exemption (PAIR 4) rather than extending
the refusal to adds. Then apply the *same* `_tick_exec_floor` at `broker.py:15381-15385`
so `_fr_allow_plain` / `_fr_allow_conv` are zeroed when the resulting room could not fund
a single position — this stops the core releasing SPY for a buy that cannot clear, which
is precisely what the comment at `broker.py:15354-15355` already says the lane must not do.

Expected effect on bt 523085: 44 `SKIP BUY` become 44 honest `SATELLITE CAP ... skipped`,
11 of the 13 SPY releases on those ticks do not happen, and the `_broker_skipped_buys`
feedback (`broker.py:16617-16627`) stops mislabelling them `insufficient_cash` — they are
not cash-starved, they are room-starved, and the backfill queue currently re-queues them
on that wrong reason.

### FIX 2 (PAIR 2) — delete `_exec_min_pos_preview` and move the diagnostic after the gate

`broker.py:16453-16474`. The diagnostic currently runs *before* the gate it claims to
preview. Move the `_log` block to immediately after `broker.py:16515-16518` and print the
gate's own outputs:

```python
(_emp_skip, _exec_min_pos, _emp_fundable, _emp_held) = _exec_min_position_gate(...)
_log(f"Buy gate inputs for {symbol}: cash=${_cash_now:.2f}{_bp_part} "
     f"reserved=${reserved_total:.2f} floor=${_cash_floor:.2f} "
     f"effective_floor=${_effective_floor:.2f} high_conv={_high_conviction} "
     f"open_pos={_open_positions} cash_per_trade=${cash_per_trade:.2f} "
     f"available=${available:.2f} cash_to_use=${cash_to_use:.2f} "
     f"min_pos=${_exec_min_pos:.2f} fundable=${_emp_fundable:.2f} held={_emp_held} "
     f"-> {'SKIP' if _emp_skip else 'PASS'}",
     "yellow" if _emp_skip else "cyan")
```

There is then exactly one min-position number in the executor and no mirror of it.
`broker.py:3723-3725` already argues this case verbatim — *"Hoisted out of the
per-symbol buy block so the decision is one testable function instead of an expression the
tests can only MIRROR. A mirrored gate is how the runt leak survived two fixes: the mirror
agreed with itself."* (`backend/tests/test_exec_min_position_floor.py:17-21` says the same
of the test copy.) `_exec_min_pos_preview` is exactly that mirror, still living in the buy
block, still disagreeing.

### FIX 3 (PAIR 3) — hoist `_min_pos_final` above the sizing lanes and thread it through

`graph_nexus_analysis.py`. Move the `_min_pos_final` computation from `:32612-32637` up to
immediately after `_min_position_size` at `:28761` (both `portfolio_total` (`:28628`) and
`config` are already bound there), then substitute it at the four NEW-ENTRY sites:

* `:30682` `_plan_executable_stock_buy_slate(..., min_position_size=_min_pos_final, ...)`
  — so the feasibility loop at `:10725` picks *fewer, larger* names instead of a slate it
  can only fund at $100 each.
* `:30755` `_etf_min = max(_min_pos_final, config.get("etf_min_position_size", _min_pos_final) ...)`.
* `:31942` `_bfq_min_pos = _min_pos_final` — which also repairs
  `_bfq_is_cash_starved` (`:32348`) so BFQ rotation triggers at the real floor.
* `:9273` `_direct_reserve_alloc` then inherits it via its `min_pos` argument.

**Leave `:30386` and `:30401` (the winner-add / anchor ADD planners) on
`_min_position_size`** — adds are exempt from the floor by design (`broker.py:3808-3812`),
and raising them would re-break SNDK/WDC.

This makes the `alloc=$100` log lines honest and removes the dependence on the concentrate
lane's `max(_cc_cash, _conc_target)` (`:32841`) silently repairing the size afterwards.

### FIX 4 (PAIR 5, smaller) — reconcile `_conc_floor` with the execution floor

`gna.py:32788`. `_conc_floor = _conc_target` means the allocator refuses every position
between 6% and 14% of NAV while the executor admits them. Either drop the target to the
floor for the *last* funded slot, or state the invariant explicitly:
`assert _conc_floor >= _min_pos_final` with a loud log when the two are set inconsistently.
I have no log evidence that this one costs money in bt 523085 (see §7), so it should be
last.

---

## 7. What I could not show

Stated explicitly, as required.

1. **NAV-basis divergence between allocator and executor: NOT DEMONSTRATED.** The allocator
   takes NAV once per bar (`_portfolio_value_with_fallback`, `gna.py:28628`); the executor
   re-reads it per symbol mid-tick (`broker.py:3848`). Across all 43 bars where both are
   observable, `floor/0.06` differs from the logged `portfolio_total` by **-$7.33 to +$8.00**
   (median +$1.00) — entirely inside the `%.0f` rounding of the printed floor. **I cannot
   claim these two disagree materially in this run.**
2. **The default-OFF runt leak.** `broker.py:3866-3867` only calls `_exec_fundable_amount`
   when `pct > 0`. With `min_position_nav_pct` absent, the historical $50 floor is measured
   against the *request*, not against what the emulator will fund — the exact defect
   `_exec_fundable_amount`'s own docstring (`broker.py:3757-3776`) says caused AVY to open
   at $47.36. bt 523085 has the pct armed, so **I have no log evidence for this; it is a
   source-only reading.**
3. **`slot_min_notional_pct` (A10):** 0 occurrences of `SLOT MIN-NOTIONAL` in the log. It is
   0 (OFF) in this config. **Cannot show it firing or not firing correctly.**
4. **`priority_min_position_size` bypass (PAIR 7):** both keys are `100.0`, so the branch is
   a no-op. **No log evidence either way.**
5. **Anchor `min_fill` (PAIR 6):** the execution lane is disabled in this run. **Latent, source-only.**
6. **P&L attribution.** I have *not* shown that fixing any of these raises the return. I
   have shown that 44 sized buys totalling $38,493 of pre-trim intent were destroyed by a
   constant mismatch and that $5,022 of SPY was sold on those ticks. Whether the 44 names
   would have been profitable is not answerable from this log, and I make no such claim.
   In particular, comparing these counts to any other backtest is invalid for the reasons
   already recorded in `docs/investigations/agent-golden-diff.md`.
7. **Live-mode behaviour.** `_exec_fundable_amount` takes the identity path for any adapter
   without `get_buying_power` (`broker.py:3778-3780`), so PAIR 1/2 are reasoned entirely
   from a backtest log. **I have not verified them against a live log.**

---

## 8. One-line summary

`_CORE_MIN_SATELLITE_TRIM_USD = 25.0` (`broker.py:3255`) and
`_exec_min_position_floor` (`broker.py:3732`, ~$370 here) are both in `broker.py`, 560
lines apart, and neither knows about the other; the satellite cap trimmed 47 buys into the
$154-$260 band and the execution floor refused 44 of them, while a hardcoded `50.0` at
`broker.py:16456` made all 78 of the run's gate diagnostics print `PASS`.
