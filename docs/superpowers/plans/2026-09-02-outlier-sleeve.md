# Outlier Sleeve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second run-once lane, `outlier_sleeve`, that buys 52-week-high / top-decile-RS breakouts confirmed by Nexus peer breadth, sized 1.5% of NAV per slot, never rebalanced down, exited only on 5 closes below SMA-200 or a 12-week time stop — living in a NEW Strategies doc beside the untouched bil25 `strategy_eb` core, engine-tested against pre-registered thresholds.

**Architecture:** A pure, clock-free module (`backend/outlier_sleeve.py`) holds every decision rule; a thin wrapper (`backend/strategies/outlier_sleeve.py`) adapts it to the broker's run-once contract and reads a point-in-time feature table through an injected store. Features are precomputed offline from Alpaca daily bars into `OutlierUniverseFeatures` (id `"YYYY-MM-DD|SYM"`) and Nexus peer sets exported once into `OutlierGraphPeers`. Three small broker edits let a non-EB lane declare its own cap and risk envelope and keep the EB lane's live bars.

**Tech Stack:** Python 3.14, pytest, `db.store` (Postgres 17 JSONB via `backend/db/`, FakeStore in tests), Alpaca market-data REST (IEX feed), neo4j driver (export script only), the IntelliStock API via `scripts/_api.py`.

**Spec:** `docs/superpowers/specs/2026-09-02-outlier-sleeve-design.md`

## Global Constraints

- No module outside `backend/db/` opens a database connection; reads/writes go through `from db import store`. `store.between(lo, hi)` is `[lo, hi)`. `store.insert(..., conflict="replace")` is the idempotent write. Never write `CREATE INDEX` at a call site — DDL lives in `backend/db/schema.py`.
- Every table id is `text COLLATE "C"`; the feature table id is exactly `f"{date}|{symbol}"` with `date` = `YYYY-MM-DD` and `symbol` upper-case.
- Point-in-time rule: a decision at `current_time` may read only sessions whose NY date is STRICTLY EARLIER than the NY date of `current_time` (same rule as `strategy_x.pit_daily_observations`).
- The lane never emits `sell_fraction` except for the winner cap (spec §5) and never re-targets a held name.
- Run-once contract (spec §7, explorer): return `{sym: 1|0|-1}` plus `_nexus_position_sizes` (`{"buy_cash": $}` or `{"sell_fraction": f}` and `_cash_reserve_floor_pct: 0.0`), `_nexus_discovered`, `_nexus_executable_buys`, `_nexus_sell_enforcement`, `_nexus_action_intents` (`"etf_sell"` for every −1). Class name must be `OutlierSleeve` (PascalCase of the module id).
- Config defaults, verbatim from spec §6: `outlier_sleeve_enabled false, sleeve_fraction 0.15, slot_fraction 0.015, max_slots 10, winner_cap_fraction 0.30, adv_min_usd 10000000, price_min 3.0, min_history_bars 120, breakout_tolerance 0.02, rs_decile_floor 0.90, confirm_enabled true, confirm_min_peers 5, confirm_frac 0.25, screen_weekdays [2], screen_every_n_weeks 2, exit_sma_bars 200, exit_below_sma_closes 5, time_stop_sessions 60, time_stop_gain 0.15, excluded_symbols ["TQQQ","SPY","BIL","QQQ","GLD","GDX","XLE"], broker_max_single_position_pct 0.95, honour_single_position_cap true, live_max_order_fraction 0.7, live_max_symbol_fraction 0.35, live_max_leveraged_fraction 0.7, live_soft_drawdown 0.25, live_hard_drawdown 0.35, live_kill_drawdown 0.45, min_order_usd 25.0`.
- The champion doc 200 and instance `strategy-eb` are never modified by any task. Nothing is pushed to `main` until Task 6 has run (a push restarts the paper instance and kills in-flight backtests).
- Tests: `python3 -m pytest backend/tests/<file> -q` from the repo root; only `backend/` goes on `sys.path` (adding `backend/strategies/` shadows `strategy_x`).
- Subagents, if any, are Opus. Commit after every task with the session's attribution trailer.

---

### Task 1: Pure decision module `backend/outlier_sleeve.py`

**Files:**
- Create: `backend/outlier_sleeve.py`
- Test: `backend/tests/test_outlier_sleeve.py`

**Interfaces:**
- Consumes: `strategy_eb.session_ordinal(session_id) -> int`, `strategy_eb.session_weekday(session_id) -> int` (Monday=0, −1 when unusable). Both already exist.
- Produces (all pure, no I/O, no clock):
  - `DEFAULTS: dict` — the spec §6 keys.
  - `visible_session(current_time, dates) -> str | None` — the latest `YYYY-MM-DD` in `dates` strictly earlier than the NY date of `current_time`.
  - `screen(rows, cfg, peers, held) -> list[str]` — ranked candidate symbols. `rows` is a list of dicts with keys `symbol, close, hi252, ret126, adv20, sma200, n_bars, rs_rank`; `peers` maps symbol → list of peer symbols.
  - `should_screen(session_id, cache, cfg) -> bool`.
  - `exit_decisions(slots, rows_by_sym, session_id, cfg) -> dict[str, str]` — symbol → reason (`"sma"` / `"time"`), MUTATES each slot's `below`/`proven`/`last_eval` counters.
  - `new_slot_orders(candidates, slots, nav, cash, cfg) -> dict[str, float]` — symbol → buy_cash.
  - `winner_cap_trims(slots, positions, prices, nav, cfg) -> dict[str, float]` — symbol → sell_fraction.
  - Slot record shape: `{"entry_px": float, "entry_ordinal": int, "entry_cost": float, "proven": bool, "below": int, "last_eval": str}`.

- [ ] **Step 1: Write the failing tests**

```python
"""Pure decision rules of the outlier sleeve: screen, cadence, exits, sizing, cap."""
import os
import sys
from datetime import datetime, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from outlier_sleeve import (  # noqa: E402
    DEFAULTS, visible_session, screen, should_screen, exit_decisions,
    new_slot_orders, winner_cap_trims,
)


def cfg(**over):
    c = dict(DEFAULTS)
    c["outlier_sleeve_enabled"] = True
    c.update(over)
    return c


def row(symbol, close=100.0, hi252=100.0, ret126=0.5, adv20=5e7, sma200=80.0,
        n_bars=300, rs_rank=0.95):
    return {"symbol": symbol, "close": close, "hi252": hi252, "ret126": ret126,
            "adv20": adv20, "sma200": sma200, "n_bars": n_bars, "rs_rank": rs_rank}


def test_visible_session_is_strictly_earlier_than_the_call_date():
    dates = ["2026-06-01", "2026-06-02", "2026-06-03"]
    # 2026-06-03 20:00Z is 16:00 NY on the 3rd: the 3rd's close is not visible.
    assert visible_session(datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc), dates) == "2026-06-02"
    # 2026-06-04 01:00Z is 21:00 NY on the 3rd: still the 3rd, still not visible.
    assert visible_session(datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc), dates) == "2026-06-02"
    # 2026-06-04 13:30Z is 09:30 NY on the 4th: the 3rd is visible now.
    assert visible_session(datetime(2026, 6, 4, 13, 30, tzinfo=timezone.utc), dates) == "2026-06-03"
    assert visible_session(datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc), dates) is None


def test_screen_requires_breakout_and_top_decile_and_liquidity():
    rows = [row("AAA"),                                   # passes
            row("BBB", close=90.0),                       # 10% under the high
            row("CCC", rs_rank=0.80),                     # not top decile
            row("DDD", adv20=1e6),                        # illiquid
            row("EEE", close=2.0, hi252=2.0),             # penny
            row("FFF", n_bars=50),                        # too young
            row("SPY"),                                   # excluded
            row("HELD")]                                  # already held
    out = screen(rows, cfg(confirm_enabled=False), peers={}, held={"HELD"})
    assert out == ["AAA"]


def test_screen_ranks_by_six_month_return_descending():
    rows = [row("LOW", ret126=0.3), row("HIGH", ret126=1.2), row("MID", ret126=0.6)]
    assert screen(rows, cfg(confirm_enabled=False), peers={}, held=set()) == ["HIGH", "MID", "LOW"]


def test_young_listing_uses_its_all_time_high_and_needs_120_sessions():
    rows = [row("NEW", n_bars=150, hi252=100.0, close=99.0)]
    assert screen(rows, cfg(confirm_enabled=False), peers={}, held=set()) == ["NEW"]
    rows = [row("NEW", n_bars=119, hi252=100.0, close=100.0)]
    assert screen(rows, cfg(confirm_enabled=False), peers={}, held=set()) == []


def test_confirmation_needs_a_quarter_of_at_least_five_peers_hot():
    peers = {"AAA": ["P1", "P2", "P3", "P4", "P5", "P6"], "BBB": ["P1", "P2"]}
    rows = [row("AAA"), row("BBB"),
            row("P1", rs_rank=0.80), row("P2", rs_rank=0.80),   # hot (>= 0.75)
            row("P3", rs_rank=0.10), row("P4", rs_rank=0.10),
            row("P5", rs_rank=0.10), row("P6", rs_rank=0.10)]
    # AAA: 2 of 6 hot = 33% >= 25% with >= 5 peers -> confirmed. BBB: only 2 peers -> rejected.
    for r in rows[2:]:
        r["close"] = 50.0  # peers are not themselves at highs; they must not be candidates
    assert screen(rows, cfg(), peers=peers, held=set()) == ["AAA"]


def test_screen_fires_on_wednesday_close_every_other_week():
    c = cfg()
    cache = {}
    assert should_screen("2026-06-03", cache, c) is True        # a Wednesday, nothing prior
    cache["_outlier_last_screen_ordinal"] = 20_000
    assert should_screen("2026-06-04", cache, c) is False       # Thursday
    # Wednesday 2026-06-10 is 5 sessions after 2026-06-03: too soon at n=2
    from strategy_eb import session_ordinal
    cache["_outlier_last_screen_ordinal"] = session_ordinal("2026-06-03")
    assert should_screen("2026-06-10", cache, c) is False
    assert should_screen("2026-06-17", cache, c) is True
    assert should_screen("2026-06-10", cache, cfg(screen_every_n_weeks=1)) is True


def test_exit_on_the_fifth_consecutive_close_below_sma200():
    from strategy_eb import session_ordinal
    slots = {"AAA": {"entry_px": 100.0, "entry_ordinal": session_ordinal("2026-01-05"),
                     "entry_cost": 150.0, "proven": True, "below": 0, "last_eval": ""}}
    below = {"AAA": row("AAA", close=70.0, sma200=80.0)}
    above = {"AAA": row("AAA", close=90.0, sma200=80.0)}
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    for d in days[:4]:
        assert exit_decisions(slots, below, d, cfg()) == {}
    assert slots["AAA"]["below"] == 4
    # the same session evaluated twice does not double count
    assert exit_decisions(slots, below, days[3], cfg()) == {}
    assert slots["AAA"]["below"] == 4
    assert exit_decisions(slots, above, days[4], cfg()) == {}
    assert slots["AAA"]["below"] == 0
    for d in ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"]:
        exit_decisions(slots, below, d, cfg())
    assert exit_decisions(slots, below, "2026-06-12", cfg()) == {"AAA": "sma"}


def test_time_stop_cuts_a_name_that_never_proved_itself():
    from strategy_eb import session_ordinal
    entry = session_ordinal("2026-01-05")
    slots = {"AAA": {"entry_px": 100.0, "entry_ordinal": entry, "entry_cost": 150.0,
                     "proven": False, "below": 0, "last_eval": ""},
             "BBB": {"entry_px": 100.0, "entry_ordinal": entry, "entry_cost": 150.0,
                     "proven": False, "below": 0, "last_eval": ""}}
    rows = {"AAA": row("AAA", close=105.0, sma200=80.0),      # +5%: never proven
            "BBB": row("BBB", close=120.0, sma200=80.0)}      # +20%: proven, immune
    late = "2026-04-06"   # >= 60 sessions after 2026-01-05
    assert session_ordinal(late) - entry >= 60
    assert exit_decisions(slots, rows, late, cfg()) == {"AAA": "time"}
    assert slots["BBB"]["proven"] is True


def test_new_slots_are_sized_at_one_and_a_half_percent_within_the_sleeve_budget():
    orders = new_slot_orders(["AAA", "BBB", "CCC"], slots={}, nav=10_000.0,
                             cash=10_000.0, cfg=cfg(max_slots=2))
    assert orders == {"AAA": 150.0, "BBB": 150.0}
    # budget = 15% of NAV minus cost basis already committed
    slots = {"OLD": {"entry_px": 1.0, "entry_ordinal": 1, "entry_cost": 1_400.0,
                     "proven": False, "below": 0, "last_eval": ""}}
    orders = new_slot_orders(["AAA", "BBB"], slots=slots, nav=10_000.0,
                             cash=10_000.0, cfg=cfg(max_slots=10))
    assert orders == {"AAA": 100.0}          # only $100 of budget left; BBB gets nothing
    assert new_slot_orders(["AAA"], slots={}, nav=10_000.0, cash=20.0, cfg=cfg()) == {}


def test_winner_cap_trims_only_the_excess_above_thirty_percent():
    slots = {"AAA": {"entry_px": 10.0, "entry_ordinal": 1, "entry_cost": 150.0,
                     "proven": True, "below": 0, "last_eval": ""}}
    trims = winner_cap_trims(slots, positions={"AAA": 40.0}, prices={"AAA": 100.0},
                             nav=10_000.0, cfg=cfg())
    # market value 4,000 = 40% of NAV; cap 30% = 3,000; sell 1,000 / 4,000
    assert trims == {"AAA": 0.25}
    assert winner_cap_trims(slots, positions={"AAA": 20.0}, prices={"AAA": 100.0},
                            nav=10_000.0, cfg=cfg()) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_outlier_sleeve.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'outlier_sleeve'`

