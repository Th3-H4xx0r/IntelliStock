# Anchor execution contract — pre-registered real-run verification

Date: 2026-08-10. Written before deployment and before the verification run.

## Why this run exists

Bt 633644, bt 584712, and bt 615886 produced seven anchor planner allocations and **zero executed
anchor adds**. Every allocation was rejected by the standing satellite cap. The new behavior is an
explicitly default-OFF, backtest-only execution envelope that admits a planned anchor only inside a
document-scoped concentration cap, a hard rolling-turnover ceiling, and the floor-bounded core
band. It also carries plan/order/fill identity and commits a stage only from a correlated confirmed
fill. This is a mechanical execution verification, **not** a causal return comparison.

## Fixed run

* Window: reference `2026-01-01..2026-03-01`
* Instance: `v2-let-run-core`; doc 193; 3600-second bars; $6,000
* `anchor_reinforce_target_pct=20`
* `anchor_reinforce_execution_enabled=true`
* `anchor_reinforce_execution_max_position_pct=20` (percent points; research-only lane cap)
* `anchor_reinforce_execution_turnover_ceiling_pct=0.80` (fraction; hard maximum)
* `anchor_reinforce_execution_core_floor_enabled=true`
* `backtest_credit_pending_sell_proceeds=true` (required for floor-funded policy validity)
* `history_scope_salt=let-run-core-193` to maximize comparability with the historical reference
  planner evidence. This is one mechanical arm, not a paired P&L claim.
* Keep the fresh-low gate N=2, rally-onset true, rotation off, and no bear profile.
* Reset `GraphNexusActiveEvents` and `NexusStrategyCache` immediately before launch.

## Required signatures and pass/fail

A planner line is never success. Reconcile the full chain by symbol, stage, plan ID, order ID, and
dollars:

1. `ANCHOR PLAN`
2. either `ANCHOR BLOCK` with a named gate, or `ANCHOR ORDER`
3. a source-tagged execution `FILL BUY`
4. `ANCHOR FILL`
5. `ANCHOR STAGE COMMIT` or explicitly logged partial state
6. a recipient quantity increase in the emitted equity snapshots/result curve

**Mechanical pass:** at least one plan reaches an actual source-tagged fill and quantity increase;
all identities reconcile; final recipient weight stays at or below 20%; projected rolling turnover
stays at or below 80%; and no core release is emitted for an order already known to be inadmissible.

**Safe but inconclusive:** no stage-eligible plan appears, or every plan is explicitly blocked before
core release/order submission. Do not claim the execution lane works from this outcome; choose a
window from existing plan evidence rather than relaxing risk limits.

**Fail and revert doc flag:** orphan/mismatched/duplicate fill; stage commit without a correlated
fill; accepted no-fill that remains pending; quantity fails to increase; recipient exceeds 20%;
turnover exceeds 80%; a rejected plan repeatedly reduces the unrelated new-entry slate; a core sell
funds an order then blocked by a gate the pre-pass could have known; or any run-once strategy abort.

P&L, SPY alpha, drawdown, names, and turnover are recorded, but this single research/lookahead run
cannot establish causal alpha or promotion readiness. A later P&L evaluation still needs frozen
identical discovery/history state, paired arms, three windows, actual fills, and the 4.94pp noise
floor.

## Run ledger

| deployed commit | backtest | status/result | execution verdict |
|---|---:|---|---|
| `960a469` | 735390 | stopped at 33.33% via `run=false` (partial through 2026-01-21; +10.552%) | **Literal broad preregistration FAIL / safely stopped; p7 order-level gates PASS** — the whole-run ledger reached 82% and UUUU later drifted to 21.61%, but the accepted p7 request projected 79.8855% and filled to 19.4831% |


## Mechanical monitoring evidence — bt 735390

### 2026-08-10 11:43:15 UTC — poll 1

