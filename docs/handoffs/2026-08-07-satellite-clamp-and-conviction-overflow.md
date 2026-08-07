# 2026-08-07/08 — the satellite clamp, and why a found winner still could not be bought

**Commits:** `00eeb15` · `4424d34` · `d5ece6f` · `5637b15` (all on `main`, all deploy-verified by hash)
**Spec:** `docs/superpowers/specs/2026-08-07-bt804832-three-layer-remediation-design.md`
**Tests:** 4,461 passed / 13 skipped
**In flight at handoff:** bt **455506** (~2/3 done)

---

## THE HEADLINE

bt 804832 returned +1.72% while SNDK returned +129% in the same window. The
cause was **not** discovery and **not** exits. SNDK was discovered, ranked #1 on
the momentum watchlist for most of two months, and emitted **13 broker-level buy
signals**. Every one was refused by portfolio construction.

A prior session concluded "SNDK was never discovered." That was wrong — it
queried `GraphNexusDiscoveredStocks` unscoped (6,891 rows across 12+ instance
scopes) and read a one-row-per-ticker-per-scope *registry* as an event log. One
row means fully discovered.

---

## RESULTS SO FAR

| | 804832 (before) | 677976 (clamp fix) | 455506 (in flight) |
|---|---|---|---|
| return | +1.72% | **+2.97%** | pending |
| SPY | +0.64% | +0.64% | +0.64% |
| vs SPY | +1.08pp | **+2.33pp** | pending |
| trades | 31 | 22 | pending |
| turnover blocks | 30 | **0** | pending |
| SNDK | never bought | bought ($156, 01-30) | pending |
| `SATELLITE CAP` fires | 43 | 70 | **10** |
| `SATELLITE OVERFLOW` fires | — | — | **63** |

bt 632754 was a pre-fix attempt, deliberately stopped — ignore it.

---

## FIX 1 — the clamp was inert on the tick that built the book (`00eeb15`, `d5ece6f`)

`core_sleeve_enabled` lives only in `regime_profiles.*`, and the overlay never
reaches the allocator's `config`. So the satellite-share clamp read `False`,
skipped itself, and the opening basket sized against `nexus_portfolio_pct`
(0.95) instead of the 0.38 design share — opening at **48.5% of NAV**. Headroom
was negative from tick 2 for the rest of the run: 43 `SATELLITE CAP` refusals
across 33 names, including SNDK's five cheapest entries ($388–418).

Proof: `V31.2 total-spend cap` appears **exactly once in 37,626 lines**, and not
on the tick that built the book.

**`SATELLITE CAP` is the only gate whose blocked basket was positive forward**
(+10.5% vs a +2.88% universe control). `MAX_POSITIONS_GATE` (−9.6%), backfill
FORCE-BLOCKED (−6.0%) and the extension gate (−8.0%) were all refusing losers.
Do not "fix" those by loosening them.

**`d5ece6f` matters:** the first version of this fix *reproduced the bug*, and
bt 632754 caught it in nine minutes. The premise — a warm-up window before the
detector had enough closes — was wrong. The detector picks a regime almost
immediately (`V31 market regime: chop (closes=90)`, two minutes before the first
buy); the overlay just never arrives. Short-circuiting to `False` on a known
regime left the clamp inert on exactly the tick that matters. A known regime now
consults **its own profile**.

---

## FIX 2 — the design share was a hard ceiling (`5637b15`)

677976 bought SNDK for the first time, and it contributed **$4.24 on a +166%
move**: bought 2026-01-30 at $614.80 for **$156**, i.e. **96.7% of the way
through the move**, on the same bar another name was sold. `SATELLITE CAP` fires
went *up* (43 → 70) — the cap working as written, and the book unable to act.

Treating the *design* share as a hard ceiling pins the satellite at exactly 38%
forever: headroom born at $0, so a winner can only enter by backfilling an exit.
That contradicts the sleeve's own design — a graph BUY should raise the
satellite and lower the core, "held ABOVE its index weight, funded by selling
the index". `core_target_weight` already implements that.

A candidate clearing `satellite_conviction_overflow_min_raw_score` now measures
headroom against the core's **floor** (`core_min_pct`, 0.30) instead of its
target: **0.68 of NAV instead of 0.38** — $4,080 vs $2,280 on a $6,000 book. The
floor is the guardrail that already existed and is already tested.

**Both gates move together — this is the whole risk.** The buy trim
(`broker.py` `_sat_room`) and the core's funding release (`_fr_room`) are keyed
on the same headroom. Extending the buy alone starves it of cash; extending the
release alone sells core to fund an order that is then refused and bought back —
the **$2,600-of-notional-for-zero-net-change** churn loop the 2026-08-03 sweep
measured. The funding request is now split by the same predicate: plain buys
draw only on design room, conviction buys take what remains of the band above it.

Threshold picked from the data, not intuition: `raw` saturates at ±1, so a 1.0
cutoff admits **~72%** of candidates and deletes the cap. Observed allocation
distribution is **p50 1.000 / p75 1.300 / p90 1.800**; doc-193 uses **1.5**
(top decile-to-quartile). **SNDK carried 1.700.**

Live evidence from 455506:
```
SATELLITE OVERFLOW: LMT raw=+1.800 >= 1.50 — funding $2,719 of room out of the core (floor-bounded)
```

---

## ALSO SHIPPED

- **Min-paths quality-filter conviction bypass** (default OFF; doc-193 = 1.0).
  `raw` saturates, so one strong path pins the ceiling and the filter was
  deleting the TOP of the distribution — 46 of its 47 fires carried raw > 1.000
  (mean 1.335, max 1.800), including SNDK's highest-scoring bar (1.482).
  Compares the **signed** score: the enclosing `raw_score` is an `abs()`, which
  would have admitted a −1.5 bearish aggregate as a buy and logged `raw=1.500`.
  Mutation-tested — the guard fails when the bug is reintroduced.
