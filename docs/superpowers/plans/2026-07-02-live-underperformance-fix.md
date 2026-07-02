# Live Underperformance Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the five confirmed live defects in the graph_nexus/alpaca-main trading path, apply the approved aggressive doc-179 tune with an 8% drawdown circuit breaker, and launch the first honest Nemotron backtest baseline.

**Architecture:** Code fixes land in `backend/scheduler.py` (pure function), `backend/strategies/graph_nexus_analysis.py` (risk exits + outcomes writer), `backend/llm_critical_guard.py`/caller (role-scoped halt), and `backend/instance.py` (halt hygiene). Prod-DB changes (doc 179 tune, key fix, stale-halt clear) go through guarded one-shot scripts under `backend/scripts/`, never ad-hoc. Config-identity rules discovered in recon: trading-rule keys are in NEITHER `live_config_hash` nor `history_scope_id`, so the tune batch needs no restamp; ONLY the macro-article LLM migration changes `history_scope_id` and requires `nexus_restamp.restamp_instance`. Any GNA code change alters `nexus_module_hash` → the deploy step must re-stamp module hashes on live snapshot rows (06-30 recovery procedure) or accept one pre-market lookback.

**Tech Stack:** Python 3 (`python3`), pytest (`backend/tests/`, conftest puts `backend/` on sys.path), RethinkDB (prod at repo-root `.env` `RETHINKDB_HOST`), Alpaca live API (read-only here).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-02-live-underperformance-fix-design.md`. Revert baseline: `docs/superpowers/specs/2026-07-02-strategy-179-pre-change-snapshot.json` + Jarvis memory note.
- **Real money:** Strategies doc 179 and the `alpaca-main` Instances row are LIVE. No script may place/cancel broker orders. Prod-DB writes happen ONLY in Tasks 8–9 scripts, each with a dry-run mode and explicit `--apply` flag.
- **Secrets:** never print API keys/secrets; redact to first-2-chars+length in any output or committed artifact.
- **GitNexus (CLAUDE.md):** run `gitnexus_impact({target, direction: "upstream"})` before editing any listed symbol; run `gitnexus_detect_changes()` before every commit; warn on HIGH/CRITICAL.
- **Tests:** run as `python3 -m pytest backend/tests/<file> -v` from repo root. If a test file imports `instance`/broker modules, stub socketio first: `sys.modules.setdefault("socketio", MagicMock())` (see `backend/tests/test_instance_crash_handling.py:8-15`).
- **Config facts (verified from live doc 179 inner `strategies[0].config` — outer block is legacy, inner is authoritative):** `fast_loser_cut_pct=-10`, `fast_loser_cut_pct_high_vol=-18`, `fast_loser_cut_recent_runup_block_pct=40`, `portfolio_drawdown_halt_pct=12`, `new_entry_reserved_budget_pct=0.3`, `max_positions=8`, `min_position_size=100`.
- Commit after every task with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` (implementer agents keep this trailer verbatim).

---

### Task 1: Scheduler FULL catch-up (missed-open resilience, spec A4)

**Files:**
- Modify: `backend/scheduler.py` (mode decision, lines ~216-224 in `get_next_wake`)
- Test: `backend/tests/test_scheduler_full_catchup.py` (create)

**Interfaces:**
- Consumes: `get_next_wake(now, marker, config) -> (next_wake_utc, mode)` (scheduler.py:161-264); `DEFAULT_CONFIG` (`full_anchor_pt_min=390`, `monitor_interval_min=20`, scheduler.py:50-58); marker = `strategy_cache["_nexus_full_cycle_completed_date"]` (PT/NY date string, written at GNA:26512 on FULL success).
- Produces: unchanged signature; new behavior — any slot AT or AFTER the full anchor returns mode `"FULL"` while `marker != today_str`. Broker needs no changes (broker.py:6842-6890 already passes the marker and dispatches the returned mode).

- [ ] **Step 1: Read the current mode decision.** Read `backend/scheduler.py:150-270` fully. Identify the exact variables: the fired slot minute (`fired_slot_min` or equivalent), `full_min` (anchor), and today's date string used against `marker`. Run `gitnexus_impact({target: "get_next_wake", direction: "upstream"})` and record callers (expect broker.py:6842-6890 only).

- [ ] **Step 2: Write failing tests** in `backend/tests/test_scheduler_full_catchup.py`:

```python
"""FULL catch-up: a missed 6:30 PT anchor must re-fire FULL later the same day.

Regression for 2026-07-01: 15 restarts spanned the open; the single FULL
anchor slot passed during churn and the strategy ran MONITOR-only all day,
never evaluating exits while CRWV fell to -19%.
"""
import sys, os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scheduler import get_next_wake, DEFAULT_CONFIG

PT = timezone(timedelta(hours=-7))  # PDT (matches July dates used below)

def _pt(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=PT).astimezone(timezone.utc)

def test_missed_anchor_catches_up_to_full():
    # 9:00 PT, anchor (6:30) passed, marker is stale (yesterday) -> FULL
    wake, mode = get_next_wake(_pt(2026, 7, 1, 9, 0), "2026-06-30", DEFAULT_CONFIG)
    assert mode == "FULL"

def test_completed_marker_stays_monitor():
    # marker == today -> anchor already done -> MONITOR as before
    wake, mode = get_next_wake(_pt(2026, 7, 1, 9, 0), "2026-07-01", DEFAULT_CONFIG)
    assert mode == "MONITOR"

def test_before_anchor_stays_monitor():
    # 5:40 PT slot precedes the 6:30 anchor -> MONITOR (no early FULL)
    wake, mode = get_next_wake(_pt(2026, 7, 1, 5, 35), "2026-06-30", DEFAULT_CONFIG)
    assert mode == "MONITOR"

def test_anchor_slot_still_full():
    wake, mode = get_next_wake(_pt(2026, 7, 1, 6, 29), "2026-06-30", DEFAULT_CONFIG)
    assert mode == "FULL"

def test_no_marker_after_anchor_is_full():
    wake, mode = get_next_wake(_pt(2026, 7, 1, 12, 0), None, DEFAULT_CONFIG)
    assert mode == "FULL"
```

Adjust helper/assertion details to the real return contract found in Step 1 (`get_next_wake` may return the wake time for the NEXT slot — assert on the mode of the slot that fires; follow the conventions of the existing scheduler tests if any exist, `grep -r "get_next_wake" backend/tests/`).

