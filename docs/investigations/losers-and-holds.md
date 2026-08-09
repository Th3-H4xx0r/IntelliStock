# Losers and Holds — bt 201039 (2026-01-01..2026-03-01, v2-let-run-core, $6,000, 3600s)

Method: full log of bt 201039 pulled with `python3 scripts/pull_backtest_logs.py 201039 --stdout`
(40,324 lines). Cross-checked against the local logs of bt 820236 (+12.33%) and bt 613166
(+9.17%) on the same window/instance. Every number below is reconstructed from the run tape
(`[execution] FILL ...`, `Buy gate inputs ...`, `Monitor decision ...`), not from config.

Run result: `Initial Value: $6,000.00 / Final Value: $6,508.68 / Profit & Loss: +$508.68 (+8.48%)`,
36 trades (19 buys / 17 sells). Non-SPY alpha book: $8,062 of gross buys, +$490.83 realised+unrealised.

---

## 0. THE ONE-LINE ANSWER

**We did not fail to hold the big movers. We bought them after they had moved.**

The complete non-SPY buy tape of bt 201039, sorted by how far the name had already travelled
from the window's opening price at the moment we filled (`ext@entry = fill / window_start - 1`):

| entry date | sym | fill $ | window start | ext@entry | window end | left from OUR entry | our result | notional |
|---|---|---|---|---|---|---|---|---|
| 2026-01-02 | TCMD | 28.16 | 28.96 | **-2.8%** | 29.29 | +4.0% | **+8.13% / +$85.17** | $1,048 |
| 2026-01-02 | XOM | 120.24 | 120.33 | **-0.1%** | 152.59 | +26.9% | **+26.90% / +$225.25** | $837 |
| 2026-01-02 | NTR | 61.91 | 61.73 | **+0.3%** | 75.06 | +21.2% | **+21.23% / +$178.30** | $840 |
| 2026-01-02 | VOYA | 75.15 | 74.52 | +0.8% | 66.90 | -11.0% | -0.79% / -$6.62 | $840 |
| 2026-01-02 | BA | 222.80 | 217.10 | +2.6% | 227.51 | +2.1% | +2.11% / +$0.73 | **$35** |
| 2026-01-06 | AAL | 16.02 | 15.32 | +4.6% | 13.09 | -18.3% | -12.33% / -$10.94 | $89 |
| 2026-02-02 | EGO | 38.87 | 35.92 | **+8.2%** | 46.43 | +19.5% | **+19.45% / +$167.35** | $860 |
| 2026-01-20 | AVNT | 35.64 | 31.25 | +14.0% | 41.07 | +15.2% | +1.12% / +$9.77 | $874 |
| 2026-01-27 | HL | 28.23 | 19.19 | **+47.1%** | 24.89 | **-11.8%** | **-18.54% / -$24.16** | $130 |
| 2026-01-30 | WDC | 259.37 | 172.27 | **+50.6%** | 278.93 | **+7.5%** | +7.54% / +$58.17 | $772 |
| 2026-01-16 | PLRZ | 15.48 | 8.11 | **+90.8%** | 13.12 | **-15.2%** | **-17.61% / -$154.46** | $877 |
| 2026-02-02 | SNDK | 660.48 | 237.33 | **+178.3%** | 631.54 | **-4.4%** | **-4.38% / -$37.73** | $860 |

Split by entry extension, bt 201039 alpha book:

```
ext@entry <= +14%   n=8   notional $5,423   P&L  +$649.01
ext@entry >  +45%   n=4   notional $2,639   P&L  -$158.18
```

**36.8% of the risk capital was deployed at more than +45% extension and lost $158.18.
The other 63.2% was deployed at +14% or less and made $649.01.**

The "big mover" framing in the brief is a proxy for the real variable. SNDK's move was +166.1%
and WDC's +61.9% — but at OUR entry price the move left was **-4.4%** and **+7.5%**. For the six names held to the
close (XOM, NTR, BA, WDC, SNDK, EGO) the column `left from OUR entry` equals `our result` to
within **0.1pp** — the entry price alone determines the outcome. The six that were sold differ
only by the exit, and by at most 14.1pp (AVNT, sold on 01-30 to fund WDC). There is no holding
problem to explain; there is an entry-price problem.

---

## 1. PLRZ — the two trades, reconstructed exactly

PLRZ is the single biggest loss in the run: **-$154.46 (-17.61%) on a stock that went +61.84%.**
It is exactly two fills.

**Trade 1 — BUY, 2026-01-16**
```
[BROKER] PLRZ @ 2026-01-16 15:00:00 ($15.01): buy action_intent=momentum_watchlist_buy
[BROKER] SATELLITE OVERFLOW: PLRZ raw=+1.700 >= 1.50 — funding $1,777 of room out of the core (floor-bounded)
[BROKER] TURNOVER BUDGET BYPASS: PLRZ raw=+1.700 >= 1.50 — admitting a conviction buy through a 111% budget; the brake is for churn, not for the trade that matters
[BROKER] Buy gate inputs for PLRZ: cash=$1818.27 reserved=$0.00 floor=$120.00 effective_floor=$0.00 high_conv=True open_pos=5 cash_per_trade=$877.05 available=$1818.27 cash_to_use=$877.05 → PASS
[BROKER] [execution] FILL BUY PLRZ qty=56.67227162 ... price=15.475358 ... quote=2026-01-16 16:00:00+00:00
```
$877.02 = **14.0% of NAV**, the largest single position opened in the run.

