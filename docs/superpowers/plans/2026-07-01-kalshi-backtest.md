# Kalshi Soccer Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a historical backtest feature to the Kalshi soccer bot (backend replay + web + mobile UI) that replays real Kalshi prices vs real Pinnacle sharp lines over a user-chosen league/date range with tunable settings, caching all immutable historical data.

**Architecture:** A pure `run_backtest` core replays per-fixture pre-match decisions by calling the SAME engine functions the live bot uses (fusion/edge/candidates/risk/planner/reconcile). A `BacktestDataProvider` fetches-or-caches fixtures, scores, public Kalshi candlesticks, and OddsPapi historical odds into persistent RethinkDB tables. A lightweight in-process worker runs queued jobs; web+mobile mirror the existing stock-backtest UX and poll for progress.

**Tech Stack:** Python 3 / FastAPI / RethinkDB (backend), Vue 3 + ApexCharts (web), Flutter + Riverpod + syncfusion charts (mobile). Data: Kalshi public `/historical/.../candlesticks`, OddsPapi free tier.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-01-kalshi-backtest-design.md`.
- **Caching is mandatory:** immutable historical data (settled fixtures' scores, candles, odds) is fetched once and never re-fetched; re-running a backtest over the same fixtures must cost **0 API calls**. Only uncached/unsettled fixtures hit the network.
- OddsPapi free tier: ~250 req/mo, key is a **query param** (`apiKey`), reuse the existing budget guard (`ingest_odds.should_scan`/`fixtures_per_day_budget`, `db.bump_scan_budget`). Never overspend silently — log skips.
- Kalshi historical candlesticks endpoint is **public (no auth)**: `GET {base}/trade-api/v2/historical/markets/{ticker}/candlesticks?start_ts&end_ts&period_interval`.
- DB name `IntelliStock`; new tables via the idempotent `ensure_tables` idiom in `backend/kalshi/db.py`.
- Reuse engine pure functions — do NOT reimplement pricing/edge/sizing/settlement.
- Tests: pytest for backend (no network in unit tests — inject a fake data provider); local test stubs need `python3` + a stubbed `socketio` (per existing kalshi test setup). `flutter analyze` clean; web build passes.
- National-team Elo is current-only → label model-only metrics "indicative".
- New config field `oddspapi_api_key` alongside `odds_api_key`.
- Backtest instance id (RethinkDB `Instances`) full form is `032f0c62-23a6-45a9-ad39-ed60ed13d106`; brokerage `09ba81b2-...`.
- Commit after every green step. Branch: `feat/kalshi-backtest`.

---

## Phase 0 — Confirm live response schemas (spike, no production code)

### Task 0: Probe Kalshi candlesticks + OddsPapi schemas
**Files:** Create `scratchpad/probe_backtest_sources.py` (throwaway; not committed).
**Interfaces:** Produces the confirmed JSON shapes for (a) Kalshi historical candlesticks, (b) OddsPapi `/v4/fixtures`, (c) OddsPapi `/v4/historical-odds`. These shapes are pasted into Tasks 1 & 3 as the parsing contract.

- [ ] **Step 1:** Pull the OddsPapi key location: there is none yet — the probe uses a key from env `ODDSPAPI_API_KEY` (ask user to export, or skip OddsPapi probe if absent and rely on documented shape). Kalshi candlesticks need no key.
- [ ] **Step 2:** Fetch one settled WC market's candlesticks: `GET https://api.elections.kalshi.com/trade-api/v2/historical/markets/{ticker}/candlesticks?start_ts=..&end_ts=..&period_interval=60` (try base `https://api.elections.kalshi.com` and `https://trading-api.kalshi.com`; confirm which the repo's `client.py` uses). Record the JSON: `{candlesticks:[{end_period_ts, yes_bid:{open,high,low,close}, yes_ask:{...}, price:{...}, volume, open_interest}]}`.
- [ ] **Step 3:** If `ODDSPAPI_API_KEY` present: `GET https://api.oddspapi.io/v4/fixtures?sportId=10&from=..&to=..&apiKey=..` and `/v4/historical-odds?fixtureId=..&bookmakers=pinnacle&apiKey=..`. Record fixture fields (id, home, away, kickoff, score/result) and historical-odds shape (snapshots with ts + 3-way prices).
- [ ] **Step 4:** Write the two confirmed shapes as comments into `backend/kalshi/client.py` (candlesticks) and `backend/kalshi/ingest_odds.py` (OddsPapi) near the new methods. No commit needed for the throwaway script.

