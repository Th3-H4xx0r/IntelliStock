# Strategy XS — stacked growth

Date: 2026-08-27
Status: design, approved in chat, not yet implemented
Supersedes nothing. Strategy X stays as it is; this is a separate strategy.

## Why this exists

Strategy X cannot beat SPY, and the reason is structural rather than a tuning
failure. Measured across roughly seventy configurations, fifteen window slices,
sixteen calendar years, both sample halves, a second underlying and one engine
run: **every mechanism available to it trades return for drawdown.** It has no
alpha source — its only signal is a trend filter on one index, and a trend
filter buys crash protection, not excess return.

The specific mistake is that **Strategy X sells equity to buy protection.**
Every defensive move takes capital out of the asset that earns. Measured over
2019-2026, a de-risking portfolio (75% SPY + 25% managed futures) loses to SPY
in **7 of 8 calendar years**.

Strategy XS does the opposite. It keeps equity exposure and **adds** a
diversifying return stream, funding it with the capital a leveraged ETF frees
up. Over the same window that construction beats SPY in **7 of 8 years**.

The leverage arithmetic that makes this work: a position `w` in a `k`x fund has
the same volatility drag as direct exposure `m = kw`, namely `(m*sigma)^2 / 2`.
The drag depends on TOTAL exposure, not on the fund's multiple. So TQQQ at 33%
and QLD at 50% carry identical drag for identical beta — and TQQQ reaches that
beta with a third of the capital, leaving more for the diversifier. Using the
3x fund is therefore correct here, which is the opposite of the conclusion for
Strategy X, where the levered fund WAS the portfolio.

## Measured result

Full 15 years (2011-2026), daily, next-bar fills. `neg` counts calendar years
below zero; `<SPY` counts calendar years below SPY.

| design | CAGR | maxDD | Sharpe | neg | <SPY | 2015 | 2018 | 2022 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| SPY buy & hold | 14.21% | -33.72% | 0.86 | 2 | — | +1.2 | -4.6 | -18.2 |
| Strategy X, best | 15.11% | -23.13% | 0.86 | 4 | 9 | -11.0 | -12.1 | -4.1 |
| **Strategy XS** | **20.84%** | **-24.59%** | **1.04** | **2** | **4 of 16** | **+3.1** | -12.1 | -17.8 |

Strategy XS beats SPY on return by 6.6pp a year, has nine points less drawdown,
a materially better Sharpe, the same number of losing years as SPY, and **turns
the chop year positive** — 2015 is the year every Strategy X configuration
loses money.

It does NOT make money in every bear. 2018 and 2022 are negative. Both are fast
crashes with V-shaped recoveries, where the trend filter whipsaws and the
levered core eats the first leg down.

## What was rejected, and why it is not in this design

An inverse sleeve (SQQQ/PSQ) gated on the trend filter WOULD deliver net
positive bears — on 7.3 years of data containing exactly one bear:

| inverse sleeve | CAGR | maxDD | 2022 | neg years |
|---|---:|---:|---:|---:|
| none | 21.56% | -14.83% | -7.1% | 1 |
| SQQQ 20% | 19.40% | -18.21% | **+0.8%** | **0** |

Over the full fifteen years the same leg is monotonically destructive. These
rows isolate the inverse sleeve on a QLD-core variant with no diversifier, so
they are not comparable to the headline table above — only to each other:

| inverse sleeve | CAGR | maxDD | neg years |
|---|---:|---:|---:|
| none | 16.13% | -24.47% | 3 |
| PSQ 20% | 14.97% | -28.08% | 5 |
| SQQQ 20% | 12.43% | -35.28% | 5 |

Worse return, worse drawdown, and MORE losing years. It fits the one bear in
the short window. This independently reproduces the verdict already recorded in
`backend/strategy_x.py`, where the inverse leg destroyed 30.4% of terminal
wealth across 32 bars.