* API: `/status`, `/summary`, `/logs`, and the paginated backtest row all returned HTTP 200. Status is `running`; progress `2.57%`; elapsed `101s` (`summary` stored elapsed `90s`); `_last_active=2026-08-10T11:43:08.711463+00:00` (about 7 seconds fresh at the poll); 3,018 log lines, file source, log error field `null`. Simulated time has reached 2026-01-02 and useful work is continuing.
* Frozen-run check: row/summary exactly report `v2-let-run-core`, doc/strategy `193`, `2026-01-01..2026-03-01`, 3,600-second bars, and `$6,000`. Frozen strategy schema exactly has target `20`, execution enabled `true`, lane cap `20`, lane turnover ceiling `0.80`, core-floor funding `true`, pending-sell credit `true`, history salt `let-run-core-193`, fresh-low N=`2`, rally-onset `true`, backfill rotation `false`, and no `bear` regime-profile override. `scripts/check_deployed_code.py 960a469` independently matched all six trade-deciding deployed hashes to commit `960a469`.
* Exact cumulative anchor signatures: `PLAN=0`, `PLAN NONE=0`, `BLOCK=0`, `ORDER=0`, `FILL=0`, `STAGE COMMIT=0`, `STAGE PARTIAL=0`; invalid fill signatures `ORPHAN=0`, `MISMATCH=0`, `DUPLICATE=0`. Thus there is no anchor chain or recipient quantity to reconcile yet. Generic `[execution] FILL BUY` count is 4 (NVO 16.13507190 / `$839.97`; ODFL 5.34525391 / `$839.97`; UUUU 53.07508767 / `$839.97`; V 2.38871176 / `$826.73`), none associated with an anchor plan; each initial stock fill is below 14.0% of starting NAV.
* Core/caps/turnover: anchor satellite admits `0`; anchor turnover admits/blocks `0/0`; `[core] funding`/`[core] released` `0/0`; `SATELLITE CAP=0`; generic turnover binding/block/bypass/ceiling counts `20/4/0/0`. The logged rolling level is `56%` of NAV, below the anchor lane's hard `80%` ceiling. No anchor recipient position-cap log exists yet.
* Errors/aborts: exact HTTP-status/error lines in the backtest log `0`; actual provider-error indicator `1` (`LLM returned no sentiment (timeout or API error...)`, with its documented company-article fallback), while subsequent provider outcome lines are `ok=True`. Raw substring hits `400=13`, `401=1`, `404=1`, `429=1`, `500=21` are false positives (scores/dollar amounts, millisecond durations, quantities/prices, and config numerals), not HTTP responses. There are `2` clean `Run once complete` lines and `0` run-once aborts/exceptions. Two `empty/failed classification — will retry` notices are retryable item-level classifications, not strategy aborts. No invalidator is present; do not stop.


### 2026-08-10 11:58:15 UTC — poll 2 and predeclared stop

* The poll began 14 ms from the registered 15-minute deadline. All three API reads returned HTTP 200. Status was still `running`, progress advanced `2.57% -> 33.33%`, elapsed `1,002s`, `_last_active=2026-08-10T11:58:12.579752+00:00` (2.6 seconds fresh), and logs advanced `3,018 -> 15,059`; simulated time reached 2026-01-21. This was useful progress, not a stall.
* **Predeclared invalidator — turnover exceeded 80%.** Exact log evidence: `[2026-08-10 11:46:17] ... TURNOVER BUDGET BINDING: 82% of NAV traded in the last 21 sessions`; the later anchor ledger gave the unrounded crossing at `[2026-08-10 11:49:52] ... ANCHOR BLOCK: NVO ... used=80.8% projected=83.5%`. At 11:59:07 UTC, after capturing the evidence, `POST /backtests/735390/stop` returned HTTP 200 with `{"stop_requested":true,"id":735390}`. The broker then logged `Backtest stop requested (run=false)` and set the result to `stopped`; terminal confirmation at 11:59:33 UTC returned status `stopped`, progress `33.33%`, elapsed `1,039s`, `_last_active=2026-08-10T11:58:57.700668+00:00`, and 15,224 full log lines.
* **Second predeclared invalidator — anchor recipient exceeded 20%.** UUUU was 19.4831% immediately after the anchor fill in the emitted 2026-01-20 14:00 equity snapshot (`60.1333260590 * $21.29 = $1,280.24` on `$6,571.03` NAV), but market appreciation made it 21.0342% at 15:00, a maximum 21.6116% at 20:00, and 21.3042% in the terminal 2026-01-21 00:00 snapshot. The order itself did not overfill; the terminal recipient-weight contract nevertheless says final weight must remain at or below 20%, so this is a mechanical failure.

#### Terminal anchor ledger (all plans reconciled)

Cumulative exact signatures are `PLAN=9`, `PLAN NONE=5`, `BLOCK=8`, `ORDER=1`, source-gated `ANCHOR FILL=1`, `STAGE COMMIT=0`, `STAGE PARTIAL=1`; `FILL ORPHAN=0`, `FILL MISMATCH=0`, `FILL DUPLICATE=0`. Generic execution fills total `9 BUY / 2 SELL`; only the UUUU increment below correlates to an anchor order.

