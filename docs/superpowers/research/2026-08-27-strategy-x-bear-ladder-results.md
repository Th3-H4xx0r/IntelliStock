# Strategy X bear system: the regime ladder, and what it is worth

Date: 2026-08-27
Instance: `strategy-x` · Strategy document: `198`
Supersedes the economic verdict in `2026-08-26-strategy-x-bear-results.md`

## Outcome

The four-state regime ladder is implemented, tested and deployed. It does what
it was designed to do: it removes most of the bear-window loss the deployed
system carried, and it takes maximum drawdown from worse than SPY's to about
eleven points better.

It does not meet the stated objective. The objective was to make money in every
regime and to beat SPY by a lot. Measured, the strategy is positive in 8 of 15
frozen windows, not 15, and its return edge over SPY is between 0 and 2.8
annualised percentage points depending on what these ETFs actually cost to
trade. The drawdown improvement is the real and durable result.

## What the ladder is

`FULL → CAUTION → DEFENSIVE → RECOVERING`, holding 100% / 80% / 0% / 80% of the
regime core. It exists because the shipped filter is late at both ends.
Measured on real closes, TQQQ at the moment MA200 plus the volatility gate
first flip risk-off, against the moment the faster averages break:

| window | at first risk-OFF | at QQQ<MA50 | at QQQ<MA20 |
|---|---:|---:|---:|
| 2018 Q4 | -25.1% | -9.4% | -6.6% |
| 2020 covid | -21.1% | -20.7% | -13.7% |
| 2022 H1 | -25.4% | -9.0% | -7.6% |
| 2025 spring | -22.9% | -5.8% | -2.3% |

And at the other end, the deployed overlay returned -1.31% across the 2022
summer recovery while SPY returned +12.61%, because DEFENSIVE only ended when
MA200 was reclaimed.

`fast_bad` and `fast_good` are deliberately not complements: price must be below
BOTH moving averages to be a confirmed break and above BOTH to be a confirmed
reclaim, so the band between them is a dead zone. That one choice gives the
right asymmetry in either tape without a second threshold — a bull dip through
MA20 does not de-lever while MA50 holds, and a bear-market poke above MA20 does
not buy the falling knife while MA50 caps it.

## Result

15/15/70 allocation, one continuous replay of the real `run_once` over 15.0
years, next-bar fills, windows sliced from the run rather than re-warmed.

