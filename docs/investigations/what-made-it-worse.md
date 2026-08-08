# What made it worse — 820236 (+12.33%) → 718249 (+4.23%) → 613166 (+9.17%) → 725146 (+0.11%)

Read-only forensic pass. Nothing was edited, nothing pushed, no backtest started or stopped.
Every number below comes from `/backtests/<id>/summary` or from a log line pulled with
`python3 scripts/pull_backtest_logs.py <id> --filter '<regex>'`. Code references are
`file:line` against the working tree as of this session.

---

## 0. THE HEADLINE, STATED PLAINLY

**No. These four runs are not comparable, and the levers are not the main story.**

820236 and 718249 ran the same window, the same instance, the same cash, the same
granularity and the same `history_scope_id`, and they hold **zero satellite names in
common out of a union of 18**. They also did not see the same *input data*: the
60-day momentum signal that produced 820236's single biggest winner was silently
returning **`0.0` for 11 of the 12 names discovered on bar 1** of 718249.

The 8.10pp drop from 820236 to 718249 is dominated by a **shared price-bar cache that a
different backtest (342380) overwrote in between the two runs**. The levers moved the
book at the margin. They did not move it 8 points.

One lever (`turnover_budget_conviction_bypass_max_pct=0.8`) is measurably harmful and I
can price it. One lever (`rank_band_momentum_exempt_min_score=0.8`) **never fired once
in three runs**. One "lever" the brief lists (the concentrate `price unresolved`
deferral) was **already present in 820236 and in 264179** — it is not new.

---

## 1. THE RUNS, AS THE ROWS REPORT THEM

All: instance `v2-let-run-core`, granularity 3600, cash $6,000, strategy 193,
window 2026-01-01 → 2026-03-01 (342380 excepted).

| bt | status | return | end value | maxDD | trades | round trips | held names |
|---|---|---|---|---|---|---|---|
| 264179 | finished | **+9.31%** | $6,558.69 | 6.24% | 34 | 2 | CPER GDX IQM SNDK SPY SYNA TE TXN |
| 820236 | finished | **+12.33%** | $6,739.61 | 8.33% | 38 | 2 | BA CORD CPER LRCX OMER SNDK SPY WDC |
| 718249 | finished | **+4.23%** | $6,253.84 | 8.01% | 39 | 5 | ABBV AGMI AMD AMZN C CLH DTE EFX ETH MSFT SPY XOM |
| 613166 | finished | **+9.17%** | $6,549.91 | 7.00% | 37 | 3 | AGMI AMZN EGO HESM NTR NVDA PLD PLRZ SNDK SPY |
| 725146 | **stopped @ 79.65%** | **+0.11%** | $6,006.73 | **11.40%** | 32 | 4 | AGMI CYTK LLY NVDA SPY WDC |
| 342380 | finished | +18.71% | $7,122.67 | 7.88% | 32 | 2 | IQM SPY SQQQ USO (window 03-02→03-30) |

### Correction on 725146

The brief says "NEGATIVE". **The row says `pnl_percent = 0.1121`, `portfolio_end_value =
6006.7265524614895`, `status = "stopped"`, `progress = 79.65`.** It was stopped on
**2026-02-17**, eight trading sessions short of the window end, holding six positions with
open marks (`Monitor decision: AGMI day 40 pnl=+11.2%`, `WDC day 19 pnl=+5.3%`,
`CYTK day 8 pnl=+5.8%`, `LLY day 8 pnl=-1.9%`, `NVDA day 20 pnl=-2.8%`, 725146 log tail).

What *is* true and is worse than "negative": it **peaked at $6,618.23 (+10.30%) on
2026-01-26 16:00 and gave the whole thing back**, touching $5,863.80 (−2.27%) on
2026-02-05. That is an **11.40% max drawdown, the worst of the five**, and it is the only
number in this set that should worry anyone.

A stopped run cannot be compared to four finished runs on return. Delete it from the
ladder or re-run it to completion.

---

## 2. RUN-TO-RUN DIVERGENCE — HOW MUCH IS NOT THE LEVERS

### 2a. The books have nothing in common

Held-name overlap, SPY (the index core, always held) excluded — `|A∩B| / |A∪B|`:

|          | 264179 | 820236 | 718249 | 613166 | 725146 |
|---|---|---|---|---|---|
| **264179** | 7/7 | 2/12 | **0/18** | 1/15 | 0/12 |
| **820236** | 2/12 | 7/7 | **0/18** | 1/15 | 1/11 |
| **718249** | 0/18 | 0/18 | 11/11 | 2/18 | 1/15 |
| **613166** | 1/15 | 1/15 | 2/18 | 9/9 | 2/12 |
| **725146** | 0/12 | 1/11 | 1/15 | 2/12 | 5/5 |

**820236 ∩ 718249 = ∅.** The parent's premise is confirmed. It is worse than that:
**264179 ∩ 718249 = ∅** too, and the "best" and "second best" live-faithful runs
(820236, 264179) share only 2 names out of 12.

### 2b. The result is one name each time, and it is a different name

| bt | top name | its P&L | share of total P&L | top-2 share |
|---|---|---|---|---|
| 264179 | GDX | +$275.16 | 49% | 79% |
| 820236 | **WDC** | **+$450.49** | **61%** | 93% |
| 718249 | AGMI | +$340.22 | **134%** (losers eat the rest) | 223% |
| 613166 | AGMI | +$341.72 | 62% | 95% |

