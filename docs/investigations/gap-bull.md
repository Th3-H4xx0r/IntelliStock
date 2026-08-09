# gap-bull — bt 915207 (bull/chop 2026-01-01..03-01, +9.70% = +4.85%/mo vs +6%/mo target)

Log: `backtests/915207_20260809-153549Z.log` (41,184 lines, 634 bars, 512 equity points).
Method: every price is a `[BROKER] SYM @ <ts> ($px)` line from that log. Clip = 14% of NAV
(`total_spend_cap_target_weight_pct=0.14`); the run's own sized clips are $840-$917, so I use
the log's own per-bar sized dollars where present and $840 otherwise.

## THE ONE NUMBER

**`core_min_pct = 0.25`.** It is the only input to the gate that refused every buy after
2026-01-08.

`backend/core_sleeve.py:305-309`
```
core_min = _f(cfg, "core_min_pct", 0.30)
ceiling  = max(0.05, min(0.95, 1.0 - core_min - cash_fl))   # 1 - 0.25 - 0.02 = 0.73
return max(ceiling, satellite_design_share(cfg, regime=regime))
```
The satellite hit 0.73 of NAV on 2026-01-09 and never came back down (no trim-back). From that
bar to the end of the window the core was asked for **$37,728** of funding and released
**$4,067 (10.8%)** across 16 `[core] funding request trimmed` lines, and the satellite bought
**$109** in seven weeks (NVDA $29 + SNDK $29 + RVLV $51).

## CONVERSION LADDER (915207, whole run)

| stage | count |
|---|---|
| symbols the broker priced in the window | 300 |
| buy signals reaching the sizer (`Pre-sizing signals` / `Pre-queue position sizes`) | **568** |
| stock buys actually sized | **27** |
| `Executable buys` names | 40 (18 bars) |
| `FILL BUY` events | **13** (4 of them SPY core legs) |
| distinct names ever bought | **11** (incl. SPY) |
| names scored/priced and NEVER bought | **289** |
| bars with >=1 buy signal and 0 sized | 30 of 41 |
| bars with `Buy budget: spendable=$0` | 31 of 43 |

Discovery is not the problem. If all 289 unbought names had been bought at one $840 clip at
their first logged price, the log-measured gross would be **+$21,598** on a $6,000 book.

## THE REFUSING GATE IS ONE GATE

42 `SATELLITE CAP` events over 33 unique names. After 2026-01-08 **every** one of them is
terminal — the name never reaches the cash gate or the turnover gate (zero
`Gate skips reported back` lines after 01-08).

That the machinery works is proved in the same log, one day earlier:
```
2026-01-08 15:00  [BROKER] SATELLITE OVERFLOW: AMAT raw=+1.800 >= 1.50 — funding $678 of room out of the core (floor-bounded)
2026-01-08 15:00  [BROKER] SATELLITE CAP: AMAT trimmed $858 -> $678 to keep the core at target
2026-01-08 15:00  [BROKER] TURNOVER BUDGET BYPASS: AMAT raw=+1.800 >= 1.50 — admitting a conviction buy through a 81% budget
2026-01-08 16:00  [BROKER] [execution] FILL BUY AMAT qty=2.31688580 price=281.513199
```
AMAT = +$211.11, the run's #2 P&L line. The identical path fired for SNDK the next day and
returned **$12**:
```
2026-01-09 15:00  [BROKER] max_positions gate armed: held=7, cap=8              <- a slot was free
2026-01-09 15:00  [BROKER] [core] funding request trimmed $3,459 -> $12 — satellite headroom will refuse the remainder
2026-01-09 15:00  [BROKER] SNDK @ 2026-01-09 15:00:00 ($363.01): buy action_intent=backfill_queue_buy
2026-01-09 15:00  [BROKER] SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($12 room); core would be squeezed below its floor
```
`overflow ceiling` == `satellite_max_share` == `1 - core_min_pct - cash_floor`. The words
"core would be squeezed below its floor" name `core_min_pct` directly.

## RANKED, COSTED LIST — SIZED AND REFUSED (all 42 SATELLITE CAP events)

`wanted` is the allocator's own number off the log line; `delivered` is the `FILL BUY` notional
on that bar or the next; `forgone = (wanted - delivered) x (last logged px / refusal px - 1)`.

