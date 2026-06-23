# Kalshi Intelligence v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the Kalshi soccer bot into a deep, multi-market, intelligence-driven 24/7 trader with a clean dedicated runtime, ML scoreline models, an LLM analyst panel, a capital planner, and an instance detail view with an LLM decision log.

**Architecture:** Sharp-anchored + ML-for-the-gaps. A `kind='kalshi'` instance runs a dedicated `kalshi.runner` process (no equities/socket scaffolding) that orchestrates discovery → feature store → intelligence (Dixon-Coles + LLM panel + fusion) → risk-tiered multi-market candidates → opportunity-scored capital planner → execute + decision log. Pure cores are unit-tested; data/LLM/launch are integration code.

**Tech Stack:** Python 3 / FastAPI / RethinkDB; Vue 3; Flutter; `soccerdata`, `scipy`/`numpy` (Dixon-Coles), `llm_utils`/`bedrock_client` (LLM). Tests: pytest (`backend/tests/`), flutter analyze, npm build.

**Spec:** `docs/superpowers/specs/2026-06-23-kalshi-intelligence-v2-design.md`

---

## File structure

New under `backend/kalshi/`:
- `runtime/runner.py` (replaces v1 `kalshi/runner.py` role — dedicated entrypoint), `runtime/scheduler.py` (pure next-wake).
- `data/discovery.py`, `data/feature_store.py`, `data/sources/soccerdata_src.py`, `data/sources/footballdata_src.py`.
- `models/dixon_coles.py`, `models/derive_markets.py`, `models/player_poisson.py`.
- `intelligence/analyst_panel.py`, `intelligence/fusion.py`.
- `strategy/risk_tiers.py`, `strategy/market_enum.py`, `strategy/candidates.py`.
- `capital/opportunity.py`, `capital/planner.py`.
- `decisions.py` (pure row builder + writer), `feature_models.py` (typed feature bundle DTOs).
Modified: `backend/kalshi/engine.py` (run_instance orchestration), `backend/kalshi/db.py` (new tables), `backend/server.py` (launcher gate), `backend/api/main.py` (detail endpoints).
Web: `frontend/src/views/KalshiInstanceDetailView.vue` + route + Kalshi-tab instance links.
Mobile: `mobile/lib/features/kalshi/presentation/kalshi_instance_detail_screen.dart` + route.

Tests in `backend/tests/test_kalshi_*.py` (direct imports; conftest adds backend/ to path; run `cd backend && python3 -m pytest tests/test_kalshi_<x>.py -q`).

---

## Slice A — dedicated runtime + decision log + instance detail view

### Task A1: Decision-log row builder (pure)

**Files:** Create `backend/kalshi/decisions.py`, `backend/tests/test_kalshi_decisions.py`

- [ ] **Step 1 — failing tests:**
```python
from kalshi.decisions import decision_doc, summarize_decisions

def test_decision_doc_captures_model_sharp_llm_and_edge():
    d = decision_doc(instance_id="i1", brokerage_id="b1", ts="t",
                     fixture_id="f1", market_ticker="KX-HOME", side="home",
                     model_prob=0.55, sharp_prob=0.52, llm_adjustment=0.01,
                     llm_rationale="Home unbeaten in 6; key striker fit.",
                     fused_fair=0.55, edge=0.04, fee=0.01, size=12,
                     opportunity_score=0.8, decision="placed")
    assert d["id"] == "i1|KX-HOME|t"
    assert d["model_prob"] == 0.55 and d["sharp_prob"] == 0.52
    assert d["llm_rationale"].startswith("Home unbeaten")
    assert d["decision"] == "placed" and d["edge"] == 0.04

def test_summarize_groups_by_decision():
    rows = [{"decision": "placed"}, {"decision": "placed"}, {"decision": "skipped"}]
    s = summarize_decisions(rows)
    assert s["placed"] == 2 and s["skipped"] == 1 and s["total"] == 3
```
- [ ] **Step 2** — run, verify ImportError.
- [ ] **Step 3 — implement** `decision_doc(**fields)` returning a dict with id `f"{instance_id}|{market_ticker}|{ts}"` + all fields (defaults: outcome=None, realized_pnl_cents=None, clv=None, block_reason=""), and `summarize_decisions(rows)` counting by `decision` + `total`.
- [ ] **Step 4** — run, pass. **Step 5** — commit `feat(kalshi): decision-log row builder`.