Four runs, four different single names carrying the number, with n = 2–5 round trips
each. `docs/OBJECTIVE.txt`: *"n=5 round trips is not evidence."* This ladder is five
samples of one draw each.

### 2c. The opening basket differs before any lever can act

Bar 1 (2026-01-01), the `[CONCENTRATE]` allocator line and the first fills:

| bt | bar-1 executable slate | funded | first 4 satellite fills (2026-01-02) |
|---|---|---|---|
| 264179 | RIG, SYNA, TXN, VAL, CPER | RIG@$840 CPER@$840 SYNA@$840 TXN@$840 / $3,780 | GDX $840, CPER $840, SYNA $1,080, TXN $840 |
| 820236 | **LRCX, RIG, CPER, GDX, WDC** | **WDC@$840** LRCX@$840 RIG@$840 CPER@$840 / $3,780 | CPER $1,080, LRCX $840, **WDC $840**, CORD $733 |
| 718249 | AGMI, ETH, MSFT | AGMI@$876 ETH@$840 MSFT@$840 AIQD@$840 / $3,780 | ETH $840, MSFT $840, XOM $837 |
| 613166 | AGMI, HESM, NTR, NVDA | AGMI@$840 HESM@$840 NTR@$840 NVDA@$840 / $3,780 | NVDA $840, HESM $835, NTR $840 |
| 725146 | AGMI, HESM, NTR, NVDA | AGMI@$840 HESM@$840 NTR@$840 / **$2,880** | HESM $1,014, NTR $840 |

The SPY core leg is identical in all five — `BUY SPY qty=3.51184282 price=682.970444`,
$2,398.48, quote `2026-01-02 16:00:00`. **Every satellite differs.**

Equity curves (percent from $6,000, daily last):

| date | 264179 | 820236 | 718249 | 613166 | 725146 |
|---|---|---|---|---|---|
| 01-07 | +3.52 | +3.10 | +0.38 | −0.81 | −0.82 |
| 01-13 | +5.69 | +5.11 | +3.38 | +2.16 | +3.76 |
| 01-29 | +10.46 | +13.57 | +5.10 | +7.75 | +7.43 |
| 02-10 | +7.59 | +9.35 | −0.19 | +3.31 | −1.08 |
| 02-26 | +9.67 | +13.29 | +2.49 | +7.83 | — |

**The ordering is set by day 5.** By 2026-01-07 the spread is already 4.34pp, and at that
point exactly four satellite buys have happened in each run.

### 2d. The noise floor, measured

The cleanest pair available: **264179 (+9.31%) vs 820236 (+12.33%)**. Same window, same
scope, same overlay-bar cache state (both `closes=90`, both 73 symbols refetched), one
config difference (`turnover_budget_conviction_bypass_enabled` — 0 bypass lines in
264179, 19 in 820236). Result: **3.02pp apart, sharing 2 of 12 satellite names.**

**Read that as the floor, not the ceiling.** A 3pp run-to-run spread with an almost
disjoint book means every gap in the ladder below ~3pp is unattributable, and the gaps
above it are only attributable if the *inputs* were held constant — which, per §3, they
were not.

---

## 3. THE ACTUAL CAUSE OF 820236 → 718249: A POISONED PRICE-BAR CACHE

This is the finding. It is not a lever.

### 3a. The signature: the 60-day momentum return went to zero

`Discovered stock (momentum): SYM (20d=..., 60d=...)` on **bar 1, 2026-01-01**:

**820236 / 264179** (top of the list):
```
Discovered stock (momentum): DZZ  (20d=+4.8%,  60d=+141.6%)
Discovered stock (momentum): TE   (20d=+40.7%, 60d=+138.8%)
Discovered stock (momentum): VICR (20d=+20.4%, 60d=+119.6%)
Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%)
Discovered stock (momentum): WDC  (20d=+7.7%,  60d=+37.5%)
```

**718249 / 613166 / 725146** (top of the list):
```
Discovered stock (momentum): AGMI (20d=+30.0%, 60d=+20.5%)
Discovered stock (momentum): PROF (20d=+27.5%, 60d=+0.0%)
Discovered stock (momentum): BOTT (20d=+20.3%, 60d=+0.0%)
Discovered stock (momentum): PILL (20d=+19.6%, 60d=+0.0%)
Discovered stock (momentum): SMTI (20d=+15.9%, 60d=+0.0%)
Discovered stock (momentum): AGPU (20d=+15.9%, 60d=+0.0%)
```

Counted, per bar, `r60 == +0.0%` out of all momentum discoveries that bar:

| bar date | 820236 | 718249 | 613166 | 725146 |
|---|---|---|---|---|
| 2026-01-01 | **0 / 19** | **12 / 13** | **14 / 15** | **14 / 15** |
| 2026-01-06 | 1 / 6 | 7 / 10 | 6 / 9 | 6 / 9 |
| 2026-01-07 | 0 / 6 | 3 / 3 | 4 / 4 | 4 / 4 |
| 2026-01-09 | 0 / 6 | 6 / 6 | 4 / 4 | 4 / 4 |
| 2026-01-12 | 0 / 6 | 4 / 4 | 5 / 5 | 5 / 5 |
| 2026-01-13 | 0 / 6 | 1 / 1 | 3 / 3 | 3 / 3 |
| **2026-01-14** | 0 / 6 | **0 / 6** | **0 / 6** | **0 / 6** |
| max 60d seen on bar 1 | **+141.6%** | +20.5% | +20.5% | +20.5% |

