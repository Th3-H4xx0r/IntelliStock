# Discovery-Expansion Fix — Cold-Start Funnel Repair (Design Spec)

- Date: 2026-05-26
- Status: Approved (brainstorming complete; pending implementation plan)
- Branch: `claude-code-integration`
- Supersedes the *tuning* of: `docs/superpowers/specs/2026-05-25-discovery-expansion-design.md` (the "Approach B" expansion that backfired in backtest 404780)
- Strategy file: `backend/strategies/graph_nexus_analysis.py`

## 1. Problem

The aggressive discovery-expansion config (run 404780) returned **+152.17%** ($10,652 on $7k) — well below the **+266.26%** baseline (run 357345, $18,638). The gap (−$7,986) decomposes (reconciled to the penny):

| Bucket | $ | Cause |
|---|--:|---|
| Baseline winners 404780 never held | −$5,870 | VOYG/ICHR/RAMP/SLAB/TWST + INTC-capture |
| Under-capture on the 52 shared names | −$5,068 | smaller tickets, shorter holds |
| New names the expansion added | +$2,951 | partial offset |

Root-cause mechanism (from log forensics on 404780 vs 357345):

1. **Momentum floods the funnel with junk ETFs.** `momentum_discovery_max_per_day` was raised to 12, and `_build_momentum_scan_universe` *deliberately* scans commodity/sector/trend ETFs. A documented "P1B overflow budget" lets momentum exceed the discovery cap by `max_per_day` **every bar**. Result: leveraged/inverse/commodity ETFs (SOXS, OILD, BOIL, KOLD, COPZ, CPER, …) that top a 20-day return screen pour into the *equity* pool and bleed money via rotation buys.
2. **Queue saturation starves real winners.** The flood saturates the backfill queue (`headroom=0`), so genuine winners (INTC, SLAB, TWST) are perpetually demoted to "queue-only hold" — bought late via forced `backfill_rotation_buy` at bad prices, or never bought. INTC: baseline rode it $38→$118 (+$2,910) via `winner_add` + `leader_lock` (held 33d); 404780's crowded queue blocked the clean entry, force-bought late, then rotated it out at −4.4% (−$38). One name = −$2,948 of the gap.
3. **Sizing dilution.** Bigger `pool_a/pool_b/overlay/max_buys` spread the fixed ~$7k over more, smaller, shorter-held positions: avg ticket **$1,206 → $838**, avg hold **13.6d → 10.4d**. Under-captured even shared winners.

Two confounds (not addressed here, see Non-Goals): the baseline used Azure `gpt-5.4-mini` + warm-start state, while 404780 used Bedrock `kimi-k2.5` + a cold start.

## 2. Goal & Success Criteria

- **Goal:** a freshly-cleared (cold-start) run on the *current* model (`kimi-k2.5`) builds a winning book by fixing the funnel — **not** by reverting discovery breadth or restoring warm-start state.
- **Pass bar:** beat **404780's +152%** on equal footing (cold start, kimi-k2.5). This is the apples-to-apples yardstick.
- **Stretch:** approach the +266% baseline. Not a hard requirement (it is a warm+Azure artifact, not directly comparable).
- **Secondary, measurable:** discovered universe contains ≪ the ETF count of 404780; ICHR-class momentum winners resurface; INTC/SLAB/TWST get clean early entries (not "queue-only hold"); avg ticket size recovers toward ~$1,200.

## 3. Non-Goals

- **Model work** — model stays `kimi-k2.5`; held constant so the next backtest isolates these fixes. (Explicit user decision.)
- **Warm-start restoration** (Approach A) — out of scope; the whole point is cold-start robustness.
- **Matching +266% exactly** — treated as stretch, not pass/fail.
- **A general offline discovery harness** — considered and rejected as over-engineering for this fix.

## 4. Design Principle

**Decouple *discovery* breadth from *buy* breadth.** Keep discovery wide (cold-start needs a broad candidate set), but concentrate the *buy* funnel so the fixed ~$7k goes into fewer, larger, longer-held positions. The expansion conflated the two; this spec separates them.

## 5. Components

Every change is **config-gated** for attribution + instant rollback. Defaults below are the intended bundle for the validation run; each can be toggled independently.

### Component 1 — De-crowd the funnel

- **1a. Exclude leveraged/inverse/commodity ETFs from momentum *equity* discovery.**
  - Add a curated `_LEVERAGED_INVERSE_ETF_TICKERS` frozenset (e.g. SOXL, SOXS, OILD, OILU, BOIL, KOLD, NRGU, NRGD, GUSH, DRIP, LABU, LABD, UNG, plus the verified 404780 offenders). Compose the momentum-exclusion set as `_LEVERAGED_INVERSE_ETF_TICKERS ∪ _COMMODITY_ETF_TICKERS` (inspect the existing `_COMMODITY_ETF_TICKERS` contents during implementation to avoid gaps/dupes).
  - In `_discover_stocks_from_momentum` (≈`graph_nexus_analysis.py:10978`), filter these out of emitted candidates. Pure sector/broad ETFs remain eligible via the **dedicated, already-capped ETF-allocation path** (`etf_allocation_enabled`, `max_trend_etfs`, `llm_overlay_max_etf_candidates`, `max_etf_buys_per_day`, `max_positions_etf=4`; overlay inputs are already split stock-vs-ETF at ≈`:18195`). We are not killing ETF exposure — we are stopping ETFs from entering the *equity* pool.
  - Gate: `momentum_discovery_exclude_leveraged_etfs` (default `true`).
