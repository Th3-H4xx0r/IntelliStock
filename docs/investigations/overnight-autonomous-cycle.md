# Overnight autonomous anchor cycle — 2026-08-10

Operator: `overnight-operator` child agent. This is the sole-mutator journal requested by the user.
All times include timezone. Backtests are `pit_mode=research` / lookahead and cannot establish
real-money readiness. `doc-179` / `alpaca-main` is out of bounds and was not touched.

## Preservation baseline

At 2026-08-10 02:20:09 PDT, before any mutation, `git status --short --branch` showed:

```text
## main...origin/main
 M AGENTS.md
 M CLAUDE.md
 M backend/backtest_portfolio_value.csv
 M backend/backtest_prices.csv
 M backend/backtest_trades.csv
?? .agents/
?? docs/OBJECTIVE.txt
?? docs/OBJECTIVE_ONELINE.txt
?? docs/investigations/anchor-multi-window.md
```

These are pre-existing user/other-agent files and will not be staged, overwritten, or cleaned.
This journal is the operator's only new file so far.

## Pre-registered experiment

Source: `docs/investigations/anchor-multi-window.md`, read together with
`docs/handoffs/2026-08-10-sizing-pattern-and-readiness.md` and `docs/OBJECTIVE.txt` before operating.
Fixed procedure: doc 193, instance `v2-let-run-core`, $6,000, 3600-second bars, cold unique salts,
event-state reset before each run, treatment 20 paired with control 12 across OOS bull, bear, and
non-semi windows. No config/reset/push while a run is in flight.

## Monitoring ledger

### bt 615886 — OOS bull treatment, target 20, salt `anchor20-oos-20260810-a`

- **2026-08-10 02:20:09–02:20:20 PDT (09:20Z):** ran
  `python3 scripts/pull_backtest_logs.py 615886 --summary` and pulled full logs plus API metadata to
  `/tmp/bt615886.{log,json}`. Status `running`; progress 3.74%; elapsed 221s; `_last_active`
  `2026-08-10T09:20:13.225400+00:00`, so not stalled. API summary read-back verified instance
  `v2-let-run-core`, doc/strategy 193, dates `2026-03-30..2026-04-27`, granularity 3600, cash 6000,
  `anchor_reinforce_target_pct=20`, salt `anchor20-oos-20260810-a`, fresh-low gate 2, rally-onset true,
  rotation false, and `pit_mode=research`. Log checks: 0x401, 0x404, 0 warnings, no traceback and no
  `Run-once strategy ... error`; no `ANCHOR ADD:` yet. One isolated data error at log line 1598:
  Alpaca returned HTTP 400 for invalid symbol `T3`; surrounding progress completed the 36/36 overlay
  batch and activity continued, so this was not a repeated invalidating data failure. Decision: let run.

- **2026-08-10 02:30:06 PDT (09:30Z):** full pull/API check. Status `running`; progress
  28.02%; elapsed 808s; `_last_active=09:29:59Z` (7 seconds fresh), line count 5,924. 0x401,
  0x404, 0 warnings, no traceback or run-once strategy abort. The earlier invalid-symbol `T3` failure
  expanded to one daily-bars 400 plus five hourly chunk 400s, all for `T3`; the engine continued to
  progress normally, so this is a localized missing-symbol input rather than a broad/repeated provider
  outage and does not invalidate the arm. `ANCHOR ADD:`: 5 negative signatures (`none funded`), 0
  funded adds; logged budgets $1497, $1495, $0, $1418, $660. Last visible log was an in-progress daily
  sentiment LLM call at 09:28:42Z, while API heartbeats continued through 09:29:59Z. Decision: let run.

- **2026-08-10 02:57:03 PDT (09:57Z):** replacement-operator takeover poll (the cancelled
  predecessor's last poll was 02:30; this replacement session was not available at the intervening
  02:45 boundary). Established a strict 15-minute cadence from this pull. Status `running`; progress
  58.91%; elapsed 2,424s; `_last_active=09:56:52Z` (11 seconds fresh), line count 12,627 and partial
  whole-run return +9.37%. No traceback, no strategy abort, no actual HTTP 401/404 (the summary's
  `1×401` is the substring in an OIH `$401.67` price line), and no broad data outage. Error lines are
  the seven previously localized invalid-symbol `T3` 400s plus one analogous daily-bars 400 for
  invalid symbol `3EN`; active replay continued. `ANCHOR ADD:` now has **12 `none funded` and zero
  positive plans**; zero `action_intent=winner_add_buy`, zero anchor recipient BUY fills, and thus
  zero possible recipient quantity increases. The nine BUY fills are ordinary opening/core fills.
  Existing downstream evidence is already active for ordinary candidates: satellite cap skips/trims,
  15% broker-cap logs on new positions, and turnover budget binding at 121% of NAV late in this pull,
  but there is no anchor plan to correlate through those gates yet. Both independent reports are now
  complete and agree that historical `ANCHOR ADD:` was planner-only, all six prior plans were blocked,
  plan-time stage/budget accounting is defective, and a cap-only change is insufficient. Decision:
  let the fresh, progressing run continue. Next scheduled poll: 03:12 PDT.

