# "Why it moved" AI analysis: design

Date: 2026-11-14. Branch: `feat/why-it-moved`.

## Intent

The operator watches the account daily and keeps asking why a holding moved.
This feature answers that inside the product: for any symbol it splits each
day's move into market, sector and stock-specific parts, flags the days that
stand out, and has an LLM name the cause from dated news it must cite. A new
"AI Analysis" screen on web and iOS shows it, and Settings picks which
registered model does the explaining.

It is an explanation tool. It is not a signal and nothing in the order path
reads it. Every explained event is stored with the publish times of the news
it cites, so the table can feed one pre-registered event study later.

## Goals

- Per-symbol view: price chart with event markers, a daily-move strip, and an
  event list with category, one-line cause, the three-way split and sources.
- Per-account view: the last session's move per holding, each split three
  ways, with the explanation where one exists.
- The operator chooses the model from the existing Models registry.
- Honest output: an event with no supporting article says "No catalyst found".

## Non-goals

- No scheduled job and no historical backfill in this version. Analysis runs
  when the operator opens a symbol and asks for explanations.
- No trading use. No change to broker.py, strategies or instances.
- No free web search by the model. It sees only the articles we hand it.

## Anti-goals

- An invented cause. The model may only cite article ids it was given; an
  answer citing none is stored as `no_catalyst`.
- Calling a market-wide day a stock story. A flagged day whose stock-specific
  part is under half the move is labelled `market` with no LLM call.

## Backend

New package `backend/move_analysis/`:

| Module | Job |
|---|---|
| `decompose.py` | Pure maths. Daily returns, rolling beta to SPY, sector part, stock-specific part, flagging. No I/O. |
| `prices.py` | Daily closes and volume for a symbol, SPY and a sector ETF (yfinance, injectable). |
| `sectors.py` | Symbol to sector to SPDR sector ETF. |
| `news.py` | Articles for one symbol between the previous close and the event close. Alpaca news first, Google News fallback. Injectable. |
| `explain.py` | Builds the prompt, calls the chosen model for structured output, validates citations, returns an event. |
| `service.py` | Settings, event storage, background explain worker, payload assembly. |

### The split

For day *t* with return `r`, SPY return `m` and sector ETF return `s`, using
the 120 trading days before *t* (at least 30, else beta 1 and no sector part):

- `beta` = cov(r, m) / var(m). `market = beta * m`.
- sector excess `x = s - beta_s * m`, where `beta_s` is the ETF's own beta.
- `gamma` = cov(r - beta*m, x) / var(x). `sector = gamma * x`.
- `specific = r - market - sector`.

A day is flagged when either holds:

- `|specific| >= max(sigma * sd(specific), min_move_pct)`, where `sd` is the
  standard deviation of `specific` over the same lookback; or
- `|r| >= 2 * min_move_pct`.

Defaults: `sigma` 2.5, `min_move_pct` 3.0. A flagged day with
`|specific| < 0.5 * |r|` gets the `market` label without an LLM call.

### Categories

`earnings`, `guidance`, `analyst`, `deal_product`, `macro`, `sector_peer`,
`regulatory_legal`, `management`, `capital_insider` (offerings, buybacks,
insider sales, lock-ups), `market` (set without the LLM), `no_catalyst`.

### Storage

Two tables registered in `backend/db/schema.py`:

- `MoveEvents`, id `SYMBOL|YYYY-MM-DD`, prefix-indexed on id. One row per
  explained, pending or failed event.
- `AppSettings`, id `move_analysis`. Global, because the app has one operator
  and no roles.

### Explain worker

`POST /analysis/moves/explain` writes `pending` rows and starts one daemon
thread that works through them in order. A `pending` row older than 10
minutes is treated as abandoned and may be re-queued. Failures are stored as
`failed` with a short error; `force` re-runs an explained event.

### API (all authenticated)

`GET /analysis/settings`

```json
{"model_id": "abc", "model_name": "Opus", "sigma": 2.5, "min_move_pct": 3.0,
 "models": [{"id": "abc", "name": "Opus", "provider": "claude-cli", "model": "claude-opus-5-5"}]}
```

`PUT /analysis/settings` with any of `model_id` (string or null), `sigma`
(1.0 to 6.0), `min_move_pct` (0.5 to 25). Returns the same shape. Unknown
`model_id` is 400.

`GET /analysis/moves?symbol=NVDA&range=3M` (range `1M`, `3M`, `6M`, `1Y`)

