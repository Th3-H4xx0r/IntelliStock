# Why late, per name — bt 201039 forensics

Read-only investigation of **bt 201039** (`v2-let-run-core`, 2026-01-01 → 2026-03-01,
$6,000, 3600s, `pit_mode=research`, +8.34% / +$500.39), with every claim checked against
**bt 820236** (+12.33%) and **bt 613166** (+9.17%) on the same window.

Logs pulled with `python3 scripts/pull_backtest_logs.py <id> --filter '<regex>' --stdout`
(201039: 40,323 lines, 634 sim bars, 43 analysis bars). "b<N>" below is the sim-bar index
derived from the 634 `PIT RESEARCH MODE: no frozen snapshots for <ts>` lines; `log:NNN` is a
0-based line index into that pulled 201039 log, `L820:NNN` into the pulled 820236 log
(`backtests/820236_20260808-142050Z.log`). `file.py:NNN` is a repo source line.

The run's own effective config, printed 43× and identical on every analysis bar:

```
[GraphNexusAnalysis] Effective config | ts=12.0/12.0/10.0 | pools=10/4 mins=3/2 |
  max_buys=8 max_disc=120 | quality=avgvol>=500000 mcap>=$1000M(block) | prop=2p/0.40 |
  sell=15d/-0.50/6d | bfq=10%/1g/15pg q=60/20 displace=0.3/rec=0.1 halt=10.0%/2d |
  rot=30d/60d/2.00/99.00 | lock=true/5d/5.0%/-0.10/30.0% | break=3.50/2.50/50% |
  discover=watch:0/0 peers:3 comp:3 mom:12 fill:10 prop_slots:40 seedcap:8 | ...
```

---

## 0. The headline number, restated from the run

`[BROKER] ---------- Backtest summary ----------` (log lines 40290–40317):

| name | stock move | our % | our $ | entry VWAP | **% of the move already gone at our entry** |
|---|---:|---:|---:|---:|---:|
| SNDK | +166.10% | **−4.38%** | −$37.73 | $660.48 | **107.3%** |
| WDC  | +61.91%  | **+7.54%** | +$58.17 | $259.37 | **81.7%** |
| PLRZ | +61.84%  | **−17.61%** | −$154.46 | $15.48 | **147.0%** |
| AVNT | +31.40%  | +1.12%   | +$9.77  | $35.64 | 44.7% |
| HL   | +29.70%  | **−18.54%** | −$24.16 | $28.23 | **158.7%** |
| EGO  | +29.26%  | +19.45%  | +$167.35 | $38.87 | 28.1% |
| XOM  | +26.81%  | +26.90%  | +$225.25 | $120.24 | **−0.3%** |
| NTR  | +21.59%  | +21.23%  | +$178.30 | $61.91 | **1.4%** |

`% of the move already gone` = `(entry_vwap − start) / (end − start)`, both prices from the
run's own `Stock movement (start -> end)` block; entry VWAP from the `[execution] FILL BUY`
lines (36 fills total — the complete trade list is short enough to read).

**There is no capture mystery.** Capture ≈ `1 − (fraction of the move consumed at entry)`,
which for a hold-to-end position is close to an arithmetic identity. The free variable is
the entry price, and *only* the entry price. Three names entered at 0–28% of their move and
returned 66–100% of it; five entered at 45–159% and returned −62% to +12%.

The same table for the two comparison runs (same window, same names where they overlap):

| run | name | move | entry VWAP | % of move consumed | our % |
|---|---|---:|---:|---:|---:|
| 820236 | WDC | +61.91% | $181.55 | **8.7%** | **+53.63% (+$450.49)** |
| 820236 | LRCX | +36.71% | $182.26 | 17.7% | +28.36% (+$238.22) |
| 820236 | SNDK | +166.10% | $523.80 | 72.7% | +20.57% (+$100.95) |
| 613166 | AGMI | +32.48% | $67.21 | 2.3% | +31.50% (+$341.72) |
| 613166 | SNDK | +166.10% | $616.76 | 96.3% | +2.39% (+$3.04) |
| 613166 | PLRZ | +61.84% | $14.68 | 131.2% | −21.53% (−$18.47) |

820236's entire edge over 201039 is two entries — WDC at 8.7% and LRCX at 17.7% of their
moves = **+$689 of a +$740 run**. Nothing else in that run is different in kind.

So the question is exactly the right one: **what set the entry price?**

---

## 1. SNDK — the priority name. It was not gated. It was INVISIBLE.

### 1a. bt 201039: first strategy line is 2026-01-30, bar 321 of 634

`grep -n SNDK` over the whole 201039 log returns **exactly one** line before index 21994:

```
log:350   [BROKER] Fetched chunk 1/1 for SNDK: 244 bars (2025-08-19 to 2026-08-08)
```

That is a *broker bar fetch* at b0. The first line in which any strategy names SNDK is:

```
log:21994  b321  2026-01-30T15:00
  [GraphNexusAnalysis] Propagation scoring expansion: 40 ticker(s) added:
    AAPL, INCY, KLAC, PLX, RBLX, RCL, SNDK, TER, USARE, VSEC
```

Bar-by-bar from first discoverability to fill — the whole chronology is **two analysis bars long**:

| bar | date | price | momentum-watchlist rank/score | sized? | what happened |
|---|---|---|---|---|---|
| b0–b320 | 01-01 → 01-29 (22 trading days) | $237.33 → ~$620 | **not in the watchlist, not scored, no rank** | no | **no gate refused it — it did not exist to the strategy** |
| b321 | 2026-01-30 15:00 | (not priced by the broker) | not scored (`top3=[('PLRZ',1.417),('WDC',0.768),('ASTS',0.76)]`) | no | discovered via propagation; `V28 ROTATION RESULT: fired=0/4 ... unfunded=7 (top: SNDK(raw=1.800,age=0d)...)`; `Backfill queue REPLACE: SNDK displaced VOYA (score=1.800)`; then `Promoted buys demoted to queue-only hold: AAPL, KLAC, RCL, SNDK, TER, VSEC, WM` (log:22101, 22109, 22151) |
| b336 | 2026-02-02 15:00 | **$617.375** | **#1, score 1.505** (`new_buys=['SNDK']`) | **yes, $860** | `V31.2 total-spend cap [CONCENTRATE]: funded 4 of 4 by conviction (EGO@$860, SONO@$860, SNDK@$860, META@$860)`; `SATELLITE OVERFLOW: SNDK raw=+1.705 >= 1.50 — funding $1,778 of room out of the core`; `TURNOVER BUDGET BYPASS ... through a 105% budget`; `Buy gate inputs for SNDK: cash=$1766.81 ... cash_per_trade=$860.44 ... cash_to_use=$860.44 → PASS`; **`FILL BUY SNDK qty=1.30271230 price=660.479056`** |

Note what is *absent* from that table: no extension block, no rank band, no
`MAX_POSITIONS_GATE`, no `SATELLITE CAP`, no `insufficient_cash`. SNDK got **100% of
`cash_per_trade`** on the first bar it was ranked. **The portfolio-construction stack never
refused SNDK once in bt 201039.** The loss was already banked before the first gate ran:
the system first *priced* SNDK at $617.375, i.e. **+160.1% off the $237.33 window open, or
96.5% of the way through the move.**

The line that proves the data was there the whole time, and only the *screen* was not:

```
log:346  b0  [GraphNexusAnalysis] Overlay bars: fetching 46 symbol(s) (2025-08-19 to 2026-08-08)
log:350  b0  [BROKER] Fetched chunk 1/1 for SNDK: 244 bars
   (batch = SPCX, MU, NVDA, SNDK, MSFT, PLTR, TSLA, AAPL, AMZN, INTC, GOOGL, META,
    AVGO, LITE, WDC, GOOG, STX, SKHY, MRVL, NBIS, COHR, TSM, LLY, BE, AAOI, ...)
```

SNDK's daily bars were in memory on bar 0. It was never screened.

### 1b. Why: the day-1 momentum screen runs against a pool that does not yet contain SNDK

The bar-0 sequence in 201039 (log lines 176–396), in order:

```
176  Overlay bars: fetching 144 symbol(s)
331  Overlay bars: cached 133/144 symbol(s)
332    Momentum ETF exclusion: dropped 5 leveraged/inverse/commodity ETF candidate(s)
333    Discovered stock (momentum): DZZ  (20d=+4.8%,  60d=+141.6%)
334    Discovered stock (momentum): MAAS (20d=+41.1%, 60d=+61.5%)
335    Discovered stock (momentum): TLSI (20d=+8.4%,  60d=+50.0%)
...344 Discovered stock (momentum): C    (20d=+13.1%, 60d=+19.0%)
345  Momentum discovery: 12 new ticker(s) → symbols now 139
346  Overlay bars: fetching 46 symbol(s)          <-- SNDK, WDC, MU, VICR, VIAV, TSM ... arrive HERE
394  Momentum watchlist: added 132 ticker(s), total=132 (sources: all_discovered=132, breadth_scan=3)
```

The 144-name pool the screen actually scanned is dominated by ETFs and alphabetically-late
tickers (`NRGU, NSIG, NTRB, ... SOXL, SOXQ, SOXS, SOXX, ... XLK, XLP, XLU, XLY, ZJYL`) —
`'SNDK' in pool → False`, `'WDC' in pool → False`, `'MU' in pool → False`. The graph-seed
batch that holds them is fetched **on the next line**, after the screen has already run and
after `Momentum watchlist: added ... (sources: all_discovered=132)` has been computed.

**The control run proves this is the whole story.** bt 820236, same window, bar 0
(`L820:130–172`):

```
L820:130  Overlay bars: fetching 13 symbol(s) (2025-10-15 to 2026-08-08)   <-- warm cache
L820:155  Overlay bars: cached 2/13 symbol(s)
L820:157    Momentum ETF exclusion: dropped 5 leveraged/inverse/commodity ETF candidate(s)
L820:160    Discovered stock (momentum): VICR (20d=+20.4%, 60d=+119.6%)
L820:162    Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%)
L820:167    Discovered stock (momentum): VIAV (20d=+1.6%,  60d=+39.5%)
L820:168    Discovered stock (momentum): WDC  (20d=+7.7%,  60d=+37.5%)
L820:171  Momentum watchlist: added 131 ticker(s), total=131 (sources: all_discovered=132, breadth_scan=3)
L820:266  Entry extension gate: SNDK recent runup +28.5% > 25% — buy blocked
```

