# Credit Safety, Exit Layer & P&L Levers — Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make credit exhaustion loud and safe, make risk exits real, make backtest P&L truthful, and ship the evidence-backed P&L levers (rotation graph-gate, concentration, overrides) from the three hunter reports.

**Architecture:** Code changes land in `backend/llm_utils.py` (usage recording, 402 handling), `backend/llm_critical_guard.py` + `backend/live_critical_abort.py` (new critical class), new `backend/openrouter_credits.py` (balance guard), `backend/strategies/graph_nexus_analysis.py` (grace bypass, rotation gate, position caps, overlay attribution, outcomes tracking), `backend/broker.py` (P&L mark, max_positions gate, sell-proceeds crediting, backtest abort), `backend/live_mode_overrides.py` (two more user-overridable keys). Config levers + dead-secret scrub ship via a new one-shot apply script (dry-run default, `--apply` USER-GATED). Spec: `docs/superpowers/specs/2026-07-03-credit-safety-exit-fixes-design.md`.

**Tech Stack:** Python 3 (`python3`), pytest (`backend/tests/`), RethinkDB prod via repo-root `.env`, OpenRouter API.

## Global Constraints

- **Real money adjacent:** graph_nexus_analysis.py / broker.py / live_mode_overrides.py run the live instance. Surgical edits only. NO prod-DB writes outside the Task-13 script's `--apply` (which nobody runs this session).
- **Test hygiene (two prior incidents):** every test file that can reach alerts/notify/kill-switch/RethinkDB gets an AUTOUSE cage fixture stubbing those seams (pattern: `backend/tests/test_llm_critical_role_scope.py::cage_alerts`). No network, no prod DB, no real Discord.
- **Real-config-pinned tests:** Track-2/5 strategy tests must build config from the committed snapshot `docs/superpowers/specs/2026-07-02-strategy-179-pre-tune-snapshot.json` via the Task-5 fixture helper, overlaid with the 2026-07 tune values where relevant (grace stays 14d) — never bare fixture defaults for keys the test's behavior depends on.
- **GitNexus (CLAUDE.md):** `mcp__gitnexus__impact` (upstream) on each symbol BEFORE editing; `mcp__gitnexus__detect_changes` before every commit; report blast radius; warn on HIGH/CRITICAL. Load via ToolSearch `select:mcp__gitnexus__impact,mcp__gitnexus__detect_changes`.
- Tests: `python3 -m pytest backend/tests/<file> -v` from repo root; socketio stub `sys.modules.setdefault("socketio", MagicMock())` before importing instance/broker modules.
- Commit trailer verbatim last line: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Known pre-existing suite failures (do not chase): test_claude_cli_provider (15), test_live_calendar (2), test_models_api_ollama (1), test_nexus_v25 profit-take-once (1).
- Key anchors (verified 2026-07-03, may drift ±lines): native usage read `llm_utils.py:246`; `_safe_record` + `_call_openrouter` ~:4586-4740; max_tokens default `_openrouter_effective_max_output_tokens` ~:1581-1614; `classify` `llm_critical_guard.py:63-110`; grace suppression writer emits `V31 grace period (day x/y): suppressed [...]` in `_evaluate_position_risk` (GNA ~:16415-16879); P&L wrap-up `broker.py:7102-7118`, dup-bar seeding `broker.py:7042-7099`, `_get_prices_at_time` `broker.py:6210-6240`; overlay workers GNA `:18135/:18258/:18335`, thread pool `:18618`; `llm_call_context` in `backend/llm_telemetry.py` (`_merge_active_ctx` :228); outcomes updater `_update_indefinite_outcomes` GNA `:9252-9316`; sell-first ordering `broker.py:8619-8631`, cached cash read `:8706`, earnings proceeds-credit pattern `:7519`; sizing `broker.py:9145-9176`; LIVE_OVERRIDES `backend/live_mode_overrides.py` (`_USER_OVERRIDABLE_KEYS`).

---

### Task 1: Record PydanticAI-native structured usage (spec 1a)

