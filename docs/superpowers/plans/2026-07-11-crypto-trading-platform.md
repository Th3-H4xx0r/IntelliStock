# Crypto Trading Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add autonomous Alpaca crypto trading to IntelliStock as specially-marked (`kind="crypto"`) instances that run through the SAME `broker.py`/adapter/scheduler as equities, with three distinct strategy bands (fast/momentum/allocator), auto-coin-discovery, and backtest support — paper-first, live gated off.

**Architecture:** Option C — a shared crypto core (`backend/strategies/crypto/core.py`) plus thin strategy plugins. All engine changes are `kind=="crypto"` branches inside shared files; no fork. A crypto instance is an Instances row with `kind="crypto"`, an Alpaca (paper) brokerage, a `crypto_config` blob (band + risk knobs), an optional `stocks` list (empty ⇒ auto-discovery), and a `scheduler_config` that makes the scheduler run 24/7.

**Tech Stack:** Python 3, `alpaca-py>=0.43.4` (`TradingClient` — same client handles crypto), Alpaca crypto data REST `data.alpaca.markets/v1beta3/crypto/us/*`, RethinkDB (schemaless), pytest.

## Global Constraints

- **Same codebase (HARD):** every crypto behavior is a `kind=="crypto"` branch inside `broker.py` / `broker_adapters/alpaca.py` / `scheduler.py` / backtest / utils. New files ONLY under `backend/strategies/crypto/`. Do NOT fork a runner (unlike Kalshi).
- **Paper-only:** live crypto trading gated OFF behind an explicit flag. Never modify the equity live path (`alpaca-main`, real money).
- **Order rules (Alpaca crypto):** `time_in_force` MUST be `gtc` or `ioc` (never `day`/`opg`); `extended_hours` MUST be `False`; order types `market`/`limit`/`stop_limit` only; symbols in slash form `BTC/USD`; long-only spot (no shorting/margin).
- **Fees are real:** apply the fee model (0.15% maker / 0.25% taker, tier 1) in sizing AND backtest. Never assume commission-free for crypto.
- **Per project rules:** run `gitnexus_impact({target, direction:"upstream"})` before editing any shared symbol; run `gitnexus_detect_changes()` before each commit; warn on HIGH/CRITICAL.
- **Symbol format:** `"BTC/USD"` throughout; `.upper()` preserves the slash.

---

## File Structure

**New (crypto strategy layer):**
- `backend/strategies/crypto/__init__.py` — exports.
- `backend/strategies/crypto/core.py` — fee model, `is_crypto_instance`, `crypto_scheduler_config`, crypto bars fetch, vol-targeted sizing, fee-aware order builder, USD risk-off helper.
- `backend/strategies/crypto/discovery.py` — auto-coin-discovery (live + point-in-time).
- `backend/strategies/crypto/reference.py` — trivial fixed-weight rebalance (proves the platform).
- `backend/strategies/crypto/fast.py` — Band 1 fast tactical.
- `backend/strategies/crypto/momentum.py` — Band 2 momentum.
- `backend/strategies/crypto/allocator.py` — Band 3 allocator.
- `backend/tests/test_crypto_core.py`, `test_crypto_discovery.py`, `test_crypto_strategies.py`, `test_crypto_wiring.py`, `test_crypto_backtest.py`.

**Modified (shared, branch on `kind`):**
- `backend/broker.py` — read `kind`; pass crypto scheduler_config at the `get_next_wake` call; bypass the NYSE session gate; branch the bars/quotes/trades URL builder; branch backtest data fetch; skip equity discovery for crypto.
- `backend/broker_adapters/alpaca.py` — `is_market_open` returns True for crypto; order path forces `gtc`/`extended_hours=False` for crypto (bypass `_order_style_for_now`).
- `backend/ticker_universe.py` — accept crypto pairs (skip the equity regex).
- `backend/nexus_broker_utils.py` — allow `asset_class="crypto"`.
- `backend/instance_config.py` (or wherever instance docs are built) — add `build_crypto_instance_doc`.
- `frontend/src/views/InstancesView.vue` — let `kind==="crypto"` past the `!== 'kalshi'` list filter.

