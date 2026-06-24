# Kalshi Live In-Match Monitoring + Runtime Crash Alerts — Implementation Plan

> **For agentic workers:** implement task-by-task with pure-core TDD. Steps use `- [ ]`.

**Goal:** In-play two-way trading on live Kalshi soccer markets (Kalshi-price-only, hybrid fair value, LLM on material events) + a configurable runtime crash-alert notification category (Discord + iOS push).

**Architecture:** New pure modules under `backend/kalshi/live/` (clock, event detection, live fair value, live decision) + a thin `monitor.py` integration. Engine loop splits matches pregame→planner / live→monitor, adaptive cadence, wall-clock analyst cache. Crash alerts: one taxonomy entry + a crash guard in `kalshi/runner.py`.

**Tech Stack:** Python (dataclasses, stdlib), pytest. Vue + Flutter for the one config toggle. Reuses `notifications.notify`, `KalshiClient`, `kalshi.decisions`, `kalshi.intelligence`, `kalshi.quant`.

---

### Task 1: Runtime crash-alert notification category + runner crash guard

**Files:**
- Modify: `backend/notification_types.py` (add one entry to `NOTIFICATION_TYPES`)
- Modify: `backend/kalshi/runner.py` (wrap `run_instance` in a crash guard)
- Test: `backend/tests/test_kalshi_runner_crash.py`, `backend/tests/test_notification_types_kalshi_runtime.py`

- [ ] **Step 1:** Add the category (before the Infrastructure section):
```python
{"key": "kalshi_runtime", "group": "Kalshi", "label": "Kalshi runtime crashed",
 "desc": "The Kalshi instance runtime crashed unexpectedly", "channel": "notifications",
 "discord": True, "push": True, "prefixes": ["KALSHI RUNTIME ["]},
```
- [ ] **Step 2:** Add a crash guard in `runner.py`. Factor the `run_instance(EngineConfig(...))` call into the new helper so it's testable:
```python
def _run_with_crash_alert(config, *, run=run_instance, notify=None) -> None:
    """Run the engine; on an uncaught crash, alert the operator (Discord + iOS
    push, category kalshi_runtime) then re-raise so the supervisor still sees it.
    The alert is best-effort and never masks the original exception."""
    try:
        run(config)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        import traceback
        try:
            _notify = notify
            if _notify is None:
                from notifications import notify as _notify
            tb = traceback.format_exc()[-600:]
            _notify(
                category="kalshi_runtime",
                instance_id=config.instance_id,
                title=f"KALSHI RUNTIME [{config.instance_id}] crashed",
                body=f"{type(e).__name__}: {e}\n\n{tb}",
                discord_channel="notifications",
                push_title="Kalshi runtime crashed",
                push_body=f"{type(e).__name__}: {str(e)[:120]}",
            )
        except Exception:
            pass
        raise
```
Call it from `main()` instead of `run_instance(...)` directly.
- [ ] **Step 3:** Tests — `classify(notif_key="kalshi_runtime")` returns `"kalshi_runtime"` and it's in `default_routing()` with push on; `_run_with_crash_alert` calls the injected `notify` with `category="kalshi_runtime"` when `run` raises, and re-raises; does not call notify on clean return; re-raises `KeyboardInterrupt` without notifying.
- [ ] **Step 4:** Run: `pytest tests/test_kalshi_runner_crash.py tests/test_notification_types_kalshi_runtime.py -q`

---

### Task 2: `kalshi/live/match_clock.py` (pure)

**Files:** Create `backend/kalshi/live/__init__.py`, `backend/kalshi/live/match_clock.py`; Test `backend/tests/test_kalshi_match_clock.py`

- [ ] Implement:
```python
PREGAME, LIVE, ENDED, UNKNOWN = "pregame", "live", "ended", "unknown"

def match_phase(kickoff_ts, now_ts, *, regulation_min=115):
    """(phase, elapsed_min). kickoff_ts/now_ts are epoch seconds; kickoff_ts None
    -> UNKNOWN. Live spans [kickoff, kickoff+regulation_min]; after that ENDED."""
    if not kickoff_ts:
        return (UNKNOWN, None)
    elapsed = (now_ts - kickoff_ts) / 60.0
    if elapsed < 0:
        return (PREGAME, elapsed)
    if elapsed <= regulation_min:
        return (LIVE, elapsed)
    return (ENDED, elapsed)

def kickoff_from_market(market: dict):
    """Best-effort kickoff epoch seconds from a raw Kalshi market dict. Tries
    explicit time fields, else parses the YYMONDD date in the ticker/event. None
    when nothing parseable (clock then UNKNOWN — monitor falls back to price moves)."""
    # parse open_time/expected_expiration_time/close_time (ISO8601 -> epoch);
    # fallback: ticker date token like 26JUN22 -> 2026-06-22 (kickoff unknown
    # within the day -> return None so we don't fake a minute).
```
- [ ] Tests: pregame/live/ended boundaries; None kickoff → UNKNOWN; ISO field parse; bad input → None/UNKNOWN, never raises.

