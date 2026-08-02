# Index-core allocation for Graph Nexus — design

Status: **design only**. Nothing here is wired. The prototype in
`backend/core_sleeve.py` is a pure module with no call sites; every config key
it reads defaults to OFF/identity.

Author's summary in one line: hold SPY by default and let the graph tilt
around it, instead of holding cash by default and letting the graph fill it.

---

## 0. What the code actually does today

Every claim below was read out of the source, and every config value out of
Strategies doc 179 (read-only) on 2026-08-03.

### 0.1 The levers, and whether anything reads them

| Key | doc-179 | Reader | Verdict |
|---|---|---|---|
| `nexus_portfolio_pct` | 0.95 | `graph_nexus_analysis.py:27730` (buy-budget split) and `:31124` (total new-spend cap) | **Real, but weaker than it reads.** It is not a portfolio weight. It splits the *per-bar buy budget* between stock and ETF buys, and caps cumulative new-entry `buy_cash` at `portfolio_total × 0.95`. `portfolio_total` is full NAV, so once a large core exists this cap stops binding by construction. |
| `etf_portfolio_pct` | 0.15 | `:27731`, `:27735` | Real, same mechanism. Splits the buy budget on bars where both stock and ETF buy lists are non-empty. **Not a standing target weight.** |
| `cash_reserve_floor_pct` | 0.02 | `_get_scaled_cash_reserve_floor_pct:8815` → `_compute_available_buy_budget:11142` | Real. Floors buy budget at `initial_value × pct`. Scaled by account size unless set explicitly (doc-179 sets it). |
| `single_position_max_pct` | 25 | `_clip_to_single_position_cap:10292`, 4 call sites (`:10481`, `:10692`, `:14050`, `:30560`) | Real. Percent-points. Clips an order to remaining headroom. |
| `max_single_position_pct` | 0.15 | **none** | **DEAD.** Already listed in `broker.py:_DEAD_STRATEGY_CONFIG_KEYS:4133`, which logs it red on every boot. It is still in doc-179. Delete it — it reads like the broker's 15% cap and is not one. |
| `slot_min_notional_pct` | 1.5 | `_slot_min_notional:9185`, called at `:30567` and `:30753` only | **Half-dead.** Both call sites are inside the backfill-queue drain. Main-signal new entries are not gated by it. Do not rely on it to keep satellite positions above dust. |
| `deployment_ramp_*` | 0.9 / 0.95 / 1.0 | `_get_deployment_ramp_caps:11059` → `_compute_available_buy_budget:11149` | Real, but `_get_deployment_ramp_bar_index:11088` counts **bars, not days**. At 900s granularity a 3-element ramp is exhausted in 45 minutes. It is a cold-start throttle, not an allocation control. |
| `rotation_min_delta` | 0.5 | `:10052`, `:28116`/`:28711` | Real. Score delta an incoming name must beat a held name by. |
| `rotation_min_hold_days` | 30 | `:10019` with per-regime overrides at `:10024`/`:10040`/`:10047` | Real, **but governs the rotation path only.** |
| `sell_enforcement_min_hold_days` | 15 | `:9461`, `:22436` | Real, **trend-reversal path only.** |
| `regime_profiles` | bull + recovery | `broker.py:_apply_regime_profile:4210`, applied at `:4482` | Real. Shallow merge onto a copy. Strips `regime_*`/`max_positions*` and `_REGIME_PROFILE_BASE_ONLY_KEYS` from overlays so detection levers can't be regime-dependent. |
| `residual_sleeve_*` | enabled, SPY, bear symbol `""` | `broker.py:_residual_sleeve_config:2678`, 3 call sites | Real. See below. |

**The min-hold gates do not control turnover.** Measured holding periods, FIFO-matched
from the trade ledgers: bt 383982 (2025-11-21..2026-01-13, 143 closed lots) median
**17 days**, but **19% of lots closed within 5 days**; bt 636325 (2025-04-07..2025-06-03,
12 closed lots) median **0 days, 100% within 1 day**. The short-hold mass comes from a
different family of exits that never sees `rotation_min_hold_days`:
`fast_loser_cut_min_hold_days=2` at `fast_loser_cut_pct=-10`,
`profit_take_tiers=[[20,0.33],[40,0.5]]`, `trailing_stop_activation_pct=15`,
`bear_book_trim_enabled=true`, and the `portfolio_dd_soft/hard/kill = 5/9/12` circuit.
Any turnover budget that does not govern that family will not bind.