There is no setting in between: gate it tighter and it stops firing (Strategy
X's SQQQ kicker engaged twice in 1,258 sessions for a net +$4.29); loosen it
and it bleeds in every whipsaw. It ships as a **default-off** config knob with
these numbers recorded beside it, so it can be re-tested without re-deriving
that it fails.

## Allocation model

Named sleeves are paid first; the core is the residual. This is Strategy X's
existing convention and the reason it is repeated here is that the alternative
— sizing the core first — is what let an unfilled sleeve raise leverage in
bt 773215.

| sleeve | default | armed | beta |
|---|---:|---:|---|
| Graph stocks (`satellite_pct`) | 0.00 | 0.20 | 1x |
| Diversifier basket (`diversifier_pct`) | 0.45 | 0.45 | ~0 to SPY |
| Regime core (residual) | 0.55 | 0.35 | 3x when risk-on |

**Arming the Graph sleeve de-levers the core**, from about 135% equity beta
(0.45 x 3) to about 106% (0.29 x 3 + 0.20). That is intended: it swaps levered
index beta for stock-picking beta rather than stacking both. It is also a real
change to the risk profile, not a bolt-on, and the strategy must log it.

The Graph sleeve defaults to 0 because this repo has measured Graph Nexus to
have no cross-sectional skill — Spearman IC of `nexus_base_score` against
forward returns is negative in every window tested. Arming it is an operator
decision that needs its own evidence.

## Regime handling

`core_signal` and `core_vol_scale` are imported from `strategy_x.py`
**unchanged**. They are already tested, already measured, and re-deriving them
would fork two copies of the same boundary.

One behavioural difference from Strategy X, and it is the whole point:

> **Risk-off sends the core to CASH, not to the unlevered index.**

Strategy X routes the de-levered weight to SPY, so a portfolio nominally 70%
TQQQ is really 27% TQQQ and 57% SPY, and its measured result therefore tracks
SPY. Strategy XS sends it to BIL. The diversifier basket stays on in both
regimes — it is the return source, not the panic button.

## Diversifier basket

Equal weight across `diversifier_symbols`, default `["GLD", "UUP", "DBMF"]`.

Each member must be priceable and carry at least `diversifier_min_history_bars`
(default 60) of positive finite closes. Members failing that are dropped and
the weight **redistributes across the survivors**, never to the core. Routing a
short sleeve to a 3x fund is the defect measured at bt 773215, where bar 1 went
80% TQQQ instead of the designed 60%.

If NO member qualifies, the basket weight goes to `bear_cash_symbol`, not to
the core.

Why these three, measured 2011-2026 on the three years every Strategy X
configuration loses money:

| asset | CAGR | corr to SPY | 2015 | 2018 | 2022 |
|---|---:|---:|---:|---:|---:|
| UUP (dollar) | 2.49% | -0.16 | +7.0 | +7.0 | +9.5 |
| GLD | 7.42% | +0.06 | -10.7 | -1.9 | -0.8 |
| DBMF (managed futures) | 9.21% | +0.19 | n/a | n/a | +21.6 |
| TLT, for contrast | 2.13% | -0.27 | -1.8 | -1.6 | **-31.2** |
| VIXY, for contrast | -48.94% | -0.79 | -36.5 | +66.8 | -25.0 |

The dollar is the only asset with fifteen years of history that is positive in
all three problem years; its 2.5% CAGR is why it cannot carry the sleeve alone.
Gold supplies the long-run return, managed futures the crisis alpha. Treasuries
are excluded on the strength of 2022 — the year the stock/bond hedge failed and
took HFEA down 67% with it. Long volatility is excluded on carry.

DBMF has no history before 2019-05, so any measurement spanning earlier years
runs the basket on two members. That is stated wherever the number is quoted.

## Configuration surface

All new keys, all on the XS strategy; nothing in Strategy X changes.

| key | default | meaning |
|---|---|---|
| `strategy_xs_enabled` | `false` | master switch |
| `core_bull_symbol` | `"TQQQ"` | the levered leg |
| `core_leverage_factor` | `3.0` | declared multiple, used by the vol scale |
| `core_weight` | `0.82` | share of the residual held in the levered leg; the rest is cash |
| `core_cash_symbol` | `"BIL"` | where risk-off and the unheld core sit |
| `diversifier_pct` | `0.45` | basket weight |
| `diversifier_symbols` | `["GLD","UUP","DBMF"]` | equal weight |
| `diversifier_min_history_bars` | `60` | per-member eligibility |
| `satellite_pct` | `0.0` | Graph sleeve; 0.20 arms it |
| `core_filter_ma_bars`, `core_vol_*` | as Strategy X | the reused filter |
| `inverse_symbol` | `""` | OFF. See the rejection above. |
| `inverse_pct` | `0.0` | OFF |
| `min_order_usd`, `core_band_pct` | as Strategy X | execution |

## Fail-safes

- Cold or unreadable state starts **defensive** and climbs. Nothing mints
  leverage out of absent state.
- A short diversifier basket redistributes; it never raises core leverage.
- Non-finite config coerces to the documented default, never to a larger
  position. Every parser fails toward less exposure.
- The strategy declares its own universe and publishes
  `_nexus_action_intents` for every sell. Strategy X shipped without the
  latter, and all 965 of its sells logged `would_block_in_phase2=True` — when
  the broker's Z2.1 check starts enforcing, a strategy with no recognised
  intent can never sell again.

## Validation protocol, frozen before any run

Pre-registered so the gate cannot be moved after seeing the numbers.

**Report:** full-period CAGR, max drawdown, Sharpe, the full calendar-year
table, both sample halves, turnover per year, and realised beta — against SPY
and against Strategy X.

**Gate, all four required:**
1. CAGR strictly above SPY's.
2. Max drawdown strictly better than SPY's.
3. No more losing calendar years than SPY.
4. Split halves agree in sign on 1 and 2.

**Costs.** The local harness charges 23 bps, calibrated to the engine's own
fills rather than assumed — 2 bps made the Strategy X harness overstate return
twofold. Any candidate must clear the gate at 23 bps, and the cost sensitivity
(2 / 5 / 10 / 23 bps) must be reported, not just the favourable end.

**Engine.** One API run of the frozen candidate before any promotion claim.
The local harness predicts drawdown faithfully and return badly; return claims
come from the engine only.

## Known limits, stated up front

- **It is not positive in every bear.** 2018 and 2022 are negative. The only
  construction that fixes that is measured to fail out of sample.
- **The managed-futures member has 7.3 years of history**, containing one bear.
  Any DBMF-dependent number is thin evidence.
- **The diversifier members are survivor-selected** — chosen from funds that
  exist today.
- **2015 turning positive is a two-member basket result** (GLD/UUP); DBMF did
  not exist.

## Live blocker, inherited

`live_risk_state.DEFAULT_MAX_LEVERAGED_FRACTION = 0.10` caps any leveraged ETF
at 10% of equity on the live order path, and `UnifiedOrderGate` **blocks rather
than clips**. A 45% TQQQ core fills **zero** live, and there is no env or
config override. This must be resolved before Strategy XS can trade real money,
independently of anything in this design.