**Trade 2 — SELL, 2026-01-30 (circuit breaker)**
```
[GNA] [sell-gate] PLRZ | gate=circuit_breaker | tier=LOW | regime=bull | unrealized=-13.4% | floor=-10.0% (base=-10%) | result=fired
[BROKER] PLRZ @ 2026-01-30 16:00:00 ($13.4): sell (weighted scores from 0 strategies)
[BROKER] [execution] FILL SELL PLRZ qty=56.67227162 ... price=12.750734 ... quote=2026-01-30 17:00:00+00:00
```
$722.61 back. Realised **-$154.41**.

**What the stock did.** Every price below is from this run's own decision/monitor lines:

```
01-01  8.110   01-02  8.110   01-05 11.795   01-06 13.195   01-07 13.060   01-08 12.470
01-09 13.330   01-12 12.520   01-13 13.240   01-14 14.340   01-15 14.500
01-16 15.010  <-- SIGNAL,  filled 15.475 one bar later
01-19 15.410   01-20 14.430   01-21 15.890   01-22 15.875   01-23 15.850
01-26 15.050   01-27 14.190   01-28 14.650   01-29 13.400   01-30 13.400  <-- SOLD 12.751
end of window: 13.12
```

The +61.84% "stock move" is the **$8.11 -> $13.12** endpoint pair. The stock's actual high was
~$16.00 on 2026-01-22. We bought at $15.475. **Maximum unrealised gain ever available to this
position was +3.4%** (`Monitor decision: PLRZ ... cp=$16.00 entry=$15.48`). There was never a
+61.84% to capture from our cost basis; there was a -15.2% to the window close.

**Why we bought it there — the gate that was supposed to stop this actually caused it.**
The entry-extension gate refused PLRZ **six times on the way up**, at exactly the prices we
wanted:

```
date       price   gate line
2026-01-01 $8.11   V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25% — no conviction bypass
2026-01-01 $8.11   Entry extension gate: PLRZ recent runup +106.2% > 25% — buy blocked
2026-01-02 $8.11   V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25%
2026-01-05 $11.80  V32 mw_buy extension-block: PLRZ recent runup +97.2%  > 25%
2026-01-06 $13.20  V32 mw_buy extension-block: PLRZ recent runup +78.8%  > 25%
2026-01-08 $12.47  Entry extension gate: PLRZ recent runup +83.6% > 25% — buy blocked
--- no PLRZ extension line anywhere from 2026-01-09 onward ---
2026-01-16 $15.01  BOUGHT
```

PLRZ was on the momentum watchlist from bar 1 (`Momentum watchlist: ... top3=[('TCMD', 0.567),
('PLRZ', 0.547), ...], new_buys=['TCMD', 'PLRZ']`, 2026-01-01) at **$8.11**. The gate blocked it
at $8.11 and released it at $15.48. Buying at the release price instead of the block price cost
the difference between +61.8% and -17.6%.

**Mechanism (verified in code).** The gate does not measure "runup". It measures the **hi/lo
range of the last N bars**:
```
backend/strategies/graph_nexus_analysis.py:9278-9280
    lo = min(closes);  hi = max(closes)
    runup_pct = ((hi - lo) / lo) * 100.0
```
with `entry_extension_lookback_bars` default 20 (`:5538`, `:23228`), cadence-scaled by
`_scale_bars` (`:257`); at this run's 3600s cadence that is **20 hourly bars ≈ 3 trading
sessions**. The gate's own reason string says it: `"Entry extension gate: recent {N}-bar range
ran +X%"` (`:23244`). A name that doubles in three days and then chops sideways for three days
has a **small 20-bar range** and passes. That is precisely the top.

The consequence is not "the gate is too loose" or "too tight" — it is that **the release of the
block carries no information about the trade**. It is a volatility read, not a position-in-move
read, and in this run it flipped from BLOCK to ALLOW within two sessions of the high on every
name it touched.

**Independent replication in bt 613166.** Same window, same instance, different run:
```
2026-01-16 $15.01  momentum_watchlist_buy -> TURNOVER BYPASS CEILING: PLRZ refused (95% of NAV)
2026-01-19 $15.41  momentum_watchlist_buy -> refused (96%)
2026-01-21 $14.43  momentum_watchlist_buy -> refused (95%)
2026-01-22 $15.88  Entry extension gate: PLRZ recent runup +115.3% > 25% — buy blocked
2026-01-23 $15.56  V32 mw_buy extension-block: PLRZ +115.3%
2026-01-26 $15.05  V32 mw_buy extension-block: PLRZ +108.0%
2026-01-27 $15.05  gate PASSES again; refused only for cash (cash=$3.53 → SKIP)
2026-01-28 $15.19  BOUGHT: FILL BUY PLRZ qty=5.84196785 price=14.683549
2026-01-30         [sell-gate] PLRZ circuit_breaker -13.0% fired; FILL SELL price=11.523551
```
Result in 613166: **-$18.47 (-21.53%)** on the same +61.84% stock. Two runs, two entries
($15.48 and $14.68 — 1.91x and 1.81x the 01-01 price), two circuit-breaker exits on the same
day, two losses. bt 820236 never bought PLRZ at all (zero mentions) and is the best of the three.

