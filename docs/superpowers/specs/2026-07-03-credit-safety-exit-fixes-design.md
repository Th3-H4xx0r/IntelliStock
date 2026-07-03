# Credit Safety, Exit Layer & Metrics Integrity — Fix Round 2

**Date:** 2026-07-03
**Status:** Approved (user, 2026-07-03), Track 5 pending P&L-hunter findings
**Predecessor:** `2026-07-02-live-underperformance-fix-design.md` (fix round 1, shipped as PRs #81/#82/#83)

## 1. Problem

Backtest 586767 (June-2026 replay, tuned config, Nemotron, fixed-code image) exposed five
new defects — three of them invalidate the run as a baseline and two are live-money risks:

1. **OpenRouter credits ran out mid-run and nothing noticed.** 195 calls failed with
   HTTP 402 from 19:11Z onward; the ENTIRE June trade simulation (19:16–19:42Z) ran
   LLM-blind (no overlay on 100% of rows, active-event set decayed to empty from 06-03,
   only Bedrock company sentiment surviving). The run produced a full month of misleading
   results with zero alerts. User's account: $10 top-up (06-29) → −$0.01.
2. **~$6.71 of ~$9.25 real spend was invisible** on the cost dashboard ($2.54 recorded):
   calls succeeding through the PydanticAI-native structured path record NO usage row
   (known PR #82 gap); only plain-path and raw-JSON-fallback calls record.
3. **PR #83's blanket `max_tokens=32768` created a hard credit cliff:** OpenRouter's
   preflight requires affording max_tokens×output-price (~$1.30 for Nemotron), so once
   balance < that, EVERY call 402s ("can only afford 10773" × 195) even though real calls
   cost ~$0.01.
4. **The V31 14-day grace period vetoes ALL risk exits.** Verified: `V31 grace period
   (day 3/14): suppressed [Fast loser cut: −10.x%]`; zero protective sells in the whole
   run (and zero EVER live); AVGO breached −10% three straight closes, exited −16.88% by
   sentiment. Median hold 10d < grace 14d → the exit layer effectively does not exist.
   Round-1's regression tests missed it because fixtures didn't include the grace config
   (config-vs-default divergence).
5. **Metrics integrity:** (a) headline P&L −2.32% vs equity-curve +0.44% — the summary
   re-marks open positions against DUPLICATE end-date bars (two 2026-07-01 closes per
   symbol; equity curve uses one, `_get_prices_at_time` picks the other, ≈$2,756 lower);
   the truthful number is +0.44%. (b) `(unset)` call-site on $0.60 of spend — overlay
   calls run in a ThreadPoolExecutor without `llm_call_context`; `attribution_keys` only
   feeds the exception, never telemetry. (c) Outcomes forward-tracking inert:
   entry_price now writes (877/877) but latest/max/min returns stay 0 and
   latest_observation_date never advances. (d) `max_positions=10` not enforced (peaked
   13, rotation overlap).

Also verified WORKING (round-1 fixes): sentiment healthy all 108 days (no token-limit
errors), outcomes entry_price populated, usage rows for plain/fallback paths with
accurate token accounting (Nemotron genuinely burns ~99.7% of completion on reasoning),
8% halt armed and correctly not triggered (−6.11% max from peak).

## 2. Goals

- No LLM-blind trading, ever: credit exhaustion halts work loudly instead of degrading it.
- The cost dashboard equals the OpenRouter bill.
- Risk exits actually protect positions, in backtest and live, with tests pinned to the
  REAL live config values.
- One truthful P&L number per backtest.
- (Track 5) Adopt the highest-confidence P&L levers surfaced by the three opportunity
  hunters (trade economics / config archaeology / execution & cadence).

## 3. Non-goals

- Enabling nexus_ml, analyst panel, or margin sizing without Track-5 evidence.
- UI redesigns; Benzinga renewal (user action); OpenRouter key rotation (user action).

## 4. Design

### Track 1 — Credit safety

**1a. Record PydanticAI-native structured successes.** In the structured-call success
path (llm_utils.py, where `result.usage()` is already read at ~:246), call the same
`_safe_record` used by `_call_openrouter`: provider/model/tokens (input, output,
reasoning subset)/duration/ok=True. Cost: PydanticAI does not expose OpenRouter's
`usage.cost` envelope → price from the model registry when available, else record
tokens with cost_source="registry_missing" — NEVER drop the row. All providers routed
through the PydanticAI path benefit, closing the $6.71-class hole.

**1b. 402 = critical.** `llm_critical_guard.classify`: new class `insufficient_credits`
(HTTP 402 or body matching `requires more credits|can only afford`), immediately fatal,
role-independent (unlike article-role degrade — no role can run without credits).
Live: halts instance (existing kill-switch path) with halt_reason
"LLM critical: insufficient_credits" + Discord/push. Backtest: aborts the run with
status="paused_credits" (resumable) + Discord/push; must NOT keep simulating days.

**1c. Preflight balance guard.** New `backend/openrouter_credits.py`:
`get_balance(api_key) -> float|None` (GET /api/v1/credits, 5s timeout, None on any
error — the guard degrades to reactive-402 mode, never blocks on its own failure).
Checked at: live FULL-run start, backtest start AND every N simulated days (N=5).
Config keys (doc-179, code defaults): `openrouter_low_credit_warn_usd=3.0` (one
warning notification per process per threshold-cross), `openrouter_halt_credit_usd=0.5`
(same critical path as 1b, but proactive).

**1d. De-cliff max_tokens.** In `_call_openrouter` + the structured settings path: on
HTTP 402 whose body carries "can only afford N", retry ONCE with
`max_tokens=max(2048, N−512)`; if the retry also 402s → escalate per 1b. Additionally,
when a preflight balance is known and < (32768 × price), pre-clamp instead of relying
on the 402 round-trip.

### Track 2 — Exit layer

**2a. Risk cuts bypass V31 grace.** In `_evaluate_position_risk`'s grace gate (the
`V31 grace period (day x/y): suppressed [...]` writer): grace continues to suppress
SIGNAL-driven sells but must not suppress fast_loser_cut, circuit-breaker
(max_open_loss), trailing-stop, or hold-limit exits. Regression tests MUST build config
from the actual doc-179 inner values (grace 14d active, fast_loser −10, circuit −15)
— add a fixture helper that loads the committed pre-tune snapshot JSON so
config-vs-default divergence can't blind tests again. Include the AVGO scenario
(−13.79% on day 3 of grace → cut fires) and the suppressed-hold inverse for a plain
negative-signal sell inside grace.

**2b. Enforce max_positions.** Find why 13 concurrent positions existed with
max_positions=10 (rotation buy executing before the funding sell settles / backfill
queue bypass). Enforcement: a hard gate at order-emission time — no NEW-name buy when
current position count ≥ max_positions (rotation pairs count net). Log + context-reason
when the gate blocks.

### Track 3 — Metrics integrity

**3a. One truthful P&L.** broker.py backtest wrap-up (~:7102): derive `final_value`
from the last portfolio snapshot's own marks (`get_portfolio_value(snapshots[-1].prices)`
or the snapshot value itself) so row `pnl`/`pnl_percent` equals the equity curve by
construction. ALSO fix the duplicate end-date bar seeding (~:7042-7099) so `data`
carries one close per symbol per day (root cause).

