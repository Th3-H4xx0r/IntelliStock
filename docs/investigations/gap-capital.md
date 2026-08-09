# gap-capital — why 96% of NAV is committed by day 2 and every later signal is a runt

Read-only, 2026-08-09. No code changed, no run started/stopped, nothing pushed.
Runs: **915207** (bull/chop 2026-01-01..03-01, +9.70%) and **383778** (OOS bull 2026-03-30..04-27, +4.75%).
Logs: `backtests/915207.log` (41,184 lines), `backtests/383778.log` (19,643 lines), pulled with
`scripts/pull_backtest_logs.py`. Every count below is grepped off those two files.
Builds on `capital-and-cash.md` (cash race, 725146/820236/613166) — that mechanism is now second-order;
this is a different, larger binder.

---

## 0. The one-paragraph answer

**The binding constraint is the satellite overflow ceiling, and it is binding because of an
arithmetic mismatch that is present on every bar of every run: the conviction overflow band is
`(core_target_pct − core_min_pct) = 0.35 − 0.25 = 0.10` of NAV ($618–$635 measured), while one
sized clip is `total_spend_cap_target_weight_pct = 0.14` of NAV ($839–$921 measured). The band is
structurally 30% too small to ever fund a single conviction clip.** `max_positions` blocks
**zero** buys in either run (`MAX_POSITIONS_GATE: blocked` = 0 lines in both logs). Cash is the
downstream symptom, not the cause. Measured conviction headroom on the bars where the top-ranked
name was refused: **$12, $21, $19, −$1, −$28** (915207) and **$2, −$4, −$21, −$41** (383778).
Consequence: 915207 sized **SNDK five times at $865–$904 and filled $29.11 (3.2%)** while SNDK did
**+166.1%** in the window.

---

## 1. Which of the three binds — counts, both runs

Order of evaluation: allocator sizes → `[core] funding request trimmed` (satellite headroom) →
`SATELLITE CAP` per symbol (satellite headroom) → `Buy gate inputs` (cash) → `MAX_POSITIONS_GATE`
→ emulator clamp → fill. Classified over **116 sized symbol-events** (52 in 915207, 64 in 383778):

| first binder | 915207 | 383778 | total |
|---|---|---|---|
| **satellite overflow ceiling** — `SATELLITE CAP: X skipped` | **28** | **20** | **48** |
| **satellite headroom** — `SATELLITE CAP: X trimmed` to <50% of sized | **6** | **10** | **16** |
| cash — `SKIP BUY … cash_to_use < min` | 4 | 6 | 10 |
| cash — gate PASSed then emulator clamped fill to <50% | 0 | 1 | 1 |
| **`MAX_POSITIONS_GATE: blocked`** | **0** | **0** | **0** |
| funded at ≥50% of sized | 9 | 24 | 33 |

**Satellite headroom is first binder on 64 of 116 events (55%); cash on 11 (9%); max_positions on 0.**
`max_positions` is not merely unbound, it is *slack*: `max_positions: honouring the regime cap 6 -> 8`
fires and the alpha book never exceeds it in a way that refuses a sized buy. The only
max_positions lines that are not "gate armed" are 21 `current=9 > max=8 (auto-heal freed 0)`
notices in 915207 and 3 `funding pre-pass: max_positions will refuse …` — none is a refusal of a
sized conviction buy at the gate.

Upstream, in dollars, the same verdict (`[core] funding request trimmed $X -> $Y —
satellite headroom will refuse the remainder`, 16 lines in each run, **reason field is
"satellite headroom" on 32 of 32**):

| run | requested | allowed | trimmed away | Y=0 | Y<$50 |
|---|---|---|---|---|---|
| 915207 | $37,728 | $4,067 | **$33,661 (89.2%)** | 6 | 10 |
| 383778 | $50,523 | $8,695 | **$41,828 (82.8%)** | 3 | 6 |

---

## 2. Why the ceiling is where it is — the identity, and the arithmetic mismatch

`broker.py:3395` defines the satellite as a NAV residual:

```
satellite = max(0.0, nav - cash - core_value - hedge_value)
```

and `core_sleeve.py:305-309` sets the hard ceiling:

```
core_min = _f(cfg, "core_min_pct", 0.30)
ceiling  = max(0.05, min(0.95, 1.0 - core_min - cash_fl))     # = 0.73 on this config
```

With cash ≈ $0 (measured: $1.69, $34, $38 in 915207; $0.08, $44 in 383778) the ceiling
`satellite ≤ 0.73·NAV` **is identically** `core ≥ 0.25·NAV`. Confirmed off the log, not the config:
on the 9 bars where both rooms print, `conv_room − design_room` = $618, $618, $620, $626, $635
(915207) and $601, $603, $622, $622 (383778) — i.e. exactly **0.10·NAV**, so
`core_target_pct=0.35`, `core_min_pct=0.25`, `cash_reserve_floor_pct=0.02`,
`design_share=0.63`, `max_share=0.73`.

**The mismatch:** the band the conviction path may spend is `0.10·NAV ≈ $620`. The clip the
allocator sizes is `total_spend_cap_target_weight_pct = 0.14·NAV`, measured at $839–$904 (915207)
and $795–$921 (383778). `0.10 < 0.14`, always, on every bar, in both windows. The conviction
overflow band cannot fund one clip even when it is completely empty.

Result at the ceiling (915207, log lines 7936–7947, bar 2026-01-09 15:00):

```
[core] funding pre-pass: max_positions will refuse 2 of 4 sized buy(s) (RVLV, UBER)
[core] funding request trimmed $3,459 -> $12 — satellite headroom will refuse the remainder
SATELLITE CAP: BTC  skipped — satellite at its overflow ceiling ($12 room); core would be squeezed below its floor
SATELLITE CAP: RVLV skipped — satellite at its design share ($-606 room); core would be squeezed below target
SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($12 room); core would be squeezed below its floor
SATELLITE CAP: UBER skipped — satellite at its design share ($-606 room); core would be squeezed below target
```

`max_positions gate armed: held=7, cap=8` on that same bar — **a free slot, and no money to use it.**

---

## 3. NAV committed by bar, cash by bar (the opening days are the whole story)

**915207** — fills from `[execution] FILL …`, cash from `Buy budget: … (cash=$X`:

| bar | fills | cum. committed | cash after |
|---|---|---|---|
| 01-02 15:00 | NTR $839.97, TCMD $814.89, VOYA $839.97, XOM $837.46 | $3,332 = **55.5%** | $2,668 |
| 01-02 16:00 | **SPY $2,398.48**, BA $267.59 | $5,998 = **100.0%** | ~$2 |
| 01-05 15:00 | — (CFG, USPH sized $851 each) | 100% | **$1.69** |
| 01-05 16:00 | SELL SPY $761.52 (released to fund CFG+USPH) | | |
| 01-06 16:00 | **BUY SPY $636.57**, AAL $88.57 | | $38 |
| 01-07 16:00 | SELL SPY $648.41 (released to fund GBDC+LLY) | | |
| 01-08 16:00 | AMAT $652.23 | | $34 |
| 01-09 15:00 | conviction room **$12** | ~100% | $34 |

**383778**:

| bar | fills | cum. committed | cash after |
|---|---|---|---|
| 03-30 15:00 | ETH $899.97, SQQQ $2,081.10 | $2,981 = **49.7%** | $3,019 |
| 04-06 14:00 | MSFT $859.68, NVDA $802.41, RIVN $803.52 | | $2,377 |
| 04-06 15:00 | **SPY $2,295.76**, HLMN $81.52 | **≈96.7%** | $0.08 |
| 04-07 15:00 | SELL SPY $753.36 | | |
| 04-08 15:00 | AAOI $709.80 | | $44 |
| 04-10 14:00 | conviction room **$2** | ~100% | $44 |

**The book is fully committed on trading day 1 (915207) / day 5 (383778), and the ceiling is
reached 2–3 bars later.** 915207 then executed **13 buys in 42 trading days**; 383778, 18 in 20.

---

## 4. Every buy sized ≥10% of NAV that filled <5% or was skipped

Sizing threshold: 10% of NAV ≈ $600; every sized clip in both runs is $795–$921, so **all 95 of
them qualify**. Grouped by symbol (max sized $, times sized, total filled $, window return):

**915207 — 41 symbols sized at 14% of NAV; 6 filled ≥76%; 4 filled as runts; 31 filled $0.**