**The 60-day momentum lane was blind for the first nine trading sessions of 718249,
613166 and 725146, and recovered on exactly 2026-01-14.** That is 61 trading sessions
after 2025-10-15.

Corroborated by the regime detector on the same bar:

```
264179/820236:  V31 market regime: chop (raw=chop, proxy=QQQ, closes=90, ret20=-1.24)
718249/613166/725146: V31 market regime: chop (raw=chop, proxy=QQQ, closes=54, ret20=-1.24)
```

Same date, same proxy, same `ret20` — **90 daily closes available vs 54**.

### 3b. Why zero and not an error

Both discovery sources return a silent `0.0` on short history:

- Source 1, `graph_nexus_analysis.py:5210-5212` (`_pct_return` inside `_recent_price_features`):
  ```python
  def _pct_return(lookback: int) -> float:
      if len(closes) <= lookback:
          return 0.0
  ```
- Source 2, `graph_nexus_analysis.py:13898-13899`:
  ```python
  r20 = ((latest - closes[-21]) / closes[-21] * 100.0) if len(closes) > 21 and closes[-21] > 0 else 0.0
  r60 = ((latest - closes[-61]) / closes[-61] * 100.0) if len(closes) > 61 and closes[-61] > 0 else 0.0
  ```

The admission test is `graph_nexus_analysis.py:13864` / `:13900`
`if r20 >= min_20d or r60 >= min_60d:` and the ranking is
`graph_nexus_analysis.py:13920` `candidates.sort(key=lambda x: (-max(x[1], x[2]), ...))`.

doc-193 sets `momentum_discovery_min_20d_return = 10`,
`momentum_discovery_min_60d_return = 25`
(`scripts/doc193_backup_patch_20260808T110842Z.json`, `strategies[0].config`).

So with `r60` pinned at 0, the momentum lane silently degenerates into a **20-day-only
screen ranked on 20-day return**. No warning is logged.

**The repo already knows this.** `graph_nexus_analysis.py:20693-20699`:

> *"the prefetch above only reaches ~55 trading bars back — so most names are unscoreable
> early and ALL 60-day returns come back 0.0, causing adverse selection (weak names pass
> while strong ones trip a parabolic ceiling on a false 0.0) and cache-luck
> nondeterminism."*

The mitigation exists and is **default OFF**: `overlay_bars_min_history_bars`
(`graph_nexus_analysis.py:20700`). It is **not set on doc-193** (absent from every
`doc193_backup_patch_*.json`).

### 3c. What flipped it between the two runs — 342380

Default fetch window, `graph_nexus_analysis.py:20692`:
```python
start = base - timedelta(days=overlay_lookback + int(overlay_lookback * 0.6) + 30)
```
= `date_key − 78 calendar days` ≈ 54 trading sessions. For a 2026-01-01 run that is
2025-10-15, which is exactly `closes=54`.

The cache row is reused only if it covers the request on **both** ends,
`graph_nexus_analysis.py:20992`:
```python
if _end_gap > 7 or _start_gap > 7:
    still_missing.append(sym)   # refetch
```
and every fetch **rewrites** the row's coverage metadata,
`graph_nexus_analysis.py:21020-21022`:
```python
_overlay_bars_cache_set(conn, sym, sym_bars,
                        fetch_start=fetch_start,
                        fetch_end=fetch_end)
```

Now the `Overlay bars:` lines, in wall-clock order:

| bt | wall clock | window start | `fetch_start` used | symbols refetched from Alpaca |
|---|---|---|---|---|
| 264179 | 11:25 | 2026-01-01 | 2025-10-15 | **73** |
| 820236 | 13:01 | 2026-01-01 | 2025-10-15 | **73** |
| **342380** | **14:21** | **2026-03-02** | **2025-12-14** | **183** |
| 718249 | 18:02 | 2026-01-01 | 2025-10-15 | **888** |
| 613166 | 20:00 | 2026-01-01 | 2025-10-15 | 89 |
| 725146 | 21:13 | 2026-01-01 | 2025-10-15 | 70 |

Example lines:
```
342380: [14:22:05] Overlay bars: fetching 15 symbol(s) (2025-12-14 to 2026-08-08)
718249: [18:02:49] Overlay bars: fetching 18 symbol(s) (2025-10-15 to 2026-08-08)
718249:            Overlay bars: cached 243/253 symbol(s) (2025-10-15 to 2026-08-08)
```

The chain, provable from those six rows:

1. Before 342380, the shared `_overlay_bars` rows had been written by an **earlier,
   wider-window run** — `closes=90` at 2026-01-01 implies coverage back to ~2025-08-24,
   which is exactly `2025-11-10 − 78d`, the fetch start of the
   2025-11-10→2026-02-24 runs (216767 and friends). 264179 and 820236 inherited those
   rows and only had to refetch 73 symbols.
