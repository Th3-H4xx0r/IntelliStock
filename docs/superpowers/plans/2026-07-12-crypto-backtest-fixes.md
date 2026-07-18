# Crypto Backtest Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make crypto backtests sell (not just buy-and-hold), step 24/7, run fast, show a correct live chart, and drop the NaN% cosmetic.

**Architecture:** Small surgical fixes in `backend/strategies/crypto/core.py` (held/position lookup), `backend/broker.py` (session gate, price-lookup speed, running-history downsample), one new backtest-only cursor module, and a frontend/mobile guard. All crypto-gated or backtest-gated or output-identical; equity/`alpaca-main` stays byte-identical.

**Tech Stack:** Python 3 / pytest (backend), Vue 3 (frontend), Flutter/Dart (mobile), RethinkDB.

## Global Constraints

- Equity/`alpaca-main` behavior MUST stay byte-identical. Crypto gate: `_is_crypto_instance_runtime()` or `"/" in symbol`.
- Live trading path changes (Task 4/5) MUST be output-identical; protect with parity tests.
- TDD: failing test first, minimal impl, green, commit. Frequent commits.
- Run backend tests from `backend/` with `python3 -m pytest`.
- Crypto symbols use a slash (`"BTC/USD"`); equities never contain a slash.

---

### Task 1: `position_qty` slash-symmetry

**Files:**
- Modify: `backend/strategies/crypto/core.py:245-259` (`position_qty`)
- Test: `backend/tests/test_crypto_position_qty.py` (create)

**Interfaces:**
- Produces: `position_qty(positions, sym) -> float` — now matches a held key regardless of slash form on EITHER side.

- [ ] **Step 1: Write failing test**
```python
# backend/tests/test_crypto_position_qty.py
from strategies.crypto import core

def test_position_qty_slash_symmetry():
    # slash position, slash-less query (the real bug) -> must find it
    assert core.position_qty({"BTC/USD": 0.14}, "BTCUSD") == 0.14
    # slash-less position, slash query
    assert core.position_qty({"BTCUSD": 0.14}, "BTC/USD") == 0.14
    # exact matches still work
    assert core.position_qty({"BTC/USD": 0.14}, "BTC/USD") == 0.14
    assert core.position_qty({"BTCUSD": 0.14}, "BTCUSD") == 0.14
    # absent -> 0.0
    assert core.position_qty({"ETH/USD": 1.0}, "BTC/USD") == 0.0
```
- [ ] **Step 2: Run — expect FAIL** (`test_position_qty_slash_symmetry`: `0.0 != 0.14`)
Run: `cd backend && python3 -m pytest tests/test_crypto_position_qty.py -v`
- [ ] **Step 3: Implement** — replace the body so lookup is slash-agnostic on both sides:
```python
def position_qty(positions: Optional[Mapping], sym: str) -> float:
    """Shares held for ``sym``, tolerant of Alpaca's slash / slash-less crypto keys
    on EITHER side (query or stored key)."""
    positions = positions or {}
    raw = positions.get(sym)
    if raw is None:
        raw = positions.get(str(sym).replace("/", ""))
    if raw is None:
        # Last resort: compare slash-stripped on both sides.
        target = str(sym).replace("/", "").upper()
        for k, v in positions.items():
            if str(k).replace("/", "").upper() == target:
                raw = v
                break
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
```
- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** — `fix(crypto): position_qty matches held keys regardless of slash form`

---

### Task 2: Held positions always evaluable for exit + temporary probe

**Files:**
- Modify: `backend/strategies/crypto/core.py:262-274` (`held_symbols`) — add the probe + a `held_positions()` helper
- Modify: `backend/strategies/crypto/momentum.py:113-170` — exit-evaluate held coins even when absent from `universe`
- Test: `backend/tests/test_crypto_exit_when_blind.py` (create)

**Interfaces:**
- Consumes: `position_qty` (Task 1).
- Produces: `core.held_positions(portfolio_emulator) -> set[str]` (all held crypto symbols, unfiltered); `held_symbols` unchanged signature but now also logs a probe line.