**3b. Overlay attribution.** Wrap `_apply_trade_overlay` / `_apply_etf_trade_overlay`
worker bodies in `with llm_call_context(call_site="overlay"/"overlay_etf", ...)` (the
thread-pool workers have no ambient context). Keep `attribution_keys` for exceptions.

**3c. Outcomes forward-tracking.** Root-cause why `_update_indefinite_outcomes` leaves
latest/max/min returns at 0 with observation date == entry date (suspects: prices dict
again, scope filter, or it never runs in the backtest loop) and fix; also populate
`action_intent` (870/877 'unknown').

### Track 4 — Valid baseline rerun

After deploy + user tops up credits: rerun the June-2026 replay (same params). This run
— with credits, working exits, true P&L — is the first valid Nemotron baseline. Compare
against SPY and live June; THEN decide whether a model A/B is warranted.

### Track 5 — P&L levers (pending hunter reports)

Three Opus agents are mining trade economics, config archaeology (incl. dead keys +
LIVE_OVERRIDES clobbers), and execution/cadence (open-auction drag, reserve stacking,
order sequencing). Their ranked levers get appended here; adopt the top items whose
evidence is strong and risk is low; config-only levers ship in the same apply-script
pattern as the 2026-07 tune. This section will be finalized before the implementation
plan is written.

## 5. Testing

- Test-hygiene rules from round 1 (autouse cages; no prod side effects) are mandatory.
- Track-2 tests pinned to real doc-179 values via the committed snapshot fixture.
- 402/preflight paths: caged requests mocks incl. the exact OpenRouter 402 body.
- P&L fix: regression test constructing duplicate end-date bars and asserting
  summary == equity-curve final.

## 6. Risks

- 1b live-halt on 402 is deliberate fail-closed: a credit outage stops trading rather
  than trading blind. Mitigated by 1c warnings at $3.
- 2a changes live exit behavior: the change direction is protective (more exits), but a
  false-positive cut costs real P&L — tests pin thresholds; grace still governs signal
  sells.
- 3a changes reported backtest P&L for future runs (historical rows untouched).

## 7. User actions

- Top up OpenRouter credits (Auto Top-Up recommended) BEFORE live restart or rerun.
- Rotate the exposed OpenRouter key.