| bar | sym | wanted | delivered | refusal px | last logged px | ret | $ forgone |
|---|---|---|---|---|---|---|---|
| 2026-01-09 | SNDK | $865 | $0 | $363.01 | $641.26 (02-27) | +76.65% | **+663** |
| 2026-01-12 | SNDK | $866 | $0 | $388.45 | $641.26 (02-27) | +65.08% | **+564** |
| 2026-01-14 | SNDK | $877 | $0 | $393.06 | $641.26 (02-27) | +63.15% | **+554** |
| 2026-01-19 | SNDK | $888 | $0 | $413.55 | $641.26 (02-27) | +55.06% | **+489** |
| 2026-01-15 | WDC | $892 | $0 | $226.08 | $288.78 (02-23) | +27.73% | **+247** |
| 2026-01-28 | SNDK | $904 | $29 | $507.67 | $641.26 (02-27) | +26.31% | **+230** |
| 2026-01-02 | TSEM | $839 | $0 | $119.89 | $134.42 (02-04) | +12.11% | **+102** |
| 2026-01-08 | INTC | $858 | $0 | $41.78 | $45.81 (02-17) | +9.64% | **+83** |
| 2026-01-08 | AMAT | $858 | $652 | $281.70 | $372.65 (02-27) | +32.29% | **+66** |
| 2026-01-20 | ENS | $887 | $0 | $171.12 | $179.07 (02-17) | +4.65% | **+41** |
| 2026-01-20 | GFI | $887 | $0 | $52.55 | $53.40 (02-20) | +1.62% | **+14** |
| 2026-01-16 | NVDA | $891 | $0 | $189.19 | $191.87 (01-28) | +1.42% | **+13** |
| 2026-01-12 | GH | $866 | $0 | $106.42 | $107.88 (02-04) | +1.38% | **+12** |
| 2026-01-09 | BTC | $865 | $0 | $39.81 | $40.20 (01-12) | +0.98% | **+8** |
| 2026-01-20 | CVX | $887 | $0 | $166.20 | $166.20 (01-20) | +0.00% | **+0** |
| 2026-01-20 | CAT | $887 | $0 | $637.65 | $637.65 (01-20) | +0.00% | **+0** |
| 2026-01-05 | CFG | $851 | $0 | $60.85 | $60.85 (01-05) | +0.00% | **+0** |
| 2026-01-19 | UHS | $888 | $0 | $199.80 | $199.80 (01-19) | +0.00% | **+0** |
| 2026-01-28 | BMY | $904 | $0 | $55.09 | $55.09 (01-28) | +0.00% | **+0** |
| 2026-01-28 | NVDA | $904 | $29 | $191.87 | $191.87 (01-28) | +0.00% | **+0** |
| 2026-01-13 | SOUN | $868 | $0 | $11.23 | $11.23 (01-13) | +0.00% | **+0** |
| 2026-01-13 | RGEN | $868 | $0 | $169.56 | $169.56 (01-13) | +0.00% | **+0** |
| 2026-01-12 | SLGN | $866 | $0 | $42.80 | $42.80 (01-12) | +0.00% | **+0** |
| 2026-01-05 | USPH | $851 | $0 | $81.79 | $81.79 (01-05) | +0.00% | **+0** |
| 2026-01-06 | AMCR | $858 | $0 | $42.43 | $42.43 (01-06) | +0.00% | **+0** |
| 2026-01-07 | LLY | $856 | $0 | $1,113.48 | $1,113.48 (01-07) | +0.00% | **+0** |
| 2026-01-07 | GBDC | $856 | $0 | $13.70 | $13.70 (01-07) | +0.00% | **+0** |
| 2026-02-26 | RVLV | $917 | $51 | $25.92 | $25.92 (02-26) | +0.00% | **+0** |
| 2026-01-12 | UBER | $866 | $0 | $84.48 | $84.48 (01-12) | +0.00% | **+0** |
| 2026-01-13 | META | $868 | $0 | $629.51 | $629.51 (01-13) | +0.00% | **+0** |
| 2026-01-15 | C | $892 | $0 | $116.39 | $115.33 (01-23) | -0.92% | **-8** |
| 2026-01-09 | UBER | $865 | $0 | $86.06 | $84.48 (01-12) | -1.84% | **-16** |
| 2026-01-19 | INTC | $888 | $0 | $46.95 | $45.81 (02-17) | -2.42% | **-21** |
| 2026-02-23 | SNDK | $840 | $0 | $683.19 | $641.26 (02-27) | -6.14% | **-52** |
| 2026-01-06 | ETH | $858 | $0 | $30.92 | $29.01 (01-08) | -6.18% | **-53** |
| 2026-01-08 | ARWR | $858 | $0 | $67.73 | $63.48 (02-27) | -6.27% | **-54** |
| 2026-01-14 | GLUE | $877 | $0 | $23.37 | $21.23 (02-03) | -9.14% | **-80** |
| 2026-01-19 | PLRZ | $888 | $0 | $15.41 | $13.90 (02-25) | -9.77% | **-87** |
| 2026-01-06 | AAL | $858 | $89 | $15.86 | $13.30 (01-29) | -16.08% | **-124** |
| 2026-01-09 | RVLV | $865 | $0 | $31.21 | $25.92 (02-26) | -16.96% | **-147** |
| 2026-01-14 | BRKR | $877 | $0 | $49.98 | $40.91 (02-06) | -18.15% | **-159** |
| 2026-01-14 | RVMD | $877 | $0 | $119.00 | $95.37 (02-04) | -19.86% | **-174** |