- [ ] **Step 1: Write failing test** — a held coin with NO bars this tick must still be exited:
```python
# backend/tests/test_crypto_exit_when_blind.py
import datetime
from strategies.crypto.momentum import Momentum
from strategies.crypto import core
core.discover_universe = lambda *a, **k: ["BTC/USD"]

class PE:
    def __init__(self, pos): self._p = pos
    def get_positions(self): return dict(self._p)

def test_held_coin_with_no_bars_is_exited():
    pe = PE({"BTC/USD": 0.14})          # holding BTC
    m = Momentum()
    ct = datetime.datetime(2026, 4, 13, 12, 0, 0)
    # data window EMPTY for BTC (poisoned/degenerate) -> must still emit -1
    res = m.run_once([], {"BTC/USD": 60000.0}, ct, {"band": "medium"}, {},
                     data={}, portfolio_emulator=pe)
    assert res.get("BTC/USD") == -1
```
- [ ] **Step 2: Run — expect FAIL** (returns `{}` / no `BTC/USD` key today)
- [ ] **Step 3: Implement** — in `core.py` add:
```python
def held_positions(portfolio_emulator) -> set:
    """All held symbols (qty>0) straight from the emulator, NOT filtered by universe."""
    if portfolio_emulator is None:
        return set()
    try:
        positions = portfolio_emulator.get_positions() or {}
    except Exception:
        return set()
    return {k for k in positions if position_qty(positions, k) > 0}
```
And add a probe at the top of `held_symbols` (temporary — Task 8 removes it):
```python
    # TEMP PROBE (remove after crypto-sell verification 2026-07-12)
    try:
        _pos = portfolio_emulator.get_positions() if portfolio_emulator else None
        if _pos:
            print(f"[HELD-PROBE] universe={list(universe)} positions={list(_pos.keys())}")
    except Exception:
        pass
```
In `momentum.py`, after computing `universe` and before the `if not universe` early return, fold held-but-blind coins into the exit set:
```python
    held_all = core.held_positions(portfolio_emulator)
    # Coins we HOLD but have no bars for this tick: exit them (can't evaluate trend).
    blind_held = [s for s in held_all if not data.get(s) and s not in universe]
    for s in blind_held:
        result[s] = -1
    if not universe:
        return core.apply_crypto_config(result, config, prices, portfolio_emulator)
```
(Existing `held = _held_symbols(...)` and the `-1 if sym in held else 0` branches stay; Task 1 makes `held` correct when the coin IS in universe.)
- [ ] **Step 4: Run — expect PASS**; also run the two repro scripts' logic as a regression (`tests/test_crypto_exit_when_blind.py`).
- [ ] **Step 5: Commit** — `fix(crypto): always evaluate held positions for exit (+ temp held probe)`

---

### Task 3: Crypto backtest steps 24/7 (no market-open skip)

**Files:**
- Modify: `backend/broker.py:10084-10097` (time-advance block)
- Test: `backend/tests/test_crypto_247_stepping.py` (create) — assert the advance helper picks the raw increment for crypto.

**Interfaces:** none new; behavior change gated on `_is_crypto_instance_runtime()`.

- [ ] **Step 1: Write failing test** — extract-free: test via a thin module-level helper. Add helper `_advance_backtest_time(current_time, increment_td, is_crypto)` and test it:
```python
# backend/tests/test_crypto_247_stepping.py
import datetime
def test_crypto_advances_by_increment_no_skip(monkeypatch):
    import broker
    ct = datetime.datetime(2026, 4, 17, 20, 0, 0)  # Fri evening (outside equity session)
    inc = datetime.timedelta(minutes=15)
    # crypto: raw increment, NO weekend skip
    assert broker._advance_backtest_time(ct, inc, True) == ct + inc
```
- [ ] **Step 2: Run — expect FAIL** (`_advance_backtest_time` not defined)
- [ ] **Step 3: Implement** — add the helper near the session helpers and call it from the loop. Helper:
```python
def _advance_backtest_time(current_time, increment_td, is_crypto):
    """Crypto trades 24/7 -> raw increment. Equity -> session-gated with market-open skip."""
    if is_crypto:
        return current_time + increment_td
    if _is_within_trading_session_pt(current_time):
        return current_time + increment_td
    next_open = _next_market_open_utc(current_time)
    return next_open if next_open is not None else current_time + increment_td
```
Replace the loop block at ~10084 with:
```python
            print("Time Increment: ", time_increment)
            _adv_is_crypto = _is_crypto_instance_runtime()
            _adv_next = _advance_backtest_time(current_time, backtest_increment_td, _adv_is_crypto)
            if (not _adv_is_crypto) and _adv_next != current_time + backtest_increment_td:
                try:
                    _log("Skipped to next market open: %s" % _adv_next, "cyan")
                except NameError:
                    pass
            current_time = _adv_next
```
- [ ] **Step 4: Run — expect PASS**; add an equity case asserting the Fri-evening skip still lands on Monday open.
- [ ] **Step 5: Commit** — `fix(crypto): backtest steps 24/7 (no market-open/weekend skip for crypto)`