### Task A2: New tables in db.py

**Files:** Modify `backend/kalshi/db.py`; Test `backend/tests/test_kalshi_db.py` (extend)

- [ ] Add to `KALSHI_TABLES`: `("kalshi_decisions","id")`, `("team_stats","id")`, `("player_stats","id")`, `("h2h_history","id")`, `("lineups","fixture_id")`, `("match_features","fixture_id")`, `("kalshi_market_listings","fixture_id")`, `("kalshi_capital_plan","instance_id")`. Add a test asserting `kalshi_decisions` and `match_features` are present. Commit.

### Task A3: Launcher gate (server.py) — run kalshi.runner directly

**Files:** Modify `backend/server.py` (the instance-launch command builder)

- [ ] Run `gitnexus_impact` on the launch function first; report risk. The supervisor builds the Docker container command running `instance.py <id>`. Add a `kind` check: when the Instances row `kind=='kalshi'`, set the container command to `python -m kalshi.runner <id>` (no instance.py). Equities instances unchanged. This removes the Socket.IO/broker/"0 seed tickers" interference. (Integration — verified by reading the clean log on a live launch; add a unit test only if the command builder is a pure helper.) Commit.

### Task A4: kalshi/runtime/runner.py (dedicated entrypoint) + scheduler

**Files:** Create `backend/kalshi/runtime/runner.py`, `backend/kalshi/runtime/scheduler.py`, `backend/kalshi/runtime/__init__.py`, `backend/tests/test_kalshi_scheduler.py`

- [ ] **scheduler (pure, tested):** `next_wake_seconds(poll_seconds, pending_settlement_in)` → `min(poll_seconds, pending_settlement_in)` when a settlement is sooner, else `poll_seconds`; floor 5s. Test both branches.
- [ ] **runner:** `python -m kalshi.runner <id>` loads the instance + brokerage, attaches the per-instance live log (v1 `_make_logger`), and calls `engine.run_instance` (rebuilt in C/D/E to run the pipeline; until then it runs the v1 loop). Move v1's `backend/kalshi/runner.py` body here; keep `backend/kalshi/runner.py` re-exporting `main` for back-compat (`python -m kalshi.runner` still works). Commit.

### Task A5: Detail endpoints

**Files:** Modify `backend/api/main.py`