**Files:**
- Modify: `backend/llm_utils.py` (structured success path where `result.usage()` is read, ~:246 region and/or the structured-call wrapper that owns provider/model context)
- Test: `backend/tests/test_native_structured_usage.py` (create)

**Interfaces:**
- Consumes: existing `_safe_record(provider=, model=, usage=, ok=, duration_ms=, retry_count=, error=, cost_usd_override=, model_id=)`; `record_llm_call` pricing-registry fallback (cost_source="models_override" path).
- Produces: every PydanticAI-native structured SUCCESS records one usage row: provider, model, `usage={"input_tokens":…, "output_tokens":…, "reasoning_tokens":…}`, ok=True, `cost_usd_override=None` (registry/zero pricing; PydanticAI has no cost envelope). This is the $6.71-invisible-spend fix.

- [ ] **Step 1: Locate the exact seam.** Read `llm_utils.py:200-320` (the `result.usage()` consumer) and the structured wrapper (`_call_structured_llm*` family) to find where a SUCCESSFUL native run returns with provider/model/timing in scope. Confirm which providers route through it (openrouter + azure + others). Run gitnexus impact on the function you'll touch.
- [ ] **Step 2: Failing tests** — caged (no DB/network): fake a PydanticAI result object with `usage` (`request_tokens`/`response_tokens`/`details` per pydantic-ai 1.0.18 — check the real attr names on the installed version with `python3 -c "from pydantic_ai.usage import Usage; print(Usage.__dataclass_fields__ if hasattr(Usage,'__dataclass_fields__') else dir(Usage))"`), monkeypatch `_safe_record` with a recorder, call the seam function, assert one row with correct provider/model/tokens/ok=True. Second test: recording failure must not break the call (exception-safe). Third: the raw-fallback path does NOT double-record when native succeeds (one row per HTTP call remains the invariant — a native success is one call → one row).
- [ ] **Step 3: RED** `python3 -m pytest backend/tests/test_native_structured_usage.py -v`
- [ ] **Step 4: Implement** — extract tokens from the PydanticAI usage object (map request→input, response→output, details reasoning if present), wrap the whole recording in try/except, call `_safe_record`. Do NOT touch the failure paths (already recorded via LLMCriticalFailure flow) — guard against double-record there.
- [ ] **Step 5: GREEN** new file + `backend/tests/test_openrouter_usage_tracking.py` + `backend/tests/test_openrouter_max_tokens.py` (must stay green).
- [ ] **Step 6: detect_changes, commit** `fix(telemetry): record PydanticAI-native structured successes — most Nemotron spend was invisible`

---

### Task 2: 402 = insufficient_credits critical class (spec 1b)

**Files:**
- Modify: `backend/llm_critical_guard.py` (classify :63-110, `is_immediately_fatal` :143-145)
- Modify: `backend/live_critical_abort.py` (handle — 402 is role-INDEPENDENT fatal: bypasses the article-role degrade)
- Test: `backend/tests/test_insufficient_credits_critical.py` (create; reuse the autouse cage from test_llm_critical_role_scope.py verbatim incl. kill-switch seams)

**Interfaces:**
- Consumes: `LLMCriticalFailure(class_tag=…, provider=…, model=…, role=…)`; `role_is_halt_worthy(role)`.
- Produces: `classify` returns class_tag `"insufficient_credits"` for HTTP 402 or body matching `re.compile(r"requires more credits|can only afford", re.I)`; `is_immediately_fatal` includes it; `handle()` treats it as halt-worthy REGARDLESS of role (article roles included — nothing runs without credits): live → existing halt path with halt_reason `"LLM critical: insufficient_credits"` + alert; the alert body must say "OpenRouter credits exhausted — top up at openrouter.ai/settings/credits".

