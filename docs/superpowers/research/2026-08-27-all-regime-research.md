# All-regime strategy research — 2026-08-27

Goal set by the operator: beat SPY by a wide margin over 3-5 year rolling
windows, keep max drawdown at or below SPY's, and make bear calendar years
flat-to-positive, on a $6k live Alpaca account. This memo records what nine
research agents and eight pre-registered tests found, so the design that
follows (`docs/superpowers/specs/2026-08-27-strategy-eb-design.md`) rests on
evidence rather than hope. Raw tables: session scratchpad `tests/*.md` and
`backtest_history.csv` (913 finished runs).

## 1. Why Strategy X and XS failed — structurally

1. **No alpha, only levered beta.** Every directional voter measured below
   the 0.6045 "always long" base rate (`backend/strategy_x.py:13-18`). The
   Nexus graph never produced a signal distribution in any run (97% of
   scores 0.000; LLM overlay changed 0.21% of decisions).
2. **The engine's cost model is flat.** `simulated_execution.py:92-122`
   charges 23.2 bps one-way on *every* symbol — SPY, TQQQ, BIL included —
   a rate calibrated on small-cap Nexus fills (notional-weighted spread
   45.6 bps). BT406990 paid $1,657 of spread on $4,053 of gross gain. At
   realistic ETF spreads (~4 bps) Strategy X finishes ≈ +90% vs SPY +66.8%
   with a −17.5% max drawdown vs SPY's −25.4%, and is SPY-relative positive
   in 2022 (+8.5pp), 2023 (+3.5), 2025 (+4.9). "X ≈ SPY" was a
   measurement artifact. XS still loses after the correction (≈ +58%).
3. **Unquantized daily vol-scaling of a 3x leg → 1,000-2,000%/yr turnover.**
   That, times the flat 23.2 bps, is where the money went.
4. **Every bear fix was a bottom-detector or a de-risk-into-nothing.** The
   SQQQ 4-of-4 gate fires when QQQ is up 74% of the next 5 days; 12 binary
   levers, 3 chop occupants and dip re-entry all removed more bull than they
   saved. Risk-off to cash costs ~8pp of CAGR.
5. **Peak give-back is confirmed out-of-sample.** The forward-paper instance
   (doc 197) went from +5.5pp alpha on 2026-06-16 to −0.2pp by 2026-08-22.
6. **Both strategies were blocked live** by
   `live_risk_state.DEFAULT_MAX_LEVERAGED_FRACTION = 0.10` (block, not clip)
   and the 15% single-position cap that trims to zero.

The two things that transferred faithfully from local harness to engine were
*drawdown* (−16.2 local vs −17.5 engine) and *turnover*; return did not.

## 2. Platform facts that bound any design

- Long-only, no margin, no options/futures, no shorting; inverse ETFs
  tradable but every measurement says they lose.
- Decision at bar t, fill at first quote after t+1 bar. Passive limit
  fills exist (`SimulationOrder.limit_price`, `passive_execution_enabled`)
  and are deliberately pessimistic; default off. Slippage on $500 clips is
  ~0.1 bps: 22.8 of the 23.2 bps is half-spread.
- Engine daily history from 2020-07-27 (IEX), no SIP, no VIX, no macro
  tables; intraday history effectively starts 2025. Daily cadence is forced.
- Nexus news/events begin 2025-01: a Nexus sleeve cannot be validated in
  any bear.
- `equity_total_cost_bps` accepts only 25 or 50 — the engine can stress
  cost up, never down. Measuring an ETF book needs a symbol-tiered model.

## 3. Pre-registered tests (thresholds frozen before data; 23.2 bps;
non-overlapping windows; yfinance daily)

