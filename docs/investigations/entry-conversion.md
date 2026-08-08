# Entry conversion — why the big movers do not get bought

Read-only investigation across bt **820236 / 718249 / 613166 / 725146**
(instance `v2-let-run-core`, 2026-01-01 → 2026-03-01, $6,000, 3600s,
`pit_mode=research`). Every number below is counted out of the run logs pulled
with `scripts/pull_backtest_logs.py`. Where a number is *imputed* rather than
read off a log line, it is labelled.

Target names from `docs/OBJECTIVE.txt:24-26`:
SNDK, VICR, AAOI, VIAV, LASR, TTMI, AMAT, ADI, WDC, LRCX, KLAC, UCTT.

---

## 0. Headline

| run | result | buy notional | of which SPY core | of which TARGET names | targets bought |
|---|---|---|---|---|---|
| 820236 | +12.33% (+$739.61) | $12,617 | $7,697 (61%) | **$2,171 (17%)** | SNDK $491, WDC $840, LRCX $840 |
| 718249 | +4.23% (+$253.84) | $13,290 | $6,501 (49%) | **$0 (0%)** | none |
| 613166 | +9.17% (+$549.91) | $13,118 | $7,752 (59%) | **$127 (1%)** | SNDK $127 |
| 725146 | +0.11% (stopped) | $13,059 | $5,574 (43%) | **$643 (5%)** | WDC $643 |

`/backtests/<id>/summary` → `pnl`, `pnl_percent`; buy notional summed from
`[execution] FILL BUY <sym> qty=… price=…` lines.

**In the best run, three target names bought with 17% of the buy budget produced
107% of the profit.** `pnl_per_stock` for 820236:

```
WDC +$450.5   LRCX +$238.2   SNDK +$100.9   ->  $789.6 of a $739.6 total
SPY   +$8.8  (on $7,697 of buy notional, 9 buys / 17 sells)
OMER  -$61.0   CORD  -$59.4   CPER +$55.7   BA  +$2.3
```

The conversion problem is not that the gates refuse *losers*. It is that the
whole budget is spent on ~50 marginal names and the handful of 60–170% movers
get the same treatment as everything else — and usually less.

---

## 1. The funnel, measured

### 1a. The sizing engine already wants ~14.5% of NAV per name

`V31.2 total-spend cap [CONCENTRATE]` sizes conviction buys at a median of
**$871** on a $6,000 account (n=305 sized buys across the four runs, min $828,
max $966). That is exactly the position size `docs/OBJECTIVE.txt:11` asks for.

Example (820236, 2026-01-01, first bar):
```
[GraphNexusAnalysis] V31.2 total-spend cap [CONCENTRATE]: funded 4 of 5 by conviction
  (WDC@$840, LRCX@$840, RIG@$840, CPER@$840) out of $3,780; dropped 1 to the queue
```

### 1b. …and almost none of it converts

| | 820236 | 718249 | 613166 | 725146 |
|---|---|---|---|---|
| conviction buys **sized** by V31.2 | 81 | 71 | 84 | 69 |
| dollars sized | $72,780 | $61,088 | $72,692 | $59,372 |
| distinct names sized | 59 | 46 | 58 | 44 |
| non-SPY **fills** | 10 | 12 | 12 | 12 |
| non-SPY dollars filled | **$4,920** | $6,789 | $5,367 | $7,485 |
| names sized-but-never-filled | **52** | 35 | 49 | 35 |

Across all four runs: **305 sized conviction buys ($265,932) → 21 fills
($11,103).** 93% of the sized notional never reaches the broker.

### 1c. Where the sized buys die (ALL names, terminal gate per sized buy)

| terminal gate | sized buys killed | sized notional refused |
|---|---|---|
| TURNOVER BUDGET BLOCK | 128 | $109,982 |
| MAX_POSITIONS_GATE | 72 | $65,022 |
| SATELLITE CAP (skip) | 42 | $36,550 |
| (no gate line logged) | 30 | $25,456 |
| insufficient_cash | 9 | $7,961 |
| SATELLITE CAP (trim) | 2 | $1,835 |
| Rank band | 1 | $840 |

Per run (sized notional refused):

```
run       MAX_POSITIONS   TURNOVER    SAT-CAP skip   insufficient_cash
820236        $41,453      $7,808        $9,756          $3,618
718249        $12,988     $27,491        $6,900              $0
613166        $10,581     $41,167        $6,214          $3,515
725146             $0     $33,516       $13,680            $828
```

Note the run-to-run swing: in **820236 (the best run) MAX_POSITIONS_GATE is the
dominant killer**; in the other three the turnover budget is. 820236 predates
commit `e5958cb` ("the index core gives up its max_positions slot"), and shows
**0** `index-core leg(s) SPY do not consume a slot` lines vs 612/612/485 in the
other three. That single lever moved the share of bars sitting at the cap from
**94.5% (820236) to 30.4% (718249) / 49.8% (613166) / 0% (725146)**
(`max_positions gate armed: held=N, cap=6`, n=634/634/634/507).

### 1d. The core will not release the money

