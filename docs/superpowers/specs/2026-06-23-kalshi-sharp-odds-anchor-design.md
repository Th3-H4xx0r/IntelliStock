# Kalshi Sharp-Odds Anchor — Design

**Date:** 2026-06-23
**Branch:** feat/kalshi-prediction-markets
**Status:** Approved (direction chosen) → implementation

## Goal

Make the bot find **real, profitable edges** by anchoring fair value to the
**de-vig'd sharp bookmaker consensus** instead of the standalone Elo model. The
edge becomes *sharp fair value vs the Kalshi price* — i.e. bet where Kalshi
disagrees with the books. This is closing-line-value betting and is the legit way
to actually win, versus the model-only path which just agrees with efficient
prices and never trades.

## Why this works (and the existing gap)

The de-vig + fusion pipeline already exists and is unit-tested:
`fair_from_odds(OddsQuote, "power")` → `{home,draw,away}` →
`build_market_probs(eg, sharp_probs, …, w_sharp=0.7)` fuses sharp (anchor) + model
+ bounded LLM → `compute_edge(fair, yes_ask, fee)`. The ONLY gap: the engine passes
`sharp_probs={}`, so fair value is 100% model. We wire real odds into `sharp_probs`.

## Architecture

- **`kalshi/data/sources/odds_api.py`** (new): the odds source.
  - `parse_event_odds(event, *, book_priority)` (pure): one The-Odds-API v4 event →
    `OddsQuote` (decimal home/draw/away from the best available bookmaker, sharpest
    first). None if no usable 3-way book.
  - `match_event(events, home, away)` (pure): find the event whose normalized team
    names match a Kalshi fixture (handles flipped home/away).
  - `sport_key_for_league(league)` (pure): map our league label → The-Odds-API sport
    key (e.g. "World Cup" → "soccer_fifa_world_cup", "EPL" → "soccer_epl").
  - `fetch_odds(sport_key, api_key, *, regions, markets)` (integration, degrade-safe):
    GET the-odds-api scoreboard of events+bookmaker odds. Returns `(events, quota)`.
- **`kalshi/fair_value.sharp_probs_from_quote(quote, method)`** (new, pure): wrap
  `fair_from_odds` into the `sharp_probs` shape the orchestrator wants:
  `{"winner": {home, draw, away}}`.
- **Engine wiring** (`run_instance`): once per tick (cached ~1h, budget-guarded),
  fetch odds for the instance's configured sport keys; per match, `match_event` →
  `sharp_probs_from_quote` → put on the fixture's `sharp_probs`. Log coverage
  ("sharp odds: N/M matches matched"). No match → `{}` (model-only, as today).
- **Best-edge pre-pass** already computes the analyst gate; with sharp anchoring the
  edges become real, so the gate + planner start producing trades on covered matches.

## Config + secrets

- `odds_api_key`: read from instance `kalshi_config.odds_api_key` first, else env
  `ODDS_API_KEY`. (Optional; absent → sharp anchoring off, model-only.)
- `sharp_weight` (default 0.7), `devig_method` (default "power"),
  `odds_refresh_secs` (default 3600), `odds_regions` (default "eu,uk,us").
- Budget: reuse `ingest_odds.should_scan/bump_scan_budget` + the_odds_api quota
  header so we never blow a free-tier monthly cap; `kalshi_budget_low` notification
  fires when low (category already exists).
- UI: an "Odds API key (sharp anchor)" field + sharp-weight on the create/edit
  modal (web + mobile). Empty = model-only.

## Fixture matching

Team names differ across the books / Kalshi. Reuse `normalize_team` +
`national_elo` aliases; `match_event` compares normalized names with the same
containment + flip logic as the live scoreboard matcher.

## Leakage / safety

- Odds are live pre-match signals; we only ever read the CURRENT odds for pricing
  the CURRENT open market — no historical backfill, no look-ahead.
- Sharp anchoring only changes FAIR VALUE; execution stays behind the existing
  demo/live `should_execute` gate. The fusion clamps + ME-group renormalization are
  unchanged and already tested.

## Honest limitation

The synthetic KXWCGAME demo fixtures are not real matches, so no bookmaker covers
them → it stays model-only there (logged clearly). Real edges appear on real Kalshi
leagues (EPL, La Liga, MLS, …) that bookmakers price. The wiring is correct and
degrade-safe regardless.

## Testing (pure-core TDD)

`parse_event_odds`, `match_event`, `sport_key_for_league`, `sharp_probs_from_quote`
are pure + unit-tested against the documented The-Odds-API v4 shape. The fetch +
engine wiring are thin and integration-tested with a fake events list. Existing
de-vig/fusion/edge tests already cover the math downstream of `sharp_probs`.

## Out of scope

- Player-prop sharp lines (books rarely price exact-score/scorer cheaply).
- Multi-book line-shopping beyond a sharp-first priority list.