---

## 2. HL — the same signature in three sessions

```
2026-01-23  V32 mw_buy extension-block: HL recent runup +65.7% > 25% — no conviction bypass
2026-01-26  V32 mw_buy extension-block: HL recent runup +68.6% > 25% — no conviction bypass
2026-01-27  (no extension line)  HL @ 2026-01-27 15:00:00 ($28.525): buy action_intent=momentum_watchlist_buy
2026-01-27  Buy gate inputs for HL: cash=$132.00 ... cash_per_trade=$838.79 available=$132.00 cash_to_use=$132.00 → PASS
2026-01-27  [execution] FILL BUY HL qty=4.61693063 price=28.234510
2026-01-28  Monitor decision: HL day 1 pnl=+0.3% cp=$28.32 entry=$28.23 → HOLD        <-- lifetime high
2026-01-30  [sell-gate] HL | gate=circuit_breaker | tier=MID | unrealized=-16.4% | floor=-15.0% | result=fired
2026-01-30  [execution] FILL SELL HL qty=4.61693063 price=23.002205
```

- Blocked twice as too extended, admitted on the third session **at a higher price**.
- Entry $28.23 vs window start $19.19 = **+47.1% already gone**; window close $24.89 is
  **below our entry**. Best possible outcome from this cost basis was -11.8%.
- **Maximum unrealised gain ever: +0.3%** (one bar). It was underwater for 25 of its 26
  monitor observations.
- Held 3 sessions. -$24.16 (-18.54%).

HL is also a **cash-race artefact**: sized $838.79, gate saw `cash=$132.00`, filled $130.36
= 15.5% of intended size. The same tick sold SPY ($708.47) and AAL ($77.85) whose proceeds
($786.32) had not settled when the buy gate read cash. (This is synthesis root-cause #1,
independently reproduced in 201039.) Here the bug *saved* $131 of loss; on 2026-01-02 the same
bug cost the book a real position — see §5.

---

## 3. SNDK — the loss is one hour wide

```
[BROKER] SNDK @ 2026-02-02 15:00:00 ($617.375): buy action_intent=momentum_watchlist_buy
[BROKER] Buy gate inputs for SNDK: cash=$1766.81 ... cash_to_use=$860.44 → PASS
[BROKER] [execution] FILL BUY SNDK qty=1.30271230 price=660.479056 quote=2026-02-02 16:00:00+00:00
```
**Signal price $617.375. Fill price $660.479. +6.98% in one bar, $56.15 of the $860 clip.**
SNDK finished the window at $631.54.

- Filled at $660.48 → **-4.38% / -$37.73**.
- Had it filled at the decision price $617.375 → **+2.3% / +$18.4**.
- The entire SNDK "loss" is the gap between the bar we decided on and the bar we executed on.

MFE while held: +5.5% (`cp=$696.66`, 2026-02-03). MAE: -18.1% (`cp=$540.72`, 2026-02-10). The
hold machinery did its job — it never sold. There was simply nothing to hold onto: we owned the
name from $660 while its whole +166% happened between $237 and $660.

**Cross-run control (same name, same window, same code family).**

| run | entry dates | fill prices | avg cost | result |
|---|---|---|---|---|
| 820236 | 01-20, 01-29, 02-23 | 443.83 / 517.69 / 679.70 | $523.80 | **+20.57% / +$100.95** |
| 613166 | 02-05, 02-23 | 592.01 / 679.70 | $616.76 | +2.39% / +$3.04 |
| 201039 | 02-02 | 660.48 | $660.48 | **-4.38% / -$37.73** |

Tranche-level, all three runs, held to the $631.54 close — monotone with entry price, 5 for 5:

```
$443.83 (820236, 01-20)  -> +42.3%   +$54.03
$517.69 (820236, 01-29)  -> +22.0%   +$54.96
$592.01 (613166, 02-05)  -> + 6.7%   + $5.84
$660.48 (201039, 02-02)  -> - 4.4%   -$37.70
$679.70 (both,   02-23)  -> - 7.1%   -$10.82
```
Even inside the winning run, the last tranche lost money. **Nothing about SNDK was different
between runs except when we bought it.**