```
[BROKER] [core] funding request trimmed $3,355 -> $889 — satellite headroom will
  refuse the remainder; releasing core for it would only be bought back
```

| run | trims | requested | released | **refused** |
|---|---|---|---|---|
| 820236 | 39 | $64,706 | $27,938 | **$36,768** |
| 718249 | 28 | $48,345 | $16,129 | **$32,216** |
| 613166 | 35 | $63,512 | $25,536 | **$37,976** |
| 725146 | 24 | $43,929 | $18,761 | **$25,168** |

Core target sits at 37.1–40.0% of NAV (`[core] hold (…): core X% vs target Y% of
NAV`, n=163). The satellite therefore runs out of room, `SATELLITE CAP` fires,
and the cash that would have bought the mover is sitting in SPY, which earned
**$8.8** in 820236.

### 1e. The backfill queue cannot buy a position

Every one of the **689** `BFQ ALLOC=` diagnostic lines reads `ALLOC=0`. The
budget it is allocating from:

```
V28 BFQ DRAIN ENTRY: queue_size=14 headroom=0 cash=$198 priority_budget=$99
  standard_budget=$99 min_pos=$100
```

Median `priority_budget` across the 680 (of 689) lines whose budget fields parse
= **$99** against `min_pos=$100`; **50.6% of them have priority_budget <
min_pos**, i.e. the
queue is structurally unable to fund a legal position. When it does fire
(`Backfill queue BUY`, n=109) the median allocation is **$100** and the max is
**$202** — 1.7% to 3.4% of NAV, against a 14.5% conviction slot.

---

## 2. Per-name census

`in universe` = a `Backtest symbol expansion: loaded N bars for <sym>` line
exists. `scored` = the name reached at least one buy-path log line.
`sized` = `V31.2 total-spend cap … (<sym>@$X)`.

### bt 820236 (+12.33%) — 12 of 12 targets in the universe

| name | in universe | scored | sized | filled | first refusal | gates hit |
|---|---|---|---|---|---|---|
| SNDK | Y | Y | 4× ($3,521) | 3 ($491) | **Entry extension gate, 2026-01-01 @ $237.33** | SAT-CAP trim×5, MAX_POSITIONS×3, full_priority_blocked×3, deferred×3, entry-ext×2, mw_buy-ext×2, rank band×1, BFQ ALLOC=0×1, promoted-demoted×1, ROTATION PREVALIDATE×1 |
| VICR | Y | Y | 1× ($873) | 0 | deferred unfunded, 2026-01-01 @ $109.62 | BFQ ALLOC=0×6, rank band×6, deferred×2, promoted-demoted×1, entry-ext×1, **max_positions core pre-pass×1**, **SAT-CAP skip×1**, full_priority_blocked×1 |
| AAOI | Y | Y | 0 | 0 | Rank band, 2026-01-08 @ $34.50 | rank band×3 |
| VIAV | Y | **n** | 0 | 0 | — | **no buy signal ever reached a gate** |
| LASR | Y | **n** | 0 | 0 | — | **no buy signal ever reached a gate** |
| TTMI | Y | **n** | 0 | 0 | — | **no buy signal ever reached a gate** |
| AMAT | Y | Y | 0 | 0 | full_priority_blocked, 2026-01-15 @ $325.79 | rank band×9, full_priority_blocked×1, deferred×1 |
| ADI | Y | Y | 0 | 0 | full_priority_blocked, 2026-02-18 @ $343.60 | full_priority_blocked×1, deferred×1 |
| WDC | Y | Y | 1× ($840) | 1 ($840) | deferred unfunded, 2026-01-01 | **BOUGHT day 2 @ $181.55 → +$450.5** |
| LRCX | Y | Y | 1× ($840) | 1 ($840) | — | **BOUGHT day 2 @ $182.26 → +$238.2** |
| KLAC | Y | Y | 1× ($854) | 0 | SATELLITE CAP trim, 2026-01-08 @ $131.47 | SAT-CAP trim×1 ($854→$774), **MAX_POSITIONS_GATE×1 ($759 refused)** |
| UCTT | Y | Y | 0 | 0 | Rank band, 2026-01-02 | **entry-ext×12**, rank band×5, + Nexus quality filter ×17 |

### bt 718249 (+4.23%) — only 2 of 12 targets ever entered the universe

| name | in universe | scored | sized | filled | first refusal |
|---|---|---|---|---|---|
| SNDK | Y | Y | 1× ($890) | 0 | promoted-demoted, 2026-01-30 @ $620.08 — then **mw_buy extension-block ×12** (runups 108%→47.8%) |
| WDC | Y | Y | 2× ($1,744) | **0** | full_priority_blocked 2026-01-15 @ $226.08; later `MAX_POSITIONS_GATE blocked WDC (held=6, cap=6)` with cash_to_use $35.98 |
| AMAT | n | Y | 0 | 0 | **price unresolved, 2026-01-08 @ $257.02** |
| KLAC | n | Y | 0 | 0 | promoted-demoted, 2026-01-08 @ $131.47 |
| LRCX | n | Y | 0 | 0 | Entry extension gate, 2026-01-08 @ $195.40 (+33.5%) |
| UCTT | n | Y | 0 | 0 | Entry extension gate, 2026-02-24 (+43.0%) |
| VICR / AAOI / VIAV / LASR / TTMI / ADI | **n** | n | 0 | 0 | never discovered into the universe |

