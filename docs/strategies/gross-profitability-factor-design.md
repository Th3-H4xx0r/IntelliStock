# A point-in-time gross-profitability factor beside the Graph Nexus signal — design

Status: **design only**. Nothing here is wired. `backend/factor_profitability.py`
now exists (landed by a parallel worker while this was being written) and has
**no call sites**; this document specifies where its output should be consumed and
what must be true before it is. §2.4 reconciles the design against the module's
actual interface — there are three places they disagree and the module wins.

Companion to `docs/strategies/index-core-allocation-design.md`, which is now
partly shipped (see §1.6). That document answers "what does the book hold by
default". This one answers "if the satellite is going to exist, what decides its
membership".

One line: **the slow accounting factor decides what may be held; the fast news
signal decides how much of it, within bounds it cannot escape.**

---

## 0. The problem, restated in the numbers this repo has measured

| Measurement | Value | Source |
|---|---|---|
| SPY total return, bull window 2026-03-30..04-27 | **+13.23%** | index-core design §0.3 |
| Stock-pick return over the same window (decomposed) | **≈ +4.3%** | index-core design §0.3 |
| Live, 38 days | **−0.34%** vs SPY **+0.68%** | brief |
| One-way turnover, live | **~290%/month** | `graph_nexus_analysis.py:22831` |
| One-way execution cost, measured against SIP NBBO on 61 real fills | **23.2 bps** | `simulated_execution.py:107` |
| Implied cost drag | **8.07%/yr** | `graph_nexus_analysis.py:22832` |
| Stock-picking alpha clearing t=3 | none, in 4 independent tests | brief |

Beating SPY in a bull market requires more beta, real alpha, or leverage. The
index core (built, dark) supplies the beta and removes the cash drag; by
construction it gets you *to* the index, never past it. Any outperformance has to
come from a tilt with an edge that was established somewhere other than this
codebase, because this codebase cannot establish one (§5).

Gross profitability is the candidate because it is the rare factor that is
simultaneously (a) externally validated — Novy-Marx, "The Other Side of Value"
(JFE 2013): predictive power comparable to book-to-market, monotone deciles, a
premium that survives 3+ years of holding and is *not* confined to micro-caps;
(b) **~25%/year turnover**, which is inside any cost budget this book can afford;
and (c) computable **point-in-time** from dated statements, which the Neo4j graph
signal structurally cannot be (§5.1).

---

## 1. What the scoring and allocation path actually does

Read out of the source on 2026-08-03 at HEAD `ea5986a`. Every claim below names a
line. Where a config key is mentioned, its reader was located by grep before it
was relied on — this repo has shipped multiple keys with no readers and §1.7
records the current state of that.

### 1.1 `_finalize_scores` — discrete decision, no `raw_net_score`

`graph_nexus_analysis.py:20299`. Returns `{sym: {"score": -1|0|1, "reason": str,
"_forced_exit": bool, optional "sell_fraction": float}}`.

It does **not** emit `raw_net_score`. That is the first thing to get right, because
the name suggests otherwise and every downstream ranking uses `raw_net_score`.

Order of resolution per symbol:

1. **Held-position union** (`:20326`, "Z2.2"): currently-held names are unioned into
   `symbols_list` so a held name is re-scored every bar even after it drops out of
   discovery. Sleeve legs (`_sleeve_symbols(config)`, `:20339`) are excluded — this
   is the exemption the index core rides on.
2. **Direct LLM sentiment** (`:20427`) wins outright when non-zero.
3. **Graph propagation** (`:20436`) otherwise: `raw >= buy_threshold` → +1,
   `raw <= sell_threshold` → −1. Defaults in the module schema are ±0.15.
4. **Breakout boost** (`:20450`, `breakout_score_boost_enabled`, default true) can
   promote a neutral name to +1 with no news at all.
5. **`_evaluate_position_risk`** (`:20465`) then **overrides** for held names:
   fast-loser cut, trailing stop, hold-limit, profit-take tiers, catastrophic stop.
   This is where the short-hold mass comes from, and it never consults
   `rotation_min_hold_days`.
6. **Pending scheduled trades** (`:20486`) are folded in by confidence-weighted vote.

`_FORCED_EXIT_TAGS` (`:19558`) and `_RISK_EXIT_TAGS` (`:19541`) are the single
definitions of "this is a protective exit". Anything new in the filter chain must
refuse to veto them.

### 1.2 `raw_net` → `final_score` — this happens *after* `_finalize_scores`

`_apply_ml_and_overlay_to_scores` (`:22174`, called at `:27370`) is what produces
`raw_net_score`. It re-decides `final_score` (`:23504`: a held name with
`raw_net < add_buy_min_raw_net` or weak ML is demoted to 0) and writes the enriched
doc at `:23541`:

```python
enriched_scores[sym] = {
    **base, "score": final_score, "confidence": ...,
    "raw_net_score": round(raw_net, 4),      # <- created HERE, not in _finalize_scores
    "base_signal": ..., "ml": {...}, "overlay": {...},
    "n_paths": ..., "quality_metadata": {...},
}
```

`raw_net_score` is not a clean continuous variable. Two structural point masses
sit in it, and they matter for the choice of combination rule in §2:

- Propagation aggregation hard-caps at **+1.0**, so multi-path conviction names all
  collide at the ceiling (`_conviction_allocation_schedule` docstring, `:10260`).
- The momentum lane **floors** its picks at 1.50 (`_eta_e_floored = max(score,
  1.50)`, `:21447`) and adds a ≤0.20 differentiator on top.

So the distribution has a spike at 1.0, a spike at ~1.5–1.7, and a continuum
elsewhere. Standardising it (a z-score) is not meaningful. Ranking it is.

### 1.3 The filter chain — demote-only, in a fixed order

At `:27396`–`:27408`, each function takes `scores` and returns it, only ever
demoting a +1 to 0 (or suppressing a −1):

```
_apply_rank_band_gate            # :22810  buy/hold spread, default OFF
_apply_portfolio_circuit_breaker # :22724
_apply_buy_price_floor           # :22934
_apply_quality_filter            # :22999  market cap / volume / negative-raw / entry extension
_apply_sector_concentration_limit# :23491
_apply_portfolio_drawdown_halt
```

`_apply_rank_band_gate` is the shape any new cross-sectional gate must copy. It:

- ranks by `_rotation_effective_score` (`:11375`) =
  `raw_net_score + ml_edge · ml_confidence · rotation_ml_weight` (0.20 default);
- **dedupes** `symbols_list` first (`:22863`) because repeated tickers inflate the
  percentile denominator and silently widen the entry band;
- sorts with the ticker as an explicit tiebreak (`:22878`) so a replayed bar cannot
  buy a different name on the decile boundary;
- **exempts** `_ALL_ETF_TICKERS | _sleeve_symbols(config)` (`:22857`);
- **never** vetoes `_forced_exit` or a `_RISK_EXIT_TAGS` reason (`:22912`);
- fails **open, loudly** if the position read throws (`:22893`).

### 1.4 The buy budget

`_compute_available_buy_budget` (`:11130`) returns
`min(cash_after_floor, ramp_room)` plus a metadata dict:

- `cash_after_sells = cash + _estimate_sell_proceeds(...)`
- `cash_floor = initial_value × _get_scaled_cash_reserve_floor_pct(config, initial_value)`
- `ramp_room = initial_value × ramp_cap_pct − post_sell_positions_value`, where
  `ramp_cap_pct` comes from `_get_deployment_ramp_caps` indexed by
  `_get_deployment_ramp_bar_index` (`:11113`) — which counts **bars, not days**. At
  900s granularity a 3-element ramp is spent in 45 minutes. It is a cold-start
  throttle, not an allocation control.

Then, in `run_once`: `_compute_releasable_cash_reserve` (`:11203`) can add back up
to the whole floor; `_compute_macro_risk_scale` haircuts; `buy_budget_floor_pct`
floors. Finally the budget is split stock/ETF by `nexus_portfolio_pct` /
`etf_portfolio_pct` (`:27730`/`:27731`).

### 1.5 From budget to `nexus_position_sizes`

`_plan_executable_stock_buy_slate` (`:10361`):

1. Drop `raw_score < allocation_execute_min_raw_score` (0.35) to `deferred`.
2. Split `strong` (≥ `allocation_top2_min_raw_score`, 0.50) from `other`; sort each
   by `−raw_net_score`, then priority flags, then ticker.
3. `slot_cap = _get_scaled_max_new_stock_buys` (`:8858`; 6/8/12 by account size,
   `allocation_max_new_stock_buys` overrides).
4. Shrink the count until every slot clears `min_position_size` (feasibility loop,
   `:10467`).
5. Per item: schedule by `allocation_profile` — `balanced` (equal) or `conviction`
   (`_conviction_allocation_schedule`, `:10253`, ∝ `raw_net_score` with an n_paths
   bonus scaled by `edge_type_corroboration_weight`) — then clip with
   `_clip_to_single_position_cap`.

Then, still inside `run_once`:

- `_apply_vol_adjustment` (`:29629`, `vol_adjust_sizing_enabled` default **true**)
  multiplies each `buy_cash` by `clamp(target_atr/atr, min_mult, max_mult)` and
  **renormalises so the total budget is invariant**. This is the exact mechanism
  the factor tilt should copy (§3.3).
- `_enforce_sector_portfolio_cap` (`:29641`).
- Funded items become `nexus_position_sizes[ticker] = {"buy_cash", "high_conviction",
  "asset_class", "raw_net_score", "signal_source", …}` (`:29677`), returned as
  `out["_nexus_position_sizes"]` (`:24060`).

Broker side: popped at `broker.py:5339`, re-read at `:12361`–`:12382` as
`cash_per_trade`. The buy path then applies, in order:

1. **Satellite headroom trim** — `_core_sleeve_satellite_headroom` (`:3033`),
   consumed at `:13734`. Returns *dollars of room*, not a boolean; the caller
   trims `cash_to_use` down to it, or skips entirely below
   `_CORE_MIN_SATELLITE_TRIM_USD = 25.0`.
2. Sleeve bear-symbol reservation (`:13752`).
3. **Turnover budget block** (`:13766`) — new buys only.
4. Regime position cap (`:13788`).
5. Cash-floor bypass logic (`:13822`), which since `:13841` excludes sleeve legs
   from the open-position count when the core is armed.
6. `BROKER_MAX_SINGLE_POSITION_PCT` (env, **fraction**, default 0.15).

### 1.6 The core sleeve is no longer dark code — it is dark *config*

The index-core doc says "no call sites". That is stale. As of `5470a05`…`ea5986a`
the module is fully wired:

| Function | Line | Role |
|---|---|---|
| `_core_sleeve_cfg` | `broker.py:2852` | single parse point; returns `None` when off |
| `_core_sleeve_decide` | `:3209` | one decision fn, both modes |
| `_core_sleeve_satellite_headroom` | `:3033` | dollars of satellite room |
| `_core_sleeve_block_new_satellite` | `:3088` | standing-weight refusal |
| `_turnover_ledger_record` | `:2988` | books one-way notional, **unconditionally** |
| `_core_turnover_state` | `:3133` | `(blocked, used_fraction)` |
| release / deploy hooks | `:3926`, `:4146` | sell side at cycle start, buy side at cycle end |

Two properties matter for this design:

- **`core_sleeve_enabled` is absent from doc-179**, so the whole thing is inert in
  live. It also **fails closed** if `residual_sleeve_enabled` is false (`:2878`),
  because the core's six sell-exemptions all come from `_sleeve_symbols`, which
  returns an empty set without that flag.
- **`_turnover_ledger_record` runs unconditionally** (`:2996` docstring). So the
  book's *true realised turnover* is already being recorded on the legacy path.
  That is free measurement, and §5 leans on it.

### 1.7 Config keys: verified readers, and the dead ones

Verified today by grep, not assumed:

| Key | Reader | Verdict |
|---|---|---|
| `single_position_max_pct` | `_clip_to_single_position_cap:10308`, 4 call sites (`:10506`, `:10717`, `:14075`, `:30844`) | **Real.** PERCENT points. |
| `max_single_position_pct` | none; listed in `broker.py:_DEAD_STRATEGY_CONFIG_KEYS:4784` | **DEAD**, logs red every boot. Delete from doc-179. |
| `slot_min_notional_pct` | `_slot_min_notional:9185`, called ONLY at `:30851` and `:31035` | **Still half-dead** — both call sites are inside the backfill-queue drain. Main-signal entries are not gated by it. Do not lean on it for a satellite minimum. |
| `rank_band_enabled/_entry_pct/_exit_pct` | `_apply_rank_band_gate:22810` | Real, default OFF. |
| `core_sleeve_enabled` and the `core_*` family | `_core_sleeve_cfg:2852` → `core_sleeve.core_sleeve_config:104` | Real, default OFF. |
| `core_respects_turnover_budget` | `broker.py:3269` | Real, default false. |
| `turnover_budget_monthly_pct` | `_core_turnover_state:3133` → `core_sleeve.turnover_budget_state:314` | **Real but COUPLED.** See below. |
| `vol_adjust_*` | `_apply_vol_adjustment` | Real, default ON. |
| `edge_type_corroboration_weight` | `_conviction_allocation_schedule:10283` | Real. |

**The coupling trap, live in the tree today.** `_core_turnover_state` returns
`(False, 0.0)` whenever `_core_sleeve_cfg(...) is None` (`:3146`). `_core_sleeve_cfg`
returns `None` unless `core_sleeve_enabled` **and** `residual_sleeve_enabled` are
both true. Therefore **`turnover_budget_monthly_pct` does nothing at all unless the
index core is armed.** An operator who sets only the budget — the single most
obvious cost-discipline lever in the file — gets silence. This is not a dead key,
it is a *conditionally* dead key, which is worse because grep finds a reader. If
cost discipline is wanted without the core, that coupling has to be broken first;
this design assumes the core is on and says so explicitly rather than depending on
a lever that will not fire.

---

## 2. How the two signals combine

### 2.1 The rule

Let `U` be the deduped, non-ETF, non-sleeve scored universe for the bar — the same
set `_apply_rank_band_gate` builds at `:22858`–`:22871`.

```
r_news(i)  = percentile rank of _rotation_effective_score(scores[i]) in U     ∈ [0,1], 1 = best
r_gp(i)    = rank_gross_profitability(U, as_of)[i]                            ∈ [0,1] or None
             GP(i) = gross profit / total assets, from the most recent fiscal period
                     whose availability date (period_end + lag_days) ≤ as_of
r_gp(i)    = factor_missing_rank (0.5) when the module returns None
r(i)       = (1 − factor_weight) · r_news(i)  +  factor_weight · r_gp(i)
             with factor_weight = 0.5, FIXED
```