---

### Task 4: `_bar_time_to_datetime` — `fromisoformat` first (≈50× parse speedup, output-identical)

**Files:**
- Modify: `backend/broker.py:6241-6258`
- Test: `backend/tests/test_bar_time_parse.py` (create)

- [ ] **Step 1: Write failing/parity test**
```python
# backend/tests/test_bar_time_parse.py
import datetime, broker
def test_iso_z_parses_to_naive_utc():
    assert broker._bar_time_to_datetime("2026-07-12T13:45:00Z") == datetime.datetime(2026,7,12,13,45,0)
def test_offset_and_naive_and_bad():
    assert broker._bar_time_to_datetime("2026-07-12T13:45:00+00:00") == datetime.datetime(2026,7,12,13,45,0)
    assert broker._bar_time_to_datetime("2026-07-12T06:45:00-07:00") == datetime.datetime(2026,7,12,13,45,0)
    assert broker._bar_time_to_datetime("not-a-date") is None
```
- [ ] **Step 2: Run** — should PASS already (behavior unchanged); this locks parity BEFORE the swap.
- [ ] **Step 3: Implement** — reorder to try `fromisoformat` first, dateutil fallback:
```python
    else:
        dt = None
        try:
            s = t_str.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.datetime.fromisoformat(s)
        except Exception:
            try:
                from dateutil import parser as dateutil_parser
                dt = dateutil_parser.parse(t_str)
            except Exception:
                return None
```
- [ ] **Step 4: Run — expect PASS** (identical outputs, faster path).
- [ ] **Step 5: Commit** — `perf(backtest): parse bar timestamps via fromisoformat first (dateutil fallback)`

---

### Task 5: `_get_prices_at_time` backtest cursor (O(n²) → O(n))

**Files:**
- Create: `backend/backtest_prices_cursor.py`
- Modify: `backend/broker.py:6364-6394` (use the cursor when `mode == MODE_BACKTEST`) + invalidate alongside the existing `invalidate_cursor()` calls
- Test: `backend/tests/test_prices_cursor.py` (create)

**Interfaces:**
- Produces: `latest_price_at(data, symbols, current_utc, *, daily_mode, allow_same_day_daily, bar_time_to_datetime) -> dict[sym,float]` with a per-symbol forward cursor; `invalidate()` to reset.

- [ ] **Step 1: Write failing test** — parity vs the naive scan across a stepped sequence, plus rewind reset. (Test both the new module output equals a reference full-scan for each step.)
```python
# backend/tests/test_prices_cursor.py
import datetime, backtest_prices_cursor as C
def _btd(t): 
    return datetime.datetime.fromisoformat(t.replace("Z","+00:00")).replace(tzinfo=None)
def _naive(data, syms, cutoff):
    out={}
    for s in syms:
        best=None
        for b in data[s]:
            if _btd(b["t"])<=cutoff: best=b
            else: break
        if best: out[s]=float(best["c"])
    return out
def test_cursor_matches_naive_and_advances():
    bars=[{"t":f"2026-04-13T{h:02d}:00:00Z","c":100+h} for h in range(10)]
    data={"BTC/USD":bars}; C.invalidate()
    for h in range(10):
        cut=datetime.datetime(2026,4,13,h,0,0)
        got=C.latest_price_at(data,["BTC/USD"],cut,daily_mode=False,allow_same_day_daily=False,bar_time_to_datetime=_btd)
        assert got==_naive(data,["BTC/USD"],cut)
    C.invalidate()  # rewind
```
- [ ] **Step 2: Run — expect FAIL** (module missing)
- [ ] **Step 3: Implement** `backend/backtest_prices_cursor.py` — mirror `backtest_price_history.py`'s cursor: per-symbol filtered (malformed-stripped) view + monotonic index; return the close of the latest bar `<= current_utc` (respecting `daily_mode`/`allow_same_day_daily`). Include `invalidate()`. Then in `_get_prices_at_time`, when `mode == MODE_BACKTEST`, delegate to it; else keep the existing scan. Add `backtest_prices_cursor.invalidate()` next to every existing `invalidate_cursor()` call site.
- [ ] **Step 4: Run — expect PASS**; run `tests/test_price_history_cursor.py` + `tests/test_backtest_pnl_consistency.py` to confirm no regression.
- [ ] **Step 5: Commit** — `perf(backtest): O(1)/step price lookup via monotonic cursor (backtest-only)`