2. **342380 (2026-03-02 window) rewrote 183 of those rows with `fetch_start=2025-12-14`.**
3. 718249 then computed `_start_gap = 2025-12-14 − 2025-10-15 = 60 days > 7`, invalidated
   every row 342380 had touched, and **refetched 888 symbols using its own narrow
   `fetch_start=2025-10-15`** — 54 closes at 2026-01-01.
4. 613166 and 725146 reused 718249's narrow rows. Same 54 closes, same dead 60-day lane.

**A bear-window backtest on a different date range, sharing one `history_scope_salt`,
blinded the momentum discovery of the next three runs on a different window.**

### 3d. The price of it — WDC

WDC is 61% of 820236's entire P&L (+$450.49 of +$739.61; WDC+LRCX = 93%). Its bar-1
discovery was:

```
820236 [13:01:53] Discovered stock (momentum): WDC (20d=+7.7%, 60d=+37.5%)
```

`20d = +7.7%` is **below** `momentum_discovery_min_20d_return = 10`. **WDC entered the
book only because the 60-day gate worked.** With `r60 = 0.0` it cannot be discovered at
all.

What WDC did in each run:

| bt | discovered on bar 1? | entry | notional | outcome |
|---|---|---|---|---|
| 820236 | **yes (momentum, 60d=+37.5%)** | **$181.55, 2026-01-02** | $840 | **+$450.49** |
| 718249 | no | never filled | $0 | `MAX_POSITIONS_GATE: blocked WDC (held=6, cap=6)` on 2026-01-29, `cash=$53.45 cash_to_use=$35.98` |
| 613166 | no | never filled | $0 | `MAX_POSITIONS_GATE: blocked WDC (held=6, cap=6)`, `cash=$42.79` |
| 725146 | no | $271.54, 2026-01-29 | $643 | entered **49.6% higher** than 820236 |

WDC ran $172.27 → $278.93 (+61.9%) over the window. 820236 caught it from $181.55.
725146 caught it from $271.54. 718249 and 613166 did not catch it at all.

This is precisely the failure `docs/OBJECTIVE.txt` names first: *"Entry timing — winners
bought late."*

### 3e. Everything else upstream also drifted

Same bar, 2026-01-01, same scope id `4ffd8b13f738660c02bbeed9` in all six runs:

| | 264179 | 820236 | 718249 | 613166 | 725146 | 342380 |
|---|---|---|---|---|---|---|
| `Market trends: N active trends loaded` | 46 | 47 | **42** | **51** | **54** | 45 |
| `Trend buy signals: N tickers` | 81 | 81 | **69** | 69 | 69 | 97 |
| `Trend sell signals: N tickers` | 309 | 309 | **452** | 452 | 452 | 86 |
| distinct trends behind bar-1 discovery | 8 | 8 | **24** | 29 | 30 | — |
| bar-1 `Discovered stock` count | 41 | 41 | 52 | 83 | 88 | 0 |
| `Active-event maintenance current=` | 23 | 6 | 5 | **0** | **0** | 183 |

The trend *names* behind bar-1 discovery share exactly **one** element between 820236 and
718249 (`analyst_pt_actions_mixed_feb25`):

- 820236: `analyst_pt_raises_semiconductors`, `quantum_computing_commercialization`,
  `bitcoin_treasury_accumulation`, `bark_acquisition_offer`, `dividend_increase_signals`,
  `dutch_bros_expansion_momentum`, `analyst_pt_raises_broad`, `analyst_pt_actions_mixed_feb25`
- 718249: `oil_supply_disruption_iran_conflict`, `saudi_oil_output_cut`, `ai_server_demand_surge`,
  `ai_nuclear_energy`, `crypto_options_expiry_rally`, `dry_bulk_m_a_consolidation`,
  `tsmc_foundry_dominance`, `robotaxi_partnerships`, … (24 total)

820236's set is the semiconductor/quantum cluster that supplied WDC, LRCX, SNDK, MU,
AMAT, VICR. 718249's is oil, AI-infra, shipping and crypto — and 718249 duly bought
`ETH` on bar 1 for $840 (realised **−$33**).

Every one of these tables is per-instance Nexus state living under **one shared
`history_scope_id`**, because `history_scope_salt = "let-run-core-193"` is identical in
every `doc193_backup_patch_*.json` and every run logs:

```
History scope: base_instance_id=v2-let-run-core | scoped_instance_id=v2-let-run-core|4ffd8b13f738660c02bbeed9
```

`docs/OBJECTIVE.txt`: *"Nexus state is shared and mutable across runs. Give each arm its
own `history_scope_salt`, and make both arms equally warm or equally cold."* **No arm in
this ladder did that.**

Also, five backtests ran between 820236 and 613166 on that same scope
(342380, 718249, 743064, 513420 — the last two `stopped`). Each mutated it.

---

## 4. THE LEVERS, ONE BY ONE

Log-signature counts across all six runs:

| signature | 264179 | 820236 | 718249 | 613166 | 725146 | 342380 |
|---|---|---|---|---|---|---|
| `max_positions: index-core leg(s) … do not consume a slot` | 0 | 0 | **612** | **612** | **485** | 0 |
| `MAX_POSITIONS_GATE: blocked …` | 12 | **45** | 15 | 12 | **0** | 0 |
| `TURNOVER BUDGET BYPASS: …` (admitted) | 0 | **19** | 18 | 9 | 6 | 0 |
| `TURNOVER BYPASS CEILING: … refused` | 0 | 0 | **28** | **44** | **37** | 0 |
| `Rank band: N momentum name(s) exempt` | **0** | **0** | **0** | **0** | **0** | **0** |
| `Rank band (entry<=…): blocked N buy(s)` — total blocked | 2,833 | 2,833 | 2,387 | 2,522 | 2,009 | — |
| `SATELLITE CAP: … skipped/trimmed` | 73 | 65 | 39 | 42 | 29 | 0 |
| `[CONCENTRATE]` median total-spend cap | — | **$4,157** | $3,829 | $3,911 | **$2,893** | — |
| `skipped as not executable: X(price unresolved)` | **47** | **33** | 48 | 49 | **74** | 0 |

### 4a. `max_positions_exclude_sleeve_legs` — fired 612×, moved the constraint, did not remove it

Code: `broker.py:3268-3278` (`_max_positions_excludes_sleeve`) and
`graph_nexus_analysis.py:5653-5660` (`slot_exclusions`). Log signature:
`max_positions: index-core leg(s) SPY do not consume a slot — alpha book holds N`.

Distribution of `max_positions gate armed: held=N, cap=6` across the run:

| bt | held=6 (at cap) | share of ticks at cap |
|---|---|---|
| 820236 (OFF) | 599 / 634 | **94.5%** — and one of the six was SPY, so the alpha book was 5 |
| 718249 (ON) | 193 / 634 | **30.4%** — all six are alpha names |
| 613166 (ON) | 316 / 634 | 49.8% |
| 725146 (ON + reserve) | 0 / 507 | **0%** |

So the lever did what it claimed: `MAX_POSITIONS_GATE` blocks fell 45 → 15 → 12 → 0, and
the alpha book got its sixth slot.

**It did not buy the winner.** On 2026-01-29 in 718249, the sixth slot was already taken
and the same gate refused WDC anyway:

```
[18:39:08] SATELLITE OVERFLOW: WDC raw=+1.700 >= 1.50 — funding $36 of room out of the core (floor-bounded)
[18:39:08] SATELLITE CAP: WDC trimmed $889 -> $36 to keep the core at target
[18:39:08] TURNOVER BUDGET BYPASS: WDC raw=+1.700 >= 1.50 — admitting a conviction buy through a 79% budget
[18:39:08] Buy gate inputs for WDC: cash=$53.45 … cash_to_use=$35.98 → PASS
[18:39:08] MAX_POSITIONS_GATE: blocked WDC (held=6, cap=6)
```

Same bar, `META` (raw=+1.800) was refused identically. 613166 the same for WDC at
`cash=$42.79`. The binding constraint moved from "5 alpha + SPY" to "6 alpha and $53 of
cash", which is not an improvement for a strategy whose thesis is *"size so one winner
matters"*.

**Verdict: works as specified, benefit unproven on this evidence, no measured harm.**
The extra name it admitted in 718249 was `PLRZ` (blocked 5× by the gate in that run,
bought in 725146 for $659 and realised **−$145**).

### 4b. `turnover_budget_conviction_bypass_max_pct = 0.8` — measurably harmful, and I can price it

Code: `broker.py:3263-3266`. Log signature:
`TURNOVER BYPASS CEILING: X refused despite raw=… — N% of NAV traded is at/over the 80% ceiling`.

Fired **28 / 44 / 37** times in 718249 / 613166 / 725146. Of those, **14 / 25 / 28** were
conviction-grade (`raw >= 1.50`) — i.e. exactly the trades the bypass exists to admit.

Now run it backwards against 820236, where the ceiling did not exist:

```
820236: 19 conviction bypasses admitted; 15 of them at a turnover budget ABOVE 80%
        (104%–125%). Names above 80%: AIR ARWR AXSM BKR CGON FNB GBDC KLAC LLY
        OMER SNDK TENX
718249: 18 admitted, 0 above 80%
613166:  9 admitted, 0 above 80%
725146:  6 admitted, 0 above 80%
```

**All 15 of 820236's above-80% admissions would have been refused by this ceiling.**
The named casualty:

```
820236 [SNDK ×4]  TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction
                  buy through a 105% budget / 104% / 104% / 104%
718249            TURNOVER BYPASS CEILING: SNDK refused despite raw=+1.700 — 88% of NAV
                  traded is at/over the 80% ceiling
```

SNDK fills:

| bt | fills | prices | notional | pnl_per_stock |
|---|---|---|---|---|
| 820236 | 3 | $443.83, $517.69, $679.70 | $491 | **+$100.95** |
| 613166 | 2 | $592.01, $679.70 | $166 | +$3.04 |
| 718249 | **0** | — | $0 | — (refused at 88%, then `price unresolved`) |
| 725146 | **0** | — | $0 | — (deferred 4× `price unresolved`) |

SNDK moved **+166.1%** over the window ($237.33 → $631.54). The ceiling's only measured
effect in this ladder is **removing the mechanism that got SNDK into 820236 for
+$100.95**, i.e. ~1.7pp of the 12.33%.

It also did not reduce turnover. Gross traded notional is flat across the four:
$19,946 (820236) / $21,182 (718249) / $20,859 (613166) / $20,209 (725146). What changed
is *which* names, not *how much*. `TURNOVER BUDGET BINDING` lines went the wrong way —
263 in 820236 → **605** in 718249.

