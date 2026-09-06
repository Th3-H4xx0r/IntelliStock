# Handoff: Strategy EB — VTS killed, champion proven untouched (A/A), K3 regime battery, review fixes deployed

## Session Metadata
- Created: 2026-09-06 01:39 UTC
- Project: github.com_th3-h4xx0r_intellistock (IntelliStock)
- Branch: feat/outlier-sleeve (worktree .claude/worktrees/main-session; in sync with origin/feat/outlier-sleeve; fast-forward pushed to origin/main at ea343b2 — main == this branch)
- HEAD: ea343b2 research(strategy-eb): clean bil25 control, doc-200 A/A byte-identical, K3 regime battery 20/25 vs SPY
- Session commits: 5f03c4d..ea343b2 (8 new this session: 2f14b4a, e0dd1be, d9599d2, 3d2bef7, 8ab94cb, fa8ebb3, 8a381b0, ea343b2) — all pushed and DEPLOYED (check_deployed_code: 11/11 files match)
- Working tree: dirty with untracked leftovers only — bt143282.json, bt186463.json (Aug 27 fetch dumps), scripts/_deploy_then_pair.sh, scripts/_deploy_then_pair2.sh, scripts/_deploy_then_api_pair.sh (earlier sessions' helpers). Do NOT stage any of them.
- Continues from: "Strategy EB goal loop CLOSED — all three clauses hold at the operator's declared horizon" (Jarvis, 2026-09-01) · Supersedes: that handoff's "next" list for VTS/short-window work

## Current State Summary
The all-regime strategy work for the $6k Alpaca account is in a stable, fully verified state. **bil25** (TQQQ vol-targeted core at 0.20, off-book GLD 37.5% / GDX 18.75% / XLE 18.75%, BIL risk-off, Wednesday-close cadence) is live on doc 200 / paper instance `strategy-eb`, holding GLD/GDX/XLE since the 2026-08-31 fills; the first real weekly rebalance (and the first TQQQ core entry, if the vol target allows) is due **Thursday 2026-09-10 ~08:30 CDT**. Friday 09:30 CDT proof passed: 4 FULL in-session ticks, 0 pre-submit sync failures (the reconciliation fix 8d66ed8 holds), no orders, marks == held set.

This session: (1) the pre-registered **VIX term-structure re-entry (VTS) was killed** — both thresholds failed all six frozen bars in the engine, and an ECC MLE review showed the registered 250-session median was never runnable (63-session warmup) — VTS is now fail-closed and observable in code, default off; (2) the user saw negative lab rows and asked whether the code changes broke the champion — **proven not**: an A/A of the unchanged doc 200 on the current code (bt 443180) reproduces the baseline (bt 785201) to the cent over 1,259 sessions; (3) **K3 (vol target 0.25) ran the 25-window regime battery: 20/25 beat SPY-TR (bil25 16/25), 19/25 beat bil25, all bears positive, cycle +219.7% vs +197.8%, DD −21.6%** — failed the frozen short-window bars, so adoption is the user's call (not made); (4) two ECC review agents (mle-reviewer, python-reviewer) produced 3 HIGH findings, all fixed and deployed; (5) a published matrix artifact of every tested arm vs SPY. Where things left off: nothing running; awaiting the user's K3 decision and Thursday's tick.

## Architecture & Carried-Forward Context (still true)
- Strategy EB = pure module `backend/strategy_eb.py` (DEFAULTS, eb_core_weight, eb_trend_state, eb_should_trade, eb_targets, strategy_eb_universe) + wrapper `backend/strategies/strategy_eb.py` (run_once: reserve, VTS block, sweep, decision row/log). Docs: 200 = paper live (instance `strategy-eb`), 201 = lab (instance `strategy-eb-lab`, EB + outlier_sleeve lane). Doc 200 carries graph_nexus_analysis as its second lane; that lane difference is why lab-doc controls run ~1pp off doc 200 (not code).
- Engine truth: backtests via POST /backtests (granularity is SECONDS; "86400" daily; equity_cost_tiers "etf-liquid"; $6,000 start). SPY-TR convention = engine SPY price × 1.0125^years. Beat rates and the 25 regime windows are fixed in `scripts/outlier_engine_test.py` (REGIME_WINDOWS, BASE = bil25 per-window return/DD).
- Store: Postgres 17 @ server7 through backend/db (store.get/insert/insert_bulk…); research reads use psycopg read-only with POSTGRES_PASSWORD; BacktestSteps(kind=pv|trade|log) holds paths.
- Deploy: backend auto-deploys from **main**; a push RESTARTS instances and KILLS in-flight backtests. `scripts/check_deployed_code.py` fingerprints 11 files incl. both EB files (fixed 5f03c4d).
- Standing user rules: NEVER post backtests in parallel (one at a time, wait for finished); all subagents on model "opus"; pre-register thresholds and never move them; never edit doc 200 without the user; no pushes within 30 min of 08:30 CDT or while a backtest runs.

## Codebase Understanding
### Critical Files
| File | Purpose | Relevance to this task |
|------|---------|------------------------|
| backend/strategy_eb.py:375-410 | `vts_ratio_norm` — ratio / trailing median | Now returns None until it holds `vts_median_bars` paired sessions (fail closed) |
| backend/strategy_eb.py:744-810 | `eb_should_trade(..., trend_state, vts_active=False)` | Off-cadence flip requires `vts_active` (ratio measured this session), default False |
| backend/strategies/strategy_eb.py:224-262 | run_once VTS block | Cadence + flip derive from `_vts_live = _vts_on and ratio is not None`; price hysteresis persisted, not the forced ON; `_log_once` red when unmeasurable |
| backend/strategies/strategy_eb.py:355-380 | decision row + log line | `cache[_LAST_DECISION_KEY]["vts"] = {ratio, pairs, fired}`; log carries `vts r= n= fired=` |
| backend/broker.py:4439-4510 | `_strategy_eb_risk_limits` | Per-lane try/except (~4478): one malformed sibling lane no longer returns None for the whole document (real-money path) |
| backend/db/store.py:865-945 | `insert_bulk` | In-batch id dedup (last write wins), RETURNING (xmax = 0) accounting, duplicates counted under conflict="error" |
| backend/db/fake.py:178-210 | FakeStore `insert_bulk` twin | Same table guard + dedup + result shape |
| scripts/eb_short_window_test.py | sequential candidate runner | `--set short|vts`, `--windows regime`, B0 control, `EngineBusy`, `running_on_instance()`, `restore_doc()` verified restore, strict flags |
| scripts/check_deployed_code.py:27-42 | deploy fingerprint list | Includes strategy_eb.py + strategies/strategy_eb.py |
| docs/superpowers/research/2026-09-03-short-window-preregistration.md | frozen bars + K1–K4/B0 results + K3 regime table + A/A section | The adoption evidence for K3 |
| docs/superpowers/research/2026-09-04-vts-reentry-preregistration.md | VTS registration + verdict | Why VTS is dead; do not re-test |
| backend/tests/test_strategy_eb*.py, test_live_risk_limits.py, test_store_insert_bulk.py, dbcore/test_schema_ensure.py | regression pins | 191 EB tests, 39 risk, 6 bulk, schema count 129+2 |

### Key Patterns / Conventions
- Tests: `python3 -m pytest backend/tests -q -p no:cacheprovider --ignore=backend/tests/test_graph_nexus_analysis.py` → 6,836 passed; **19 failures are pre-existing** (test_adv_exit_discipline_findings.py, test_core_sleeve_adversarial.py, test_zz_adversarial_sweep.py — identical at base 127f75b). No local Docker → `scripts/dev_pg.sh` unavailable; suite runs on FakeStore.
- Commit footer: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01Lm9Fzv5RihgHoMjXRBLvPx`. main is protected but the user's account bypasses (push prints "Bypassed rule violations").
- CLAUDE.md mandates gitnexus impact/detect_changes; the graph queries currently ERROR ("Binder exception: Cannot find property id for n") and detect_changes returns partial/0 symbols even after `GITNEXUS_MAX_FILE_SIZE=2048 npx gitnexus analyze` — use grep callers as the fallback and say so. Re-analyze rewrites CLAUDE.md/AGENTS.md and drops the env-var warning; restore it (done 8a381b0).
- ECC GateGuard hook blocks the first Bash/Edit/Write of a turn until facts are stated (request + what the command does); state them and retry.
- `scripts/_api.py` `call()` raises SystemExit on HTTP errors — catch BaseException in probes. macOS has no `timeout` binary.
- Lab runs: reset + re-read doc 201 before any control (see gotchas). Clean lab state = EB: vts_enabled False, target_vol 0.2, reserve_for_other_lanes_pct 0.15, bil25 books; sleeve lane enabled True.

## Work Completed
### Tasks finished
- [x] VTS engine test (V1 thr 1.00 bt 560944; V2 thr 1.05 bt 807899): both FAIL T1–T6; verdict recorded; line closed.
- [x] ECC mle-reviewer (VTS gap) + python-reviewer (EB diff) run on opus; reports in scratchpad (vts_gap_review.md, eb_code_review.md); H1–H3 + M4–M10 fixed.
- [x] VTS fail-closed + observability + hysteresis fix + wiring test (3d2bef7).
- [x] Broker per-lane risk-envelope guard + test (8ab94cb).
- [x] insert_bulk dedup/accounting + FakeStore twin + tests; runner strictness; schema table count (fa8ebb3).
- [x] Contaminated B0 control (bt 906467 == V1) diagnosed → lab doc 201 reset + verified; clean B0 (bt 630425) and doc-200 A/A (bt 443180, byte-identical to 785201).
- [x] K3 25-window regime battery (20/25 vs SPY, 19/25 vs bil25) appended to the research doc; memory updated.
- [x] Pushed main + branch, deploy verified (11/11), paper instance back with GLD/GDX/XLE.
- [x] Friday 09:30 CDT reconciliation proof: 4 FULL ticks, 0 sync failures, no orders, doc 200 bil25.
- [x] Backtest matrix artifact: https://claude.ai/code/artifact/18b58c3a-e719-4aa4-a1dd-38d6bec64fea

### Commits
- `2f14b4a` test(strategy-eb): --windows regime — 25-window battery per candidate (pushed)
- `e0dd1be` test(strategy-eb): B0 control candidate (pushed)
- `d9599d2` research(strategy-eb): VTS engine verdict + review findings (pushed)
- `3d2bef7` fix(strategy-eb): VTS fails closed; cadence/flip follow the ratio; price hysteresis; ratio logged; runner hardening (pushed, deployed)
- `8ab94cb` fix(broker): one malformed lane no longer collapses the live risk envelope (pushed, deployed)
- `fa8ebb3` fix(db,test): insert_bulk dedup + honest counts; runner strict flags; schema count (pushed, deployed)
- `8a381b0` chore(gitnexus): refresh counts; keep GITNEXUS_MAX_FILE_SIZE warning (pushed)
- `ea343b2` research(strategy-eb): clean control, A/A, K3 regime battery (pushed)

### Files modified
| File | Changes | Rationale |
|------|---------|-----------|
| backend/strategy_eb.py:388-395, 744-810 | `n < bars` → None; `vts_active` kwarg gating `_vts_flip` | A 63-session "250-median" was an unregistered rule; a data gap must not become a daily-flip strategy |
| backend/strategies/strategy_eb.py:224-262, 355-380 | `_vts_live`, price-state persistence, unmeasurable log, decision-row vts stamp | Reviewer D1/D2/A2: cadence gated on config only; zero VTS observability |
| backend/broker.py:~4478 | per-lane try/except around RiskLimits | Reviewer H3: KeyError in a sibling lane returned None for all → EB buys blocked silently |
| backend/db/store.py:899-945, backend/db/fake.py:178-210 | dedup, RETURNING xmax, counts, twin guard | Reviewer M4–M6: PG rejects repeated ids in one statement; counts were wrong |
| scripts/eb_short_window_test.py | EngineBusy, running_on_instance, single POST, restore_doc verified, vts reset in main(), strict flags, results in finally | Reviewer H1/H2/M7–M10; the contaminated-control incident |
| backend/tests/… (5 files) | pins for all of the above; schema count 129+2 | regression |
| docs/superpowers/research/2026-09-0{3,4}-*.md | verdict + control/A/A/K3 sections | evidence trail |
| CLAUDE.md, AGENTS.md | refreshed counts; warning restored | gitnexus analyze rewrite |

### Decisions made
| Decision | Options considered | Rationale |
|----------|--------------------|-----------|
| Kill VTS, no re-shopping of thresholds | fix warmup + retest; raise WARMUP_CYCLES; kill | Registered rule unmeasurable on this engine; threshold=median has no information; failed all bars; frozen "no second round" |
| Let V2 finish before killing | stop mid-run | 10 min of engine time to complete the registered pair; record complete |
| Rerun B0 after lab-doc reset rather than trust the runner's "original" | trust restore | The restore had faithfully re-applied a contaminated doc; only an explicit reset + re-read is proof |
| Doc-200 A/A on the paper instance (read-only for the doc) | argue from the code diff only | The user needed byte-level proof; lab-doc controls can never give it (sibling lane differs) |
| Push + deploy at 22:30 CDT | wait for the 01:15 cron | No backtest running, far from 08:30 CDT, suite gated, reviews done |
| K3 adoption NOT made | adopt K3; stay bil25 | Failed frozen bars; the regime table is the user's decision input |

No config/knob changes on doc 200. Lab doc 201 before→after: vts_enabled True/1.0 (contaminated) → False; target_vol 0.25 (mid-battery) → 0.2; reserve 0.0 → 0.15; sleeve off → on. ROLLBACK = none needed (this IS the canonical lab state).

## Pending Work
### Immediate next steps (ordered)
1. (USER) Decide K3 adoption: vol target 0.25 on doc 200 (`target_vol` 0.2 → 0.25, one key). Evidence: research doc §"Control, A/A and the K3 regime battery" + the matrix artifact. If adopted, edit doc 200 only on the user's explicit go, and re-verify with a doc-200 cycle run (expect ≈ bt 877293's +219.7%).
2. Thursday 2026-09-10 after 08:30 CDT: verify the first real bil25 rebalance on `strategy-eb` — GET /instances/strategy-eb/live-logs?since_line=0, expect a `StrategyEb 2026-09-09 | core TQQQ target X%` line (Wednesday close seen on Thursday), orders consistent with eb_targets, no sync failures; then live-state marks == held set.
3. Store durable knowledge items from this session (done in memory; mirror to Jarvis knowledge if not present): lab-doc contamination; offline screens must reproduce the engine control; gitnexus queries erroring.
4. Optional, low value: explain the 1pp lab-vs-doc-200 gap definitively (Dec 6 2021 $31 crumb sweep — sibling lane graph_nexus_analysis on doc 200); only if the user asks.

### Blockers / open questions
- [ ] None blocking. Open: K3 adoption (user); LiveState initial_value baseline (paper P&L display) still unfixed — deferred from an earlier session.

### Deferred
- python-reviewer M11 ("only the core takes the haircut" test is a fixture artifact) — cosmetic test note, not behaviour.
- 19 pre-existing failing adversarial-finding tests — documented findings, not regressions; untouched.
- Untracked helper scripts in scripts/_deploy_then_*.sh and the two bt*.json dumps — not this session's; leave for the user.

## Context for Resuming Agent
### Important Context (READ BEFORE DOING ANYTHING)
- **Real money adjacency:** doc 200 drives the paper instance today and is the config the user intends to fund. Never edit doc 200, never post a backtest while another runs, never push within 30 min of 08:30 CDT or while a backtest is in flight (a deploy kills it and restarts instances).
- **Nothing is running.** All chain scripts (run_more.sh, run_aa.sh, run_vts.sh) finished; all session crons were cancelled or fired; the engine slot is free.
- **The champion is untouched by this session's code:** A/A bt 443180 == bt 785201 exactly. If anyone claims otherwise, point at that pair.
- **VTS is dead.** vts_enabled False everywhere; do not re-test or re-shop the threshold (docs/superpowers/research/2026-09-04-vts-reentry-preregistration.md §Verdict).
- **Lab doc 201 canonical state** (verified): EB vts_enabled False, target_vol 0.2, reserve 0.15, bil25 books; sleeve lane enabled. Re-read it before any control run; a control that reproduces a candidate to the decimal is a contaminated doc.

### Assumptions Made
- bt 785201 was run on doc 200 / instance strategy-eb before the reserve commit (BacktestSteps has no timestamps; the A/A made the assumption moot).
- The Nexus lane on doc 200 is the source of the Dec 6 2021 crumb buys (inferred from the doc diff, not traced).
- The user's account bypass on protected main is intentional (push succeeded with "Bypassed rule violations").

### Potential Gotchas
- A killed candidate runner leaves lab doc 201 in the candidate's config; the relaunched runner captures that as "original" and restores it (2026-09-03 incident).
- `GET /backtests` API rows omit granularity/instance for older runs; read BacktestResults.doc via psycopg for instance_id/strategy_id.
- `_api.call` → SystemExit on 404; `timeout` missing on macOS; GateGuard needs facts first each turn; GitNexus impact/detect_changes error even after re-index.
- Deploy check must pass BEFORE any engine run after a push (a run posted on the old image, bt 847463, was stopped and discarded this session).
- Two B0 rows exist in the short-window doc: bt 906467 (contaminated, == V1) and bt 630425 (clean). Use the latter.

## Environment State
- Tools / services: IntelliStock API https://intellistock-api.pkrishna.dev via scripts/_api.py (source .env); Postgres read-only @ server7 (psycopg, POSTGRES_PASSWORD); Alpaca paper account behind instance strategy-eb (no per-instance orders/account API route — use live-state + live-logs); no local Docker; Jarvis MCP + GitNexus MCP available (GitNexus queries erroring).
- Active processes: None (verified: no run_more/run_aa/eb_short_window processes; GET /backtests shows nothing running).
- Environment variables: POSTGRES_PASSWORD, PG_DSN, APCA_API_KEY_ID / APCA_API_SECRET_KEY (instance-side), RETHINKDB_* (retained), GITNEXUS_MAX_FILE_SIZE (set to 2048 when indexing).
- Resume commands:
  - `set -a && . ../../../.env && set +a`
  - `python3 scripts/check_deployed_code.py` (must print 11/11 match before any engine run)
  - `python3 -m pytest backend/tests/test_strategy_eb.py backend/tests/test_strategy_eb_run_once.py backend/tests/test_strategy_eb_broker_wiring.py backend/tests/test_live_risk_limits.py backend/tests/test_store_insert_bulk.py -q -p no:cacheprovider`
  - `python3 scripts/eb_short_window_test.py --set short B0` (bil25 control, 4 windows, sequential) · `--set short K3 --windows regime` (25 windows)
  - Live proof: `python3 -c "import sys;sys.path.insert(0,'scripts');from _api import call;print(call('GET','/instances/strategy-eb/live-state')[1])"`

## Related Resources
- docs/superpowers/research/2026-09-03-short-window-preregistration.md (frozen bars, K1–K4, B0, K3 regime table, A/A)
- docs/superpowers/research/2026-09-04-vts-reentry-preregistration.md (VTS registration + verdict)
- docs/superpowers/research/2026-09-02-outlier-sleeve-engine-test.md (sleeve arm, 17/25)
- Backtest matrix artifact: https://claude.ai/code/artifact/18b58c3a-e719-4aa4-a1dd-38d6bec64fea
- Memory: project_short-window-frontier.md, project_strategy-eb.md, reference_lab-doc-contamination.md, feedback_sequential-backtests.md, reference_deploy-verification.md
- Scratchpad reviews (session-local): vts_gap_review.md, eb_code_review.md
- Prior Jarvis handoff: "Strategy EB goal loop CLOSED — all three clauses hold at the operator's declared horizon"