### bt 613166 (+9.17%) — 7 of 12 in the universe

| name | in universe | scored | sized | filled | first refusal |
|---|---|---|---|---|---|
| SNDK | Y | Y | 4× ($3,467) | 2 ($127) | full_priority_blocked, 2026-01-30 @ $620.08 |
| WDC | Y | Y | 2× ($1,791) | **0** | full_priority_blocked, 2026-01-15 @ $226.08 |
| UCTT | Y | Y | 0 | 0 | mw_buy extension-block, 2026-02-11 @ $54.84 (+44.6%) |
| AMAT | n | Y | 0 | 0 | **price unresolved, 2026-01-08 @ $257.02** |
| KLAC | n | Y | 0 | 0 | promoted-demoted, 2026-01-08 @ $131.47 |
| LRCX | n | Y | 0 | 0 | Entry extension gate, 2026-01-08 @ $195.40 |
| VICR / AAOI / VIAV / TTMI | Y | **n** | 0 | 0 | in the universe, **no buy signal ever reached a gate** |
| LASR / ADI | n | n | 0 | 0 | never discovered |

### bt 725146 (STOPPED, +0.11%) — 2 of 12 in the universe

| name | in universe | scored | sized | filled | first refusal |
|---|---|---|---|---|---|
| SNDK | Y | Y | 1× ($828) | 0 | **price unresolved ×4 (2026-01-30 → 02-04)** while it was the **#1 momentum name** (scores 1.505 → 1.907 → 1.936) |
| WDC | Y | Y | 1× ($915) | 1 ($643) | full_priority_blocked, 2026-01-15 |
| AMAT / KLAC | n | Y | 0 | 0 | **price unresolved, 2026-01-08** |
| LRCX | n | Y | 0 | 0 | Entry extension gate, 2026-01-08 |
| VICR / AAOI / VIAV / LASR / TTMI / ADI / UCTT | n | n | 0 | 0 | never discovered |

---

## 3. THE RANKED GATE TABLE (target names only)

Every log line naming a target symbol, all four runs pooled.

`$ refused` = the dollars the sizing engine had assigned on that tick where a
number is on the line (`SATELLITE CAP: X trimmed $A -> $B`, `Buy gate inputs …
cash_to_use=$Y`); otherwise the same tick's `V31.2 … (<sym>@$X)`; otherwise the
run's median conviction slot ($888 / $855 / $868 / $845). The `src` column says
which. `forgone $` = `$ refused × (price at 2026-02-27 / price at refusal − 1)`
— i.e. what a hold-to-window-end position of that size would have made.

| gate | refusals of TARGET names | names | runs | median $ refused | **total $ refused** | forgone $ to 02-27 | $ src |
|---|---|---|---|---|---|---|---|
| Rank band | 28 | 6 | 3 | $888 | $24,758 | $6,405 | imputed |
| V32 mw_buy extension-block | 26 | 2 | 4 | $855 | $22,268 | $2,845 | imputed |
| Entry extension gate | 22 | 4 | 4 | $888 | $19,311 | $3,835 | imputed |
| Promoted buys demoted to queue-only hold | 22 | 6 | 4 | $855 | $18,894 | $2,992 | imputed |
| Deferred unfunded buys demoted to hold | 15 | 5 | 4 | $888 | $13,062 | $3,943 | imputed |
| full_priority_blocked | 14 | 7 | 4 | $868 | $12,210 | $2,628 | imputed |
| price unresolved | 10 | 3 | 3 | $845 | $8,493 | $1,263 | imputed |
| BFQ ALLOC=0 | 7 | 2 | 1 | $888 | $6,216 | $3,310 | imputed |
| SATELLITE CAP (trim) | 15 | 3 | 4 | $319 | $6,092 | $751 | logged |
| SATELLITE CAP (skip) | 3 | 3 | 3 | $867 | $2,595 | $497 | imputed |
| insufficient_cash | 3 | 2 | 2 | $869 | $2,581 | $75 | imputed |
| MAX_POSITIONS_GATE | 7 | 3 | 3 | $150 | $2,270 | $891 | logged |
| MAX_POSITIONS core funding pre-pass | 2 | 2 | 2 | $864 | $1,728 | $502 | imputed |
| Queued due to cash / slot cap | 2 | 1 | 2 | $844 | $1,687 | $114 | imputed |
| TURNOVER BUDGET BLOCK | 1 | 1 | 1 | $890 | $890 | $60 | imputed |
| ROTATION PREVALIDATE sector-cap | 1 | 1 | 1 | $888 | $888 | $516 | imputed |

**Caveats, stated rather than hidden.**

1. **`Rank band` is under-counted by ~8×.** The line prints only the first ~8
   symbols of the blocked set: across 164 rank-band lines it declares
   **9,751** blocked buys and prints **1,148** names (11.8%). The 28 target
   refusals above are a floor; the true figure is plausibly ~200.