**Execution note (existing codebase):** shared-file tasks give the exact anchor + the branch to insert; the implementer MUST open the file, confirm the surrounding lines (line numbers drift), run `gitnexus_impact` on the enclosing function, then edit. Read one existing simple strategy (`backend/strategies/rsi.py`) to copy the EXACT `run_once` signature before writing any strategy class.

---

## Phase A — Platform seams

### Task A0: Confirm the strategy contract + broker seams (read-only, no commit)

**Files:** read `backend/strategies/rsi.py` (or `macd.py`), `backend/broker.py` (~1194-1208, ~6931-6945, ~7558-7592, ~870-880), `backend/broker_adapters/alpaca.py` (~155-170, ~667-760, ~1558-1645, ~1241), `backend/server.py:405`.

- [ ] Record the exact `run_once(...)` signature + return type used by loaded strategies.
- [ ] Record the exact lines of: the `get_next_wake` call, the session-gate block, the bars-fetch URL builder, `submit_order`’s tif/extended_hours handling, `is_market_open`.
- [ ] Confirm how `broker.py` currently reads the Instances row (to add `kind`/`crypto_config` reads).

(No test/commit — this is the grounding step every later task depends on.)

### Task A1: `is_crypto_instance` + fee model + scheduler config (core.py foundation)

**Files:**
- Create: `backend/strategies/crypto/__init__.py`, `backend/strategies/crypto/core.py`
- Test: `backend/tests/test_crypto_core.py`

**Interfaces — Produces:**
- `CRYPTO_FEES = {"maker": 0.0015, "taker": 0.0025}`
- `is_crypto_instance(instance_doc: Mapping) -> bool` (True iff `instance_doc.get("kind")=="crypto"`)
- `round_trip_fee(maker_in: bool, maker_out: bool) -> float`
- `crypto_scheduler_config(band: str) -> dict` — returns a `scheduler.get_next_wake` config: `{open_pt_min:0, close_pt_min:1440, weekdays_only:False, full_anchor_pt_min:0, monitor_interval_min: {high:5, medium:15, low:60}[band]}`; unknown band ⇒ medium.

- [ ] **Step 1: failing tests**
```python
from strategies.crypto import core
def test_is_crypto_instance():
    assert core.is_crypto_instance({"kind": "crypto"}) is True
    assert core.is_crypto_instance({"kind": "kalshi"}) is False
    assert core.is_crypto_instance({}) is False
def test_round_trip_fee_taker():
    assert abs(core.round_trip_fee(False, False) - 0.005) < 1e-9
    assert abs(core.round_trip_fee(True, True) - 0.003) < 1e-9
def test_scheduler_config_is_24_7_and_band_paced():
    hi = core.crypto_scheduler_config("high")
    assert hi["weekdays_only"] is False and hi["open_pt_min"] == 0 and hi["close_pt_min"] == 1440
    assert hi["monitor_interval_min"] == 5
    assert core.crypto_scheduler_config("low")["monitor_interval_min"] == 60
    assert core.crypto_scheduler_config("bogus")["monitor_interval_min"] == 15
```
- [ ] **Step 2:** `pytest backend/tests/test_crypto_core.py -v` → FAIL (module missing).
- [ ] **Step 3:** implement the three functions + constants in `core.py`; `__init__.py` re-exports `core`.
- [ ] **Step 4:** run tests → PASS.
- [ ] **Step 5:** validate the config against the real scheduler:
```python
from datetime import datetime, timezone
import scheduler
def test_scheduler_accepts_crypto_config_weekend():
    cfg = core.crypto_scheduler_config("medium")
    sat = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)  # Saturday
    nxt, mode = scheduler.get_next_wake(sat, marker=None, config=cfg)
    assert nxt > sat  # schedules a wake on the weekend (24/7), not skipped to Monday
```
Run → PASS (proves `_resolve_config` honors 24/7). 
- [ ] **Step 6: commit** `feat(crypto): core fee model + 24/7 scheduler config + is_crypto_instance`.