> If OddsPapi key is unavailable at build time, implement against the documented shape (spec §2) behind the normalized `parse_three_way` contract and mark the integration test `@pytest.mark.skipif` on missing key.

---

## Phase A — Backend data layer

### Task 1: Kalshi historical candlesticks client method
**Files:**
- Modify: `backend/kalshi/client.py` (add method to the client class)
- Test: `backend/tests/kalshi/test_client_candlesticks.py`

**Interfaces:**
- Produces: `KalshiClient.get_historical_candlesticks(ticker: str, start_ts: int, end_ts: int, period_interval: int = 60) -> list[dict]` returning a list of candlestick dicts each with keys `end_period_ts:int, yes_bid:dict, yes_ask:dict, price:dict, volume, open_interest`. Also a pure helper `parse_candlesticks(payload: dict) -> list[dict]` and `yes_ask_close_at(candles: list[dict], ts: int) -> int|None` (last candle with `end_period_ts <= ts`, returns `yes_ask['close']` in cents).

- [ ] **Step 1: Write failing test** for the pure helpers (no network):

```python
# backend/tests/kalshi/test_client_candlesticks.py
from kalshi.client import parse_candlesticks, yes_ask_close_at

RAW = {"candlesticks": [
    {"end_period_ts": 1000, "yes_bid": {"close": 40}, "yes_ask": {"close": 44}, "price": {"close": 42}, "volume": "5", "open_interest": "10"},
    {"end_period_ts": 2000, "yes_bid": {"close": 46}, "yes_ask": {"close": 50}, "price": {"close": 48}, "volume": "3", "open_interest": "12"},
]}

def test_parse_candlesticks_extracts_rows():
    rows = parse_candlesticks(RAW)
    assert len(rows) == 2 and rows[0]["end_period_ts"] == 1000

def test_yes_ask_close_at_returns_last_on_or_before():
    rows = parse_candlesticks(RAW)
    assert yes_ask_close_at(rows, 1500) == 44   # last <= 1500 is the 1000 candle
    assert yes_ask_close_at(rows, 2500) == 50
    assert yes_ask_close_at(rows, 500) is None   # nothing before

def test_parse_candlesticks_empty_is_safe():
    assert parse_candlesticks({}) == []
```

- [ ] **Step 2:** Run `pytest backend/tests/kalshi/test_client_candlesticks.py -v` → FAIL (import error).
- [ ] **Step 3: Implement** pure helpers + the fetch method in `client.py`:

```python
def parse_candlesticks(payload: dict) -> list[dict]:
    return list((payload or {}).get("candlesticks") or [])

def yes_ask_close_at(candles: list[dict], ts: int):
    best = None
    for c in candles:
        if int(c.get("end_period_ts", 0)) <= ts:
            best = c
        else:
            break
    if best is None:
        return None
    return int((best.get("yes_ask") or {}).get("close")) if (best.get("yes_ask") or {}).get("close") is not None else None
```
Add to the client class (mirror existing request style in `client.py`; historical endpoint is unauthenticated — plain GET):
```python
def get_historical_candlesticks(self, ticker, start_ts, end_ts, period_interval=60):
    url = f"{self.base_url}/historical/markets/{ticker}/candlesticks"
    params = {"start_ts": int(start_ts), "end_ts": int(end_ts), "period_interval": int(period_interval)}
    resp = self._session.get(url, params=params, timeout=20)
    resp.raise_for_status()
    return parse_candlesticks(resp.json() if resp.content else {})
```
(Confirm `self.base_url`/`self._session` names against the existing client; adapt.)

- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5: Commit** `feat(kalshi): historical candlesticks client method + pure price helpers`.

### Task 2: OddsPapi fixtures + historical-odds methods
**Files:**
- Modify: `backend/kalshi/ingest_odds.py`
- Test: `backend/tests/kalshi/test_ingest_odds_methods.py`