That is a rank average, 1/N over two signals, and nothing is estimated.

### 2.2 Why rank-average and not a fitted weight

**Because we have no data to fit on, and the parameter we would be fitting is the
most error-sensitive one available.**

- DeMiguel, Garlappi & Uppal (RFS 2009) test 14 optimisation models across seven
  datasets: none beats 1/N consistently out-of-sample. For 25 assets the estimation
  window required before mean-variance beats naive diversification is on the order
  of 3,000 months. This project has **26 days** of live history and a backtest that
  cannot measure selection alpha at all (§5.1).
- Chopra & Ziemba (1993): errors in estimated **means** are roughly **11×** as
  costly as errors in variances at a risk tolerance of 50, and ~21× at RT 25. A
  fitted blend weight between two return signals *is* an estimated mean. It is
  precisely the parameter you should refuse to estimate from a short sample.
- The alternative — an IC-weighted or regression blend — would be fit on ~0
  independent observations, and its standard error would exceed its point estimate.
  Setting it to 0.5 by prior is not laziness; it is the only defensible choice when
  the estimator's variance dominates.

**Rank, not z-score, for a code-specific reason.** §1.2 documents two hard point
masses in `raw_net_score` (the +1.0 propagation cap and the 1.50 momentum floor).
A z-score of a distribution with atoms at 1.0 and ~1.5 standardises nothing;
whichever lane happens to be loud that bar dominates the standardised scale.
Percentile ranks are invariant to any monotone artefact, including both of those,
and to the fact that the score distribution shifts with news volume (which is the
same reason `_apply_rank_band_gate` exists at all).

**Missing data is a rank of 0.5, not a drop and not a zero.**

- Dropping no-data names makes yfinance coverage a *selection rule* on the
  portfolio, and coverage correlates with size and listing venue. That is a factor
  bet nobody chose.
- Zeroing them makes "we could not fetch a statement" identical to "this is the
  least profitable company in the universe", which is a lie the sizing rule will
  act on.
- At `r_gp = 0.5`, `r(i) = 0.5·r_news(i) + 0.25`, a strictly monotone transform of
  the news rank. A no-data name is therefore ranked *relative to other no-data
  names exactly as it is today*, and sits at the median of the factor axis. That is
  the honest encoding of ignorance.
- `factor_min_coverage_pct` (0.60) makes the factor **disable itself for the bar,
  loudly**, when coverage collapses — so the degenerate case is a logged no-op
  rather than a silent one.

### 2.3 Why combining *reduces* turnover rather than adding to it

Frazzini, Israel & Moskowitz, "Trading Costs of Asset Pricing Anomalies", measure
a 50/50 value+momentum combination at **~79%/month** one-way against momentum alone
at **~127%/month**, and find it has the highest break-even capacity of any strategy
in their sample. The mechanism is not diversification of returns; it is
**cancellation of trades**. When one leg wants to buy a name the other leg is
already long, the trade does not happen twice — and when one leg wants to buy a
name the other leg ranks poorly, the combined rank never clears the entry cut and
the trade does not happen at all.

GP and news sentiment are close to orthogonal by construction: one is a quarterly
accounting ratio with a multi-year horizon, the other is a text signal whose alpha
is priced in 1–4 days. The offsetting therefore applies here in the strong form:
**most of the factor's contribution to this book is trades that never happen.**

The caveat is in §7.5 — GP is mechanically higher for asset-light software, and the
graph's trend routing already concentrates in technology. If both legs load on the
same sector the cancellation is weaker than the literature implies. §5.3 makes that
a counted quantity rather than an assumption.

### 2.4 Reconciliation with the shipped `factor_profitability.py`

The module landed on 2026-08-03 with no call sites. Its public surface:

```python
gross_profitability(symbol, as_of, *, lag_days=120, max_period_age_days=730,
                    excluded_sectors=None, allow_cogs_fallback=True,
                    fetcher=None, cache=None) -> float | None
gross_profitability_detail(...) -> dict   # + reason, period_end, available_at
gross_profitability_batch(symbols, as_of, **kw) -> dict[str, float | None]
cross_sectional_ranks(scores, *, min_names=5) -> dict[str, float | None]
rank_gross_profitability(symbols, as_of, **kw) -> dict[str, float | None]
NEUTRAL_PERCENTILE = 0.5
```

Three disagreements with the design as first drafted, all resolved in the
module's favour:

1. **Missing data returns `None`, not `0.5`.** `cross_sectional_ranks` maps an
   unavailable name to `None` and its docstring explicitly forbids
   `ranks.get(sym, 0.0)` — 0.0 is the strongest possible negative tilt, so that
   default would turn "no fundamentals" into "short it". The module exports
   `NEUTRAL_PERCENTILE` for callers that structurally need a number. **The
   consumer therefore owns the substitution**: `_apply_factor_gate` maps
   `None → factor_missing_rank` (0.5) at the point of use, and counts how often it
   does so (§5.3e). This is the better split — the producer abstains, the consumer
   decides what abstention means.