2. Totals sum the *same name refused on many consecutive ticks*, so
   `$ total refused` is a repeat-refusal volume, not a bankroll. It ranks gates;
   it is not an account-level number.
3. `P3 undersized guard` — **zero occurrences.** The string `undersized` does
   not appear in any of the four logs. The guard
   (`graph_nexus_analysis.py:31999-32029`) never fired.
4. `TURNOVER BUDGET BLOCK` hits target names **once** in four runs (718249,
   SNDK, 2026-02-05) because high-conviction names take the bypass:
   `TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50` (52 bypasses total, 9 on
   target names). The turnover brake is the #1 killer of *sized buys overall*
   ($109,982) and a near-non-issue for the movers.
5. `MAX_POSITIONS_GATE`'s median `$ refused` looks small ($150) only because the
   paired `Buy gate inputs` line has *already* been clamped to available cash.
   The pre-clamp intent is the V31.2 slot: SNDK was sized **$873** on 2026-01-12
   and **$881** on 01-13; the gate refused what was left after SATELLITE CAP and
   the cash clamp had eaten most of it.

---

## 4. What actually happened to each big mover — the receipts

### SNDK (+170.2%: $237.33 → $641.26). The single most expensive refusal.

820236, first bar of the run:

```
13:02:12 [GraphNexusAnalysis] Final scoring + ML overlay: done in 15.29s
13:02:12 [GraphNexusAnalysis] Rank band (entry<=#14, exit>#68 of 136): blocked 54 buy(s) [...]
13:02:12 [GraphNexusAnalysis] Entry extension gate: SNDK recent runup +28.5% > 25% — buy blocked
13:02:12 [GraphNexusAnalysis] Nexus quality filter: blocked 6 low-quality buy(s): MPB, TRDA, UCTT
13:02:12 [GraphNexusAnalysis] Executable stock slate: LRCX, RIG, CPER, GDX, WDC
```

SNDK was a live buy signal on **2026-01-01 at $237.33** and was refused for a
**3.5 percentage-point** overshoot of a 25% runup cap. It ended the window at
$641.26.

It was then refused a further 21 times in that run. Two representative ticks:

```
2026-01-12  V31.2 total-spend cap: funded 3 of 4 by conviction (SNDK@$873, VICR@$873, TER@$873)
            [core] funding pre-pass: max_positions will refuse 1 of 3 sized buy(s) (VICR)
            [core] funding request trimmed $2,620 -> $591
            SATELLITE OVERFLOW: SNDK raw=+1.700 >= 1.50 — funding $591 of room out of the core
            SATELLITE CAP: SNDK trimmed $873 -> $591 to keep the core at target
            TURNOVER BUDGET BYPASS: SNDK raw=+1.700 >= 1.50 — admitting a conviction buy
            Buy gate inputs for SNDK: cash=$618.09 … cash_to_use=$591.39 → PASS
            MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)          <-- price $388.455

2026-01-13  V31.2 … (LLY@$881, SNDK@$881)
            SATELLITE CAP: SNDK trimmed $881 -> $579
            Buy gate inputs for SNDK: cash=$127.83 … cash_to_use=$127.83 → PASS
            MAX_POSITIONS_GATE: blocked SNDK (held=6, cap=6)          <-- price $390.49
```

It finally filled **2026-01-20 @ $443.83 for $127.73** — 87% above the price at
which it was first refused, at 1/7th the intended size. Later adds: $249.89 @
$517.69 (01-29), $113.20 @ $679.70 (02-23).

Result: SNDK contributed **+$100.9**. A single $888 slot bought on 2026-01-01
and held would have contributed **+$1,511**. Delta **+$1,410 = +23.5% of the
account, from one name.**

In 718249 / 613166 / 725146 the block is self-fulfilling: because nothing was
bought early, by 2026-02-06 the measured runup is **+108%** and
`V32 mw_buy extension-block` fires on every subsequent bar (12 / 4 / 8 times).

### VICR (+81.4%: $109.62 → $198.89). Sized once, refused by the funding chain.

```
2026-01-12 V31.2 total-spend cap: funded 3 of 4 by conviction (SNDK@$873, VICR@$873, TER@$873)
2026-01-12 [core] funding pre-pass: max_positions will refuse 1 of 3 sized buy(s) (VICR)
             — not releasing core to fund them
2026-01-12 SATELLITE CAP: VICR skipped — satellite at its design share ($-32 room);
             core would be squeezed below target
```

Before that it was `BFQ ALLOC=0` six times with `priority_budget` of
**$28–$132** against `min_pos=$100`. Price on 2026-01-12 was $131.79.
$873 held to 02-27 = **+$444**. Actual: **$0.**

### AAOI (+102.2%: $34.84 → $70.43). Rank band only.

Three lines in the whole of 820236, all `Rank band`:
```
2026-01-08 Rank band (entry<=#17, exit>#82 of 163): blocked 69 buy(s) [TXN, VOYA, XRN, XRP, A, AAOI, …]
2026-02-06 Rank band (entry<=#19, exit>#94 of 187): blocked 56 buy(s) [XRP, SYNA, XRN, USDE, VOYA, AAOI, …]
2026-02-20 Rank band (entry<=#20, exit>#98 of 195): blocked 71 buy(s) [XRP, SVIV, TIH, VIVK, VOYA, WDAY, XRN, AAOI, …]
```
The entry cut is the **top 10.3% of the ranked list** (median entry rank 17 of
169 across all four runs). AAOI never got closer than that.