**Interfaces:**
- Produces: `OddsPapiClient.list_fixtures(sport_id: int, date_from: str, date_to: str) -> list[dict]`; `OddsPapiClient.historical_odds(fixture_id, books=("pinnacle",)) -> list[dict]`; pure parsers `parse_fixtures(raw) -> list[{fixture_id, home, away, kickoff_ts, home_score, away_score, result, settled}]` and `parse_hist_odds(raw) -> list[{ts, home, draw, away}]` (decimal odds per snapshot for the chosen book).

- [ ] **Step 1: Write failing tests** for the pure parsers using the Phase 0 confirmed shape (or documented shape). Include: fixtures parse (settled with scores → result derived home/draw/away; unsettled → settled=False, result None), hist odds parse (snapshots sorted by ts), empty-safe.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** parsers + thin methods over `fetch_raw` (already exists). `result` from scores: home if hs>as, away if as>hs, draw if equal; None if score missing.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat(kalshi): OddsPapi fixtures + historical-odds methods & parsers`.

### Task 3: Cache tables + BacktestDataProvider (fetch-or-cache)
**Files:**
- Create: `backend/kalshi/backtest_data.py`
- Modify: `backend/kalshi/db.py` (add 3 cache tables + 2 job tables to `KALSHI_TABLES`)
- Test: `backend/tests/kalshi/test_backtest_data.py`

**Interfaces:**
- Produces: `class BacktestDataProvider` with:
  - `fixtures(leagues, start_date, end_date) -> list[Fixture]`
  - `final_score(fx) -> str|None` (`home|draw|away`, None if unsettled)
  - `candles(ticker) -> list[dict]`
  - `sharp_odds(fx) -> list[dict]`  (snapshots)
  - `kalshi_tickers(fx) -> dict[str,str]` (side→ticker)
  - counters: `.api_calls`, `.cache_hits`
  - Constructor injects `kalshi_client`, `oddspapi_client`, `conn`, `budget_used_getter/bumper` — all optional so tests pass fakes.
- Consumes: Task 1 & 2 client methods; `db` table names.

- [ ] **Step 1: Write failing tests** (inject FAKE clients + an in-memory dict "table"):
  - cache miss → calls client once, stores row, `api_calls==1`
  - second call same key → no client call, `cache_hits==1`, returns stored
  - fixture with `settled=False` cached → `final_score` re-fetches (may now be settled); once `settled=True` → never re-fetches
  - OddsPapi budget exhausted (`should_scan` false) → returns cached-or-None, logs skip, does NOT call client
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Implement** the provider. Cache read/write against RethinkDB tables (or the injected fake). Keys per spec §5. Use `db.bump_scan_budget` for OddsPapi calls; guard with `should_scan`. Increment `.api_calls`/`.cache_hits`.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Add tables to `db.KALSHI_TABLES`: `("KalshiBacktests","id")`, `("KalshiBacktestResults","id")`, `("KalshiHistCandles","id")`, `("KalshiHistOdds","id")`, `("KalshiHistFixtures","fixture_key")`. Run `pytest backend/tests/kalshi/ -k db -v`.
- [ ] **Step 6: Commit** `feat(kalshi): backtest cache tables + fetch-or-cache data provider`.

### Task 4: Fixture ↔ Kalshi ticker resolution
**Files:**
- Modify: `backend/kalshi/backtest_data.py` (add `kalshi_tickers`)
- Test: `backend/tests/kalshi/test_backtest_ticker_match.py`

**Interfaces:**
- Produces: `kalshi_tickers(fx) -> {"home": ticker, "draw": ticker, "away": ticker}` by matching an OddsPapi fixture to Kalshi market tickers, reusing `data/ticker_names.py` (`parse_market_ticker`, `name_for_code`) and `normalize.py`/`quant.national_elo.canonical_team` for name folding, with the fuzzy-uniqueness safety of `odds_api.match_event`.

- [ ] **Step 1: Write failing test:** given a fake Kalshi market list for `KXWCGAME-26JUL01ENGCOD-*` and an OddsPapi fixture England/DR Congo, resolve the three side tickers; a name variant ("Congo DR"/"DR Congo") still matches; an unmatched fixture returns `{}` and is logged (not guessed).
- [ ] **Step 2-4:** FAIL → implement (reuse crosswalk + fuzzy) → PASS.
- [ ] **Step 5: Commit** `feat(kalshi): resolve OddsPapi fixture to Kalshi tickers`.

---

## Phase B — Backend replay engine

### Task 5: BacktestConfig + caps builder
**Files:**
- Create: `backend/kalshi/backtest.py` (config dataclass + helpers)
- Test: `backend/tests/kalshi/test_backtest_config.py`

**Interfaces:**
- Produces: `@dataclass BacktestConfig{ leagues, start_date, end_date, bankroll_cents, caps: RiskCaps, sharp_weight, devig_method, decision_offsets_sec: tuple=( -3*3600,), fee_rate }` and `config_from_body(dict) -> BacktestConfig` reusing `instance_config.risk_caps_from_config`.
- Consumes: `instance_config`, `risk.RiskCaps`.

- [ ] **Step 1-4 (TDD):** test that `config_from_body` maps the same knobs the live instance uses (edge_threshold, no_sharp_edge_threshold, kelly_fraction, order_size_min/max_cents, max_open_exposure_frac, per_bet_cap_frac, min/max_price_cents, draw_min_edge, sharp_weight, devig_method, bankroll_cents) into a `BacktestConfig`; defaults applied when missing. Implement. PASS.
- [ ] **Step 5: Commit** `feat(kalshi): backtest config + caps builder`.

### Task 6: Per-fixture evaluate() — reuse fusion/edge/candidates/planner
**Files:**
- Modify: `backend/kalshi/backtest.py`
- Test: `backend/tests/kalshi/test_backtest_evaluate.py`

**Interfaces:**
- Produces: `evaluate(cfg, model_probs, sharp_probs, kalshi_asks, fixture) -> list[SizedBet]` where `SizedBet{ side, market_ticker, entry_cents, size, model_prob, sharp_prob, fused_fair, edge }`. Internally builds fused probs via `intelligence.fusion.build_market_probs`, generates candidates via `strategy.candidates.generate_candidates`, sizes via `capital.planner.allocate` with `cfg.caps`. `model_probs`/`sharp_probs` are `{side: prob}`; `kalshi_asks` is `{side: cents}`.
- Consumes: fusion, candidates, planner, edge (all existing).

- [ ] **Step 1: Write failing test:** with a fabricated model (home 0.55), a sharp line (home 0.50 devigged), and Kalshi ask home 45¢, edge>threshold → returns one home SizedBet with size>0; when sharp says 0.44 and ask 45¢ (edge≈0) → returns [] (gated out). A no-sharp case honors `no_sharp_edge_threshold`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: Implement** by wiring the existing functions (match their real signatures — read `orchestrator.py` `plan_and_allocate` for the exact call sequence and replicate the pre-match slice).
- [ ] **Step 4:** PASS.
- [ ] **Step 5: Commit** `feat(kalshi): backtest per-fixture evaluate reuses live pricing/sizing`.

### Task 7: settle() + aggregate()
**Files:**
- Modify: `backend/kalshi/backtest.py`
- Test: `backend/tests/kalshi/test_backtest_settle.py`

**Interfaces:**
- Produces: `settle(bet: SizedBet, result: str, fee_rate) -> Trade{...bet fields, outcome, realized_pnl_cents, clv}` (reuse `reconcile.reconcile_position` semantics: win → (100-entry)*size - fee; loss → -entry*size); `aggregate(trades, bankroll_cents) -> BacktestResult{ pnl_cents, roi, n_bets, win_rate, clv_avg, equity_curve, per_league, calibration, trades }`. Equity curve ordered by fixture kickoff.
- Consumes: `reconcile`.

- [ ] **Step 1: Write failing test:** a home bet entry 45¢ size 10, result home → pnl = (100-45)*10 - fee; result away → -450. `aggregate` of [win 550, loss -450] → pnl_cents 100, n_bets 2, win_rate 0.5, equity_curve cumulative [550, 100] in kickoff order; calibration buckets predicted vs actual.
- [ ] **Step 2-4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(kalshi): backtest settlement + aggregation (equity/roi/clv/calibration)`.