**Total forgone on names the system sized and then refused: +$2,111 net / +$3,086 gross**
on a $6,000 book (the run made +$582). SNDK alone, counted once at its first refusal, is
**+$663 = +11.1pp**.

## RANKED, COSTED LIST — SCORED BUT NEVER SIZED (top 25 of 264)

$ forgone = $840 x (last logged px / first logged px - 1). This clock needs foresight at bar 1,
so treat it as the discovery upper bound, not an achievable number.

| sym | first logged | last logged | ret | $ @ 14% clip | gate lines seen |
|---|---|---|---|---|---|
| AAOI | 2026-01-08 $34.50 | 2026-02-27 $70.43 | +104.1% | **+875** | RANKBANDx2 |
| LITE | 2026-01-01 $368.58 | 2026-02-26 $687.25 | +86.5% | **+726** | MW_EXTENSIONx7; ENTRY_EXTENSIONx6; RANKBANDx1 |
| VIAV | 2026-01-16 $18.89 | 2026-02-27 $29.23 | +54.7% | **+460** | (no gate line — never signalled) |
| TER | 2026-01-02 $205.38 | 2026-02-27 $316.97 | +54.3% | **+456** | ROT_SKIPx1; BFQ_BLOCKEDx1; DEFERREDx1 |
| MU | 2026-01-01 $285.47 | 2026-02-25 $432.06 | +51.4% | **+431** | ENTRY_EXTENSIONx1; ROT_SKIPx1; BFQ_BLOCKEDx1; DEFERREDx1 |
| CIEN | 2026-01-05 $227.58 | 2026-02-27 $343.75 | +51.0% | **+429** | (no gate line — never signalled) |
| ENLT | 2026-01-01 $45.33 | 2026-02-27 $67.88 | +49.7% | **+418** | RANKBANDx1 |
| DSX | 2026-01-01 $1.66 | 2026-02-26 $2.48 | +49.7% | **+417** | RANKBANDx3 |
| PMN | 2026-02-13 $14.78 | 2026-02-27 $21.30 | +44.1% | **+371** | ENTRY_EXTENSIONx3 |
| TRX | 2026-01-26 $1.26 | 2026-02-27 $1.81 | +43.5% | **+365** | (no gate line — never signalled) |
| DOCN | 2026-01-01 $48.13 | 2026-02-19 $68.20 | +41.7% | **+350** | RANKBANDx1 |
| SPAI | 2026-01-01 $4.17 | 2026-01-15 $5.89 | +41.4% | **+348** | (no gate line — never signalled) |
| GITS | 2026-02-20 $2.32 | 2026-02-27 $3.17 | +36.9% | **+310** | MOM_CEILINGx17 |
| IMMX | 2026-02-10 $6.08 | 2026-02-27 $8.26 | +35.9% | **+301** | ENTRY_EXTENSIONx1 |
| COHR | 2026-01-01 $184.36 | 2026-02-27 $249.78 | +35.5% | **+298** | (no gate line — never signalled) |
| STX | 2026-01-07 $299.53 | 2026-02-27 $405.81 | +35.5% | **+298** | BFQ_BLOCKEDx1; DEFERREDx1 |
| NVST | 2026-01-01 $21.71 | 2026-02-27 $29.20 | +34.5% | **+290** | (no gate line — never signalled) |
| UCTT | 2026-01-16 $43.29 | 2026-02-27 $58.08 | +34.2% | **+287** | MW_EXTENSIONx1; ENTRY_EXTENSIONx1 |
| CNL | 2026-01-20 $15.37 | 2026-02-27 $20.57 | +33.9% | **+284** | (no gate line — never signalled) |
| ZURA | 2026-01-13 $5.11 | 2026-02-27 $6.70 | +31.2% | **+262** | (no gate line — never signalled) |
| CTMX | 2026-01-01 $4.28 | 2026-02-26 $5.57 | +30.1% | **+253** | (no gate line — never signalled) |
| MRNA | 2026-01-15 $39.41 | 2026-02-27 $51.24 | +30.0% | **+252** | (no gate line — never signalled) |
| LASR | 2026-01-15 $44.65 | 2026-02-27 $57.89 | +29.7% | **+249** | (no gate line — never signalled) |
| CECO | 2026-01-01 $59.85 | 2026-02-23 $77.45 | +29.4% | **+247** | RANKBANDx9 |
| TNDM | 2026-01-01 $22.00 | 2026-02-24 $28.46 | +29.4% | **+247** | (no gate line — never signalled) |

