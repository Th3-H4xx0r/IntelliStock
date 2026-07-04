# Run 185254 leak fixes + A/B measurement — design (round 3)

**Date:** 2026-07-04
**Baseline:** backtest 185254 (June-2026 replay, $100k, Nemotron-3-ultra via OpenRouter, round-2 config): **+0.73%** vs SPY **−1.28%** (beat SPY by ~2pp). Portfolio high $105,304; closed round-trips 1W/5L.
**Goal (user):** maximize absolute return, keep the SPY edge robust, and fix plumbing (failed calls, spend accounting) in the same round. Scope: config + code + model A/B (gpt-5.4-mini).

## Evidence summary

Forensics on the full trade log, decisions JSON, and LLMUsage (agents' reports, code claims verified by hand):

- **Buy-and-holding the day-1 basket would have finished +1.45%.** All trading after day 1 netted **−$725**. The alpha was day-1 selection; the machinery destroyed value.
- **Leak 1 (~$8.1k forgone):** both momentum rotations executed the sell leg, then the broker killed the buy leg on `buy_price_floor: $8` (`nexus_broker_utils.py:232`) — SLBT $3.17, ABSI $7.38. ABSI rose +53.8% after the skip. WDC's $17.3k proceeds idled to month-end. Rotation pairs are never pre-validated against execution gates.
- **Leak 2 (~$3.7k):** LLM sell signals on META/IBM (raw −0.55…−1.15) from day 1-2 were blocked by min-hold-15 + 14-day grace; the only remaining exit was the −10% fast-loser cut, which fired on UAL/ZS at the June-10 close — the window low. UAL rebounded +31.5% after the cut. No exit path exists between −3% and −10%.
- **Leak 3 (~$1.8k):** `max_positions=8` was binding in all 23 cycles; when WDC's sale freed a slot, `_bfq_direct_alloc = max(min_pos, min(stock_budget*0.15, min_pos*2))` (`graph_nexus_analysis.py:~25970`) bought **$200 of MU** and re-locked the book while $49.8k cash idled and score-2.2 candidates were starved.
- **Leak 4 (~$1.1–2.9k):** ROBN round-tripped +24% → −8.6%; the trailing stop only evaluates while `unrealized_pct >= activation` (`graph_nexus_analysis.py:~17155`), so collapsing through activation disarmed it.
- **Leak 5:** `anchor_reinforce_enabled` (default True, `graph_nexus_analysis.py:8215`) shadows the `winner_add_*` levers (path requires `not anchor_reinforce_enabled`, line 24793) and tops winners up only to 1.3× *entry* notional → PANW (+21%, best name) got one $835 add on the second-to-last bar.
- **Sizing:** conviction tiers are mcap-driven, so day-1 sizing degenerated to equal-weight $9,428 clips; three of the four max-clip names were the worst losers; ML term inert (0.50/0.50 on all 2,762 decisions).
- **Rotation graph gate:** sticky-positive graph scores (DNOW +0.70…+1.25 while bleeding to −$788) meant the positive-graph gate froze rotation all month (fired 0/4 in every cycle).
- **29 failed LLM calls (23×429, 6×502/503, 1 parse):** zero P&L impact, but `_try_raw_structured_json_once` hardcodes `retries=0` (`llm_utils.py:~1680`) so the existing exponential-backoff + Retry-After loop in `_call_openrouter` is unreachable (`retry_count=0` on all 1,062 calls); the 10-worker overlay pool bursts ~3.5 req/s with no OpenRouter rate limiter; terminal overlay failures are not logged to the engine log.
- **Spend gap ($5.06 real vs $3.67 attributed):** $0.19 = user's own aborted false-start (run 209469, hidden in UI); ~$0.33 = `compute_cost` has no `reasoning_tokens` price key (macro/sentiment `models_override` rows drop reasoning cost); remainder = billed-but-unrecorded (TTL-pruned LLMUsage, HTTP-200-but-unparsed recorded $0, boot calls). OpenRouter `/credits` reconciles the $14.94 balance exactly.

## Changes

### C1 — Rotation buy-leg pre-validation (top leak; bug fix)
Before executing a rotation's sell leg, validate the incoming candidate against the same broker execution gates the buy will face (price floor, min notional, sector cap). If blocked, fall through to the next-ranked rotation candidate; if none passes, skip the rotation and keep the held position. Extract the gate predicate from `nexus_broker_utils.py` into a shared helper used by both the broker and the rotation/conditional-swap paths in `graph_nexus_analysis.py`. Unconditional (bug fix, applies live too).

### C2 — Mid-band conviction loser exit (new levers)
When the LLM sell signal is strong and the position is already losing, bypass min-hold/grace:
- `llm_sell_conviction_bypass_enabled` (default **false** — live behavior unchanged)
- `llm_sell_conviction_min_raw` (default −0.5)
- `llm_sell_conviction_min_loss_pct` (default 3.0)
Backtest config enables it. Fills the −3%…−10% dead zone that forced bottom-tick cuts.

### C3 — Slot-crumb fix (bug fix + lever)
- Re-size direct-reserved allocations at drain time from the *current* budget: drop the `min_pos*2` cap → `max(min_pos, stock_budget_after_adds * direct_reserve_alloc_pct)` with new lever `direct_reserve_alloc_pct` (default 0.15).
- Min-notional slot guard: a new-position buy consuming a slot must be ≥ `slot_min_notional_pct` of portfolio (default 0, i.e. off; backtest sets 1.5%). ETF-sleeve and add-on buys exempt.

### C4 — Trailing-stop ratchet (bug fix)
Persist an "armed" flag in strategy_cache when `unrealized_pct >= activation` first becomes true; once armed, the peak-drop check keeps evaluating even after the gain collapses below activation. Peak state is already persisted — this adds only the armed bit. Unconditional.

### C5 — Winner-room unshadow
Change anchor-reinforce's top-up target from 1.3× entry notional to a target position size: `anchor_reinforce_target_pct` of portfolio (default preserves current behavior via entry-notional fallback when unset; backtest sets ~10-12%). Anchor gates (7d AND +15%) unchanged. This makes adds scale with the winner instead of shrinking as it runs.

### C6 — Rotation graph-gate loss override (small lever)
`rotation_graph_gate_max_loss_pct` (default off): the positive-graph gate is ignored for a held name whose P&L ≤ −X% (backtest sets 5) — sticky-positive graph scores stop freezing slow bleeders like DNOW.

### C7 — LLM reliability plumbing (bug fixes)
- Thread the caller's `retries` through `_try_raw_structured_json_once` → `call_llm_by_provider` so `_call_openrouter`'s existing backoff + `Retry-After` handling actually runs.
- Add an OpenRouter per-model request rate limiter entry (pace overlay workers to ~2-3 req/s) — the limiter table currently only covers NVIDIA-NIM kimi.
- Log terminal overlay/overlay_etf failures to the engine log (currently invisible outside LLMUsage).

### C8 — Cost accounting (bug fixes)
- Add `reasoning_tokens` pricing to `compute_cost` (`llm_telemetry.py`): bill at output rate, overridable via `reasoning_cost_per_1m`.
- Classification/native-structured path: request OpenRouter's `usage:{include:true}` envelope and pass the billed cost through (become `cost_source=envelope`).
- Record provider-billed cost for HTTP-200-but-unparsed responses (currently $0).
- Deferred (backlog, not this round): UI roll-up of non-backtest/aborted-run spend; durable daily cost table instead of TTL-pruned LLMUsage; strategy-editor mask-writeback bug (separate ticket, already known).

### Explicit non-goals
- No change to live doc-179 model/provider; A/B is backtest-only.
- No conviction-sizing overhaul this round (entry scores had no predictive value in this run — exits/rotation were the loss driver). Revisit after A/B shows which model produces differentiated scores.
- No live-instance restart decision.

## Measurement protocol

1. **Run A** — fixed code, same round-2 config + Nemotron, same June window. Isolates mechanical gains. Success: ≥ +2% (leak math supports +3-5% directional), no new failure modes.
2. **Run B** — fixed code, same config, **gpt-5.4-mini**. Isolates model delta on entry quality and JSON reliability.
3. Compare on: P&L% (vs SPY −1.28%), closed win rate, avg idle cash %, rotations completed vs blocked, mid-band exits taken, sub-$1k slot buys, LLM fail count + `raw_json_fallback` rate, LLM cost.
4. Constraints: runs execute on the prod backtest engine, **one at a time** (concurrent graph builds starve the engine). Budget ≈ $4 (Nemotron) + $2-4 (mini) of the $14.94 OpenRouter balance.

## Testing
- Unit tests per change following `tests/test_nexus_allocation.py` patterns (rotation pre-validation, crumb re-size, ratchet arming, mid-band bypass, reasoning-token pricing).
- GitNexus impact analysis before editing each symbol; `gitnexus_detect_changes` before each commit.
- 1-week smoke backtest before the full June runs.

## Risk
All behavior-changing levers default to current live behavior; the unconditional changes (C1, C4, C7, C8) are correctness fixes. The live instance is stopped, so nothing ships to real money until the user restarts it after reviewing A/B results.