- [ ] **Step 1:** Read classify + the strategy-side catch sites (GNA company/macro catches from round 1 — they check `role_is_halt_worthy`; an article-role 402 must RE-RAISE, not degrade). Decide the cleanest override: `role_is_halt_worthy` stays role-based; add `failure_is_role_independent(failure) -> bool` (True for insufficient_credits) checked BEFORE the role check at every degrade site (live_critical_abort.handle + the two GNA catch sites).
- [ ] **Step 2: Failing tests:** (a) classify 402 → insufficient_credits, immediately fatal; (b) handle() with role="macro_article" + insufficient_credits → HALT (not degrade), alert fired once via cage; (c) auth_failure with article role still degrades (round-1 behavior preserved); (d) body-regex variant without status code.
- [ ] **Step 3-4: RED → implement → GREEN** (+ `test_llm_critical_role_scope.py` stays green — its degrade tests must not regress).
- [ ] **Step 5:** GNA catch sites: add the `failure_is_role_independent` check (import from llm_critical_guard) before the degrade branch. detect_changes, commit `fix(llm-guard): 402 insufficient-credits is role-independent fatal — never trade LLM-blind`

---

### Task 3: Backtest pause on critical LLM failure (spec 1b, backtest side)

**Files:**
- Modify: `backend/broker.py` (the backtest per-day loop — find where `run_run_once_strategies` exceptions propagate in backtest mode; the June sim previously kept looping through 30 days of 402s)
- Test: `backend/tests/test_backtest_credit_pause.py` (create)

**Interfaces:**
- Produces: when `LLMCriticalFailure` (any immediately-fatal class) escapes a backtest simulation day, the run STOPS: BacktestResults row updated `{status: "paused_credits", error: "<class_tag>: <human message>", paused_at_date: <sim date>}`, partial results preserved, Discord/push alert fired once (`alert_strategy_error` seam), process exits cleanly (no further sim days). Resumability = re-queue (the existing resume-date query skips processed days).

- [ ] **Step 1:** Trace the backtest day loop: `grep -n "Historic lookback progress\|for.*trading_days\|BACKTEST" backend/broker.py | head -30`, then read the sim-day loop and its exception handling. Determine why 402s did NOT propagate (llm calls caught somewhere → empty results returned). The fix point: LLMCriticalFailure is a BaseException — verify the loop doesn't `except BaseException`/bare-except it; if the strategy-side catches degrade it (Task 2 fixed article roles; decision roles re-raise), the loop just needs a `except LLMCriticalFailure` → pause handler.
- [ ] **Step 2: Failing test:** simulate the loop handler with a fake results-row writer + caged alerts: raise LLMCriticalFailure(insufficient_credits) from a stubbed run-once inside the extracted/patched loop body → assert status update payload + alert + loop termination. (Extract a `_pause_backtest_on_credit_exhaustion(rrow_id, failure, sim_date, conn)` helper for testability.)
- [ ] **Step 3-4: RED → implement → GREEN.**
- [ ] **Step 5:** detect_changes, commit `fix(backtest): pause with status=paused_credits on critical LLM failure instead of simulating blind`

---

### Task 4: Preflight balance guard + 402 de-cliff (spec 1c, 1d)

**Files:**
- Create: `backend/openrouter_credits.py`
- Modify: `backend/llm_utils.py` (`_call_openrouter` 402 handling; `_openrouter_effective_max_output_tokens` pre-clamp), `backend/broker.py` (guard call at live FULL start + backtest start/every 5 sim days — co-locate with the Task-3 loop)
- Test: `backend/tests/test_openrouter_credits_guard.py` (create)

**Interfaces:**
- Produces: `get_balance(api_key, timeout=5.0) -> float | None` (GET `https://openrouter.ai/api/v1/credits`, returns `total_credits - total_usage`; None on ANY error). `check_credit_guard(api_key, config, notify_fn) -> "ok"|"warn"|"halt"` using config keys `openrouter_low_credit_warn_usd` (default 3.0) / `openrouter_halt_credit_usd` (default 0.5), one-shot warn latch per process. De-cliff in `_call_openrouter`: on 402 whose body matches `can only afford (\d+)`, retry ONCE with `max_tokens=max(2048, N-512)`; second 402 → normal terminal failure (feeds Task-2 classify). Pre-clamp: when a cached balance is known and `< 32768 × price`, clamp the default max_tokens proportionally (only when balance-fetch succeeded).