2. **Availability is `period_end + lag_days` (120), not a filing date.** The
   module's own docstring says a real SEC filing date would be better. The
   consequence is that the factor is *conservatively* late rather than exact: a
   10-K filed 60 days after year-end is invisible for another 60. That is the safe
   direction for lookahead and the wrong direction for freshness, and it is
   another reason the factor is slow (which §4.1's turnover budget wants).
3. **Staleness is `max_period_age_days = 730`, not 120.** My draft conflated the
   reporting lag with the staleness cutoff. Two different parameters: 120 days is
   how long after period-end the number becomes *visible*; 730 days is how old the
   newest visible period may be before the name is refused outright. The config
   table in §8 is corrected to expose both.

The module also **excludes by construction** what GP/A cannot describe:
`EXCLUDED_QUOTE_TYPES` (funds, ETFs, indices) and `DEFAULT_EXCLUDED_SECTORS`
(financials — for a bank, total assets *are* the loan book, so GP/A measures
leverage). Those are deliberate abstentions, not coverage failures, and §5.2
now separates the two.

`min_names = 5`: below five ranked names the module abstains entirely rather than
handing a 2-name book a 0.0/1.0 pair that looks like a decile sort. At the
satellite sizes in §4.5 (8–15 names) this is comfortably clear, but a bear regime
with `max_positions_bear = 2` would trip it — in which case the factor correctly
returns nothing and the gate becomes a no-op.

---

## 3. Which signal decides what to hold, and which decides how much

### 3.1 The split

| Decision | Owner | Mechanism |
|---|---|---|
| **Eligible universe** (may this name ever be bought?) | **Factor** | `r_gp ≥ factor_min_entry_rank` (0.40) for a NEW entry |
| **Continued eligibility** (may it stay?) | **Factor**, loosely | exit only when `r_gp < factor_min_hold_rank` (0.20) — a 20pp buffer |
| **Which eligible names get bought this bar** | **Combined rank** | existing `rank_band_entry_pct` band, ranked on `r(i)` |
| **Base weight** | **Combined rank** | existing `allocation_profile` schedule, unchanged |
| **Tilt on the base weight** | **News**, bounded | multiplicative, clamped `[0.7, 1.4]`, renormalised |
| **When a signal exit fires** | **News**, subject to the band | unchanged; `_RISK_EXIT_TAGS` always win |

### 3.2 Why the slow signal owns "what"

**A signal whose information half-life is 1–4 days cannot justify a holding.**
Ke, Kelly & Xiu ("Predicting Returns with Text Data") find the return to a
news-text signal is realised within roughly one to two days and substantially
complete inside a week. If the news signal decides *membership*, then every decay
of that signal is an exit — and set-membership churn, not weighting, is what
produces 290%/month. You cannot cost-control a strategy whose universe is
re-drawn every bar.

Novy-Marx's GP portfolios turn over ~25%/year and the premium survives 3+ years of
holding. A signal with a multi-year horizon *can* define a holding, and a holding
is the only thing a $6k book can afford at 23.2 bps.

There is a second, repo-specific reason. The graph's candidate set is *discovered*
each bar — `max_discovered_stocks = 90`, momentum re-discovery, propagation
expansion, backfill queue. It has no stable universe at all. Layering a slow gate
over it is the only way the book acquires a persistent eligible set, and a
persistent eligible set is a precondition for turnover ever being low.

### 3.3 The tilt, concretely

Applied to the funded slate at `:29629`, **immediately after `_apply_vol_adjustment`**
and with the same contract (multiply, then renormalise so the total is invariant):

```
m(i) = clamp(1 + factor_tilt_gain · (r(i) − mean_r),  factor_tilt_min_mult, factor_tilt_max_mult)
buy_cash(i) ← buy_cash(i) · m(i)
then rescale all buy_cash so Σ buy_cash is unchanged
```

Order matters and is deliberate: vol-adjust is a *risk* normalisation, the factor
tilt is an *alpha* tilt. Normalise risk first, then tilt, so the tilt expresses a
view about expected return rather than accidentally undoing a volatility
adjustment. `_clip_to_single_position_cap` still runs afterwards at `:10506`, so
the tilt can never breach `single_position_max_pct`.

For **held** positions the tilt is a rebalance, so it is band-and-cadence gated
(§4.2) rather than continuous.

### 3.4 The alternatives, and why they are rejected

**Alternative A — news picks, factor only sizes ("quality overlay").**
Rejected on three counts.

1. *It does not touch the problem.* Turnover is driven by set-membership churn.
   A weighting overlay on a set that is still re-drawn on news changes essentially
   none of the 290%/month. It would buy the same names on the same days in slightly
   different sizes.
2. *It puts the factor where errors are cheapest and gives it no say where they are
   dearest.* Chopra-Ziemba: errors in means cost ~11× errors in variances. A sizing
   overlay is a variance-side intervention. The factor's whole claim is about the
   cross-section of *expected returns* — which names, not what weights.
3. *It cannot fail loudly.* A size overlay that is wrong looks like noise for years.
   An eligibility gate that is wrong shows up in the first mechanical test as a
   coverage breach or a rank-stability breach (§5.2), before any money moves.

**Alternative B — factor decides everything, delete the news lane.**
Rejected. The honest read of the evidence is that the graph signal's measured
gross alpha is *approximately zero*, not *demonstrably negative*: n=1 window,
large replicate noise, and a decomposition rather than a direct measurement. Zero
is not a mandate for deletion. A 15-name GP portfolio on $6k at 23.2 bps is also
a strictly worse version of buying an ETF, so the deletion would not even be a
clean improvement. The news lane earns a bounded, budgeted role; it does not earn
the right to define the universe.

**Alternative C — put the factor into `raw_net_score` itself, upstream.**
Rejected for a mechanical reason. `raw_net_score` is read by the rotation lane,
the sector cap, the backfill queue, the anchor reinforcement, the cash-reserve
release and the conviction allocation schedule — a dozen consumers with different
thresholds calibrated on the current distribution (`allocation_execute_min_raw_score
= 0.35`, `allocation_top2_min_raw_score = 0.50`, `cash_reserve_release_min_score =
0.50`, `rotation_min_delta`, …). Shifting the distribution under all of them at
once is untestable. The factor therefore enters at exactly **two** sites: one gate
and one tilt, both individually flagged.

---

## 4. Turnover budget: the arithmetic

Structure assumed: index core ON at 60%, satellite ≤38%, cash 2%. All figures are
**one-way** notional as a fraction of NAV, and all costs use the **measured**
23.2 bps, not the older 30.3 bps model that `simulated_execution.py:107` replaced.

Target: **≤50%/month** — the Novy-Marx & Velikov (RFS 2016) line above which
anomaly spreads rarely survive their own trading costs. That is 6×/yr, or
`6.0 × 23.2 bps = 1.39%/yr` of drag, against the measured **8.07%/yr** today.

### 4.1 What the factor's own rebalance costs

GP changes only when a statement lands: at most 4 observations per name per year.
The factor lane is therefore put on a fixed **quarterly** cadence
(`factor_rebalance_min_days = 63` trading days), so it can trigger at most four
membership reviews a year regardless of what the data does intra-quarter.

Per review, let `f` be the fraction of *satellite value* replaced. Each replacement
is two legs (sell the exit, buy the entry):

```
factor turnover/yr = 4 reviews × 2 legs × f × satellite_share
```

| `f` per review | satellite 38% | one-way %/yr | one-way %/month |
|---|---|---|---|
| Novy-Marx's ~25%/yr, scaled to a 38% sleeve | — | 9.5 | **0.79** |
| Design budget, `f = 0.10` (conservative) | 0.38 | 30.4 | **2.53** |
| Stress, `f = 0.20` | 0.38 | 60.8 | 5.07 |

The design budgets **2.5%/month**, roughly 3× the literature figure, because the
20pp gap between `factor_min_entry_rank` (0.40) and `factor_min_hold_rank` (0.20)
is the only thing suppressing boundary churn and it has not been measured on *this*
universe. §5.2 test 0 measures it before anything ships.

### 4.2 What the news tilt costs

The tilt is a weight change on names that are already held and already eligible.
It is bounded structurally rather than forecast:

- On a **new entry** the tilt costs nothing extra — it changes the size of a trade
  that was happening anyway.
- On a **held** position it fires at most once per `factor_tilt_min_days = 5`
  trading days (≤4.2 windows/month), only when the implied change exceeds
  `factor_tilt_band_position_pct = 0.20` of that position's value, and never more
  than `factor_tilt_max_nav_pct = 0.02` of NAV in total per window.

Ceiling: `4.2 × 2% = 8.4%/month`. Expected realised is well below, because the
band suppresses most windows — this is the same band+cadence shape
`core_rebalance_order` already uses, and the reason that function collapses the
sleeve's turnover.

### 4.3 What is left for the news entry/exit lane

Three things already in the tree shrink it, and one caps it:

| Effect | Multiplier | Grounding |
|---|---|---|
| Satellite falls from a measured 66.6% mean to ≤38% of NAV | ×0.57 | arithmetic; index-core §0.3 measured the 66.6% |
| `rank_band_enabled` at entry 10% / exit 50% | ×0.4 | Chen & Velikov size the buy/hold spread's effect on the highest-turnover quartile at roughly a 3–5× cut, surrendering ~11% of gross signal. ×0.4 is the conservative end. |
| Factor eligibility gate, `factor_min_entry_rank = 0.40` | ×0.8 | removes 40% of the universe from entry, and the removal *persists for a quarter* — unlike a threshold block, which lets a name re-enter next bar. Entries and exits are roughly half each, so ~×0.6 on entries and ×1.0 on exits. |

`290 × 0.57 × 0.4 × 0.8 ≈ 53%/month` — still above target. **So the forecast is not
the control.** The hard budget is.

### 4.4 The budget table

| Component | one-way %/month | Enforced by |
|---|---|---|
| Index core rebalance | 3.0 | `core_rebalance_band_pct = 0.05` + `core_rebalance_min_days = 5` (shipped, `core_sleeve.py:289`–`296`) |
| Factor quarterly rebalance | 2.5 | `factor_rebalance_min_days = 63` + the 20pp entry/hold gap |
| News tilt within the sleeve | ≤8.4 (expect ~4) | `factor_tilt_min_days = 5`, `factor_tilt_band_position_pct = 0.20`, `factor_tilt_max_nav_pct = 0.02` |
| News entry/exit | **residual, ≤36.1** | `turnover_budget_monthly_pct = 0.50` at `broker.py:13766` |
| **Total** | **≤50.0** | |

Cost: `0.50 × 12 × 23.2 bps = 1.39%/yr`, against a measured **8.07%/yr**.
**Saving ≈ +6.7%/yr**, and that number is *counted arithmetic on a measured cost*,
not a forecast of alpha. It is also larger than the factor's own expected
contribution by an order of magnitude (§6).

Three properties of the enforcement worth stating plainly:

1. **The news row is a residual, not a prediction.** The design does not claim the
   news lane's turnover falls to 36%. It caps it there and accepts that the cap
   will bind and refuse trades. That is acceptable precisely because four
   independent tests found no alpha clearing t=3 in that lane.
2. **Sells are never blocked.** `broker.py:13384`: `decision == -1` is reduce-only
   by construction, so every stop, DD-circuit exit and bear de-risk runs at full
   budget. A budget that traps you in a loser costs more than it saves.
3. **The factor lane is governed at both the read and the write.** The core is
   exempted at both (`_turnover_is_governed`, `:2958`) for a documented reason:
   exempting a lane at the read while still writing its notional to the ledger
   starved the satellite on tick 1 of bt 152918. The factor lane is discretionary,
   so it stays fully governed on both sides. Do **not** add a write-side exemption
   for it; `factor_rebalance_exempt_from_budget` is deliberately not proposed.

### 4.5 The $6k sizing reality

At 38% satellite on a $6,000 book the satellite is **$2,280**.

| Names | $/position | 20% tilt | Clears `factor_tilt_min_order_usd = 25`? |
|---|---|---|---|
| 8 | 285 | 57 | yes |
| 12 | 190 | 38 | yes |
| 15 | 152 | 30 | yes, barely |
| 20 | 114 | 23 | **no — the tilt never fires** |

`min_position_size = 100` is cleared down to 22 names, but the *tilt mechanism*
runs out of resolution at about 15. **At $6k the practical satellite is 8–15
names.** Twenty names is below this design's own arithmetic resolution, and the
tilt would silently no-op — the exact class of failure §1.7 is about. If 20 names
is a requirement, either the satellite share has to rise (which reverses the
turnover argument) or the account has to grow.

---

## 5. What would falsify this

### 5.1 What our harness cannot establish, and why

**Alpha. Not "has not yet"; cannot.** Four independent obstructions:

1. **Graph lookahead.** The Neo4j graph is read live. A historical decision sees
   post-decision news unless the run is `pit_mode="strict"`, which needs a
   finalised bundle per session. `interactive_utils.py:5580` defaults `pit_mode`
   to `"research"`, which constructs a `PointInTimeContext` with
   `strict=False, is_live=False` — provenance `legacy_unverified`, and
   `uses_live_sources == True` (`point_in_time_data.py:291`). Under that context
   `_pit_use_legacy_sources` (`graph_nexus_analysis.py:15116`) returns True and
   every dated consumer falls back to present-day state.
2. **Graph coverage.** ~36% of delisted names are absent from Neo4j.
3. **Survivorship in the ticker set.** yfinance serves currently-listed tickers.
   A strict bundle built today has correctly-dated *values* but a survivor-biased
   *universe*. Nothing available to this project fixes that.
4. **Replicate noise.** The brief's floor is **0.43pp** on a 28-day same-config
   replicate. The index-core doc observed far worse on doc-179 at granularity 900
   over 2026-03-30..04-27: `+0.58, +0.78, +1.38, +3.32, +7.05, +10.45%`. Treat
   0.43pp as a floor on the floor, and any return difference under **~3pp** as
   measuring nothing.

**Consequence for the factor specifically, and it is subtler than it looks.**
`factor_profitability.py` does *not* read the PIT `fundamentals` snapshot. It
fetches from yfinance through its own cache and enforces point-in-time by an
**availability rule** — a fiscal period is invisible until `period_end + lag_days`
(120). That is a genuinely different guarantee, and it splits cleanly:

- **In LIVE, the module's rule is sufficient and is the right design.** `as_of` is
  today, the statements fetched are the current ones, and the lag rule only ever
  makes the factor *later* than reality. There is no lookahead available.
- **In a BACKTEST, the module's rule is necessary but not sufficient.** It prevents
  lookahead in *time* — a 2025-06 decision cannot see a period that became
  available in 2025-09. It does **not** prevent lookahead in *revision*: yfinance
  serves statements as they read today, so a period fetched now reflects any later
  restatement of it. The module's own docstring says this plainly. Only a
  vintage-aware source (Compustat PIT, or reconstructing from the original EDGAR
  filing rather than the latest) closes it.

So `factor_require_strict_pit = true` should mean: **in a non-live context, refuse
to run unless the values came from the PIT `fundamentals` snapshot** (§9.2), rather
than from a live yfinance fetch. Under the default `pit_mode="research"` that means
the factor disables itself and logs red — the factor is OFF in every casual
backtest, and only strict runs with a finalised bundle exercise it. That is a real
operational cost and it belongs at the top of the plan rather than being discovered
halfway through. The alternative — letting the module's lag rule stand in for a
snapshot in backtest — is the failure this whole project has already been burned by
once with the graph, and the restatement channel makes it a *quiet* failure rather
than a loud one.

### 5.2 Test 0 — offline, zero backtests, zero dollars

The single most important test, and it needs no engine. **Part of it is already
done**: the module's docstring reports a measured 79-name run against
alpaca-main's own book (every symbol with a real `BotTradeDecisions` row plus a
seeded draw from the 1,308 tickers `GraphNexusDiscoveredStocks` has surfaced).