- [ ] **Step 3: Write the module**

```python
"""Outlier sleeve — pure decision rules. No clock, no I/O, no store.

Buys 52-week-high breakouts with top-decile six-month relative strength,
confirmed by Nexus peer breadth, in small slices that are never rebalanced
down; exits only on a slow trend break or a time stop. Every rule here was
chosen by measurement (spec §2): the SMA-200 exit is the load-bearing one.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from strategy_eb import session_ordinal, session_weekday

_NY = ZoneInfo("America/New_York")

DEFAULTS: dict = {
    "outlier_sleeve_enabled": False,
    "sleeve_fraction": 0.15,
    "slot_fraction": 0.015,
    "max_slots": 10,
    "winner_cap_fraction": 0.30,
    "adv_min_usd": 10_000_000.0,
    "price_min": 3.0,
    "min_history_bars": 120,
    "breakout_tolerance": 0.02,
    "rs_decile_floor": 0.90,
    "confirm_enabled": True,
    "confirm_min_peers": 5,
    "confirm_frac": 0.25,
    "confirm_hot_rank": 0.75,
    "screen_weekdays": [2],
    "screen_every_n_weeks": 2,
    "exit_sma_bars": 200,
    "exit_below_sma_closes": 5,
    "time_stop_sessions": 60,
    "time_stop_gain": 0.15,
    "excluded_symbols": ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"],
    "min_order_usd": 25.0,
    "broker_max_single_position_pct": 0.95,
    "honour_single_position_cap": True,
    "live_max_order_fraction": 0.7,
    "live_max_symbol_fraction": 0.35,
    "live_max_leveraged_fraction": 0.7,
    "live_soft_drawdown": 0.25,
    "live_hard_drawdown": 0.35,
    "live_kill_drawdown": 0.45,
}

LAST_SCREEN_KEY = "_outlier_last_screen_ordinal"


def _f(cfg, key) -> float:
    try:
        v = float(cfg.get(key, DEFAULTS.get(key)))
    except (TypeError, ValueError):
        v = float(DEFAULTS.get(key) or 0.0)
    return v


def _i(cfg, key) -> int:
    try:
        return int(cfg.get(key, DEFAULTS.get(key)))
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key) or 0)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def ny_date(current_time) -> str:
    """`YYYY-MM-DD` of the NY session the call falls in."""
    t = current_time
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(_NY).date().isoformat()


def visible_session(current_time, dates):
    """The latest date in `dates` STRICTLY earlier than the call's NY date.

    A daily row carries the 16:00 close; at 09:30 the same day it is six hours
    in the future. Comparing on the NY calendar date is the whole PIT rule.
    """
    today = ny_date(current_time)
    earlier = [d for d in dates if str(d)[:10] < today]
    return max(earlier) if earlier else None


def _eligible(r, cfg, excluded, held) -> bool:
    sym = str(r.get("symbol") or "").upper()
    if not sym or sym in excluded or sym in held:
        return False
    try:
        close = float(r.get("close") or 0.0)
        adv = float(r.get("adv20") or 0.0)
        n = int(r.get("n_bars") or 0)
    except (TypeError, ValueError):
        return False
    if close < _f(cfg, "price_min") or adv < _f(cfg, "adv_min_usd"):
        return False
    if n < _i(cfg, "min_history_bars"):
        return False
    return r.get("ret126") is not None and r.get("rs_rank") is not None


def screen(rows, cfg, peers, held) -> list:
    """Ranked breakout candidates for ONE visible session's cross-section."""
    cfg = {**DEFAULTS, **(cfg or {})}
    excluded = {str(s).upper() for s in (cfg.get("excluded_symbols") or [])}
    held = {str(s).upper() for s in (held or set())}
    tol = _f(cfg, "breakout_tolerance")
    floor = _f(cfg, "rs_decile_floor")
    rank = {str(r.get("symbol") or "").upper(): r.get("rs_rank")
            for r in rows if r.get("rs_rank") is not None}
    out = []
    for r in rows:
        if not _eligible(r, cfg, excluded, held):
            continue
        close = float(r["close"])
        hi = float(r.get("hi252") or 0.0)
        if hi <= 0 or close < hi * (1.0 - tol):
            continue
        if float(r["rs_rank"]) < floor:
            continue
        sym = str(r["symbol"]).upper()
        if _truthy(cfg.get("confirm_enabled")):
            ps = [p for p in (peers or {}).get(sym, []) if p in rank]
            if len(ps) < _i(cfg, "confirm_min_peers"):
                continue
            hot = sum(1 for p in ps if float(rank[p]) >= _f(cfg, "confirm_hot_rank"))
            if hot / len(ps) < _f(cfg, "confirm_frac"):
                continue
        out.append((float(r["ret126"]), sym))
    out.sort(key=lambda t: (-t[0], t[1]))
    return [s for _, s in out]


def should_screen(session_id, cache, cfg) -> bool:
    """Wednesday's close, every `screen_every_n_weeks` weeks, once per session."""
    cfg = {**DEFAULTS, **(cfg or {})}
    wd = session_weekday(session_id)
    days = {int(d) for d in (cfg.get("screen_weekdays") or DEFAULTS["screen_weekdays"])}
    if wd < 0 or wd not in days:
        return False
    ordinal = session_ordinal(session_id)
    last = (cache or {}).get(LAST_SCREEN_KEY)
    if last is None:
        return True
    try:
        gap = ordinal - int(last)
    except (TypeError, ValueError):
        return True
    # n weeks of sessions, tolerant of one holiday: 5n - 2 (n=2 -> 8).
    return gap >= max(1, 5 * _i(cfg, "screen_every_n_weeks") - 2)


def exit_decisions(slots, rows_by_sym, session_id, cfg) -> dict:
    """symbol -> "sma" | "time" for slots that must be closed this session.

    Mutates the slot counters. Each session is counted once: a second call in
    the same session (15m granularity fires ~26 times) is a no-op.
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    ordinal = session_ordinal(session_id)
    out = {}
    for sym, slot in (slots or {}).items():
        r = (rows_by_sym or {}).get(sym)
        if not r or slot.get("last_eval") == session_id:
            continue
        try:
            close = float(r.get("close") or 0.0)
            sma = r.get("sma200")
            entry = float(slot.get("entry_px") or 0.0)
        except (TypeError, ValueError):
            continue
        if close <= 0 or entry <= 0:
            continue
        slot["last_eval"] = session_id
        if close / entry - 1.0 >= _f(cfg, "time_stop_gain"):
            slot["proven"] = True
        if sma is not None and close < float(sma):
            slot["below"] = int(slot.get("below") or 0) + 1
        else:
            slot["below"] = 0
        if slot["below"] >= _i(cfg, "exit_below_sma_closes"):
            out[sym] = "sma"
            continue
        held_for = ordinal - int(slot.get("entry_ordinal") or ordinal)
        if held_for >= _i(cfg, "time_stop_sessions") and not slot.get("proven"):
            out[sym] = "time"
    return out


def new_slot_orders(candidates, slots, nav, cash, cfg) -> dict:
    """symbol -> buy_cash for free slots, inside the sleeve's NEW-money budget.

    Budget is `sleeve_fraction * nav` minus the COST BASIS of open slots, so a
    winner that has grown past the sleeve does not block new entries — only
    cash newly committed counts (spec §5).
    """
    cfg = {**DEFAULTS, **(cfg or {})}
    free = _i(cfg, "max_slots") - len(slots or {})
    if free <= 0 or nav <= 0:
        return {}
    committed = sum(float(s.get("entry_cost") or 0.0) for s in (slots or {}).values())
    budget = _f(cfg, "sleeve_fraction") * float(nav) - committed
    slice_ = _f(cfg, "slot_fraction") * float(nav)
    spendable = float(cash or 0.0)
    out = {}
    for sym in candidates:
        if len(out) >= free:
            break
        amt = min(slice_, budget, spendable)
        if amt < _f(cfg, "min_order_usd"):
            break
        out[sym] = round(amt, 2)
        budget -= amt
        spendable -= amt
    return out


def winner_cap_trims(slots, positions, prices, nav, cfg) -> dict:
    """symbol -> sell_fraction bringing a name above the cap back TO the cap."""
    cfg = {**DEFAULTS, **(cfg or {})}
    cap = _f(cfg, "winner_cap_fraction")
    if nav <= 0 or cap <= 0:
        return {}
    out = {}
    for sym in (slots or {}):
        try:
            mv = float((positions or {}).get(sym) or 0.0) * float((prices or {}).get(sym) or 0.0)
        except (TypeError, ValueError):
            continue
        if mv > cap * nav:
            out[sym] = round((mv - cap * nav) / mv, 6)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_outlier_sleeve.py -q`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add backend/outlier_sleeve.py backend/tests/test_outlier_sleeve.py