```json
{"symbol": "NVDA", "range": "3M", "sector": "technology", "sector_etf": "XLK",
 "beta": 1.62, "model_configured": true, "pending": 0,
 "bars": [{"date": "2026-11-13", "close": 201.4, "volume": 190000000,
           "ret_pct": -4.1, "market_pct": -1.2, "sector_pct": -0.9,
           "specific_pct": -2.0, "flagged": false}],
 "events": [{"id": "NVDA|2026-10-02", "symbol": "NVDA", "date": "2026-10-02",
             "close": 188.1, "ret_pct": -7.9, "market_pct": -0.8,
             "sector_pct": -1.1, "specific_pct": -6.0,
             "status": "explained", "category": "analyst",
             "category_label": "Analysts", "headline": "Downgrade on export limits",
             "explanation": "…", "confidence": "medium",
             "sources": [{"title": "…", "url": "https://…", "source": "Benzinga",
                          "published_at": "2026-10-02T12:31:00Z"}],
             "model_name": "Opus", "explained_at": "2026-11-14T20:00:00Z",
             "error": null}],
 "categories": [{"key": "earnings", "label": "Earnings"}]}
```

`bars` run oldest first; the first bar of a series may have null parts.
`events` holds every flagged day in the range, newest first. `status` is one
of `unexplained`, `pending`, `explained`, `failed`. For `unexplained` and
`pending` the `category`, `category_label`, `headline`, `explanation`,
`confidence`, `model_name` and `explained_at` are null and `sources` is
empty. `confidence` is `low`, `medium` or `high`. `sector`, `sector_etf` and
`beta` may be null. An unknown symbol or one with no price history is 404.

`POST /analysis/moves/explain` with `{"symbol": "NVDA", "range": "3M",
"dates": ["2026-10-02"], "force": false}`. `dates` omitted means every
unexplained or failed event in the range. Returns `{"queued": 3}`. 400 with a
readable message when no model is chosen.

`GET /analysis/portfolio?brokerage_id=…`

```json
{"as_of": "2026-11-13", "spy_pct": -0.7,
 "totals": {"contribution_pct": -1.31, "market_pct": -0.82,
            "sector_pct": -0.2, "specific_pct": -0.29},
 "holdings": [{"symbol": "TQQQ", "weight_pct": 41.2, "ret_pct": -2.1,
               "contribution_pct": -0.87, "market_pct": -0.6,
               "sector_pct": 0.0, "specific_pct": -0.27, "flagged": false,
               "event": null}]}
```

The three `*_pct` parts of a holding are contributions to the account (the
holding's part times its weight), so they add up to `contribution_pct`.
`event` is the event object (same shape as above) for that symbol and day
when the day is flagged, else null. Holdings are sorted by absolute
contribution, largest first. `as_of` is null and `holdings` empty when the
account has no positions. Only equity positions are included.

## Screens

Both clients add one screen, "AI Analysis", with the same content:

1. Symbol picker: current holdings as quick picks, plus symbol search.
2. Range switch (1M, 3M, 6M, 1Y) and a category filter.
3. Price chart with a marker on every event, coloured by category; hollow
   when unexplained.
4. Daily-move strip under the chart, flagged days emphasised.
5. Event list. Selecting an event shows headline, explanation, the three-way
   split, confidence and tappable sources.
6. "Explain moves" action, which polls while events are pending.
7. "Today in your account" section from `/analysis/portfolio`.
8. An empty state that links to Settings when no model is chosen.

Settings on both clients gains an "AI Analysis" section: model picker from
`models`, plus the two thresholds.

- Web: new route `/analysis`, new nav entry, in the existing Vue stack and
  chart library.
- iOS: new screen under the More tab and a link from the Stock page, in the
  existing design system (no gradients, Apple Stocks style, no third-party
  dependencies).

## Left to the builder

Chart marker shapes, exact layout, polling interval (2 to 5 seconds), copy.

## Tests

- `decompose.py`: parts sum to the return; known-beta synthetic series;
  short history; flagging thresholds.
- `explain.py`: uncited answer becomes `no_catalyst`; invented article id is
  dropped; `market` label skips the model.
- `service.py` and endpoints against `FakeStore`: settings validation,
  queueing, stale-pending recovery, portfolio parts sum to the contribution.
- Web build passes; iOS unit tests for decoding and the view model.