| assumed cost | CAGR | maxDD | turnover |
|---|---:|---:|---:|
| 2 bps | 17.89 | -22.23 | 1096%/yr |
| 5 bps | 17.51 | -22.34 | 1097%/yr |
| 10 bps | 16.82 | -22.56 | 1104%/yr |
| 23 bps (the engine's own) | 15.11 | -23.13 | 1108%/yr |
| SPY buy & hold | 15.12 | -33.72 | ~0 |

The engine confirms the drawdown and refuses the return. BT406990 ran the
earlier candidate end to end: +67.55% against SPY's +66.79% price return over
4.82 years — **+0.76pp total** — with a 17.47% maximum drawdown. The local
harness had predicted that drawdown almost exactly (-16.2% against -17.5%
actual) and overstated the return by two times. That asymmetry is the single
most useful thing measured here: **the risk control transfers faithfully; the
return does not.**

## Calendar years — the decisive measurement

Fifteen window slices flatter a strategy, because some are six weeks long. The
natural unit for "makes money in every regime" is the calendar year:

| year | A (BIL) | B (SPY) | C (QLD 2x) | SPY |
|---|---:|---:|---:|---:|
| 2011 | -4.3 | -8.6 | -13.4 | 3.0 |
| 2012 | 0.5 | 5.1 | 12.0 | 14.2 |
| 2013 | 31.9 | 43.3 | 52.5 | 29.0 |
| 2014 | 10.8 | 17.2 | 24.0 | 14.6 |
| 2015 | -7.3 | -11.0 | -7.3 | 1.3 |
| 2016 | 1.5 | 1.8 | 2.9 | 13.6 |
| 2017 | 37.6 | 45.2 | 46.3 | 20.8 |
| 2018 | -9.3 | -12.1 | -14.9 | -5.2 |
| 2019 | 15.3 | 24.0 | 32.6 | 31.1 |
| 2020 | 16.5 | 28.5 | 38.6 | 17.2 |
| 2021 | 22.9 | 37.9 | 38.8 | 30.5 |
| 2022 | -0.8 | -4.1 | -7.3 | -18.6 |
| 2023 | 18.9 | 26.6 | 44.5 | 26.7 |
| 2024 | 18.8 | 23.4 | 28.0 | 25.6 |
| 2025 | 12.2 | 19.4 | 18.8 | 18.0 |
| 2026 | 7.1 | 8.9 | 15.5 | 12.7 |
| **negative years** | **4** | **4** | **4** | **2** |
| **years below SPY** | **13** | **9** | **5** | — |

Every configuration is negative in MORE calendar years than SPY, and the losing
years are all the same ones: 2011, 2015, 2016, 2018 — chop. The strategy wins
enormously in trending years (2013 +52%, 2017 +46%, 2023 +44% against SPY's 29,
21 and 27) and loses in sideways ones. That is the definition of a leveraged
trend strategy, and it is what the trend literature predicts.

**This is why the objective is unreachable as stated, and it is not a tuning
problem.** The strategy has no alpha source. Its only signal is a trend filter
on one index, and the evidence is consistent that trend filters buy crash
protection, not excess return. Everything else here — leverage level, the
defensive sleeves, the commodity sleeve, managed futures — is asset allocation,
which moves you along a risk/return frontier but never off it.

A permanent managed-futures sleeve, the literature's own crisis-alpha
recommendation, was the last candidate and lands in the same place. Over the
7.3 years the data allows (DBMF from 2019-05, KMLM 2020-12, CTA 2022-03):

| asset | CAGR | maxDD | corr vs SPY |
|---|---:|---:|---:|
| SPY | 16.05% | -33.72% | +1.00 |
| DBMF | 9.17% | -20.39% | +0.19 |
| KMLM | 6.25% | -31.01% | -0.14 |
| CTA | 7.47% | -20.80% | -0.14 |

Blending it into SPY moves monotonically down the same frontier: 15% gives
15.17% / -29.72%, 30% gives 14.25% / -25.70%.

## What was tried and rejected

Every one of these was implemented, measured, and left off. The numbers are the
argument.

| mechanism | verdict | evidence |
|---|---|---|
| Slower re-entry than de-risking | REJECTED | CAGR falls monotonically 27.02 / 25.75 / 24.34 / 23.36 / 22.68 at 2/3/5/8/12 confirmation bars. A slower climb misses the recovery by as much as it avoids the relapse. |
| Binary trailing drawdown halt | REJECTED | Non-monotonic in its own threshold: 5% and 15% both give a WORSE drawdown than no guard. That is the signature of fitting noise. |
| Continuous drawdown taper | REJECTED | Monotonic and honest, and still strictly dominated: at a matched -21% drawdown it returns 16.27 against volatility targeting's 20.85. |
| SQQQ kicker | NEAR-INERT | 4x the size and 2x the hold change nothing, because DEFENSIVE is reached only after the fresh breakdown `advance_kicker` requires has already passed. It engaged twice in 1,258 sessions for a net +$4.29. |
| Wider rebalance band | REJECTED | 1108%/yr to 565%/yr costs 2.4pp of CAGR to save ~1.3pp even at the engine's own 23 bps. The rebalancing is doing real work, not churning. |
| CPPI | NOT ATTEMPTED | Disqualified in the literature by cash-lock, not gap risk. The cushion's log-growth turns negative above `m = 2(mu-r)/sigma^2`, which is 0.79 for a 3x-like sleeve against a commercial multiplier of 3-7. Carvalho, Gaspar & Sousa show CPPI-5 at 40% vol over 15 years returns the floor exactly, *conditional on the underlying tripling*. |

The pattern across all of them is one sentence: **a drawdown is a lagging
measure and realised volatility is a contemporaneous one.** By the time the
account is down the loss has happened, and de-levering on it mostly guarantees
missing the rebound.

## What actually worked

1. **Continuous volatility targeting** on the levered leg. Already in the
   module, default-off, and the largest single effect measured. A discrete rung
   that fires on a false alarm pays a full round trip; scaling by
   `target / (leverage * realised_vol)` pays nothing.
2. **A 200-day trend filter on the commodity sleeve**, up from 100. The sleeve —
   not the ladder — was the largest single cause of the chop loss: a 100-day
   filter bought every false start in 2015. Worth 2015 chop -19.9 to -14.0 and
   full-period drawdown -29.6 to -23.6, at no cost to any bear window.
3. **The ladder itself**, for the bear windows specifically: 2022 full year from
   -27.8% to about flat, and 5 or 6 of the six frozen bear windows beating SPY
   against 1 of 6 for the deployed baseline.

## Sizing, on the evidence rather than the fit

Peters (*Quantitative Finance*, 2010) gives optimal leverage `m* =
mu_excess / sigma^2`, so 3x beats 1x only when `mu_excess > 2 sigma^2` — 12.5%
at the Nasdaq-100's 25.79% five-year volatility (ProShares TQQQ prospectus,
Nov 2024). **3x is above growth-optimal for this index at any volatility over
roughly 18-20%**, which is why the sleeve weight has to be the thing that
moves rather than a constant.