### 0.2 The residual sleeve

`_residual_sleeve_config` (broker.py:2678) is the single parse point; all three call
sites go through it.

- **Deploy** (`_residual_sleeve_deploy:3447`, called at `:13646`, cycle end).
  Only in confirmed `bull` — or `chop` when `residual_sleeve_chop_enabled` and the
  `chop_min_ret20_pct` gate passes (both absent in doc-179, so bull-only today).
  Parks `cash − max(buffer, release_cash_pct + buffer) × NAV`, i.e. everything above
  **17% of NAV**, with a floor of `max($50, 5% × NAV)`.
- **Release** (`_residual_sleeve_release:3118`, called at `:12598`, cycle start).
  On `bear`/`crash`: **full unconditional liquidation**. Otherwise, if
  `cash < 15% × NAV`, sell just enough to refill cash to 15%, subject to
  `min_park_hours = 24`.
- Exemption: `_sleeve_symbols` (graph_nexus_analysis.py:7459) makes the sleeve legs
  invisible to scoring (`:20209`), the position safety net (`:27079`), the bear-book
  trim (`:27167`, `:7832`), broker sell-enforcement (`broker.py:11648`) and the
  regime position cap (`broker.py:12945`). This machinery was hard-won and is the
  reason a core is cheap to build here.

**The trigger is a cash level with a 2pp hysteresis band (park above 17%, release to
15%).** Every stock-lane trade moves cash across that band. That is the churn.

- `residual_sleeve_bear_symbol` is `""` in doc-179 — **the SQQQ hedge is already OFF
  in live.** It was on in the backtests that produced the bull-window numbers.

### 0.3 The measured book

Reconstructed from `portfolio_value_history[].positions_snapshot × prices`,
bt **987397** (2026-03-30..04-27, the run the brief cites at $19,855 notional /
40.9% sleeve / SPY $4,131 / SQQQ $3,988 — reproduced exactly), 958 snapshots:

| Bucket | mean weight | median |
|---|---|---|
| cash | **25.1%** | 15.0% |
| SPY (sleeve) | 4.2% | 0.0% |
| SQQQ (hedge) | 4.2% | 0.0% |
| single stocks | 66.6% | 68.5% |

Mean long beta **70.8%**. Mean dead-or-short **29.2%**, in a window where SPY total
return was **+13.23%**. The median cash weight is 15.0% — exactly
`residual_sleeve_release_cash_pct`. The sleeve is pinning cash at its release target.

Decomposing that run's **+0.78%** against the measured weights and the measured
$60 of realised cost:

```
+0.78 = 0.666·r_stocks + 0.042·(+13.23) + 0.042·(−3×13.23) + 0.251·0 − 1.00
      ⇒ r_stocks ≈ +4.3%
```

Applying the *same* weights to bt **852704** (+1.38%, the figure in the brief, with
$80.75 = 1.35% of realised cost) gives `r_stocks ≈ +5.8%` — approximate, since the
weight path was only reconstructed for 987397. Either way the stock sleeve returned
roughly a third of the index on a strong bull window. **n = 1 window, the weights
are time-weighted averages that ignore the path, and the replicate noise is large —
this is a decomposition, not a verdict** — but there is no measurement anywhere in
this project showing the picks beat the index gross.

### 0.4 The noise floor is worse than stated

The same window `2026-03-30..04-27` at granularity 900 on strategy 179 produced,
across runs on 2026-08-01/02: **+0.58, +0.78, +1.38, +3.32, +7.05, +10.45%**. The
brief's 2.11pp median replicate spread is a floor on the floor. Any evaluation that
leans on a return *difference* under ~3pp on a 28-day window is measuring nothing.

---

## 1. Target structure

### 1.1 Instrument: SPY, not QQQ, not a blend

1. `_sleeve_symbols` and its six exemption sites already key off
   `residual_sleeve_symbol`, and doc-179 already sets it to `SPY`. Reusing it means
   the change is a **sizing rule**, not new plumbing. Introducing a separate core
   symbol without adding it to `_sleeve_symbols` would let the monitor, the kill
   loop, fast-loser-cut and the bear-book trim sell the core. That is the single
   highest-risk mistake available in this design.