### Task A2: Crypto bars/quotes fetch in core.py

**Files:** Modify `backend/strategies/crypto/core.py`; Test `backend/tests/test_crypto_core.py`.

**Interfaces — Produces:**
- `crypto_bars_url(timeframe: str) -> str` → `"https://data.alpaca.markets/v1beta3/crypto/us/bars"`.
- `crypto_bars_params(symbols: list[str], timeframe: str, start: str|None, end: str|None, limit: int) -> dict` → `{"symbols": "BTC/USD,ETH/USD", "timeframe": timeframe, ...}` (comma-joined symbols as ONE query param; NO `feed`).
- `fetch_crypto_bars(symbols, timeframe, key, secret, start=None, end=None, limit=1000) -> dict[str, list[dict]]` (uses `requests.get`, header `APCA-API-KEY-ID`/`-SECRET-KEY`, returns `raw["bars"]`).

- [ ] **Step 1:** failing tests asserting `crypto_bars_url` has no `/stocks/`, `crypto_bars_params(["BTC/USD","ETH/USD"],"1Min",None,None,100)["symbols"]=="BTC/USD,ETH/USD"` and no `feed` key.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement (pure URL/param builders + a thin `fetch_crypto_bars` that calls `requests`). **Step 4:** run → PASS. Keep `fetch_crypto_bars` network call out of unit tests (test only the builders; monkeypatch `requests.get` for one happy-path test returning a canned `{"bars": {...}}`).
- [ ] **Step 5: commit** `feat(crypto): crypto bars fetch + param builders in core`.

### Task A3: Broker reads `kind`; passes crypto scheduler_config

**Files:** Modify `backend/broker.py` (the Instances-row read + the `get_next_wake` call site ~6939); Test `backend/tests/test_crypto_wiring.py`.

**Change:** where the broker loads the instance doc, capture `kind = inst_doc.get("kind")` and `crypto_config = inst_doc.get("crypto_config") or {}`. At the scheduler call, when `kind=="crypto"`, pass `config=core.crypto_scheduler_config(crypto_config.get("band","medium"))` instead of `None`. Guard the import (`from strategies.crypto import core`).

- [ ] **Step 1:** `gitnexus_impact({target:"get_next_wake", direction:"upstream"})` → confirm sole caller is broker.py; record risk.
- [ ] **Step 2:** failing test — extract the selection into a tiny pure helper `broker._scheduler_config_for(kind, crypto_config)` returning `None` for equities and the crypto config for crypto; test both branches.
- [ ] **Step 3:** run → FAIL. **Step 4:** implement helper + call it at the scheduler site. **Step 5:** run → PASS.
- [ ] **Step 6:** `gitnexus_detect_changes()` → confirm only the scheduler-call flow is affected; **commit** `feat(crypto): 24/7 scheduler wiring for crypto instances`.

### Task A4: Bypass NYSE session gate + is_market_open for crypto

**Files:** Modify `backend/broker.py` (session gate ~7558-7592) and `backend/broker_adapters/alpaca.py` (`is_market_open` ~1241); Test `backend/tests/test_crypto_wiring.py`.

**Change:** the `within_session` computation short-circuits to `True` when `kind=="crypto"` (crypto is always "in session"). `AlpacaAdapter.is_market_open` returns `True` when the adapter is flagged crypto (add an `asset_class`/`is_crypto` attr set at adapter construction from the instance kind).