| # | idea | verdict | decisive number |
|---|---|---|---|
| 1 | XAT long-only cross-asset trend | KILL | 2/4 worst SPY years; 2013-19 −9% cumulative; CAGR 2.9% |
| 2 | CLR credit-led divergence top-detector | KILL | n=77, forward −35 bps vs unconditional (t −0.95); DD hit-rate below base rate |
| 3 | DISP dispersion timer for sector momentum | KILL | max t 1.84; sector 60d/21d momentum is −38 bps/mo gross |
| 4 | CPL cointegrated ETF-pair rotation | KILL | 13/80 pair-years pass ADF; +0.09 bps/qtr gross; chop 0-for-14 |
| 5 | OID overnight−intraday divergence | KILL | Q5−Q1 29 bps, t 1.55; inverts in high-momentum names |
| 6 | 52-week-high proximity | KILL | wrong sign: laggards beat leaders by 81 bps/mo |
| 7 | Bar-based spread estimators for sizing | KILL | rho(CS, CHL) 0.27-0.42; SPY estimated 11-21 bps vs ~1 real |
| 8 | Small/mid-cap reversal + passive fills | KILL | gross 21 bps/mo ≈ one round trip; at 3 bps 14.5%/yr vs SPY 14.6%, DD −41% |
| 9 | Vol-targeted levered core, SPY remainder | PARTIAL | 0/288 meet the full bar; **+2.6pp CAGR at matched max drawdown (49/52 configs, same sign both halves)**; passive fills ≈ +0.7pp; GHM gate a wash |

Test 9 is the only survivor and it is a *risk transform*, not alpha: being
de-levered into rallies costs ~14pp/yr gross and is repaid ~12-13pp/yr in
selloffs; what survives is the drawdown reduction. The cheapest thing that
clears a −40% drawdown cap with no timing at all is a static 34% QLD / 66%
SPY blend: 21.3% CAGR, 10%/yr turnover, versus the best timed config's
24.9% at 601%/yr.

## 4. Literature and practitioner record (what to steal, what to avoid)

- Vol-managed alphas (Moreira-Muir) do not survive out of sample (Cederburg
  et al. 2020); vol targeting helps equities as a drawdown tool (Harvey et
  al. 2018). Use it for risk, expect no alpha.
- Gayed's own "Leverage for the Long Run" fund (RORO) is ≈ −1.5%/yr live
  since 2020; HFEA fell from +150% to −14% cumulative in 2022; 9Sig's
  faithful-rules drawdown is −99.7%. Bare 200-day crosses on 3x products
  and leveraged hedges are dead.
- Quantopian's 888-algo study: backtest Sharpe has R² < 0.025 against live;
  more backtests → wider live gap. Select on drawdown, skew, and recent
  window, never on full-sample Sharpe.
- Rebalance-timing luck exceeds 100 bp/yr tracking error (Hoffstein);
  tranching across N staggered dates cuts it by 1/N.
- Slow beats fast (Carver: slow trend Sharpe 0.49 vs fast 0.31); bands and
  buffers instead of bare crosses were the one thing r/LETFs learned from
  2022-23.
- No audited retail algo track record above 30% CAGR over 5+ years exists.
  The $10k→$1M stories are concentrated leverage in 2020-21 with
  survivorship reporting.

## 5. What is and is not reachable

With weight ≥ 0 and the de-levered remainder in SPY, a 2022 above SPY's own
−18% is impossible by construction. Bear *years* flat-to-positive requires
the remainder in T-bills, which costs ~8pp of CAGR, or a hedging asset —
and every hedging asset tested (TLT, GLD/UUP, managed futures, SQQQ,
cross-asset trend) failed. The operator's bar is therefore a dial between
two strategies, not one strategy; the design exposes the dial.

Realistic envelope for the chosen design on 2010-2026 data, at realistic
ETF costs: CAGR 20-25%, max drawdown −35 to −40%, 3-year rolling win rate
vs SPY ≥ 95%, 2022 ≈ −30% (SPY remainder) or ≈ −12% (BIL remainder, CAGR
≈ 15-17%). Every local number overstates; the engine, with a corrected cost
model, is the verdict.