- [ ] **Step 1:** Verify the credits endpoint shape (`curl -s https://openrouter.ai/api/v1/credits -H "Authorization: Bearer $KEY"` — get the key from env/config at runtime, NEVER print it; the response is `{"data":{"total_credits":…,"total_usage":…}}` — confirm against docs/response).
- [ ] **Step 2: Failing tests (all caged, requests mocked):** balance math; None-on-error (timeout, 500, bad JSON); guard thresholds (2.9→warn once, second call no re-warn; 0.4→halt); de-cliff retry (mock 402 body with "can only afford 10773" → second request carries max_tokens=10261 → 200 ok recorded); double-402 → terminal.
- [ ] **Step 3-4: RED → implement → GREEN** (+ usage-tracking + max-tokens suites green).
- [ ] **Step 5:** Wire guard calls: live FULL-run start (broker.py, near the FULL dispatch) and backtest start + every 5 sim days (Task-3 loop site) — `halt` result routes into the Task-2/3 critical paths. detect_changes, commit `feat(llm): OpenRouter credit guard (warn $3 / halt $0.50) + 402 affordability de-cliff`

---

### Task 5: Real-config test fixture (prereq for Tracks 2/5)

**Files:**
- Create: `backend/tests/nexus_real_config.py` (helper, not a test)
- Test: `backend/tests/test_nexus_real_config.py` (create, trivial)

**Interfaces:**
- Produces: `def real_config(**overrides) -> dict` — loads `docs/superpowers/specs/2026-07-02-strategy-179-pre-tune-snapshot.json`, takes `["strategies"][0]["config"]`, overlays the 2026-07 tune deltas (`{"portfolio_drawdown_halt_pct": 8, "profitable_min_hold_conviction_override_enabled": False, "new_entry_reserved_budget_pct": 0.1, "cash_reserve_floor_pct": 0.02, "allocation_max_new_stock_buys": 10, "max_propagated_scoring_slots": 40, "max_positions": 10, "rotation_break_glass_delta": 2.5, "rotation_break_glass_raw_score": 3.5, "rotation_profitable_min_incoming_raw_score": 2.0}`), strips every key containing `key|secret|password|token` (defense in depth), then applies `overrides`. This makes strategy tests run against the REAL live config so config-vs-default divergence (the V31-grace blind spot) can't recur.