| symbol | sized $ | times | filled $ | fill % | window return |
|---|---|---|---|---|---|
| **SNDK** | **$904** | **5** | **$29.11** | **3.2%** | **+166.1%** |
| NVDA | $904 | 2 | $28.96 | 3.2% | −4.8% |
| RVLV | $917 | 2 | $50.81 | 5.5% | −16.6% |
| AAL | $858 | 1 | $88.57 | 10.3% | −14.6% |
| INTC / UBER | $888 / $866 | 2 / 2 | $0 | 0% | never bought |
| TNDM, LMT, TSEM, CFG, USPH, AMCR, ETH, GBDC, LLY, ARWR, BTC, GH, SLGN, META, SOUN, RGEN, GLUE, RVMD, BRKR, WDC, C, PLRZ, UHS, CAT, CVX, ENS, GFI, BMY | $839–$904 | 1 each | **$0** | 0% | never bought |
| *(funded)* XOM, VOYA, NTR, TCMD, AMAT, BA | $838–$858 | | $268–$840 | 32–100% | |

**383778 — 54 symbols sized; 8 filled ≥87%; 5 runts; 41 filled $0.**

| symbol | sized $ | times | filled $ | fill % | window return |
|---|---|---|---|---|---|
| **LWLG** | **$921** | **6** | **$0** | **0%** | never bought |
| **XOM** | **$907** | **4** | **$0** | **0%** | never bought |
| LIN | $884 | 1 | $6.77 | 0.8% | +4.0% |
| AAPL | $900 | 1 | $88.06 | 9.8% | +7.6% |
| HLMN | $808 | 1 | $81.52 | 10.1% | +7.5% |
| HOOD | $905 | 1 | $107.25 | 11.9% | +27.0% |
| ABT | $888 | 1 | $112.56 | 12.7% | −10.7% |
| TLX, NFLX, VLO | $871–$907 | 2 each | $0 | 0% | never bought |
| XOP, LYV, NCNO, NTRS, NET, PSX, CNQ, OXY, FTH, INTC, TERN, CRS, HON, TSEM, BKR, BLK, CAN, AXP, DVLT, ILMN, AMRX, SBLK, MP, ADBE, WEX, WMG, GOOGL, VOD, OKLO, RELY, MDT, APO, OGN, DOW | $795–$921 | 1 each | **$0** | 0% | never bought |

**Totals: 95 symbol-sizings at 14% of NAV → 14 fills at ≥76%, 9 runt fills (0.8–12.7%), 72 zero fills.**
The 9 runts together took **$595 of capital and returned −$13 of P&L** — they consume a position
slot and turnover budget and return nothing.

---

## 5. The second-order leak that makes any fix evaporate: the core re-buys what it just released

`[core] released` → next bar `[core] bought … band_deploy`. Measured on SPY fills only:

| run | SPY released (post-opening) | SPY re-bought (post-opening) | recaptured |
|---|---|---|---|
| 915207 | $1,410.  (SELL SPY $761.52 + $648.41) | $1,316.36 (BUY SPY $636.57 + $679.79) | **93.4%** |
| 383778 | $2,437 (5 sells) | $1,715.06 (BUY SPY $890.37 + $824.69) | **70.4%** |

And on 4 bars across the 2 runs the core's re-buy filled **in the same tick** as the starved
alpha name, taking the cash the alpha name had been sized against:

| run | bar (fill) | cash at gate | core took | alpha name got | of sized |
|---|---|---|---|---|---|
| 915207 | 01-06 16:00 | $763.24 | SPY $636.57 | AAL $88.57 | $858 |
| 915207 | 02-26 16:00 | $821.60 | SPY $679.79 | RVLV $50.81 | $866 |
| 383778 | 04-16 15:00 | $1,053.66 | SPY $890.37 | ABT $112.56 | $888 |
| 383778 | 04-22 15:00 | $958.15 | SPY $824.69 | AAPL $88.06 | $900 |
| **total** | | **$3,596.65** | **$3,031.42 (84.3%)** | **$340.00 (9.5%)** | **$3,512** |