Same screen, same date, same universe config — but the shared bar cache was warm with the
semis, so the pool contained them and the screen returned them. Result: WDC bought day 2 at
$181.55 (+$450.49) and SNDK bought 01-20 at $443.83 (+$100.95).

bt 613166 behaves exactly like 201039: `SNDK first-seen bar 321, 2026-01-30, Propagation
scoring expansion` — identical line, identical bar, and SNDK's entry there is $616.76
(96.3% of the move) for +$3.04.

**And SNDK would comfortably have made the cut had it been in the pool.** The screen's
per-day cap is 12 (`Effective config ... discover=... mom:12`) and 201039 bar 0 emitted
exactly 12 — it was saturated. Ranked by 60d, those 12 were:

```
DZZ +141.6 | MAAS +61.5 | TLSI +50.0 | OBIO +49.5 | TNDM +46.7 | PILL +45.4
PROF +38.0 | BBC +35.7 | SBIO +31.5 | AGMI +20.5 | COPP +19.3 | C +19.0
```

820236 measured the same two names off the same market data on the same date:
**SNDK 60d=+95.9% (would rank #2 of 13), WDC 60d=+37.5% (would rank #8)** — both inside the
12-name cap, and both clear the schema thresholds `momentum_discovery_min_20d_return: 15.0`
/ `min_60d_return: 40.0` (SNDK's 20d was +15.6%, i.e. it passed on both legs). The screen
did not reject SNDK on merit. **It never saw it.**

**This is a determinism/coverage defect in the discovery pool, not a name-specific tuning
issue.** Which mega-cap movers the run can see on day 1 is decided by what happens to be
sitting in `strategy_cache["_overlay_bars_raw"]` when `Momentum discovery` executes — i.e.
by which unrelated backtest last warmed the shared cache. Across the three runs it flipped
the biggest winner of the window between "+$450" and "invisible for 22 sessions."

### 1c. The one market-wide channel is throttled and structurally excludes SNDK-class names

`breadth_scan` is the only path from the whole market into the watchlist. Its contribution
in 201039 grows by **exactly 3 per analysis day** — 3, 6, 9, … 126 over 43 bars
(`Momentum watchlist: added N ... breadth_scan=K`). That matches
`breadth_scan_admit_per_bar` default 3 (`backend/strategies/graph_nexus_analysis.py:20861`)
and `breadth_scan_batch_per_bar` default 50 over `breadth_scan_universe_size` 500
(`:20843, :20848`) — one full pass over the universe takes **10 trading days**.

Worse, `graph_nexus_analysis.py:20859–20860` and the filter at `:20876`:

```python
_r20_cap = float(config.get("breadth_scan_r20_parabolic_cap_pct", 60.0) or 60.0)
_r60_cap = float(config.get("breadth_scan_r60_parabolic_cap_pct", 150.0) or 150.0)
...
if _r20 > _r20_cap or _r60 > _r60_cap:
    continue
```

SNDK ran +160% in 22 trading days. Its r20 was above 60% for essentially the whole run-up,
so `breadth_scan` **could not have admitted SNDK on any bar of this window**, at any batch
size. The one channel designed to find "a genuine market mover unconnected to the book"
(its own docstring, `:20781–20787`) has a hard filter that excludes the exact class of name
`docs/OBJECTIVE.txt:8-10` is asking for.

---

## 2. PLRZ — signalled on the FIRST bar at the LOWEST price, and refused by the entry-extension gate

PLRZ is the cleanest evidence in the run because it was in the universe from b0 and the
system emitted a buy intent for it immediately.

| bar | date | price | mw rank / score | sized? | exact refusal |
|---|---|---:|---|---|---|
| b0 | 2026-01-01 00:00 | **$8.11** | **#2, 0.547**, `new_buys=['TCMD','PLRZ']` | no | `V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25% — no conviction bypass` (log:648) |
| b6 | 2026-01-01 15:00 | $8.11 | #2, 0.547, `new_buys=['TCMD','PLRZ']` | no | `Entry extension gate: PLRZ recent runup +106.2% > 25% — buy blocked` (log:2663) + `V32 mw_buy extension-block` (log:2676) |
| b21 | 2026-01-02 15:00 | $8.11 | #2, 0.547, `new_buys=['PLRZ']` | no | `V32 mw_buy extension-block ... +106.2%` (log:3522) |
| b36 | 2026-01-05 15:00 | $11.795 | **#1, 0.889**, `new_buys=['PLRZ']` | no | `V32 mw_buy extension-block ... +97.2%` (log:4333) |
| b51 | 2026-01-06 15:00 | $13.195 | **#1, 0.766**, `new_buys=['PLRZ']` | no | `V32 mw_buy extension-block ... +78.8%` (log:5242) |
| b66–b81 | 01-07 → 01-08 | $13.06 / $12.47 | #2 | no | `Entry extension gate: PLRZ recent runup +83.6% > 25% — buy blocked` (log:7216) |
| b96–b141 | 01-09 → 01-14 | $13.33 → $14.34 | #3/#2, 0.68–0.76 | no | no buy intent (rank slot went to GLUE/RVMD); `Rank band ... blocked 58 buy(s) [UBER, WKEY, PBLS, PLRZ, ...]` on 01-14 (log:10902) |
| b156 | 2026-01-15 15:00 | **$14.50** | **#1, 0.933**, `new_buys=['PLRZ']` | **yes, $938** | `Momentum rotation: sell TCMD (0.348) → buy PLRZ (0.933, $1120)` → `V31.2 rotation cap: PLRZ alloc $1120 → $938` → `[core] funding pre-pass: max_positions will refuse 1 of 1 sized buy(s) (PLRZ) — not releasing core` (log:12000) → **`SATELLITE CAP: PLRZ skipped — satellite at its design share ($18 room); core would be squeezed below target`** (log:12011) |
| b171 | 2026-01-16 15:00 | $15.01 | **#1, 1.13** | **yes, $877** | everything passes: `SATELLITE OVERFLOW ... funding $1,777 out of the core`, `TURNOVER BUDGET BYPASS ... 111% budget`, `Buy gate inputs for PLRZ: cash=$1818.27 ... cash_to_use=$877.05 → PASS` → **`FILL BUY PLRZ qty=56.67227162 price=15.475358`** |

**On the bar PLRZ should have been bought (b0/b6, $8.11 — the lowest price it traded all
window) the thing that stopped it was the entry-extension gate.** On the bar it *was*
bought, what had changed was that the gate had stopped firing (its 20-bar window had rolled
so the range fell under 25%) **and** enough core had been released to clear the satellite
cap. Nothing about PLRZ's momentum score improved the entry — the score was *higher* at
$15.01 (1.13) than at $8.11 (0.547), because the score is itself a trailing-return measure.

Then, 10 sessions later, `[sell-gate] PLRZ | gate=circuit_breaker | tier=LOW | regime=bull
| unrealized=-13.4% | floor=-10.0% | result=fired` (log:21622) → `FILL SELL PLRZ price=12.750734`.
PLRZ closed the window at $13.125. **The stop cost $21.21. The entry price cost $697.**

### The mechanism: the "extension" gate measures a RANGE, not a run-up

`backend/strategies/graph_nexus_analysis.py:9259–9281`:

```python
def _recent_runup_protect(sym, price_history, block_pct, lookback_bars) -> tuple[bool, float]:
    """True when a position's recent close range ran up more than block_pct
    over the last lookback_bars bars ... Used to spare such a name from a forced
    exit at a local dip ..."""
    ...
    lo = min(closes)
    hi = max(closes)
    runup_pct = ((hi - lo) / lo) * 100.0
    return (runup_pct > _bp), runup_pct
```

Three facts follow directly from those four lines:

1. It is a **high/low range**, not a return. It is completely direction-blind: a name at the
   *bottom* of a 106% range is blocked exactly as hard as one at the top. PLRZ at $8.11 —
   the cheapest tick of the window — scored `+106.2%`.
2. It was written to **protect an EXIT** (docstring, `:9262-9264`: "spare such a name from a
   forced exit at a local dip"). It is re-used verbatim to **block an ENTRY** at
   `:23233–23295` (`Entry extension gate`) and `:5545–5558` (`V32 <lane> extension-block`).
   The sign of the intent was inverted; the metric was not.
3. Because it is a trailing window, it *decays*. A name that gaps and then consolidates
   sees its range fall under the threshold — i.e. **the gate is guaranteed to release the
   name after the move, never before it.**

> **Cross-reference.** A parallel read of the same three runs
> (`docs/investigations/extension-gate-inversion.md`) establishes a second, independent
> defect in the same gate: `regime_profiles.bull.entry_extension_block_pct = 0` and
> `regime_profiles.recovery.entry_extension_block_pct = 0`, so the gate is armed **only in
> chop** — 393 fires across the three runs, **zero** under a bull profile. bt 201039 ran
> `V31 market regime: chop` for the window (`Regime capacity gate (Z4.1): regime=chop` ×43),
> which is why every block quoted here did fire. The two findings compose: fixing the metric
> alone changes nothing in a bull, and arming the gate in a bull with the current metric
> would make bull entries *later*. Both must be addressed together.

Measured on every extension-blocked symbol that was later filled anyway, all three runs:

| run | sym | block date | logged "runup" | price at block | fill date | fill price | **entry made worse by** |
|---|---|---|---:|---:|---|---:|---:|
| 820236 | **SNDK** | 2026-01-01 | +28.5% | **$237.33** (L820:266) | 2026-01-20 | $443.83 | **+87.0%** |
| 201039 | **PLRZ** | 2026-01-01 | +106.2% | **$8.11** | 2026-01-16 | $15.48 | **+90.8%** |
| 820236 | OMER | 2026-01-01 | +96.0% | $17.18 | 2026-01-09 | $13.03 | −24.2% (gate helped) |
| 613166 | PLRZ | 2026-01-22 | +115.3% | $15.875 | 2026-01-28 | $14.68 | −7.5% (gate helped) |

n=4 is small and 2 of 4 went the gate's way, but the magnitudes are wildly asymmetric
(+87%, +91% vs −24%, −8%; mean **+36.5% worse**), and in both harmful cases the blocked
price was **the lowest price of the entire window** — which is what a range metric
guarantees whenever a name enters the window already trending.

---

## 3. WDC — visible from bar 0, no buy signal for 14 sessions, then gated for 10 more

WDC is the counter-example that separates "discovery late" from "gating late", because in
201039 it was in the universe from b0 at $172.27.

| bar | date | price | mw rank/score | sized? | exact refusal |
|---|---|---:|---|---|---|
| b0–b141 | 01-01 → 01-14 | $172.27 → $213.46 (**+23.9%**) | not in top-3, never `new_buys` | no | **no buy intent at all** — `[BROKER] WDC @ ... hold action_intent=hold` on every analysis bar |
| b156 | 2026-01-15 15:00 | **$226.08** | not in top-3 | no | first buy score (raw 1.750, direct lane): `V28 ROTATION RESULT: fired=0/4 pairs=[] unfunded=8 (... WDC(raw=1.750,age=0d) ...)` (log:11850) → **`Backfill queue BLOCKED: WDC (full_priority_blocked, score=1.750, source=direct)`** (log:11874) → **`Deferred unfunded buys demoted to hold: C, GS, TSEM, WDC`** (log:11936) → `[BROKER] WDC @ 2026-01-15 15:00:00 ($226.08): hold action_intent=deferred_unfunded_buy` |
| b171–b291 | 01-16 → 01-28 | $217.26 → ~$250 | absent | no | no buy intent |
| b306 | 2026-01-29 15:00 | ~$250 | **#2, 0.765** | no | ranked but the momentum lane's `new_buys=[]` (slot went nowhere; `held_momentum=2`) |
| b321 | 2026-01-30 15:00 | **$266.60** | **#2, 0.768** | **yes, $878** | `Momentum portfolio swap: sell AVNT (pnl=+0.5%) → buy WDC (score=0.768, $878)` (log:22106) → `Backfill queue BLOCKED: WDC (full_priority_blocked)` (log:22116) → but also `V31.2 total-spend cap [CONCENTRATE]: funded 2 of 2 by conviction (INCY@$869, WDC@$878)` → `SATELLITE CAP: WDC trimmed $878 -> $826` → `Buy gate inputs for WDC: cash=$795.09 ... cash_per_trade=$826.41 ... cash_to_use=$795.09 → PASS` → **`FILL BUY WDC price=259.372612`** |

Two separate failures stacked:
* **14 sessions with no signal** while WDC rose +31% ($172.27 → $226.08). The momentum
  watchlist never ranked it; the graph lane had nothing to say. In 820236 the same name on
  the same date *was* momentum-discovered (`WDC (20d=+7.7%, 60d=+37.5%)`) and bought on
  day 2 at $181.55.
* **10 more sessions of gating** ($226.08 → $259.37, +14.7%) after the first buy score,
  killed by `full_priority_blocked` / `deferred_unfunded_buy`.

WDC only got in when a rotation freed both a slot and the cash — see §4.

---

## 4. AVNT — the one name whose ENTRY was fine. We rotated out of it.

| bar | date | price | what happened |
|---|---|---:|---|
| b201 | 2026-01-20 15:00 | $35.425 | discovered same-bar by propagation (`Aggregated: 470 tickers with paths | top raw: AVNT=+1.000(1p)`), sized $873.59, `Buy gate inputs ... cash_to_use=$873.59 → PASS`, **`FILL BUY AVNT price=35.636422`** — 44.7% into a +31.4% move, the best big-name entry of the run |
| b321 | 2026-01-30 15:00 | $35.825 | **`Momentum portfolio swap: sell AVNT (pnl=+0.5%) → buy WDC (score=0.768, $878)`** (log:22106) → `FILL SELL AVNT price=36.037286` |
| b471 | 2026-02-13 15:00 | $42.995 | re-signalled (`Executable stock slate: AVNT`, `V31.2 ... (AVNT@$888, FDX@$888)`) → **`SATELLITE CAP: AVNT skipped — satellite at its overflow ceiling ($-22 room); core would be squeezed below its floor`** (log:31108). Never re-entered. |

Accounting, from the fills: the AVNT sale released $883.35; $795.09 of it bought WDC, $88.26
stayed as cash. End of window: WDC leg $832.49 + $88.26 = $920.75. Holding AVNT instead:
24.51323 × $41.07 = **$1,006.76**. **The swap cost $86.01** — and it is also the mechanism
that made the WDC entry so late, since WDC could not be funded any other way.

The lock that should have stopped it already exists —
`_rotation_winner_lock_active`, `graph_nexus_analysis.py:9348–9370`. The run's own
`Effective config` prints `lock=true/5d/5.0%/-0.10/30.0%`, i.e. enabled, `min_hold_days=5`,
**`min_pnl_pct=5.0`**. AVNT was held 10 days (`d=10`, passes min_hold) at **+0.5%**, so
`graph_nexus_analysis.py:9361` (`min_pnl_pct = float(config.get("rotation_winner_lock_min_pnl_pct", 2.0)...)`)
→ `return False, "min_pnl"` and the lock never engaged. One knob.

---

## 5. HL — bought at the top, at 1/6 of the intended size, then stopped out

| bar | date | price | mw rank/score | sized? | exact refusal |
|---|---|---:|---|---|---|
| b231 | 2026-01-22 15:00 | not priced | **#3, 0.75** | no | not selected for `new_buys` (slot went to GLUE) |
| b246 | 2026-01-23 15:00 | not priced | **#2, 0.94**, `new_buys=['HL']` | no | **`V32 mw_buy extension-block: HL recent runup +65.7% > 25% — no conviction bypass`** (log:17464) and **`V32 mw_swap extension-block`** (log:17472) |
| b261 | 2026-01-26 15:00 | not priced | **#2, 0.991**, `new_buys=['HL']` | no | **`V32 mw_buy extension-block: HL recent runup +68.6% > 25%`** (log:18386) + `mw_swap` (log:18395) |
| b276 | 2026-01-27 15:00 | **$28.525** | #2, 0.876, `new_buys=['HL']` | **yes, $887** | extension gate no longer fires; `V31.2 total-spend cap [CONCENTRATE]: funded 1 of 1 by conviction (HL@$887)` → `SATELLITE CAP: HL trimmed $887 -> $839` → **`Buy gate inputs for HL: cash=$132.00 ... cash_per_trade=$838.79 available=$132.00 cash_to_use=$132.00 → PASS`** → `FILL BUY HL price=28.234510` (**$130.4 = 2.2% of NAV, 15.7% of the intended slot**) |
| b323 | 2026-01-30 17:00 | $23.60 | — | — | `[sell-gate] HL | gate=circuit_breaker | tier=MID | regime=bull | unrealized=-16.4% | floor=-15.0% | result=fired` → `FILL SELL HL price=23.002205` |

HL is the case where the extension gate *appeared* to help and then hurt in a third way: it
blocked HL on 01-23 and 01-26, released it on 01-27 at $28.525 — which is the **highest
price in HL's entire logged series** — and HL ended the window at $24.89, still below our
entry. Sizing HL correctly would have made it *worse* (a full $838.79 slot held to the end
loses ≈$99 instead of ≈$15). **HL's only fixable defect is that the gate released it at the
top of its range**, which is the §2 mechanism again.

---

## 6. What did NOT cause the problem in this run (checked, so it can be dropped)

* **Cash.** All 46 `Buy gate inputs` lines parsed: **only 11 were cash-truncated below 95%
  of `cash_per_trade`**, and of the five names in scope, PLRZ ($877.05/$877.05), AVNT
  ($873.59/$873.59) and SNDK ($860.44/$860.44) got **100%** of their intended slot; WDC got
  96.2%; only HL was starved (15.7%). The `_SYNTHESIS` "cash race" is real but it is **not**
  the binding constraint for the big movers *in this run*.
* **The exit stack / circuit breaker.** Total measured cost of both circuit-breaker exits:
  PLRZ $21.21 + HL $8.72 = **$29.93**. Both `[sell-gate] ... circuit_breaker ... result=fired`
  lines are quoted above. Compare $697 for PLRZ's entry price alone. `_SYNTHESIS`'s
  "DO NOT TOUCH the exits" holds in 201039 too.
* **Satellite cap as a *size* leak.** In 201039 it trimmed 47× and skipped 27×, but never
  trimmed one of the five names below ~95% of slot (WDC $878→$826). **In 820236 it is a
  major leak** — it trimmed SNDK on *every* attempt: `$873→$591`, `$881→$579`, `$883→$564`,
  `$884→$562` (≈64% of target, four times). Fix it for 820236-shaped runs, not for this one.
* **Turnover budget.** `TURNOVER BUDGET BINDING` on 569/634 bars, but `TURNOVER BUDGET
  BYPASS` fired 42× and admitted every one of the five names. It refused none of them.

## 7. What corroborates across runs but did not bind here

`max_positions` plumbing: `Regime capacity gate (Z4.1): regime=chop max_positions 6->8`
fires **43 times** in 201039, and **all 634** `max_positions gate armed` lines read
`cap=6`. The book sits at the cap on **553/634 = 87.2%** of bars (820236: 599/634 = 94.5%).
In 201039 it only blocked 25 times and none of them were our five names — but **in 820236 it
is what bought SNDK's top**:

```
b111 01-12 $388.46  SATELLITE CAP: SNDK trimmed $873 -> $591 ... → PASS → MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)
b126 01-13 $390.49  Backfill queue BUY: SNDK alloc=$100 ... → PASS → MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)
b186 01-19 $413.55  SATELLITE CAP: SNDK trimmed $883 -> $564 ... → PASS → MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)
b201 01-20 $446.96  ... → PASS → FILL BUY SNDK price=443.834068
```

Three refusals at $388.46 / $390.49 / $413.55 before a fill at $443.83 — **+14.3% of entry
price handed away by a gate reading a stale cap.** (Full 820236 SNDK gate ladder, for the
record: `Entry extension gate` b0 $237.33 → `Rank band (entry<=#14 of 137)` b6 → `Entry
extension gate` b21 $262.08 → `mw_buy extension-block` b66 $335.90, b81 $333.19 → `BFQ
ALLOC=0 ... priority_budget=$33 ... min_pos=$100` b96 $363.01 → `MAX_POSITIONS_GATE` b111,
b126 → `full_priority_blocked` b141 $393.06, b156 $418.72 → `ROTATION PREVALIDATE sector-cap:
skip incoming SNDK (sector 'technology' $3,123 > 40% cap $2,507)` b171 $405.47 →
`MAX_POSITIONS_GATE` b186 → fill b201 $443.83.)

---

## 8. Dollars on the table in bt 201039

Each counterfactual holds the **same dollars** the run actually deployed, buys at a price the
run itself printed, and holds to the window-end price from `Stock movement`.

| lever | same $ | actual entry | counterfactual entry | actual P&L | c/f P&L | **Δ** |
|---|---:|---:|---:|---:|---:|---:|
| PLRZ bought on its first buy signal (b0, 2026-01-01) | $877.02 | $15.48 | **$8.11** | −$154.46 | +$542.33 | **+$696.79** |
| SNDK bought at 820236's own fill price (2026-01-20) | $860.41 | $660.48 | **$443.83** | −$37.73 | +$363.89 | **+$401.62** |
| WDC bought on its first buy signal (b156, 2026-01-15) | $771.74 | $259.37 | **$226.08** | +$58.17 | +$180.41 | **+$122.24** |
| AVNT not rotated away on 2026-01-30 | $883.35 | — | — | (path $920.75) | $1,006.76 | **+$86.01** |
| **total** | | | | | | **+$1,306.66** |

+$1,306.66 on $6,000 = **+21.8pp**, i.e. +8.34% → **≈+30%** for the two-month window, which
is the `docs/OBJECTIVE.txt:3-5` target rate. The three buys are simultaneously affordable
($877 + $860 + $772 = $2,509 against a satellite that ran ~60% of a $6,000 NAV), and AVNT is
a no-op (just don't sell). Circuit-breaker relief, by contrast, is worth $29.93.

---

## 9. RANKED — what to change

Ordered by (dollars × strength of evidence × generalizability). Every item states a
mechanism and is checked on **≥2 runs**. Items that only reproduce on one window are
labelled as such and are **not** recommended.

### 1. Make the day-1 momentum screen's candidate pool complete and deterministic
**Change.** Run `Momentum discovery` *after* every overlay-bar batch for the bar has been
fetched (in 201039 the screen executes at log:332–345, the graph-seed batch at log:346), and
seed the scan pool from a fixed, config-declared universe rather than from whatever is
already resident in the shared `_overlay_bars_raw` cache. Ship with
`overlay_bars_min_history_bars` set so a short-cached symbol cannot silently score 0.
**Expected effect.** SNDK enters on 2026-01-01 instead of 2026-02-02. Priced at 820236's own
fill, **+$402 on bt 201039**. Also removes the largest source of run-to-run irreproducibility.
**Evidence.** 820236 b0: `Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%)` and
`WDC (20d=+7.7%, 60d=+37.5%)`, cache warm (`Overlay bars: fetching 13 symbol(s)`) → both
bought, +$551 of a +$740 run. 201039 b0: `Overlay bars: fetching 144 symbol(s)`, pool
contains neither (`'SNDK' in pool → False`), screen returns DZZ/MAAS/TLSI/…; SNDK's first
strategy line is log:21994, b321, 2026-01-30. 613166 identical (SNDK first-seen b321).
**Generalizable?** Yes — it is an ordering/determinism bug, independent of name, window or
regime. Same defect, same line numbers, three runs.

### 2. Stop using a high/low RANGE as an entry gate
**Change.** At `graph_nexus_analysis.py:23233–23295` (`Entry extension gate`) and
`:5545–5558` (`V32 <lane> extension-block`), replace `_recent_runup_protect`
(`:9259–9281`, `(max−min)/min`) with a **position-in-range** test —
`(px − lo) / (hi − lo) > threshold` — or with a directional `_recent_return_pct`
(`:9284–9295`, already written, unused by these two call sites). Leave
`_recent_runup_protect` where it belongs: on the *exit* path it was written for
(docstring `:9262-9264`).
**Expected effect.** The gate stops refusing names at the bottom of a wide range. On the
four measurable block→fill pairs across the three runs the mean entry price would improve by
**36.5%**; the two harmful cases (PLRZ $8.11→$15.48, SNDK $237.33→$443.83) are worth
**+$697 (bt 201039)** and a large fraction of SNDK's +$101 (bt 820236).
**Evidence.** 201039: `V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25%` at
$8.11, the lowest price of the window (log:648, 2663, 3522). 820236: `Entry extension gate:
SNDK recent runup +28.5% > 25% — buy blocked` at $237.33, likewise the lowest price of the
window (L820:266, b0; re-fired L820:3064, b21 at $262.08). Both filled later at +91% / +87%.
**Must ship with** the regime-profile finding in
`docs/investigations/extension-gate-inversion.md`: `regime_profiles.bull.entry_extension_block_pct = 0`
and `...recovery... = 0` mean the gate is dead in a bull (393 fires across the three runs,
**0** under a bull profile). Changing the metric without changing the profile override is a
no-op in a bull; arming it in a bull with the *current* metric makes bull entries later.
**Generalizable?** Yes, and it is a *metric* fix rather than a threshold tune, so it does not
fit to a window. **Honest caveat:** in 2 of the 4 measurable pairs (820236/OMER,
613166/PLRZ) the gate improved the entry by 24% and 8%. Do not ship blind — ship as a paired
A/B with its own `history_scope_salt`, on ≥3 windows including one non-semiconductor leader,
and read the run.

### 3. Make the broker read the max_positions cap the strategy already computed
**Change.** Have the broker gate consume the Z4.1-adjusted cap instead of the static
`cfg["max_positions"]`. This is **not** "raise max_positions"
(`docs/OBJECTIVE.txt` DO-NOT-RETRY): the strategy has already decided 8; the broker is
reading 6. Ship with the breach auto-heal ratcheted so a cap change cannot latch into
per-bar forced liquidation.
**Expected effect.** In 820236, SNDK fills at $388.46 (b111) instead of $443.83 (b201):
**+14.3% of entry price**, ≈+$80 on the $562 slot actually available, plus two more slots
freed on 599 of 634 bars.
**Evidence.** 201039: `Regime capacity gate (Z4.1): regime=chop max_positions 6->8` ×43;
all 634 `max_positions gate armed` lines read `cap=6`; at cap on 553/634 = 87.2%.
820236: 599/634 = 94.5%, 45 `MAX_POSITIONS_GATE` blocks, **three of them on SNDK** at
$388.46 / $390.49 / $413.55.
**Generalizable?** Yes — a plumbing mismatch, identical in both runs. Corroborates
`_SYNTHESIS` root cause #2 with per-name dollar attribution.

### 4. Never fund a buy by selling a position that is not losing
**Change.** Set `rotation_winner_lock_min_pnl_pct` from this run's effective **5.0 → 0.0**
(`graph_nexus_analysis.py:9361`; run value from `Effective config ... lock=true/5d/5.0%/-0.10/30.0%`),
so any non-negative position is winner-locked against rotation. Optionally require the
outgoing leg to be below its own entry *and* out of the momentum watchlist.
**Expected effect.** +$86.01 on bt 201039, and it removes the mechanism that forced WDC's
entry to wait for a funding sale.
**Evidence.** 201039 log:22106 `Momentum portfolio swap: sell AVNT (pnl=+0.5%) → buy WDC
(score=0.768, $878)`; AVNT then ran $36.04 → $41.07 while WDC ran $259.37 → $278.93; AVNT
re-signalled 2026-02-13 and refused by `SATELLITE CAP: AVNT skipped ... ($-22 room)`.
820236 — the best run — contains **zero** `Momentum portfolio swap` / `Momentum rotation`
lines. Corroborates `_SYNTHESIS` #5.
**Generalizable?** Partly. The knob and the mechanism are general (`min_pnl` returning
`False` at +0.5% is a boundary, not a fit). But the **dollar** evidence is n=1 in this run
and n=0 in 820236, so treat +$86 as illustrative, not as a promotion case. Validate on ≥3
windows.

### 5. Re-shape the breadth-scan parabolic cap, or accept that the market-wide lane cannot
   see the OBJECTIVE's target names
**Change.** `breadth_scan_r20_parabolic_cap_pct` (default 60) and
`breadth_scan_r60_parabolic_cap_pct` (default 150) at `graph_nexus_analysis.py:20859–20860`
are absolute return caps. Replace with a *pullback / position-in-range* admission test
(same metric as item 2), so a strong trend that is not currently extended is admitted while
a vertical blow-off is not. Separately raise `breadth_scan_batch_per_bar` (default 50,
`:20848`) so a 500-name universe is covered in ≤2 bars rather than 10.
**Expected effect.** Makes item 1's benefit robust to which names the graph happens to seed.
Unquantified in dollars — no run in evidence has this configuration.
**Evidence.** 201039 `breadth_scan=` grows by exactly 3 per analysis bar (3, 6, 9, … 126 over
43 bars), matching `breadth_scan_admit_per_bar` default 3 (`:20861`). SNDK's r20 exceeded
60% for essentially the whole +160% run-up, so the cap excluded it on every bar. The
function's own docstring (`:20781–20787`) names this exact failure ("CAR: seen at +166%,
bought the top").
**Generalizable?** The *mechanism* claim is (an absolute return cap must exclude the largest
movers, by construction). The *benefit* is unmeasured. Recommend as an experiment, not a ship.

### 6. Do NOT change — measured, in this run
* **Circuit breaker / exit stack.** Total cost across both firings in 201039 = **$29.93**
  (PLRZ $21.21, HL $8.72). `[sell-gate] ... circuit_breaker ... result=fired` quoted in §2/§5.
  Confirms `_SYNTHESIS` "DO NOT TOUCH".
* **Cash-race work, for this run's shape.** PLRZ, AVNT and SNDK each received **100%** of
  `cash_per_trade`; only 11 of 46 gate evaluations were cash-truncated. (Still real in
  820236 — SNDK trimmed to ~64% of slot four times — so fix satellite-cap sizing there, not
  the buy-gate clock here.)
* **Raising position size on HL-class names.** HL closed the window *below* our entry; a
  full slot would have lost ≈$99 instead of ≈$15. The defect is the entry price, not the size.

---

## 10. The one-sentence answer

In bt 201039 the big movers were not refused by portfolio construction — **SNDK was never
seen until 96.5% of its move was over, and PLRZ/HL were seen and then refused by a gate that
measures a high/low range and therefore blocks a name hardest at the bottom of its own
range** — so by the time any sizing or cash logic ran, the entry price had already decided
the outcome.
