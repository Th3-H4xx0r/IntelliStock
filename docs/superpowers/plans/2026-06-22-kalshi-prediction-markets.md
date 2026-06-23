# Kalshi Prediction-Markets Trading — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fully-autonomous Kalshi soccer event-contract trading to IntelliStock as lean, Kalshi-only instances, surfaced on a web tab, a dashboard card, and a dedicated mobile tab.

**Architecture:** A new `backend/kalshi/` package holds a lean instance engine (odds → de-vig → edge → ¼-Kelly → order) isolated from the equities trade path. It reuses the brokerage model, instance lifecycle, kill switch, credential/secret store, scheduler, notifications, order WAL, and the nexus telemetry→cards pipeline. New surface: a Kalshi v2 RSA-PSS REST/WS client, OddsPapi/fixtures ingestion, the de-vig/edge math, CLV, and the Vue + Flutter UI.

**Tech Stack:** Python 3 / FastAPI / RethinkDB (backend), Vue 3 + ApexCharts (web), Flutter/Riverpod/go_router (mobile). Tests: pytest (`backend/tests/`), flutter test.

**Spec:** `docs/superpowers/specs/2026-06-22-kalshi-prediction-markets-trading-design.md`

---

## File structure

**Backend — new package `backend/kalshi/`:**
- `__init__.py` — exports public DTOs + functions.
- `models.py` — pure dataclasses: `KalshiMarket`, `KalshiContractPosition`, `KalshiOrderRef`, `KalshiFill`, `KalshiBalance`, `Fixture`, `OddsQuote`, `EdgeFlag`.
- `devig.py` — `power_devig`, `shin_devig`, `proportional_devig` (pure).
- `fair_value.py` — `fair_from_odds`, optional Elo/Dixon-Coles blend (pure).
- `edge.py` — `implied_from_cents`, `fee_as_prob`, `compute_edge`, `flag_edges` (pure).
- `risk.py` — `quarter_kelly_fraction`, `RiskCaps`, `size_order`, `check_caps` (pure).
- `clv.py` — `compute_clv`, `summarize_clv` (pure).
- `normalize.py` — `normalize_team`, `TeamCrosswalk` (pure).
- `telemetry.py` — pure row→card-payload shapers (mirrors `nexus_telemetry.py`).
- `fees.py` — live Kalshi fee-schedule fetch + cache (pulls, never hardcodes).
- `client.py` — Kalshi v2 REST + WS, RSA-PSS signing (`KalshiClient`).
- `signing.py` — RSA-PSS SHA256 path-signing helper (separately unit-tested).
- `ingest_odds.py` — OddsPapi client + budget guard + cache.
- `ingest_fixtures.py` — football-data.org + `soccerdata`.
- `engine.py` — the lean instance loop.
- `replay.py` — order-book/odds replay simulator (feature #8).
- `db.py` — RethinkDB table ensure + typed read/write helpers for `kalshi_*` tables.

**Backend — modified:**
- `backend/api/main.py` — add `/kalshi/*` read endpoints + `/brokerages/test-kalshi`.
- `backend/live_kill_switch.py` — add a Kalshi branch to the per-brokerage cancel step.
- `backend/credential_service.py` / `secret_store.py` — store/load Kalshi key id + RSA PEM.

**Web — new (`frontend/src/`):**
- `views/KalshiView.vue` — the tab page (card feed; chart hero).
- `components/kalshi/` — `KalshiPortfolioChart.vue` (wraps `PortfolioChart.vue`), `EdgeRadarCard.vue`, `PositionsCard.vue`, `ClvScorecardCard.vue`, `SettlementCard.vue`, `RiskPanelCard.vue`, `WhyTradeCard.vue`, `SteamCard.vue`, `FillsCard.vue`, `InstancesCard.vue`, `OddsBudgetCard.vue`, `ScanQueueCard.vue`, `KalshiDashboardCard.vue`.
- `router/router.js` — add `/kalshi` route.
- `views/BrokeragesView.vue` — add a `kalshi` tab + form.
- `views/DashboardView.vue` — mount `KalshiDashboardCard`.
- `components/TheNavbar.vue` — add the `⚽ Kalshi` nav link.

**Mobile — new (`mobile/lib/features/kalshi/`):** `data/` (DTOs + repository), `application/` (Riverpod providers, NOT keep-alive-empty), `presentation/` (`kalshi_screen.dart`, `kalshi_dashboard_card.dart`, cards, `trade_detail_screen.dart`).
**Mobile — modified:** `core/router/app_shell.dart` (promote Kalshi tab, Backtests→More), `core/router/more_sheet.dart` (+Kalshi, +Backtests), `core/router/router.dart` + `route_screens.dart` (routes), `features/dashboard/presentation/dashboard_screen.dart` (mount card).

---

## Phase B-core: pure logic (TDD) — the actual edge

> Build this first: it's the edge, it's fully unit-testable with no creds, and it's off the real-money path. Tests live in `backend/tests/`; run `cd backend && python3 -m pytest tests/test_kalshi_<x>.py -q`.

### Task 1: Package skeleton + models

**Files:** Create `backend/kalshi/__init__.py`, `backend/kalshi/models.py`

- [ ] **Step 1: Create `backend/kalshi/models.py`** with frozen dataclasses:

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class OddsQuote:
    """Decimal odds for a 3-way (1X2) soccer market from one book."""
    book: str
    home: float
    draw: float
    away: float

@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    sport: str
    league: str
    home: str
    away: str
    kickoff_utc: str  # ISO8601

@dataclass(frozen=True)
class KalshiMarket:
    market_ticker: str
    fixture_id: str
    side: str          # 'home' | 'draw' | 'away'
    yes_ask_cents: int # 0..100

@dataclass(frozen=True)
class EdgeFlag:
    fixture_id: str
    market_ticker: str
    side: str
    fair_prob: float
    kalshi_implied: float
    fee: float
    edge: float

@dataclass(frozen=True)
class KalshiContractPosition:
    market_ticker: str
    side: str          # 'YES' | 'NO'
    contracts: int
    avg_price_cents: float
    current_price_cents: Optional[float] = None

@dataclass(frozen=True)
class KalshiBalance:
    cash_cents: int
    portfolio_value_cents: int
```

- [ ] **Step 2: Create `backend/kalshi/__init__.py`** re-exporting the dataclasses and (added in later tasks) the pure functions.

```python
from kalshi.models import (
    OddsQuote, Fixture, KalshiMarket, EdgeFlag,
    KalshiContractPosition, KalshiBalance,
)
```

- [ ] **Step 3: Commit** — `git commit -m "feat(kalshi): package skeleton + DTOs"`

### Task 2: De-vig (`devig.py`)

**Files:** Create `backend/kalshi/devig.py`, `backend/tests/test_kalshi_devig.py`

- [ ] **Step 1: Write failing tests** in `backend/tests/test_kalshi_devig.py`:

```python
import math
from kalshi.devig import proportional_devig, power_devig, shin_devig

def _implied(o):  # decimal odds -> raw implied prob
    return [1.0/x for x in o]

def test_proportional_normalizes_to_one():
    p = proportional_devig(_implied([2.0, 3.5, 4.0]))
    assert abs(sum(p) - 1.0) < 1e-9
    assert p[0] > p[1] > p[2]

def test_power_sums_to_one_and_shifts_vig_to_longshots():
    raw = _implied([1.5, 4.5, 7.0])  # heavy favorite + longshots
    p = power_devig(raw)
    assert abs(sum(p) - 1.0) < 1e-9
    prop = proportional_devig(raw)
    # power removes proportionally MORE from the longshot than proportional does
    assert p[-1] < prop[-1]

def test_shin_sums_to_one():
    p = shin_devig(_implied([2.1, 3.3, 3.6]))
    assert abs(sum(p) - 1.0) < 1e-9
    assert all(0.0 < x < 1.0 for x in p)
```

- [ ] **Step 2: Run, verify fail** — `python3 -m pytest tests/test_kalshi_devig.py -q` → ImportError.

- [ ] **Step 3: Implement `devig.py`:**

```python
"""De-vig methods for n-way markets. Input: raw implied probs (1/decimal_odds).
Output: fair probabilities summing to 1.0."""
from __future__ import annotations

def proportional_devig(raw: list[float]) -> list[float]:
    o = sum(raw)
    return [q / o for q in raw]

def power_devig(raw: list[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Find k s.t. sum(q_i**k) == 1; return q_i**k. Bisection on k>0."""
    lo, hi = 0.0001, 10.0
    def s(k): return sum(q ** k for q in raw)
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        val = s(mid)
        if abs(val - 1.0) < tol:
            break
        # s(k) is decreasing in k for overround>1; raise k to shrink sum
        if val > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    return [q ** k for q in raw]

def shin_devig(raw: list[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Shin (1992): solve for insider proportion z; p_i = (sqrt(z^2 + 4(1-z)q_i^2/O) - z)/(2(1-z))."""
    o = sum(raw)
    z_lo, z_hi = 0.0, 0.2
    def probs(z):
        return [ (math.sqrt(z*z + 4*(1-z)*(q*q)/o) - z) / (2*(1-z)) for q in raw ]
    import math
    for _ in range(max_iter):
        z = (z_lo + z_hi) / 2
        p = probs(z)
        tot = sum(p)
        if abs(tot - 1.0) < tol:
            return p
        if tot > 1.0:
            z_lo = z
        else:
            z_hi = z
    return probs((z_lo + z_hi) / 2)
```

(Move `import math` to module top.)

- [ ] **Step 4: Run, verify pass.** **Step 5: Commit** — `feat(kalshi): de-vig (power/shin/proportional)`.

### Task 3: Fair value (`fair_value.py`)

**Files:** Create `backend/kalshi/fair_value.py`, `backend/tests/test_kalshi_fair_value.py`

- [ ] **Step 1: Failing test:**

```python
from kalshi.models import OddsQuote
from kalshi.fair_value import fair_from_odds

def test_fair_from_pinnacle_quote():
    q = OddsQuote(book="pinnacle", home=2.0, draw=3.5, away=4.0)
    f = fair_from_odds(q, method="power")
    assert abs(f["home"] + f["draw"] + f["away"] - 1.0) < 1e-9
    assert f["home"] > f["away"]
```

- [ ] **Step 2: Implement:**

```python
from kalshi.models import OddsQuote
from kalshi.devig import power_devig, shin_devig, proportional_devig

_METHODS = {"power": power_devig, "shin": shin_devig, "proportional": proportional_devig}

def fair_from_odds(q: OddsQuote, method: str = "power") -> dict:
    raw = [1.0/q.home, 1.0/q.draw, 1.0/q.away]
    p = _METHODS[method](raw)
    return {"home": p[0], "draw": p[1], "away": p[2]}
```

- [ ] **Step 3: Run pass. Step 4: Commit.**

### Task 4: Edge (`edge.py`) — fees baked in

**Files:** Create `backend/kalshi/edge.py`, `backend/tests/test_kalshi_edge.py`

- [ ] **Step 1: Failing tests:**

```python
from kalshi.models import KalshiMarket, EdgeFlag
from kalshi.edge import implied_from_cents, compute_edge, flag_edges

def test_implied_from_cents():
    assert abs(implied_from_cents(48) - 0.48) < 1e-9

def test_compute_edge_subtracts_fee():
    # fair 0.53, ask 48c -> raw edge 0.05; fee 0.01 -> net 0.04
    e = compute_edge(fair_prob=0.53, yes_ask_cents=48, fee=0.01)
    assert abs(e - 0.04) < 1e-9

def test_flag_edges_filters_below_threshold():
    fair = {"home": 0.53, "draw": 0.25, "away": 0.22}
    markets = [
        KalshiMarket("KX-HOME", "f1", "home", 48),  # edge +0.04 (fee .01)
        KalshiMarket("KX-AWAY", "f1", "away", 21),  # edge ~+0.0 -> filtered
    ]
    flags = flag_edges("f1", fair, markets, fee=0.01, threshold=0.03)
    assert [f.market_ticker for f in flags] == ["KX-HOME"]
    assert isinstance(flags[0], EdgeFlag)
```

- [ ] **Step 2: Implement:**

```python
from kalshi.models import KalshiMarket, EdgeFlag

def implied_from_cents(cents: float) -> float:
    return cents / 100.0

def compute_edge(*, fair_prob: float, yes_ask_cents: float, fee: float) -> float:
    return fair_prob - (implied_from_cents(yes_ask_cents) + fee)

def flag_edges(fixture_id, fair: dict, markets: list[KalshiMarket], *, fee: float, threshold: float) -> list[EdgeFlag]:
    out = []
    for m in markets:
        fp = fair[m.side]
        e = compute_edge(fair_prob=fp, yes_ask_cents=m.yes_ask_cents, fee=fee)
        if e > threshold:
            out.append(EdgeFlag(fixture_id, m.market_ticker, m.side, fp,
                                implied_from_cents(m.yes_ask_cents), fee, e))
    return sorted(out, key=lambda f: f.edge, reverse=True)
```

- [ ] **Step 3: Run pass. Step 4: Commit.**

### Task 5: Risk + sizing (`risk.py`)

**Files:** Create `backend/kalshi/risk.py`, `backend/tests/test_kalshi_risk.py`

- [ ] **Step 1: Failing tests:**

```python
from kalshi.risk import quarter_kelly_fraction, RiskCaps, size_order, check_caps

def test_quarter_kelly():
    # edge 0.04, fair 0.53 -> 0.25 * 0.04/(1-0.53)
    f = quarter_kelly_fraction(edge=0.04, fair_prob=0.53)
    assert abs(f - 0.25 * 0.04/0.47) < 1e-9

def test_size_order_caps_contracts_per_market():
    caps = RiskCaps(max_contracts_per_market=50, bankroll_cents=100000)
    n = size_order(edge=0.20, fair_prob=0.55, yes_ask_cents=50, caps=caps)
    assert 0 < n <= 50

def test_check_caps_blocks_daily_loss():
    caps = RiskCaps(daily_loss_cap_cents=40000)
    ok, reason = check_caps(caps, day_pnl_cents=-40000, open_exposure_frac=0.1, league="EPL", league_exposure_frac=0.1)
    assert ok is False and "daily" in reason.lower()
```

- [ ] **Step 2: Implement** `quarter_kelly_fraction`, a `RiskCaps` dataclass (edge_threshold, kelly_fraction=0.25, max_contracts_per_market, max_open_exposure_frac, per_league_cap_frac, daily_loss_cap_cents, bankroll_cents), `size_order` (fraction × bankroll ÷ price, floored, clamped to `max_contracts_per_market`), and `check_caps` (returns `(ok, reason)`, blocks on daily-loss, max-exposure, per-league). Full code in the module.

- [ ] **Step 3: Run pass. Step 4: Commit.**

### Task 6: CLV (`clv.py`)

**Files:** Create `backend/kalshi/clv.py`, `backend/tests/test_kalshi_clv.py`

- [ ] **Step 1: Failing tests:** entry 48¢ vs close 44¢ → positive CLV (bought cheaper than close); `summarize_clv` groups by league with avg + n. **Step 2: Implement** `compute_clv(entry_cents, close_cents)` = `implied(close) - implied(entry)` for YES (you want the line to move toward you), and `summarize_clv(rows)`. **Step 3: pass. Step 4: commit.**

### Task 7: Team crosswalk (`normalize.py`)

**Files:** Create `backend/kalshi/normalize.py`, `backend/tests/test_kalshi_normalize.py`

- [ ] **Step 1: Failing tests:** `normalize_team("Man Utd")=="Manchester United"`, case/whitespace-insensitive, unknown passes through title-cased. **Step 2: Implement** a `TeamCrosswalk` backed by a replacements dict + `normalize_team`. **Step 3: pass. Step 4: commit.**

### Task 8: Telemetry shapers (`telemetry.py`)

**Files:** Create `backend/kalshi/telemetry.py`, `backend/tests/test_kalshi_telemetry.py`

- [ ] **Step 1: Failing tests:** `edge_radar_payload(flags, limit)` returns rows sorted by edge with kickoff countdown fields; `portfolio_series(snapshots, range)` downsamples; `settlement_items(positions)` computes ETA. **Step 2: Implement** pure shapers (mirror `nexus_telemetry.py` style — no DB). **Step 3: pass. Step 4: commit.**

---

## Phase A: foundation (brokerage + client + read path)

### Task 9: RSA-PSS signing (`signing.py`)

**Files:** Create `backend/kalshi/signing.py`, `backend/tests/test_kalshi_signing.py`

- [ ] **Step 1: Failing test** — generate a throwaway RSA key in the test, sign `"GET" + "/trade-api/v2/portfolio/balance" + timestamp`, verify with the public key + PSS/SHA256. **Step 2: Implement** `sign_request(private_key_pem, method, path, ts_ms) -> str` (base64 RSA-PSS SHA256 over `f"{ts_ms}{method}{path}"`, path WITHOUT query) and `access_headers(key_id, method, path, ts_ms, pem) -> dict` returning the three `KALSHI-ACCESS-*` headers. **Step 3: pass. Step 4: commit.**

### Task 10: Fees (`fees.py`)

**Files:** Create `backend/kalshi/fees.py`, `backend/tests/test_kalshi_fees.py`

- [ ] Pull the fee schedule live (Kalshi exposes per-market fee params); cache with TTL; `fee_as_prob(market_meta, price_cents)`. Test the *parsing/calc* against a captured fixture and a 0%-promo case. Never hardcode a constant. Commit.

### Task 11: Kalshi client (`client.py`)

**Files:** Create `backend/kalshi/client.py`

- [ ] `KalshiClient(key_id, private_key_pem, environment)` → base URL `demo-api.kalshi.co` vs `api.elections.kalshi.com` (prod). Methods: `get_balance`, `get_positions`, `get_fills`, `get_markets(event)`, `get_orderbook(ticker)`, `submit_order(...)` (limit, client_order_id), `cancel_order`, `cancel_all_open_orders`, plus a `connect_ws()` for prices/fills. Uses `signing.access_headers`. Returns `models.*` DTOs. Integration test against demo is a separate manual step (needs creds) — unit-test request construction with a mocked transport. Commit.

### Task 12: DB tables (`db.py`)

**Files:** Create `backend/kalshi/db.py`

- [ ] `ensure_tables(conn)` creates `sports_fixtures`, `kalshi_markets`, `kalshi_odds_snapshots`, `kalshi_edges`, `kalshi_orders`, `kalshi_positions`, `kalshi_portfolio_snapshots`, `kalshi_clv_log`, `team_stats`, `kalshi_scan_budget` (idempotent, mirrors existing table-ensure code). Typed read/write helpers. Commit.

### Task 13: Brokerage type — backend + web + mobile

**Files:** Modify `backend/api/main.py`, `backend/credential_service.py`/`secret_store.py`, `frontend/src/views/BrokeragesView.vue`, `frontend/src/views/BrokeragesView.vue` (form), mobile `features/brokerages/`.

- [ ] Backend: accept `brokerage_type='kalshi'` on brokerage create/update; store `key_id` + RSA PEM via `secret_store`; `environment` field; `POST /brokerages/test-kalshi` validates against `/portfolio/balance`. Web: add a `kalshi` tab + form (account name, key id, PEM textarea, demo/live toggle). Mobile: add Kalshi to the brokerage add flow. Commit per surface.

### Task 14: Read endpoints (`/kalshi/*`)

**Files:** Modify `backend/api/main.py` (+ small handlers using `kalshi/telemetry.py` + `kalshi/db.py`).

- [ ] Implement the §8 read endpoints (`/kalshi/portfolio`, `/positions`, `/edges`, `/fills`, `/clv`, `/settlement`, `/risk`, `/rationale/:id`, `/steam/:fixtureId`, `/scan-budget`), brokerage-scoped, read-only. Each thin: query `kalshi_*` → shape via `telemetry.py` → JSON. Contract test each returns the documented shape with seeded rows. Commit.

---

## Phase B-ingest + engine

### Task 15: Odds ingestion (`ingest_odds.py`)

- [ ] OddsPapi client (key as query param), `kalshi_scan_budget` guard (refuse when month quota exhausted; `log()` what was skipped), cache to `kalshi_odds_snapshots` with `ts`. Unit-test the budget guard + the snapshot-write shape against a captured OddsPapi response fixture. Commit.

### Task 16: Fixtures/stats ingestion (`ingest_fixtures.py`)

- [ ] football-data.org schedule/results → `sports_fixtures`; `soccerdata` xG/Elo → `team_stats`; team names crosswalked via `normalize.py`. Cache; respect rate limits. Unit-test the fixture-normalization mapping. Commit.

### Task 17: Lean engine (`engine.py`)

**Files:** Create `backend/kalshi/engine.py`

- [ ] The §3.2 loop: read odds (cached) → `fair_from_odds` → `flag_edges` (fee from `fees.py`) → `risk.size_order` + `check_caps` → `client.submit_order` (WAL via `broker_adapters/_wal.py`) → snapshot to `kalshi_portfolio_snapshots` + write `kalshi_edges` → notify. Honors `runCommand=False` and the kill switch each iteration (poll like equities instances). **Dry-run gate:** orders only fire when the instance's brokerage is connected AND `live_enabled` (hard gate; demo unrestricted). **Dry-run unit test:** given canned odds + markets + a fake client, assert the exact orders it *would* place and that `check_caps` blocks the right ones (no network). Commit.

### Task 18: Kill-switch Kalshi branch

**Files:** Modify `backend/live_kill_switch.py`

- [ ] In the per-brokerage cancel step, when the linked brokerage `brokerage_type=='kalshi'`, call `KalshiClient(...).cancel_all_open_orders()`. Test: a kalshi-linked instance halt cancels via the Kalshi path; an unlinked instance cancels nothing (fail-safe, preserved). Commit.

### Task 19: Replay simulator (`replay.py`) — feature #8 (deferrable)

- [ ] Replay recorded `kalshi_odds_snapshots` + order-book snapshots through `engine`'s decision functions; report hypothetical fills + CLV; document limitations (no queue/latency/impact). Unit-test over a tiny recorded fixture. Commit.

---

## Phase UI-web

### Task 20: Router + navbar + dashboard card

- [ ] Add `/kalshi` route (`router.js`), `⚽ Kalshi` link in `TheNavbar.vue`, and mount `KalshiDashboardCard.vue` (compact: value + sparkline + day P&L + counts → links to `/kalshi`) in a new "Kalshi" section of `DashboardView.vue`. Commit.

### Task 21: KalshiView + cards

- [ ] Build `KalshiView.vue` (card feed; chart hero half-desktop/full-mobile via CSS grid) + the card components in `components/kalshi/`, each fetching its `/kalshi/*` endpoint. `KalshiPortfolioChart.vue` wraps `PortfolioChart.vue` with `1D/1W/1M/ALL` ranges. Account selector (demo/live) + KILL button in the header (KILL calls the existing halt endpoint scoped to the selected instance). Commit per a few cards.

## Phase UI-mobile

### Task 22: Promote Kalshi tab + routes

**Files:** Modify `core/router/app_shell.dart`, `more_sheet.dart`, `router.dart`, `route_screens.dart`.

- [ ] In `app_shell.dart` `_destinations`, insert `(_DestKind.branch, 'sports_soccer', 'Kalshi', 1)` after Dashboard and renumber; move Backtests off the bar. Add Backtests + Kalshi to `more_sheet.dart`. Add the `/kalshi` branch + routes. Commit.

### Task 23: Kalshi feature (screen + cards + detail)

**Files:** Create `mobile/lib/features/kalshi/...`

- [ ] DTOs + Dio repository for `/kalshi/*`; Riverpod providers (pull-to-refresh `invalidate`, NOT keep-alive-empty — known repo gotcha); `kalshi_screen.dart` (full-width chart + ranges, KPIs, Edge Radar, positions, instances, KILL header), `kalshi_dashboard_card.dart` (compact), `trade_detail_screen.dart` (live P&L, settlement countdown, why-this-trade, steam, manual sell). Golden tests for the cards. Commit.

---

## Phase: notifications + wiring

### Task 24: Notification categories

- [ ] Add Kalshi categories (`kalshi_edge_flag`, `kalshi_fill`, `kalshi_settlement`, `kalshi_risk_block`, `kalshi_killswitch`, `kalshi_budget_low`) to `notification_types.py`; emit from `engine.py`; route via existing Discord/iOS push. Test routing. Commit.

---

## Self-review

- **Spec coverage:** §3 lean engine→T17; §3.4 brokerage→T13; §3.5 kill switch→T18; §4 tables→T12; §5 de-vig/edge/risk→T2–5; §6 client/signing→T9–11; §7 A/B/C phases→all; §8 endpoints→T14; §9 UI→T20–23; §10 features 1–7,9→cards/telemetry, #8→T19; §11 notifications→T24; §13/§14 testing→TDD tasks. No gaps.
- **Placeholder scan:** core modules (T2–8) have full test+impl code; T10–23 are concrete (paths + interfaces + key code) but coarser-grained for client/ingest/UI where TDD is integration/visual — verify those by running + on-device, not unit asserts.
- **Type consistency:** `EdgeFlag`, `KalshiMarket`, `KalshiContractPosition`, `RiskCaps` names match across tasks; `fee` is always a probability; cents are ints 0–100.

## Execution

Inline execution (user-directed): implement T1–24, then parallel bug sweep, then push. The pure-core tasks (T1–8) are fully TDD-verified this session; client/ingest/engine (T9–19) build real code + dry-run/unit tests but live API + on-device paths need creds (Phase 0) to fully verify; UI (T20–23) needs a running app/device to verify visually.