- [ ] **Step 3: Run tests, verify the catch-up cases FAIL** (`test_missed_anchor_catches_up_to_full`, `test_no_marker_after_anchor_is_full` fail; the others should already pass — if they don't, your Step-1 reading of the contract is wrong; fix the tests first).

Run: `python3 -m pytest backend/tests/test_scheduler_full_catchup.py -v`

- [ ] **Step 4: Implement.** In the mode decision (currently `FULL` only when `fired_slot_min == full_min and marker != today_str`), change to:

```python
if marker != today_str and fired_slot_min >= full_min:
    mode = "FULL"   # anchor slot, or catch-up: today's FULL never completed
else:
    mode = "MONITOR"
```

Preserve existing IDLE/window/weekday handling untouched. Add a one-line comment: `# catch-up: if the anchor was missed (crash/restart churn), the next slot re-fires FULL`.

- [ ] **Step 5: Run the new file AND the full scheduler-adjacent suite; verify PASS.**

Run: `python3 -m pytest backend/tests/test_scheduler_full_catchup.py -v` then `python3 -m pytest backend/tests/ -k "scheduler" -v`

- [ ] **Step 6: `gitnexus_detect_changes()`, then commit** `feat(scheduler): re-fire FULL cycle when daily anchor was missed`.

---

### Task 2: Outcomes writer price fallback (spec A2)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — `_save_trade_contexts_and_outcomes` (def at :9148; gate at :9210)
- Test: `backend/tests/test_nexus_outcome_entry_price.py` (create)

**Interfaces:**
- Consumes: the writer receives `prices` (live: held-names only — root cause of zero outcomes), `scores` payloads per symbol.
- Produces: new pure helper `def _outcome_entry_price(sym, payload, prices, portfolio_emulator) -> float | None` in graph_nexus_analysis.py just above `_save_trade_contexts_and_outcomes`; the :9210 gate becomes `if action_intent != "hold" and _entry_px:`.

- [ ] **Step 1: Read GNA:9148-9250 and the caller at :26350-26361.** Record what fields a `scores` payload carries for buy/sell candidates (`grep -n "current_price\|last_price\|\"price\"" backend/strategies/graph_nexus_analysis.py | head -40` around the score-building sites) and what price surface `portfolio_emulator` exposes (`_resolve_symbol_price` signature, GNA — find with `grep -n "_resolve_symbol_price" backend/strategies/graph_nexus_analysis.py`). Run `gitnexus_impact({target: "_save_trade_contexts_and_outcomes", direction: "upstream"})`.

- [ ] **Step 2: Write failing tests** in `backend/tests/test_nexus_outcome_entry_price.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from strategies.graph_nexus_analysis import _outcome_entry_price

class _Emu:
    def __init__(self, px): self._px = px
    # match the real price surface found in Step 1; adjust method name accordingly
    def get_last_price(self, sym): return self._px.get(sym)

def test_prices_dict_wins():
    assert _outcome_entry_price("CRWV", {}, {"CRWV": 104.55}, _Emu({})) == 104.55

def test_payload_price_fallback():
    # live buy/sell candidates are NOT in `prices` (root cause of 0 outcomes ever)
    px = _outcome_entry_price("GOOGL", {"current_price": 182.4}, {}, _Emu({}))
    assert px == 182.4

def test_emulator_fallback():
    px = _outcome_entry_price("DAL", {}, {}, _Emu({"DAL": 55.1}))
    assert px == 55.1

def test_no_price_returns_none():
    assert _outcome_entry_price("XXXX", {}, {}, _Emu({})) is None

def test_zero_and_negative_rejected():
    assert _outcome_entry_price("Y", {"current_price": 0}, {}, _Emu({})) is None
```

Adapt the payload key(s) and emulator method to what Step 1 found (payload may use `"price"` or `"last_close"`; use the real keys, try them in a sensible order).

- [ ] **Step 3: Run, verify FAIL** (`ImportError: cannot import name '_outcome_entry_price'`).

Run: `python3 -m pytest backend/tests/test_nexus_outcome_entry_price.py -v`

- [ ] **Step 4: Implement the helper + wire the gate.**

```python
def _outcome_entry_price(sym, payload, prices, portfolio_emulator):
    """Entry price for a TradeOutcomes row. Live `prices` only covers held
    names (candidates are unpriced there), which kept this table empty for
    every live instance — fall back to the payload's own price, then the
    emulator/adapter last price."""
    for candidate in (
        (prices or {}).get(sym),
        (payload or {}).get("current_price"),   # + the real keys found in Step 1
        _emu_last_price(portfolio_emulator, sym),
    ):
        try:
            if candidate and float(candidate) > 0:
                return float(candidate)
        except (TypeError, ValueError):
            continue
    return None
```

(`_emu_last_price` = tiny inline try/except around the emulator surface found in Step 1.) At the :9210 gate replace `prices.get(sym)` with the helper result (compute once, reuse for the doc's `entry_price` field at :9211-9228). Do NOT touch the stale-doc deletion branch. `_save_trade_contexts_and_outcomes` must keep identical behavior when the helper returns None.

- [ ] **Step 5: Run new tests + existing regression files; verify PASS.**

Run: `python3 -m pytest backend/tests/test_nexus_outcome_entry_price.py backend/tests/test_clear_main_instance_lookback_state.py -v`

- [ ] **Step 6: `gitnexus_detect_changes()`, commit** `fix(nexus): TradeOutcomes writer falls back beyond held-only prices dict — live outcomes were never written`.

---

### Task 3: Risk-exit hard floor + loud SKIP (spec A1)

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` — `_evaluate_position_risk` (:16415-16879; fast-loser block :16647-16694; SKIP at :16480-16493)
- Test: extend `backend/tests/test_nexus_evaluate_position_risk.py` (canonical `_Emu`/`_base_config` fixtures at lines 41-73)

**Interfaces:**
- Consumes: `_base_config(**overrides)` fixture; config keys `fast_loser_cut_pct`, `fast_loser_cut_pct_high_vol`, `fast_loser_cut_recent_runup_block_pct`, `fast_loser_cut_recent_runup_lookback_bars`.
- Produces: (1) carve-outs (recent-runup block, high-vol threshold) can never suppress a cut once `_unrealized_pct <= fast_loser_cut_pct_high_vol` (hard floor); (2) the :16491 "Risk pipeline SKIP" path calls a new module-level `_alert_risk_pipeline_skip(instance_id, sym)` (once per sym per day) so a silent bypass can never happen again.

- [ ] **Step 1: Reproduce CRWV as a failing test.** Read the fast-loser block :16640-16700 to see exactly how the runup block and high-vol threshold gate the cut, then write (exact fixture style of the file's existing `test_fast_loser_cut_fires_at_minus_10`, line 182):

```python
def test_fast_loser_hard_floor_beats_runup_block():
    """CRWV 2026-07: -19.4% with a >40% recent runup. The runup carve-out may
    defer cuts between -10 and -18, but beyond fast_loser_cut_pct_high_vol the
    cut MUST fire regardless of runup history."""
    cfg = _base_config(
        fast_loser_cut_pct=-10.0,
        fast_loser_cut_pct_high_vol=-18.0,
        fast_loser_cut_recent_runup_block_pct=40.0,
        fast_loser_cut_recent_runup_lookback_bars=20,
    )
    emu = _Emu(positions={"CRWV": 5.61},
               trades=[{"ticker": "CRWV", "action": "buy", "shares": 5.61,
                        "price": 104.5534, "timestamp": "2026-06-25T13:35:00Z"}])
    # price history with a +50% runup within the 20-bar lookback, now 84.21
    score, reason = _run_risk(emu, cfg, sym="CRWV", current_price=84.21,
                              fresh_score=0.0, runup_pct=50.0)
    assert score == -1
    assert "Fast loser" in reason
```

Mirror the file's real invocation helper — the existing tests show how `_evaluate_position_risk` is called and how runup/price history is injected; reuse their pattern verbatim (`_run_risk` above is shorthand for whatever the file actually does — copy its call shape). Also add the inverse guard: `test_runup_block_still_defers_between_cut_and_high_vol` (loss −12%, runup 50% → NO cut).

- [ ] **Step 2: Run; record which gate suppresses the cut.** If the hard-floor test already PASSES on current code, the runup block was not CRWV's suppressor — keep both tests as regressions, skip Step 3's logic change, and proceed to Step 4 (the SKIP alert), noting in the commit that 07-01's zero-cycles outage (fixed by Task 1) was the sole cause.

Run: `python3 -m pytest backend/tests/test_nexus_evaluate_position_risk.py -v -k "hard_floor or runup_block"`

- [ ] **Step 3: Implement the hard floor (only if Step 2 failed).** In the fast-loser block, evaluate the runup block ONLY while `_unrealized_pct > _fast_cut_high_vol`:

```python
_runup_blocked = (_runup_block_pct > 0 and _recent_runup >= _runup_block_pct
                  and _unrealized_pct > _fast_cut_high_vol)  # hard floor: beyond
                  # high-vol threshold nothing defers the cut
```

Re-run Step 2 tests → PASS.

- [ ] **Step 4: Make the SKIP loud.** At the :16491 `Risk pipeline SKIP` branch, add a once-per-(sym, PT-date) red log + Discord alert. Find the existing alert utility with `grep -n "def alert_\|_send_discord\|discord_notify" backend/strategies/graph_nexus_analysis.py backend/*.py | head` and reuse it exactly as `_apply_portfolio_drawdown_halt` does (GNA ~19088-19133, try/except-wrapped so Discord outage never blocks the strategy). Track sent alerts in `strategy_cache.setdefault("_risk_skip_alerted", {})[f"{sym}|{date}"] = True`. Unit-test the dedup helper if you extract one; the Discord call itself is fire-and-forget.

- [ ] **Step 5: Full risk-suite run.**

Run: `python3 -m pytest backend/tests/test_nexus_evaluate_position_risk.py backend/tests/test_nexus_v25.py backend/tests/test_nexus_v9_preflight.py -v`

- [ ] **Step 6: `gitnexus_detect_changes()`, commit** `fix(nexus): fast-loser hard floor beyond high-vol threshold + loud alert on risk-pipeline skip`.

---

### Task 4: Role-scoped LLM criticality — degrade, don't halt (spec A3)

**Files:**
- Modify: `backend/llm_critical_guard.py` (classify :63-110, `is_immediately_fatal` :143-145, `LLMCriticalFailure` :174-205)
- Modify: the raise/handle sites — `backend/live_critical_abort.py:31-82` and the strategy-side raise site (locate in Step 1)
- Test: `backend/tests/test_llm_critical_role_scope.py` (create)

**Interfaces:**
- Consumes: `LLMCriticalFailure` (BaseException subclass), `live_critical_abort.handle(instance_id, failure)` → `halt_live_trading(...)` (live_kill_switch.py:103-105).
- Produces: `def role_is_halt_worthy(role: str | None) -> bool` in llm_critical_guard.py — `False` for `{"macro_article", "company_article", "lookback_macro_article", "lookback_company_article"}` (article-enrichment roles), `True` for decision roles (`None`/unknown defaults to True, fail-safe). `LLMCriticalFailure` gains an optional `role=None` attribute. `handle()` degrades (log + one Discord alert, NO halt) when `not role_is_halt_worthy(failure.role)`.

- [ ] **Step 1: Locate the raise site + role plumbing.** `grep -n "LLMCriticalFailure(" backend/ -r` and `grep -n "llm_critical_guard" backend/strategies/graph_nexus_analysis.py backend/broker.py`. Read enough to answer: at the point of raise, is the LLM role (e.g. `macro_article`) in scope? (It is the role string used for per-role provider config, so almost certainly yes.) Run `gitnexus_impact({target: "handle", direction: "upstream"})` on live_critical_abort.

- [ ] **Step 2: Failing tests** in `backend/tests/test_llm_critical_role_scope.py`:

```python
import sys, os
from unittest.mock import MagicMock, patch
sys.modules.setdefault("socketio", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm_critical_guard import role_is_halt_worthy, LLMCriticalFailure
import live_critical_abort

def test_article_roles_do_not_halt():
    for role in ("macro_article", "company_article",
                 "lookback_macro_article", "lookback_company_article"):
        assert role_is_halt_worthy(role) is False

def test_decision_and_unknown_roles_halt():
    for role in ("llm", "sentiment", "overlay", "event_maintenance", None, "??"):
        assert role_is_halt_worthy(role) is True

def test_handle_degrades_for_article_role():
    failure = LLMCriticalFailure("codex_quota_exhausted", "quota", provider="codex-cli",
                                 role="macro_article")  # match real ctor found in Step 1
    with patch.object(live_critical_abort, "_halt_live_trading") as halt:
        live_critical_abort.handle("alpaca-main", failure)
        halt.assert_not_called()

def test_handle_still_halts_for_decision_role():
    failure = LLMCriticalFailure("auth_failure", "401", provider="openrouter", role="llm")
    with patch.object(live_critical_abort, "_halt_live_trading") as halt:
        live_critical_abort.handle("alpaca-main", failure)
        halt.assert_called_once()
```

Match the real `LLMCriticalFailure` constructor and the real halt-callable name inside live_critical_abort (Step 1 tells you both; patch what `handle` actually calls).

- [ ] **Step 3: Run, verify FAIL.** `python3 -m pytest backend/tests/test_llm_critical_role_scope.py -v`

- [ ] **Step 4: Implement.** (a) `role_is_halt_worthy` + `role` attr on `LLMCriticalFailure`; (b) thread `role=` through the raise site(s); (c) in `live_critical_abort.handle`, before halting: degrade path = red log `f"LLM critical in non-decision role {failure.role}: degrading (no halt)"` + the existing one-shot Discord alert machinery (`_already_alerted` idempotence, reuse it), then `return` without `_halt_live_trading`. (d) The strategy must SURVIVE the degrade: at the strategy-side call site make sure the macro-article stage catches the non-fatal case and continues the run with that signal empty (follow how Benzinga-401 degrade behaves, benzinga_client.py:262-280 precedent).

- [ ] **Step 5: Run new + adjacent suites; verify PASS.** `python3 -m pytest backend/tests/test_llm_critical_role_scope.py backend/tests/test_instance_crash_handling.py -v`

- [ ] **Step 6: `gitnexus_detect_changes()`, commit** `fix(llm-guard): article-role LLM failures degrade instead of halting the instance`.

---

### Task 5: Clear stale halt fields on healthy boot (spec A5)

**Files:**
- Modify: `backend/instance.py:507-512` (startup Instances update)
- Test: `backend/tests/test_instance_halt_hygiene.py` (create; socketio stub required)

**Interfaces:**
- Consumes: the startup `.update({"uptimeStart": r.now(), "running": True, "crashed": False, "crashed_at": None})`.
- Produces: that update additionally sets `"halt_reason": None, "halted_at": None`. (Safe: instance.py only runs when `runCommand=True` — the operator already un-halted; recon confirmed nothing else ever clears these.)

- [ ] **Step 1: Failing test.** Follow `test_instance_crash_handling.py` structure (socketio stub, import `instance`, mock the rethink handle). Assert the startup update payload contains `halt_reason=None` and `halted_at=None` alongside `running=True`.
- [ ] **Step 2: Run, verify FAIL.** `python3 -m pytest backend/tests/test_instance_halt_hygiene.py -v`
- [ ] **Step 3: Implement** — add the two keys to the dict at instance.py:507-512.
- [ ] **Step 4: Run new + crash-handling suites; PASS.** `python3 -m pytest backend/tests/test_instance_halt_hygiene.py backend/tests/test_instance_crash_handling.py -v`
- [ ] **Step 5: `gitnexus_detect_changes()`, commit** `fix(instance): clear stale halt_reason/halted_at on healthy boot`.

---

### Task 6: Drawdown-halt precedence — make 8% actually take effect (spec Track B prerequisite)

**Files:**
- Read: `backend/live_mode_overrides.py` (line 44 sets `portfolio_drawdown_halt_pct = 10.0` for live)
- Modify: `backend/live_mode_overrides.py` (only if override clobbers explicit config)
- Test: `backend/tests/test_live_overrides_drawdown.py` (create)

**Interfaces:**
- Produces: guarantee that an explicit `portfolio_drawdown_halt_pct` in doc-179 config wins over the live-mode default-tightening override. Task 8's script sets the config key to `8`.

- [ ] **Step 1: Read `live_mode_overrides.py` fully.** Determine: does it `setdefault` (config wins — nothing to do) or assign unconditionally (override wins — bug for our purposes)?
- [ ] **Step 2: Failing test:** apply the overrides to a config dict that explicitly contains `portfolio_drawdown_halt_pct: 8.0`; assert the value survives as `8.0`. Second test: a config WITHOUT the key gets the live default (`10.0`) — preserve current safety behavior.
- [ ] **Step 3: Implement** (only if needed): change the assignment to respect an explicitly-set key (`setdefault`, or guard on key presence). Keep every other override untouched.
- [ ] **Step 4: PASS both tests**, `python3 -m pytest backend/tests/test_live_overrides_drawdown.py -v`
- [ ] **Step 5: `gitnexus_detect_changes()`, commit** `fix(live-overrides): explicit portfolio_drawdown_halt_pct wins over live default`.

---

### Task 7: PR for Tasks 1–6 + deploy with module-hash restamp

**Files:** none new (process task)

- [ ] **Step 1:** `python3 -m pytest backend/tests/ -x -q` — full suite green (pre-existing failures: note them, don't fix unrelated ones).
- [ ] **Step 2:** `gitnexus_detect_changes()` — verify affected symbols are exactly the Task-1–6 set; report risk level.
- [ ] **Step 3:** Push branch, open PR titled `fix(live): risk-exit hard floor, FULL catch-up, outcomes writer, role-scoped LLM halt, halt hygiene` with the spec linked; merge per the repo's admin-merge convention.
- [ ] **Step 4: Deploy pre-market** (before 6:00 AM PT). GNA changed → `nexus_module_hash` drifts. Immediately after deploy, re-stamp live snapshot rows to the deployed hash (06-30 recovery procedure): compute via `backend/broker_snapshot_helpers._resolve_nexus_module_path` + `backend/strategy_cache_persistence._compute_module_hash`, update `NexusStrategyCache` rows for base id `alpaca-main`. Verify next boot logs `reason=ok gap_days=0` (NOT `module_drift`).
- [ ] **Step 5: Post-deploy watch (first FULL run):** CRWV gets cut (or a `Fast loser` / runup-defer decision is visibly logged); `GraphNexusTradeOutcomes` gains alpaca-main rows; no `Risk pipeline SKIP` alerts; boots/day ≈ 1.

---

### Task 8: Doc-179 hygiene script — dead Alpaca key + stale halt (spec A5b)

**Files:**
- Create: `backend/scripts/fix_doc179_hygiene.py`
- Test: `backend/tests/test_fix_doc179_hygiene.py` (pure-function parts only)

**Interfaces:**
- Consumes: repo-root `.env` `RETHINKDB_HOST`; `backend/interactive_utils.get_conn()`; BrokerageAccounts row id `08f683af-76f6-404d-872c-37baa45711ee` ("Alpaca Live" — holds the WORKING key; doc-179's inner `alpaca_key` returns 401).
- Produces: one-shot script, `--dry-run` default / `--apply` to write: (1) copy `key`/`secret` from the BrokerageAccounts row into doc-179 inner `strategies[0].config.alpaca_key/alpaca_secret` (in-DB copy — values never printed; log sha1[:8] fingerprints only); (2) clear `halt_reason`/`halted_at` on Instances row `alpaca-main` (redundant after Task 5 but fixes the current stale row immediately).

- [ ] **Step 1:** Write the script: argparse (`--apply`), connect via interactive_utils, fetch both docs, build the update, PRINT a redacted plan (fingerprints + which fields change), require `--apply` to write. Structure the doc-mutation as a pure function `build_updates(doc179, brokerage_row) -> dict` for testability.
- [ ] **Step 2:** Unit-test `build_updates` with fixture dicts (asserts: only `alpaca_key`/`alpaca_secret` inside `strategies[0].config` change; outer config untouched; returns rethink-ready partial update).
- [ ] **Step 3:** `python3 -m pytest backend/tests/test_fix_doc179_hygiene.py -v` → PASS. Run script `--dry-run` against prod (read-only) and eyeball the plan.
- [ ] **Step 4:** Run with `--apply`. NOTE: doc-179 edits restart the broker (Strategies changefeed) — do this pre-market together with Task 9. `alpaca_key` is in neither identity hash (recon-verified) → no restamp needed for this edit.
- [ ] **Step 5:** `gitnexus_detect_changes()`, commit script+test: `chore(scripts): doc-179 hygiene — replace dead alpaca key, clear stale halt`.

---

### Task 9: Track B tune script — apply + restamp + fresh backup

**Files:**
- Create: `backend/scripts/apply_tune_2026_07.py`
- Test: `backend/tests/test_apply_tune_2026_07.py`

**Interfaces:**
- Consumes: `backend/nexus_restamp.py` — `preview_change(conn, r, strategy_id, proposed_strategies)` (:202-240), `restamp_instance(conn, r, base_instance_id, resolved_cfg)` (:160-199), `resolve_for_identity(conn, raw_cfg)` (:46-64). Models table: the openrouter Nemotron row referenced by the default role's `llm_model_id` in doc 179.
- Produces: one-shot script (`--dry-run` default / `--apply`) that (1) dumps a FRESH full redacted doc-179 snapshot to `docs/superpowers/specs/2026-07-02-strategy-179-pre-tune-snapshot.json`, (2) applies the tune to inner `strategies[0].config`, (3) handles identity: runs `preview_change` with the proposed strategies; the LLM-role migration WILL flip `history_scope_id` → after `--apply`, calls `restamp_instance(conn, r, "alpaca-main", resolved_cfg)` and prints the restamp report.

**The tune (B1 — trading keys, in NO identity hash; recon-verified):**

```python
B1_CHANGES = {
    "portfolio_drawdown_halt_pct": 8,            # was 12 (user circuit breaker)
    "profitable_min_hold_conviction_override_enabled": False,  # was True (confirmed drag)
    "new_entry_reserved_budget_pct": 0.1,        # was 0.3 (idle-cash fix)
    "cash_reserve_floor_pct": 0.02,              # was 0.05
    "allocation_max_new_stock_buys": 10,         # was 6 (matches +266% run)
    "max_propagated_scoring_slots": 40,          # was 20 (signal width)
    "max_positions": 10,                         # was 8 — replaces the spec's
        # "priority buys tap reserve": June's 28 blocked buys were
        # queue_status=full_priority_blocked (POSITION SLOTS full), not cash;
        # 2 more slots is the change that actually unblocks them
    "rotation_break_glass_delta": 2.5,           # was 1
    "rotation_break_glass_raw_score": 3.5,       # was 1.5
    "rotation_profitable_min_incoming_raw_score": 2.0,  # was 1.5
    # Benzinga sub lapsed — silence until renewed:
    "benzinga_company_actions_enabled": False, "benzinga_earnings_calendar_enabled": False,
    "benzinga_gov_trades_enabled": False, "benzinga_insider_trades_enabled": False,
    "benzinga_insights_enabled": False, "benzinga_ipo_enabled": False,
    "benzinga_ma_enabled": False, "benzinga_ratings_enabled": False,
    "benzinga_splits_enabled": False,
}
```

**(B2 — LLM role migration, changes `history_scope_id` → restamp required):** set `macro_article_llm_provider` and `lookback_macro_article_llm_provider` to `openrouter`, and copy `macro_article_llm_model` / `_model_id` (+ lookback twins) from the doc's default-role (`llm_provider`/`llm_model`/`llm_model_id`) Nemotron values. Clear the now-unused `*_azure_openai_*` fields for those roles only if `resolve_for_identity` tolerates it — otherwise leave them (harmless).

- [ ] **Step 1:** Write the script. Pure function `build_tuned_strategies(doc179) -> (proposed_strategies, diff_rows)` applies B1+B2 and returns a printable diff (`key: old -> new`); snapshot/redaction helpers reuse the Task-8 fingerprint approach. Flow: fetch → snapshot dump → diff print → `preview_change` print (`needs_prompt`, `would_rebuild` per instance) → require `--apply` → write via `.update({"strategies": proposed})` → `restamp_instance` → verify with a fresh `preview_change` (expect no rebuild needed).
- [ ] **Step 2:** Unit tests for `build_tuned_strategies` with a fixture doc: every B1 key lands with the exact new value; B2 provider/model/model_id copied from default role; UNTOUCHED spot-checks (`fast_loser_cut_pct` still −10, `min_position_size` still 100, `buy_threshold` untouched, secrets untouched); diff_rows complete.
- [ ] **Step 3:** `python3 -m pytest backend/tests/test_apply_tune_2026_07.py -v` → PASS. `--dry-run` against prod; verify preview says restamp will be needed (scope flip) and NO other instance is affected.
- [ ] **Step 4:** Pre-market, run `--apply` (same session as Task 8, single broker restart window). Confirm restamp report + subsequent boot `reason=ok gap_days=0`. Update the Jarvis revert note if any pre-change value differs from the stored baseline.
- [ ] **Step 5:** Commit script+test+fresh snapshot: `feat(scripts): apply 2026-07 live tune (8pct halt, budget unlock, rotation tighten, macro-role migration) with restamp`.
- [ ] **Step 6: First-FULL-run watch:** decisions log shows `prot=`/slots reflecting 40 propagated slots and 10 max positions; drawdown state shows 8% threshold; no codex-cli calls anywhere (`LLMUsage` per-role check); Benzinga errors gone.

---

### Task 10: Track C — Nemotron baseline backtests (parallel, does not gate deploy)

**Files:**
- Create: `docs/superpowers/specs/2026-07-02-nemotron-baseline-results.md` (results doc, written at the end)

- [ ] **Step 1:** Find the backtest entry point: `grep -n "FIXED_START_DATE\|def run\|argparse\|__main__" backend/engines/ai_backtest_engine.py | head -30` and how stored runs in `BacktestResults` were launched (UI/API path: `grep -rn "BacktestResults" backend/api/main.py | head`). Determine how to pass: config snapshot, window (start/end), cadence `dual_cadence_backtest_sim`, model roles.
- [ ] **Step 2:** Launch 5 runs (background, sequential if the engine is single-slot): (a) June-2026 replay, CURRENT config — ×2 repeats; (b) 2026-01-02→2026-06-30, TUNED config — ×2 repeats; (c) one non-bull window (e.g. 2025-07→2025-12), TUNED — ×1. All with the doc-179 Nemotron roles. Costs OpenRouter tokens — keep repeats at this count.
- [ ] **Step 3:** Collect results into the results doc: pnl% per run, trade count, win rate, vs SPY same-window, variance across repeats, and an explicit gross-vs-estimated-net line (fills are frictionless; apply a sensitivity note rather than a fake precision haircut). Compare June replay vs actual live June (+0.5%).
- [ ] **Step 4:** Commit the results doc. Store a 1-3 sentence Jarvis knowledge note with the headline numbers (`store_code_knowledge`, type `note`).

---

## Verification (after all tasks)

1. `python3 -m pytest backend/tests/ -q` — green (minus documented pre-existing failures).
2. Live checklist over the next 2 trading days: exactly one FULL/day (or a logged catch-up), TradeOutcomes accumulating, CRWV resolved, no stale halt fields, no codex-cli usage, drawdown threshold 8%, ≥1 of June's blocked priority names funded if signals persist.
3. Report to user: what shipped, live-config diff as applied, backtest baseline table, and the explicit list of what was NOT done (Benzinga renewal, OpenRouter key rotation — user actions).
