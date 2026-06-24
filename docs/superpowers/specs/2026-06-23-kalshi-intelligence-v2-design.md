# Kalshi Intelligence v2 — Design Document

**Status:** Draft v1 · Pre-build
**Date:** 2026-06-23
**Builds on:** `docs/superpowers/specs/2026-06-22-kalshi-prediction-markets-trading-design.md` (v1: lean Kalshi instance, de-vig edge, brokerage/instance lifecycle, web tab + mobile tab, instance create/start/stop, live logs — all shipped on branch `feat/kalshi-prediction-markets`).

**Goal:** Turn the Kalshi soccer bot from a single-market de-vig engine into a deep, multi-market, intelligence-driven trader that runs 24/7, auto-discovers every match, prices many bet types, layers ML + an LLM analyst panel on top of the sharp line, plans capital across the forward book, and explains every decision — with a dedicated clean runtime and an instance detail view.

---

## 0. Decisions locked in brainstorming

1. **Intelligence philosophy — sharp-anchored + ML for the gaps.** De-vigged sharp odds stay the anchor for markets they price (winner, totals). The ML model + LLM analyst panel are the PRIMARY signal for markets the sharp book doesn't price (player-to-score, exact score, props) and a bounded confidence/adjustment layer elsewhere. Fused per market type.
2. **Risk-tier → bet-type mapping** (per instance): **Low** = match winner / double-chance only (≤1 bet/game). **Medium** = + over/under goals, BTTS (≤2). **High** = + correct score, player-to-score, halftime; multiple correlated bets/game (≤4). **Max** = every market Kalshi lists; aggressive multi-bet/game. Higher tiers rely more on the model for exotic markets.
3. **LLM role — analyst panel: adjust + select + explain.** Reuses `nexus_analyst_panel`. Reads qualitative factors → a BOUNDED probability adjustment, a shortlist of exploitable markets, and a written rationale per bet (→ the decision log). Never bypasses the edge gate or risk caps.
4. **Capital planner — opportunity-scored + forward reserve + bounded portfolio optimizer.** Scores every forward opportunity; a constrained Kelly-across-opportunities optimizer allocates under caps + a forward reserve. Out of capital → queue + wait for settlements.
5. **Instance detail view** — click an instance → trades, equity history, the LLM-reasoned decision log, CLV per trade, per-market breakdown.
6. **Dedicated runtime** — clean Kalshi-only process; no equities/Socket.IO/broker interference; 24/7.

**Success metric unchanged:** consistently positive **CLV**. The new model layers must be validated against real outcomes before they're trusted with size; nothing here changes that the CLV gate decides whether this makes money.

---

## 1. Architecture overview

A Kalshi instance keeps the v1 shape (an `Instances` row `kind='kalshi'` bound to a Kalshi brokerage, started/stopped via `runCommand`, killable). v2 replaces the trivial v1 loop body with a layered pipeline:

```
 dedicated runtime (kalshi/runner.py, launched directly by server.py — no instance.py)
        │  24/7 scheduler tick
        ▼
 1. DISCOVERY     auto-discover fixtures (football-data.org) + Kalshi markets per match
        ▼
 2. FEATURE STORE per fixture: Elo/xG, form, h2h, home/away, lineups/injuries, player rates
        ▼
 3. INTELLIGENCE  base model (Dixon-Coles bivariate Poisson) + sharp de-vig anchor
                  + LLM analyst panel (bounded adjust + market shortlist + rationale)
                  → fused fair value per market
        ▼
 4. STRATEGY      enumerate markets → filter by instance risk tier → edge per market
                  → candidate bets (multiple/game, correlation-aware)
        ▼
 5. CAPITAL PLAN  opportunity score across forward calendar → constrained Kelly optimizer
                  under risk caps + forward reserve → orders (or wait-for-capital)
        ▼
 6. EXECUTE+LOG   KalshiClient.submit_order (gated) → kalshi_decisions row (model/sharp/LLM
                  reason/edge/size/score/outcome) → snapshot → notify → live log
```

**Reused (v1 + repo):** lean instance + brokerage + risk caps + kill switch + telemetry→cards + live logs + `KalshiClient`/signing/fees + `nexus_analyst_panel` LLM pattern + `llm_utils`/`bedrock_client`/`model_resolver` + `InstanceDetailView` (web) / `instance_detail_screen` (mobile) + `soccerdata`.