The canonical case, 915207: on 01-05 the core sold **$761.52** of SPY explicitly to fund CFG+USPH;
the buy gate then read `cash=$1.69` and printed
`SKIP BUY CFG — cash_to_use $1.69 < min $50 (allocated $759.42)` and the same for USPH; on 01-06
the core bought **$636.57** of SPY straight back. Net alpha exposure moved by that whole
release/refuse/redeploy cycle: **$88.57**. Identical shape on 01-07 (SELL SPY $648.41 → GBDC and
LLY both `SKIP BUY … cash_to_use $38.15`).

---

## 6. THE ONE CHANGE

### Make the conviction overflow band bigger than one clip, and stop `band_deploy` recapturing it.

**Change (both halves are one coherent change to the funding path; either alone is measurably inert):**
1. `core_min_pct: 0.25 → 0.10` (`core_sleeve.py:305-309`). Band goes from `0.10·NAV = $620` to
   `0.25·NAV ≈ $1,550`, i.e. from 72% of a clip to 1.8 clips. (Equivalently: any setting with
   `core_target_pct − core_min_pct > total_spend_cap_target_weight_pct`. Today 0.10 < 0.14.)
2. Suppress `_residual_sleeve_deploy`'s `band_deploy` on any tick where a satellite buy was sized
   and filled at <50% of its sizing in the last 2 bars — otherwise the measured 93.4% / 70.4%
   recapture puts the money straight back into SPY.

**Expected effect.** On 915207 this is priceable off the run's own fills: SNDK was sized at
$865–$904 on five bars (01-09, 01-12, 01-14, 01-19, 01-28) against conviction room of $12, $21,
−$1, −$28, $29, and filled $29.11. That $29.11 returned **+$6.90 = +23.70%** to run end. At the
sized $865 the same position returns **+$205**, i.e. **+3.4pp**: the run goes **+9.70% → ~+13.1%**
(+6.5%/mo, past the 1x target). That is one name; 31 other names were sized at 14% and filled $0.

**Evidence, ≥2 windows, mechanism-level:**
- The `0.10 < 0.14` mismatch is arithmetic and window-independent; it is confirmed numerically off
  both logs (band = $618/$618/$620/$626/$635 vs clip $839–$904 in 915207; band = $601/$603/$622/$622
  vs clip $795–$921 in 383778).
- 48 `SATELLITE CAP … skipped` events, **none with ≥$50 of room**, split 28/20 across the two runs.
- `[core] funding request trimmed` discards 89.2% / 82.8% of the request, reason "satellite
  headroom", 32/32 lines.
- Recapture 93.4% / 70.4%; same-tick core-vs-alpha 84.3% / 9.5% on 4 bars across both runs.
- `MAX_POSITIONS_GATE: blocked` = 0/0 — the cap is **not** the binder; do not touch it
  (`OBJECTIVE.txt` DO-NOT-RETRY already says raising it dilutes the prize).

**Stated plainly — where this is one-window.** The **+3.4pp is 915207 only.** On 383778 it is
neutral-to-slightly-negative: SPY returned **+12.79%** in that window and the marginal refused
names did not (ABT −10.7%, ACM −4.0%, BIIB −1.8%, AXTI −$74 realised), so moving capital out of
the core there would have cost money. What generalises across both windows is the **mechanism and
the counts** (band < clip; 48 zero-room skips; 0 max_positions blocks; ~90%/~70% recapture), not
the +3.4pp. This must be validated as a paired A/B on ≥3 windows with its own
`history_scope_salt`, per `OBJECTIVE.txt:88-96`, before anyone believes the number.

### Explicitly NOT the fix (measured here, so nobody re-tries them)
- **`max_positions`**: 0 blocked buys in 41,184 + 19,643 log lines. Slack, not binding.
- **Cash / the 725146 cash race**: 10 `SKIP BUY … insufficient_cash` events total, 9% of refusals.
  Real, already documented in `capital-and-cash.md`, and an order of magnitude smaller than the
  ceiling. `backtest_credit_pending_sell_proceeds=True` is already on and there are **0**
  `Sell-proceeds credit` lines in either run.
- **Filling the runts**: the 9 runts cost $595 and returned −$13. Sizing them up on their own
  (AAL −14.6%, RVLV −16.6%, ABT −10.7%) makes both runs *worse*. The prize is in the **72 names
  sized at 14% that filled $0**, not in the ones that filled small.