2. The benchmark is SPY **total** return: `_fetch_adjusted_spy_benchmark`
   (broker.py:1321) fetches with `adjustment="all"` and `experiment_registry` pins
   `total_return: True`. A QQQ core would inject tracking error that is a factor bet,
   not alpha, and this project has no evidence the factor bet is positive. Making the
   core *be* the benchmark makes "did the tilt add anything" a clean question.
3. `portfolio_emulator.DEFAULT_EQUITY_DIVIDEND_YIELDS` credits SPY at 1.25%/yr and
   QQQ at 0.50%/yr, so a SPY core is compared TR-to-TR with no convention mismatch.
4. The satellite is **already** tech/momentum-tilted: `_TREND_ETF_MAP` routes
   "technology"/" ai " to `QQQ/XLK/AIQ/SOXX`, and momentum discovery selects on
   20d/60d returns. A QQQ core would double the beta the graph already produces.
   SPY diversifies against the tilt.

### 1.2 Weights

```
cash            2%   (already cash_reserve_floor_pct = 0.02)
satellite   0–68%   (graph-selected single names + trend ETFs)
core        rest, clamped to [30%, 98%]
```

with `core_target_pct = 0.60` as the base. Rationale, not optimisation:

- The satellite falls from a measured 66.6% mean to ≤38% steady-state. Turnover
  scales with satellite notional, so this alone is the largest single lever.
- 60% core makes "market return minus costs" the dominant term, so a zero-edge tilt
  costs little. At 40% the outcome is still dominated by the thing with no evidence.
- 38% of a $6,000 book is $2,280, or ~$285 across 8 names — above
  `min_position_size = 100`, above the $1 broker floor, and a 5% trim ($14) clears
  the $5 sleeve release floor. `single_position_max_pct = 25` (of NAV, $1,500) stays
  non-binding.

**60 is not claimed optimal.** It is the largest core that leaves the graph enough
capital to be measurable.

### 1.3 The tilt rule

```
core_target = clamp(1 − cash_floor − satellite_actual, core_min_pct, core_max_pct)
```

That is the whole rule, and everything the brief asks for falls out of it:

- **A graph BUY becomes an overweight.** Funding a satellite name raises
  `satellite_actual`, which lowers `core_target`, which sells SPY. Net: that name is
  now held *above* its index weight, funded by the index.
- **A graph SELL becomes an underweight.** Exiting a name lowers
  `satellite_actual`, raising `core_target`, buying SPY back. The name reverts to
  its index weight (effectively zero for most of this universe). **The proceeds go
  to the index, never to cash.**
- **No opinion ⇒ hold the core.** Zero BUY signals means `satellite_actual = 0`
  means `core_target = 98%`. The default state is fully invested in the market.
- **No shorting.** A SELL on an unheld name still does nothing; there is no equity
  short path in this codebase and this design does not add one.

`core_min_pct = 0.30` bounds how much of the book the graph may ever command (68%).
`core_max_pct = 0.98` is "fully invested when silent".

### 1.4 Rebalance rule (this is where the turnover is)

Replace the cash-level trigger with a **weight-band + cadence** trigger:

- Rebalance only when `|actual_core_w − core_target| > core_rebalance_band_pct`
  (default 0.05 absolute).
- At most one non-funding rebalance per `core_rebalance_min_days` (default 5
  trading days).
- Two exemptions, both one-directional and both risk-reducing or already-approved:
  1. **Funding**: a satellite buy the allocator already approved may release core
     immediately, ignoring the cadence. This is the sleeve's original job and it
     must stay instant or the allocator starves.
  2. **Bear de-risk**: see below.
- Keep `_RESIDUAL_SLEEVE_MIN_RELEASE_USD = 5.0` (broker.py:2790) and add the
  symmetric deploy floor that already exists (`max($50, min_deploy_pct × NAV)`).

### 1.5 Regime behaviour

Replace the full protective liquidation on `bear`/`crash` (`_residual_sleeve_release`
line 3418: `sell_qty = qty; frac = 1.0`) with a **bounded** de-risk:

```
core_target_bear = core_target_pct × core_bear_scale     (default 0.5 ⇒ 30%)
```

gated by `core_bear_min_dwell_days` (default 3), reusing the semantics already built
for `residual_sleeve_bear_min_dwell_days` (broker.py:2720 — added precisely because
17 of 19 backtests parked a hedge on day 1 of a +12.8% month). The freed weight goes
to **cash**, not to an inverse ETF. Cap transitions at 2 round trips per year
(`core_regime_max_round_trips_per_year`); the cadence gate enforces this.