| plan | intent | terminal chain |
|---|---|---|
| `UUUU:s1:p1` | stage 1; target/current/need/planned `$1285/$1045/$241/$241` | `SATELLITE ADMIT`; turnover `used=79.7%`, projected `82.9%`; `BLOCK gate=turnover_ceiling`; no order/fill |
| `NVO:s1:p2` | stage 1; `$1269/$968/$301/$175` | `SATELLITE ADMIT`; turnover `used=80.8%`, projected `83.5%`; `BLOCK gate=turnover_ceiling`; no order/fill |
| `NVO:s1:p3` | stage 1; `$1286/$969/$316/$178` | satellite overflow admitted; turnover `used=79.7%`, projected `82.5%`; `BLOCK gate=turnover_ceiling`; no order/fill |
| `UUUU:s1:p4` | stage 1; `$1289/$1045/$244/$178` | `SATELLITE ADMIT`; turnover `used=79.5%`, projected `82.3%`; `BLOCK gate=turnover_ceiling`; no order/fill |
| `UUUU:s1:p5` | stage 1; `$1307/$1111/$195/$181` | `SATELLITE ADMIT`; turnover `used=78.4%`, projected `81.2%`; `BLOCK gate=turnover_ceiling`; no order/fill |
| `UUUU:s2:p6` | stage 2; `$1318/$1165/$153/$153` | `SATELLITE ADMIT`; turnover `used=77.8%`, projected `80.1%`; `BLOCK gate=turnover_ceiling`; no order/fill |
| `UUUU:s2:p7` | stage 2; `$1321/$1166/$155/$155` | core two-leg pre-pass excluded release at `82.2%`; cash-funded `SATELLITE ADMIT`; turnover projected `79.9%`; `ORDER sim-000000000011-UUUU` requested/cash/fundable `$155.43`; matching `ANCHOR FILL` `$150.61`, exact same plan/order, then `STAGE PARTIAL` with `$41.25` remaining; no commit |
| `NVO:s1:p8` | stage 1; `$1321/$1006/$315/$108` | core two-leg pre-pass excluded release at `80.8%`; `SATELLITE ADMIT`; turnover `used=79.9%`, projected `81.5%`; `BLOCK gate=turnover_ceiling`; no order/fill |
| `NVO:s1:p9` | stage 1; `$1327/$974/$353/$258` | `SATELLITE ADMIT`, then trim to `$33.82`; `BLOCK gate=satellite_cap`; no order/fill |

The p7 fill is an actual quantity change: the immediately correlated execution line is `[execution] FILL BUY UUUU qty=7.05823839 ... price=21.338755` (`$150.6140`), and the emitted position moves from `53.0750876660` shares to `60.1333260590`. The generic execution line does not itself print a `source=` field; the adjacent `ANCHOR FILL` exists only after the fill's `anchor_reinforcement:` source and `UUUU:s2:p7` / `sim-000000000011-UUUU` identity have matched, so the source-gated chain is present without claiming that the generic line visibly contains the tag.

#### Core funding, cap, turnover, and runtime integrity

* Every one of the 9 plans has an explicit `[core] anchor funding excluded ... projected two-leg turnover ... exceeds 80.0%` pre-pass line. The 2 actual `[core] released` lines both precede the first anchor plan; there is no core release after a known-inadmissible anchor plan. The accepted p7 order used existing cash after its core release was excluded. The emitted SPY core stayed above its configured 10% floor (minimum 11.1162%; terminal 11.1623%).
* Anchor-specific counts: satellite admits `8` plus one high-raw generic satellite-overflow admit; turnover admits/blocks `1/7`. Maximum anchor planned projection is `83.5%`; maximum exact `used` is `80.8%`; generic rolling logs reach `82%`. Summary core-sleeve churn is 4 SPY fills, `$4,314.48` gross (71.91% of starting NAV), `$1,915.93` post-initial gross (31.93%), `$737.23` net, churn ratio `5.8523`, and 3 side flips. All 11 execution fills total `$9,503.26` gross, but that all-fill figure is not substituted for the logged rolling-turnover ledger.
* Runtime error audit on the full log: exact HTTP 4xx/5xx response lines `0`; `ok=False` provider outcomes `0`; actual provider fallback flags `0`. There is one generic provider-error indicator (`LLM returned no sentiment (timeout or API error...)`) which took its documented company-article fallback. There are 33 `raw_json_fallback=True` successful parses and 6 retryable item-level `empty/failed classification` notices, with no strategy abort. Raw numeral substring hits `400=70`, `401=7`, `404=8`, `429=1`, `500=46` are scores, amounts, durations, prices/quantities, and config values—not HTTP responses. `Run once complete=15`; run-once abort/exception/traceback `0`. No isolated bad-symbol HTTP 400 occurred.

