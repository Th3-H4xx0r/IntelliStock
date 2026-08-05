# Ticker research pipeline — design

**Status:** written autonomously while the operator was asleep, under an explicit
"work fully autonomously" instruction. The brainstorming skill's approval gate was
therefore not honoured; **this document is the thing to review.**

## Problem

Buy decisions are made with no view of whether the business is viable. Measured
over 98 positions the system actually took across 11 runs:

| bucket | n | P&L | hit rate |
|---|---|---|---|
| **no EDGAR filings visible** | 36 | **−$590** | 25% |
| grade F (burns cash + unprofitable) | 16 | −$262 | 12% |
| grade A | 12 | +$42 | 67% |
| **everything (baseline)** | 98 | **−$798 (−$8.15/position)** | 34% |

The single largest destroyer is not bad companies — it is **companies that cannot
be researched at all** (foreign issuers, shells, fresh listings). And the model
cannot help: **89% of its buys carry the maximum raw conviction score**, so there
is no internal ranking to filter on.

## Goal

A pipeline that, for a ticker and an `as_of`, returns a research dossier built
**only from information public at `as_of`**, and a grade the buy path can veto on.

## Non-goals

- Return forecasting. This orders and excludes; it does not predict.
- Concentration. Grinold: a top-3 needs ~2.2× the information coefficient of the
  average pick merely to break even. A good grade is permission, never sizing.
- Beating SPY on its own. The prior ten-agent primary-data sweep found nothing
  retail-tradeable does; this is a cost/quality filter, not an alpha engine.

## The leakage guarantee — the core requirement

Backtests must not see the future. Three rules, enforced structurally:

1. **Every record carries an `available_at`.** A source adapter that cannot
   produce one for a record cannot emit that record. There is no default and no
   heuristic fallback.
2. **The pipeline, not the adapter, does the filtering**, through the repo's
   existing `point_in_time_data.filter_available(context, records, available_at)`.
   An adapter cannot opt out of it.
3. **A source with no verifiable timestamp is BANNED, not approximated.**

### Source classification

| source | as-of key | verdict |
|---|---|---|
| SEC EDGAR XBRL | `filed` | ✅ true point-in-time |
| Alpaca news | article `published_at` | ✅ when filtered |
| Benzinga ratings / insider / gov trades | event date | ✅ when filtered |
| Neo4j `GraphEdgeInterval` | interval bounds | ✅ already interval-scoped |
| price bars | bar timestamp | ✅ |
| **yfinance fundamentals** | **none — RESTATED figures** | ❌ **BANNED** |

yfinance is the trap that matters. It serves statements as they read *today*,
with no filing date, so restatements are invisible and a 2026-04 backtest would
silently use figures published in 2026-08. `factor_profitability`'s own docstring
says its 120-day lag heuristic is "a PROXY and a strictly worse one than the
truth". EDGAR replaces it.

## Approaches considered

**A — extend `company_research` inline.** Simplest, but couples every source to
one function and makes leakage a matter of discipline rather than structure.

**B — adapter registry with a central PIT filter (RECOMMENDED).** Each source is
a small unit: `fetch(symbol, as_of) -> records` plus `available_at(record)`. The
pipeline applies `filter_available` to every adapter's output uniformly. Leakage
becomes structurally impossible rather than reviewed-for, each adapter is testable
alone, and new sources plug in without touching the core.

**C — full snapshot/manifest freezing.** Most rigorous. Rejected: the repo already
attempted it and currently has **zero** PIT manifests (the one written was deleted
because its payloads were empty). Too heavy for the value here.

**Chosen: B.**

## Architecture

```
research_ticker(symbol, as_of)
        │
        ├── PointInTimeContext(as_of)         # from point_in_time_data
        │
        ├── for each registered adapter:
        │      raw = adapter.fetch(symbol, as_of)
        │      visible = filter_available(ctx, raw, adapter.available_at)   # ENFORCED
        │
        ├── merge visible records -> Dossier
        └── grade + red/green flags
```

`ResearchAdapter` protocol: `name`, `enabled`, `fetch()`, `available_at()`.
Registry starts with EDGAR (working today). News/Benzinga adapters are declared
but **disabled by default** — an adapter ships disabled until its timestamp
semantics are verified against live data, because an unverified adapter is a
leakage vector.

## Error handling

Fails **open** everywhere: no data, an adapter raising, a network timeout — all
resolve to "no opinion", never to a veto. A research feed must never be able to
halt trading. The one exception is `fundamental_veto_block_unknown`, where the
operator explicitly opts into treating "cannot research" as a reason to skip —
which the measurement supports (−$590 across 36 positions).

## Testing

The tests that matter are the leakage tests:

1. A record dated after `as_of` is never returned — per adapter and end-to-end.
2. An adapter that emits a record with no `available_at` raises rather than
   silently passing it through.
3. A dossier for `as_of = T` is byte-identical whether computed at T or later —
   the property that makes backtests reproducible.
4. Sign conventions: every factor is "higher is better", so a composite cannot
   silently reward balance-sheet bloat or dilution.

## What this cannot do

It will not produce 2–3×/year. It moves the satellite from −$8.15/position toward
break-even by excluding the unresearchable and the fragile. It also **excludes
some winners** — AAOI, the single biggest gainer in the measured set, grades F on
cash burn. That trade-off is inherent: the screen that removes failures also
removes the pre-profit names multi-baggers come from.