**Why 201039 was 13 sessions late on SNDK — it never discovered it.** On bar 1:
```
820236, 2026-01-01:  Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%)
                     Discovered stock (momentum): WDC  (20d= +7.7%, 60d=+37.5%)
                     ... also TE, VICR, CRCD, CORD, TSEM, MU, VIAV, LRCX-adjacent names
                     Discovery source usage: propagation=14, sector_peer=6, competitor=4, momentum=87, trend_etf=6, unknown=9

201039, 2026-01-01:  Discovered stock (momentum): DZZ, MAAS, TLSI, OBIO, TNDM, PILL, PROF,
                     BBC, SBIO, AGMI, COPP, C     <-- no SNDK, no WDC, no VICR, no MU
                     Discovery source usage: propagation=13, momentum=43, trend_etf=6, unknown=64
```
Both runs took the top-12 by 60d return. 201039's momentum candidate pool was **43 vs 87** and
the graph lanes (`sector_peer`, `competitor`) produced **zero**. SNDK first enters 201039's log
on 2026-01-30 as a *propagation neighbour of WDC*, and reaches the watchlist on 02-02 —
by which time the only price available was $617/$660. This is upstream of entry logic; it
belongs to the discovery investigation, and it is the reason the extension question even arises
for SNDK in this run.

---

## 4. AAL and VOYA — a different, much smaller failure

These two are **not** the same family and should not be fixed by the same lever.

- **AAL** — bought 2026-01-06 at $16.02 vs window start $15.32 (+4.6% ext). The stock fell
  -14.59% over the window. MFE while held **+2.7%**; underwater on **150 of 156** monitor
  observations. Circuit breaker fired 2026-01-27 at -10.9%, sold $14.048; the window closed at
  $13.09, so **the exit saved $5.31**. Loss: -$10.94 on an $89 position.
- **VOYA** — bought 2026-01-02 at $75.15 vs start $74.52 (+0.8% ext). The stock fell -10.22%.
  Sold 2026-01-20 at $74.56 by a portfolio swap. Realised -$6.62 (-0.79%) versus -$92.18 if
  held to the close. **The exit saved $85.56.**

Combined damage: **-$17.56, 3.5% of the run's +$508.68.** Both were bought early and cheap;
they were simply wrong names. This is signal quality, and it is not where the money is.

---

## 5. BA — a dead slot for 56 sessions

```
2026-01-02 [BROKER] SATELLITE CAP: BA trimmed $839 -> $817 to keep the core at target
2026-01-02 [BROKER] Buy gate inputs for BA: cash=$2434.77 ... cash_to_use=$817.00 → PASS
2026-01-02 [BROKER] Buy gate inputs for LMT: ... cash_to_use=$817.00 → PASS
2026-01-02 [execution] FILL BUY SPY  qty=3.51184282 price=682.970444   ($2,398.48)
2026-01-02 [execution] FILL BUY BA   qty=0.15606141 price=222.799049   (   $34.77)
                                     (no LMT fill anywhere in the run)
```
$2,398.48 + $34.77 = $2,433.25 — **exactly the $2,434.77 the gate saw**. The core SPY deploy
had been submitted on the previous tick (`[core] bought $2400.00 SPY @ 681.82 ... accepted=True,
filled=False`) and was invisible to the gate. **$1,634 of approved satellite buys became $34.77
of fills (2.1%).**

BA then sat in the book for all 56 sessions at **$34.77 -> $35.51**, returning +$0.73, while:
- `max_positions gate armed: held=6, cap=6` on **553 of 634 bars (87.2%)**;
- `MAX_POSITIONS_GATE: blocked` fired **25 times** (AMAT, RVMD ×3, GLUE ×3, AMZN, ASML, INCY,
  SONO, CAT, CYTK, GFI, LLY ×2, ...);
- `Regime capacity gate (Z4.1): regime=chop max_positions 6->8` fired 41 times and
  `6->14` twice — **and the armed cap read 6 on every one of the 634 bars** (synthesis #2,
  reproduced here).

So one of six alpha slots held **0.55% of the book's equity** for the entire run.
Same pattern in 820236: BA approved $838.87, filled $107.02, book full on **599/634 bars
(94.5%)**, **45** MAX_POSITIONS blocks. A $100 minimum already exists elsewhere in the same
pipeline (`ETF min-size filter: funded 3 ETF(s) @ $133 each, skipped 1: ['AIQ'] (min=$100)`;
`V28 BFQ DRAIN ENTRY: ... min_pos=$100`) but is applied to the **sized allocation**, never to
the **fill**.

---

## 6. Rotations: we systematically sold early-in-move to buy late-in-move

bt 201039 fired three rotations/swaps. Reconstructed against "do nothing":

**(a) 2026-01-15 `Momentum rotation: sell TCMD (score=0.348) → buy PLRZ (score=0.933, $1120)`**
```
TCMD  cost $1,047.71  sold $1,132.94  (+$85.23)   hold-to-close would be $1,089.78 (+$42.07)
PLRZ  cost   $877.02  sold   $722.61  (-$154.41)
net vs simply holding TCMD:  -$111.25
```
TCMD's ext@entry was **-2.8%** (bought below the window's opening price). PLRZ's was **+90.8%**.
The rotation moved capital from the least-extended holding to the most-extended candidate.