**Verdict: harmful. −$100.95 of directly attributable, named P&L, and zero reduction in
gross churn.** This is the one lever I would remove on the evidence available.

### 4c. `rank_band_momentum_exempt_min_score = 0.8` — never fired. Not once.

Code: `graph_nexus_analysis.py:23008-23021`, log at `:23044-23052`
(`Rank band: N momentum name(s) exempt from the entry band`).

**`grep 'momentum name(s) exempt'` returns 0 lines in all six logs**, across 43 rank-band
bars per run, while the band was refusing 2,387–2,833 buys per run. The lever whose
justification was *"the band refused 2,833 buys including VICR (60d=+119.6%, blocked ×6,
never bought)"* (`scripts/run_validation_suite.py:22-24`) changed that count by **zero**.

Probable cause, stated as a hypothesis with the evidence for it: the exemption reads
`sc.get("momentum_watchlist_score")` at `graph_nexus_analysis.py:23016`, but the only
writer into `scores` for that key is `graph_nexus_analysis.py:28622` —
**~1,000 lines later in the same `run_once` body**. The gate is invoked at
`graph_nexus_analysis.py:27625`, before the stamp exists. Absent key → `0.0` →
`0.0 >= 0.8` is False → the exemption is unreachable on the bar the name first appears.
I have not stepped a debugger; the *fact* is 0 firings in 3 runs × 42 bars.

**Verdict: inert. It cost nothing and did nothing. Do not credit or blame it.**

### 4d. `satellite_conviction_reserve_pct = 0.15` — fired, cost one opening position, exact size known

Code: `core_sleeve.py:276-279`:
```python
reserve = _f(cfg, "satellite_conviction_reserve_pct", 0.0)
if reserve > 0:
    design = max(0.05, design - reserve)
```

It has no dedicated log line, but it has an exact fingerprint in the `[CONCENTRATE]`
budget. Bar 1, same seven candidates, same top three:

```
613166: V31.2 total-spend cap [CONCENTRATE]: funded 4 of 7 by conviction
        (AGMI@$840, HESM@$840, NTR@$840, NVDA@$840) out of $3,780; dropped 3 to the queue
725146: V31.2 total-spend cap [CONCENTRATE]: funded 3 of 7 by conviction
        (AGMI@$840, HESM@$840, NTR@$840)          out of $2,880; dropped 4 to the queue
```

**$3,780 → $2,880 = −$900 = exactly 15.0% of the $6,000 NAV.** One fewer position funded
on the bar that builds the book.

Across the run:

| | 613166 | 725146 |
|---|---|---|
| median `[CONCENTRATE]` cap | $3,911 | **$2,893** (−26%) |
| candidates dropped to queue | 57 over 43 bars (1.33/bar) | 101 over 35 bars (**2.89/bar**) |
| `SATELLITE CAP: … skipped` | 42 | 29 |
| ticks at `held=6` | 316 / 634 | **0 / 507** |
| position count at stop | 7 | 6 |

It did concentrate, as designed: AGMI cost basis $1,085 → **$1,746**, HESM $835 →
$1,014. But the same concentration ran the other way — **PLRZ $86 → $659 cost basis,
realised −$18 → −$145**, and `AIFD` at $844 realised **−$43**. Realised losses in 725146
total **−$202** on three names.

The reserve is also the plausible cause of the 11.40% max drawdown, the worst of the
five: fewer, larger positions with the reserve never actually deployed to a better name.

**Verdict: fires exactly as specified and is exactly as large as advertised. On the one
(truncated) run available it removed a position from the opening basket and doubled the
drop-to-queue rate. n=1, run stopped — not enough to convict, more than enough to say
"do not stack this on top of an unvalidated change".**

### 4e. The `price unresolved` deferral — **this is not a new lever**

The brief lists it as "added after 820236". The log says otherwise:

```
264179: 47 × "(price unresolved)"    ← BEFORE 820236
820236: 33 ×
718249: 48 ×
613166: 49 ×
725146: 74 ×
```

The committed code at HEAD (`git show HEAD:…graph_nexus_analysis.py`, line 31935) has:
```python
if _cc_price <= 0 and _conc_any_priced:
    _conc_skipped.append(f"{_cc_sym}(price unresolved)")
    _conc_dropped.append(_cc_sym)
    continue
```
It was in every one of these five runs. **The removal is uncommitted work in the tree**
(`git diff backend/strategies/graph_nexus_analysis.py`, +15/−12, comment
*"2026-08-08 (bt 725146) NARROWED"*) and has therefore **never been run**.

The deferral is real and it is expensive:
```
718249: … funded 3 of 4 by conviction (C@$846, ENPH@$846, PLRZ@$846) out of $3,809;
        dropped 1 to the queue; skipped as not executable: SNDK(price unresolved)
725146: … skipped as not executable: SNDK(price unresolved), KLAC(price unresolved) [×4 for SNDK]
```
SNDK — the +166% name — was the single most-deferred symbol in 725146.

**Verdict: pre-existing, not a regression, and the pending fix is untested. It plausibly
cost 718249 and 725146 their SNDK entry, alongside the turnover ceiling.**

