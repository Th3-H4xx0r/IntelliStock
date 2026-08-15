# Self-Learning Subsystem — Design

**Date:** 2026-08-15
**Status:** approved in brainstorming, pending spec review
**Scope:** a strategy-agnostic subsystem that observes live and backtest activity, forms
hypotheses about its own mistakes, tests them, and applies what survives — up to and
including real money — with a web tab, a mobile tab, notifications, and configurable AI
models.

---

## 1. Purpose

IntelliStock currently learns nothing from its own history. Findings are discovered by a
human reading logs, and the resulting levers are applied by hand. That loop has produced a
documented failure pattern: thirteen levers shipped inert, a "SPY-beating" result that was
a max-of-N artifact, and a selection signal that was a constant for the entire life of the
project without anyone noticing.

This subsystem closes the loop. It records what the system *decided* (including everything
it refused to do), resolves those decisions to outcomes, raises findings, proposes and
tests changes, and promotes the survivors along a staged ladder with automatic rollback.

It is **strategy-agnostic**: nothing in it knows what a strategy is. It works for RSI's
four tunables and Nexus's three hundred through the same code path, and a strategy written
next year is discovered automatically.

### Design constraints drawn from this project's own defect history

Each of these is a structural rule, not a guideline:

| Observed failure | Structural response |
|---|---|
| Two runs of one window differed by ~16pp; no dispersion was ever measured | **Guard 1:** a target with no measured noise floor cannot promote anything |
| Thirteen levers shipped inert and were scored as "no effect" | **Guard 2:** an unchanged observation stream marks a change INERT — "not testable as specified", never "no effect" |
| 717 of 723 candidates scored exactly +1.000, undetected for months | **Guard 3:** input-variance assertion — a field where ≥95% of samples share one value raises a *defect finding* instead of being learned from |
| Window-d's +20.54% is unattributable because it was a 4-way change | **Interlock:** one uncommitted lever per document at a time |
| 52 of the first 100 backtests used one window (W0), making it in-sample for everything | **Interlock:** an in-sample registry; a window used as evidence is never reused |
| A second backtest launch silently preempts the first | **Interlock:** single-flight lease, always yielding to human-initiated runs |

---

## 2. Architecture

Two pieces, split by purity, following the existing `engines/*.py` + pure-helper-module
idiom (`scheduler.py`, `nexus_telemetry.py`, `bot_decision_log.py` are all kept DB-free so
they unit-test without RethinkDB).

### 2.1 `backend/self_learning/` — pure, importable, no I/O in the core