A full liquidation is 60pp of one-way notional in a single bar and is the largest
discrete turnover event the system can produce. Halving instead of zeroing costs
30pp and keeps the book tracking if the regime call was wrong — which, per the
sleeve's own comment at broker.py:2720, it demonstrably has been.

---

## 2. Turnover: making it a first-class constraint

### 2.1 The buy/hold spread (Novy-Marx & Velikov)

Today's gate is `buy_threshold = 0.3` / `sell_threshold = −0.3` on `raw_net_score` —
a symmetric dead-band on an **absolute** score. The score distribution shifts with
news volume, so a quiet day admits everything or nothing.

Make it a **rank** spread:

- `satellite_entry_rank_pct` (default **0 = OFF**): a name may be *entered* only if
  it is in the top *N*% of the bar's scored universe **and** clears the existing
  `allocation_execute_min_raw_score = 0.35`.
- `satellite_exit_rank_pct` (default **100 = OFF**): a *held* name is exited only
  when it falls out of the top *M*%.

Recommended live values: **enter top 10%, exit below top 33%.** This is a pure
superset of today's gate — it can only ever block a trade — so it ships as an
identity no-op and is enabled per-config.

### 2.2 Cadence vs signal decay

News-sentiment alpha peaks at 3–10 days. The loop reads the graph at 900s in
backtest and hourly-ish live: a 5-day signal is re-evaluated ~26×/day in backtest.
Add `satellite_action_min_bars` (default 0 = OFF): entry and exit *decisions* are
acted on at most once per trading day. Scoring, monitoring and protective stops
still run every bar — only the discretionary entry/exit path is throttled. That one
change divides the satellite's opportunity to trade by ~26 in backtest and ~7 live.

### 2.3 Hard monthly turnover budget

Nothing in the equity path has one. `benchmark_alpha/research_policy.py` has a
`turnover_cap` (lines 107–122) but its own docstring says it is a *non-trading*
research policy with "no broker, WAL, runtime, or order-intent dependency" — it
cannot constrain live or backtest execution.

Add `turnover_budget_monthly_pct` (default **0 = OFF**). Track rolling 21-session
one-way notional / NAV in the strategy cache as `_turnover_ledger`, persisted the
same way `_RESIDUAL_SLEEVE_PERSIST_KEY` (broker.py:2797) is — no new table, no new
writer, no new failure mode.

When the rolling figure exceeds budget:

- **Block**: new satellite entries; all rotations (a rotation is 2× notional and is
  churn by construction); non-funding core rebalances.
- **Allow**: protective exits (stops, DD circuit, bear de-risk) and core rebalances
  *toward* target.
- **Never block a risk-reducing SELL.** A budget that traps you in a loser is worse
  than the cost it saves.

### 2.4 The arithmetic

One-way cost is 30.3 bps (`portfolio_emulator.py:69–86`: spread 24/2 + slippage 18 +
fee 0.3).

**Baseline.**

| | one-way turnover | annual cost @30.3 bps |
|---|---|---|
| Live (measured, ~35×/yr) | **290%/month** | **10.6%/yr** |
| Backtest bt 852704 (4.44× / 20 sessions) | 466%/month, 56×/yr | **17.0%/yr** |
| Backtest bt 987397 (3.31× / 20 sessions) | 347%/month, 42×/yr | 12.6%/yr |
| Backtest bt 383982 (2.95× / 38 sessions) | 164%/month, 20×/yr | 6.0%/yr |

**Target: <50%/month = 6×/yr = 1.8%/yr.**

**Path from 290%/month (using the measured 40.9%/59.1% sleeve/signal split):**

| Component | Today | After | Mechanism |
|---|---|---|---|
| Sleeve park/release | 119%/mo | **3%/mo** | Weight band (±5pp) + 5-day cadence replaces the 2pp cash band. A 60% core with a 5pp band and a 5-day floor rebalances ≈1×/month. |
| Satellite entry/exit | 171%/mo | **25%/mo** | ×0.57 from shrinking the sleeve 66.6% → 38% of NAV (→ 98%/mo at unchanged per-dollar intensity); ×0.26 from daily-only action + the rank spread. |
| Regime de-risk | (inside above) | **10%/mo** | ≤2 round trips/yr × (30pp down + 30pp up) = 120pp/yr. |
| **Total** | **290%/mo** | **38%/mo** | = 4.6×/yr |

