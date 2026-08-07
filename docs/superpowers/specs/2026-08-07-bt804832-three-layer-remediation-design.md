# bt 804832 — what was actually wrong, and what shipped

**Date:** 2026-08-07 · **Shipped:** `00eeb15`
**Evidence:** backtest 804832 (`v2-let-run-core`, doc-193, 2026-01-01..2026-03-01,
3600s, $6,000 → $6,103.23, +1.72%) — 37,626 log lines, 17,281 decision rows,
31 trades, 1,828 LLM calls. Six-agent forensic sweep, then three adversarial
reviews of the resulting plan, then three adversarial sweeps of the code.

> **This document was rewritten after review.** Its first version proposed four
> workstreams. Adversarial review refuted most of them, including the claim they
> rested on. What follows is the corrected record. The refuted material is kept
> in §5 deliberately — the wrong version cost a day, and the next person deserves
> to know which attractive ideas are already dead.

---

## 1. The finding

The run returned +1.72% while SNDK returned +129% in the same window. SNDK was
**discovered, scored, ranked #1 on the momentum watchlist for most of two
months, and emitted 13 broker-level buy signals**. Every one was refused.

A prior session concluded "SNDK was never discovered." That was wrong: it queried
`GraphNexusDiscoveredStocks` unscoped (6,891 rows across 12+ instance scopes) and
treated a one-row-per-ticker-per-scope *registry* as an event log. SNDK has
exactly one row in this run's scope, which means fully discovered.

**The binding constraint was portfolio construction, not discovery and not
exits.**

### The origin event

`core_sleeve_enabled` is absent from the operative base config
(`strategies[0].config`) and lives only in `regime_profiles.{bull,chop,recovery}`.
`_apply_regime_profile` merges the matching overlay in before the config is read.
On the first tick the regime detector has not seen enough closes, no overlay has
merged, and `config.get("core_sleeve_enabled", False)` is **False**.

That one False built the failed book. The allocator's satellite-share clamp is
gated on it, so the opening basket sized against `nexus_portfolio_pct` (0.95)
instead of the 0.38 design share and opened at **48.5% of NAV**. Headroom went
negative on tick 2 and never recovered.

Proof: `V31.2 total-spend cap` appears **exactly once** in 37,626 lines — on the
second tick of day 1, capping at $2,280 (= 0.38 × 6,000). It never fired on the
tick that actually built the book.

### What that cost

43 `SATELLITE CAP` refusals across 33 symbols, all reporting negative headroom
(−$102 to −$646). SNDK's five cheapest entries ($388–418) are among them.

Of every gate measured on this run, **`SATELLITE CAP` is the only one whose
blocked basket was positive forward** (+10.5% EOW vs a +2.88% universe control).
The others were refusing losers: `MAX_POSITIONS_GATE` −9.6%, backfill
FORCE-BLOCKED −6.0%, extension-block −8.0%.

### The rest of the picture

- **Exits are fine.** Closed holds 8–25 days, winners held 1.8× longer than
  losers, exit decisions net **+$87.62** vs holding to the bell.
  `trailing_stop_disabled` suppressed 70 GDX exits and saved more than the run's
  entire P&L. Entries are the defect, not exits.
- **The +1.72% was an accident.** GDX is +$191 = **185% of total P&L**, bought
  2026-01-02 on a live `gold_momentum` thesis, then frozen in place by the same
  gates. Closed round-trips **lost $84.10** (win rate 20%); the gain is
  unrealized mark-to-market.
- **Sizing.** Per-name satellite weight measured mean 6.75% / median 4.73% of
  NAV — not the 1.71% a prior analysis derived from `max_positions_bull=14`. The
  effective cap was 6 *including* the core, so the satellite never had more than
  five slots.
- **Achievable capture is +62.58%, not +129%.** SNDK entered this run's price
  universe on 2026-01-12 at $388.455.

---

## 2. What shipped (`00eeb15`)

| # | Change | Where |
|---|---|---|
| 1 | `core_sleeve_armed_for_bar(config, regime=…)` — regime-aware arming | `core_sleeve.py` |
| 2 | `satellite_design_share(config, regime=…)` — one definition, three call sites | `core_sleeve.py`, `broker.py` ×2, `graph_nexus_analysis.py` |
| 3 | Min-paths quality filter gains a conviction bypass (default OFF) | `graph_nexus_analysis.py` |
| 4 | `stock_buys_scored` tiebreak: continuous + NaN-safe, was alphabetical | `graph_nexus_analysis.py` |
| 5 | `dividend_summary` on the result row | `broker.py` |
| 6 | `_satellite_cap` test helper calls the real functions instead of mirroring | `test_core_sleeve.py` |