| Outcome | n | Reading |
|---|---|---|
| computed a GP/A | **57** | 72.2% of the raw sample |
| `excluded_fund` | 10 | **deliberate abstention** — funds have AUM, not gross profit |
| `excluded_sector` | 4 | **deliberate abstention** — financials (BX, FHB, HIG, MARA) |
| `missing_gross_profit` | 4 | genuine gap — clinical-stage biotech with no COGS line |
| `no_data` | 3 | genuine gap |
| `fetch_failed` | 1 | Yahoo 404 on a delisted ticker |

The two numbers must not be conflated. **Raw coverage is 72.2%.** Coverage of the
names GP/A is *supposed* to describe — operating, non-financial companies — is
**57/65 = 87.7%**. The 14 exclusions are the factor working correctly. So the
gate in §8 is set on the eligible-universe figure, and `factor_min_coverage_pct`
must be evaluated against the post-exclusion denominator or it will read a
correctly-abstaining factor as a broken one.

GP/A spread across the 57: **−0.230 / 0.197 / 0.586** (min / median / max), with
AAPL at 0.5434 on FY2025-09-30, verified directly against yfinance. That is a real
cross-section with real dispersion — not a degenerate one where every name ranks
the same.

**Still outstanding, and it is the criterion that decides the design:**