**(b) 2026-01-30 `Momentum portfolio swap: sell AVNT (pnl=+0.5%) → buy WDC (score=0.768, $878)`**
```
AVNT  cost $873.56  sold $883.39 (+$9.83)   hold-to-close would be $1,006.76 (+$133.19)
WDC   cost $771.74  close value $829.93 (+$58.19)
net vs simply holding AVNT:  -$65.18
```
AVNT ext@entry **+14.0%** (it went on to +31.4% for the window); WDC ext@entry **+50.6%**.
Again: sold the earlier name to buy the later one.

**(c) 2026-01-20 `Momentum portfolio swap: sell VOYA (pnl=-1.0%) → buy SOC (score=0.938, $874)`**
VOYA was sold. **SOC was never bought** — there is no SOC fill in the run (synthesis #5). This
one happened to save $85.56 because VOYA fell.

Net rotation cost in 201039: **-$90.87**.

**Cross-run.** bt 613166, `2026-01-15 Momentum rotation: sell HESM (score=0.036) → buy RVMD
(score=0.862, $869)`: HESM sold for $845.47 (+$10.19); **RVMD never filled**; HESM would have
been worth $940.45 at the close (+$104.99). **-$94.80.** HESM's ext@entry was **-0.3%** — it
was evicted *because it had not moved yet*, and it then moved +12.19%.
bt 820236 fired **zero** rotations and is the best of the three runs.

**Mechanism (code).** The rotation/eviction key is `_score_momentum_rank`
(`graph_nexus_analysis.py:21335-21345`): a weighted sum of trailing returns,
`0.05·r(10) + 0.20·r(20) + 0.25·r(21) + 0.25·r(42) + 0.25·r(63)` bars. It ranks names by
**how much they have already gone up**. Evicting the lowest-scoring holding for the
highest-scoring candidate is, by construction, selling the name earliest in its move to buy the
name latest in its move. Both sides of every rotation in these runs are the wrong side.

---

## 7. Sizing is blind to signal quality

Every admitted buy in bt 201039 got the same clip:
```
V31.2 total-spend cap [CONCENTRATE]: funded 4 of 6 by conviction (PLRZ@$877, RVMD@$877, BTC@$877, GLUE@$877) out of $3,947
V31.2 total-spend cap [CONCENTRATE]: funded 4 of 4 by conviction (EGO@$860, SONO@$860, SNDK@$860, META@$860) out of $3,872
```
$840–$877 ≈ 14% of NAV for everything. The reason the downstream gates cannot discriminate is
`graph_nexus_analysis.py:21542-21547`:
```python
_eta_e_floored = max(score, 1.50)
_eta_e_diff    = min(0.20, max(0.0, float(score)) * 0.5)
_eta_e_raw_net = _eta_e_floored + _eta_e_diff
```
Every momentum-watchlist pick is floored to `raw_net_score ∈ [1.50, 1.70+]`, which is at or above
the 1.50 threshold used by `SATELLITE OVERFLOW` and `TURNOVER BUDGET BYPASS`. Measured in the run:

| name | natural momentum score | logged raw | outcome |
|---|---|---|---|
| TCMD | 0.567 | `raw=+1.700` | +$85.17 |
| HL | 0.876 | `raw=+1.700` | -$24.16 |
| PLRZ | 1.130 | `raw=+1.700` | -$154.46 |
| SNDK | 1.505 | `raw=+1.705` | -$37.73 |

A 0.876 and a 1.505 are indistinguishable to every gate that matters. "Conviction" in this
codebase means "came through the momentum lane", not "is a better trade".

---

## 8. The momentum lane bypasses the quality filter entirely

The run's own config banner: `quality=avgvol>=500000 mcap>=$1000M(block)`.
PLRZ's market cap in this run: **$12M** (`conviction_tier: sym=PLRZ tier=LOW mcap=12M`).
TCMD's: **$589M**. Both bought.

Demonstrated inside a single bar on 2026-01-01:
```
line 2666  [GNA] Nexus quality filter: blocked 5 low-quality buy(s): PESI, TCMD
line 2677  [GNA] Momentum watchlist: ... new_buys=['TCMD', 'PLRZ']
line 2846  [BROKER] TCMD @ 2026-01-01 15:00:00 ($28.96): buy action_intent=momentum_watchlist_buy
line 2847  [BROKER] SATELLITE OVERFLOW: TCMD raw=+1.700 >= 1.50 — funding $4,380 of room out of the core
line 2848  [BROKER] TURNOVER BUDGET BYPASS: TCMD raw=+1.700 >= 1.50
```
The quality filter blocked TCMD; eleven lines later the momentum lane re-promoted it.

**Mechanism (code).** `_apply_quality_filter` is called at `graph_nexus_analysis.py:27673`.
The momentum lane writes its picks into `scores` at `:28657-28658`
(`scores[t]["score"] = 1; scores[t]["action_intent"] = "momentum_watchlist_buy"`) — **after** the
filter has already run. The module comment at `:23254-23255` states it outright: *"The momentum
reserved-buy lane injects picks AFTER this filter and bypasses the bear RS gate."* The mcap
floor, the volume floor and the negative-raw-score check are simply never applied to these names.

**What that costs on a $12M-cap name.** Round-trip execution on PLRZ, measured:
```
201039 BUY   decision $15.010 -> fill $15.475   +3.10%   $26.37
201039 SELL  decision $13.400 -> fill $12.751   -4.85%   $36.80
613166 BUY   decision $15.190 -> fill $14.684   -3.33%  (favourable)
613166 SELL  decision $12.780 -> fill $11.524   -9.83%   $7.34
```
$63.17 of the $154.46 loss (**41%**) is decision-to-fill drag on a nano-cap. Total non-SPY
decision-to-fill drag in 201039: **+$91.52 over 19 fills**, of which PLRZ $63.17 and SNDK $56.15
(offset by favourable prints on TCMD -$42.26 and WDC -$21.50).

---

## 9. Holds and exits: this is NOT the leak — it is what worked

43 `[sell-gate]` decisions in bt 201039: **9 fired, 34 blocked**
(`winner_protect` 19, `llm_sell_min_hold` 15, `circuit_breaker` 9), plus 12
`V31 grace SUPPRESS` (EGO ×9, WDC ×2, SPY ×1).

Without the hold machinery the run loses its winners:
```
[sell-gate] EGO | gate=llm_sell_min_hold | held=1d < 15d | raw=-0.850 | result=blocked (hold instead)   (×7 for EGO)
[sell-gate] WDC | gate=llm_sell_min_hold | held=4d < 15d | raw=-0.844 | result=blocked (hold instead)
[sell-gate] XOM | gate=winner_protect | pnl=+15.8% | drop_from_peak=0.9% < 8.0% | result=blocked (hold)
[sell-gate] EGO | gate=winner_protect | pnl=+20.5% | drop_from_peak=2.0% < 8.0% | result=blocked (hold)
```
The four names that were challenged and blocked — EGO (+$167.35), WDC (+$58.17),
XOM (+$225.25) and BA (+$0.73) — total **+$451.50**, i.e. 89% of the run's +$508.68.
(NTR, +$178.30, was never challenged at all: `[sell-gate]` fires only on EGO ×15, WDC ×10,
PLRZ ×7, BA ×6, XOM ×2, SPY, AAL, HL.)

Circuit-breaker exits vs holding to the close, all three runs:
```
201039 AAL   sold $14.05  close $13.09   +$5.31
201039 PLRZ  sold $12.75  close $13.12   -$20.93
201039 HL    sold $23.00  close $24.89   -$8.72
820236 CORD  sold $32.08  close $17.68  +$302.30
820236 OMER  sold $12.07  close $12.05   +$1.42
613166 PLRZ  sold $11.52  close $13.12   -$9.33
613166 NVDA  sold $175.49 close $177.21  -$7.63
                                        --------
                          NET           +$262.43
```
The exit stack is **net +$262.43 across the three runs**, and in 201039 it costs $24.34 — 4.8%
of the run's P&L. Confirms the existing `DO NOT TOUCH` in `_SYNTHESIS.md`, from an independent
reconstruction. **Do not spend effort here.**

---

## 10. Is there a common signature?

Tested against the tape. Of the five candidate signatures in the brief:

| candidate signature | verdict | evidence |
|---|---|---|
| "all bought after a vertical move" | **YES** | PLRZ +90.8%, SNDK +178.3%, HL +47.1%, WDC +50.6% extension at entry vs +0.3%/-0.1%/+8.2% for NTR/XOM/EGO |
| "all bought right after the extension gate released them" | **YES for PLRZ and HL** (and for SNDK in 820236); NOT for SNDK/WDC in 201039, which were never gated because they were never discovered until late | §1, §2, §3 |
| "all came through the momentum-watchlist lane" | **YES, 4 of 4 late entries** — PLRZ, HL, SNDK are `action_intent=momentum_watchlist_buy`; WDC is `momentum_watchlist_portfolio_swap`. Every entry at ext<=+14% (XOM, NTR, VOYA, BA, AAL, EGO, AVNT) is `initial_buy`; the single exception is TCMD, a momentum-lane pick bought at **-2.8%** ext on bar 2, which made +$85.17 | buy-tape §0 |
| "all sized largest" | **NO** | sizing is flat $840–$877 for every admitted buy; HL was $130 (cash race), BA $35 |
| "all the same bar type" | **NO** | all 19 buys fill on the 16:00 quote after a 15:00 decision; no differentiation |

**The signature is: `momentum_watchlist` lane + entry more than ~45% above the window base +
a single full-size clip at one price.** Four for four. Note that one of the four, WDC, still
made money (+$58.17) — but it captured **7.5 of the stock's 61.9 points**, and the same name
bought on bar 1 in bt 820236 captured **53.6**. Late entry does not only lose; it converts a
+53.6% trade into a +7.5% trade.

The winners are the mirror image: XOM, NTR, VOYA, BA (2026-01-02) and TCMD (2026-01-02) are the
**opening basket**, bought at ±3% of the window's opening price and held 56 sessions; EGO
(2026-02-02, +8.2% ext) and AGMI in 613166 (2026-01-08, +0.7% ext) are late-started but bought
near their base. `left from OUR entry` for the six winners averages **+21.0%**; realised
average **+19.4%**. They did not need any special handling. They needed only to be bought
before the move.

---

## 11. RANKED — what to change

Ordered by (evidence strength × dollars), with the generalisability test applied to each.
Nothing here is tuned to a threshold that separates SNDK from PLRZ, because — see §12 — **no
threshold on the extension axis does.**

### 1. Apply the existing quality floor to the momentum-watchlist lane
**Change.** Run `_apply_quality_filter`'s mcap / dollar-volume / negative-raw checks on
momentum-lane picks, i.e. move the injection at `graph_nexus_analysis.py:28657` before
`:27673`, or re-run the filter on the injected set. Set the floor at the value the run already
advertises in its own version banner (`MCAPPREFILTER200M`, $200M), not the $1,000M config value.

**Expected effect.**
- bt 201039: PLRZ ($12M) never bought → **+$154.46**; the TCMD→PLRZ rotation never fires so
  TCMD is held to the close instead of sold (+$42.07 instead of +$85.23, -$43.16).
  **Net +$111.25 → run goes +8.48% → ≈ +10.3%.**
- bt 613166: PLRZ ($12M) never bought → **+$18.47.**
- bt 820236: no PLRZ, no effect. TCMD ($589M) survives a $200M floor, so the one momentum-lane
  winner is untouched.

**Why it generalises.** It is not a price or timing rule; it is a size-of-instrument rule with a
liquidity mechanism that holds in every window and regime: a $12M-cap name cannot absorb a
14%-of-NAV clip. The tape prices this directly — PLRZ's exits filled **-4.85%** (201039) and
**-9.83%** (613166) below the decision price. Both runs that touched PLRZ improve; the run that
never touched it is unaffected. **Recommend.**

### 2. Do not let a fill below the minimum position size keep a max_positions slot
**Change.** The $100 minimum already applied to ETF sizing (`ETF min-size filter ... (min=$100)`)
and to BFQ drain (`min_pos=$100`) should be applied to the **filled** notional. A buy that fills
below it is cancelled/flattened rather than retained as a position.

**Expected effect.** Frees 1 of 6 alpha slots for 56 sessions in bt 201039 (BA, $34.77, +$0.73)
and in bt 820236 (BA, $107.02, +$2.26) — in books that were at `held=6, cap=6` on 87.2% and
94.5% of bars respectively, refusing 25 and 45 buys.

**Why it generalises.** Purely structural: it makes no forecast. A position that cannot pay for
its slot should not hold one, in any window or regime. **The dollar benefit is not directly
measurable from these logs** (it depends on what fills in the freed slot), so this must be run
paired rather than asserted. **Recommend, with paired validation.**

### 3. Forbid rotations that increase the book's average entry-extension
**Change.** A rotation/swap must not evict holding `H` for candidate `C` when `C` is more
extended above its base than `H` is. Today the only comparison is the trailing-return score
(`_score_momentum_rank`, `:21335-21345`), which guarantees the opposite ordering.

**Expected effect.**
- bt 201039: TCMD→PLRZ (**+$111.25**) and AVNT→WDC (**+$65.18**) both blocked. VOYA→SOC blocked
  too (**-$85.56**, that one was accidentally profitable). Net **+$90.87**.
- bt 613166: HESM→RVMD blocked (**+$94.80**). NVDA→SNDK blocked (-$1.79). Net **+$93.01**.
- bt 820236: zero rotations, no effect — and it is the best run.

**Why it generalises.** The mechanism is stated and window-independent: ranking on trailing
return means the incoming name is by construction later in its move than the outgoing one, so a
rotation is a systematic transfer from "move remaining" to "move spent". Positive on both runs
that rotate; neutral on the one that does not. **Recommend.**

### 4. Stop treating "no longer extended" as a buy signal
**Change.** When the extension gate blocks a name, that name must not become buyable merely
because its 20-bar hi/lo range decayed. Either the block expires on a *price* condition measured
against a fixed anchor (e.g. the watchlist `first_seen_price`, which already exists at
`:21321-21333`), or the name goes on an explicit cooldown and the block is logged as consumed.

**Expected effect (measured, not modelled).** In bt 201039 the two names that were blocked and
then bought are PLRZ and HL: **-$178.62 combined**, both bought within 6 and 1 sessions of the
last block, both above the block price. In bt 613166, PLRZ: **-$18.47**, bought 2 sessions after
its last block at +115.3%. In bt 820236 the same pattern appears on SNDK (blocked 01-01/01-02/
01-07/01-08 at $237–$300; bought 01-20 at $443.83) — but there it **made +$100.95**.

**Generalisability caveat — read this before implementing.** A *cooldown length* that blocks
PLRZ (last block 01-08, bought 01-16 = 6 sessions) but spares SNDK-820236 (last block 01-08,
bought 01-20 = 8 sessions) must be exactly 7 sessions. That is fitted to three runs and I do not
recommend it. What generalises is the weaker, mechanically-justified version: **the release must
be monotone in price.** A name refused at $8.11 must not become buyable at $15.48; a name
refused at $237 may become buyable at $443 only if the anchor moved for a reason other than the
last 20 bars flattening. This changes PLRZ (1.91x and 1.81x the block price) and HL
(bought above the block price) in both runs, and leaves SNDK-820236's 01-20 entry decided by the
anchor, not by a bar count. **Recommend the anchor change; do NOT recommend a cooldown constant.**

### 5. Split the momentum-lane conviction floor from the sizing decision
**Change.** `max(score, 1.50)` at `:21542` makes every momentum pick "high conviction" and
therefore full-size, core-funding and turnover-exempt. Keep the floor for *admission* if it is
needed, but size on `raw_net_natural` (already carried through at `:28654`), not on the floored
value.

**Expected effect.** In bt 201039 this separates HL (0.876) and TCMD (0.567) from SNDK (1.505)
and PLRZ (1.130), all of which currently receive an identical ~14%-of-NAV clip and identical
gate exemptions. **The dollar effect cannot be read off these logs** — it depends on the sizing
curve chosen — so this is a *precondition* for any sizing work, not a P&L claim.

**Why it generalises.** It restores information that is currently destroyed before every
downstream decision, in every window. **Recommend as an enabler; do not promote on P&L.**

### 6. DO NOT TOUCH the exit / hold stack
Net **+$262.43** across the three runs; 34 of 43 sell-gate decisions in 201039 were *blocks*
that preserved +$629 of winners. The two circuit-breaker exits that cost money (PLRZ -$20.93,
HL -$8.72) are downstream of entries that had, respectively, +3.4% and +0.3% of lifetime upside.
Loosening the breaker to "hold PLRZ to the close" recovers $20.93 and re-exposes the book to
CORD's -$361.70 in 820236. **Confirmed; leave it alone.**

---

## 12. Tested and REJECTED — do not try these

- **Tightening `entry_extension_block_pct` / lengthening `entry_extension_lookback_bars`.**
  The extension axis does not separate the losers from the winners. Measured against the same
  anchor for both (the window's opening price): SNDK's winning 820236 tranche was bought at
  **1.87x** ($443.83 / $237.33) and made +$54.03; PLRZ was bought at **1.91x** ($15.475 / $8.11)
  in 201039 and lost $154.46, and at **1.81x** in 613166 and lost $18.47. Any threshold that
  catches PLRZ catches SNDK. Also on the
  DO-NOT-RETRY list in `docs/OBJECTIVE.txt` (loosening returned -7.95%).
- **Lowering `momentum_max_runup_multiple` (currently 3.0, `:21333`).** Same reason: 1.75 would
  block PLRZ in both runs (1.91x, 1.81x) **and** SNDK's winning 01-20 tranche in 820236
  (1.87x, +$54.03).
  Additionally the anchor is `first_seen_price`, which for a late-discovered name is itself a
  late price — SNDK's anchor in 201039 was ~$617, so 660/617 = 1.07x and no multiple would have
  bound. The lever is only as good as discovery.
- **A "chase cap" that rejects a fill more than K% from the decision price.** Tempting after
  SNDK's +6.98% (§3). Measured against the other two runs, a 5% cap would have rejected
  820236's **LRCX (+6.50%, +$238.22)** and **WDC (+5.39%, +$450.49)** — 93% of that run's P&L —
  to save $37.73 on SNDK. **Catastrophic. Do not implement.** (Passive/limit execution via
  `SimulationOrder.limit_price`, `backend/simulated_execution.py:135-150`, is a different and
  still-open question; a hard reject is not.)
- **Blaming the -10%/-15% circuit breaker for PLRZ/HL.** See §9: net +$262.43 across three runs.
- **Reading anything into "big movers vs modest movers".** The correlation between window stock
  move and our result across 27 (run, name) pairs is **+0.17**; against entry extension for the
  14 big movers it is **-0.52**. Size of move is not the variable. Position in move is.

---

## 13. Open items handed to other threads

- **Discovery (§3).** bt 201039's bar-1 momentum pool was 43 names vs 820236's 87, with
  `sector_peer=0, competitor=0` and `unknown=64`. It never saw SNDK, WDC, VICR, MU, TSEM or
  LRCX. No entry lever can fix a name the run does not know exists until day 20.
- **Capital / cash race (§2, §5).** Reproduced in 201039 twice: BA+LMT $1,634 approved → $34.77
  filled; HL $838.79 approved → $130.36 filled with `cash=$132.00` while $786.32 of sell
  proceeds were in flight on the same tick.
- **max_positions plumbing (§5).** `Regime capacity gate (Z4.1)` lifted the cap 43 times;
  `max_positions gate armed` read `cap=6` on all 634 bars.