---

### Task 3: `kalshi/live/event_detect.py` (pure)

**Files:** Create `backend/kalshi/live/event_detect.py`; Test `backend/tests/test_kalshi_event_detect.py`

- [ ] Implement:
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class PriceMove:
    moved: bool
    delta_cents: float      # signed: + = YES richer (team more likely)
    direction: str          # 'up' | 'down' | 'flat'

def detect_move(history, *, threshold_cents=8.0, lookback=3) -> PriceMove:
    """history: list of mid-prices in cents (oldest..newest). A material move =
    |newest - min/max over lookback| >= threshold. Returns the signed delta vs the
    reference `lookback` points back."""
```
- [ ] Tests: jump up ≥ threshold → moved/up; drop → down; noise < threshold → flat; <2 points → flat; threshold scaling.

---

### Task 4: `kalshi/live/live_fair.py` (pure)

**Files:** Create `backend/kalshi/live/live_fair.py`; Test `backend/tests/test_kalshi_live_fair.py`

- [ ] Implement:
```python
def live_fair(market_prob, prematch_prob, elapsed_min, llm_tilt=0.0, *,
              w_market=0.7, regulation_min=115, tilt_cap=0.05):
    """Hybrid live fair value in [0,1]. Model weight decays linearly to 0 by full
    time so late in the match the market dominates. llm_tilt clamped to ±tilt_cap."""
    m = _clamp01(market_prob)
    p = _clamp01(prematch_prob if prematch_prob is not None else m)
    frac = _clamp01((elapsed_min or 0) / regulation_min)
    model_w = (1.0 - w_market) * (1.0 - frac)   # decays to 0
    mkt_w = 1.0 - model_w
    base = mkt_w * m + model_w * p
    tilt = max(-tilt_cap, min(tilt_cap, llm_tilt or 0.0))
    return _clamp01(base + tilt)
```
- [ ] Tests: elapsed 0 → blend honors w_market; elapsed≥regulation → equals market(+tilt); tilt clamped; None prematch → market; clamps to [0,1].

---

### Task 5: `kalshi/live/live_decision.py` (pure) + InPlayCaps

**Files:** Create `backend/kalshi/live/live_decision.py`; Test `backend/tests/test_kalshi_live_decision.py`

- [ ] Implement `InPlayCaps` + `decide`:
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class InPlayCaps:
    edge_threshold: float = 0.03
    kelly_fraction: float = 0.25
    bankroll_cents: int = 0
    max_contracts_per_market: int = 50
    inplay_exposure_frac: float = 0.25     # cap of bankroll in-play per match
    max_adds_per_match: int = 3
    no_add_after_min: float = 80.0
    stop_loss_frac: float = 0.5            # exit if mark <= entry*(1-this)

@dataclass(frozen=True)
class LiveAction:
    kind: str          # open | add | reduce | exit | hold
    contracts: int
    reason: str

def decide(*, position, live_fair, yes_ask_cents, yes_bid_cents, caps, phase,
           elapsed_min, adds_so_far=0, allow_open=True) -> LiveAction:
    """position: KalshiContractPosition-like or None. Logic:
    - stop-loss / thesis-break (live_fair well below mark) -> reduce/exit.
    - strong value (live_fair - ask_prob > edge_threshold) and risk allows
      -> add (if holding) or open (if flat and allow_open).
    - else hold. Never act in non-live phases except exit/reduce."""
```
Sizing reuses quarter-Kelly on `(live_fair - ask_prob)`; honor `max_contracts_per_market`, `inplay_exposure_frac`, `max_adds_per_match`, `no_add_after_min`. When `phase == UNKNOWN`, `allow_open` is forced False by the caller (monitor) unless a move was detected.
- [ ] Tests: thesis-break → exit; stop-loss hit → exit; value + holding → add (sized); value + flat + allow_open → open; over-cap → hold/reduce to cap; after no_add_after_min → no add; non-live phase → only exit/reduce; flat with no value → hold.