#### Partial result and verdict

This is a deliberately stopped partial result through 2026-01-21, not the registered full-window result: `$6,000 -> $6,633.12`, P&L `+$633.12` / `+10.552%`, 11 trades (9 buys / 2 sells), no closed round trips, high `$6,720.22`, low `$5,992.25`, and max drawdown `1.6339%`. The emitted-curve SPY proxy is `$681.82 -> $677.66` (`-0.610%`), so the partial arithmetic spread is `+11.162pp`; neither that incomplete spread nor this single research/lookahead arm is causal alpha evidence.

**Literal preregistration verdict: FAIL / safely stopped, revert the doc flag before any future run.** The lane did mechanically reach one identity-correlated source-gated real fill and quantity increase, with no orphan/mismatch/duplicate and no improper core release. The broad wording nevertheless fails because the whole-run request ledger exceeded 80% and the recipient later/finally remained above 20%. The explicit partial stage does not turn that literal whole-run contract into a pass.

**Post-run gate-level diagnosis: the UUUU p7 order itself passed both implemented admission gates.** Independent reconstruction in `agent-anchor-735390-turnover.md` and `agent-anchor-735390-cap.md` established:

* The logged 82% occurred on simulated January 7, before the first anchor plan, from the opening basket plus unrelated AMCR/AMD submissions. Before p7, accepted-request ledger turnover was 77.5331%; accepting its `$155.43` request projected 79.8855%, below the lane's 80% ceiling. At the later fill snapshot the same request ledger became 80.3282% only because NAV declined; actual governed fills including p7 were 78.9648%. The lane is an accepted-order request-admission ceiling, not a continuous global filled-turnover invariant.
* UUUU was 17.647662% at its p7 decision, with exact `$155.429489` headroom. The correlated `$150.6140` fill produced 19.483069%, below 20%. Quantity then stayed unchanged while the next mark snapshot rose to 21.034245% and later peaked at 21.611592%. The lane is a decision-time buy-admission cap, not a continuous rebalance or forced-winner-trim policy.
* Therefore the correct finding is not “the anchor order breached its gates.” It is “the preregistration accidentally imposed broader continuous/global invariants than the implemented order-admission contract.” Both facts are retained: the registered run fails literally, while the p7 execution contract passes mechanically.

Future preregistrations must name four separate quantities: accepted-request turnover at admission, actual governed fill turnover, decision-time/fill-snapshot position weight, and later mark drift. They must not call accepted-request notional “traded,” and they must require visible source provenance on the generic execution fill line.

#### Safety reversion

After terminal status was independently confirmed, `scripts/set_doc_config.py` changed only doc 193 `anchor_reinforce_execution_enabled` from `true` back to `false`; the API returned HTTP 200 and read-back confirmed `false`. Doc 179 / `alpaca-main` was not touched.

## Post-run observability resolution

A subsequent behavior-neutral patch reconciles the implementation and future preregistrations without adding forced trimming or changing any order gate:

* turnover ledger comments and operator messages now say **accepted-order request notional**, not realized “traded”/fill notional;
* the generic next-event execution line emits `source=` before source-specific reconciliation;
* the accepted anchor order freezes its decision-time admission cap beside the order ID;
* `ANCHOR FILL` emits an explicitly **all-source tick snapshot** with current NAV, weight, mark basis, valuation status, and frozen admission cap;
* the pending-fill mark map now includes dynamically discovered held symbols from point-in-time data, rejects invalid/non-finite marks, labels prior-mark fallbacks, and reports valuation as unavailable rather than fabricating zero NAV/weight;
* passive fills use the current quote/event mark for NAV rather than their resting limit fill price;
* a cap-drift diagnostic states that all same-tick sources are included and cannot prevent final stage disposal if logging fails.

Regression coverage includes marketable and passive fills, current/prior/missing/invalid marks, valuation exceptions, dynamic holdings and future-bar exclusion, same-symbol and multi-symbol batches, partial/final/expired state, frozen cap provenance, source families, and accepted-request wording. Final validation was **119 focused passed** and **4,846 full-suite passed, 13 skipped**, with the same **19 intentional/pre-existing adversarial failures**. Early independent sweeps A-F found and reproduced the stale-map, mutable-cap, failure-order, invalid-mark, same-tick, dynamic-symbol, and passive-limit defects; after correction, independent sweeps G and H both returned **PASS / no blockers**.

This closes log/accounting ambiguity only. It does not turn the partial bt 735390 return into causal alpha evidence, does not establish continuous caps, and does not enable another run. Doc 193 remains disabled; doc 179 remains untouched.