- [ ] Add read endpoints: `GET /instances/{id}/kalshi/decisions?limit=` (query `kalshi_decisions` by instance, newest first, via a pure shaper in `decisions.py`), `GET /instances/{id}/kalshi/detail` (instance row config + run state + brokerage env), `GET /instances/{id}/kalshi/equity` (reuse `kalshi_portfolio_snapshots` by the instance's brokerage). All `get_current_user`-guarded, degrade to empty. Contract-test the shapers. Commit.

### Task A6: Web instance detail view

**Files:** Create `frontend/src/views/KalshiInstanceDetailView.vue`; Modify `frontend/src/router/index.js` (route `/kalshi/instances/:id`), `frontend/src/views/KalshiView.vue` (instances list → link each to the detail route)

- [ ] Build the detail view (glass-card theme): header (name, status, Start/Stop/KILL), equity chart (reuse `KalshiPortfolioChart`), open positions, **decision log** (each row expandable: model vs sharp vs LLM adjustment + rationale, edge, size, outcome/CLV), per-market breakdown, live logs (`InstanceLiveLogs`). Fetches the A5 endpoints. `npm run build` clean. Commit.

### Task A7: Mobile instance detail screen

**Files:** Create `mobile/lib/features/kalshi/presentation/kalshi_instance_detail_screen.dart`; Modify `mobile/lib/core/router/router.dart` (route), `kalshi_screen.dart` (instances list → tap to detail), `kalshi_repository.dart` (decisions/detail fetch)

- [ ] Native detail screen (GlassCard theme): status + Start/Stop/KILL, equity sparkline, positions, decision log (expandable rationale), `LiveLogsPanel`. `flutter analyze` clean. Commit.

---

## Slice B — data layer + feature store

### Task B1: Feature DTOs

**Files:** Create `backend/kalshi/feature_models.py`, `backend/tests/test_kalshi_feature_models.py`

- [ ] Frozen dataclasses: `TeamForm(elo, xg_for, xg_against, form_pts, home_xg, away_xg)`, `PlayerRate(name, minutes, shots, xg_per90, scored_last_n)`, `MatchFeatures(fixture_id, home, away, home_form, away_form, h2h, players, days_rest_home, days_rest_away, lineup_confirmed)`. Test construction + a `MatchFeatures.is_complete()` predicate (has both team forms). Commit.

### Task B2: Discovery (pure parse + client)

**Files:** Create `backend/kalshi/data/discovery.py`, `backend/tests/test_kalshi_discovery.py`

- [ ] Pure `market_types_for(listing)` → set of market types from Kalshi market tickers/subtitles; pure `to_match_listing(fixture, kalshi_markets)`. Client `discover(league, client, fixtures_client)` (integration). Test the pure parsers on fixtures. Commit.

### Task B3: soccerdata + football-data adapters

**Files:** Create `backend/kalshi/data/sources/soccerdata_src.py`, `footballdata_src.py`

- [ ] Wrap `soccerdata` (FBref/Understat/ClubElo/Sofascore) → `TeamForm`/`PlayerRate`; football-data fixtures (extends v1 `ingest_fixtures`). Lazy import (`soccerdata` optional). Pure mapping helpers unit-tested against recorded frames; live scrape is integration. Add `soccerdata`, `scipy`, `numpy` to `backend/requirements.txt`. Commit.

### Task B4: Feature store assembly (pure) + writer

**Files:** Create `backend/kalshi/data/feature_store.py`, `backend/tests/test_kalshi_feature_store.py`

- [ ] Pure `assemble_features(fixture, home_team, away_team, h2h, players, lineup)` → `MatchFeatures`; writer caches to `match_features` (db.py helper). Test assembly + missing-data degradation (returns a partial bundle, `is_complete()` False). Commit.

---

## Slice C — intelligence engine

### Task C1: Dixon-Coles scoreline distribution (pure)

**Files:** Create `backend/kalshi/models/dixon_coles.py`, `backend/tests/test_kalshi_dixon_coles.py`

- [ ] **failing tests:**
```python
from kalshi.models.dixon_coles import scoreline_matrix, rho_correction

def test_matrix_sums_to_one():
    m = scoreline_matrix(home_xg=1.6, away_xg=1.1, max_goals=10)
    total = sum(sum(row) for row in m)
    assert abs(total - 1.0) < 1e-6

def test_higher_home_xg_raises_home_win_mass():
    m1 = scoreline_matrix(1.2, 1.2, 10)
    m2 = scoreline_matrix(2.0, 1.0, 10)
    home_win = lambda m: sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if i > j)
    assert home_win(m2) > home_win(m1)

def test_rho_correction_adjusts_low_scores():
    # Dixon-Coles low-score dependence (0-0,1-0,0-1,1-1) tweak; rho<0 lowers 1-1
    assert rho_correction(0, 0, 1.5, 1.2, rho=-0.1) != 1.0
```
- [ ] **implement** Poisson PMF × Dixon-Coles low-score `rho` correction, normalized matrix. Use `math` (no scipy needed for the PMF). pass. Commit.

### Task C2: Derive per-market probabilities (pure)

**Files:** Create `backend/kalshi/models/derive_markets.py`, `backend/tests/test_kalshi_derive_markets.py`

- [ ] From a scoreline matrix, pure derivations:
```python
from kalshi.models.dixon_coles import scoreline_matrix
from kalshi.models.derive_markets import one_x_two, over_under, btts, exact_score, double_chance

def test_one_x_two_sums_to_one():
    m = scoreline_matrix(1.6, 1.1, 10)
    p = one_x_two(m)
    assert abs(p["home"] + p["draw"] + p["away"] - 1.0) < 1e-6

def test_over_under_complement():
    m = scoreline_matrix(1.6, 1.1, 10)
    o = over_under(m, line=2.5)
    assert abs(o["over"] + o["under"] - 1.0) < 1e-6

def test_btts_and_exact_score_bounds():
    m = scoreline_matrix(1.6, 1.1, 10)
    assert 0 < btts(m)["yes"] < 1
    assert 0 < exact_score(m, 1, 1) < 1
```
- [ ] implement `one_x_two`, `over_under(line)`, `btts`, `exact_score(i,j)`, `double_chance`. pass. Commit.

### Task C3: Player-to-score Poisson (pure)

**Files:** Create `backend/kalshi/models/player_poisson.py`, `backend/tests/test_kalshi_player_poisson.py`

- [ ] `p_player_scores(xg_per90, expected_minutes, confirmed_starter)` = `1 - exp(-lambda)` where `lambda = xg_per90 * minutes/90 * start_factor`. Test: more minutes/xg → higher prob; bounded (0,1); unconfirmed → reduced. Commit.

### Task C4: Fusion (pure)

**Files:** Create `backend/kalshi/intelligence/fusion.py`, `backend/tests/test_kalshi_fusion.py`

- [ ] `fuse(sharp, model, llm_adjustment, *, w_sharp, llm_cap)`:
```python
from kalshi.intelligence.fusion import fuse, clamp_adjustment, renormalize_group

def test_clamp_adjustment():
    assert clamp_adjustment(0.20, cap=0.05) == 0.05
    assert clamp_adjustment(-0.20, cap=0.05) == -0.05

def test_fuse_blends_and_applies_clamped_llm():
    f = fuse(sharp=0.50, model=0.60, llm_adjustment=0.20, w_sharp=0.7, llm_cap=0.05)
    base = 0.7*0.50 + 0.3*0.60
    assert abs(f - (base + 0.05)) < 1e-9   # llm clamped to +0.05

def test_fuse_model_only_when_no_sharp():
    f = fuse(sharp=None, model=0.62, llm_adjustment=0.0, w_sharp=0.7, llm_cap=0.05)
    assert abs(f - 0.62) < 1e-9

def test_renormalize_group_sums_to_one():
    g = renormalize_group({"home": 0.6, "draw": 0.3, "away": 0.3})
    assert abs(sum(g.values()) - 1.0) < 1e-9
```
- [ ] implement (sharp=None → model only; clamp llm; renormalize). pass. Commit.

### Task C5: LLM analyst panel (stubbed-LLM test)

**Files:** Create `backend/kalshi/intelligence/analyst_panel.py`, `backend/tests/test_kalshi_analyst_panel.py`

- [ ] `analyze(features, markets, *, llm_call)` builds a prompt from the `MatchFeatures` bundle, calls `llm_call` (injected; defaults to `llm_utils.call_structured_llm`), and returns `{adjustments: {market: float}, shortlist: [..], rationales: {market: str}}` with adjustments clamped. Test with a fake `llm_call` returning canned JSON: asserts prompt includes team names + the adjustments are clamped + rationales captured. Commit.

---

## Slice D — multi-market strategy + risk tiers

### Task D1: Risk tiers (pure)

**Files:** Create `backend/kalshi/strategy/risk_tiers.py`, `backend/tests/test_kalshi_risk_tiers.py`

- [ ] `allowed_markets(tier)` + `max_bets_per_game(tier)` per §0.2:
```python
from kalshi.strategy.risk_tiers import allowed_markets, max_bets_per_game

def test_low_is_winner_only():
    assert allowed_markets("low") == {"winner", "double_chance"}
    assert max_bets_per_game("low") == 1

def test_max_allows_everything():
    a = allowed_markets("max")
    assert {"winner", "over_under", "btts", "exact_score", "player_score"} <= a
    assert max_bets_per_game("max") >= 4
```
- [ ] implement tier maps (low/medium/high/max). pass. Commit.

### Task D2: Candidate generation (pure)

**Files:** Create `backend/kalshi/strategy/candidates.py`, `backend/tests/test_kalshi_candidates.py`

- [ ] `generate_candidates(fixture_id, tier, market_probs, kalshi_markets, *, fee_rate, edge_threshold)` → list of `Candidate(market_ticker, market_type, side, fair, edge, ...)`, filtered to `allowed_markets(tier)`, edge>threshold, capped at `max_bets_per_game(tier)` (keep highest-edge), correlation-aware (drop a candidate that's a strict subset/overlap of a kept higher-edge one). Tests: tier filtering, multi-bet cap, correlation drop, edge filter. Commit.

---

## Slice E — capital planner

### Task E1: Opportunity scoring (pure)

**Files:** Create `backend/kalshi/capital/opportunity.py`, `backend/tests/test_kalshi_opportunity.py`

- [ ] `score(edge, model_confidence, liquidity, hours_to_kickoff)` = `edge * model_confidence * liquidity_factor(liquidity) * time_factor(hours)` where nearer kickoff and higher liquidity score higher (bounded). Tests: monotonic in edge + confidence; near-kickoff > far. Commit.

### Task E2: Capital allocator (pure)

**Files:** Create `backend/kalshi/capital/planner.py`, `backend/tests/test_kalshi_planner.py`

- [ ] `allocate(candidates, *, bankroll_cents, caps, reserve_frac, expected_better_soon)`:
```python
from kalshi.capital.planner import allocate

def test_holds_reserve_for_better_future_opp():
    cands = [{"id":"now","score":0.3,"edge":0.04,"fair":0.55,"price_cents":50}]
    out = allocate(cands, bankroll_cents=100000, caps=CAPS, reserve_frac=0.4,
                   expected_better_soon=True)
    spent = sum(o["stake_cents"] for o in out)
    assert spent <= 60000   # reserve (40%) held when a better opp is expected

def test_deploys_when_no_better_soon():
    cands = [{"id":"now","score":0.9,"edge":0.08,"fair":0.6,"price_cents":50}]
    out = allocate(cands, bankroll_cents=100000, caps=CAPS, reserve_frac=0.4,
                   expected_better_soon=False)
    assert sum(o["stake_cents"] for o in out) > 0

def test_respects_per_bet_kelly_and_caps():
    # each stake <= quarter-Kelly and <= per-market cap
    ...
```
- [ ] implement greedy-by-score fill with ¼-Kelly per-bet cap, per-market/per-league caps, and a reserve floor (hold `reserve_frac` when `expected_better_soon`). Returns allocations + a `queued` list when out of capital. Reuse `kalshi.risk.quarter_kelly_fraction`. pass. Commit.

---

## Slice F — orchestration

### Task F1: Rebuild engine.run_instance to run the pipeline

**Files:** Modify `backend/kalshi/engine.py`

- [ ] Replace the v1 loop body with: discovery → feature store → for each fixture: scoreline model + sharp anchor + analyst panel → fusion per market → candidates by tier → collect across fixtures → opportunity score → allocate → execute (gated) → write `kalshi_decisions` + snapshot → log. Each sub-step wrapped so one failure doesn't kill the loop; degrade to sharp-only when features/LLM unavailable. Integration (pragma no cover); the pieces are unit-tested. A `run_once_v2(...)` decision function (no network) gets a dry-run unit test asserting the candidate→allocation→decision-row flow with fakes. Commit.

---

## Self-review

- **Spec coverage:** §2 runtime→A3/A4; §3 data→B; §4 intelligence→C; §5 strategy→D; §6 capital→E; §7 decision log + detail view→A1/A5/A6/A7; §8 tables→A2/B4; §9 build order→slices; §10 testing→TDD tasks. No gaps.
- **Placeholders:** pure cores (A1, C1–C4, D1–D2, E1–E2, scheduler) have full test+impl; data/LLM/launch/UI are concrete (paths + interfaces + key code) integration tasks verified by run/build/analyze, not unit asserts — flagged as such.
- **Type consistency:** `MatchFeatures`/`Candidate`/`Decision` names consistent; `scoreline_matrix` → market derivations all consume the matrix; `fuse(sharp, model, llm_adjustment)` signature stable; cents are ints, probs floats 0–1.

## Execution
Inline (user-directed): A1–A7, B1–B4, C1–C5, D1–D2, E1–E2, F1, then parallel bug sweep, then push. Pure cores are fully TDD-verified this session; data adapters / LLM panel / launch gate / detail-view UIs build real code but their live paths need creds/libs/Docker to fully verify.
