# Kalshi Sharp-Odds Anchor — Implementation Plan

> Pure-core TDD. Wire real bookmaker odds → de-vig → `sharp_probs` so edges become
> "sharp consensus vs Kalshi price". Works identically on demo + real (odds are
> environment-agnostic; only the Kalshi price source differs). Degrade-safe.

**Architecture:** New `kalshi/data/sources/odds_api.py` (The-Odds-API v4) + a
`sharp_probs_from_quote` helper, wired into `run_instance`'s pre-pass so the existing
de-vig/fusion/edge pipeline finally gets fed. Config: `odds_api_key` (config/env),
`sharp_weight`, `devig_method`, `odds_refresh_secs`, `odds_regions`.

---

### Task 1: `odds_api.py` — pure parse + match + sport-key + integration fetch

**Files:** Create `backend/kalshi/data/sources/odds_api.py`; Test `backend/tests/test_kalshi_odds_api.py`

- [ ] `sport_key_for_series(series)` / `SPORT_KEY_BY_SERIES = {"KXWCGAME": "soccer_fifa_world_cup"}`; `sport_key_for_league(league)` for EPL/La Liga/MLS/etc. (best-effort, None if unknown).
- [ ] `parse_events(payload)` (pure): The-Odds-API v4 list → `[{home, away, commence_time, books: {bookkey: {home, draw, away}}}]`. Reads each event's `home_team`/`away_team` + `bookmakers[].markets[key=h2h].outcomes[{name, price}]` (decimal); maps outcome name==home_team→home, ==away_team→away, =="Draw"→draw. Skips events with no usable 3-way book. Never raises.
- [ ] `quote_for_event(event, *, book_priority=("pinnacle","betfair_ex_eu","williamhill","marathonbet"))` (pure) → `OddsQuote` from the sharpest available book (priority first, else first complete book). None if none complete.
- [ ] `match_event(events, home, away)` (pure): normalized-name match (reuse `normalize_team`), handling flipped home/away. Returns the event dict or None.
- [ ] `fetch_events(sport_key, api_key, *, regions="eu,uk,us", markets="h2h")` (integration, degrade-safe): GET `https://api.the-odds-api.com/v4/sports/{sport_key}/odds?apiKey=…&regions=…&markets=h2h&oddsFormat=decimal`; returns `(parse_events(json), quota_remaining_int_or_None)` from the `x-requests-remaining` header. `([], None)` on any failure.
- [ ] Tests: parse a documented v4 event (Arsenal/Chelsea/Draw) → quote home/away/draw; book priority picks pinnacle; match_event by name + flipped; unknown sport key → None; malformed payload → [].

---

### Task 2: `fair_value.sharp_probs_from_quote`

**Files:** Modify `backend/kalshi/fair_value.py`; Test `backend/tests/test_kalshi_fair_value.py` (extend)

- [ ] `sharp_probs_from_quote(quote, method="power")` → `{"winner": fair_from_odds(quote, method)}` (the `sharp_probs` shape `build_market_probs`/orchestrator expect). None/empty quote → `{}`.
- [ ] Test: a quote → `{"winner": {home, draw, away}}` summing to 1; bad method raises (delegated).

---

### Task 3: EngineConfig + instance_config + runner

**Files:** Modify `backend/kalshi/engine.py`, `backend/kalshi/instance_config.py`, `backend/kalshi/runner.py`; Test `backend/tests/test_kalshi_inplay_config.py` (extend)

- [ ] `EngineConfig`: `odds_api_key: str = ""`, `sharp_weight: float = 0.7`, `devig_method: str = "power"`, `odds_refresh_secs: int = 3600`, `odds_regions: str = "eu,uk,us"`.
- [ ] `normalize_config`: persist `odds_api_key` (str), `sharp_weight` (0..1, default 0.7), `devig_method` (power|shin|proportional, default power), `odds_refresh_secs` (≥300, default 3600), `odds_regions` (str).
- [ ] `runner.py`: pass them into `EngineConfig` (`odds_api_key` from cfg else `os.environ.get("ODDS_API_KEY","")`).
- [ ] Test: round-trip + clamping + env fallback documented.

---

### Task 4: Engine wiring (the actual anchor)

**Files:** Modify `backend/kalshi/engine.py`

- [ ] Before the loop: `odds_cache = {}` (sport_key → {"ts", "events"}). Resolve `sport_keys` from `discovery.DEFAULT_SOCCER_SERIES` via `sport_key_for_series`.
- [ ] In the tick, before the pre-pass: if `config.odds_api_key`, for each sport key refresh `fetch_events` when `now_wall - cached.ts > odds_refresh_secs` (budget-guarded via `should_scan`/`bump_scan_budget`); build `all_events`. Log `sharp odds: fetched E events across {keys} (quota left …)`.
- [ ] In the pre-pass per match: `ev = match_event(all_events, home, away)`; if `ev`, `q = quote_for_event(ev)`, `sharp = sharp_probs_from_quote(q, config.devig_method)`. Recompute `best_edge` from the **fused** sharp-anchored winner probs (`build_market_probs(eg, sharp, {}, w_sharp=config.sharp_weight)`) vs ask — so the gate + diagnostics reflect the real edge. Store `meta["sharp_probs"]=sharp`.
- [ ] Put `meta["sharp_probs"]` on the pregame fixture dict (replacing `{}`); pass `w_sharp=config.sharp_weight` to `plan_and_allocate`. Live monitor: feed sharp as the prematch prob anchor too (use fused winner prob per side when available).
- [ ] Coverage log each tick: `sharp anchor: matched M/N matches; closest edges …`. No key → one-time "no odds key — model-only (set odds_api_key or ODDS_API_KEY to trade on sharp edges)".

---

### Task 5: UI — odds key + sharp weight (web + mobile)

**Files:** Modify `frontend/src/components/kalshi/KalshiCreateInstanceModal.vue`, `mobile/lib/features/kalshi/presentation/kalshi_screen.dart`, `backend/api/main.py` (CreateKalshiInstanceBody)

- [ ] Add `odds_api_key` (password field) + `sharp_weight` (slider 0–100%, default 70) with an InfoTip explaining "anchor fair value to sharp bookmaker odds; bet where Kalshi disagrees". Include in create/edit payloads. Backend body accepts them.

---

### Final: suite + bug sweep + push

- [ ] `pytest tests/test_kalshi_*.py -q` green. Parse-check engine/api.
- [ ] `gitnexus_detect_changes()`.
- [ ] Parallel adversarial bug-sweep of the odds parse/match + engine wiring (leakage, None-safety, budget, degrade paths).
- [ ] Commit + push with footer.