## TOP 5 MISSES — EXACT REFUSING GATE AND BAR

1. **SNDK — $663 (largest achievable miss).** Refused 6x, all by the same gate.
   `2026-01-09 15:00 SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($12 room);
   core would be squeezed below its floor` (wanted $865 @ $363.01). Again 01-12 ($21 room,
   $388.45), 01-14 (-$1 room, $393.06), 01-19 (-$28 room, $413.55), 01-28 trimmed $904 -> **$29**,
   02-23 (design share, -$729). On every one of those bars `max_positions gate armed: held=7, cap=8`
   — a slot was free — and `Buy budget: spendable=$0 (cash=$35)`. Ended $641.26 (+76.7%).

2. **AAOI — $875 (upper bound, never sized).** Priced from 2026-01-08 $34.50 -> 02-27 $70.43
   (+104.1%). It never produced a sized buy. Only gate lines:
   `2026-01-15 15:00 Rank band (entry<=#23, exit>#112 of 223): blocked 53 buy(s) [... AAOI ...]`
   and the same on 01-19 (`entry<=#22 of 218`). AAOI was outside the top-22/23 entry band on a
   223-name universe. Note: bt 383778 DID buy AAOI (+$148) — so this is a ranking miss, not a
   capital miss, and it is window-specific.

3. **LITE — $726 (upper bound, never sized).** 2026-01-01 $368.58 -> 02-26 $687.25 (+86.5%).
   `2026-01-01 15:00 V32 mw_buy extension-block: LITE recent runup +30.8% > 25% — no conviction bypass`
   (7x), then `2026-02-10 15:00 Entry extension gate: LITE recent runup +78.2% > 25% — buy blocked` (6x).
   **Do not act on this**: `_RUNS`/OBJECTIVE already measured loosening the extension gate and the
   blocked basket returned -7.95%.

4. **WDC — $247 (achievable).** `2026-01-15 15:00 Executable buys: C, WDC` -> sized $892 ->
   `2026-01-15 15:00 SATELLITE CAP: WDC skipped — satellite at its overflow ceiling (-$37 room);
   core would be squeezed below its floor`. $226.08 -> $288.78 (+27.7%). Same gate as SNDK.

5. **TSEM — $102 (achievable).** `2026-01-02 15:00 SATELLITE CAP: TSEM trimmed $839 -> $450 to
   keep the core at target` then `TURNOVER BUDGET BLOCK: TSEM skipped — 56% of NAV traded in
   21 sessions` -> `Gate skips reported back: TSEM (turnover_budget)`, $0 filled.
   $119.895 -> $134.42 (+12.1%). Two gates in series; SATELLITE CAP fired first.

Runners-up on the achievable clock: INTC +$83 (01-08, trimmed $858 -> $65 then turnover-blocked),
AMAT +$66 (01-08 trim shortfall $206), ENS +$41 (01-20, overflow ceiling).

## THE ONE CHANGE

**`core_min_pct: 0.25 -> 0.05` on doc-193** (equivalently `satellite_max_share` 0.73 -> 0.93).
Nothing else moves. The conviction gate that governs the overflow path
(`satellite_conviction_overflow_min_raw_score = 1.5`) is untouched, so only names at raw >= 1.5
can consume the core — SNDK was raw=+1.700, AMAT raw=+1.800.