| Check | Pass | Why it decides the design |
|---|---|---|
| **Rank persistence**: quarter-over-quarter Spearman ρ of `r_gp` over the available periods | **ρ ≥ 0.8** | the entire turnover budget (§4.1) rests on GP being slow. Novy-Marx's result is large-cap; this is the test that it transfers to a small/mid-cap discovery universe. Nothing measured so far speaks to it. |
| **Depth**: fiscal periods available per name | **≥ 4** | the module reports ~4 annual periods from yfinance. Four points is enough for one persistence estimate and not enough for a historical sort. Confirm before designing any study that needs more. |

**Kill conditions.** ρ < 0.6 → GP is not slow on this universe → the "slow signal
owns what to hold" split (§3.2) collapses → do not ship. Post-exclusion coverage
< 60% → inert.

This test is survivorship-contaminated for *returns*, which is why it measures no
returns. Coverage, dispersion, rank persistence and depth are properties of the
data, not of an equity curve.

### 5.3 Test 1 — one backtest, mechanical properties only

Window 2026-03-30..04-27, granularity 900, `pit_mode="strict"`, core ON (60/38/2),
factor ON, rank band ON (10/33), turnover budget ON at 50%/mo. Every criterion is
**counted**, so none has a noise floor:

| # | Criterion | Pass | Source |
|---|---|---|---|
| a | one-way notional over 20 sessions | **≤ 48% of NAV** | `Σ|trades[].total|`, cross-checked against `_turnover_ledger` rows |
| b | realised cost | **≤ 0.25% of book** | `Σ(fees + slippage_cost + spread_cost)`; measured baselines 1.00% (bt 987397) / 1.35% (bt 852704) |
| c | mean core weight / mean satellite weight | **55–65% / 30–40%** | `portfolio_value_history[].positions_snapshot × prices` |
| d | factor-lane trade count | **≤ 2** in 20 sessions | quarterly cadence must fire at most once, plus the initial build |
| e | **coverage neutrality**: fraction of funded entries that carried `factor_missing_rank` | **≤ 50%** | if most funded names had no factor value, the run proves nothing about the factor |
| f | realised sector distribution of the satellite | recorded, not gated | tests §2.3's caveat: if GP and the graph both load on technology, the trade-cancellation argument is weaker than claimed |

**Kill:** (a) > 100% of NAV, or (e) > 50%.

### 5.4 Test 2 — one backtest, A/B on counted quantities

Same window, same everything, `factor_profitability_enabled` false then true,
**sequentially** (config is read live; this project's own A/B protocol requires
sequential runs) and with a distinct `history_scope_salt` per arm, because shared
mutable Nexus state has invalidated every A/B this project has run without it.

Read only:

- **Δ turnover** — counted, no noise floor.
- **Δ realised cost** — counted.
- **Δ held-set Jaccard** over the window — counted. This is the actual measurement
  of "did the factor change what we hold". **Jaccard ≥ 0.9 → the factor is inert
  and the design is decorative. Jaccard ≤ 0.3 → it is effectively a different
  strategy and §4's turnover derivation has to be redone.**
- **Δ return** — recorded and **explicitly not interpreted** unless |Δ| > 3pp, and
  even then only as a flag to re-run, never as evidence of an edge.

### 5.5 The falsification list, written before running

1. Rank persistence ρ < 0.6 on this universe → do not ship.
2. Post-exclusion coverage < 60% → the factor is `factor_missing_rank` in disguise
   → do not ship. (Measured 87.7% on 2026-08-03; the raw 72.2% figure is the wrong
   denominator — see §5.2.)
3. Realised one-way turnover after the change > 100%/month → the cap is not binding
   where it must, and the cost saving — the design's only quantified benefit —
   does not exist.
4. Held-set Jaccard ≥ 0.9 between arms → inert.
5. Any run with `pit_mode != "strict"` whose *return* is cited as evidence → that
   is a process failure, not a data failure, and it invalidates the conclusion
   regardless of the number.

### 5.6 What is carrying the evidential load, and it is not us

Novy-Marx (JFE 2013): gross profits-to-assets, US 1963–2010, monotone deciles,
t on the long-short spread in the 2.5–4.5 range depending on specification;
survives controls for book-to-market, size and momentum; **not** confined to
micro-caps, which is unusual among anomalies; and negatively correlated with
value, so a GP+value combination has lower volatility than either leg.

That is the *only* evidence for the edge. Our tests establish that we implemented
a slow, cheap, low-turnover, correctly-dated version of it and did not blow up the
cost budget doing so. They establish nothing about whether it works here. Saying
that explicitly is the point of §5.

---

## 6. The honest expectation

**Expected contribution, worked through.**

- Novy-Marx's long-short GP decile spread is roughly **0.31%/month ≈ 3.7%/yr**
  gross, over 1963–2010.
- A long-only tilt captures roughly **half** a long-short spread — you get the long
  leg, not the short. Call it **~1.5–2%/yr** gross on a fully GP-tilted book.
- We apply it to **38% of NAV**: `0.38 × 1.75% ≈ **+0.7%/yr**` at the portfolio
  level, before the satellite's own trading costs.

**That is undetectable in this system, permanently.** Against a 0.43pp same-config
replicate floor on 28 days — and an observed ~10pp spread on the window this
project actually uses — a 0.7%/yr effect will never clear the measurement
apparatus. You will not be able to tell whether it worked. That is not an argument
against doing it. It is an argument for being clear that **the deliverable is the
cost saving (+6.7%/yr, counted) and the factor is a small, externally-justified
tilt riding along beside it.**

**15–20 names is not a decile portfolio.** Novy-Marx's spread is a cross-sectional
average over hundreds of names. At N ≈ 12, the idiosyncratic variance of the tilt
exceeds a 1.75%/yr expected edge by roughly an order of magnitude. The realistic
distribution of any one year's satellite outcome is dominated by a dozen coin
flips. The factor does not change that; it changes the *mean* of the coin flips by
an amount smaller than one bad earnings print.

**The factor can underperform for years, and that is the normal case.** Value-adjacent
factors had a brutal 2018–2020: HML was negative through most of 2017–2020 and its
drawdown from the 2007 peak ran over a decade. GP itself lagged badly through the
2020 unprofitable-growth melt-up. **A 3–5 year stretch of the factor subtracting is
an expected outcome, not a bug.**

The operational consequence is sharper than it looks: **if the factor will be
switched off after six bad months, do not ship it.** Turning a 25%/yr-turnover
factor on and off converts it into a high-turnover regime-timing strategy with no
evidence behind it — strictly worse than never having had the factor, because you
pay 23.2 bps for each switch and keep none of the premium. That commitment has to
be made before the flag is set, not after the first drawdown.

**What this design does and does not claim.**