**doc-193 config:** `propagation_min_paths_conviction_bypass_raw = 1.0`.
`max_positions`, `turnover_budget_monthly_pct`, `entry_extension_block_pct`,
`propagation_min_paths` **deliberately unchanged**.

### Why regime-aware arming, specifically

An absent flag is ambiguous. It means *either* "no overlay merged yet" (warm-up,
the bug) *or* "this regime has no profile, so the core is off on purpose" —
which is doc-193's bear, where `test_regime_conditional_core` measures the arm at
**+10.07%** precisely because the core stays off and the SQQQ hedge runs.

A first version scanned profiles unconditionally and armed the clamp on **every
bear bar**, reserving ~62% of NAV for a core that would never be bought.
`strategy_cache["_market_regime"]` disambiguates: known regime → trust the merged
config; unknown → warm-up → scan. Only that third branch is new behaviour.

Profile resolution also ignores overlays that switch the core *off*, and no
longer depends on dict order — RethinkDB returns sorted keys, so the old "first
profile wins" would have let an alphabetically-first `bear` profile impose a
de-risked target on every bull bar.

### Honest limitation

The satellite now opens at *exactly* its design share, so headroom is born at
**$0** and goes negative as soon as the alpha sleeve outperforms the core. The
cap is entry-only and nothing trims back (pre-existing; see
`test_core_sleeve_adversarial.py::test_A11`). This removes the opening
**deficit**; it does not create durable headroom. Expect refusals later in a run
rather than gone.

---

## 3. Written and reverted

Both are documented at their sites so they are not re-attempted blind.

**Excluding the sleeve from `max_positions`.** SPY permanently held one of six
slots and the book read `held=6, cap=6` on 418 of 634 bars. But `_mpg_held` is
one of **four** counters — `_z41_held_now`, `_count_open_positions`,
`_mw_open_set` — and moving it alone desynchronises them: a latched bear's
headroom went 0 → 2, re-opening the per-bar refill the latch exists to stop.
That gate was also blocking losers (−2.6% to −9.6% forward). Do it by moving all
four together, behind its own A/B.