Arithmetic on 2026-01-09 (NAV $6,176 from the log's `Budget split: portfolio_total=$6176`):
satellite $4,496 (72.8%), cash $34, core $1,646 (26.6%).
* today: ceiling 0.73 -> room = $12. SNDK refused.
* at 0.05: ceiling 0.93 -> room = $1,248, core releasable $1,337. SNDK's $865 clip funds.

| SNDK entry actually available | $ gain | less SPY funding leg (-0.65%) | run becomes |
|---|---|---|---|
| 01-09 $363.01 (same bar) | +$663 | +$669 | **+20.84% = +10.42%/mo** |
| 01-12 $388.45 (+1 bar for the sell to settle — the known cash race) | +$564 | +$569 | **+19.19% = +9.59%/mo** |
| 01-19 $413.55 (worst of the five refusals) | +$489 | +$495 | **+17.95% = +8.97%/mo** |

The gap to close is 1.15%/mo. The worst case above closes it **3.5x** on this one name, before
WDC (+$247) and TSEM/INTC/ENS.

Cost of the capital being released: SPY returned **+0.64%** over the whole window
(`stock_price_change.SPY: 681.82 -> 686.16`) and contributed **+$8.97** of the run's +$581.83.
From 01-09 to the end SPY was **-0.65%**. The core is not paying for the room it occupies.

### Generalisable? Yes on bull/chop, inert in bear — 3 windows

| run | window | `[core] funding request trimmed` | requested | released | SATELLITE CAP events |
|---|---|---|---|---|---|
| 915207 | bull/chop 01-01..03-01 | 16 | $37,728 | $4,067 (10.8%) | 42 |
| 383778 | OOS bull 03-30..04-27 | 16 | $50,523 | $8,695 (17.2%) | 41 |
| 542754 | bear 03-02..03-30 | **0** | - | - | **0** |

383778 shows the identical terminal lines — `[core] funding request trimmed $3,486 -> $0`
(04-14), `$3,485 -> $0` (04-15), `$2,533 -> $0` (04-13), `$3,686 -> $16` (04-21) — and 13
overflow-ceiling skips. Costing those at that run's $880 clip from refusal price to last logged
price: **+$591 gross / +$283 net** (LWLG 04-09 $8.25 -> $12.53 = +$457; INTC +$56; FTH +$36;
against OXY -$73, NFLX -$92). Smaller than 915207 because the window is 4 weeks, but the same
mechanism and the same sign.

542754 (bear) has **zero** SATELLITE CAP lines and zero funding-trim lines — doc-193 has no bear
profile, so the core is off and `core_min_pct` is never read. **The change cannot touch the bear
leg.** That is the safety argument, and it is measured, not assumed.

### Honest limits
* The +$663 SNDK number is one name in one window. The *mechanism* (core floor is the terminal
  gate, requested >> released) is on 2 of 2 bull/chop windows; the *dollar size* is 915207-specific.
* Turnover is the second gate in series. It did not bind on the SNDK bars (`TURNOVER BUDGET
  BYPASS` admitted AMAT at raw=1.800 through an 81% budget on 01-08, and SNDK was raw=1.700 >=
  1.5), but if SATELLITE CAP is opened, watch for `TURNOVER BUDGET BLOCK` becoming the new
  terminal gate on the raw<1.5 names. That would be a *good* outcome (churn stays braked,
  conviction gets through).
* This does NOT fix AAOI (rank band) or LITE (extension gate). Those are ranking problems, and
  the extension gate is already on the DO-NOT-RETRY list.
* Lowering the floor removes the de-risk that `core_min_pct` provides. 915207's max drawdown was
  3.78%; expect it to rise roughly with the extra satellite share.

## WHAT I DID NOT RE-DO
`_SYNTHESIS.md` items 1-6, `entry-conversion.md`, `capital-and-cash.md` (the `_fr_room` /
`satellite_max_share` derivation at `broker.py:14409-14432`, `core_sleeve.py:212-309`),
`churn-and-cost.md`, `why-late-per-name.md`. This note only adds the 915207-specific enumeration,
the costing, and the observation that `core_min_pct` — not the design share, not max_positions,
not cash, not turnover — is the single terminal input in this run.