- **2026-08-10 03:12:03–03:12:08 PDT (10:12Z):** on-cadence full pull/API check.
  Status `running`; progress 76.29%; elapsed 3,329s; `_last_active=10:12:06Z` (2 seconds fresh),
  line count 15,592 and partial return +13.45% (not a terminal result). No new errors: still only
  the eight localized invalid-symbol 400s, no traceback/strategy abort, no real HTTP 401/404. This
  interval produced the run's first positive planner allocation: `ANCHOR ADD: AAOI stage=1 +$265`
  (held 9d, +34.2%, 2.9% drop, entry $782, raw 1.200), followed by
  `action_intent=winner_add_buy` and immediately `SATELLITE CAP: AAOI skipped` with -$595 design
  room. There was **no AAOI BUY fill**: its quantity stayed exactly 7.031933505093401 from the
  original 04-07 fill through the latest 04-21 equity snapshot. The next BUY fill was SPY, not AAOI.
  Totals: 15 planner signatures = 14 negative + 1 positive, one winner-add intent, **zero actual
  anchor fills / quantity increases**. Turnover was simultaneously 120% of NAV; because satellite
  rejected AAOI first, no per-recipient turnover or 15% cap decision was reached/logged. This is the
  seventh observed plan-to-zero-execution chain and directly reproduces both analysts' diagnosis in
  the current build. Progress remains fresh and a later high-raw or partially filled recipient could
  still exercise a different downstream branch, so stopping now would discard useful gate evidence.
  Decision: let run. Next scheduled poll: 03:27 PDT.

- **2026-08-10 03:27:03–03:27:08 PDT (10:27Z):** on-cadence full pull/API check.
  Status `running`; progress 87.93%; elapsed 4,230s; `_last_active=10:26:55Z` (13 seconds fresh),
  line count 18,411 and partial return +10.61% / partial max drawdown 3.93% (neither terminal).
  No strategy abort/traceback/new provider error. The summary's `1×404` is also a substring false
  positive (`prompt=...dd404...` / `$404`), not HTTP 404. Anchor totals are now 18 planner
  signatures = 17 negative + the same single AAOI plan, one winner-add intent, and **zero anchor
  BUY fills or quantity increases**. No later plan reached turnover or the broker position cap. AAOI
  retained its original 7.031933505093401 shares until its later ordinary exit; it never increased.
  The latest log was an in-progress sentiment LLM call while API heartbeat/progress remained fresh.
  Remaining replay could still expose a high-raw/partial-position branch and completion is near, so
  decision remains let run. Next scheduled poll: 03:42 PDT.

- **2026-08-10 03:42:03–03:42:08 PDT (10:42Z):** on-cadence terminal pull. Status
  `finished`, progress 100%, elapsed 4,840s / 81 minutes, `_last_active=10:37:13Z`, 20,276 lines.
  `summarize_backtest.py`: return **+9.02%** ($541.45), max drawdown 5.4%, 27 fills
  (15 BUY / 12 SELL), 5 round trips, 80% win rate; SPY benchmark +13.10%, so this research run
  trailed SPY by 4.08pp while clearing the window's +5.5% 1x pace. No strategy abort/traceback.
  Final anchor audit: 20 planner signatures = **19 `none funded` + one AAOI stage-1 $265 plan**;
  one `winner_add_buy` intent; immediate `SATELLITE CAP` rejection; **zero anchor fills**. AAOI's
  only BUY was its original 7.03193351-share fill on 04-07. All 180 equity snapshots from that fill
  through 04-23 show exactly 7.031933505093401 shares, then the only SELL removed exactly the same
  quantity. Its +$238.63 result is entirely the original lot, not reinforcement P&L. No recipient
  turnover or single-position-cap decision was reached because satellite rejected first.

  This current-build reproduction, together with the five bt633644 plans and AXTI in bt584712, makes
  the old paired-return plan non-identifying: seven planner allocations, seven downstream rejects,
  zero exposure; moreover salt changes alter/inherit discovery before any add. The +9.02% versus
  historical +12.34% is therefore **not** a target effect. The five remaining control/bear/non-semi
  runs are cancelled as non-causal compute. Next action is documentation correction and the smallest
  safe default-OFF execution/fill-accounting fix analysis; no config/reset/push occurred during this
  run.

## Pre-fix reconciliation and impact analysis

Both independent reports (`agent-anchor-log-analysis.md` and `agent-anchor-code-audit.md`) agree on
the execution defect and were reconciled with bt 615886 before code work: planner allocation is not a
fill; satellite is the first observed blocker; the default 15% broker cap is next for full clips;
plan-time stage advancement and budget charging are wrong; later targets ignore actual added shares;
and raising the hard-coded 40% budget alone is not a fix.