### AMAT (+45.0%), KLAC (+7.6%), LRCX (+37.7%) in the three later runs — a plumbing failure, not a risk gate

```
718249 2026-01-08  V31.2 total-spend cap: funded 3 of 6 by conviction (SLVP@$841, AIFD@$841, AGMI@$841)
                     out of $3,784; … skipped as not executable: BKNG(price unresolved),
                     AMAT(price unresolved), APP(price unresolved)
```

**187 of the 204 `price unresolved` drops across the four runs are symbols that
never got a `Backtest symbol expansion: loaded N bars` line at all**; 11 more got
their bars on a *later* date than the drop. AMAT and KLAC are in that set in
718249 / 613166 / 725146.

The clearest case is SNDK in 725146:

```
21:53:11  Executable stock slate: KLAC, SNDK
21:53:11  V31.2 …: funded 1 of 3 (META@$870); skipped as not executable:
            SNDK(price unresolved), KLAC(price unresolved)
21:54:41  Momentum watchlist: top3=[('SNDK', 1.505), …], new_buys=['SNDK']
21:54:41  V31.2 …: funded 1 of 4 (AAPL@$844); skipped: … SNDK(price unresolved)
21:56:59  Momentum watchlist: top3=[('SNDK', 1.907), …]
21:56:59  V31.2 …: funded 2 of 5 (TNC@$844, VOYA@$844); skipped: SNDK(price unresolved) …
21:58:30  Momentum watchlist: top3=[('SNDK', 1.936), …]
21:58:30  V31.2 …: funded 2 of 5 (TSEM@$843, RGEN@$843); skipped: SNDK(price unresolved) …
22:00:19  Backtest symbol expansion: loaded 733 1Hour bars for SNDK     <-- bars finally arrive
22:00:22  Buy gate inputs for SNDK: cash=$124.64 … effective_floor=$120.00 available=$4.64
            cash_to_use=$4.64 → SKIP
2026-02-06 onward:  V32 mw_buy extension-block: SNDK recent runup +108.0% > 25%
```

Four days as the **#1 ranked momentum name**, dropped for having no price, while
META / AAPL / TNC / VOYA / TSEM / RGEN were funded at ~$844 each. By the time the
bars arrive the cash is gone; by the next bar the extension gate has locked it
out permanently.

**Already fixed, not in any of these runs.** The narrowing is in the working
tree, uncommitted (`git diff backend/strategies/graph_nexus_analysis.py`, the
`2026-08-08 (bt 725146) NARROWED` block at `graph_nexus_analysis.py:31928-31940`).
`grep -c NARROWED` on `HEAD` = 0.

### VIAV (+63.9%), LASR (+24.1%), TTMI (+52.0%) — discovered, then silence

In 820236 these three are in the universe (bars loaded) and produce **no gate
line at all**. Their entire footprint:

```
[GraphNexusAnalysis]   Discovered stock (momentum): VIAV (20d=+1.6%, 60d=+39.5%)
[BROKER] Backtest symbol expansion: loaded 705 1Hour bars for VIAV
[GraphNexusAnalysis]   Discovered stock (momentum): VIAV (20d=+42.0%, 60d=+66.6%)
```
```
[GraphNexusAnalysis]   Discovered stock (momentum): TTMI (20d=-6.8%, 60d=+31.0%)
[GraphNexusAnalysis]   Discovered stock (momentum): TTMI (20d=+31.9%, 60d=+38.7%)
```
```
[GraphNexusAnalysis]   Discovered stock (momentum): LASR (20d=+21.5%, 60d=+40.9%)
```

No rank band, no extension gate, no queue entry, no size. They were scored into
the momentum watchlist (`watchlist=131…505, scored=79…337`) and never made
`new_buys` or the top-3. There is no per-name log for why. **This is not a gate
refusal and I cannot attribute it to one from the log.**

---

## 5. A gate-by-gate reality check on "are these gates refusing losers?"

Forward return of each blocked basket, first block per (run, gate, name), held to
2026-02-27, restricted to names with a real price series (≥5 observations, last
observation ≥ 2026-02-20). Size-weighted by the refused dollars.

**All names** (n varies by gate):

| gate | blocks | size-wtd fwd return | forgone $ |
|---|---|---|---|
| Promoted buys demoted to queue-only hold | 135 | +6.3% | $7,374 |
| BFQ ALLOC=0 | 77 | +7.8% | $5,207 |
| Rank band | 112 | +5.4% | $5,173 |
| Deferred unfunded demoted to hold | 100 | +4.0% | $3,467 |
| full_priority_blocked | 167 | +2.3% | $3,322 |
| TURNOVER BUDGET BLOCK | 43 | +7.2% | $2,642 |
| Entry extension gate | 50 | +6.0% | $2,604 |
| price unresolved | 22 | +12.8% | $2,403 |
| MAX_POSITIONS_GATE | 25 | **+2.3%** | $221 |
| SATELLITE CAP (skip) | 22 | +0.6% | $117 |
| ROTATION PREVALIDATE sector-cap | 4 | −2.5% | −$90 |