### 4f. Clearing `GraphNexusActiveEvents` — did not deliver the cache hit it was built for, and poisoned day 1 of 725146

`scripts/reset_backtest_event_state.py:30-38` promises: clear the two state tables, keep
`GraphNexusActiveEventMaintenance`, and the next run replays from a cold baseline and
**hits** the cache.

Measured, 2026-01-01 → the run:

| bt | day-1 `current=` | day-1 live events returned | cache hits over 42 days | LLM maintenance batches |
|---|---|---|---|---|
| 820236 | 6 | 20 | **0** | 42 |
| 718249 | 5 | 21 | **0** | 42 |
| 613166 | **0** (cleared) | 15 | **0** | 42 |
| 725146 | **0** | **0** | **1** | 34 |

```
613166 [20:01:04] Active-event maintenance: date=2026-01-01 | current=0 | candidates=24
613166 [20:01:16] Active-event maintenance: returning 15 live event(s), updates=15 in 11.38s
725146 [21:14:26] Active-event maintenance: date=2026-01-01 | current=0 | candidates=24
725146 [21:14:26] Active-event maintenance cache hit: 2026-01-01 scope=de83e7d59f26...
725146 [21:14:26] Active-event maintenance: reused cached result with 0 live event(s) in 43ms
```

613166 got **zero cache hits** — the clear did not achieve its stated purpose. 725146 got
the one hit, and **the cached document contained 0 live events**, one day behind 613166's
15. Because `current_events` on day N is the maintenance output of day N−1
(`reset_backtest_event_state.py:22-25`), that one-day lag propagated:

```
live events by bar:  613166  15, 25, 42, 57, 75, 91, …   sum over run = 6,625
                     725146   0, 13, 29, 43, 58, 74, …   sum over run = 4,575
```

725146 ran the whole window on a **graph-event set ~25% smaller** than 613166's, purely
because of a stale cache document.

**Verdict: the clear made the two arms *less* comparable, not more. 613166 (cold, 15
events on day 1) and 725146 (cold, 0 events on day 1) are not the same experiment, so the
"613166 vs 725146 isolates the reserve lever" claim is false.**

---

## 5. SO WHICH CHANGE HURT?

Attributing the ladder honestly:

| step | Δ return | how much is lever | how much is noise/inputs |
|---|---|---|---|
| 264179 → 820236 (+9.31 → +12.33) | +3.02pp | 19 turnover bypasses admitted, incl. SNDK ×4 (+$100.95 ≈ 1.7pp) | ~1.3pp unexplained; books share 2/12 names |
| **820236 → 718249 (+12.33 → +4.23)** | **−8.10pp** | ceiling refused 14 conviction buys incl. SNDK (~−1.7pp); slot lever neutral; rank-band lever 0 firings | **the rest.** 60-day momentum dead for 9 sessions, WDC (61% of 820236's P&L) never discovered, 0/18 name overlap |
| 718249 → 613166 (+4.23 → +9.17) | +4.94pp | event-state clear delivered 0 cache hits; ceiling fired *more* (28→44) | essentially all of it — 2/18 name overlap on the same config |
| 613166 → 725146 (+9.17 → +0.11*) | −9.06pp* | reserve: −$900 opening budget, 1 fewer opening position, 2.2× drop-to-queue rate | run **stopped at 79.65%**; day-1 event set 0 vs 15; *not a valid comparison* |

`718249 → 613166` is the decisive one. **Same config, same levers, one state wipe apart,
2 names in common out of 18, and a 4.94pp swing.** That single pair puts the
run-to-run noise band at ≥ 4.94pp on this harness — larger than any lever effect I could
measure.

---

## RANKED: WHAT TO CHANGE

Ordered by measured evidence, not by size of hoped-for effect.

### 1. Set `overlay_bars_min_history_bars` on doc-193 (to ≥ 70)
- **Expected effect:** the 60-day momentum return stops silently returning `0.0` for the
  first ~9 sessions of every 2026-01-01 run. Restores the discovery axis that produced
  WDC (+$450.49, 61% of 820236's P&L) and the whole `60d=+141.6% / +138.8% / +119.6%`
  head of the bar-1 list.
- **Evidence:** `closes=90` vs `closes=54` on the identical bar; `r60 == +0.0%` for
  **12/13, 14/15, 14/15** of bar-1 momentum discoveries in 718249/613166/725146 vs
  **0/19** in 820236; recovery on exactly 2026-01-14 = 61 sessions after 2025-10-15.
  `graph_nexus_analysis.py:13899` returns `0.0` when `len(closes) <= 61`;
  `graph_nexus_analysis.py:20693-20700` documents this exact failure and ships the key
  default OFF; the key is absent from every `doc193_backup_patch_*.json`.
- **Risk:** wider Alpaca fetch, one-off cost. It also invalidates every cached row
  (`_start_gap > 7`), so budget one slow first run.

### 2. Give every run its own `history_scope_salt`, and never interleave windows on one salt
- **Expected effect:** removes the mechanism by which a **bear-window run (342380) blinded
  the next three bull-window runs**. Also removes trend/event/discovery carry-over.
- **Evidence:** all six runs log `scoped_instance_id=v2-let-run-core|4ffd8b13f738660c02bbeed9`;
  `history_scope_salt = "let-run-core-193"` in every config backup; 342380 wrote
  `fetch_start=2025-12-14` rows and 718249 then had to refetch **888** symbols vs 820236's
  **73**; bar-1 active trends 47 → 42 → 51 → 54 and bar-1 discovery overlap between
  820236 and 718249 is **1 ticker (TSEM)** out of 41 and 52.
  `docs/OBJECTIVE.txt` already requires this.

### 3. Remove `turnover_budget_conviction_bypass_max_pct` (set it back to 0)
- **Expected effect:** +$100.95 recovered on SNDK alone on the reference window (~1.7pp),
  plus 14–28 other conviction-grade buys per run stop being refused.
- **Evidence:** `TURNOVER BYPASS CEILING` fired 28/44/37×, of which 14/25/28 at
  `raw >= 1.50`; `TURNOVER BYPASS CEILING: SNDK refused despite raw=+1.700 — 88%` in
  718249 vs `TURNOVER BUDGET BYPASS: SNDK … through a 105% budget` ×4 in 820236;
  SNDK moved +166.1%. Gross traded notional is unchanged by the ceiling
  ($19,946 → $21,182), so it is not buying any churn reduction — `TURNOVER BUDGET BINDING`
  went **263 → 605**.

### 4. Delete `rank_band_momentum_exempt_min_score` or fix its ordering, then re-measure
- **Expected effect:** zero, as configured today. If the ordering hypothesis is right,
  fixing it would expose ~2,400–2,800 blocked buys per run to the exemption — which is a
  large, unmeasured change and must not ship in the same run as anything else.
- **Evidence:** `Rank band: N momentum name(s) exempt` = **0 lines in all six logs**,
  against 43 rank-band bars and 2,387–2,833 blocked buys per run. Read
  `graph_nexus_analysis.py:23016` (reader) vs `:28622` (writer) vs `:27625` (call site).

### 5. Do not judge `satellite_conviction_reserve_pct=0.15` on 725146 — re-run it to completion
- **Expected effect of the lever, measured:** opening `[CONCENTRATE]` budget $3,780 →
  $2,880 (−$900 = −15.0% NAV), 4 → 3 funded positions on bar 1, drop-to-queue 1.33 → 2.89
  per bar, ticks at `held=6` 316 → 0, max drawdown 7.00% → **11.40%**.
- **Evidence:** the two `[CONCENTRATE]` lines quoted verbatim in §4d; concentration ran
  both ways (AGMI basis $1,085 → $1,746; PLRZ realised −$18 → **−$145**).
- **Why not judge it:** 725146 is `status=stopped, progress=79.65`, ended 2026-02-17 with
  six open positions, and its day-1 event set was **0 vs 613166's 15** because of a stale
  `Active-event maintenance cache hit`. Two variables, one truncated sample.

### 6. Stop treating `reset_backtest_event_state.py` as a comparability fix
- **Expected effect:** none today; it delivered **0 cache hits on 613166** and a
  **0-live-event day 1 on 725146**.
- **Evidence:** cache-hit counts 0/0/0/1 across 820236/718249/613166/725146; day-1 live
  events 20/21/15/**0**; run-total live events 6,941/6,555/6,625/**4,575**.
  Either clear all three tables (accept the LLM cost) or clear none — the asymmetric
  clear produced a third state that matches neither arm.

### 7. Before any further lever work: establish the noise floor on this harness
- **Expected effect:** stops 3–5pp of noise being read as lever signal, which is what
  produced this entire ladder.
- **Evidence:** 718249 vs 613166 — **same config, one state wipe apart, 4.94pp** and
  2/18 name overlap. 264179 vs 820236 — one lever apart, **3.02pp**, 2/12 name overlap.
  Two identical-salt reruns of the *same* config would put a number on it. Until then,
  per `docs/OBJECTIVE.txt`, *"n=5 round trips is not evidence"* and every one of these
  runs had 2–5.

---

## WHAT I COULD NOT PROVE

- **Which commit each run executed.** No build/artifact stamp appears in any log
  (`Run once | V32-PHASE3-PATH12-…` is a static string, identical in all six). I inferred
  lever presence from log signatures, and the wall-clock ordering lines up with
  `git log --date=format:'%Y-%m-%d %H:%M'` (`e5958cb` 10:19 slot exclusion,
  `0064f79` 10:28 rank band, `fe544da` 10:39 bypass ceiling, `184f8f5` 14:07 reserve),
  but that is circumstantial.
- **That 342380 is the only writer that narrowed the cache.** I can prove 342380 used
  `fetch_start=2025-12-14` and refetched 183 symbols, and that 718249 immediately
  refetched 888. I cannot rule out another writer in the 15:22–18:02 gap
  (`743064` and `513420` ran later, after 718249).
- **Why `rank_band_momentum_exempt` never fired.** The count is 0; the reader/writer line
  ordering is the hypothesis, not a traced execution.
- **The counterfactual returns.** Nothing here says "820236 would have been +12.33% with
  the ceiling on". I priced the named trades the ceiling refused (SNDK, +$100.95) and
  stopped there.
- **`725146` being "NEGATIVE".** The row reports **+0.1121%**. It was under water
  intra-run (low $5,863.80, −2.27%) and it round-tripped +10.30% → +0.11%, but the final
  stored figure is positive and the run is truncated.