- **Claims:** turnover falls to ≤50%/month, enforced not forecast, cutting cost
  drag from a measured 8.07%/yr to ~1.4%/yr. The satellite acquires a persistent,
  externally-motivated eligible set instead of a set re-drawn every bar. Both are
  arithmetic on counted quantities.
- **Does not claim:** any measurable alpha; that 0.5/0.5 is optimal (it is
  deliberately not optimised); that our backtest can distinguish this from the
  null.

---

## 7. Residual risk

1. **The edge is unverifiable here.** Accepted by construction (§5.1). Everything
   this design can prove is mechanical.
2. **Strict PIT is not the default.** `pit_mode` defaults to `"research"`
   (`interactive_utils.py:5580`), under which the factor disables itself. So the
   factor is off in every casual backtest, and every run that exercises it needs a
   finalised bundle per session. This is a real cost and a real chance to fool
   yourself: a run that *looks* like it tested the factor may have had it silently
   disabled. The coverage-neutrality criterion (§5.3e) exists to catch exactly that.
3. **Survivorship is not fixed by dating the values.** A bundle built today has a
   survivor-biased ticker set even with perfectly dated rows. Any historical
   *return* from a factor run inherits it. The module abstains on delisted names
   (one `fetch_failed` in the 79-name sample was exactly this) rather than
   inventing them, which keeps the live path honest and does not repair a
   historical study.
4. **Restatement lookahead is real and quiet.** yfinance serves statements as
   they read *today*. A backtest that computes GP/A for a 2024 decision gets the
   *restated* 2024 figures if they were later revised. The availability rule
   (`period_end + 120d`) prevents lookahead in time and does nothing about
   lookahead in revision. This is invisible in every diagnostic — it does not
   change coverage, dispersion or turnover, only returns, and only slightly and
   in the flattering direction. Do not treat a strict-PIT backtest as clean on
   this axis; it is clean on the time axis only.
5. **Depth is ~4 annual periods per name.** Enough for one rank-persistence
   estimate (§5.2), not enough for a historical factor sort. Any study that needs
   a longer panel needs a different data source, and that is a separate project.
6. **The sector exclusion is only as good as the vendor label.** Yahoo files MARA
   (a bitcoin miner) under Financial Services, so it is excluded correctly by
   accident; a mislabelled bank would be included incorrectly by the same
   mechanism. An EDGAR SIC code is the durable fix.
7. **Small N.** 8–15 names cannot express a decile spread (§4.5, §6).
8. **The two legs may not be orthogonal.** GP is mechanically high for asset-light
   software; `_TREND_ETF_MAP` routes "technology"/"ai" to QQQ/XLK/AIQ/SOXX and
   momentum discovery selects on 20d/60d returns. If both legs concentrate in the
   same sector, §2.3's trade-cancellation benefit shrinks.
   `_apply_sector_concentration_limit` and `_enforce_sector_portfolio_cap` are
   already in the chain; §5.3f measures the realised distribution rather than
   assuming they suffice.
9. **`turnover_budget_monthly_pct` is inert without `core_sleeve_enabled`** (§1.7).
   The whole cost argument in §4 assumes the core is armed. If it is not, none of
   the enforcement fires and the design delivers nothing.
10. **The satellite headroom trim wins over the tilt.** `_core_sleeve_satellite_headroom`
   at `broker.py:13734` trims `cash_to_use` after `nexus_position_sizes` is built,
   so a tilted-up `buy_cash` can be silently cut back. The tilt's renormalisation
   happens strategy-side, before the broker sees it, so the *relative* tilt
   survives; the *absolute* sizes may not. That is the correct precedence — the
   core's share is a structural constraint and the tilt is a preference — but it
   means the tilt is weaker in practice than its multiplier suggests.
11. **Multiple testing.** This project has run ~60 configs across 9 approaches on
   the bull-participation objective and found nothing; that history means any
   parameter tuned on our windows is suspect by default. The factor is a *new*
   hypothesis with *external* evidence, which is the right kind — but if
   `factor_weight` is then tuned on our data it rejoins the multiple-testing
   problem and the external validation stops transferring. **`factor_weight = 0.5`
   is fixed by construction and must not be optimised on this project's backtests.**
   That is a rule, not a default.
12. **`slot_min_notional_pct` is still half-wired** (`:30851`, `:31035` only). Do not
   use it to keep tilted satellite positions above dust; use
   `factor_tilt_min_order_usd` and `min_position_size`.

---

## 8. Config keys

**Reuse (readers verified in §1.7):** `core_sleeve_enabled`, `core_target_pct`,
`core_min_pct`, `core_max_pct`, `core_rebalance_band_pct`, `core_rebalance_min_days`,
`turnover_budget_monthly_pct`, `rank_band_enabled`, `rank_band_entry_pct`,
`rank_band_exit_pct`, `single_position_max_pct`, `min_position_size`,
`allocation_execute_min_raw_score`, `cash_reserve_floor_pct`, `rotation_ml_weight`,
`vol_adjust_sizing_enabled`.

**Add — every one defaults to OFF or identity, so an untouched doc-179 is
byte-identical:**

| Key | Default | Meaning | Reader to be written |
|---|---|---|---|
| `factor_profitability_enabled` | `false` | master gate | `factor_profitability.factor_config` |
| `factor_weight` | `0.5` | weight on the factor rank; `1 − w` on the news rank. **Fixed — see §7.8.** | `combine_ranks` |
| `factor_missing_rank` | `0.5` | rank substituted when `cross_sectional_ranks` returns `None`. The module deliberately does **not** substitute — the consumer does (§2.4). Equals the module's exported `NEUTRAL_PERCENTILE`. | `_apply_factor_gate` |
| `factor_min_coverage_pct` | `0.60` | below this the factor disables itself for the bar and logs red. Evaluated against the **post-exclusion** denominator (§5.2) — funds and financials are correct abstentions, not misses. | `_apply_factor_gate` |
| `factor_min_ranked_names` | `5` | passed to `cross_sectional_ranks(min_names=…)`; below it the module abstains entirely | `_apply_factor_gate` |
| `factor_min_entry_rank` | `0.40` | eligibility floor for a NEW entry | `_apply_factor_gate` |
| `factor_min_hold_rank` | `0.20` | factor-driven exit floor for a HELD name | `_apply_factor_gate` |
| `factor_rebalance_min_days` | `63` | trading days between factor rank refreshes | `_apply_factor_gate` |
| `factor_reporting_lag_days` | `120` | days after `period_end` before a period is visible. Maps to the module's `lag_days` / `DEFAULT_REPORTING_LAG_DAYS`. **Not** a staleness cutoff. | `gross_profitability` |
| `factor_max_period_age_days` | `730` | newest visible period older than this → refuse the name. Maps to `max_period_age_days`. | `gross_profitability` |
| `factor_excluded_sectors` | module default | financials, lower-cased match. Maps to `excluded_sectors`. | `gross_profitability` |
| `factor_require_strict_pit` | `true` | in a non-live context, refuse unless the values came from the PIT `fundamentals` snapshot rather than a live fetch (§5.1) | `_dated_gross_profitability` |
| `factor_tilt_enabled` | `false` | separate gate — eligibility can ship without sizing | `_apply_factor_tilt` |
| `factor_tilt_gain` | `0.6` | slope of the weight tilt in rank units | `tilt_multiplier` |
| `factor_tilt_min_mult` / `factor_tilt_max_mult` | `0.7` / `1.4` | clamp; mirrors `vol_adjust_min_mult`/`_max_mult` | `tilt_multiplier` |
| `factor_tilt_min_days` | `5` | trading days between tilt rebalances of a held name | `_apply_factor_tilt` |
| `factor_tilt_band_position_pct` | `0.20` | minimum weight change, as a fraction of the **position** (not NAV — see §4.5) | `_apply_factor_tilt` |
| `factor_tilt_min_order_usd` | `25.0` | absolute floor; mirrors `_CORE_MIN_SATELLITE_TRIM_USD` | `_apply_factor_tilt` |
| `factor_tilt_max_nav_pct` | `0.02` | ceiling on total tilt notional per window | `_apply_factor_tilt` |

