# Discovery Expansion — Design & Plan

- Date: 2026-05-25
- Status: approved (autonomous implement → bug-sweep → push)
- Scope: `backend/strategies/graph_nexus_analysis.py` (+ tests). Config-default changes plus two localized logic changes.
- Branch: `claude-code-integration`

## Problem

Backtest #266753 (+84%) underperformed baseline #357345 (+266%). Diagnosis (3-agent investigation):
the gap is ~65% a *different basket* (7 big winners — AAOI, VOYG, ICHR, RAMP, CNTA, SLAB, TWST — were
**never discovered**, 0 appearances in #266753's 8,645 decision records) and ~46% a model-driven
sizing/exit leak on shared names. Risk knobs were byte-identical between runs. The missed-winner bucket
is a **discovery-breadth/timing** problem: the monitored universe is hard-capped at
`max_discovered_stocks = 50`, so high-momentum movers that aren't already on the list never get bought.

## Goal

Expand and accelerate discovery so high-momentum movers are **discovered and monitored earlier** (and
therefore eligible to be bought and ridden), from a cold, deterministic start — without depending on
accumulated/stale state. Bounded LLM cost. Safe for live.

## Non-goals

- Model selection / A-B (explicitly out of scope per user).
- The "soft clear / warm-start snapshot" fix (Approach A) — not pursued now.
- A tiered cheap-monitor subsystem — only if cost measurement later proves the simple expansion too expensive.
- Raising `max_positions` (held-count). Fixed capital ⇒ more positions = smaller tickets = worse sizing leak.

## Key finding: raising the cap alone is inert

Tracing discovery → buy (`graph_nexus_analysis.py`), a bigger `max_discovered_stocks` never reaches a buy
unless the downstream chain co-moves:

`llm_overlay_max_stock_candidates` (24) → `pool_a_base`+`pool_b_base` (8+4) → `allocation_max_new_stock_buys`
(4) → `max_stock_buys_per_day` (10). Only the top-24 scored candidates get the LLM overlay verdict that
confirms a buy (`:18119`); the rest are silently dropped. So the expansion must widen the whole funnel.

## Design

### 1. Config default changes (new strategy defaults)

| Knob | Current | New | Why |
|------|---------|-----|-----|
| `max_discovered_stocks` | 50 | **90** | Core: ~1.8× the persistently-monitored set; lowers eviction pressure so quiet names survive to breakout. |
| `momentum_discovery_max_per_day` | 3 | **6** | Promote breakouts into the set faster from cold. (`overflow_budget` follows it — `:10943`.) |
| `momentum_discovery_min_20d_return` | 20.0 | **15.0** | Capture movers earlier in their run. |
| `momentum_discovery_min_60d_return` | 50.0 | **40.0** | Same. Parabolic ceilings (80 / 200) unchanged — still avoid buying tops. |
| `llm_overlay_max_stock_candidates` | 24 | **40** | **Required** so the bigger set actually reaches the overlay/buy verdict. Main (bounded) added LLM cost. |
| `pool_a_base` | 8 | **12** | Widen the buy-candidate consideration pool (not the held count). |
| `pool_b_base` | 4 | **6** | Same. |
| `allocation_max_new_stock_buys` | 4 | **6** | Let rotation move into newly-discovered winners a bit faster. |
| `max_stock_buys_per_day` | 10 | **12** | Same; still bounded by `max_positions`. |
| `momentum_discovery_protect_days` | (new) | **10** | Window during which a freshly momentum-discovered row is shielded from eviction (logic change 2A). |

`max_positions` unchanged.

These are principled ~1.5–1.8× widenings of the full chain, not values tuned to recapture specific tickers.
Update both the registered schema (file-top `INTELLISTOCK_SCHEMA`) and the effective-config / scattered
`config.get(key, DEFAULT)` fallbacks so a missing key cannot silently revert to old behavior. (Trace the
config flow into the discovery functions during implementation and update consistently.)

### 2. Logic changes

**2A. Momentum retention protection.** `_sort_discovered_docs_for_retention` (`:10027`) ranks by
`(watchlist, source_priority, -propagation_score, …)`. A momentum row with low live `propagation_score`
sorts to the bottom and is the first evicted by `_trim_discovered_stock_cap` (`:10072`) or
`_find_discovery_eviction_candidate` (`:10052`) — even while quietly building before a breakout.
Add a **time-bounded** protection: a row with `source == "momentum"` whose `discovered_date` is within
`momentum_discovery_protect_days` of the current `date_key` is treated as protected (sorts high, like a
watchlist match) and is skipped by the eviction/trim helpers. Time-bounded so names that never move age
out and free their slot. Requires threading `date_key` + `protect_days` into the sort/trim/eviction
helpers and their callers.

**2B. Determinism.** `_discover_stocks_from_sector_fill` builds `matched_tickers` in dict/cache iteration
order without a final sort (`~:10728`). A larger sector universe amplifies this nondeterminism. Add a
final `sorted(...)` so selection is order-independent (consistent with `_ordered_discovery_candidates`).

## Tests (TDD)

- `_sort_discovered_docs_for_retention`: a recent (`< protect_days`) momentum row outranks a low-score
  non-momentum row; an **old** momentum row does not.
- `_trim_discovered_stock_cap`: over-cap trim does **not** evict a protected recent momentum row; **does**
  evict an unprotected/old one.
- `_find_discovery_eviction_candidate`: skips protected recent momentum rows.
- `_discover_stocks_from_sector_fill`: matched-ticker order is deterministic regardless of input order.
- Schema defaults: assert the new default values are what the table above specifies (guards regressions).
- Determinism: same config twice ⇒ identical discovered set (extend existing `test_phase_alpha_variance.py`
  coverage if applicable).

Run from repo root: `python3 -m pytest backend/tests/ --ignore=backend/tests/test_intellistock_logger.py
--ignore=backend/tests/test_redact_logger.py`. Expect the documented 21 pre-existing failures (not
regressions); 0 new failures.

## Cost & live impact

Costly LLM stages are cached (sentiment) or hard-capped (LLM overlay). With the overlay cap at 40 the main
added cost is the +16 overlay candidates/bar plus first-time scoring of genuinely new names (sentiment
cache absorbs repeats). These are global strategy defaults — they change live behavior on deploy (more
monitored names, modestly faster rotation, higher overlay LLM spend). The operator controls rollout via
deploy and per-instance config; defaults are reasonable for live.

## Implementation notes (bug-sweep outcomes)

- Logic 2A gained a third helper, `_select_discovered_to_trim`, added during the
  pre-push bug-sweep. `_find_discovery_eviction_candidate` already skipped protected
  momentum rows, but `_trim_discovered_stock_cap` trimmed everything past the cap by
  rank — so a shielded mover could still be evicted if retention bucket 0 ever exceeded
  the cap. The trim now routes through `_select_discovered_to_trim`, which excludes
  protected momentum rows (the pool may transiently exceed the cap rather than evict a
  shielded name). Covered by `test_trim_selection_*`.
- Operator-monitor items (not code defects — validate, then tune if needed):
  - **Book turnover:** `allocation_max_new_stock_buys` 4→6 and `max_stock_buys_per_day`
    10→12 with `max_positions` unchanged means faster rotation against fixed slots, so
    more realized churn in live. Watch turnover / realized-loss rate.
  - **LLM cost/bar:** overlay 24→40 (+67% overlay calls, hard-capped); ~80% more names
    scored, but sentiment is cached so marginal cost ≈ first-time scoring of new names.
  - **Shield crowding:** worst case ~6/day × 10 days ≈ 60 protected momentum rows of the
    90 cap (bounded + self-clearing). If validation shows trend/sector/propagation
    discovery starved, lower `momentum_discovery_protect_days` (10→7) or
    `momentum_discovery_max_per_day`.
  - **Regime caps** (bull/chop/bear/crash via `max_positions_*`) still bound held
    positions; the wider funnel only enlarges the candidate set, so it stays subordinate.

## Validation (post-merge, operator-run)

Operator runs ≥2 date ranges; analyze: total return, whether previously-missed movers now get
**discovered and held**, runtime/bar, and LLM $. Judge the mechanism (early monitoring) and bounded cost,
not a single return number. If cost is prohibitive, revisit a tiered cheap-monitor tier.

## Rollout

Config-default change + localized logic; deterministic; `max_positions` untouched. After deploy the
operator updates instance `main`'s strategy config to the new values (or resets to defaults), since
existing instances retain their stored config.
