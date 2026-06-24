# Kalshi Live In-Match Monitoring + Runtime Crash Alerts — Design

**Date:** 2026-06-23
**Branch:** feat/kalshi-prediction-markets
**Status:** Approved (brainstorm) → implementation

## Goal

While a discovered Kalshi soccer match is **in-play**, the engine watches the live
market, detects material events from price action, re-reads commentary/news via the
LLM on those events, recomputes a **hybrid** live fair value, and takes **full
two-way** action (open / add / reduce / exit) under in-play risk caps. Separately,
if the Kalshi runtime **crashes**, the operator is notified (Discord + iOS push) via
a new configurable notification category.

## Approved decisions (brainstorm)

- **In-play actions:** full two-way — open new, add, reduce, exit.
- **Live fair value:** hybrid = market de-vig (anchor) + time-decayed pre-match model + bounded LLM tilt.
- **Live data feed:** **Kalshi price only** — no external API. The live market price IS the live-state sensor; material events are inferred from price moves.
- **LLM in live loop:** on material events (or every ~15 min while live) — not every poll.

## Key resolution: "Kalshi price only" + hybrid

With no external feed we do **not** know the live score or minute directly, so a
true score-conditioned Dixon-Coles is impossible. Resolution:

- The **live Kalshi mid-price** (de-vig) is the anchor — it has the score baked in.
- **Material events** = a winner side's mid jumping beyond a tier-scaled threshold
  between polls (≈ a goal / red card). This is the primary in-play trigger and is
  independent of the clock.
- The **model** component is a **time-decayed pre-match prior** that fades toward the
  market as the (best-effort) clock advances — not score-conditioned.
- A **best-effort match clock** (from Kalshi market timestamps / ticker date) gives
  phase (pregame/live/ended/unknown) and elapsed minute. When the clock is
  *unknown*, reduce/exit is still allowed (defensive) but open/add requires a
  detected live price move (so we never trade pregame as if live).
- Seam left for a future ESPN feed to supply true score/minute (one module swap).

## Architecture — `kalshi/live/` (pure core + thin integration)

- **`match_clock.py`** (pure): `match_phase(kickoff_ts, now_ts, regulation_min=115)` →
  `(phase, elapsed_min)`; `kickoff_from_market(market)` best-effort kickoff epoch.
- **`event_detect.py`** (pure): `detect_move(history, threshold)` → material-move flag
  + direction from a rolling mid-price list.
- **`live_fair.py`** (pure): `live_fair(market_prob, prematch_prob, elapsed_min, llm_tilt,
  *, w_market, regulation_min, tilt_cap)` → blended, clamped [0,1]. Market-weighted
  in-play; model weight decays to ~0 by full time.
- **`live_decision.py`** (pure): `decide(position, live_fair, yes_ask_cents, yes_bid_cents,
  tier, caps, phase, elapsed_min, move)` → `LiveAction{kind, contracts, reason}` where
  kind ∈ {open, add, reduce, exit, hold}. In-play analog of the planner.
- **`monitor.py`** (integration, thin): per in-play match — poll prices, update history,
  detect events, (on event / cadence) call LLM for commentary, compute live fair,
  decide, execute (gated by `should_execute`), write a LIVE-tagged decision row.

## In-play risk caps (tier-scaled)

`InPlayCaps`: `inplay_exposure_frac` (cap of bankroll deployed in-play per match),
`max_adds_per_match`, `no_add_after_min` (e.g. 80'), `stop_loss_frac` (exit if a
position's mark falls this far below entry). Tier scales aggressiveness
(low→exit-biased, max→freely open/add). All execution stays behind the existing
demo/live gate (`should_execute`): demo always; live only if `live_enabled`.

## Loop integration

- Pre-match planning runs as today (now cheap — news/LLM cached). Matches are split
  by clock phase: **pregame** → pre-match planner (opens pre-match positions);
  **live** → live monitor.
- `analyst_cache` TTL becomes **wall-clock** (30 min) so it's correct under variable
  cadence. A new `price_history` map (per market) persists across ticks.
- Adaptive cadence: sleep `live_poll_seconds` (~30s) when any match is live and
  `live_monitoring` is on, else `poll_seconds` (60s).
- Live decisions persist to `kalshi_decisions` with `in_play=True` + detected event +
  minute, surfacing in the decision log tagged **LIVE**.

## Config + UI

- `EngineConfig`: `live_monitoring: bool=True`, `live_poll_seconds: int=30`, `InPlayCaps`.
  `instance_config.normalize_config` stores + validates them; `risk_caps_from_config`
  builds `InPlayCaps`.
- UI: a "Live in-match trading" toggle in the create/edit modal (web) and the create
  sheet (mobile). Persisted in `kalshi_config`.

## Runtime crash alerts

- New notification category in `notification_types.py`:
  `{"key": "kalshi_runtime", "group": "Kalshi", "label": "Kalshi runtime crashed",
    "desc": "The Kalshi instance runtime crashed unexpectedly", "channel": "notifications",
    "discord": True, "push": True, "prefixes": ["KALSHI RUNTIME ["]}`.
  It auto-appears in the web + mobile notification-settings UIs (they render from the
  API `types` array) and is routable (Discord + iOS push). Default push **on**.
- `kalshi/runner.py`: wrap `run_instance(...)` in a crash guard. On an uncaught
  exception, call `notifications.notify(category="kalshi_runtime", instance_id=...,
  title="KALSHI RUNTIME [<id>] crashed", body=<exc + short traceback>,
  discord_channel="notifications", push_body=...)`, then re-raise so the supervisor
  still sees the failure. The send is best-effort (never masks the original error).

## Testing (pure-core TDD)

`match_clock`, `event_detect`, `live_fair`, `live_decision` are pure + unit-tested.
`monitor.py` and the runner crash guard are thin and tested with fakes (fake client,
fake notify). No money logic on the real-money path is untested.

## Out of scope

- True live score/minute (needs an external feed) — seam left.
- Player-prop in-play markets (winner/double-chance/O-U only in-play for v1).
