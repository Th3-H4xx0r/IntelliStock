# Forward test 2026-11: frozen protocol for the hunt finalists

Written 2026-11-07, before any forward session had closed. The 104-spec strategy hunt produced no WINNER and no
CANDIDATE. Its best shape, F14_F, failed only on drawdowns before 1933 and abroad, and on the multiple-testing
haircut (DSR 0.72). This test measures that shape, and two simpler comparators, on data nobody has seen yet.
**Nothing in this directory may be tuned after the freeze.** The SHA-256 block at the end pins every file.

## 1. What is tested

| role | name | rule (live form) | instruments | trades |
|---|---|---|---|---|
| **primary** | **F14_F_live** | Hunt `F14_F_tsfix_sign12_Ttc23` with the TSMOM stream = the real DBMF ETF, exactly as REPORT_F14's "Live-implementable version". Streams L (trend leverage + stress brake), B (bond trend), D (dip re-leverage), M (momentum tilt), V (value tilt) are each on iff their own trailing-12-month shadow excess > 0. T (turn-of-month +0.5) is on iff its trailing-12m shadow excess, re-charged at 23 bps, > 0. S = 0.417 x DBMF is always on. Gross is capped at 2.0 with priority equity core, then DBMF, then T, then bond. | SPY, IEF, MTUM, IWD, DBMF, cash or margin | only on TOM day -3 and day +2: at most 2 trade dates a month |
| comparator | F13_D_live | Hunt `F13_D_f4core_exeq_s04` with its synthetic ex-equity TSMOM overlay replaced by 0.4167 x DBMF on margin, decided at month-end. Core = the F4r2_stress_xs equity exposure 1 + 0.5u - 0.3(1-u), or 0.7 when 10-day vol > 30%, decided weekly. | SPY, DBMF, cash or margin | weekly (core), monthly (DBMF) |
| comparator | F4r2_stress_xs | Hunt F4 round-2b rule, unchanged: the same equity core plus 0.4 x IEF when IEF's 12m TR beats bills (8-tranche average). | SPY, IEF, cash or margin | weekly (W-WED) |

Rule code is copied verbatim from the hunt (`f14_engine.py` from f14/engine.py a514d70a22; `f13_engine.py` from
f13/engine.py 41f9296f51; `f4_rule.py` from f4/f4core.py c149a7164b and f4/f4core2.py eec5ea1fb9; `harness.py` from
lib.py f291498b03 and f14/bt.py 8d02807bb7). Every edit is listed in section 7.

## 2. Fixed test parameters

- **Forward start:** the first NYSE session on or after **2026-11-09**, which is Monday 2026-11-09. NAV = 1.0 at
  the base close, the last session before the start: 2026-11-06.
- **Benchmark:** SPY total return (yfinance Adj Close), rebased at the same base close.
- **Timing:** signal from close t, fill at close t+1 (lag 1). The book entering the test is the one each rule was
  already holding. Each strategy is simulated continuously from a fixed inception: 2019-05-10 for F14_F_live and
  F13_D_live, 2013-01-01 for F4r2_stress_xs. The forward window is that NAV rebased at the base close. History
  before 2026-11-09 is warm-up and is never scored. The truncation check (repro_check.py section 5) shows that a run
  on data cut at any date t reproduces the full run's NAV and decisions through t. So each monthly re-run cannot
  rewrite the past.
- **Costs:** **5 bps** per unit traded on every fill, buys and sells (primary, hunt R10). A **23 bps** stress row is
  also reported. Margin is financed at T-bill + 0.5%/yr. DBMF's 0.85% fee is inside its NAV. F13_D_live also pays
  F13's spread drag of 0.5% x 0.4167 per year.
- **Leverage:** an equity weight above 1 is scored as SPY on margin, as in the hunt. `score_forward.py` also prints
  an optional SSO form (SPY 2-E + SSO E-1). That form is for execution only and is not scored. A book run with SSO
  will drift slightly from the score because of SSO's daily reset and 0.89% expense ratio.
