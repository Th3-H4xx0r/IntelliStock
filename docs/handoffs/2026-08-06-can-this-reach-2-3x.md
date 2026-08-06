# Can this account reach 1–2–3x/year? — the evidence

Written overnight 2026-08-05/06 under an autonomous instruction. Every number is
computed from primary data or from this repo's own database. **Short answer: no,
and the specific reasons are below. But the question produced the single most
useful finding of the whole effort.**

---

## 1. The SNDK question — the premise was RIGHT

| | entry | exit | return |
|---|---|---|---|
| SNDK | $116.69 (2025-10-07) | $2,272.05 (2026-06-30) | **+1,847%** |
| MU | $101.92 (2025-01-07) | $916.27 (2026-07-31) | **+799%** |

$2k in each would have become **~$56,900 — 14x**. The premise was not just right,
it was understated.

## 2. But they could not be identified as individual stocks

536 symbols with full coverage. **16 (3.0%) returned >200%.**

| | moonshots (n=16) | everything else (n=520) |
|---|---|---|
| return BEFORE the run | **−3.9%** | +1.6% |
| realised vol before | 32% | 21% |
| forward return | **+279%** | +8.9% |

**The moonshots were slightly WORSE performers beforehand.** A "buy what is going
up" filter yields **2.1% precision** against a 3.0% base rate — worse than random.

## 3. The signal was at the SECTOR level

11 of the top 16 were semiconductors/semi-equipment/components: MXL, ICHR, AAOI,
MU, VICR, VSH, SIMO, INTC, MRVL, ATOM, RFIL — plus SNDK.

And the sector vehicle captured it **without any stock selection**:

| vehicle, 2025-12-01 → 2026-06-30 | return | annualised |
|---|---|---|
| **SOXX** | **+114.4%** | **+274%** |
| SMH | +85.0% | +190% |
| clean 13-name semi basket (no names taken from the winners list) | median +82.4%, 1 of 13 negative | ~+180% |
| QQQ | +18.9% | +35% |
| SPY | +9.4% | +16.8% |

**You never needed SNDK. You needed to own semiconductors.** A plain ETF, no
leverage, no options, fully reachable from a $6k account, returned 2.7x
annualised.

## 4. And that is where it dies — sector momentum does NOT generalise

Ranking sector ETFs by trailing return and holding the top N, walk-forward over
**260 months (2005–2026)**, 18 sector ETFs, 10bp costs:

| strategy | CAGR | Sharpe | maxDD | vs SPY |
|---|---|---|---|---|
| **SPY buy & hold** | **11.3%** | **0.80** | −50.8% | — |
| top1, 3m | 0.9% | 0.17 | −62.9% | −10.4% |
| top1, 6m | 7.2% | 0.38 | **−67.6%** | −4.1% |
| top2, 6m | 12.4% | 0.63 | −53.9% | +1.1% |
| top3, 12m | 10.9% | 0.62 | −54.7% | −0.4% |

**Every configuration has a worse Sharpe than buy-and-hold.** By sub-period
(top1/6m): 2006-10 **−9.8%**, 2011-15 −1.2%, 2016-20 **−5.2%**, 2021-26 −0.1%.
**It loses to SPY in all four.**

The in-sample version looked spectacular — the #1-ranked sector beat SPY in 4 of 4
recent windows, averaging +55.0% vs +8.7%. But those windows all ended on the same
date: one regime, overlapping samples. The 20-year test is what it is worth.

## 5. Three independent routes, all closed

| route | verdict | evidence |
|---|---|---|
| pick the individual moonshots | **no** | 2.1% precision; winners had negative prior returns |
| own the winning sector | **no as a rule** | loses to SPY in 4/4 sub-periods over 20 years |
| lever a modest edge into 2–3x | **mathematically impossible** | median growth caps at e^(S²/2); Sharpe 1.0 → 1.65x max at ANY leverage |

Supporting, from the four-agent sweep:
- **0 of 106** Ken French anomaly portfolios have a median annual return above 25%.
- Highest Sharpe in AQR's entire published library: **0.98**. 2x/yr needs **1.18**; 3x/yr needs **1.48**.
- P(reaching 2x before losing half): **95.3% at 1x leverage, 42.0% at 10x.** Leverage *reduces* the odds of doubling; it only makes the answer arrive faster.
- Taiwan, complete population, 15 years: **"less than 1% of the day trader population is able to predictably and reliably earn positive abnormal returns net of fees."**
- Bessembinder: **57.4%** of stocks underperform T-bills over their lifetime; median lifetime return **−2.29%**.

## 6. What IS real, and shipped

| item | value |
|---|---|
| turnover 66.5 → 16.4×/yr, exec cost 1.06% → 0.26% of book | **~+$885/yr** — ranked the #1 available lever by three independent research streams |
| `cash_to_use` crash (would have killed the first live buy with the core armed) | fixed, verified in production |
| bear leg reaching 95% of NAV in a −3x ETF | fixed, verified at 69.5% vs a 70% cap |
| live path had NO in-flight-order guard | fixed, tested |
| deploy pipeline stranding containers | fixed |
| EDGAR point-in-time research pipeline + veto | built, tested, default-off |

## 7. Unfixed, and they matter

1. **Corrupt bars** — unadjusted corporate actions injecting fake 30× overnight
   gains (AGL 0.33→9.755; NRGD frozen three weeks then 4.4×; VGT showing −84.7%).
   Historical 100%+ results are contaminated by these.
2. **Run-to-run non-determinism** — 20 reruns of one config on one window produced
   **20 different universes and a 40pp spread**. A/Bs are meaningless until fixed.
3. **Binance.US USD pairs have a 586-day hole** (2023-07-14 → 2025-02-19). Use
   USDT pairs. The +132% crypto headline sits entirely inside that hole.
4. **The deploy pipeline has silently swallowed pushes twice.** Verify before
   trusting any experiment.
5. **Passive limit orders** — built, tested, unshipped. Ranked the #2 lever
   (+$100–300/yr) and the only place account size is a genuine advantage.

## 8. The honest recommendation

Do not fund an active strategy. The current live config loses to SPY by 1.20pp on
the same window, so the status quo is not a safe default either.

What the evidence supports:
- **hold the index**, execute cheaply, and keep turnover near zero
- **finish the two execution fixes** — they are worth more than any signal here
- at $6,000, **contributions dominate**: $500/month is a 100%/yr increase with
  zero variance and zero ruin risk. No strategy on this menu beats that.

The instinct about semiconductors was correct for 2025–26. It just is not a rule
that survives 20 years of testing, and the difference between those two things is
the entire discipline.