- **1b. Lower `momentum_discovery_max_per_day` 12 → 6.** Stops momentum from monopolizing the P1B overflow budget and the discovery cap.
- **1c. Protect genuine winners in the backfill queue.** Ensure reserved priority slots (`backfill_queue_reserved_priority_slots`) favor non-ETF, high-raw-score names so INTC/SLAB/TWST-class names get a clean early entry instead of perpetual "queue-only hold." ETFs must not consume reserved priority slots. (Tuning + a small guard; exact mechanism finalized in the plan after reading the queue code.)

### Component 2 — Concentrate sizing (keep discovery wide)

Pull the **buy-side** knobs back from the extra-aggressive prod values; leave `max_discovered_stocks` broad (90–120):

| knob | 404780 (prod) | proposed |
|---|--:|--:|
| pool_a_base | 14 | 10 |
| pool_b_base | 8 | 4 |
| llm_overlay_max_stock_candidates | 48 | 30 |
| max_stock_buys_per_day | 14 | 8 |
| allocation_max_new_stock_buys | 10 | 6 |
| max_discovered_stocks | 120 | **keep broad (90–120)** |

Target: avg ticket back toward ~$1,200, recovering the −$5,068 under-capture.

### Component 3 — Retain & scale winners

- Tune rotation guards so a recently-entered, high-conviction name is not churned on a small drawdown before it can become a winner: raise `rotation_min_hold_days`, and/or shield high-`raw_score` names from `rotation_replace_loss_threshold_pct`. Confirm `winner_add` + `rotation_winner_lock` engage on the resulting cleaner entries.
- **Much of the INTC pattern is fixed for free** once Component 1 yields a clean early entry — so this is light tuning, not a rewrite. Exact knob values finalized in the plan after reading `_rotation_candidate_allowed` (≈`:7524`), `_rotation_winner_lock_active` (≈`:6846`), and the rotation-sell path (≈`:23560–23820`).

### Component 4 — Genuine discovery misses

- **ICHR** (momentum, +23.9% 20d) is expected to resurface *for free* once Component 1 frees momentum budget/cap from ETF junk — **validate, don't build.**
- **VOYG** (a propagation pick the cold graph never surfaced) is the hard residual: investigate cold-start propagation coverage/determinism. **If it requires graph-state or model work, split to a follow-up spec** so this spec stays shippable.

## 6. Validation Flow (cheap → expensive)

- **Gate 1 — unit tests (seconds):** ETF exclusion from momentum; leveraged/inverse classification correctness; deterministic discovery ordering preserved; reserved-slot behavior; rotation/winner-retention guard behavior. TDD: tests written first.
- **Gate 2 — smoke (minutes):** a short-window or discovery-only dry run confirming the discovered universe drops the junk ETFs, surfaces ICHR-class names, and shows reduced cap/queue pressure.
- **Gate 3 — full cold backtest (~7h, operator-run):** redeploy → clear → backtest on kimi-k2.5; decompose vs +152% with the 3-agent method (quant / log-forensics / config-archaeology).

## 7. Operational / Live-Money

- ⚠️ **Strategies doc 179 is shared by `main`, `nexus-live` (real money), and `nexus-testing`.** Before iterating, **decouple `nexus-live` to its own Strategies doc** pinned to a safe config, so backtest-config changes never touch real money. This also resolves the still-open keep/revert/decouple decision.
- **Division of labor:** code/config/tests + smoke analysis are mine; **operator runs redeploy → clear → full backtest**; I analyze the result ID. (SSH to the deployment host is denied to the assistant; the backtest is operator-gated and costs real LLM spend.)
- Code-default config changes here are for *new* instances; the prod `main` config (doc 179) is updated separately by an operator DB write (merge-only, never print `api_key`).

## 8. Testing Strategy

- New tests in `backend/tests/test_nexus_discovery_expansion_fix.py` (TDD, real asserts), covering each component's gate.
- Run backend suite from repo root with `--ignore=backend/tests/test_intellistock_logger.py --ignore=backend/tests/test_redact_logger.py`.
- Baseline is **21 pre-existing failures** (claude_cli ×15 needs a `claude` binary, live_calendar ×2 date-dependent, nexus_v25 profit-take ×1, robinhood ×3). Success = **0 new failures** beyond that baseline.

## 9. Risks & Rollback

- **Bundled run attribution:** if the cold backtest regresses, use the per-component config flags to toggle and isolate (one extra run).
- **VOYG-class propagation** may be model/graph-bound → follow-up spec.
- **Tuning values are starting points**, not gospel; expect one knob-tuning iteration.
- **Rollback:** every change is config-gated; setting the flags to their 404780 values restores prior behavior. No destructive migrations.

## 10. Open Questions (non-blocking)

- Final composition of `_LEVERAGED_INVERSE_ETF_TICKERS` (verify against `_COMMODITY_ETF_TICKERS`).
- Exact reserved-priority-slot mechanism (read queue code in plan phase).
- Exact rotation-guard knob values (read rotation code in plan phase).