**New backend package layout** (extends `backend/kalshi/`):
- `runtime/` — `runner.py` (entrypoint), `scheduler.py` (24/7 tick).
- `data/` — `discovery.py` (fixtures+markets), `feature_store.py`, `sources/` (soccerdata, football-data adapters), `crosswalk.py` (extends existing `normalize.py`).
- `models/` — `dixon_coles.py` (scoreline distribution), `player_poisson.py`, `derive_markets.py` (scoreline dist → per-market probs).
- `intelligence/` — `analyst_panel.py` (LLM), `fusion.py` (sharp+model+LLM → fair value).
- `strategy/` — `market_enum.py`, `risk_tiers.py`, `candidates.py`.
- `capital/` — `opportunity.py` (scoring), `planner.py` (Kelly-across-book + reserve).
- `decisions.py` — decision-log row builder (pure) + writer.
- *(existing v1 modules — devig, edge, risk, clv, fees, client, engine, etc. — stay; `engine.run_instance` is rebuilt to orchestrate the pipeline.)*

---

## 2. Dedicated runtime (sub-project A)

**Problem:** v1 launched Kalshi instances through `instance.py`, which spins up a Socket.IO connection, a `broker.py` subprocess, and "0 seed tickers" logs — all equities scaffolding irrelevant to Kalshi, polluting the logs.

**Fix:** gate the launcher. `server.py`'s instance supervisor (`launch_instances_from_db` + the Instances changefeed, which today launches a Docker container running `instance.py`) checks the instance `kind`. For `kind='kalshi'` it runs **`python -m kalshi.runner <id>`** directly as the container command — a lean process that:
- reads `kalshi_config` + the linked brokerage from the DB,
- attaches the per-instance live log (already wired in v1),
- runs the v2 pipeline scheduler **continuously** (no market-hours/session gating — soccer resolves at kickoff times worldwide),
- polls `runCommand` + the kill switch each tick and exits cleanly when stopped.

No Socket.IO, no broker subprocess. The "different python binary" requirement is satisfied by a dedicated entrypoint/process (a separate optimized image can come later; not needed for correctness). Equities instances are completely unaffected (the gate only branches on `kind`).

