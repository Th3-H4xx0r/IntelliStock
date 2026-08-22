# graph_nexus_analysis.py code audit — what 34k lines actually contain

2026-08-22. Requested premise: "34k lines is unnecessary, must be lots of dead code."
Method: mechanical pass (AST symbol cross-reference repo-wide incl. tests/dart/ts, vulture,
normalized-AST duplicate detection, 12-line block hashing) + two independent Opus semantic
sweeps (lines 1–17k, 17k–34k) hunting superseded version paths, write-only state, semantic
duplication, unreachable branches. Every deletion re-verified by repo-wide word-boundary grep.

## The honest headline
**The file is dense, not dead.** Of 34,011 lines: ~25.6k code, ~6.5k comments/docstrings
(deliberate — this repo documents defect history inline), 1.9k blank. Truly dead: **88 lines**
(deleted this session, file now 33,919). Zero duplicate functions, zero unreachable statements,
zero constant-condition branches, zero shadowed elifs, zero write-only strategy-cache keys in
the first half. The size is real strategy surface, not rot.

**GitNexus is blind to this file.** Every other strategy file is indexed (earnings.py 342
symbols); graph_nexus_analysis.py has ZERO symbols in the graph — it silently exceeds the
indexer's capacity. Impact analysis on its internals is impossible via the graph; repo-wide
grep is the verification tool of record here. (Worth an upstream ticket: a silent per-file
indexing failure looks exactly like "no callers".)

## Deleted (88 lines + 5 import names) — each verified sole-definition/zero-use repo-wide
`_extract_sentiment_json` (56), `_AliasDisambiguationResponse` (9, superseded by
`_PrivateEntityNewsResolutionResponse`), `_SearchEvidenceSelection` (7, orphaned copy of the
live one in engines/nexus_graph_engine.py), `_log_lookback_banner` (7), `_chunked` (3),
`SECONDS_PER_DAY` (1, ml_news.py has its own), unused imports `call_llm_by_provider`/
`call_structured_llm_by_provider` (both try/except branches) and `as_completed`.
Vulture false positives NOT touched: `attrs` (required HTMLParser callback arg), `llm_traces`
(function parameter).

**Lesson caught by the suite:** `call_llm_by_provider`/`call_structured_llm_by_provider` looked
unused (zero call sites in the file) but are **mock.patch targets** in
test_nexus_v9_preflight — an import can be test contract even with no caller. Removing them
broke 2 tests; restored. "Unused import" requires a patch-target grep
(`mock.patch.*<name>` / `patch.object.*<name>`) before deletion in this repo.

## FIVE REAL DEFECTS the audit flushed out (each worth more than the deletions)
1. **`buy_price_floor` divergence:** 5.0 at seven read sites and in INTELLISTOCK_SCHEMA;
   **8.0 at :31913** (`_mw_ba_buy_price_floor`, momentum/breakout-add lane). One lane applies
   a different price floor than every other lane, silently.
2. **A4 post-sell re-entries are FULL SIZE.** `post_sell_reentry_size_fraction` (default 0.50)
   is written into scores (`_reentry_size_fraction`) and **nothing reads it** — sizing never
   consults it. Real-money behavioral gap, not dead code.
3. **`overlay_price_history_bars` is inert.** Production docs set it to 20; the code branches
   on `price_history` shapes (`{"bars":...}` / list) that `_fetch_price_history_for_overlay`
   never produces. Both branches unreachable; the lever does nothing.
4. **Post-sell-watch TTL never runs in live mode.** `_mark_discovered_stock_forgotten` /
   `_mark_discovered_stock_re_entered` have no live caller (backtest path is wired at :28849);
   live DB rows accumulate as permanent `post_sell_watch` state, and `run_once` never sets the
   re-entry flag its own docstring promises.
5. **The trade overlay pays for LLM output nothing reads.** The prompt explicitly requests
   `feature_boosts`/`feature_penalties`/`rationale` (≤3 items each, 500-char rationale) on
   EVERY overlay call; consumers read only decision_bias/blocks/deltas/confidence/reasons.
   Paid output tokens per call, discarded. (Fix = prompt change → changes prompt hash →
   invalidates sentiment-cache scope; must ship deliberately, not casually.)

## Duplication worth refactoring (report-only, ~400 lines, behavior-risk so not auto-applied)
- Company vs macro article LLM classifiers: ~230 lines of identical orchestration (59–63%
  line-identical); extract and parameterize on (prompt, response_model, cache_kind).
- **Three market-cap resolvers with different cache precedence** — the $2B momentum gate and
  the conviction tier can disagree on the same symbol/bar. One resolver with explicit source
  order per call site.
- Two `available_at` extractors feeding the same PIT filter; the narrow one drops the exact
  field benzinga_client documents. Merge.
- Batch-vs-single pydantic response classes (inherit instead of copy), triplicated
  monitor-dispatch boilerplate, repeated Neo4j edge-dedup scaffold, 3 hand-inlined copies of
  `_ny_date_str`'s logic while the tested helper sits orphaned.

## Kept deliberately
- **TEST-ONLY set (~148 lines)**: 6 symbols referenced solely by tests (migration survivors).
  Deleting them means deleting their tests — operator's call, not an autonomous one.
- **Default-OFF levers**: `_breakout_opportunity_audit` (+5 similar flags settable from
  nowhere) — repo convention says default-OFF ships deliberately.
- **Write-only telemetry/DB fields** (~20 keys, incl. `final_action`, `graph_snapshot_date`,
  `alpha_return` — which is NOT alpha, it duplicates `latest_return`): pure sinks, but they
  ride persisted docs that ad-hoc ReQL/self-learning queries could read. Strip them alongside
  the pending alpaca-main field-strip, not independently. They are storage-bloat relevant
  (the 18GB GraphNexusTradeContexts precedent).
- `_get_effective_nexus_config`: 86 of 158 keys computed and dropped by its only consumer —
  but they double as ~30 test assertions and a second source of defaults (which is HOW the
  buy_price_floor divergence was caught). Fix the divergence first, then slim.

## Suite
Full run after deletion: compare against the 19-failure baseline SET (result recorded in the
commit).