GitNexus index was current at `2cd998c`. Upstream impacts were run before edits for
`_plan_anchor_reinforcement`, `GraphNexusAnalysis.run_once`, and
`_apply_backtest_confirmed_fill_state`; all returned `UNKNOWN / target not found` because
`graph_nexus_analysis.py` and `broker.py` exceed the indexer's 512 KiB symbol limit. Manual radius:
`_plan_anchor_reinforcement` has one production caller plus focused tests; `run_once` feeds every
graph-strategy order; `_apply_backtest_confirmed_fill_state` sees every backtest fill but the planned
change is source-prefixed; and the broker buy choke point serves every lane but the new branch is
default-OFF. Assessed **MEDIUM** with strict default-off tests, not HIGH/CRITICAL. No
`PortfolioEmulator` method or global broker cap will be changed.

## 04:02 PDT — default-OFF execution contract implemented; full suite reconciled

Implemented the smallest backtest-only, explicit-opt-in contract in `graph_nexus_analysis.py` and
`broker.py`:

- planner emits pending stage state rather than advancing on intent;
- later target sizing uses actual held position value, not accumulated plan budgets;
- qualifying next-event orders/fills carry `anchor_reinforcement:*` source tags;
- a stage commits only after a qualifying final fill;
- anchor lane gets explicit position and turnover ceilings when enabled, without modifying the
  global broker position cap or unrelated lanes;
- live/unknown mode remains fail-closed and the feature defaults OFF;
- greppable `ANCHOR REINFORCEMENT PLAN/BLOCK/ORDER/FILL/STAGE` audit lines identify each boundary.

Added `backend/tests/test_anchor_execution_contract.py` including real `PortfolioEmulator`
next-event fill tests. Validation so far: focused anchor/core selection **104 passed**; broad
broker/emulator selection **112 passed**; `py_compile` passed. Full backend suite was then run twice;
final concise result: **4820 passed, 13 skipped, 19 failed**. The exact same 19 pre-existing
adversarial red tests remain (11 exit-discipline, 7 core-sleeve, 1 client-timeout classification),
so there is no new full-suite failure. Output is retained at
`/tmp/intellistock-full-pytest-anchor.txt`.

Attempted to spawn the required two post-edit read-only bug sweeps, but this child session is already
at the daemon recursion ceiling (`RLM_DEPTH=1`, `RLM_MAX_DEPTH=1`). Parent was notified at 03:55 PDT
and asked to spawn two sibling reviews and relay results. No push, deploy, config reset, or new
backtest has occurred; no run is active.

## 04:34 PDT — post-edit adversarial reconciliation complete

Parent spawned the two required read-only sibling sweeps after this child hit the recursion ceiling.
They wrote `bug-sweep-anchor-planner.md` and `bug-sweep-anchor-broker.md` and initially returned a
**do-not-enable** verdict. Reproductions found partial-final false completion, ask-price rather than
mid-mark valuation, uncorrelated stage/plan/order fills, buying-power underfills, passive expiry
wedges, stale stage state across re-entry, legacy OFF metadata drift, non-cumulative core/turnover
and fill accounting, pending-sell credit coupling, and zero-exposure new-entry crowd-out.

All reproduced blockers were reconciled and fixed behind the same default-OFF/backtest-only branch:

- unique plan + accepted order identity is carried and validated at fill; duplicate/mismatched fills
  fail closed;
- actual emulator midpoint marks determine target completion, and a partial final order no longer
  completes a stage merely because the residual is below the next order minimum;
- cumulative per-stage filled dollars survive retries, while confirmed full exits reset the position
  episode;
- expired/cancelled/restored orphan orders reconcile against the simulator order book;
- policy min-fill always reads actual emulator buying power/reservations/T+1;
- core preflight uses production position headroom, exact execution order, cumulative two-leg
  turnover projection, and a rebuilt filtered funding request;
- floor-funded execution is invalid unless explicit pending-sell credit is enabled;
- execution-aware plan intent does not reduce the unrelated same-bar entry slate; only an accepted
  order competes through actual broker cash/buying power;
- the dormant/false branch no longer emits execution-only hint fields; schema defaults remain OFF.

Both original sweep agents rechecked the final bytes and reported **no remaining blocker from their
reproduced sets**. Final validation: focused/broad selection **259 passed**, `py_compile` passed, and
full backend suite **4829 passed, 13 skipped, exact baseline 19 failed** (11 exit-discipline, 7
core-sleeve adversarial, 1 timeout classification). Final output:
`/tmp/intellistock-full-pytest-anchor-final2.txt`.

GitNexus `detect-changes --scope working` reported LOW/0 indexed processes, but it did not map the
oversized broker/strategy files; per the pre-edit analysis, the honest manual risk remains
**MEDIUM**, with no HIGH/CRITICAL result. No push, deployment, config mutation, or verification run
yet; no backtest is active.