- [ ] **Step 1:** `gitnexus_impact` on `is_market_open` and the session-gate function; record blast radius (equity path must be unchanged).
- [ ] **Step 2:** failing tests: an adapter constructed with `is_crypto=True` → `is_market_open()` True regardless of clock; the session helper returns True for crypto kind.
- [ ] **Step 3-4:** implement the crypto branches (equity path untouched — default `is_crypto=False`); run → PASS.
- [ ] **Step 5:** `gitnexus_detect_changes()`; **commit** `feat(crypto): 24/7 market-hours bypass for crypto`.

### Task A5: Crypto order placement (gtc / no extended-hours)

**Files:** Modify `backend/broker_adapters/alpaca.py` (`buy`/`sell` shims + `_order_style_for_now` ~1558-1645); Test `backend/tests/test_crypto_core.py` + `test_crypto_wiring.py`.

**Change:** add `core.build_crypto_order(symbol, side, qty, prefer_maker, last_price) -> dict` returning `{"order_type": "limit"|"market", "limit_price": .., "tif": "gtc", "extended_hours": False}`. In the adapter, when `is_crypto`, bypass `_order_style_for_now` and use `build_crypto_order` (maker-limit slightly through the book when `prefer_maker`, else marketable). Force `tif="gtc"`, `extended_hours=False`.

- [ ] **Step 1:** failing test on `build_crypto_order`: maker buy at `last_price` returns `order_type=="limit"`, `tif=="gtc"`, `extended_hours is False`, `limit_price` set; taker returns marketable limit/market with `tif=="gtc"`.
- [ ] **Step 2-4:** implement `build_crypto_order` in core; wire the adapter crypto branch. `gitnexus_impact` on `submit_order`/`_order_style_for_now` first. run → PASS.
- [ ] **Step 5:** `gitnexus_detect_changes()`; **commit** `feat(crypto): gtc/no-extended-hours crypto order placement`.

### Task A6: Crypto bars endpoint + symbol validator in the broker path

**Files:** Modify `backend/broker.py` (bars/quotes/trades URL builder ~1194-1208 & base ~874) and `backend/ticker_universe.py` (~53/128) and gate `backend/discover.py`; Test `backend/tests/test_crypto_wiring.py`.

**Change:** when `kind=="crypto"`, the broker fetches bars via `core.crypto_bars_url/params` (v1beta3, no feed) instead of `/stocks/{sym}/bars`; quotes/trades similarly. `ticker_universe.is_valid_us_ticker` (or the caller) accepts a slash pair when crypto (add `is_valid_crypto_pair(sym)` = matches `^[A-Z0-9]{2,10}/[A-Z]{3,4}$`); equity discovery (`discover.py`) is skipped for crypto.

- [ ] **Step 1:** failing tests: `is_valid_crypto_pair("BTC/USD")` True, `("AAPL")` False; the broker URL builder returns a v1beta3 crypto URL for a crypto instance (extract URL choice into a pure helper to test without network).
- [ ] **Step 2-4:** `gitnexus_impact` on the bars fetcher; implement branches; run → PASS.
- [ ] **Step 5:** `gitnexus_detect_changes()`; **commit** `feat(crypto): crypto bars endpoint + pair symbol validation`.

## Phase B — Crypto strategy layer

### Task B1: Sizing + fee-aware helpers + USD risk-off (core.py)

**Files:** Modify `core.py`; Test `test_crypto_core.py`.