| module | single responsibility |
|---|---|
| `observers.py` | normalize live ticks and finished backtests into typed `Observation` records |
| `findings.py` | the finding ledger — thread roots |
| `hypotheses.py` | immutable, content-hashed hypotheses (borrows `benchmark_alpha/records.py`'s identity discipline) |
| `experiments.py` | turn a hypothesis into a runnable design: windows, arms, repeats, acceptance rule |
| `noise.py` | measured dispersion floor per target — **Guard 1** |
| `execution_proof.py` | did the change actually fire — **Guard 2** |
| `variance.py` | input-variance assertion — **Guard 3** |
| `ladder.py` | rung definitions, promote/demote arithmetic, hysteresis |
| `permissions.py` | action-class × rung matrix, hold-for-live rule, timeout policy |
| `budget.py` | reserve-before-spend accounting against daily/monthly ceilings |
| `levers.py` | derive the tunable surface from declared strategy schemas |
| `actions/` | one adapter per action class; each implements `apply`, `rollback`, `prove_executed` |
| `venues/` | one adapter per venue; each implements `observe` and `write_config` |
| `llm.py` | the four AI roles, each resolving its own model id |

### 2.2 `backend/engines/self_learning_engine.py` — the daemon, and the only writer

One `EngineControl` document (`self_learning_engine`), started and stopped by a `server.py`
changefeed watcher exactly as `daily_digest_engine` and `discover_engine` are. Event-driven
via RethinkDB changefeeds (a backtest completes, a decision is logged, a position exits)
plus a slow heartbeat. No polling loop.

**One turn:** observe → attribute → hypothesize → design experiment → run → judge →
apply-or-rollback → notify → record.

**Hard boundary:** the engine never writes a strategy document directly. Every mutation
goes through an action adapter that owns its rollback token, and every live-tier mutation
additionally passes `live_kill_switch.py`. "Undo everything this ever did" is one
operation.

### 2.3 Strategy-agnosticism

Strategy-specific knowledge exists in exactly two declarative places.

**The lever surface is derived, not hardcoded.** `strategies_meta.py` already parses
`INTELLISTOCK_SCHEMA:` headers from all 29 strategy modules, yielding each tunable's name,
type and default. `levers.py` reads that to produce four generic lever classes:

- `config.<key>` — any declared tunable, typed from the schema
- `weight` — the ensemble mix across sub-strategies in a Strategies document
- `execution_position` — ordering
- membership — adding or removing a sub-strategy from the document's list

A Strategies document is `{id, name, strategies: [{strategy, weight, execution_position,
conditions, config}, …]}`, so all four classes apply uniformly to every strategy.

**Venue adapters** localize where observations come from and where writes go:

| adapter | reads | writes |
|---|---|---|
| `equity` | `BotTradeDecisions`, backtest logs, `BacktestResults` | Strategies document |
| `crypto` | crypto instance trades and equity series | `crypto_config` |
| `kalshi` | Kalshi fills and settlements | Kalshi instance config |

Everything above that boundary operates on one normalized `Observation` type and one
normalized `Lever` type. Adding a venue is one adapter, not a change to the loop.

**The LLM stays generic the same way:** the hypothesis generator receives the target
strategy's source file and declared schema as context, and reasons about four levers or
three hundred with no new code.

**Consequence:** the noise floor is measured per `(strategy, venue, window-class)`. RSI's
dispersion and Nexus-with-an-LLM's dispersion are different quantities, and a hypothesis is
only ever scored against its own target's floor.

**`window-class`** is the tuple `(bar granularity, window length bucket, market regime
label)` — e.g. `(3600s, 60d, bull)`. Dispersion is not constant across these: a 15-minute
run has more decision points than an hourly one, and a bear window has more forced exits.
Floors measured in one class are not transferable to another, and the system will not
substitute one for the other.

---

## 3. Data layer

**The grain is the decision, not the trade.** The highest-value findings in this project's
history all live in what the system *refused* to do: 100% of the 52 names that moved ≥30%
in one window were discovered and 0% were bought; 0 of 134 grants could clear the
min-position floor; 144 buys were refused for insufficient cash. Ordinary trade telemetry
records fills and is blind to all of it.

So the primary record is **every symbol the system formed a decision about** — decided,
sized, submitted, filled or not — and **each is resolved to a forward outcome whether or
not it was ever bought.** The counterfactual on the ones it declined is the mistake signal.

### 3.0 Where the data actually comes from — and why no strategy code changes

An earlier draft of this section described stages ("scored, ranked, granted") that are
Nexus vocabulary, not generic. Corrected: the decision record already exists, it is already
strategy-agnostic, and it is already written for every strategy.

`broker.py:17342` appends a per-symbol decision record on every backtest bar — in the
**common execution path**, not inside any strategy:

```
{timestamp, symbol, action, decision, normalized_score, override_applied,
 pre_override_action, pre_override_decision, primary_strategy,
 primary_action_intent, final_reason,
 strategies: [{strategy, decision, weight, reason}, …],
 post_decision: [...]}
```

It lands in `BacktestResults.backtest_decisions`. `BotTradeDecisions` is its live twin,
built by `bot_decision_log.primary_decision()` from the same `strategy_summary` shape and
indexed by `brokerage_id`.

| observation | source |
|---|---|
| what was decided, by which sub-strategy, with what reason | `BacktestResults.backtest_decisions` / `BotTradeDecisions` |
| what actually executed | `BacktestResults.backtest_trades` / broker fills |
| **what was decided BUY and never executed — the refusals** | the join: decided-BUY minus filled |
| equity path for attribution | `BacktestResults.portfolio_value_history` |

**Consequence: Phase 1 requires no changes to any strategy file.** The refusal set is
derivable from data already being written. This also means the subsystem inherits
strategy-agnosticism from the execution layer rather than having to enforce it.

`normalized_score` on that record is the immediate target of Guard 3 — running the variance
assertion over existing `BacktestResults` documents reproduces the "717 of 723 candidates
scored exactly +1.000" finding from history already on disk, with no new instrumentation.

### 3.0.1 CORRECTION — the gate refusals are NOT in the source table

An adversarial sweep of the Phase 1 implementation falsified the strongest form of the
claim above. It is recorded here rather than quietly fixed, because the difference decides
what the subsystem can honestly say.

`broker.py:17315` records a decision only when `not _trade_skipped_no_price`. That flag is
set at **eleven sites**, and they are not price gaps — they are refusal gates. The comment
at `broker.py:16820` states it outright:

```python
_log(f"SKIP BUY {symbol} — {_emp_what} < min ${_exec_min_pos:.0f} …")
_trade_skipped_no_price = True  # reuse flag to prevent recording
```

The eleven include the **min-position floor** (`:16820`) — the very refusal this design
cites as its motivating example — plus `max_positions` (`:17008`), the fundamental veto
(`:16189`), the satellite cap (`:16132`), the buying-power block (`:16742`), and the
min-hold sell block (`:17029`). A further set of gate `continue`s jump past the record
site entirely.

**Consequence:** `backtest_decisions` contains {all HOLDs} ∪ {orders that survived every
gate and reached submission}. The names refused AT A GATE are precisely the ones absent.

**What Phase 1 therefore measures**, honestly stated: `refusal_reason="unfilled"` means a
decision reached the execution path and no fill followed. That is a **lower bound** on
refusals, and `finding_from_funnel` says so in its own operator-facing text. Nothing in
the package describes its refusal count as complete.

**What closing the gap requires:** an emission at those eleven sites in `broker.py` —
the live order path. That is a change to the execution path, not an observation of it, and
it is deliberately **not** made in Phase 1. It is the first task of the Phase 2 plan and
needs an explicit decision, because it touches the file that places real orders.

**Two further corrections from the same sweep:**

1. **The execution join is an interval join, not an equality join.** Decision stamps are
   naive (`_parse_date`, `broker.py:9470`); fill stamps are aware-UTC one execution-delay
   later (`fill.executed_at` = `quote.timestamp`). String equality could never match on the
   equity path, so every buy read as a refusal and the flagship `buy_conversion` finding
   fired on every run as a guaranteed false positive. The join now matches on
   `(symbol, side)` with the fill at-or-after the decision, consumed once, within a bounded
   lag — and `funnel_summary` carries **join health** so a broken join reports itself as
   `join_failure` instead of masquerading as 0% conversion.
2. **Refusal *reason codes* are not structured.** Why a submitted order went unfilled lives
   only in run logs. Phase 1 records the refusal and leaves the reason `unfilled`. Log
   parsing is explicitly rejected as a source — the first sweep reader in this project was
   broken by a regex crossing a timestamp.

### 3.1 Tables

| table | grain | retention |
|---|---|---|
| `LearningObservations` | one row per (run, symbol, bar) decision: action, decision, `normalized_score`, per-sub-strategy votes, executed y/n, refusal reason (`unknown` in Phase 1) | **90d raw (configurable), then rolled up** |
| `LearningOutcomes` | forward return at 1/5/20 sessions plus SPY-relative excess, via `benchmark_alpha/outcomes.py`'s exchange-authoritative calendar — **including refused candidates** | rollups kept indefinitely |
| `LearningFindings` | thread root: what was observed, severity, target, status | forever |
| `LearningHypotheses` | immutable, content-hashed: claim, mechanism, levers, **predicted direction and magnitude** | forever |
| `LearningExperiments` | registered design: arms, windows, repeats, acceptance rule, floor used, budget reserved, run ids | forever |
| `LearningVerdicts` | statistical verdict, LLM judge opinion, final call, rung transition | forever |
| `LearningActions` | every mutation: document, key, from→to, rung, rollback token, execution-proof status, applied/rolled-back timestamps | forever |
| `LearningReports` | periodic and per-thread narrative artifacts | forever |
| `LearningNoiseFloor` | per (strategy, venue, window-class): repeat measurements, current estimate, sample count | forever |
| `LearningBudgetLedger` | every $ debit with cause | 1y |
| `LearningConfig` | single document: permissions matrix, budgets, model per AI role, ladder thresholds, document allowlist, retention | n/a |

Every downstream record carries `finding_id`, so a thread is one query and a rollback can
unwind an entire thread rather than an orphaned key.

### 3.2 Volume control

RethinkDB is already this deployment's bottleneck — `PriceHistory` at ~2.3M rows drove 17
restarts in 12 days on a memory-starved VM. Two rules keep this subsystem from becoming a
second elephant:

1. **Only `LearningObservations` is large, and it is the only table with a TTL.** Raw rows
   expire; daily rollups preserve the learning value permanently.
2. **Two write tiers.** Decision rows are written in full — the source
   (`backtest_decisions`) is already bounded to symbols that reached the trade loop, which
   is hundreds per run, not tens of thousands. Anything coarser (per-bar funnel counts,
   price-skip counts) is written as **per-bar aggregates, not rows**.

Because Phase 1's sources are documents that already exist, backfill over historical
`BacktestResults` is a read-only batch job — the subsystem has useful history on day one
rather than starting empty.

### 3.3 The three guards

**Guard 1 — noise floor.** Until a target has a measured floor, the system may not promote
anything on it; the only experiment it may run against that target is the dispersion
measurement itself (identical config, fresh salts, repeated runs). Acceptance requires the
effect to exceed the floor by a margin **and** hold its sign across repeats.

**Guard 2 — execution proof.** A treatment whose observation stream is byte-identical to
control is marked `INERT` and its hypothesis closes as *"not testable as specified"* —
never as *"no effect"*. The action adapter's `prove_executed` supplies the positive
evidence (a differing decision, a named counter, a log line).

**Guard 3 — input variance.** Before the learner consumes any scored field it asserts the
field varies. If ≥95% of samples take one value it raises a **defect finding** rather than
learning from it.

---

## 4. Autonomy machinery

### 4.1 The ladder

| rung | what it proves | cost |
|---|---|---|
| `PROPOSED` | hypothesis pre-registered with predicted direction and magnitude, before any run | free |
| `BACKTEST` | paired A/B across multiple windows × repeats; clears the target's own floor and holds sign | $ per run |
| `SHADOW` | `shadow.py` virtual portfolio on live quotes, no broker surface | free |
| `PAPER` | a real paper instance, N sessions, control-relative | free |
| `LIVE_CAPPED` | real money on a bounded book, watched for N sessions | real |
| `LIVE_FULL` | applied to the primary live document | real |

**Default constants** (all operator-editable in `LearningConfig`): `BACKTEST` = 4 windows ×
2 repeats, requiring pass on ≥3 of 4; `SHADOW` = 10 sessions; `PAPER` = 20 sessions;
`LIVE_CAPPED` = 20 sessions; demotion after `D = 3` consecutive sub-bar evaluations.

**Acceptance rule form** (constants calibration-pending, see §9): a rung passes when the
mean paired effect exceeds `floor × margin` *and* the effect holds its sign in at least
`sign_k` of the repeats. Proposed starting values `margin = 1.5`, `sign_k = all repeats`.
The *form* is fixed and implementable now; only the two constants await a real measured
floor to calibrate against.

### 4.1.1 What `LIVE_CAPPED` actually means — operational requirement

Most levers cannot be partially applied to a single book: you cannot run
`max_positions=6` on 5% of a portfolio. So `LIVE_CAPPED` is **not** a scaled-down version of
the change on the main book. It is the treatment config running on a **separate, small,
real-money instance** alongside the primary instance running control.

That requires a second funded live account or sub-account, sized to the cap (e.g. 5% of
total live capital). **This is an operational prerequisite for the live rungs and does not
exist today** — the current live footprint is a single instance, `alpaca-main` on document
179.

Three options, to be decided before Phase 5 (see §9):

1. Fund a second small live account; `LIVE_CAPPED` runs there. Cleanest, costs capital and
   an account.
2. Collapse `LIVE_CAPPED` into `LIVE_FULL` and rely on `PAPER` plus the automatic breaker
   as the last line before full live. Cheapest, removes a real rung.
3. Restrict `LIVE_CAPPED` to the subset of levers that *are* per-position scalable (sizing
   and threshold levers), and route structural levers straight from `PAPER` to `LIVE_FULL`.

Phases 1–4 are unaffected by this decision.

This ladder is explicitly a **different, faster tier** than `benchmark_alpha/promotion.py`'s
38-condition gate, which requires ≥24 months of forward-only point-in-time capture and
currently reports 0/6 (state RESEARCH). For the live rungs, a change is gated by whichever
tier is stricter for its action class; the two are never conflated in the UI.

### 4.2 Demotion is asymmetric

Permission modes govern **applying** changes. Rolling one back is a safety action that
always executes immediately, without asking, at any hour. If realized effect falls below the
bar for D consecutive evaluations, the thread demotes one rung and reverts via its rollback
token.

### 4.3 Permissions

Action class × rung, all editable in the tab. Defaults:

| action class | ≤ PAPER | LIVE_CAPPED | LIVE_FULL |
|---|---|---|---|
| config levers | autonomous | ask | ask |
| LLM model / prompt | autonomous | ask | ask |
| universe / entry timing | autonomous | ask | ask |
| strategy code | ask | ask | ask |

Unanswered "ask" at sub-live rungs auto-proceeds after an operator-set timeout. At live
rungs it holds indefinitely and escalates (push → Discord → daily digest).

### 4.4 Interlocks

1. **One uncommitted lever per document.**
2. **In-sample registry** — a window used as evidence is never reused; W0
   (2026-01-01..2026-03-01) is permanently blacklisted.
3. **Single-flight backtest lease**, always yielding to human-initiated runs.

### 4.5 Budget

Daily and monthly $ ceilings. An experiment **reserves** its estimated cost before launching
and cannot start if the reserve exceeds the remainder. LLM spend prices through the existing
`llm_pricing.yaml` and `llm_telemetry.py`. Hard stop at zero with a notification.

### 4.6 Kill switch, three levels

1. `EngineControl.running=False` — halts the loop, changes nothing.
2. **Revert live tier** — rolls back every live-rung action.
3. **Revert all** — unwinds everything the subsystem has ever applied.

Plus an automatic breaker: if drawdown attributable to learning actions breaches a limit,
the live tier self-reverts and pages the operator. Automatic reverts are never
permission-gated.

### 4.7 Document allowlist

`LearningConfig` holds an explicit allowlist of documents the subsystem may write to. It
ships **empty**; arming a document (including `alpaca-main`) is one switch. This is a
bootstrap requirement rather than a policy veto: on day one no target has a measured noise
floor, so Guard 1 leaves nothing promotable regardless.

### 4.8 Notifications

New types registered in `notification_types.py`, which per its own contract makes them
appear in the settings UIs and become routable automatically:

`learning_finding`, `learning_proposal`, `learning_applied`, `learning_demoted`,
`learning_inert`, `learning_budget`.

Approvals are actionable from the web tab, mobile, and Discord (`!learning approve <id>`).

---

## 5. AI layer

Four roles, each with its **own model id** configured in the tab and resolved through the
existing `Models` table and `model_resolver.py`.

| role | input | output |
|---|---|---|
| **Analyst** | rolled-up observations and outcomes for a period | structured findings and the prose feed |
| **Hypothesis generator** | findings, the target strategy's source and declared schema, lever surface, prior ledger **including rejections**, noise floor | typed hypothesis: claim, mechanism, levers, predicted direction and magnitude |
| **Code writer** | a confirmed hypothesis needing code | branch, diff, tests; ships default-OFF behind a flag with its own log line |
| **Judge** | the pre-registered prediction and the measured result | veto / hold / demote / confirm |

Three properties that matter more than model choice:

- **Every LLM output is schema-constrained and recorded** with model id, prompt hash and
  cost, making a bad model attributable and the models themselves A/B-able.
- **The judge never sees the generator's reasoning** — only the pre-registered prediction
  and the outcome, in a separate call chain. The statistical verdict is a floor the judge
  can only be more conservative than; it can veto a promotion but never manufacture one.
- **Rejected hypotheses stay in the generator's context**, so disproved ideas are not
  re-proposed.

### Code-writer specifics

Because the backend auto-deploys from `main`, a merged code change deploys. Therefore the
code adapter's rollback token is a revert commit, code changes ship default-OFF behind a
flag with their own log line (repo convention), and no branch merges until it has cleared
the ladder. The generated suite must leave the baseline failure set unchanged.

---

## 6. User interface

### 6.1 Web — `/learning`, `LearningView.vue`

Header: engine state, budget remaining today and this month, kill switch.

**Three primary sections, mirrored on mobile:**

1. **Pending approvals.** One card per proposal: intended action, target document, rung,
   evidence, predicted vs measured, approve/reject. Live-rung items pinned and visually
   distinct, since those hold indefinitely.
2. **Findings & reports.** Every finding with status and narrative report, filterable by
   strategy, venue, severity, status. Reports are readable artifacts (daily, weekly,
   per-thread).
3. **Thread detail — the ladder stepper.** A finding's full life as a vertical stepper down
   the rungs, each step carrying its evidence, cost, run ids, and a revert control:

```
● FINDING #142 — "0 of 134 grants clear the min-position floor"
│  detected 2026-08-15 · nexus / alpaca-main · severity HIGH
│
├─● PROPOSED        hypothesis: clamp weight to design_share/max_positions
│                   predicts +2 to +6pp · levers: sizing_respects_satellite_share
│                   author: claude-opus-5 · 2026-08-15 14:02
│
├─● BACKTEST        4 windows × 2 repeats · floor 4.1pp · measured +5.9pp, sign held 4/4
│  │                PASS · $2.80 · runs 333727, 559934, 866880, 235194
│  └─ proof: 11 distinct funded sizes vs 1 in control → NOT INERT ✓
│
├─● SHADOW          12 sessions · +1.2pp vs control · PASS
│
├─● PAPER           20 sessions · +0.4pp · below floor · HELD
│  └─ judge: "shadow gain not reproduced on paper; hold, do not demote yet"
│
├─○ LIVE_CAPPED     ⏸ awaiting approval · notified 2026-08-15 21:40
│
└─○ LIVE_FULL       locked
```

The stepper is the audit trail: where a thread died and why is visible at a glance, and a
thread that never left `BACKTEST` because it proved inert says so on its face.

Additional panels: hypothesis ledger, actions log with one-click revert, and a
**noise-floor panel** stating plainly which targets have no measured floor and therefore
cannot yet be trusted. Settings: permissions matrix, budgets, model per role, ladder
thresholds, document allowlist, retention.

### 6.2 Mobile — `mobile/lib/features/learning/`

The five bottom-tab slots are occupied (dashboard, kalshi, instances, strategies, more), so
the tab lives in the **More sheet** — the same treatment Backtests received — plus a
dashboard card showing pending approvals, budget remaining, and the latest finding. Push
notifications deep-link into approval detail.

Mobile carries all three primary sections, including the stepper (already a natural mobile
shape). Scope: read, approve/reject, revert, kill switch. Form-heavy settings remain
web-only.

---

## 7. Testing

The repo's expensively-learned rule holds: **never write a test that re-implements the logic
it tests.** Two files in this codebase stayed green over live defects by doing exactly that
and were deleted.

- **Pure modules** (`noise`, `ladder`, `permissions`, `budget`, `execution_proof`,
  `variance`, `levers`) get unit tests calling production functions with fixtures built from
  **real document shapes**. A fixture that puts a key at base level when real documents carry
  it in `regime_profiles` cannot catch the bug that lives there.
- **Action adapters** get a contract test: `apply` → `prove_executed` → `rollback` returns
  the document to its original bytes.
- **Venue adapters** get a contract test against recorded fixtures from each venue.
- **The engine** gets an integration test against a fake changefeed.
- An adversarial findings test file in the existing house style.

Baseline is **19 pre-existing failures** (`test_adv_exit_discipline_findings` ×11,
`test_core_sleeve_adversarial` ×7, `test_zz_adversarial_sweep` ×1). Compare the failure
**set**, not the count. The new suite must not add to it.

---

## 8. Phasing

Five slices, each independently useful, each getting its own implementation plan.

| phase | contents | standalone value |
|---|---|---|
| **1. Observe** | data layer, venue adapters, outcome resolution, variance assertion, retention; read-only tab | the refusal-counterfactual data exists for the first time, and the constant-signal class of defect is reported automatically |
| **2. Measure** | noise floor, execution proof, experiment registry, single-flight lease | the dispersion commitment becomes structural instead of forgotten |
| **3. Hypothesize** | analyst, generator, finding threads, ledger, approval queue, notifications | proposals with evidence, no writes |
| **4. Act** | action adapters, ladder, permissions, rollback, budget, kill switch | autonomous through PAPER |
| **5. Live + code** | live rungs, code-writer adapter, automatic breaker | full autonomy as specified |

---

## 9. Open items for implementation planning

- **`LIVE_CAPPED` mechanism (§4.1.1) — needs an operator decision before Phase 5.** Fund a
  second small live account, collapse the rung, or restrict it to scalable levers. Blocks
  nothing before Phase 5.
- **Acceptance constants** `margin` and `sign_k` (§4.1) — the rule's form is fixed; the two
  numbers get calibrated in Phase 2 against the first real measured floor.
- Backtest cost estimation model for `budget.py` reservations. Bootstrap from the observed
  ~$0.70/run in recent sweeps, then learn per-target from `LearningBudgetLedger`.
- Retention default for `LearningObservations` (proposed 90d).
- Whether `LearningReports` renders to Discord digests via the existing
  `daily_digest_engine` or stays in-app.
- Whether the crypto and Kalshi venue adapters land in Phase 1 or follow after equity. The
  design supports all three; sequencing is a scope call for the Phase 1 plan.