**Drain-time extension re-check on the backfill queue.** VTYX — the run's worst
entry — was extension-blocked (+77.4% > 25%) *and* rank-band rejected (#27 vs
#20), then bought via the queue 19 days after the gap that was its entire move,
with `max_return_so_far` of 0.865%. But the first version referenced
`price_history`, which does not exist in `run_once` (it is `data`), so it
`NameError`'d and failed open on every evaluation. And once fixed it **blocks
SNDK**: `entry_extension_block_pct` is 0 in the bull/recovery overlays and 25
only in the base, and the regime flips chop↔bull eight times in this window.
SNDK's queue buy landed on a bull tick by luck. Refusing the +129% winner to
avoid a −$0.41 loser is the wrong side of that bet.

---

## 4. Measurement

- **The $7.42 residual is not a bug.** It is deliberate SPY dividend accrual
  (`portfolio_emulator.py:969`), which credits cash without writing a trade row,
  so `pnl_per_stock` and `pnl` legitimately disagree by exactly that amount. The
  arithmetic closes ($6,000 × 61.9% SPY × 1.25%/yr × 59/365 ≈ $7.50). An earlier
  draft made "assert residual < $0.01" a program gate — that would fail **every
  backtest holding an index ETF**. It is now surfaced instead.
- **It is not a noise floor either.** It is deterministic common-mode bias that
  cancels in a paired A/B. The real noise floor is single-position: GDX alone is
  3.2% of NAV, and it entered from carryover state visible from 2025-12-01 — so
  the fresh-salt-per-arm protocol can itself move the baseline by more than any
  lever being tested.
- **46.5% of `trade_contexts` predates the window** (8,029 of 17,281 rows, 6+
  config hashes of shared-scope residue). In-window it is 141 buy rows across
  106 symbols, not 304/218. Scope every forensic query by date AND
  `instance_id`.
- **n = 5 round trips, 1 winner.** No lever is validatable on this window alone.

---

## 5. Refuted — do not re-propose without new evidence

| Claim | Verdict |
|---|---|
| "Ranking IC ≈ 0.000, the score is noise" | **Refuted.** With symbol-clustered bootstrap CIs the IC is *positive*: +0.170 @1d (t=3.06), +0.226 @10d (t=3.18). The ≈0 figure came from joining scores to `backtest_prices.json`, which holds only the 11 **traded** names — range restriction on both variables, which attenuates ρ mechanically. |
| "Monotonicity inverts" | **Refuted.** `raw=+1.000` beats `raw<0` at *every* horizon by 2.8–5.0pp. |
| "Loosen the entry-extension gate" | **Refuted.** Its blocked basket is −7.95% event-weighted / −14.25% symbol-level vs a +2.88% control — the only unambiguously loss-blocking gate. Its `no conviction bypass` is documented as removed after CAR (raw 5.56) entered parabolic and gapped −33%. |
| "Exempt initial construction and sells from the turnover budget" | **Refuted, and dangerous.** Charged turnover would fall to 7.1%/month against a 50% budget — 7× slack, never binds again. The budget is anchored on Novy-Marx total traded notional; counting buys only silently doubles it. |
| "Raise `max_positions` to 8–10" | **Refuted.** Combined with the sleeve exclusion it latches `_position_breach_active` and force-liquidates every bar. It also dilutes: 10 names under a 38% ceiling is 3.8% each, cutting the prize 62%. And rotation is disabled by sentinel (`rotation_min_delta=99`), so the book fills day 1 and cannot swap. |
| "`Total weight: 1.000` proves graph paths collapse" | **Refuted.** That line is `broker.py:14292`, the *cross-strategy* vote aggregator. With one strategy at weight 1.0 it is a tautology. Real path aggregation (`graph_nexus_analysis.py:19346`) already aggregates with geometric decay. |
| "De-quantize the score" | **Not actionable as stated.** Path count is 1 on 72% of rows, and the LLM emits an integer by schema (`sentiment: int = Field(0)`, prompt says 1/−1/0). Downstream de-quantization is interpolating between three integers. Start at the sentiment source or not at all. |
| "Wire the ML slot" | **Wrong flag.** `nexus_ml_enabled: False` short-circuits one line before `ml_signal_weight`. The real blocker is that `features` is deliberately not persisted (the 18GB → lean-doc fix), so every training vector is zeros. |
| "Widen `core_rebalance_band_pct` / add `core_rebalance_min_days`" | **Inert.** Both already set (0.1 / 20). All 8 rebalances are `funding`, which bypasses band and cadence by design. |
| "Arm a mid-range give-back guard" | **Refuted.** It risks GDX (+$191, 185% of P&L) to recover ADNT's −$3.58 — 32:1 against. |
| "The circuit breaker bottom-ticked EFX" | **Overstated.** It sold at $176.11; the series low was $167.42 the next morning. |

---

## 6. Next, in evidence order

1. **Verify.** Paired run, identical window/instance/granularity to 804832.
   Pre-registered: `SATELLITE CAP` fires → ~0; opening basket ≤ 38% of NAV;
   `V31.2 total-spend cap` fires on tick 1; a name in SNDK's position gets
   bought. Note the run is PIT `research` mode — lookahead, not promotion-eligible.
2. **Satellite trim-back** — the A11 defect. Without it the cap re-binds as soon
   as the sleeve works, which caps the ceiling of everything above.
3. **Passive execution** — the codebase's own documented "largest unexploited
   cost lever" (`simulated_execution.py:135`), scaffolding already present,
   ~22.8 bps per side. Note the cost model applies one notional-weighted 45.6 bps
   to every symbol (true p90 is 109), so it *under-charges* exactly the microcaps
   discovery surfaces.
4. **Delete the overlay** — 1,472 of 1,828 LLM calls, 67.5% of spend, 7,606 of
   12,371 LLM-seconds, and it left no trace in any of 17,281 decision records.
   Buys back the wall clock the validation protocol needs.
5. **All four `max_positions` counters together**, then re-test the sleeve
   exclusion.

## 7. doc-179 (real money, `alpaca-main`, STOPPED)

**Unaffected by this commit** — `core_sleeve_enabled` and `core_target_pct`
appear nowhere in that document, including its regime profiles, so
`core_sleeve_armed_for_bar` returns False and every path is byte-identical. The
two new levers default OFF.

Nothing here should be ported without separate sign-off. The standing constraint
is that turnover is the leak (~290%/month vs a ~50% break-even), so anything that
raises position count or loosens entry gates is the wrong direction on that book.