**Interfaces — Produces:**
- `vol_target_size(equity_usd, price, recent_vol, target_vol=0.02, max_frac=0.25) -> float` (fractional qty; caps notional at `max_frac*equity`).
- `min_edge_to_trade(maker: bool) -> float` (= round-trip fee + a spread buffer; used by fast/momentum to reject trades that can't clear costs).
- `risk_off_targets(symbols) -> dict[str,float]` (all-zero target weights ⇒ hold USD/USDC).

- [ ] TDD: qty caps at `max_frac*equity/price`; `min_edge_to_trade(False) >= 0.005`; risk-off returns zeros. Implement, run → PASS, **commit**.

### Task B2: Auto-coin-discovery (discovery.py)

**Files:** Create `backend/strategies/crypto/discovery.py`; Test `test_crypto_discovery.py`.

**Interfaces — Produces:**
- `list_tradable_pairs(assets_provider) -> list[str]` (USD-quoted, tradable; `assets_provider()` returns Alpaca assets — injected for testing).
- `rank_universe(bars_by_symbol: dict[str,list[dict]], band: str) -> list[tuple[str,float]]` (composite = z(dollar-volume) + z(momentum) + band-specific volatility weighting; pure, point-in-time — consumes only bars up to `as_of`).
- `discover(band, k, assets_provider, bars_provider, as_of=None) -> list[str]` (top-K symbols; deterministic given inputs).

- [ ] TDD with **canned bars** (no network): given three symbols with known volume/return, `rank_universe` orders them as expected; `discover("low", k=2, ...)` returns the top 2; passing an earlier `as_of` changes the ranking (proves point-in-time, no lookahead). Implement, run → PASS, **commit**.

### Task B3: Reference strategy (platform proof)

**Files:** Create `backend/strategies/crypto/reference.py`; Test `test_crypto_strategies.py`.

**Change:** a strategy class matching the EXACT `run_once` signature from Task A0 that holds a fixed target weight across its symbols (or the discovered universe when `stocks` empty) and emits buy/sell scores to rebalance to target on each MONITOR tick. Uses only `core` helpers.

- [ ] TDD: given a flat portfolio + two symbols, `run_once` returns scores that move toward equal weight; respects `min_edge_to_trade`. Implement, run → PASS, **commit**.

### Task B4: Fast tactical strategy (Band 1)

**Files:** Create `backend/strategies/crypto/fast.py`; Test `test_crypto_strategies.py`.

**Change:** BTC/ETH only. Signal: short-window breakout/momentum (e.g. price > N-bar high AND fast-EMA > slow-EMA on 1–5 min bars); maker-limit entries; ATR stop; take-profit must exceed `min_edge_to_trade(maker=True)`. Long-only; exit to USD on stop/target/loss-of-signal.

- [ ] TDD with synthetic bar series: a clean breakout ⇒ buy score; chop below the edge threshold ⇒ no trade; stop hit ⇒ exit. Implement, run → PASS, **commit**.

### Task B5: Momentum strategy (Band 2)

**Files:** Create `backend/strategies/crypto/momentum.py`; Test `test_crypto_strategies.py`.

**Change:** ~5–8 majors (from `stocks` or discovery). Trend-following: EMA(fast/slow) cross or Donchian breakout on 15m–1h bars, ADX/vol chop filter, hold top-K by momentum, vol-targeted sizing, all-to-USD risk-off when the basket is in aggregate downtrend.

- [ ] TDD with synthetic multi-symbol series: uptrending symbols get positive scores and are ranked top-K; downtrend ⇒ risk-off zeros. Implement, run → PASS, **commit**.

### Task B6: Allocator strategy (Band 3)

**Files:** Create `backend/strategies/crypto/allocator.py`; Test `test_crypto_strategies.py`.

**Change:** broad majors, daily/weekly. Hold coins above a long-term MA (e.g. 20-period on daily), inverse-vol weights, rebalance on threshold drift; all-to-USDC when BTC is below its long-term MA (regime off).

- [ ] TDD: coins above MA get inverse-vol weights summing to ≤1; BTC below MA ⇒ full risk-off. Implement, run → PASS, **commit**.

## Phase C — Backtest

### Task C1: Crypto backtest data + fee model

**Files:** Modify `backend/broker.py` (backtest data-fetch branch) and wherever the backtest applies fills; Test `backend/tests/test_crypto_backtest.py`.

**Change:** in backtest mode for a crypto instance, fetch historical bars via `core.fetch_crypto_bars` (v1beta3) stepping by `granularity_time_increment`; apply `core.round_trip_fee`/`CRYPTO_FEES` to every simulated fill (crypto backtests are NOT commission-free). Auto-discovery inside backtest calls `discovery.discover(..., as_of=<current backtest time>)` (point-in-time).

- [ ] **Step 1:** `gitnexus_impact` on the backtest fill/step function. failing test: a 2-bar synthetic crypto backtest produces a fill whose net P&L reflects the taker fee (assert the fee was deducted). 
- [ ] **Step 2-4:** implement the crypto branch; run → PASS.
- [ ] **Step 5:** `gitnexus_detect_changes()`; **commit** `feat(crypto): crypto backtest data + fee-aware fills`.

## Phase D — Instance creation, UI, integration

### Task D1: `build_crypto_instance_doc` + asset_class

**Files:** add `build_crypto_instance_doc(instance_id, *, brokerage_id, name, config, stocks) -> dict` (mirror `build_kalshi_instance_doc`); Modify `backend/nexus_broker_utils.py` to allow `asset_class="crypto"`; Test `test_crypto_wiring.py`.

- [ ] TDD: the doc has `kind=="crypto"`, `crypto_config`, `stocks` (may be `[]`), `runCommand False`. Implement, run → PASS, **commit**.

### Task D2: UI — allow crypto instances through the list filter

**Files:** Modify `frontend/src/views/InstancesView.vue` (`i.kind !== 'kalshi'` filter ~137-139) so crypto instances render; add a read-only "24/7 · crypto" badge next to existing status pills.

- [ ] Change the filter to also allow `kind==="crypto"`; verify the build (`npm run build` in `frontend/`) is clean. **commit** `feat(crypto): show crypto instances in the instances list`.

### Task D3: End-to-end paper verification (no code — verification gate)

**Files:** none (uses `scripts/diag_alpaca_open.py`-style read-only checks against the paper account on the server).

- [ ] Create a paper `kind="crypto"` instance (medium band, `stocks=[]` ⇒ auto-discovery) via the DB/helper against the Alpaca **paper** brokerage.
- [ ] Start it; confirm from logs: boots, discovers a universe, fetches crypto bars from v1beta3 (not `/stocks/`), runs `run_once` on the monitor cadence, places a `gtc` order that is accepted (no `day`/extended-hours rejection), **including a tick that fires on a weekend/overnight** (proves 24/7).
- [ ] Run a short crypto backtest for the same instance; confirm fills show fee deduction.
- [ ] Confirm equity instances (`alpaca-main`) are untouched and still running.

---

## Self-Review

- **Spec coverage:** six seams → A3/A4/A5/A6 + A1 (scheduler) ; shared crypto core → A1/A2/B1 ; auto-discovery → B2 (+ backtest point-in-time C1) ; three strategies → B4/B5/B6 ; reference/proof → B3 ; backtest → C1 ; same-codebase → enforced by "modify shared files, branch on kind" throughout ; paper-only/live-gate → Global Constraints + D3 ; UI minimal → D2 ; instance creation → D1. Covered.
- **Placeholders:** strategy internals are specified by signal + test intent with concrete thresholds; exact indicator code is written during TDD against synthetic series (acceptable — logic is defined, not deferred).
- **Type consistency:** `core` names (`is_crypto_instance`, `crypto_scheduler_config`, `CRYPTO_FEES`, `round_trip_fee`, `fetch_crypto_bars`, `crypto_bars_url/params`, `build_crypto_order`, `vol_target_size`, `min_edge_to_trade`, `risk_off_targets`) and `discovery` names (`list_tradable_pairs`, `rank_universe`, `discover`) are used consistently across B/C/D.

## Execution order
A0 → A1 → A2 → A3 → A4 → A5 → A6 → B1 → B2 → B3 → B4 → B5 → B6 → C1 → D1 → D2 → D3.
Phase A is mostly sequential (shared-file edits). B3–B6 and B2 are independent of each other once B1 exists (parallelizable). D3 is the final verification gate.