**Target names only:**

| gate | blocks | size-wtd fwd return | forgone $ |
|---|---|---|---|
| Rank band | 8 | **+42.3%** | $2,972 |
| Promoted buys demoted to queue-only hold | 15 | +22.3% | $2,872 |
| Entry extension gate | 10 | **+30.2%** | $2,611 |
| Deferred unfunded demoted to hold | 10 | +29.7% | $2,587 |
| full_priority_blocked | 11 | +16.7% | $1,603 |
| BFQ ALLOC=0 | 2 | +74.1% | $1,316 |
| price unresolved | 6 | +25.4% | $1,300 |
| V32 mw_buy extension-block | 4 | +28.1% | $970 |
| MAX_POSITIONS_GATE | 5 | +31.6% | $498 |
| SATELLITE CAP (skip) | 3 | +19.1% | $497 |

The in-code note at `broker.py:14157-14160` says *"the blocked basket for
MAX_POSITIONS_GATE measured −2.6% to −9.6% forward, i.e. it was refusing
losers."* **On these four runs that reproduces on the all-names basket
(+2.3% size-weighted, +1.0% equal-weight, median 0.0%) but not on the movers
(+31.6%).** The gate is close to neutral on the average name and expensive on
the tail. That is precisely the asymmetry `docs/OBJECTIVE.txt:8-11` says matters.