- [ ] **Step 1:** Write helper + test asserting: grace-related keys present as in live (find the actual grace key names first: `grep -n "grace" backend/strategies/graph_nexus_analysis.py | grep -i "config\|get(" | head -20` — record the exact keys and their live values in the helper's docstring), `fast_loser_cut_pct == -10`, no secret-like keys, overrides win.
- [ ] **Step 2: GREEN, commit** `test(nexus): real-config fixture from committed doc-179 snapshot`

---

### Task 6: Risk exits bypass V31 grace (spec 2a)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (the grace gate inside `_evaluate_position_risk` that logs `V31 grace period (day x/y): suppressed [<reason>]`)
- Test: extend `backend/tests/test_nexus_evaluate_position_risk.py`

**Interfaces:**
- Consumes: `real_config(**overrides)` from Task 5; existing `_Emu` fixture pattern.
- Produces: grace suppression applies ONLY to signal-driven sells. Risk-exit reasons (`Fast loser cut`, `Circuit breaker`, `Trailing stop`, `Hold-limit`) are exempt: when the pre-grace decision is one of these, the sell survives grace untouched (score −1, reason preserved, `_forced_exit` semantics intact).

- [ ] **Step 1:** Read the grace gate: `grep -n "grace period" backend/strategies/graph_nexus_analysis.py` → read ±60 lines. Map how the suppressed reason string is captured (it's embedded in the log: `suppressed [Fast loser cut: …]`) — the gate sees the fresh_reason, so exemption = check `any(tag in reason for tag in _RISK_EXIT_TAGS)` where `_RISK_EXIT_TAGS = ("Fast loser", "Circuit breaker", "Trailing stop", "Hold-limit")` (verify exact strings from the emit sites :16657-16694 area). gitnexus impact on `_evaluate_position_risk`.
- [ ] **Step 2: Failing tests (real_config — grace ACTIVE at its live value):**

```python
def test_circuit_breaker_fires_inside_grace_avgo():
    """AVGO 2026-06: -13.79% on grace day 3 was suppressed to hold; must cut."""
    cfg = real_config()
    # entry 3 days ago, current price -13.79% below entry, fresh_score 0
    ... assert score == -1 and ("Circuit breaker" in reason or "Fast loser" in reason)

def test_signal_sell_still_suppressed_inside_grace():
    """A plain negative-signal sell on grace day 3 stays suppressed (grace's purpose)."""
    cfg = real_config()
    ... entry 3 days ago, current -2%, fresh_score -1 (signal sell)
    ... assert score != -1  # suppressed to hold, log contains 'grace'
```

(Copy the file's real invocation pattern; the first test MUST fail on current code — if it passes, the grace key isn't active in real_config: stop and re-check Task 5's key mapping before proceeding.)
- [ ] **Step 3-4: RED → implement the tag-exemption at the gate → GREEN** (full file + test_nexus_v25 + v9_preflight suites; profit-take-once stays the known red).
- [ ] **Step 5:** detect_changes (expect MEDIUM+ radius — FULL and MONITOR paths — proceed, it's the intended protective change), commit `fix(nexus): risk exits bypass V31 grace — grace only gates signal sells`

---

### Task 7: max_positions hard gate (spec 2b)

**Files:**
- Modify: `backend/broker.py` (order-emission loop, near :8619-8631) and/or GNA allocation if investigation shows the overshoot originates there
- Test: `backend/tests/test_max_positions_gate.py` (create)

**Interfaces:**
- Produces: no NEW-name buy order is emitted when `len(current_positions) - len(sells_this_cycle_for_full_exit) + new_names_already_emitted >= max_positions`. Adds/winner-adds to EXISTING names are exempt. Blocked buys log `MAX_POSITIONS_GATE: blocked <sym> (held=N, cap=M)`.

- [ ] **Step 1: Root-cause first.** 586767 peaked at 13 with cap 10. Investigate: does the allocation count queue/backfill buys against max_positions? Do rotation buys emit before their funding sells reduce the count? `grep -n "max_positions" backend/strategies/graph_nexus_analysis.py backend/broker.py | head -20`, read each gate. Write the finding in the report — the fix must target the real leak (likely: backfill-queue drain + rotation buys bypass the slate-level cap).
- [ ] **Step 2: Failing test** on an extracted pure helper `def max_positions_gate(held_symbols, cap, planned_sells_full_exit, emitted_new_names, candidate) -> bool` + a wiring test with a fake emulator/order list asserting the 11th new name is blocked and logged.
- [ ] **Step 3-4: RED → implement (helper + wire at emission) → GREEN.**
- [ ] **Step 5:** detect_changes, commit `fix(broker): hard max_positions gate at order emission`

---

### Task 8: One truthful P&L + dup-bar fix (spec 3a)

**Files:**
- Modify: `backend/broker.py` (:7102-7118 wrap-up; :7042-7099 seeding)
- Test: `backend/tests/test_backtest_pnl_consistency.py` (create)

**Interfaces:**
- Produces: (1) `final_value` derives from the LAST portfolio snapshot's own prices (`portfolio_emulator.get_portfolio_value(last_snapshot_prices)` or the snapshot's stored value) so `pnl`/`pnl_percent` == equity-curve end by construction; (2) the end-of-run seeding no longer appends a second same-date close per symbol (dedupe: keep the bar already present for that date; only append when the date is absent).

- [ ] **Step 1:** Read :7020-7130. Identify exactly where the duplicate 07-01 bars enter `data` (the sibling report: "writes both the raw bar `c` and snapshot-derived prices"). gitnexus impact on the wrap-up function.
- [ ] **Step 2: Failing test:** construct a minimal emulator with 2 positions + a `data` dict carrying DUPLICATE end-date bars with different closes + snapshots list; run the extracted wrap-up computation (extract `def compute_backtest_summary(emulator, snapshots, data, initial_cash) -> dict` if not already isolable); assert `summary["pnl"] == snapshots[-1]["value"] - initial_cash` exactly.
- [ ] **Step 3-4: RED → implement both fixes → GREEN.**
- [ ] **Step 5:** detect_changes, commit `fix(backtest): summary P&L uses the equity curve's own final mark; dedupe end-date bars`

---

### Task 9: Overlay call-site attribution (spec 3b)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (`_apply_trade_overlay` :18135ff, `_apply_etf_trade_overlay` :18335ff — the worker bodies submitted at :18618)
- Test: `backend/tests/test_overlay_call_site_attribution.py` (create)

**Interfaces:**
- Consumes: `llm_telemetry.llm_call_context(call_site=…, instance_id=…, backtest_id=…)` (context manager; thread-local — must be entered INSIDE the worker function, not around the executor.submit).
- Produces: overlay usage rows carry `call_site="overlay"` / `"overlay_etf"`; `(unset)` disappears for these paths.

- [ ] **Step 1:** Read the two overlay functions + how sibling sites (sentiment :14437) enter the context INSIDE their workers; confirm what instance/backtest ids are in scope at the overlay call sites.
- [ ] **Step 2: Failing test:** monkeypatch the telemetry recorder; invoke the overlay LLM-call wrapper (or a thin extraction of the worker body) on a fake executor (synchronous); assert the recorded row's call_site.
- [ ] **Step 3-4: RED → wrap worker bodies → GREEN.** Keep `attribution_keys` (exception enrichment) untouched.
- [ ] **Step 5:** detect_changes, commit `fix(telemetry): overlay LLM calls attribute call_site from worker threads`

---

### Task 10: Outcomes forward-tracking (spec 3c)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (`_update_indefinite_outcomes` :9252-9316 and/or its caller; `_save_trade_contexts_and_outcomes` for action_intent)
- Test: `backend/tests/test_outcomes_forward_tracking.py` (create)

**Interfaces:**
- Produces: outcome rows' `latest_return` / `max_return_so_far` / `min_return_so_far` / `latest_observation_date` advance on subsequent days when a price is available (reuse `_outcome_entry_price`-style fallbacks — the held-only `prices` dict is suspect #1 again); `action_intent` persists the real intent (was 'unknown' on 870/877).

- [ ] **Step 1: Root-cause.** Read `_update_indefinite_outcomes` + caller: why did 877 rows stay frozen (all with latest_observation_date == entry_date)? Suspects: (a) `prices` held-only again, (b) it only runs in live mode / a gate skips backtests, (c) exact-scope query mismatch, (d) writes swallowed. Verify against the 586767 rows READ-ONLY (prod query allowed). State the mechanism in your report before coding.
- [ ] **Step 2: Failing test** against the extracted update logic with fake rows + a prices source: day-2 update advances latest_return and dates; missing price leaves row untouched (no regression to zeros).
- [ ] **Step 3-4: RED → fix (price fallback and/or gate) → GREEN** (+ test_nexus_outcome_entry_price.py stays green).
- [ ] **Step 5:** detect_changes, commit `fix(nexus): outcome rows track forward returns; persist real action_intent`

---

### Task 11: LIVE_OVERRIDES user-overridable keys #2/#3 (spec 5.6)

**Files:**
- Modify: `backend/live_mode_overrides.py` (`_USER_OVERRIDABLE_KEYS`)
- Test: extend `backend/tests/test_live_overrides_drawdown.py`

**Interfaces:**
- Produces: explicit doc-179 values for `quality_filter_missing_metadata_policy` and `break_glass_fresh_shield_enabled` survive live-override application; absent keys still get the live defaults ("block" / True).

- [ ] **Step 1: Failing tests** (mirror the existing drawdown tests): explicit "warn" survives; explicit False survives; absent → "block"/True; other overrides unchanged.
- [ ] **Step 2-3: RED → add both keys to the frozenset (+ docstring note on why each is operator-owned) → GREEN.**
- [ ] **Step 4:** detect_changes, commit `fix(live-overrides): quality-filter policy + fresh-shield are operator-owned when explicitly set`

---

### Task 12: Rotation graph-gate + single-position cap (spec 5.1 code, 5.7)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (rotation candidate selection — `_rotation_candidate_allowed` ~:7524 region and the backfill-rotation outgoing-position picker; buy/winner-add/amplifier sizing sites for the cap)
- Test: `backend/tests/test_rotation_graph_gate.py` (create), extend `backend/tests/test_nexus_evaluate_position_risk.py` only if cap logic lands nearby

**Interfaces:**
- Consumes: `real_config(**overrides)` (Task 5).
- Produces: (1) a position whose CURRENT raw graph signal is positive (`raw_net > 0` — find the exact per-symbol field the rotation picker sees) cannot be selected as a rotation-funding sell, regardless of winner-lock-bypass thresholds; log `ROTATION_GRAPH_GATE: kept <sym> (raw=+x.xx)`. (2) `single_position_max_pct` (live value 25) enforced: any buy/add/amplifier order that would push `position_value / portfolio_total > pct/100` is clipped to the cap (partial fill of the intent, or skipped if already ≥ cap), logged `SINGLE_POS_CAP: clipped <sym>`.

- [ ] **Step 1:** Read the rotation outgoing-position selection (grep `backfill_rotation|_rotation_candidate|winner_lock_bypass` in GNA) and the three add paths (initial sizing, `_plan_winner_adds` :8061ff, momentum amplifier :18457 region). gitnexus impact on each function you'll touch. Map where current raw score per held symbol is available to the rotation picker.
- [ ] **Step 2: Failing tests:** (a) held AMAT-like position raw=+0.284, pnl +9%, all bypass thresholds met → NOT selected for rotation; (b) raw=−0.5 same pnl → selected (rotation still works); (c) buy that would create a 30% position on a $6k portfolio → clipped to 25%; (d) winner-add pushing 24%→28% → clipped to 25%.
- [ ] **Step 3-4: RED → implement → GREEN** (risk suite + rotation-adjacent suites).
- [ ] **Step 5:** detect_changes, commit `feat(nexus): rotation respects positive graph signal; wire single_position_max_pct as a real cap`

---

### Task 13: Sell-proceeds crediting in live cycles (spec 5.8)

**Files:**
- Modify: `backend/broker.py` (the submit loop :8619-8706 region; mirror the earnings-proceeds pattern at :7519)
- Test: `backend/tests/test_live_sell_proceeds_credit.py` (create)

**Interfaces:**
- Produces: within one live cycle, after sell orders are submitted, the buy-affordability ceiling = `cached_cash + 0.95 × Σ(expected proceeds of this cycle's submitted sells)` (0.95 partial-fill haircut), never exceeding `cash + proceeds`. Backtest path unchanged (emulator already credits synchronously). Config kill-switch `live_credit_sell_proceeds_enabled` default True.

- [ ] **Step 1:** Read :8590-8720 (ordering + cash read) and the earnings pattern :7519. Confirm sells are identified before buys in `_exec_order` and their qty×price is computable at submit time.
- [ ] **Step 2: Failing test** on an extracted `def buy_ceiling(cached_cash, submitted_sells, enabled=True, haircut=0.95) -> float` + a wiring test with fake adapter: cycle with $840 cash + $1,388 sells → buys see $2,158.60 ceiling; disabled → $840.
- [ ] **Step 3-4: RED → implement → GREEN.**
- [ ] **Step 5:** detect_changes, commit `feat(broker): live rotation buys may spend same-cycle sell proceeds (95% haircut)`

---

### Task 14: Round-2 apply script — config levers + dead-secret scrub (spec 5.2-5.5, 5.9, 5.10, 5.1-config)

**Files:**
- Create: `backend/scripts/apply_round2_2026_07.py`
- Test: `backend/tests/test_apply_round2_2026_07.py`

**Interfaces:**
- Consumes: patterns from `backend/scripts/apply_tune_2026_07.py` (argparse --apply gating, `_fp` redaction, scrubbed `raise … from None`, snapshot dump, `nexus_restamp.preview_change`).
- Produces: one-shot script, dry-run default, USER-GATED apply. Changes to `strategies[0].config`:

```python
R2_CHANGES = {
    "max_positions": 8,                                   # was 10 (5.2)
    "allocation_max_new_stock_buys": 6,                    # was 10 (5.2)
    "profitable_min_hold_release_peak_drop_pct": 12,       # was 8 (5.3)
    "backfill_rotation_winner_lock_bypass_max_held_pnl_pct": 3,  # was 10 (5.1)
    "backfill_rotation_min_hold_days": 15,                 # was 10 (5.1)
    "backfill_budget_reserve_pct": 0.1,                    # was 0.2 (5.9)
    "macro_risk_scale_min": 0.9,                           # was 0.8 (5.5) — VERIFY exact key name in GNA first
    # ETF sleeve (5.4): VERIFY the real key (budget-split log says etf_pct=0.20;
    # grep GNA for the config key behind it) then 0.20 -> 0.05
}
DELETE_KEYS_WITH_SECRETS = [  # dead keys carrying live credentials (5.10) — verified dead by config archaeology
    # every strategies[0].config key matching r".*_llm_azure_openai_(api_key|endpoint|api_version|model_id)$"
    # for roles analyst_panel, company_article, event_maintenance, macro_article, overlay, sentiment, lookback_*
]
```

- [ ] **Step 1:** VERIFY the two flagged key names by reading GNA (`grep -n "macro_risk_scale_min\|etf_pct\|etf_portfolio" backend/strategies/graph_nexus_analysis.py`) — if a key doesn't exist in code, DO NOT include it (report instead; a dead-key write is the exact disease this round treats). Confirm each DELETE key has zero code references (`grep -c` per key) and matches the secret-family regex.
- [ ] **Step 2: Tests** on pure `build_round2_strategies(doc179) -> (proposed, diff_rows, deleted_keys)`: every change lands; deletions only hit regex-matching dead keys; secrets never in diff output (fingerprints only); untouched spot-checks (fast_loser_cut_pct −10, single_position_max_pct still present — Task 12 now reads it!, grace keys untouched).
- [ ] **Step 3: GREEN → dry-run against prod** (read-only): print full diff + preview_change verdict (these keys are in NO identity hash — expect needs_prompt=False / no restamp; if preview says otherwise, STOP and report).
- [ ] **Step 4:** detect_changes, commit `feat(scripts): round-2 config levers (concentration, winner room, reserves, macro floor) + dead-secret scrub`. Do NOT run --apply.

---

### Task 15: PR + runbook + Track 4 rerun (process; deploy/apply USER-GATED)

- [ ] **Step 1:** Full suite `python3 -m pytest backend/tests/ -q` — green minus the 4 known pre-existing files; verify at merge-base if any NEW failure looks suspicious.
- [ ] **Step 2:** Final whole-branch review (SDD flow), fix wave if needed.
- [ ] **Step 3:** Push branch, open PR titled `fix(live): credit safety, risk-exit grace bypass, truthful backtest P&L + P&L levers`, admin-merge per repo convention.
- [ ] **Step 4 (runbook, user-gated):** user tops up OpenRouter credits + rotates key → Dokploy backend restart (also revives the dead instance spawner) → `python3 -m backend.scripts.apply_round2_2026_07 --apply` pre-market → instance boots (module_drift rebuild expected — new GNA code) → verify: risk exits fire inside grace (watch first FULL run), credit guard logs balance, no `(unset)` call sites.
- [ ] **Step 5 (Track 4):** re-queue the June-2026 replay (same params as 586767); on finish, compare pnl (now single-mark) vs SPY vs live June; write results into `docs/superpowers/specs/2026-07-02-nemotron-baseline-results.md` as "Run 2 (valid)". Only then decide the gpt-5.4-mini model A/B.

---

## Verification (after all tasks)

1. Suite green (minus 4 known). 2. All new tests use cages + real-config fixture where specified. 3. The three hunter top-levers each map to a shipped task (5.1→12/14, exec#3→13, S2→12, clobbers→11) or an explicit deferral in the spec. 4. Report to user: shipped list, gated runbook, deferred levers.