- **Continuous, NaN-safe tiebreak** for `stock_buys_scored` (was alphabetical).
  `raw_net_score` saturates, so the entry cutoff repeatedly fell inside a tie and
  admission was decided by spelling.
- **`dividend_summary` on the result row.** `pnl − sum(pnl_per_stock)` is now a
  checkable identity — verified exactly on 677976: 178.2202 − 170.7851 = 7.4351.

## CONFIG STATE — doc-193 (`v2-let-run-core`)

- `propagation_min_paths_conviction_bypass_raw = 1.0`
- `satellite_conviction_overflow_min_raw_score = 1.5`
- **Deliberately unchanged:** `max_positions=6`, `turnover_budget_monthly_pct=0.5`,
  `entry_extension_block_pct=25`, `propagation_min_paths=2`, `core_min_pct` unset (0.30 default).
- Backups: `scripts/doc193_backup_patch_*.json`.

**doc-179 (real money, `alpaca-main`, STOPPED) is byte-identical.** The core keys
appear nowhere in it, including its regime profiles, and both new levers default
to 0.0/off.

---

## WRITTEN AND REVERTED — do not re-attempt blind (notes are at both sites)

- **max_positions sleeve exclusion.** `_mpg_held` is 1 of **4** counters
  (`_z41_held_now`, `_count_open_positions`, `_mw_open_set`); moving it alone took
  a latched bear's headroom 0 → 2, re-opening the per-bar refill the latch
  exists to stop. That gate was blocking losers anyway.
- **BFQ drain-time extension re-check.** Referenced `price_history`, which does
  not exist in `run_once` (it is `data`) — `NameError`, failed open on every
  call. Once fixed it **blocks SNDK** on any chop bar: the threshold is 0 in the
  bull/recovery overlays and 25 in the base, and the regime flips chop↔bull eight
  times in this window. Refusing a +129% winner to avoid a −$0.41 loser.

## REFUTED — do not re-propose without new evidence (full table in the spec)

- **"Ranking IC ≈ 0, the score is noise."** Wrong, and it was the original
  plan's load-bearing claim. It came from correlating scores against
  `backtest_prices.json`, which holds only the **11 traded** names — range
  restriction on both variables. Measured on the full scored set with
  symbol-clustered bootstrap CIs the IC is **positive**: +0.170 @1d (t=3.06).
- Loosening the entry-extension gate (blocked basket −7.95%; its conviction
  bypass was removed after CAR entered parabolic and gapped −33%).
- Exempting sells / the initial basket from turnover (would leave 7.1% charged
  against a 50% budget — never binds again).
- Raising `max_positions` (latches breach auto-heal; dilutes the prize 62%;
  rotation is disabled by sentinel so the book cannot swap).
- **The $7.42 residual is not a ledger bug** — it is deliberate SPY dividend
  accrual. An "assert residual is zero" gate would fail every backtest holding an
  index ETF.

---

## NEXT

1. **Read 455506 to completion** against 677976 (+2.97%) and 804832 (+1.72%).
   The number to watch: does SNDK enter near its 01-12 signal at a full slot
   rather than 01-30 at $156?
2. **Passive execution** — the codebase's own documented "largest unexploited
   cost lever" (`simulated_execution.py:135`), ~22.8 bps/side, scaffolding
   already present. Note the cost model applies one notional-weighted 45.6 bps to
   every symbol (true p90 is 109), so it *under-charges* exactly the microcaps
   discovery surfaces.
3. **Delete the overlay** — 1,472 of 1,828 LLM calls, 67.5% of spend, 7,606 of
   12,371 LLM-seconds, and zero trace in any of 17,281 decision records.
4. **All four `max_positions` counters together**, then retry the sleeve exclusion.
5. Watch whether the overflow raises turnover. 677976 was already down to 22
   trades from 31; if 455506 climbs materially, the overflow is buying churn.

## TRAPS

- **Two config blocks.** `strategies[0].config` (~540 keys) is operative;
  top-level is legacy and says `max_positions=50` / `allocation_profile=balanced`
  and carries a stray `llm_model: gemini-3-flash-preview`.
- **Nexus tables are scope AND time contaminated.** 46.5% of bt 804832's 17,281
  `trade_contexts` rows predate the window (6+ config hashes). Scope by
  `instance_id` AND `date_key`.
- **Never push while a backtest is in flight** — the backend auto-deploys from
  main and the restart kills the run. Verify with
  `scripts/check_deployed_code.py`, and confirm the **hash** rather than trusting
  a fast `rc=0`.
- `backend/tests/test_{core_sleeve_adversarial,adv_exit_discipline_findings,zz_adversarial_sweep}.py`
  are untracked, written to FAIL, **NOT FOR COMMIT** (7 fail / 4 pass is the
  expected baseline for the first).
- Every equities backtest overrides `pit_mode` to `research` — lookahead bias,
  not promotion-eligible. A live claim needs a PIT run.
- **Read a run before believing a mechanism.** Config-based predictions were
  wrong 3 of 4 times in the prior session, and several more times in this one.

## KNOWN LIMITATION

The satellite cap is still **entry-only** — nothing trims a position back once
it is over share (`test_core_sleeve_adversarial::test_A11`, which fails on
purpose). The overflow gives a conviction name room to enter at size; it does
not reclaim room from a stale holding. If 455506 still shows late entries, that
is the next constraint.