git commit -m "feat(outlier-sleeve): pure decision rules — screen, cadence, exits, sizing, winner cap"
```

---

### Task 2: Feature table registration and point-in-time reader

**Files:**
- Modify: `backend/db/schema.py` (add two names to `ALL_TABLES`, two `TableSpec` entries to `_SPECS`)
- Create: `backend/outlier_features.py`
- Test: `backend/tests/test_outlier_features.py`

**Interfaces:**
- Consumes: `db.store` API (`insert(conflict="replace")`, `between`, `run`, `get`, `get_all`), the `store` pytest fixture (FakeStore without `PG_TEST_DSN`).
- Produces:
  - Tables `OutlierUniverseFeatures` (id `"{date}|{symbol}"`, `prefix_fields=("id",)`) and `OutlierGraphPeers` (id `symbol`).
  - `FEATURES_TABLE = "OutlierUniverseFeatures"`, `PEERS_TABLE = "OutlierGraphPeers"`.
  - `feature_id(date, symbol) -> str`.
  - `compute_features(closes, volumes, dates) -> list[dict]` — per-symbol trailing features (pure).
  - `rank_cross_section(rows, adv_min) -> list[dict]` — adds `rs_rank` per date (pure).
  - `cross_section(store, date) -> list[dict]` — one date's rows.
  - `visible_dates(store, before_date, lookback_days=10) -> list[str]` — distinct dates in `[before-lookback, before)`.
  - `peers_for(store, symbols) -> dict[str, list[str]]`.

- [ ] **Step 1: Write the failing tests**

```python
"""Feature table: pure feature math, cross-sectional ranks, and the PIT reader."""
import os
import sys

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from outlier_features import (  # noqa: E402
    FEATURES_TABLE, PEERS_TABLE, feature_id, compute_features,
    rank_cross_section, cross_section, visible_dates, peers_for,
)


def test_tables_are_registered_with_a_prefix_index_on_id():
    from db import schema
    assert FEATURES_TABLE in schema.ALL_TABLES and PEERS_TABLE in schema.ALL_TABLES
    assert schema.TABLES[FEATURES_TABLE].prefix_fields == ("id",)


def test_feature_id_is_date_pipe_upper_symbol():
    assert feature_id("2026-06-03", "sndk") == "2026-06-03|SNDK"


def test_compute_features_trailing_windows_are_inclusive_and_pit_safe():
    n = 260
    dates = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n)]
    closes = [float(i + 1) for i in range(n)]        # rising 1..260
    volumes = [1000.0] * n
    rows = compute_features(closes, volumes, dates)
    assert len(rows) == n
    last = rows[-1]
    assert last["date"] == dates[-1] and last["close"] == 260.0
    assert last["hi252"] == 260.0                    # includes this session
    assert last["ret126"] == 260.0 / 134.0 - 1.0
    assert last["n_bars"] == 260 and last["first_bar"] == dates[0]
    assert abs(last["sma200"] - sum(range(61, 261)) / 200.0) < 1e-9
    assert abs(last["adv20"] - sum(range(241, 261)) / 20.0 * 1000.0) < 1e-6
    young = rows[100]
    assert young["ret126"] is None and young["sma200"] is None
    assert young["hi252"] == 101.0 and young["n_bars"] == 101


def test_rank_cross_section_is_a_percentile_among_liquid_rows_only():
    rows = [{"date": "d", "symbol": s, "ret126": r, "adv20": a}
            for s, r, a in (("A", 0.1, 1e8), ("B", 0.5, 1e8), ("C", 0.9, 1e8),
                            ("D", 2.0, 1e5), ("E", None, 1e8))]
    out = {r["symbol"]: r["rs_rank"] for r in rank_cross_section(rows, adv_min=1e7)}
    assert out["A"] == 0.0 and out["B"] == 0.5 and out["C"] == 1.0
    assert out["D"] is None and out["E"] is None


def test_reader_returns_one_dates_cross_section_and_visible_dates(store):
    docs = []
    for d in ("2026-06-01", "2026-06-02", "2026-06-03"):
        for s in ("AAA", "BBB"):
            docs.append({"id": feature_id(d, s), "date": d, "symbol": s, "close": 1.0})
    store.insert(FEATURES_TABLE, docs, conflict="replace")
    rows = cross_section(store, "2026-06-02")
    assert sorted(r["symbol"] for r in rows) == ["AAA", "BBB"]
    assert all(r["date"] == "2026-06-02" for r in rows)
    assert visible_dates(store, "2026-06-03") == ["2026-06-01", "2026-06-02"]
    assert visible_dates(store, "2026-06-04") == ["2026-06-01", "2026-06-02", "2026-06-03"]