- **Data:** free sources only. yfinance (auto_adjust=False) Adj Close for SPY, IEF, MTUM, IWD and DBMF. Cash uses
  ^IRX Close, with gaps filled from FRED DTB3 via curl and floored at 0. Session t earns the rate published at
  t-1, divided by 252 (the hunt's CASH_US). Each review re-fetches the full history, so dividend re-adjustments are
  absorbed as they come. Scoring stops at the last session on which every ETF has a real close.

## 3. Scorecard (`python3 score_forward.py`)

For each strategy at 5 bps and 23 bps, over the forward window only:

- sessions;
- cumulative return vs SPY's, and cumulative excess (pp);
- annualised excess (CAGR difference, pp/yr);
- max drawdown vs SPY's;
- share of daily-stepped rolling 3-, 6-, 12- and 36-month windows won, by more than 1e-6 (hunt R8), with the
  window count;
- trailing-12-month excess (pp);
- next-session action: TRADE to target weights at the next close, or HOLD with the drifted book.

## 4. Review cadence

**Monthly.** Review on the first weekend after each month's last NYSE session:

```
python3 score_forward.py --save reviews/YYYY-MM.csv > reviews/YYYY-MM.md
```

Commit each review in its own commit. Never edit a hashed file. The first review is due the weekend of
2026-12-05 (window 2026-11-09 to 2026-11-30). The decision reviews are month 12, the first review on or after
2027-11-09; month 24, on or after 2028-11-09; and month 36, on or after 2029-11-09.

## 5. Decision rules (written now; they apply to F14_F_live and, for context, to each comparator)

"Excess" means the 5 bps annualised excess vs SPY total return over the forward window, unless stated otherwise.

1. **Months 1-11: no performance decisions.** At about 10% tracking error (section 6), a few months of excess is
   noise. Only the integrity stops in rule 6 apply.
2. **Kill, at any monthly review from month 12 on:** trailing-12-month excess < **-5 pp** gives KILL.
3. **Month 12:**
   - CONTINUE if excess > 0 AND forward max drawdown is no deeper than SPY's over the same window.
   - Otherwise, unless rule 2 fired, WATCH: continue to month 24.
4. **Month 24:** KILL unless excess > 0 AND max drawdown is no deeper than SPY's.
5. **Month 36, final verdict.** PASS only if all of these hold:
   - (a) excess >= **+1.0 pp/yr** at 5 bps and > 0 at 23 bps (PROTOCOL C1);
   - (b) max drawdown no deeper than SPY's (C3);
   - (c) >= **80%** of daily-stepped rolling **12-month** windows won. This is the C2 reliability bar, applied at
     the longest horizon that 36 months can sample. The single 36-month window is reported but not judged.

   Otherwise FAIL. PASS earns a real-money discussion with the operator; it never triggers trading by itself.
   **Simplicity tie-break:** if F14_F_live passes but its excess trails a comparator that also passes by more than
   1 pp/yr, prefer the comparator. The extra machinery (stream gating, DBMF, TOM, tilts) has then not paid for
   itself.
6. **Integrity stops** suspend a strategy at its last good close; they do not kill it on performance:
   - an instrument it trades is delisted, merged or changes mandate (DBMF in particular);
   - a free data source fails permanently.

   Nothing may be substituted inside this test. A substitute is a new, separately pre-registered test.
7. **No tuning.** No parameter, instrument, cost, schedule or rule changes. If a bug makes the code differ from
   the documented hunt rule:
   - the fix must keep `repro_check.py` at PASS;
   - it is recorded in a dated addendum below the hash block;
   - both the pre-fix and post-fix scorecards are reported.

## 6. What to expect (2019-05-10 to 2026-11-06 replay, 5 bps; from `repro_check.log`)

| book | excess pp/yr (23 bps) | maxDD / SPY | win 3m/6m/12m % | tracking error (daily, ann.) | beta |
|---|---|---|---|---|---|
| F14_F_live | +6.57 (+4.95) | -30.0 / -33.7 | 63/69/72 | 11.6% | 1.10 |
| F13_D_live | +6.23 (+5.81) | -34.0 / -33.7 | 66/71/75 | 9.9% | 1.12 |
| F4r2_stress_xs | +3.60 (+3.19) | -29.8 / -33.7 | 68/67/71 | 7.4% | 1.05 |

- With about 10% tracking error, one year of excess has a standard error of about 10 pp.
- If F14_F_live's true edge is the replay's +6.6 pp/yr, rule 2 still kills it at month 12 about 1 time in 6 to 8.
  This is accepted in advance.
- The replay won 72% of 12-month windows, below rule 5c's 80% bar. Passing at month 36 needs a better path than
  2019-26 gave.
- F13_D_live drew down slightly deeper than SPY in the replay (2020-02-19 to 2020-03-23). Its core was 1.5x into
  the first leg of the crash and was cut to 0.7x only from the 2020-03-04 decision; DBMF fell 6.6% over the same
  span.

## 7. Adaptations from the hunt code (each one, with the reason)

- **A1. Calendar edge.** The hunt's `lib.decision_days` and F14's TOM calendar flag the last data row as a week-
  and month-end ALWAYS. F14 also extends the final month with plain business days (no holidays).
  - This was harmless in the hunt, whose backtests end there.
  - It is wrong when the last row is "today" and we print tomorrow's trade.
  - Fix: period-end flags and TOM positions are computed on the data's sessions extended with expected NYSE sessions
    (regular holidays, `harness.nyse_holidays`; matches SPY's 2010-26 sessions except the 4 unscheduled closures).
  - Effect: identical flags on every row except the last data row. No historical number changes. The truncation
    check passes, and without A1 a truncated F4 run records a spurious decision on its last row.
- **A2. Reporting only.** `backtest` and `f13_engine.run` also return the end-of-run book and any decision made at
  the last row, so the next session's target can be printed.
- **A3. Plumbing.**
  - `import lib` now points to `harness.py`.
  - F14's MOM/VALS slots are named for the ETFs the hunt held in VAL: MTUM (VAL used MTUM from its 2013-04-18
    launch) and IWD (VAL's value slot throughout). The French stand-in before 2013-04 is not needed after warm-up.
  - F13's `frames` import is replaced by `MONTHLY_SLEEVES = set()`.
  - Frames start 2003-01-02, not 1954. Every stream's state converges within about 2 years, and the reproductions
    below are exact.
- **A4. F13_D_live (the one substantive adaptation).**
  - F13_D's synthetic 11-sleeve overlay at 4% nominal vol is replaced by 0.4167 x DBMF, held from each month-end
    decision.
  - 0.4167 = 1.25 x 0.04 / 0.12 is F13's own ETF-equivalent of that overlay (`E_etf`, on which F13 charged its
    drag), and the same 0.417 F14 used.
  - DBMF enters as a futures-like excess-return series (prod(1 + r_DBMF - r_cash)), so its P&L is that of DBMF
    financed at cash.
  - `FEE = 0` because DBMF's NAV is already net of its 0.85% fee. F13's 0.5% x E spread drag is kept verbatim.
    It is conservative, because it is charged even when an idle 0.3 cash from a 0.7x core could fund part of DBMF.
  - The hunt never ran this combination, so it has no logged number to reproduce. The engine copy itself is
    verified on F13_D's synthetic VAL row (check 3).
- **A5. F14_F_live.** The fund column is the real DBMF, held as an asset financed at cash + 0.5%. This is verbatim
  REPORT_F14 / f14/dbmf_check.py.
- **A6. Data vintage.**
  - The hunt's IEF series lacked the 2026-10-01 dividend adjustment (+0.34% that day). This snapshot has it.
  - All other daily returns (SPY, MTUM, IWD, DBMF, cash, 2012-26) agree with the hunt's within 1.6e-6.
  - No reproduced number moves at the displayed precision.

## 8. Reproduction check (mandatory; `python3 repro_check.py`, output in `repro_check.log`, OVERALL: PASS)

Same code path as `score_forward.py`, on `snapshot/` (fetched 2026-11-07, cut at 2026-11-06). Tolerance:
0.3 pp/yr and 1 pt of drawdown.

| check | got | hunt reference | diff |
|---|---|---|---|
| F14_F_live (DBMF) 2019-05-10..2026-11-06, 5 bps | +6.57 pp/yr, maxDD -30.0 (SPY -33.7), win 63/69/72, 3y 84, turnover 384%, DBMF w 0.42 | +6.57, -30.0, 63/69/72, 84, 384, 0.42 (REPORT_F14) | 0.00 / 0.0 |
| same, 23 bps | +4.95, maxDD -30.2 | +4.95 (only the 5 bps DD is logged: -30.0) | 0.00 / 0.2 |
| F4r2_stress_xs VAL 2013-01-02..2026-11-06, 23 bps | +3.23, -29.9, 67/69/69, 3y/5y 96/100 | +3.23, -29.9, 67/69/69, 96/100 (f4/tables.md) | 0.00 / 0.0 |
| same, 5 bps | +3.65, -29.8 | +3.65 | 0.00 / 0.1 |
| F13_D engine, synthetic sleeves, VAL, 5 bps | +4.04, -25.3, 59/67/75, 85/98, turnover ov/core 598/87 | +4.04, -25.3, 59/67/75, 85/98, 598/87 (f13/tables.md) | 0.00 / 0.0 |
| same, 23 bps | +1.23, -25.5 | +1.23, -25.5 | 0.00 / 0.0 |
| F13_D_live 2019-05-10..2026-11-06 (new) | +6.23 (+5.81), -34.0 / -33.7 | none: never run in the hunt | n/a |

`repro_data/f13_sleeves_2009.csv.gz` holds the hunt's 11 F13_D sleeves (2009+), used only by check 3. Provenance
and the original files' SHA-256 are in `repro_data/PROVENANCE.txt`.

## 9. Files

- `harness.py`: backtest, judge, calendar.
- `f14_engine.py`, `f13_engine.py`, `f4_rule.py`: the rules.
- `strategies.py`: the three frozen books, inceptions and next-session targets.
- `fetch_data.py`: the data fetcher.
- `score_forward.py`: the scorecard.
- `repro_check.py` and `repro_check.log`: the reproduction check and its output.
- `snapshot/`: data as of the freeze.
- `repro_data/`: F13 sleeves for check 3.
- `cache/`: refreshed data; gitignored, never hashed.

Python 3 with pandas, numpy and yfinance. Built with pandas 3.0.3, numpy 2.4.3, yfinance 1.5.2.

## 10. State at the freeze (`score_forward.py`, data through 2026-11-06)

No forward session has closed. All three books HOLD into 2026-11-09 with no decision at the 2026-11-06 close.
Fractions of NAV:

- F14_F_live: SPY 0.518, DBMF 0.435, IWD 0.327, MTUM 0.156, cash -0.435. This is the drift from the 2026-11-03
  day +2 decision. Stream state at that decision:
  - L wants +0.5 but is gated off by its trailing-12m shadow record.
  - T's cost-aware gate is open, but its bump is outside the TOM window.
  - The bond trend is 0.
  - M, V and S are on.

  The next decision is TOM day -3, the 2026-11-25 close (Thanksgiving accounted for), filled 2026-11-27.
- F13_D_live: SPY 1.490, DBMF 0.427, cash -0.490.
- F4r2_stress_xs: SPY 1.498, cash -0.498.

## 11. Freeze

Freeze timestamp: **2026-11-07T19:51:25Z** (UTC). SHA-256 of every file in this directory, computed after everything else was final. FROZEN.md cannot hash itself; its git commit pins it. `cache/` and `__pycache__/` are gitignored and excluded. Verify with `shasum -a 256 -c` on the block below, run from this directory.

```
d5e19c8139339db19753c9ec829357f75aea6cb29b50a63d6130341b07bb2053  .gitignore
9f008753116bd95a0d4420547817a3149a3ae0ab496c438207575821bfaa0191  f13_engine.py
a045dd1b0c1019d10b86982ebdca1032ec72c3bd4ac59a24fdb71df004615970  f14_engine.py
e66cd299c3a558d790322ea9e7e6fdbc133b8d65c36e2fb32dd34237d87ee440  f4_rule.py
6e2d21308da21c729f95edfd31278d3417052cfcd2430525c51c5c9bfec2e841  fetch_data.py
ae1433b61aef474979cc1a60ca100cb2b55e2dcb1a058b9338b18ce8386f163c  harness.py
e5be393d060a3b9d651ed12834f7f4dfec97409aeddfbc476b0818dd35999340  repro_check.log
47f70635571603e098d722d1d9564513062e1e6306871a9f70eda4683ace07ac  repro_check.py
c8e59c9285a635df73048ab66d4df2723377a657e4f6ec2ea931726c63ae6f0d  repro_data/PROVENANCE.txt
cc0f2838371137d7d685edd98a34c5a77203227a51ac75cf1299ca89aa3391e5  repro_data/f13_sleeves_2009.csv.gz
d41f7f5ae0aa17e6ca7c1c54a9232c71afec8398e1e85fe3822e92db4d9cf65c  score_forward.py
c5889690782919dabf2d6ad03a1b822fe3cd41c37819c9d9509c39b61749fa5d  snapshot/DBMF.csv
6428189139f5a333c8412bce866c0a7a59a1330fe569a38516661f37a259e77f  snapshot/DTB3.csv
840f4810e93364194a66ae131166ba7717e1702a9f22075cfdd4a70045baf589  snapshot/IEF.csv
a3ad972a8c5dee4d6dd556d3e932d37c7737ebbd948c25b53498f3c0c8811083  snapshot/IRX.csv
8cb59434b7d7810b98cdf2fb1a1c08e4b6a1f70862061ad844f6137586340895  snapshot/IWD.csv
6bc7a428575b7a5c09eeff4db7e4808b203f399626ddafacfc3bb63d612ce872  snapshot/MTUM.csv
6bea97e790896326b3d48a3229cc9f5514962053f3b1e6c9da1301de89a1bed8  snapshot/SPY.csv
f9431bc1c295e043cf9036292b3a5b275c5dcd7165c2d5b06924dd0cc94c6789  strategies.py
```