**Delete from doc-179:** `max_single_position_pct` (dead, logs red at every boot,
`broker.py:4784`).

**Deliberately not added:** `factor_rebalance_exempt_from_budget`. See §4.4 item 3.

---

## 9. Implementation notes, file by file

1. **`backend/factor_profitability.py`** — **already landed, do not re-specify.**
   Pure computation plus a cache and one network fetcher, in the shape of
   `core_sleeve.py` and for the same reason: `broker.py` is not import-safe
   (argparse at module scope `SystemExit`s under pytest), so keeping this out of
   it makes the arithmetic unit-testable without the `ast`-extraction trick
   `backend/tests/test_residual_sleeve.py` needs. Its docstring is explicit that
   it "does not decide anything… so that a factor bug can never place an order by
   itself" — the consumer sites below are where that decision is taken, and they
   are the only places it should be.

   Two helpers this design needs that the module does not provide, and which
   belong on the consumer side because they encode *policy*, not *measurement*:
   `combine_ranks(news_rank, factor_rank, weight)` and
   `tilt_multiplier(rank, mean_rank, config)`.

2. **Data layer.** For the LIVE path, the module's own yfinance fetcher plus its
   `period_end + lag_days` availability rule is sufficient (§5.1). For a **strict
   backtest** the values must come from the PIT `fundamentals` snapshot instead.

   GP must ride the **existing** dataset, not a new one. `point_in_time_registry.py:30`
   fixes `REQUIRED_DATASETS = ("graph", "fundamentals", "universe", "news")` and
   `_build_bundle_rows` **rejects** anything else
   (`"PIT bundle contains unsupported datasets"`, `:408`). Adding a `factors`
   dataset would break every existing manifest hash.

   `_validate_bundle_datasets` requires only that `fundamentals` be a Mapping
   (`:262`), so **extra per-ticker fields validate today**. Add to each row the
   fields `gross_profitability_detail` already returns, so the snapshot and the
   live path are the same shape: `gross_profit`, `total_assets`, `period_end`,
   `available_at`, `frequency`, `source`. `available_at` is load-bearing — without
   it the factor is not point-in-time even inside a strict bundle.

3. **`backend/strategies/graph_nexus_analysis.py`** — three surgical changes.

   a. **Read side.** Add `_dated_gross_profitability(symbol, *, context,
   fundamentals)` beside `_dated_market_cap` (`:5331`), mirroring it exactly:
   `_market_cap_point_in_time_inputs` (`:5315`) already resolves
   `_point_in_time_context` / `_point_in_time_fundamentals` off `strategy_cache`,
   so this needs **no new plumbing** and inherits the strict-mode fail-closed
   behaviour (`raise PointInTimeDataError` when strict and the payload is absent;
   return `None` when `_pit_use_legacy_sources`).

   b. **Eligibility gate.** New `_apply_factor_gate(scores, symbols_list,
   portfolio_emulator, config, strategy_cache)`, inserted in the filter chain at
   `:27396` **immediately before `_apply_rank_band_gate`**, so the band ranks a
   universe the factor has already pruned and a factor-blocked name never inflates
   the band's denominator. Copy `_apply_rank_band_gate`'s contract exactly:
   demote to `score = 0` only; never override `_forced_exit` or a `_RISK_EXIT_TAGS`
   reason (`:22912`); exempt `_ALL_ETF_TICKERS | _sleeve_symbols(config)`
   (`:22857`); dedupe `symbols_list` before computing percentiles (`:22863` — the
   comment there explains why); ticker as explicit tiebreak (`:22878`); fail open
   and loud on a position-read error (`:22893`).

   c. **Combined ranking.** Do **not** modify `_rotation_effective_score`
   (`:11375`) — it is read by both the rotation lane and the band, and changing it
   moves both silently. Add `_combined_effective_score(score_doc, config,
   factor_rank)` and have `_apply_rank_band_gate` call it only when the factor is
   armed. One behaviour change, one site, one flag.

   d. **Sizing tilt.** New `_apply_factor_tilt(funded_slate, factor_ranks, config)`
   called at `:29629`, **immediately after `_apply_vol_adjustment`**, with the same
   multiply-then-renormalise contract so `_available_buy_budget` is invariant and
   nothing downstream (`_stock_budget_after_adds`, `:29661`) changes shape.
   `_clip_to_single_position_cap` still runs after (`:10506`), so the tilt cannot
   breach `single_position_max_pct`.

4. **`backend/broker.py` — no changes required.** The gate and the tilt both ride
   in `_nexus_position_sizes` (`:5339` → `:12361`). The satellite headroom trim
   (`:13734`), the turnover budget (`:13766`) and the regime cap (`:13788`) already
   govern the result, and they take precedence over the tilt by design (§7.7).

5. **Tests.**
   - `backend/tests/test_factor_profitability.py` — **already landed** alongside
     the module; covers the producer. The consumer still needs its own: the
     `None → factor_missing_rank` substitution, the clamp, budget invariance after
     renormalisation, and the coverage cutoff on the post-exclusion denominator.
   - A live-reachability test in the shape of
     `backend/tests/test_core_sleeve_live_reachability.py`. This is not optional:
     `_residual_sleeve_deploy` sat live-unreachable for weeks while backtests
     exercised it, and that test exists because of it. Any new gate reached only in
     one mode is the same bug.
   - An identity test: with `factor_profitability_enabled = false`, `scores` and
     `nexus_position_sizes` must be byte-identical to today's.

6. **doc-179 hygiene.** Delete `max_single_position_pct`. Decide whether
   `slot_min_notional_pct` should be wired to the main-signal path or removed —
   it has been half-dead across at least two design cycles now.

---

## 10. Order of work

0. ~~`factor_profitability.py` + its unit tests, no call sites.~~ **Done**
   (2026-08-03). Coverage and dispersion measured; see §5.2.
1. **Test 0, remaining half** (§5.2) — rank persistence ρ over the available
   periods. Offline, free, and it can kill the whole design before a line of
   strategy code is written. **Do this next, before anything else.**
2. Data layer: the `gross_profitability_detail` fields on the `fundamentals` PIT
   rows, so a strict backtest reads a snapshot rather than a live fetch.
3. `_dated_gross_profitability` + `_apply_factor_gate`, default OFF, with the
   identity test.
4. **Test 1** (§5.3) — one backtest, mechanical criteria only.
5. `_apply_factor_tilt`, default OFF.
6. **Test 2** (§5.4) — one A/B, counted quantities only, return explicitly not
   interpreted.

Do not proceed past step 1 if ρ < 0.6, or past step 2 if post-exclusion coverage
falls below 60% on a wider universe than the 79-name sample.