A 0.18 volatility target at 25% index volatility implies about 24% of NAV in
TQQQ. The optimal-control literature lands independently in the same place:
van Staden, Forsyth & Li (2024) find 48-70% of wealth in a *2x* fund, and
Forsyth, van Staden & Li (2025) find 30-45%.

The volatility window was corrected on the same basis. An earlier cut used 12
days because it improved bear windows in sample — precisely the failure
Cederburg, O'Doherty, Wang & Yan (*JFE* 138(1), 2020) document, whose test
breaks the 20-day inverse-variance specification on 103 strategies. The
estimator is now `max(20-day, 60-day)`, the S&P DJI Risk Control convention:
de-risk fast, re-risk slow.

## Generalisation

Split-sample, and a different underlying entirely, both at the shipped config:

| arm | first half | second half | full |
|---|---|---|---|
| QQQ / TQQQ | 15.07 / -21.01 | 26.87 / -21.96 | 20.85 / -21.96 |
| SPY / SPXL | 11.83 / -23.24 | 22.60 / -22.68 | 17.09 / -23.24 |
| SPY buy & hold | 14.03 / -19.35 | 16.21 / -33.72 | 15.12 / -33.72 |

The drawdown control transfers — about -22% in every half on both underlyings,
against a no-control baseline of -36.8 and -41.7. The return edge is
Nasdaq-specific and loses to SPY on the S&P's first half. That bounds what this
strategy can claim.

## Defects found and fixed

- **Every Strategy X sell was flagged `would_block_in_phase2=True`** — 965 of
  965. `broker.py`'s Z2.1 check reads `action_intent` off the strategy summary
  and whitelists only graph_nexus's enum; Strategy X published none. Phase 1
  only logs, so this was invisible. When phase 2 enforces, a strategy with no
  recognised intent can never sell again — buy-only, permanently, with a
  levered core. Fixed: it now publishes `_nexus_action_intents`.
- **The ladder's most drastic action never stated its cause.** A volatility
  blowout logged the self-contradictory `emergency: above MA20 and MA50`.
- **The session clock overflowed a bound.** Date ordinals are ~739,000 and the
  bear module bounds every parsed counter at 100,000, so the clock read as
  corrupt and forced DEFENSIVE during risk-on bars. Now days-since-1970.
- **A cold or invalidated ladder starts DEFENSIVE and climbs**, so nothing can
  mint leverage out of absent state.

## Open, and blocking live use

- **`live_risk_state.DEFAULT_MAX_LEVERAGED_FRACTION = 0.10`** caps TQQQ at 10%
  of equity on the live order path, and `UnifiedOrderGate` blocks rather than
  clips. A 70% TQQQ core fills **zero** live. There is no env or config
  override. This must be resolved before any live consideration.
- **Strategy X's schema sets `broker_max_single_position_pct: 0.95`**, which
  disarms the broker's 15% failsafe process-wide inside that container for
  every sibling strategy in the same document.
- The engine did not persist terminal metrics for BT406990, BT449776 or
  BT857529 — the container exits at ~100% without writing `final_value`.
  Every result here was reconstructed from logs.
- Alpaca's IEX feed has no bars before roughly August 2021, so 2018 Q4 and
  2020 covid cannot be tested on the engine at all. They exist only in the
  local replay.

## TL;DR

- The ladder cuts the bear-window loss hard: 2022 from -27.8% to about flat,
  and 5-6 of 6 bear windows beating SPY against 1 of 6 before.
- Maximum drawdown goes from -41.7% to about -22%, which is eleven points
  better than SPY rather than eight points worse.
- The return edge over SPY is 0 to 2.8pp/yr and depends entirely on real
  trading costs. The engine's own cost model puts it at zero.
- Volatility targeting is what works. Every stop-loss, drawdown halt and taper
  tried here is dominated by it, for a structural reason that is unlikely to
  reverse on other data.
- It is positive in 8 of 15 windows, not 15. Chop and the three fast crashes
  remain negative.
- It cannot trade live today: the leveraged-fraction gate blocks it outright.