---

### Task 6: Running-history downsample (true live chart)

**Files:**
- Modify: `backend/broker.py:9982-9984`
- Test: `backend/tests/test_history_downsample.py` (create)

- [ ] **Step 1: Write failing test**
```python
# backend/tests/test_history_downsample.py
import broker
def test_downsample_keeps_first_last_and_caps():
    hist=[{"timestamp":i,"value":i} for i in range(9000)]
    out=broker._downsample_history(hist, 3000)
    assert out[0]==hist[0] and out[-1]==hist[-1]
    assert len(out)<=3000
def test_downsample_small_is_identity():
    hist=[{"timestamp":i,"value":i} for i in range(50)]
    assert broker._downsample_history(hist,3000)==hist
```
- [ ] **Step 2: Run — expect FAIL** (`_downsample_history` missing)
- [ ] **Step 3: Implement** helper + use it:
```python
def _downsample_history(hist, cap=3000):
    n = len(hist)
    if n <= cap:
        return list(hist)
    step = n / float(cap - 1)
    idxs = sorted(set([int(i*step) for i in range(cap-1)] + [n-1]))
    return [hist[i] for i in idxs]
```
Replace the running write:
```python
                            update_payload['portfolio_value_history'] = _convert_datetimes_to_iso(
                                _downsample_history(list(portfolio_emulator.get_portfolio_history() or []), 3000)
                            )
```
- [ ] **Step 4: Run — expect PASS**
- [ ] **Step 5: Commit** — `fix(backtest): downsample running portfolio history (true start/shape live)`

---

### Task 7: NaN% price-change guard (frontend + mobile)

**Files:**
- Modify: `frontend/src/views/BacktestDetailView.vue` (per-stock "Price Change" cell)
- Modify: `mobile/lib/features/backtests/data/models/backtest.dart` (price-change formatting)

- [ ] **Step 1:** Read the price-change render in `BacktestDetailView.vue`; find where it computes/prints the `%`.
- [ ] **Step 2:** Guard: when the per-stock price-change value is missing/`null`/`NaN` (running backtest → `stock_price_change` empty), render `—` instead of `NaN%`. Mirror in `backtest.dart`.
- [ ] **Step 3:** `cd frontend && npm run build` (or type-check) to confirm no errors.
- [ ] **Step 4: Commit** — `fix(crypto): show — not NaN% for missing per-stock price change`

---

## Verification (post-implementation, one deploy)

1. Full backend suite: `cd backend && python3 -m pytest tests/ -q` — all green incl. the 73 crypto tests.
2. Purge poisoned cache (RethinkDB): `r.db('IntelliStock').table('AlpacaBarsCache').filter(r.row['symbol'].match('/')).delete()`.
3. Deploy backend + backtest container + frontend; rebuild mobile.
4. Run a fresh paper `crypto:momentum` backtest; pull `/backtests/:id/logs` + `/summary` via the API and confirm:
   - **Sells > 0 / round-trips present** (the fix).
   - Read the `[HELD-PROBE]` lines to confirm exactly why `held` was empty and that it's now populated.
   - **No "Skipped to next market open"**; steps land on nights/weekends (24/7).
   - Completes in **minutes not hours** (perf).
   - Running chart starts ~$9,975 (not $10,785); no NaN%.
5. Run a short equity backtest — confirm `alpaca-main` behavior unchanged (same trades/curve as a pre-change run).
6. **Remove the temporary `[HELD-PROBE]`** (Task 2) and re-commit once confirmed.