**Cost: 4.6 × 30.3 bps = 1.4%/yr.**

**Saving: 10.6 − 1.4 = +9.2%/yr vs live. 17.0 − 1.4 = +15.6%/yr vs backtest.**

The ×0.26 in the satellite row is the only number in this table that is a *forecast*
rather than an arithmetic consequence of a structural change, and it is the number
backtest 1 exists to falsify. The other rows follow mechanically from the band, the
cadence and the sleeve size.

---

## 3. The SQQQ hedge and the residual sleeve

**Kill the hedge. Keep the sleeve, re-purpose it.**

Hedge:

- It is **already off in live** (`residual_sleeve_bear_symbol = ""` in doc-179), so
  removing it changes no live behaviour.
- Where it was on, it cost **1.67pp time-weighted** on the bull window (mean 4.2% of
  NAV in a −3× instrument through a +13.23% index move) and consumed **15.0%** of all
  traded notional ($2,100 deploy + $1,888 stop-exit on a $19,855 book).
- Its maintainers have layered **six** independent suppressors on it:
  `bear_require_fresh_pct`, `bear_min_dwell_days`, `_sleeve_rally_onset`, the
  one-stop-per-episode latch, `bear_leg_trail_activation_pct`/`_pct`, and the
  post-exit `min_park_hours` re-entry dwell. Six patches on one signal is evidence
  the signal does not separate the cases it needs to separate.
- A −3× daily-rebalanced instrument carries a volatility-decay term that is
  structurally negative outside a monotone decline, and the regime label lags a
  V-bottom by ~2 sessions **by the code's own comment** (broker.py:3491).

Recommendation: leave the bear-leg code in place — it is default-OFF and the exit
machinery was expensive to get right — but make `""` the documented, enforced
default, and route bear de-risk to cash. If a hedge is wanted later, a −1× (SH) or a
listed put has no decay term; that is a separate project needing its own evidence.

Sleeve: keep it, keep `_sleeve_symbols`, keep the exemptions, keep the $5 release
floor and the persistence. Change only the sizing rule (§1.4). The 41%-of-turnover
figure is a property of the **cash-level trigger**, not of the sleeve.

---

## 4. The cheapest falsifying evaluation

Budget: 2–3 backtests, ~$1.30 each, ~$9 remaining.

**Do not spend a backtest on return.** With a ≥2.11pp (observed: ~10pp) replicate
spread on a 28-day window, a return comparison at n=1 decides nothing. Spend them on
quantities that are **counted, not estimated**, and therefore have no noise floor:

- traded notional — exact, `Σ|backtest_trades[].total|`
- realised cost — exact, `Σ(fees + slippage_cost + spread_cost)`
- weight path — exact, `portfolio_value_history[].positions_snapshot × prices`
- rebalance count — exact, count of `residual_*` ledger rows

### Backtest 1 — does the structure hold?

Window **2026-03-30..04-27**, granularity 900, core ON (60/38/2), SQQQ off, budget ON
at 50%/mo, rank spread ON (10/33), daily action cadence ON.

Decides, all pass/fail:
- **(a)** mean core weight within **55–65%** across all snapshots → the band+cadence
  holds a target rather than oscillating;
- **(b)** total one-way notional **≤ 48% of NAV** (50%/mo scaled to 20 sessions);
- **(c)** realised cost **≤ 0.15% of book**, against the measured 1.00% (bt 987397) /
  1.35% (bt 852704);
- **(d)** core rebalance count **≤ 5** in 20 sessions.

**Kill condition:** notional > 100% of NAV, or mean core weight outside 55–65. The
mechanism does not work and nothing downstream matters.

### Backtest 2 — does it survive a regime turn?

Window **2026-03-02..03-30** (the bear window where the old sleeve earned its keep),
same config.

Decides:
- **(a)** core rebalance count **≤ 4** in 20 sessions → the cadence gate binds
  *through* a transition, which is when it is most likely to fail;
- **(b)** max drawdown **≤ SPY's** max drawdown over the same window → the bounded
  de-risk does something, rather than only paying costs;
- **(c)** turnover stays in budget through the transition.

**Kill condition:** >4 rebalances, or drawdown worse than SPY's. Either means the
de-risk is a cost with no benefit and `core_bear_scale` should be 1.0 (no de-risk at
all — just hold the core through the bear, which is what an index investor does).

### Backtest 3 — only if 1 and 2 pass — does the tilt pay for itself?