**24/7 scheduler** (`kalshi/runtime/scheduler.py`): a simple loop with a configurable base cadence (the instance's `poll_seconds`) plus event-driven wakeups on settlement (capital freed) — pure next-wake logic, unit-testable.

---

## 3. Data layer + feature store (sub-project B)

**Discovery** (`data/discovery.py`): for each enabled league, list upcoming fixtures (football-data.org) and the Kalshi markets that exist per fixture (`KalshiClient.get_markets` by event), producing a `MatchListing` (fixture + available market tickers by type). Budget-aware (OddsPapi/scraper caps from v1's budget guard).

**Feature store** (`data/feature_store.py`): per fixture, assemble a `MatchFeatures` bundle from `soccerdata` (cached in RethinkDB):
- Team: ClubElo, season + recent xG for/against, form (last-N results), home/away splits, goals distribution.
- Head-to-head: recent meetings, scoreline history.
- Availability: confirmed lineup + injuries/suspensions (Sofascore/Understat).
- Players: per-player minutes, shots, xG, historical scoring rate (for player-to-score props), set-piece duties.
- Context: schedule congestion / days rest, (optional) weather.

Stored in new RethinkDB tables: `sports_fixtures` (exists), `team_stats`, `player_stats`, `h2h_history`, `lineups`, `match_features`. **Extensible feature store**: the bundle is a typed dict of feature groups; adding a new factor is a new group + a new source adapter, no re-architecture. v1 ships a strong initial set; "much more" is additive.

**Pure vs I/O split:** the source adapters do network/scraping (integration-tested); the feature-assembly + normalization is pure and unit-tested.

---

## 4. Intelligence engine (sub-project C)

**Base statistical model** (`models/dixon_coles.py`): a Dixon-Coles bivariate-Poisson fit on team attack/defense strengths (from xG + results), producing the **full scoreline probability matrix** P(home_goals=i, away_goals=j). From that matrix, `models/derive_markets.py` derives — in pure code — winner/draw/away, over/under any line, BTTS, exact score, correct score, double chance, halftime (with a halftime split factor). **Player-to-score** (`models/player_poisson.py`): per-player Poisson on minutes-adjusted xG/shot rate × lineup confirmation → P(player scores ≥1).

**Sharp anchor:** where Pinnacle prices a market (winner, totals), v1's de-vig remains the anchor. Where it doesn't (props, exact score), the model is primary.

**LLM analyst panel** (`intelligence/analyst_panel.py`, reuses `nexus_analyst_panel` + `llm_utils.call_structured_llm`): input = the `MatchFeatures` bundle + recent news/lineup notes; output (structured) = per-market **bounded** probability adjustment (clamped to ±`llm_adjust_cap`, default ±0.05), a ranked shortlist of exploitable markets for this match, and a concise rationale string per recommended bet. Runs once per fixture per scan window (cached; respects LLM cost via `llm_telemetry`/`model_resolver`).

**Fusion** (`intelligence/fusion.py`, pure): per market, `fair = w_sharp·sharp + w_model·model`, then apply the clamped LLM adjustment, renormalize within mutually-exclusive market groups (e.g. 1X2 sums to 1). Weights by market type: winner/totals → sharp-heavy; props/exact-score → model-heavy. All fused fair values flow into v1's existing **fee-net edge gate** unchanged.

**Guardrails:** LLM adjustment is hard-clamped; model probabilities run through a calibration step; a per-market `model_confidence` (from sample size / fit quality) feeds both sizing and the opportunity score. Low confidence → smaller or no bet.

---

## 5. Multi-market strategy + risk tiers (sub-project D)

`strategy/risk_tiers.py` maps the instance's risk tier → allowed market types + max bets/game (§0.2). `strategy/market_enum.py` lists the Kalshi markets available for a fixture; `strategy/candidates.py` prices each allowed market (fused fair value), computes fee-net edge, and emits `Candidate` bets — **multiple per game** — with **correlation awareness** (caps overlapping exposure, e.g. winner + correct-score that double-count the same outcome, within the per-game / per-league caps). Pure given features + markets + fair values; unit-tested on the tier filtering, multi-bet emission, and correlation caps.

---

## 6. Capital planner (sub-project E)

`capital/opportunity.py` (pure): `score = f(edge, model_confidence, liquidity, time_to_kickoff)` for every candidate across the forward calendar. `capital/planner.py` (pure given the candidate set + bankroll state): a **constrained Kelly-across-opportunities** allocator that maximizes expected log-growth subject to the risk caps **and a forward reserve** — it will hold back capital from a marginal bet now if a higher-scored opportunity is expected within a lookahead window. Re-solved each scan and on settlement (capital-freeing) events. **Out of capital** → candidates queue with their scores and are deployed to the best pending opportunity when funds free up. The optimizer is bounded (greedy fill by score with a reserve floor + a Kelly cap per bet) — not a full solver — so it can't misfire on real money. Unit-tested: reserve held for a better future opp; queue/deploy on capital free; caps respected.

---

## 7. Decision log + instance detail view (sub-project A, with the runtime)

**Decision log:** every bet placed AND every candidate considered-and-skipped writes a `kalshi_decisions` row (pure builder in `decisions.py`): fixture, market, model_prob, sharp_prob, llm_adjustment, llm_rationale, fused_fair, edge, fee, size, opportunity_score, decision (placed/skipped/queued/blocked), block_reason, and (on settlement) outcome + realized P&L + CLV.

**Backend endpoints** (brokerage-/instance-scoped, read-only): `GET /instances/{id}/kalshi/detail` (summary + config), `/instances/{id}/kalshi/decisions`, `/instances/{id}/kalshi/trades`, `/instances/{id}/kalshi/equity` (reuses the snapshot series).

**Web instance detail view** (`KalshiInstanceDetailView.vue`, route `/kalshi/instances/:id`): equity curve, open positions, the **decision log** (each row expandable to show model vs sharp vs LLM, the rationale, edge, sizing, outcome/CLV), per-market and per-league breakdown, start/stop/KILL, live logs. Reached by clicking an instance card on the Kalshi tab. Mirrors `InstanceDetailView`.

**Mobile instance detail screen** (`kalshi_instance_detail_screen.dart`): same content, native; reached by tapping an instance in the Kalshi screen's instances list.

---

## 8. Data model (new RethinkDB tables)

`team_stats`, `player_stats`, `h2h_history`, `lineups`, `match_features` (feature store); `kalshi_market_listings` (discovered markets per fixture); `kalshi_decisions` (the log); `kalshi_capital_plan` (current forward allocation + reserve state, for visibility). Plus v1's `sports_fixtures`, `kalshi_*`. All idempotently ensured (extends `kalshi/db.py`).

---

## 9. Build order (one continuous push, sliced)

- **A. Dedicated runtime + decision log + instance detail view.** Fixes the interference, gives visibility. (Includes the `kalshi_decisions` table + endpoints + web/mobile detail views; the engine writes decision rows even with v1 pricing.)
- **B. Data layer + feature store.** Discovery + soccerdata adapters + feature assembly + tables.
- **C. Intelligence engine.** Dixon-Coles + derive-markets + player Poisson + analyst panel + fusion.
- **D. Multi-market strategy + risk tiers.** Market enumeration + tier filtering + candidates + correlation.
- **E. Capital planner.** Opportunity scoring + constrained allocator + reserve + queue.

Each slice is independently shippable and leaves the bot working (A alone already cleans the runtime + adds the detail view; B–E progressively deepen the intelligence).

---

## 10. Testing

- **Pure cores fully unit-tested** (the high-value, real-money-adjacent logic): Dixon-Coles scoreline math + market derivation (known-answer fixtures), player Poisson, fusion (clamping + renormalization), risk-tier filtering + multi-bet + correlation caps, opportunity scoring, the capital allocator (reserve/queue/caps), the decision-row builder, the scheduler next-wake.
- **LLM analyst panel** tested with a stubbed `call_structured_llm` (asserts prompt assembly + output clamping + rationale capture); not a live LLM call.
- **Data adapters** integration-tested against recorded fixtures; feature assembly unit-tested.
- **Runtime/launcher gate** + endpoints: contract tests; the live launch path needs the Docker/live env to fully verify.
- Backend: `cd backend && python3 -m pytest tests/test_kalshi_*.py -q`. Web: `npm run build`. Mobile: `flutter analyze`.

---

## 11. Risk register / honest failure modes

| Risk | Severity | Mitigation |
|---|---|---|
| Model beats neither the sharp line nor randomness on props | High | CLV gate per market type; `model_confidence` gates size; validate prop models on real outcomes before sizing up |
| LLM hallucinated/overconfident adjustment moves real money | High | Hard-clamp adjustment (±cap); LLM never bypasses the edge gate or risk caps; bounded by `model_confidence` |
| Capital optimizer misallocates / over-concentrates | High | Bounded greedy-by-score + reserve floor + per-bet Kelly cap (not a free solver); existing risk caps still enforced |
| Scraper/feature-store breakage (`soccerdata`) | Medium | Multi-source; cache; football-data.org fallback for fixtures; degrade to sharp-only pricing when features missing |
| 24/7 loop hammers data sources / LLM cost | Medium | OddsPapi budget guard (v1); LLM cached per fixture/window + `llm_telemetry` cost tracking; adaptive cadence |
| Real-money bug in the new launch path | **Critical** | `kind`-gated (equities untouched); demo-first; live behind `live_enabled`; kill switch; decision log is the audit trail |
| Correlated multi-bets on one game blow the per-game cap | Medium | Correlation-aware candidate caps; per-game/per-league exposure caps from v1 risk manager |

---

## 12. Open / Phase-0 to verify
- Which Kalshi soccer **market types actually exist** (winner/totals/score/player props) and their tickers — confirm via `get_markets` against demo before building per-market derivation for markets Kalshi doesn't list.
- `soccerdata` coverage for the **target leagues' player-level data** (some lower divisions are thin) — degrade gracefully.
- LLM model choice + cost envelope for the analyst panel (via `model_resolver`).
- Same v1 Phase-0 items still open: OddsPapi free tier, Kalshi live fee schedule, prod host.

*Not financial advice. Event-contract trading can lose 100% of capital. The intelligence layers do not change that the CLV gate decides whether this makes money.*