def test_peers_for_reads_the_exported_sets(store):
    store.insert(PEERS_TABLE, [{"id": "AAA", "sector": "Technology", "peers": ["P1", "P2"]}],
                 conflict="replace")
    assert peers_for(store, ["AAA", "ZZZ"]) == {"AAA": ["P1", "P2"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_outlier_features.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'outlier_features'`

- [ ] **Step 3: Register the tables in `backend/db/schema.py`**

Add the two names to the `ALL_TABLES` tuple (alphabetical position is not enforced; append next to the other `Outlier`/`Nexus` names) and these two entries to `_SPECS`, directly after the `TableSpec("NexusStrategyCache", ...)` entry:

```python
    # Outlier sleeve (spec docs/superpowers/specs/2026-09-02-outlier-sleeve-design.md §4):
    # id = "YYYY-MM-DD|SYMBOL", so one date's cross-section is a single prefix scan.
    TableSpec("OutlierUniverseFeatures", prefix_fields=("id",)),
    TableSpec("OutlierGraphPeers"),
```

- [ ] **Step 4: Write the module**

```python
"""Outlier sleeve feature table: pure feature math and the point-in-time reader.

Rows are written ONLY by scripts/build_outlier_features.py. The reader is
handed a store (db.store in production, the FakeStore fixture in tests) so
nothing here opens a connection or imports the pool.
"""
from __future__ import annotations

from datetime import date as _date, timedelta

FEATURES_TABLE = "OutlierUniverseFeatures"
PEERS_TABLE = "OutlierGraphPeers"

HI_BARS = 252
RET_BARS = 126
ADV_BARS = 20
SMA_BARS = 200
SMA_MIN_BARS = 180


def feature_id(date, symbol) -> str:
    return f"{str(date)[:10]}|{str(symbol).strip().upper()}"


def compute_features(closes, volumes, dates) -> list:
    """Trailing features for ONE symbol, one row per session, oldest first.

    Every window INCLUDES the session itself; a row for date d uses closes
    through d only, so a reader that only touches dates < today is PIT-safe by
    construction.
    """
    out = []
    n = len(closes)
    run_hi = []
    for i in range(n):
        c = float(closes[i])
        lo = max(0, i - HI_BARS + 1)
        hi = max(closes[lo:i + 1])
        ret = (c / float(closes[i - RET_BARS]) - 1.0) if i >= RET_BARS and closes[i - RET_BARS] else None
        a_lo = max(0, i - ADV_BARS + 1)
        adv = sum(float(closes[j]) * float(volumes[j]) for j in range(a_lo, i + 1)) / (i + 1 - a_lo)
        sma = (sum(float(x) for x in closes[i - SMA_BARS + 1:i + 1]) / SMA_BARS
               if i + 1 >= SMA_BARS else None)
        if sma is None and i + 1 >= SMA_MIN_BARS:
            w = closes[:i + 1][-SMA_MIN_BARS:]
            sma = sum(float(x) for x in w) / len(w)
        out.append({"date": str(dates[i])[:10], "close": c, "hi252": float(hi),
                    "ret126": ret, "adv20": adv, "sma200": sma,
                    "first_bar": str(dates[0])[:10], "n_bars": i + 1})
    return out


def rank_cross_section(rows, adv_min) -> list:
    """Attach `rs_rank` (0..1 percentile of ret126) among rows liquid enough to
    be candidates; everything else gets None. Mutates and returns `rows`."""
    liquid = [r for r in rows
              if r.get("ret126") is not None and float(r.get("adv20") or 0.0) >= adv_min]
    liquid.sort(key=lambda r: float(r["ret126"]))
    m = len(liquid)
    for r in rows:
        r["rs_rank"] = None
    for k, r in enumerate(liquid):
        r["rs_rank"] = (k / (m - 1)) if m > 1 else 1.0
    return rows


def cross_section(store, date) -> list:
    """Every row for `date`. `|` sorts below `~`, so `[date|, date|~)` is the
    prefix; bytewise (COLLATE "C") on both stores."""
    d = str(date)[:10]
    return list(store.run(store.between(FEATURES_TABLE, f"{d}|", f"{d}|~")))


def visible_dates(store, before_date, lookback_days=10) -> list:
    """Distinct session dates in [before - lookback, before), ascending."""
    b = _date.fromisoformat(str(before_date)[:10])
    lo = (b - timedelta(days=lookback_days)).isoformat()
    rows = store.run(store.between(FEATURES_TABLE, f"{lo}|", f"{b.isoformat()}|"))
    return sorted({str(r.get("date") or r.get("id", "")[:10]) for r in rows})


def peers_for(store, symbols) -> dict:
    out = {}
    for s in symbols:
        doc = store.get(PEERS_TABLE, str(s).upper())
        if doc and doc.get("peers"):
            out[str(s).upper()] = [str(p).upper() for p in doc["peers"]]
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_outlier_features.py backend/tests/test_schema*.py -q`
Expected: all pass (the schema tests confirm the registry still builds; if a test pins the `ALL_TABLES` length, update its expected count by +2 in the same commit and say so in the report).

- [ ] **Step 6: Commit**

```bash
git add backend/db/schema.py backend/outlier_features.py backend/tests/test_outlier_features.py
git commit -m "feat(outlier-sleeve): feature table registration, pure feature math, PIT reader"
```

---

### Task 3: Offline builder and Nexus peers export scripts

**Files:**
- Create: `scripts/build_outlier_features.py`
- Create: `scripts/export_outlier_peers.py`
- Test: `backend/tests/test_build_outlier_features.py`

**Interfaces:**
- Consumes: `outlier_features.compute_features`, `rank_cross_section`, `feature_id`, `FEATURES_TABLE`, `PEERS_TABLE`; `secret_store.decrypt`; `db.store`.
- Produces: `build_outlier_features.select_liquid(recent_bars, adv_min, price_min, min_bars) -> list[str]` and `build_outlier_features.rows_for_universe(bars_by_symbol, adv_min) -> list[dict]` (pure, tested); CLI `python3 scripts/build_outlier_features.py --start 2020-06-01 --end 2026-08-31 [--brokerage-id ID]`; CLI `python3 scripts/export_outlier_peers.py`.

- [ ] **Step 1: Write the failing tests**

```python
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (os.path.join(_root, "backend"), os.path.join(_root, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from build_outlier_features import select_liquid, rows_for_universe  # noqa: E402


def bars(n, close, vol, start_day=1):
    return [{"t": f"2026-01-{start_day + i:02d}T05:00:00Z", "c": close, "v": vol}
            for i in range(n)]


def test_select_liquid_applies_adv_price_and_history_floors():
    recent = {"BIG": bars(45, 50.0, 1_000_000),      # ADV $50M
              "THIN": bars(45, 50.0, 10_000),        # ADV $0.5M
              "PENNY": bars(45, 2.0, 100_000_000),   # $2
              "NEW": bars(10, 50.0, 1_000_000)}      # too few sessions
    assert select_liquid(recent, adv_min=1e7, price_min=3.0, min_bars=40) == ["BIG"]


def test_rows_for_universe_ranks_within_each_date_and_keys_ids():
    by_sym = {"AAA": bars(3, 10.0, 1e6), "BBB": bars(3, 20.0, 1e6)}
    for i, b in enumerate(by_sym["AAA"]):
        b["c"] = 10.0 + i            # rising
    for i, b in enumerate(by_sym["BBB"]):
        b["c"] = 20.0 - i            # falling
    rows = rows_for_universe(by_sym, adv_min=0.0)
    assert len(rows) == 6
    assert {r["id"] for r in rows} == {f"2026-01-0{d}|{s}" for d in (1, 2, 3) for s in ("AAA", "BBB")}
    # ret126 needs 126 sessions: ranks are None on a 3-bar history, never a crash
    assert all(r["rs_rank"] is None for r in rows)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_build_outlier_features.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'build_outlier_features'`

- [ ] **Step 3: Write the builder**

```python
#!/usr/bin/env python3
"""Build OutlierUniverseFeatures from Alpaca daily bars (IEX, adjusted).

    python3 scripts/build_outlier_features.py --start 2020-06-01 --end 2026-08-31

Universe: US equities on NASDAQ/NYSE/ARCA/AMEX, active AND inactive (the
survivorship guard), alphabetic tickers <= 5 chars, ADV >= $10M and close >= $3
over the last ~90 sessions, plus inactive names that were liquid in 2023-H1.
Measured: ~10k bars per 0.7s page; a full 5.5-year build is minutes.
Idempotent: rows are inserted with conflict="replace".
Credentials: the Alpaca key pair of --brokerage-id (default: the paper account
used by strategy-eb), decrypted through secret_store. Never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from outlier_features import (  # noqa: E402
    FEATURES_TABLE, compute_features, feature_id, rank_cross_section,
)

DEFAULT_BROKERAGE = "bf78ad0c-3073-4aac-97a5-a29c7b043404"
DATA = "https://data.alpaca.markets/v2/stocks/bars"
ASSETS = "https://paper-api.alpaca.markets/v2/assets"
EXCHANGES = {"NASDAQ", "NYSE", "ARCA", "AMEX"}


def select_liquid(recent, adv_min, price_min, min_bars) -> list:
    out = []
    for sym, bb in recent.items():
        if len(bb) < min_bars:
            continue
        adv = sum(float(x["c"]) * float(x["v"]) for x in bb) / len(bb)
        if adv >= adv_min and float(bb[-1]["c"]) >= price_min:
            out.append(sym)
    return sorted(out)


def rows_for_universe(bars_by_symbol, adv_min) -> list:
    """Feature rows for every (date, symbol), ranked within each date."""
    by_date = {}
    for sym, bb in bars_by_symbol.items():
        if not bb:
            continue
        dates = [str(x["t"])[:10] for x in bb]
        rows = compute_features([float(x["c"]) for x in bb], [float(x["v"]) for x in bb], dates)
        for r in rows:
            r["symbol"] = sym.upper()
            r["id"] = feature_id(r["date"], sym)
            by_date.setdefault(r["date"], []).append(r)
    out = []
    for d in sorted(by_date):
        out.extend(rank_cross_section(by_date[d], adv_min))
    return out


def _headers(brokerage_id):
    from db import store
    from secret_store import decrypt
    b = store.get("BrokerageAccounts", brokerage_id)
    if not b:
        raise SystemExit(f"brokerage {brokerage_id} not found")
    return {"APCA-API-KEY-ID": decrypt(b["alpaca_key"]),
            "APCA-API-SECRET-KEY": decrypt(b["alpaca_secret"])}


def _get(url, headers):
    for att in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
                return json.load(r)
        except Exception:
            time.sleep(3 * (att + 1))
    raise SystemExit("alpaca request failed: " + url[:90])


def fetch_bars(symbols, start, end, headers, chunk=150) -> dict:
    out = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        tok = None
        while True:
            q = {"symbols": ",".join(part), "timeframe": "1Day", "start": start, "end": end,
                 "limit": 10000, "feed": "iex", "adjustment": "all"}
            if tok:
                q["page_token"] = tok
            d = _get(DATA + "?" + urllib.parse.urlencode(q), headers)
            for s, bb in (d.get("bars") or {}).items():
                out.setdefault(s, []).extend(bb)
            tok = d.get("next_page_token")
            if not tok:
                break
    return out


def candidate_symbols(headers) -> list:
    assets = (_get(ASSETS + "?status=active&asset_class=us_equity", headers)
              + _get(ASSETS + "?status=inactive&asset_class=us_equity", headers))
    return sorted({a["symbol"] for a in assets
                   if a.get("exchange") in EXCHANGES and a["symbol"].isalpha() and len(a["symbol"]) <= 5})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--start", default="2020-06-01")
    ap.add_argument("--end", required=True)
    ap.add_argument("--brokerage-id", default=DEFAULT_BROKERAGE)
    ap.add_argument("--adv-min", type=float, default=1e7)
    ap.add_argument("--price-min", type=float, default=3.0)
    args = ap.parse_args(argv)
    from db import store
    headers = _headers(args.brokerage_id)
    syms = candidate_symbols(headers)
    print(f"candidates {len(syms)}", flush=True)
    recent_start = _shift(args.end, -130)
    recent = fetch_bars(syms, recent_start, args.end, headers)
    liquid = select_liquid(recent, args.adv_min, args.price_min, 40)
    dead = [s for s in syms if s not in recent]
    old = fetch_bars(dead, "2023-01-01", "2023-06-30", headers)
    liquid = sorted(set(liquid) | set(select_liquid(old, args.adv_min, args.price_min, 60)))
    print(f"liquid universe {len(liquid)}", flush=True)
    bars = fetch_bars(liquid, args.start, args.end, headers)
    rows = rows_for_universe(bars, args.adv_min)
    print(f"rows {len(rows)}", flush=True)
    for i in range(0, len(rows), 5000):
        store.insert(FEATURES_TABLE, rows[i:i + 5000], conflict="replace")
    print("done", flush=True)
    return 0


def _shift(day, days):
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write the peers export**

```python
#!/usr/bin/env python3
"""Export Nexus peer sets to OutlierGraphPeers (one row per ticker).

    python3 scripts/export_outlier_peers.py

Peers = COMPETES_WITH | SUPPLIER_OF | STRATEGIC_PARTNER | PARENT_OF | CONTROLS,
either direction. Static structure (industry membership), not a dated signal;
the lane never queries Neo4j itself (strict-PIT replay forbids new Cypher).
Needs NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD in the environment.
"""
from __future__ import annotations

import collections
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "backend"))

from outlier_features import PEERS_TABLE  # noqa: E402

_Q_NODES = "MATCH (c:Company) WHERE c.ticker IS NOT NULL RETURN c.ticker AS t, c.sector AS s, c.industry AS i"
_Q_EDGES = ("MATCH (a:Company)-[:COMPETES_WITH|SUPPLIER_OF|STRATEGIC_PARTNER|PARENT_OF|CONTROLS]-(b:Company) "
            "WHERE a.ticker IS NOT NULL AND b.ticker IS NOT NULL RETURN a.ticker AS a, b.ticker AS b")


def main():
    from neo4j import GraphDatabase
    from db import store
    drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
                               auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
                               connection_timeout=15)
    sector, industry, peers = {}, {}, collections.defaultdict(set)
    with drv.session() as s:
        for r in s.run(_Q_NODES):
            sector[r["t"]] = r["s"]
            industry[r["t"]] = r["i"]
        for r in s.run(_Q_EDGES):
            peers[r["a"]].add(r["b"])
    drv.close()
    docs = [{"id": t.upper(), "sector": sector.get(t), "industry": industry.get(t),
             "peers": sorted(p.upper() for p in ps)} for t, ps in peers.items()]
    for i in range(0, len(docs), 2000):
        store.insert(PEERS_TABLE, docs[i:i + 2000], conflict="replace")
    print(f"exported {len(docs)} tickers with peers", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_build_outlier_features.py -q`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add scripts/build_outlier_features.py scripts/export_outlier_peers.py backend/tests/test_build_outlier_features.py
git commit -m "feat(outlier-sleeve): offline feature builder and Nexus peers export"
```

---

### Task 4: The wrapper lane `backend/strategies/outlier_sleeve.py`

**Files:**
- Create: `backend/strategies/outlier_sleeve.py`
- Test: `backend/tests/test_outlier_sleeve_run_once.py`

**Interfaces:**
- Consumes: everything from Task 1 and Task 2 (`cross_section`, `visible_dates`, `peers_for`), `strategy_eb.session_ordinal`.
- Produces: class `OutlierSleeve` with `run_once(self, symbols, prices, current_time, config, conditions, data=None, portfolio_emulator=None, strategy_cache=None, time_increment=None, mode=None, **kwargs) -> dict`; module attribute `store` (the injected store; tests monkeypatch it); cache keys `SLOTS_KEY = "_outlier_slots"`, `LAST_SCREEN_KEY` (from Task 1), `LAST_DECISION_KEY = "_outlier_last"`.

- [ ] **Step 1: Write the failing tests**

```python
"""Broker contract of the outlier sleeve wrapper."""
import json
import os
import re
import sys
from datetime import datetime, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

import strategies.outlier_sleeve as mod  # noqa: E402
from strategies.outlier_sleeve import OutlierSleeve, SLOTS_KEY  # noqa: E402
from outlier_sleeve import DEFAULTS, LAST_SCREEN_KEY  # noqa: E402
from outlier_features import FEATURES_TABLE, PEERS_TABLE, feature_id  # noqa: E402

#: Called Thursday 2026-06-04 09:35 NY: Wednesday 2026-06-03's row is visible.
DECIDES = datetime(2026, 6, 4, 13, 35, tzinfo=timezone.utc)
SKIPS = datetime(2026, 6, 5, 13, 35, tzinfo=timezone.utc)      # sees Thursday
VISIBLE = "2026-06-03"


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None):
        self._cash = cash
        self._positions = dict(positions or {})

    def get_cash(self):
        return self._cash

    def get_buying_power(self, reserved=0.0, prices=None):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or {}
        return self._cash + sum(q * float(px.get(s, 0.0)) for s, q in self._positions.items())


def cfg(**over):
    c = dict(DEFAULTS)
    c["outlier_sleeve_enabled"] = True
    c["confirm_enabled"] = False
    c.update(over)
    return c


def seed(store, date=VISIBLE, rows=None):
    rows = rows or [
        {"symbol": "AAA", "close": 100.0, "hi252": 100.0, "ret126": 0.8, "adv20": 5e7, "sma200": 70.0, "n_bars": 300},
        {"symbol": "BBB", "close": 50.0, "hi252": 50.0, "ret126": 0.6, "adv20": 5e7, "sma200": 40.0, "n_bars": 300},
        {"symbol": "CCC", "close": 10.0, "hi252": 20.0, "ret126": -0.3, "adv20": 5e7, "sma200": 12.0, "n_bars": 300},
    ]
    docs = []
    for r in rows:
        d = dict(r)
        d["date"] = date
        d["id"] = feature_id(date, r["symbol"])
        d.setdefault("rs_rank", 0.95 if r["ret126"] > 0 else 0.1)
        docs.append(d)
    store.insert(FEATURES_TABLE, docs, conflict="replace")


def test_disabled_or_no_emulator_emits_nothing(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    seed(store)
    assert OutlierSleeve().run_once([], {}, DECIDES, dict(DEFAULTS), {}, portfolio_emulator=FakeEmulator()) == {}
    assert OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=None) == {}


def test_a_screen_day_buys_ranked_candidates_at_one_and_a_half_percent(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    seed(store)
    cache = {}
    out = OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=FakeEmulator(),
                                   strategy_cache=cache)
    assert out["AAA"] == 1 and out["BBB"] == 1 and "CCC" not in out
    sizes = out["_nexus_position_sizes"]
    assert sizes["AAA"] == {"buy_cash": 150.0} and sizes["BBB"] == {"buy_cash": 150.0}
    assert sizes["_cash_reserve_floor_pct"] == 0.0
    assert set(out["_nexus_discovered"]) == {"AAA", "BBB"}
    assert out["_nexus_executable_buys"] == ["AAA", "BBB"]
    assert cache[SLOTS_KEY]["AAA"]["entry_px"] == 100.0
    assert cache[SLOTS_KEY]["AAA"]["entry_cost"] == 150.0
    assert cache[LAST_SCREEN_KEY] > 0


def test_off_days_hold_and_keep_the_held_names_in_the_universe(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    seed(store)
    seed(store, date="2026-06-04")
    cache = {}
    OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=FakeEmulator(), strategy_cache=cache)
    emu = FakeEmulator(cash=9700.0, positions={"AAA": 1.5, "BBB": 3.0})
    out = OutlierSleeve().run_once([], {"AAA": 100.0, "BBB": 50.0}, SKIPS, cfg(), {},
                                   portfolio_emulator=emu, strategy_cache=cache)
    assert out.get("AAA", 0) == 0 and out.get("BBB", 0) == 0
    assert set(out["_nexus_discovered"]) == {"AAA", "BBB"}
    assert out["_nexus_executable_buys"] == []


def test_a_trend_break_sells_with_the_etf_sell_intent(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    from strategy_eb import session_ordinal
    cache = {SLOTS_KEY: {"AAA": {"entry_px": 100.0, "entry_ordinal": session_ordinal("2026-01-05"),
                                 "entry_cost": 150.0, "proven": True, "below": 4, "last_eval": ""}}}
    seed(store, rows=[{"symbol": "AAA", "close": 60.0, "hi252": 100.0, "ret126": -0.2, "adv20": 5e7,
                       "sma200": 70.0, "n_bars": 300}])
    emu = FakeEmulator(cash=100.0, positions={"AAA": 1.5})
    out = OutlierSleeve().run_once([], {"AAA": 60.0}, SKIPS, cfg(), {}, portfolio_emulator=emu,
                                   strategy_cache=cache)
    assert out["AAA"] == -1
    assert out["_nexus_sell_enforcement"] == ["AAA"]
    assert out["_nexus_action_intents"] == {"AAA": "etf_sell"}
    assert "AAA" not in cache[SLOTS_KEY]


def test_winner_cap_emits_a_partial_sell_fraction(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    from strategy_eb import session_ordinal
    cache = {SLOTS_KEY: {"AAA": {"entry_px": 10.0, "entry_ordinal": session_ordinal("2026-01-05"),
                                 "entry_cost": 150.0, "proven": True, "below": 0, "last_eval": ""}}}
    seed(store, rows=[{"symbol": "AAA", "close": 100.0, "hi252": 100.0, "ret126": 3.0, "adv20": 5e7,
                       "sma200": 60.0, "n_bars": 300}])
    emu = FakeEmulator(cash=6000.0, positions={"AAA": 40.0})     # 4,000 of 10,000 = 40%
    out = OutlierSleeve().run_once([], {"AAA": 100.0}, SKIPS, cfg(), {}, portfolio_emulator=emu,
                                   strategy_cache=cache)
    assert out["AAA"] == -1
    assert out["_nexus_position_sizes"]["AAA"] == {"sell_fraction": 0.25}
    assert out["_nexus_action_intents"] == {"AAA": "etf_sell"}
    assert "AAA" in cache[SLOTS_KEY]        # a trim keeps the slot


def test_no_visible_session_refuses_to_trade(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    assert OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=FakeEmulator(),
                                    strategy_cache={}) == {}


def test_the_schema_header_contains_exactly_every_default():
    path = os.path.join(_backend, "strategies", "outlier_sleeve.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert set(schema["config"]) == set(DEFAULTS)
    assert schema["strategy"] == "outlier_sleeve"
    assert schema["execution_scope"] == "run_once"
    assert schema["decision_phase"] == "pre"
    assert schema["execution_position"] == 20


def test_the_class_name_matches_what_the_broker_derives_from_the_id():
    from strategies_meta import _module_to_class_name
    derived = _module_to_class_name("outlier_sleeve")
    assert derived == "OutlierSleeve" and hasattr(mod, derived)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_outlier_sleeve_run_once.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'strategies.outlier_sleeve'`

- [ ] **Step 3: Write the wrapper**

The first line MUST be the schema header on one line; build it from `DEFAULTS` mentally and write it out verbatim (the test pins the key set):

```python
# INTELLISTOCK_SCHEMA: {"strategy": "outlier_sleeve", "weight": 1.0, "execution_position": 20, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"outlier_sleeve_enabled": false, "sleeve_fraction": 0.15, "slot_fraction": 0.015, "max_slots": 10, "winner_cap_fraction": 0.3, "adv_min_usd": 10000000.0, "price_min": 3.0, "min_history_bars": 120, "breakout_tolerance": 0.02, "rs_decile_floor": 0.9, "confirm_enabled": true, "confirm_min_peers": 5, "confirm_frac": 0.25, "confirm_hot_rank": 0.75, "screen_weekdays": [2], "screen_every_n_weeks": 2, "exit_sma_bars": 200, "exit_below_sma_closes": 5, "time_stop_sessions": 60, "time_stop_gain": 0.15, "excluded_symbols": ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"], "min_order_usd": 25.0, "broker_max_single_position_pct": 0.95, "honour_single_position_cap": true, "live_max_order_fraction": 0.7, "live_max_symbol_fraction": 0.35, "live_max_leveraged_fraction": 0.7, "live_soft_drawdown": 0.25, "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45}}
# INTELLISTOCK_DESCRIPTION: Outlier sleeve — buys 52-week-high breakouts with top-decile six-month relative strength, confirmed by Nexus peer breadth, in 1.5%-of-NAV slices that are never rebalanced down, and exits only on five closes below the 200-day average or a 12-week time stop. Captures the power-law tail (SNDK, SMCI, CLS) beside the EB core. No LLM, no news.
"""Outlier sleeve wrapper: the broker's run-once contract around the pure rules.

Reads ONE visible session's cross-section from OutlierUniverseFeatures through
the injected `store`; never touches bars, never queries Neo4j.
"""
from __future__ import annotations

import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from db import store  # noqa: E402  (tests monkeypatch this name)
from outlier_features import cross_section, peers_for, visible_dates  # noqa: E402
from outlier_sleeve import (  # noqa: E402
    DEFAULTS, LAST_SCREEN_KEY, exit_decisions, new_slot_orders, ny_date,
    screen, should_screen, visible_session, winner_cap_trims,
)
from strategy_eb import session_ordinal  # noqa: E402

try:
    from colorama_utils import log as _log  # type: ignore
except Exception:  # pragma: no cover
    def _log(msg, color="white"):
        print(msg)

SLOTS_KEY = "_outlier_slots"
LAST_DECISION_KEY = "_outlier_last"
_SELL_INTENT = "etf_sell"


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _emit(decisions, sizes, universe) -> dict:
    out = dict(decisions)
    sizes = dict(sizes)
    sizes["_cash_reserve_floor_pct"] = 0.0
    out["_nexus_position_sizes"] = sizes
    out["_nexus_discovered"] = sorted(universe)
    out["_nexus_executable_buys"] = sorted(s for s, d in decisions.items() if d == 1)
    out["_nexus_sell_enforcement"] = sorted(s for s, d in decisions.items() if d == -1)
    out["_nexus_action_intents"] = {s: _SELL_INTENT for s, d in decisions.items() if d == -1}
    return out


class OutlierSleeve:
    def run_once(self, symbols, prices, current_time, config, conditions,
                 data=None, portfolio_emulator=None, strategy_cache=None,
                 time_increment=None, mode=None, **kwargs):
        cfg = {**DEFAULTS, **(config or {})}
        if not _truthy(cfg.get("outlier_sleeve_enabled", False)) or portfolio_emulator is None:
            return {}
        cache = strategy_cache if isinstance(strategy_cache, dict) else {}
        slots = cache.setdefault(SLOTS_KEY, {})

        session = visible_session(current_time, visible_dates(store, ny_date(current_time)))
        if session is None:
            _log("OutlierSleeve: REFUSING to trade — no visible feature row before "
                 f"{ny_date(current_time)}; run scripts/build_outlier_features.py", "red")
            return {}
        rows = cross_section(store, session)
        rows_by_sym = {str(r.get("symbol") or "").upper(): r for r in rows}

        eff = {str(s).upper(): float(v) for s, v in (prices or {}).items() if v}
        for sym in slots:
            if not eff.get(sym) and rows_by_sym.get(sym):
                eff[sym] = float(rows_by_sym[sym].get("close") or 0.0)
        nav = float(portfolio_emulator.get_portfolio_value(eff) or 0.0)
        if nav <= 0:
            return {}
        positions = portfolio_emulator.get_positions() or {}

        decisions, sizes = {}, {}
        # 1. exits — unconditional, every call, once per session
        for sym, why in exit_decisions(slots, rows_by_sym, session, cfg).items():
            decisions[sym] = -1
            slots.pop(sym, None)
            _log(f"OutlierSleeve {session} | EXIT {sym} ({why})", "yellow")
        # 2. winner cap — a partial sell that keeps the slot
        for sym, frac in winner_cap_trims(slots, positions, eff, nav, cfg).items():
            if sym in decisions:
                continue
            decisions[sym] = -1
            sizes[sym] = {"sell_fraction": frac}
            _log(f"OutlierSleeve {session} | CAP {sym} sell {frac:.1%}", "yellow")
        # 3. entries — on the screen cadence only
        if should_screen(session, cache, cfg):
            held = set(slots) | {str(s).upper() for s, q in positions.items() if float(q or 0) > 0}
            peers = peers_for(store, [r["symbol"] for r in rows]) if _truthy(cfg.get("confirm_enabled")) else {}
            ranked = screen(rows, cfg, peers, held)
            try:
                cash = float(portfolio_emulator.get_buying_power(prices=eff) or 0.0)
            except (AttributeError, TypeError):
                cash = float(portfolio_emulator.get_cash() or 0.0)
            ordinal = session_ordinal(session)
            for sym, amt in new_slot_orders(ranked, slots, nav, cash, cfg).items():
                px = float(rows_by_sym[sym].get("close") or 0.0)
                decisions[sym] = 1
                sizes[sym] = {"buy_cash": amt}
                slots[sym] = {"entry_px": px, "entry_ordinal": ordinal, "entry_cost": amt,
                              "proven": False, "below": 0, "last_eval": session}
            cache[LAST_SCREEN_KEY] = ordinal
            _log(f"OutlierSleeve {session} | screen: {len(ranked)} candidates, "
                 f"{sum(1 for d in decisions.values() if d == 1)} entries | nav=${nav:,.0f}", "cyan")

        universe = set(slots) | set(decisions)
        cache[LAST_DECISION_KEY] = {"session": session, "slots": sorted(slots), "orders": len(decisions)}
        if not decisions:
            return _emit({s: 0 for s in slots}, {}, universe) if slots else {}
        return _emit(decisions, sizes, universe)
```

If `colorama_utils.log` does not exist in this repo, copy the `_log` fallback pattern from `backend/strategies/strategy_eb.py` lines 46-56 verbatim instead.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_outlier_sleeve_run_once.py backend/tests/test_outlier_sleeve.py -q`
Expected: all pass. Also run `python3 -m pytest backend/tests/test_dead_config_registry.py -q` — it scans every strategy header; if it reports the new keys as unregistered, add them where that test's registry lives (it names the file) in the same commit.

- [ ] **Step 5: Commit**

```bash
git add backend/strategies/outlier_sleeve.py backend/tests/test_outlier_sleeve_run_once.py
git commit -m "feat(outlier-sleeve): run-once wrapper — PIT feature reads, entries, exits, winner cap"
```

---

### Task 5: Broker admission of a second lane (sole-lane guard, cap, risk limits)

**Files:**
- Modify: `backend/live_equity_bars.py:38-43` (`EB_STRATEGY_NAMES` → add a companion set) and `other_enabled_run_once_lanes`
- Modify: `backend/broker.py:4395-4422` (`_strategy_eb_single_position_pct`), `backend/broker.py:4425-4470` (`_strategy_eb_risk_limits`)
- Test: `backend/tests/test_live_equity_bars.py` (extend), `backend/tests/test_live_risk_limits.py` (extend)

**Interfaces:**
- Consumes: `outlier_sleeve.DEFAULTS`, `live_risk_state.RiskLimits`.
- Produces: `live_equity_bars.COMPANION_LANES = frozenset({"outlier_sleeve", "outliersleeve"})` — lanes allowed beside EB; `_strategy_eb_single_position_pct` returns the MAX cap across enabled lanes in `{strategy_eb, outlier_sleeve}` that opt in; `_strategy_eb_risk_limits` returns the WIDEST envelope (max of each fraction, max of each drawdown threshold) across those lanes.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_live_equity_bars.py`:

```python
def test_the_outlier_sleeve_is_a_permitted_companion_lane():
    from live_equity_bars import other_enabled_run_once_lanes
    specs = [{"strategy": "strategy_eb", "weight": 1.0, "config": {"strategy_eb_enabled": True}},
             {"strategy": "outlier_sleeve", "weight": 1.0, "config": {"outlier_sleeve_enabled": True}}]
    assert other_enabled_run_once_lanes(specs) == []
    specs.append({"strategy": "graph_nexus_analysis", "weight": 1.0, "config": {}})
    assert other_enabled_run_once_lanes(specs) == ["graph_nexus_analysis"]
```

Append to `backend/tests/test_live_risk_limits.py` (it already imports `broker` symbols through an AST/regex harness or a direct import — follow whichever pattern the file uses for `_strategy_eb_risk_limits`; the assertions are):

```python
def test_the_outlier_lane_widens_the_symbol_fraction_and_the_cap():
    from broker import _strategy_eb_risk_limits, _strategy_eb_single_position_pct
    specs = [{"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True,
              "honour_single_position_cap": True, "broker_max_single_position_pct": 0.95}},
             {"strategy": "outlier_sleeve", "config": {"outlier_sleeve_enabled": True,
              "honour_single_position_cap": True, "broker_max_single_position_pct": 0.95}}]
    limits = _strategy_eb_risk_limits(specs)
    assert limits.max_symbol_fraction == 0.7        # EB's 0.7 is wider than the sleeve's 0.35
    assert limits.max_order_fraction == 0.7 and limits.kill == 0.45
    assert _strategy_eb_single_position_pct(specs) == 0.95
    only_sleeve = [specs[1]]
    assert _strategy_eb_risk_limits(only_sleeve).max_symbol_fraction == 0.35
    assert _strategy_eb_single_position_pct(only_sleeve) == 0.95
    disabled = [{"strategy": "outlier_sleeve", "config": {"outlier_sleeve_enabled": False,
                 "honour_single_position_cap": True, "broker_max_single_position_pct": 0.95}}]
    assert _strategy_eb_risk_limits(disabled) is None
    assert _strategy_eb_single_position_pct(disabled) is None
```

If `broker` cannot be imported directly in that test file (argparse at module scope SystemExits under pytest — see the note in `backend/strategy_eb.py`), use the same AST-extraction harness the file already uses for `_strategy_eb_single_position_pct` and evaluate both functions through it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_live_equity_bars.py backend/tests/test_live_risk_limits.py -q`
Expected: the two new tests FAIL (`["outlier_sleeve"]` returned; `max_symbol_fraction` None/AttributeError).

- [ ] **Step 3: Whitelist the companion lane in `backend/live_equity_bars.py`**

Below `EB_STRATEGY_NAMES` add:

```python
#: Lanes allowed beside strategy_eb on one document. The guard exists because
#: graph_nexus_analysis reads `data is not None` as "backtest"; the outlier
#: sleeve reads no bars at all (it reads OutlierUniverseFeatures), so handing
#: bars to a document that carries it changes nothing for it.
COMPANION_LANES = frozenset({"outlier_sleeve", "outliersleeve"})
```

and in `other_enabled_run_once_lanes` change the skip line to:

```python
        if not name or name in EB_STRATEGY_NAMES or name in COMPANION_LANES:
            continue
```

- [ ] **Step 4: Generalise the cap and the risk envelope in `backend/broker.py`**

Replace the body of `_strategy_eb_single_position_pct` so it scans BOTH lanes and returns the max:

```python
def _strategy_eb_single_position_pct(cached_strategies):
    """Per-document single-position cap for LIVE ticks, mirroring
    backtest_engine._instance_single_position_pct: honoured only when an
    ENABLED strategy_eb or outlier_sleeve lane opts in via
    `honour_single_position_cap` and carries a valid (0, 1]
    `broker_max_single_position_pct` (bools rejected). The WIDEST opted-in cap
    wins: the cap is buy-admission only (17455), and the outlier sleeve's whole
    design is a slice that is allowed to grow. Returns None everywhere else so
    every other document keeps the 15% failsafe."""
    best = None
    try:
        for spec in (cached_strategies or []):
            name = str((spec or {}).get("strategy", "")).strip().lower()
            flag = _LANE_ENABLE_FLAGS.get(name)
            if flag is None:
                continue
            cfg = dict((spec or {}).get("conditions") or {})
            cfg.update((spec or {}).get("config") or {})
            if not _truthy(cfg.get(flag, False)):
                continue
            if not _truthy(cfg.get("honour_single_position_cap", False)):
                continue
            raw = cfg.get("broker_max_single_position_pct")
            if isinstance(raw, bool) or raw is None or raw == "":
                continue
            val = float(raw)
            if 0.0 < val <= 1.0 and (best is None or val > best):
                best = val
    except Exception:
        return None
    return best
```

Add, directly above it, the lane table:

```python
#: run_once lanes whose config may widen this document's live envelope, and the
#: config flag that says the lane is actually ON. Anything else is ignored: a
#: document that carries none of these keeps live_risk_state's defaults.
_LANE_ENABLE_FLAGS = {
    "strategy_eb": "strategy_eb_enabled", "strategyeb": "strategy_eb_enabled",
    "outlier_sleeve": "outlier_sleeve_enabled", "outliersleeve": "outlier_sleeve_enabled",
}
```

Replace the `try:` body of `_strategy_eb_risk_limits` (keep its docstring and the import block with the same fallbacks) with:

```python
    try:
        from outlier_sleeve import DEFAULTS as _OS_DEFAULTS
    except Exception:
        _OS_DEFAULTS = {}
    defaults_by_lane = {"strategy_eb": _EB_DEFAULTS, "strategyeb": _EB_DEFAULTS,
                        "outlier_sleeve": _OS_DEFAULTS, "outliersleeve": _OS_DEFAULTS}
    widest = None
    try:
        for spec in (cached_strategies or []):
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("strategy") or "").strip().lower()
            flag = _LANE_ENABLE_FLAGS.get(name)
            if flag is None:
                continue
            merged = {**defaults_by_lane[name], **(spec.get("config") or {})}
            if not _truthy(merged.get(flag, False)):
                continue
            mine = RiskLimits(
                max_order_fraction=merged["live_max_order_fraction"],
                max_symbol_fraction=merged["live_max_symbol_fraction"],
                max_leveraged_fraction=merged["live_max_leveraged_fraction"],
                soft=merged["live_soft_drawdown"],
                hard=merged["live_hard_drawdown"],
                kill=merged["live_kill_drawdown"],
            )
            if widest is None:
                widest = mine
            else:
                widest = RiskLimits(
                    max_order_fraction=max(widest.max_order_fraction, mine.max_order_fraction),
                    max_symbol_fraction=max(widest.max_symbol_fraction, mine.max_symbol_fraction),
                    max_leveraged_fraction=max(widest.max_leveraged_fraction, mine.max_leveraged_fraction),
                    soft=max(widest.soft, mine.soft), hard=max(widest.hard, mine.hard),
                    kill=max(widest.kill, mine.kill),
                )
    except Exception as _eb_exc:
        try:
            _log(f"[strategy_eb] live risk limits ignored ({_eb_exc}); "
                 "using the module defaults", "yellow")
        except Exception:
            pass
        return None
    return widest
```

Check `RiskLimits` is constructed with exactly those six keyword names (read `backend/live_risk_state.py`'s dataclass first; if a field is named differently, use the dataclass's names — the test asserts on `max_symbol_fraction`, `max_order_fraction`, `kill`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest backend/tests/test_live_equity_bars.py backend/tests/test_live_risk_limits.py backend/tests/test_strategy_eb_run_once.py -q`
Expected: all pass (the EB tests prove the single-lane behaviour is unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/live_equity_bars.py backend/broker.py backend/tests/test_live_equity_bars.py backend/tests/test_live_risk_limits.py
git commit -m "feat(broker): admit the outlier sleeve beside strategy_eb — companion lane, widest cap and risk envelope"
```

---

### Task 6: Lab doc, lab instance, feature build, and the pre-registered engine test

**Files:**
- Create: `scripts/outlier_lab_setup.py`
- Create: `scripts/outlier_engine_test.py`
- Create: `docs/superpowers/research/2026-09-02-outlier-sleeve-engine-test.md` (written by the test script; committed by the implementer)

**Interfaces:**
- Consumes: `scripts/_api.py: call(method, path, body=None) -> (status, json)`; Postgres read-only DSN from `.env` (`POSTGRES_PASSWORD`, host `server7`, user `intellistock`, db `IntelliStock`) for `BacktestSteps`; the two Task 3 scripts.
- Produces: Strategies doc "Strategy EB + Outlier Sleeve" (id printed and recorded in the research doc), instance `strategy-eb-lab` (`runCommand` never set true), the research doc with the §9 verdict.

- [ ] **Step 1: Build the features and export the peers (one-time, ~10 minutes)**

```bash
cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock/.claude/worktrees/main-session
set -a; . ../../../.env; set +a
export PG_DSN="host=server7 port=5432 user=intellistock dbname=IntelliStock password=$POSTGRES_PASSWORD"
python3 scripts/export_outlier_peers.py
python3 scripts/build_outlier_features.py --start 2020-06-01 --end 2026-09-01
```

Expected: `exported ~2400 tickers with peers`; `candidates ~12780`, `liquid universe ~800`, `rows ~1.1M`, `done`. Never echo `PG_DSN`.

- [ ] **Step 2: Write the lab setup script**

```python
#!/usr/bin/env python3
"""Create the lab Strategies doc (bil25 EB + outlier sleeve) and the backtest-only
lab instance. Idempotent: re-running finds the existing rows by name/id.

    python3 scripts/outlier_lab_setup.py
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
from _api import call  # noqa: E402

DOC_NAME = "Strategy EB + Outlier Sleeve"
INSTANCE_ID = "strategy-eb-lab"
CHAMPION_DOC = "200"
STOCKS = ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"]


def main():
    from outlier_sleeve import DEFAULTS as OS
    _, champ = call("GET", f"/strategies/{CHAMPION_DOC}")
    eb = next(l for l in champ["strategies"] if l["strategy"] == "strategy_eb")
    assert eb["config"]["trend_off_book"] == {"GLD": 0.375, "GDX": 0.1875, "XLE": 0.1875}, "doc 200 is not bil25"
    sleeve = {"strategy": "outlier_sleeve", "weight": 1.0, "execution_position": 20,
              "decision_phase": "pre", "execution_scope": "run_once", "conditions": {},
              "config": {**OS, "outlier_sleeve_enabled": True}}
    _, docs = call("GET", "/strategies")
    existing = next((d for d in (docs if isinstance(docs, list) else docs.get("strategies", []))
                     if d.get("name") == DOC_NAME), None)
    payload = {"name": DOC_NAME, "strategies": [json.loads(json.dumps(eb)), sleeve]}
    if existing:
        call("PUT", f"/strategies/{existing['id']}", payload)
        doc_id = existing["id"]
    else:
        _, created = call("POST", "/strategies", payload)
        doc_id = created.get("id") or created.get("strategy_id")
    print("lab doc id:", doc_id)
    code, inst = call("GET", f"/instances/{INSTANCE_ID}")
    if code == 404:
        _, brok = call("GET", "/instances/strategy-eb")
        body = {"id": INSTANCE_ID, "name": "Strategy EB lab (backtest only)", "strategy_id": doc_id,
                "granularity_time_increment": 86400, "brokerage_id": brok.get("brokerage_id"),
                "stocks": STOCKS, "kind": None}
        call("POST", "/instances", body)
        print("created instance", INSTANCE_ID)
    else:
        call("PATCH", f"/instances/{INSTANCE_ID}", {"strategy_id": doc_id})
        print("instance exists; strategy_id set to", doc_id)
    for s in STOCKS:
        try:
            call("POST", f"/instances/{INSTANCE_ID}/stocks", {"symbol": s})
        except BaseException:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

If `POST /strategies` or `PATCH /instances/{id}` return a different shape (check `backend/api/main.py:2267` and the `@app.patch("/instances/{instance_id}")` route before running), adapt the two response reads — the row ids are the only things consumed.

- [ ] **Step 3: Run the setup and verify the champion is untouched**

Run: `set -a; . ../../../.env; set +a; python3 scripts/outlier_lab_setup.py`
Then: `python3 -c "import sys; sys.path.insert(0,'scripts'); from _api import call; d=call('GET','/strategies/200')[1]; c=next(l['config'] for l in d['strategies'] if l['strategy']=='strategy_eb'); print(c['trend_off_book'], c['risk_off_symbol'], c['strategy_eb_enabled'])"`
Expected: `{'GDX': 0.1875, 'GLD': 0.375, 'XLE': 0.1875} BIL True` and the lab doc id printed. `GET /instances/strategy-eb-lab` shows `runCommand` absent or false.

- [ ] **Step 4: Write the engine test script**

```python
#!/usr/bin/env python3
"""Pre-registered engine test for the outlier sleeve (spec §9). POST-only against
the LAB instance; never touches doc 200.

    python3 scripts/outlier_engine_test.py [--arm default|confirm50]

Writes docs/superpowers/research/2026-09-02-outlier-sleeve-engine-test.md.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
from _api import call  # noqa: E402

INSTANCE = "strategy-eb-lab"
STOCKS = ["TQQQ", "SPY", "BIL", "QQQ", "GLD", "GDX", "XLE"]
WINDOWS = [("cyc", "2021-11-01", "2026-08-27"), ("ny1", "2022-01-01", "2023-12-31"),
           ("ny3", "2024-01-01", "2026-08-27"), ("rb1", "2022-01-01", "2022-06-30"),
           ("rb3", "2025-02-15", "2025-04-15"), ("nb4", "2026-01-15", "2026-04-30"),
           ("nc3", "2022-03-01", "2022-08-31")]
# bil25 engine baselines (return %, maxDD %) already on file
BASE = {"cyc": (197.78, -21.1), "ny1": (36.00, -21.1), "ny3": (139.49, -13.5),
        "rb1": (2.06, -12.3), "rb3": (2.59, -6.9), "nb4": (1.94, -12.7), "nc3": (-11.20, -18.6)}
OUT = os.path.join(_ROOT, "docs", "superpowers", "research", "2026-09-02-outlier-sleeve-engine-test.md")


def maxdd(x):
    pk, m = x[0], 0.0
    for v in x:
        pk = max(pk, v)
        m = min(m, v / pk - 1)
    return m


def path_and_trades(bid):
    import psycopg
    dsn = (f"host=server7 port=5432 user=intellistock dbname=IntelliStock "
           f"password={os.environ['POSTGRES_PASSWORD']} options=-c\\ default_transaction_read_only=on")
    with psycopg.connect(dsn) as c, c.cursor() as cur:
        cur.execute('select doc from "BacktestSteps" where backtest_id=%s and kind=%s and doc is not null order by seq',
                    (str(bid), "pv"))
        pv = [r[0] for r in cur.fetchall()]
        cur.execute('select doc from "BacktestSteps" where backtest_id=%s and kind=%s and doc is not null order by seq',
                    (str(bid), "trade"))
        trades = [r[0] for r in cur.fetchall()]
    byday = {}
    for d in pv:
        if d.get("value") is not None and (d.get("prices") or {}).get("SPY"):
            byday[str(d["timestamp"])[:10]] = (float(d["value"]), float(d["prices"]["SPY"]))
    days = sorted(byday)
    nav = [byday[t][0] for t in days]
    spy = [byday[t][1] for t in days]
    f = lambda t: dt.date.fromisoformat(t)  # noqa: E731
    spytr = [v * (1.0125 ** ((f(days[i]) - f(days[0])).days / 365.25)) for i, v in enumerate(spy)]
    return days, nav, spytr, trades


def attribution(trades, excluded=frozenset(STOCKS)):
    """Realised P&L per symbol from trade steps (buy/sell pairs, FIFO on cash)."""
    pnl = collections.defaultdict(float)
    for t in trades:
        sym = str(t.get("symbol") or t.get("ticker") or "").upper()
        if not sym or sym in excluded:
            continue
        side = str(t.get("side") or t.get("action") or "").lower()
        cash = float(t.get("notional") or t.get("value") or 0.0) or float(t.get("qty") or 0) * float(t.get("price") or 0)
        pnl[sym] += cash if side.startswith("s") else -cash
    return dict(sorted(pnl.items(), key=lambda kv: -kv[1]))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="default", choices=["default", "confirm50"])
    args = ap.parse_args(argv)
    if args.arm == "confirm50":
        _, inst = call("GET", f"/instances/{INSTANCE}")
        _, doc = call("GET", f"/strategies/{inst['strategy_id']}")
        for l in doc["strategies"]:
            if l["strategy"] == "outlier_sleeve":
                l["config"]["confirm_frac"] = 0.50
        call("PUT", f"/strategies/{inst['strategy_id']}", {"name": doc["name"], "strategies": doc["strategies"]})
    ids = []
    for tag, s, e in WINDOWS:
        for _ in range(3):
            try:
                _, r = call("POST", "/backtests", {"instance_id": INSTANCE, "stocks": STOCKS, "start_date": s,
                                                   "end_date": e, "granularity": "86400", "initial_cash": 6000,
                                                   "equity_cost_tiers": "etf-liquid"})
                ids.append((r["id"], tag))
                break
            except BaseException:
                time.sleep(10)
    print("posted", ids, flush=True)
    done = {}
    for _ in range(240):
        time.sleep(30)
        try:
            _, b = call("GET", "/backtests")
        except BaseException:
            continue
        bts = {x.get("id"): x for x in (b.get("backtests", b) if isinstance(b, dict) else b)}
        for i, tag in ids:
            if tag not in done and bts.get(i, {}).get("status") in ("finished", "error", "stopped"):
                done[tag] = i
                print("done", tag, i, flush=True)
        if len(done) == len(ids):
            break
    rows, checks = [], {}
    for tag, bid in done.items():
        days, nav, spytr, trades = path_and_trades(bid)
        ret = (nav[-1] / nav[0] - 1) * 100
        dd = maxdd(nav) * 100
        s = (spytr[-1] / spytr[0] - 1) * 100
        rows.append((tag, bid, days[0], days[-1], ret, s, dd, maxdd(spytr) * 100, BASE[tag]))
        if tag == "cyc":
            att = attribution(trades)
            gain = nav[-1] - nav[0]
            big = [k for k, v in att.items() if gain > 0 and v / gain >= 0.05]
            checks["c1_return"] = ret >= BASE["cyc"][0] + 15
            checks["c2_drawdown"] = dd >= BASE["cyc"][1] - 3
            checks["c4_population"] = len(big) >= 3
            checks["attribution"] = {k: round(v, 0) for k, v in list(att.items())[:12]}
    bears = [r for r in rows if r[0] in ("rb1", "rb3", "nb4")]
    checks["c3_bears_nonnegative"] = all(r[4] >= 0 for r in bears if r[8][0] >= 0)
    verdict = "PASS" if all(checks.get(k) for k in ("c1_return", "c2_drawdown", "c3_bears_nonnegative")) else "FAIL"
    lines = [f"# Outlier sleeve — engine test ({args.arm})", "",
             f"Instance {INSTANCE} · granularity 86400 · etf-liquid tiers · run {dt.datetime.utcnow():%Y-%m-%d %H:%MZ}", "",
             "| window | bt | span | EB+sleeve | SPY-TR | bil25 base | DD | SPY DD | base DD |", "|---|---|---|---|---|---|---|---|---|"]
    for tag, bid, d0, d1, ret, s, dd, sdd, base in sorted(rows, key=lambda r: r[0]):
        lines.append(f"| {tag} | {bid} | {d0}..{d1} | {ret:+.2f}% | {s:+.2f}% | {base[0]:+.2f}% | {dd:.1f}% | {sdd:.1f}% | {base[1]:.1f}% |")
    lines += ["", f"## Verdict: **{verdict}**", "", "```", json.dumps(checks, indent=1), "```"]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a") as fh:
        fh.write("\n".join(lines) + "\n\n")
    print("\n".join(lines), flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Before the first run, check the `trade` step kind's field names on one existing strategy-eb backtest (`select doc from "BacktestSteps" where backtest_id='785201' and kind='trade' limit 3`) and adjust `attribution()`'s key reads to match; the `pv` kind's shape is already known.

- [ ] **Step 5: Run the default arm (~40 minutes) and read the verdict**

Run: `set -a; . ../../../.env; set +a; nohup python3 -u scripts/outlier_engine_test.py --arm default > /tmp/outlier_engine_default.log 2>&1 &` then poll the log. Do NOT push to main while it runs.
Expected: the research doc gains a table and a verdict block; the script exits 0 on PASS.

- [ ] **Step 6: Run the second arm only after the first completes**

Run: `python3 scripts/outlier_engine_test.py --arm confirm50` (same polling). Then restore the lab doc's `confirm_frac` to 0.25 with one `PUT` (the setup script re-run does this: `python3 scripts/outlier_lab_setup.py`).

- [ ] **Step 7: Commit**

```bash
git add scripts/outlier_lab_setup.py scripts/outlier_engine_test.py docs/superpowers/research/2026-09-02-outlier-sleeve-engine-test.md
git commit -m "test(outlier-sleeve): lab doc/instance setup and the pre-registered engine test with results"
```

---

## Self-review

**Spec coverage.** §3 architecture → Tasks 1, 2, 4, 6. §4 data model → Task 2 (+ builder Task 3). §5 algorithm → Task 1 (rules) and Task 4 (contract). §6 config → Task 1 `DEFAULTS` and the Task 4 header (identical key sets, pinned by test). §7 broker changes 1–3 → Task 5; change 4 (universe admission) is the existing `_nexus_discovered` path used in Task 4. §8 builder → Task 3. §9 engine test → Task 6, thresholds copied verbatim. §10/§11 need no task.

**Placeholders.** None: every code step carries its full content; the two "check the real field name first" notes (RiskLimits fields, trade-step keys) name the exact file/query to read and what the test asserts.

**Type consistency.** Slot record keys (`entry_px, entry_ordinal, entry_cost, proven, below, last_eval`) are identical in Tasks 1 and 4. `screen(rows, cfg, peers, held)`, `exit_decisions(slots, rows_by_sym, session_id, cfg)`, `new_slot_orders(candidates, slots, nav, cash, cfg)`, `winner_cap_trims(slots, positions, prices, nav, cfg)` match between the module, its tests, and the wrapper. `cross_section(store, date)`, `visible_dates(store, before_date)`, `peers_for(store, symbols)` match Task 2 and Task 4. `confirm_hot_rank` is in `DEFAULTS` and the header (it is the 0.75 quartile constant from the spec, exposed rather than hard-coded).