Window **2025-11-21..2026-01-13** at granularity 3600, core ON. There is already a
same-config, same-window, no-core comparator on record: **bt 383982, +7.62%**.

Decides: the cost delta (exact) and the return delta (noisy). **State the reading
rule before running:** with a ≥2.11pp floor on a 28-day window and 53 days here, any
return difference under ~3pp is reported as **"no signal"** — not as a win, not as a
loss. A cost delta of the predicted size (≈−1%/yr of drag over the window) is the
only thing this run can establish, and it is worth one backtest because it confirms
the arithmetic in §2.4 on a window with a different cadence and a different regime mix.

### What none of these can establish

**Alpha.** The Neo4j graph is read live, so a historical decision sees post-decision
news, and ~36% of delisted names are absent from the graph. Any return attributable
to *stock selection* in these runs is contaminated in a direction that flatters.

These three tests were chosen precisely because turnover, cost, weight path and
rebalance counts are **immune to that lookahead**: a leaked signal changes *which*
names get bought, not *how much notional* the band, the cadence and the budget
permit. That is the whole reason the evaluation is built on mechanical quantities.

---

## 5. What this claims, and what it does not

**Claims.**
- With a 60% SPY core, ~60% of the book earns SPY *total* return minus a near-zero
  maintenance cost, deterministically. That is arithmetic, not a forecast.
- Turnover falls from ~290%/month to ~38%/month, cutting cost drag from ~10.6%/yr to
  ~1.4%/yr — **+9.2%/yr**, larger than any alpha this project has ever claimed.
- The book stops being structurally unable to track a rising index. The measured
  29.2% mean dead-or-short weight in a +13.23% window goes to ~2%.

**Does not claim.**
- Any alpha. Measured gross edge ≈ zero. The §0.3 decomposition puts the stock
  sleeve's own return at ~+4 to +6% against SPY's +13.23% on that window; if that is
  real the sleeve has *negative* gross alpha, not zero. n=1 and the noise is large —
  do not over-read it — but nothing in this repo measures the picks beating the index
  gross.
- That 60/38/2 is optimal. It is a defensible starting point, not a tuned one.

**Where outperformance would have to come from.** Only the 38% satellite. To beat SPY
by 1pp/yr at the portfolio level, a 38% sleeve must beat SPY by ~2.6pp/yr **gross of
its own ~25%/month turnover** (≈0.9%/yr of cost on that sleeve), i.e. ~3.5pp/yr gross.
Current evidence that it can: none.

**The honest framing.** This design does not make money appear. It stops the book
paying 10%/yr for the privilege of a signal that has not been shown to be worth
anything, and it makes the baseline the market instead of zero. If the graph turns
out to have real edge, a 38% sleeve captures 38% of it. If it does not, the core
bounds the damage to the sleeve's costs.

---

## 6. Residual risk

1. **The core is a beta bet.** 60% SPY guarantees you take the market's drawdown.
   The old cash+SQQQ structure was a (bad) attempt to avoid that. If the operator's
   real objective is drawdown control rather than benchmark-relative return, this
   design is wrong for them and that needs saying now, not after backtest 2.
2. **The budget can trap capital.** Exhaust it mid-month and a genuine opportunity is
   refused. Mitigated by exempting protective exits; the asymmetry is deliberate and
   real.
3. **A bad signal stays bad.** A 38% satellite with negative gross alpha still loses.
   The core bounds the damage; it does not fix the signal.
4. **Live/backtest divergence.** `_residual_sleeve_deploy` had **no live-reachable
   call site** until 2026-08-02 — live ran the sleeve sell-only for weeks. Any new
   core entry point MUST be covered by
   `backend/tests/test_residual_sleeve_live_reachability.py`, which exists for exactly
   this failure and asserts live reachability per function.
5. **Regime detector is a single point of failure** for the bear de-risk, and
   `regime_blind_fallback = "chop"` means a data outage silently reads chop.
6. **`slot_min_notional_pct` is only half-wired** (§0.1). Do not lean on it.
7. **`nexus_portfolio_pct` silently stops binding** once a large core shares the NAV
   it is measured against (§7, item 6).

---

## 7. Implementation notes, file by file

1. **`backend/broker.py:2678 `_residual_sleeve_config``** — add the new keys to the
   returned dict. This is the only parse point; all three call sites go through it.
   Leave every existing key and default untouched.