---

### Task 6: `kalshi/live/monitor.py` (integration, thin)

**Files:** Create `backend/kalshi/live/monitor.py`; Test `backend/tests/test_kalshi_monitor.py` (fake client + fake llm)

- [ ] Implement `run_live_step(*, client, live_matches, price_history, positions_by_ticker, caps, environment, live_enabled, instance_id, brokerage_id, ts, llm_call=None, log=..., now_ts=...) -> list[dict]`:
  - For each live match's markets: compute mid from `yes_bid`/`yes_ask` (fallback to ask), append to `price_history[ticker]` (capped length), `detect_move`.
  - On a move (or cadence): optionally call the analyst LLM with the event + fresh news for a bounded tilt.
  - `live_fair(...)`; map side→prematch prob from the match's model probs.
  - `decide(...)` with `allow_open = (phase==LIVE) or move.moved`.
  - Execute via `should_execute(environment, live_enabled)` gate using `client.submit_order` (add/open) / a reduce via opposite side or `cancel`+resize (v1: reduce = no-op log + record; exit handled by selling — record intent). Write a `decision_doc(..., decision=..., )` row with `in_play=True`.
  - Returns the decision rows; the engine persists them.
- [ ] Test with a fake client returning crafted markets + a price_history that triggers a move → asserts an add/exit decision row with `in_play=True`, and dry-run respected for live env without live_enabled.

---

### Task 7: EngineConfig + instance_config + engine loop integration

**Files:** Modify `backend/kalshi/engine.py`, `backend/kalshi/instance_config.py`; Test `backend/tests/test_kalshi_instance_config.py` (extend), `backend/tests/test_kalshi_engine.py` (extend if cheap)

- [ ] `EngineConfig` gains `live_monitoring: bool = True`, `live_poll_seconds: int = 30`, `inplay_caps: InPlayCaps = field(default_factory=InPlayCaps)`.
- [ ] `instance_config.normalize_config`: persist `live_monitoring` (bool, default True), `live_poll_seconds` (clamped ≥10, default 30), and in-play cap fields. Add `inplay_caps_from_config(config)`.
- [ ] `runner.py`: pass `live_monitoring`, `live_poll_seconds`, `inplay_caps` into `EngineConfig`.
- [ ] Engine loop:
  - Change `analyst_cache` TTL to **wall-clock**: store `{"ts": time.time(), "out": ...}`, refresh when `time.time()-ts > 1800`.
  - Add a persistent `price_history: dict = {}` before the loop.
  - After building `fixtures_in`, compute each match's `phase` via `match_clock` and split: pregame/unknown-without-move → pre-match planner (existing path); live → collect for the monitor.
  - Call `monitor.run_live_step(...)` for live matches; persist returned rows; log a LIVE summary.
  - `any_live = bool(live_matches)`; at loop end `time.sleep(config.live_poll_seconds if (config.live_monitoring and any_live) else config.poll_seconds)`.
- [ ] Tests: `normalize_config` round-trips the new fields with clamping/defaults; `inplay_caps_from_config` maps values.

---

### Task 8: Config toggle UI (web + mobile)

**Files:** Modify `frontend/src/components/kalshi/KalshiCreateInstanceModal.vue`; `mobile/lib/features/kalshi/presentation/kalshi_screen.dart` (create sheet); ensure PATCH/POST bodies carry `live_monitoring`.

- [ ] Web: add a styled toggle "Live in-match trading" (with InfoTip explaining it monitors live matches and trades two-way in-play) bound into the create + edit config payload (`live_monitoring`, default true).
- [ ] Mobile: add the same toggle to `_CreateInstanceSheet` config and include it in the create/update body.
- [ ] Verify the backend `CreateKalshiInstanceBody` / `UpdateKalshiInstanceBody` accept + persist `live_monitoring` (add field if missing; `normalize_config` already stores it).

---

### Final: full suite + bug sweep + push

- [ ] `cd backend && python3 -m pytest tests/test_kalshi_*.py tests/test_notification_types_kalshi_runtime.py -q` all green.
- [ ] `gitnexus_detect_changes()` — confirm scope, warn on HIGH/CRITICAL.
- [ ] Parallel adversarial bug-sweep of the live decision/execution path + crash guard.
- [ ] Commit + push with the co-author footer.