### Task 8: run_backtest() orchestration
**Files:**
- Modify: `backend/kalshi/backtest.py`
- Test: `backend/tests/kalshi/test_backtest_run.py`

**Interfaces:**
- Produces: `run_backtest(cfg, data: BacktestDataProvider, model_fn, progress_cb=None) -> BacktestResult`. `model_fn(fx) -> {side: prob}` injected (so tests don't need Elo; production passes an Elo/Dixon-Coles closure). Loops fixtures in kickoff order, skips unsettled (score None) and unmatched (no tickers) fixtures with a logged reason, evaluates at `cfg.decision_offsets_sec` before kickoff using `yes_ask_close_at`+`sharp_at`, settles, calls `progress_cb(frac)`, returns aggregate. One decision snapshot per fixture (first offset that produces candles+odds).
- Consumes: Tasks 3,6,7 + `client.yes_ask_close_at`.

- [ ] **Step 1: Write failing test** with a fully FAKE data provider (2 fixtures: one bettable win, one gated-out) + fake model_fn → asserts result.n_bets==1, pnl>0, progress_cb called with 1.0, and that a fixture with score None is skipped. Assert `data.api_calls`/`cache_hits` are surfaced into `result.summary`.
- [ ] **Step 2-4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(kalshi): run_backtest orchestration over cached historical data`.

### Task 9: Production model_fn (Elo/Dixon-Coles as-of) + wiring helper
**Files:**
- Modify: `backend/kalshi/backtest.py`
- Test: `backend/tests/kalshi/test_backtest_modelfn.py`

**Interfaces:**
- Produces: `build_model_fn(nat_elo_table, elo_table) -> (fx -> {side:prob})` reusing `quant`/`intelligence.pricing.model_market_probs` (same path as engine). Documented look-ahead caveat (national elo current-only).
- [ ] **Step 1-4 (TDD):** test that for a fabricated fixture with known Elos it returns a 3-way summing to 1.0 (reusing the real pricing chain). Implement. PASS.
- [ ] **Step 5: Commit** `feat(kalshi): production model_fn via live pricing chain (indicative nat-elo)`.

---

## Phase C — Backend API + worker

### Task 10: Job row helpers + doc builders
**Files:**
- Modify: `backend/kalshi/db.py` (pure doc builders + thin writers)
- Test: `backend/tests/kalshi/test_backtest_job_docs.py`

**Interfaces:**
- Produces: `backtest_job_doc(*, id, brokerage_id, instance_id, name, config, leagues, start_date, end_date, bankroll_cents, created_at) -> dict` (status="pending", run=True, progress=0); `backtest_result_doc(id, result: BacktestResult) -> dict`. Thin writers `create_backtest_job`, `update_backtest_progress`, `save_backtest_result`, `list_backtests`, `get_backtest`, `set_backtest_run`.
- [ ] **Step 1-4 (TDD)** on pure doc builders (no DB). Implement. PASS.
- [ ] **Step 5: Commit** `feat(kalshi): backtest job + result doc builders and DB writers`.

### Task 11: Background worker
**Files:**
- Create: `backend/kalshi/backtest_worker.py`
- Test: `backend/tests/kalshi/test_backtest_worker.py`

**Interfaces:**
- Produces: `run_job(job: dict, *, conn, clients) -> None` (builds cfg+provider+model_fn, runs `run_backtest`, writes progress throttled ≤ every 2%, saves result, sets status finished/error, honors `run==False` → stopped). `start_worker(conn)` — changefeed on `KalshiBacktests` pending + boot drain (mirror `backtest_engine._changefeed_worker`/`_load_initial_queue`), small ThreadPool.
- [ ] **Step 1: Write failing test** for `run_job` with fakes: a pending job dict + fake provider/model → asserts result saved, status→finished, progress reaches 100; a job with `run=False` mid-flight → status stopped.
- [ ] **Step 2-4:** FAIL → implement → PASS. (Changefeed/threadpool tested only via `run_job`; the loop is integration.)
- [ ] **Step 5: Commit** `feat(kalshi): in-process backtest worker (changefeed + drain + stop)`.

### Task 12: FastAPI endpoints + startup hook
**Files:**
- Modify: `backend/api/main.py` (routes + `KalshiBacktestBody` model + start worker at startup) — locate the existing Kalshi routes and add alongside.
- Test: `backend/tests/api/test_kalshi_backtest_endpoints.py` (TestClient, worker disabled / job left pending)

**Interfaces:**
- Produces routes (spec §7): `POST /brokerages/{bid}/kalshi/backtests`, `GET /brokerages/{bid}/kalshi/backtests`, `GET /kalshi/backtests/{id}/status`, `GET /kalshi/backtests/{id}/results`, `POST /kalshi/backtests/{id}/stop`, `DELETE /kalshi/backtests/{id}`. Body validated by `KalshiBacktestBody`.
- [ ] **Step 1: Write failing test:** POST creates a pending row (assert 200 + id + row in a fake/real test DB); GET status returns it; stop sets run=False; DELETE removes. Use the existing test-app fixture pattern (find how other endpoint tests build the TestClient + auth + conn dependency override).
- [ ] **Step 2-4:** FAIL → implement (thin handlers calling Task 10 writers; enqueue only — worker runs async) → PASS.
- [ ] **Step 5: Commit** `feat(api): kalshi backtest endpoints + worker startup`.

---

## Phase D — Web UI (`frontend/`)

### Task 13: API calls + backtest list/section on Kalshi instance detail
**Files:**
- Modify: `frontend/src/views/KalshiInstanceDetailView.vue` (add Backtest section + "New backtest" button in header action group; list past backtests; poll running)
- (Reuse inline `fetch` + `authHeaders()` idiom.)

**Interfaces:**
- Consumes endpoints from Task 12. Poll `GET /kalshi/backtests/{id}/status` every 3s while status∈{pending,running} (pattern: `BacktestsView.vue:140-161`), render progress bar (`BacktestsView.vue:385-410`).
- [ ] **Step 1:** Add data + methods: `loadBacktests()`, `pollRunning()`, `openCreateBacktest()`, `stopBacktest(id)`, `openResults(id)`.
- [ ] **Step 2:** Add template: a "Backtests" card section (list rows: name, date range, status pill, progress bar, P&L, actions) + header button.
- [ ] **Step 3:** Manual check: `cd frontend && npm run build` passes; dev server renders the section (empty state ok).
- [ ] **Step 4: Commit** `feat(web): kalshi backtest list + section on instance detail`.

### Task 14: Create-backtest modal (leagues + tuning + dates)
**Files:**
- Create: `frontend/src/components/kalshi/KalshiBacktestModal.vue` (adapt `KalshiCreateInstanceModal.vue`)
**Interfaces:**
- Reuse league multi-select (`LEAGUES`/`toggleLeague`), numeric tuning grid + `InfoTip`, native `<input type=date>` range, bankroll. Prefill from the instance config (`GET /instances/{id}/kalshi/detail`). Submit `POST /brokerages/{bid}/kalshi/backtests` then show it in the list / open results.
- [ ] **Step 1:** Build the modal from a copy of `KalshiCreateInstanceModal.vue`, swap the "create instance" submit for the backtest body ({leagues, start_date, end_date, bankroll_cents, config}); remove live-only fields; add date range.
- [ ] **Step 2:** Wire open/close from Task 13's button; on submit, refresh list.
- [ ] **Step 3:** `npm run build` passes.
- [ ] **Step 4: Commit** `feat(web): kalshi backtest create modal (leagues/tuning/date range)`.

### Task 15: Results view (equity curve + tables)
**Files:**
- Create: `frontend/src/views/KalshiBacktestResultView.vue`; route in `frontend/src/router/index.js` (`/kalshi/backtests/:id`)
**Interfaces:**
- `GET /kalshi/backtests/{id}/results` → render: summary stat cards (pnl, roi, win rate, clv, api_calls_used, cache_hits), equity curve via `KalshiPortfolioChart.vue` (feed `equity_curve`), trade `<table>`, calibration `<table>`, per-league `<table>`. Poll status until finished.
- [ ] **Step 1:** Build view; reuse `KalshiPortfolioChart`.
- [ ] **Step 2:** Route + navigation from the list row.
- [ ] **Step 3:** `npm run build` passes.
- [ ] **Step 4: Commit** `feat(web): kalshi backtest results view (equity, trades, calibration)`.

---

## Phase E — Mobile UI (`mobile/`)

### Task 16: Repo methods + providers
**Files:**
- Modify: `mobile/lib/features/kalshi/data/kalshi_repository.dart`
**Interfaces:**
- Produces: `createBacktest(bid, body) -> id`, `listBacktests(bid)`, `backtestStatus(id)`, `backtestResults(id)`, `stopBacktest(id)` + `FutureProvider.autoDispose.family` providers (mirror existing kalshi getters).
- [ ] **Step 1-2:** Add methods + providers (existing Dio idiom). `flutter analyze` clean.
- [ ] **Step 3: Commit** `feat(mobile): kalshi backtest repository methods + providers`.

### Task 17: Create sheet + list section
**Files:**
- Create: `mobile/lib/features/kalshi/presentation/kalshi_backtest_sheet.dart` (mirror `KalshiInstanceSheet`)
- Modify: `mobile/lib/features/kalshi/presentation/kalshi_instance_detail_screen.dart` (Backtests section + AppBar action → open sheet)
**Interfaces:**
- Sheet collects leagues (chips), tuning `_field` numerics, date text fields, bankroll → `createBacktest`. List section shows past backtests with `StatusPill` + `LinearProgressIndicator`, tap → results screen.
- [ ] **Step 1-2:** Build sheet + section. `flutter analyze` clean.
- [ ] **Step 3: Commit** `feat(mobile): kalshi backtest create sheet + list section`.

### Task 18: Results screen
**Files:**
- Create: `mobile/lib/features/kalshi/presentation/kalshi_backtest_result_screen.dart`; route in `mobile/lib/core/router/router.dart` (`/kalshi/backtests/:id` on `_rootKey`)
**Interfaces:**
- Render summary `StatTile` grid, equity `ScrubbableAreaChart` (from `core/charts/`), trades `DataTable`, calibration + per-league. Poll status via `Timer.periodic` until finished.
- [ ] **Step 1-2:** Build screen + route. `flutter analyze` clean.
- [ ] **Step 3: Commit** `feat(mobile): kalshi backtest results screen`.

---

## Phase F — Integration, bug sweep, verify

### Task 19: End-to-end smoke against the live DB (Tailscale up)
- [ ] Run one real backtest for a small WC date window via the API (worker on), confirm: fixtures resolved, candles+odds fetched & CACHED (`api_calls>0`, `cache_hits==0` first run), a SECOND identical run has `api_calls==0`/`cache_hits>0`, results populate (pnl/equity/trades/calibration). Record numbers.
- [ ] Verify OddsPapi budget guard: with budget near limit, extra fixtures are skipped + logged, not silently dropped.

### Task 20: Parallel bug sweep + full suite
- [ ] Dispatch parallel review agents (correctness of pricing reuse, cache correctness/immutability, budget-guard, ticker matching, API/worker lifecycle, UI wiring). Fix confirmed findings.
- [ ] `pytest backend/tests/kalshi backend/tests/api -q` green; `cd frontend && npm run build`; `cd mobile && flutter analyze` (pre-existing infos only).
- [ ] `npx gitnexus analyze` to refresh the index; `git` clean; open PR.

---

## Self-Review (against spec)

**Coverage:** §2 sources → Tasks 0,1,2; §5 tables → Task 3/10; §5 caching rule → Task 3 (+T19 proof); §6 replay → Tasks 5-9; §7 endpoints → Task 12; §4.1 worker → Task 11; §8 web → Tasks 13-15; §9 mobile → Tasks 16-18; §10 testing → per-task + T19/T20; §11 risks (budget/ticker/lookahead/schema/restart) → Tasks 2-4,11,0. **No gaps.**

**Placeholders:** none — UI tasks reference exact reusable components + endpoints; backend tasks carry test code and signatures. UI tasks give interfaces + files rather than full component source (skilled-dev + identified components); acceptable for this layer.

**Type consistency:** `BacktestDataProvider` methods, `BacktestConfig`, `SizedBet`, `Trade`, `BacktestResult`, `run_backtest`, `evaluate`, `settle`, `aggregate`, `build_model_fn`, doc builders — names used consistently across Tasks 3–12.