2. **`backend/broker.py:3447 `_residual_sleeve_deploy``** — under
   `core_sleeve_enabled`, replace `idle = cash − park_floor_pct × nav` (≈:3735) with
   the target-weight delta from `core_sleeve.core_rebalance_order`. **Keep** the
   `_sleeve_circuit_tier() in ("hard","kill")` early return (:3535) — a core must
   still not be *added to* during a kill.
3. **`backend/broker.py:3118 `_residual_sleeve_release``** — under
   `core_sleeve_enabled`, replace the protective branch (`sell_qty = qty; frac = 1.0`,
   ≈:3418) with the bounded bear de-risk, and the `needed = release_cash_pct × nav −
   cash` refill (≈:3413) with the band + funding-request rule.
4. **`backend/core_sleeve.py`** (new, in this branch) — the pure sizing functions.
   No emulator, no broker, no I/O: numbers in, numbers out, so it is unit-testable
   without the `ast`-extraction trick `backend/tests/test_residual_sleeve.py` needs
   (broker.py is not import-safe — argparse at module level `SystemExit`s under
   pytest).
5. **`backend/strategies/graph_nexus_analysis.py:7459 `_sleeve_symbols``** — **no
   change needed** if the core reuses `residual_sleeve_symbol`. If a separate core
   symbol is ever introduced it MUST be added here, or the monitor, kill loop,
   fast-loser-cut and bear-book-trim will sell the core.
6. **`backend/strategies/graph_nexus_analysis.py:31124`** — `_tot_cap = portfolio_total
   × nexus_portfolio_pct` is measured against full NAV. With a 60% core, 0.95 never
   binds. Change to `min(nexus_portfolio_pct, 1 − core_target − cash_floor)` or lower
   the config value in tandem.
7. **`backend/broker.py:12938`** — `_open_positions` (cash-floor bypass) counts sleeve
   legs; `_rc_open` at :12945 correctly excludes them. Make the former consistent, or
   a core position inflates the count that releases the cash floor.
8. **Turnover ledger** — piggy-back on `_RESIDUAL_SLEEVE_PERSIST_KEY` (:2797) and
   `strategy_cache_persistence`, exactly as the sleeve state does. Backtest keeps
   per-run in-memory state; live persists. No new table.
9. **`backend/tests/test_residual_sleeve_live_reachability.py`** — extend
   `_LIVE_REACHABLE` with any new core entry point. See risk 4.
10. **doc-179 hygiene** — delete `max_single_position_pct: 0.15` (dead, logged red at
    every boot). Decide whether `slot_min_notional_pct: 1.5` should be wired to the
    main path or removed.

## 8. Config keys

**Reuse (readers verified above):** `residual_sleeve_enabled`,
`residual_sleeve_symbol`, `cash_reserve_floor_pct`, `single_position_max_pct`,
`max_positions[_bull|_chop|_bear]`, `rotation_min_delta`, `rotation_min_hold_days`,
`sell_enforcement_min_hold_days`, `allocation_execute_min_raw_score`,
`residual_sleeve_bear_min_dwell_days`, `regime_profiles`.

**Add — every one defaults to OFF or identity:**

| Key | Default | Meaning |
|---|---|---|
| `core_sleeve_enabled` | `false` | Master gate. False ⇒ deploy/release byte-identical to today. |
| `core_target_pct` | `0.60` | Base core weight. |
| `core_min_pct` / `core_max_pct` | `0.30` / `0.98` | Clamp on `1 − cash_floor − satellite`. |
| `core_rebalance_band_pct` | `0.05` | Absolute weight deviation before a non-funding rebalance. |
| `core_rebalance_min_days` | `5` | Trading days between non-funding rebalances. |
| `core_bear_scale` | `0.5` | Core multiplier on confirmed bear/crash; remainder to **cash**. |
| `core_bear_min_dwell_days` | `3` | Bear persistence before the de-risk fires. |
| `satellite_entry_rank_pct` | `0` (OFF) | Buy/hold spread — entry rank percentile. |
| `satellite_exit_rank_pct` | `100` (OFF) | Buy/hold spread — exit rank percentile. |
| `satellite_action_min_bars` | `0` (OFF) | Bars between acted-on entry/exit decisions. |
| `turnover_budget_monthly_pct` | `0` (OFF) | Rolling 21-session one-way notional cap, % of NAV. |

**Delete from doc-179:** `max_single_position_pct` (dead).