Similarly, the entry-extension basket in 820236 (first block per name, real
series only, n=20) returns **mean +13.5% / median +0.3%**; excluding SNDK,
**+5.3% / 0.0%**. This does **not** contradict
`docs/OBJECTIVE.txt:73` ("Loosening the entry-extension gate: blocked basket
returned −7.95%") — that is a *portfolio* result from actually loosening the
gate, which also admits every other over-extended name. The two measurements are
different objects. What the measurement here does say is: **the blocked basket's
value is concentrated in one name (SNDK), so a blanket loosen/tighten is the
wrong instrument.**

---

## 6. A provable configuration mismatch: the regime capacity lift never reaches the broker

Every decision bar in all four runs:

```
[GraphNexusAnalysis] Regime capacity gate (Z4.1): regime=chop max_positions 6->8 (spy_20d=-1.24%, v31=chop)
[BROKER]             max_positions gate armed: held=6, cap=6
```
(one second apart, same tick)

Counts:

| run | Z4.1 lifts 6→8 (chop) | Z4.1 lifts 6→14 (bull) | broker `cap=` values observed |
|---|---|---|---|
| 820236 | 41 | 2 | **6 × 634** |
| 718249 | 41 | 2 | **6 × 634** |
| 613166 | 41 | 2 | **6 × 634** |
| 725146 | 33 | 2 | **6 × 507** |

**2,409 broker arm lines, every one at cap=6. Zero at 8 or 14.**

Cause, from the tree: `nexus_broker_utils.py:127-155`
(`resolve_max_positions_cap`) reads `cfg["max_positions"]` straight off the
static strategy config. The strategy's regime-adjusted `_max_positions`
(`graph_nexus_analysis.py:28794-28801`) is a local variable that is never
written back to anything the broker reads. The strategy believes it may hold 8
(or 14 in a bull); the broker enforces 6.

In 820236 the book sat at `held=6, cap=6` on **94.5% of 634 bars** and that gate
refused **$41,453** of already-sized conviction buys.

---

## 7. What was bought instead

820236 buy fills, in full:

```
SPY   ×9  $7,696.84   ->  +$8.8
CPER  ×2  $1,079.97   ->  +$55.7
LRCX  ×1    $839.97   ->  +$238.2
WDC   ×1    $839.97   ->  +$450.5
OMER  ×1    $829.28   ->  -$61.0
CORD  ×1    $732.95   ->  -$59.4
SNDK  ×3    $490.83   ->  +$100.9
BA    ×1    $107.02   ->  +$2.3
```

17 SPY sells against 9 SPY buys — the core was churned $7,697 in and $5,888 out
for **$8.8** of P&L, while SNDK's $591 sizing was refused for lack of a slot on
the same bars.

---

## 8. Counterfactual, with its assumptions on the table

One conviction slot (the run's own median V31.2 size) bought at each target's
**first-refusal price** and held to 2026-02-27:

| run | targets with a refusal | counterfactual P&L | actual P&L from targets | delta |
|---|---|---|---|---|
| 820236 | 8 | +$3,970 | +$551 | **+$3,419 (+57% of NAV)** |
| 613166 | 6 | +$973 | +$3 | +$970 |
| 725146 | 5 | +$898 | $0 | +$898 |
| 718249 | 6 | +$759 | $0 | +$759 |

820236 line by line:

| name | first refusal | price | end px | $888 slot P&L | actual | delta |
|---|---|---|---|---|---|---|
| SNDK | Entry extension gate 2026-01-01 | $237.33 | $641.26 | +$1,511 | +$100.9 | **+$1,410** |
| AAOI | Rank band 2026-01-08 | $34.50 | $70.43 | +$925 | $0 | **+$925** |
| VICR | deferred unfunded 2026-01-01 | $109.62 | $198.89 | +$723 | $0 | **+$723** |
| AMAT | full_priority_blocked 2026-01-15 | $325.79 | $372.65 | +$128 | $0 | +$128 |
| KLAC | SATELLITE CAP trim 2026-01-08 | $131.47 | $150.17 | +$126 | $0 | +$126 |
| WDC | (bought) | $172.27 | $278.13 | +$546 | +$450.5 | +$95 |
| ADI | full_priority_blocked 2026-02-18 | $343.60 | $347.87 | +$11 | $0 | +$11 |

**Assumptions I am not hiding:** eight simultaneous $888 slots is $7,104 on a
$6,000 account — infeasible. The **feasible** four-name version
(SNDK + AAOI + VICR + WDC = 4 × $888 = $3,552 = 59% of NAV, exactly the shape
`docs/OBJECTIVE.txt:14-15` describes) is **+$3,705** against the +$551 actually
earned from targets, i.e. the run goes from +12.33% to roughly **+65%**. This is
a hold-to-end, no-slippage, no-stop, perfect-hindsight bound. It is an upper
bound on the size of the prize, not a forecast.

**Also not hidden:** the four runs straddle three commits
(`e5958cb` 10:19 PDT, `0064f79` 10:28 PDT, `184f8f5` 14:07 PDT on 2026-08-08).
820236 ran before all three; 718249/613166 after the first two; 725146 after all
three. They are **not** a clean A/B of anything — the `max_positions` /
`price unresolved` / rank-band differences between them are partly code
differences. Any lever below needs its own paired run per
`docs/OBJECTIVE.txt:88-96`.

---

## 9. RANKED: what to change, expected effect, evidence

### 1. Make the broker's `max_positions` cap read the regime-adjusted value (or set the config cap to 8)
- **Effect:** in 820236 this is the terminal refusal for **45 sized buys worth
  $41,453**, including SNDK at $591 and $128 and KLAC at $759. The book sat at
  `held=6, cap=6` on 94.5% of 634 bars.
- **Evidence:** 164 `Regime capacity gate (Z4.1): … max_positions 6->8` lines vs
  **2,409** `max_positions gate armed: … cap=6` lines, zero of which read 8 or
  14. Cause at `nexus_broker_utils.py:148-152` (reads static
  `cfg["max_positions"]`) vs `graph_nexus_analysis.py:28794-28801` (local
  `_max_positions`).
- **Caveat, from the run:** the sleeve-leg exclusion (`e5958cb`) already took
  at-cap bars from 94.5% → 30.4%/49.8%/0%, so part of this is banked. And
  `docs/OBJECTIVE.txt:75-76` warns raising `max_positions` latches breach
  auto-heal into forced liquidation — that is the risk to test, and the Z4.1
  lift is *already* the strategy's own intent, so aligning the broker to it is
  narrower than "raise the cap".

### 2. Ship the `price unresolved` narrowing that is sitting uncommitted
- **Effect:** removes 187 of 204 sized-buy drops caused by a symbol having no
  bars yet. In 725146 it is the direct cause of SNDK — the **#1 momentum name,
  scores 1.505/1.907/1.936** — being dropped on four consecutive bars while
  META/AAPL/TNC/VOYA/TSEM/RGEN took the $844 slots.
- **Evidence:** the four `SNDK(price unresolved)` V31.2 lines (2026-01-30 →
  02-04), followed by `loaded 733 1Hour bars for SNDK` at 22:00:19 on 02-05 and
  `cash_to_use=$4.64 → SKIP`. Size-weighted forward return of the
  price-unresolved basket: **+12.8% all names, +25.4% targets** — the
  highest of any gate. Fix already written:
  `graph_nexus_analysis.py:31925-31940` (working tree, `HEAD` has 0 matches for
  `NARROWED`).
- **Cost of being wrong:** an occasional wasted slot on a genuinely unbuyable
  name (the bt 865585 RIG case the old behaviour was written for).

### 3. Give the backfill queue a budget that can buy a position
- **Effect:** the queue is where refused conviction names go, and it can never
  buy them back. **All 689 `BFQ ALLOC=` lines read ALLOC=0.** Median
  `priority_budget` **$99** vs `min_pos=$100`; **50.6%** of the 680 (of 689)
  lines whose budget fields parse have budget < floor. When it does fire, median allocation **$100** (max $202), i.e.
  1.7% of NAV against a 14.5% conviction slot.
- **Evidence:** `V28 BFQ DRAIN ENTRY: … cash=$198 priority_budget=$99
  standard_budget=$99 min_pos=$100`; VICR sat there six times at
  `priority_budget=$28…$132` before being sized at $873 and then refused.
  `BFQ ALLOC=0` blocked basket: **+7.8% all names, +74.1% on targets** (n=2).
- **Caveat:** n=2 on targets. Weak on its own; strong combined with the fact
  that the queue holds the names the other gates refused.

### 4. Rank on breakout, or exempt a conviction name from the rank band
- **Effect:** `Rank band` is the most frequent refusal of target names (28
  observed, and it prints only **11.8%** of the names it blocks — declared
  9,751, printed 1,148 — so the real count is ~8× higher). Its entry cut is the
  **top 10.3%** of the ranked list. It is the only thing that ever touched AAOI
  (+102.2%) and it hit AMAT 9 times in 820236.
- **Evidence:** 164 `Rank band (entry<=#N, exit>#M of K)` lines, median
  `entry<=#17 of 169`. Size-weighted forward return of the blocked basket:
  **+5.4% all names, +42.3% on targets.** Commit `0064f79`
  ("the rank band selects on the wrong axis for a breakout") already moves on
  this and landed between 820236 and 718249 — but 718249 still shows rank-band
  refusals of targets, so it is not resolved.

### 5. Do NOT loosen the extension gates globally — make them conviction-aware instead
- **Effect:** the entry-extension family is the *first* refusal for SNDK
  (2026-01-01, +28.5% vs a 25% cap, at $237.33 into a +170% move) and for LRCX
  in three runs (+33.5%), and it is what permanently locks SNDK out of
  718249/613166/725146 once the runup reads +108%. 48 refusals of target names,
  **$41,579** of refused size.
- **Evidence:** all 48 blocks are against a single hard `25.0%` cap. **14 of the
  48 are within 10pp of it** (25–35%). The block that mattered most missed by
  **3.5pp**.
- **But:** `docs/OBJECTIVE.txt:73` — loosening it returned **−7.95%**, and my
  own measurement of the 820236 blocked basket (mean +13.5%, **median +0.3%**;
  ex-SNDK **+5.3% / 0.0%**) says the median blocked name is worthless. The value
  is in one name. So the change worth testing is **not** a higher cap; it is a
  conviction carve-out of the shape that already exists elsewhere in the code
  (`TURNOVER BUDGET BYPASS: … raw >= 1.50`, `Nexus quality filter CONVICTION
  BYPASS`). SNDK's raw score on the blocked ticks was **+1.700–1.800**.
  This one needs its own paired A/B; I will not claim the direction.

### 6. Stop the core round-tripping money it will not release
- **Effect:** `[core] funding request trimmed` refused **$36,768** of $64,706
  requested in 820236 (and $25k–$38k in each other run), on the reasoning that
  "satellite headroom will refuse the remainder". Meanwhile SPY was bought
  $7,697 / sold $5,888 for **+$8.8** of P&L.
- **Evidence:** 126 `funding request trimmed` lines; core target 37.1–40.0% of
  NAV; 27 `[core] funding pre-pass: max_positions will refuse …` lines naming
  the exact buy that will be refused (VICR on 2026-01-12).
- **Note:** this is downstream of #1. The pre-pass explicitly refuses to release
  the core *because* `max_positions` will refuse the buy. Fix #1 and this
  unblocks with it; measure them together, not separately.

### Gates that are NOT the problem, measured
- **`TURNOVER BUDGET BLOCK`** — 1 refusal of a target name in four runs (the
  conviction bypass at `raw >= 1.50` covers them; 52 bypasses, 9 on targets).
  It is the biggest killer of *sized buys overall* ($109,982) but not of movers.
  Consistent with `docs/OBJECTIVE.txt:74-75` — leave it alone.
- **`ROTATION PREVALIDATE sector-cap`** — 1 target refusal (SNDK, 820236,
  2026-01-16, technology $3,123 > 40% cap $2,507). Blocked-basket return −2.5%.
- **`insufficient_cash`** — 3 target refusals. A symptom of #1/#6, not a cause.
- **`P3 undersized guard`** — **never fired.** Zero occurrences of the string
  `undersized` in any of the four logs.

---

## 10. What I could not prove

- **Why VIAV, LASR and TTMI produce no buy signal.** They are discovered
  (momentum, 60d = +39.5% / +40.9% / +31.0%) and their bars are loaded, and then
  nothing. No gate line exists for them in any run. The momentum watchlist logs
  only `top3` and `new_buys`, so there is no per-name record of the score that
  kept them out. This needs a log line before it can be investigated.
- **The true `Rank band` refusal count.** The line truncates at ~8 names (11.8%
  of what it blocks). Every rank-band number here is a floor.
- **Whether any of the six changes above actually helps P&L.** Nothing here is a
  paired A/B. The four runs differ by code as well as config
  (three commits land between 820236 and 725146), which
  `docs/handoffs/2026-08-08-production-readiness-research.md` measures as a
  6.12pp median spread across a commit boundary. Per
  `docs/OBJECTIVE.txt:97-100`, read the run — and then run the pair.

---

*Method: logs pulled with `python3 scripts/pull_backtest_logs.py <id>`; parsed
in full (38,154 / 38,545 / 38,587 / 30,321 lines). Prices taken from the
broker's own `[BROKER] <SYM> @ <date> <time> ($<price>)` stream; fills from
`[execution] FILL BUY/SELL`; P&L from `/backtests/<id>/summary`.*
