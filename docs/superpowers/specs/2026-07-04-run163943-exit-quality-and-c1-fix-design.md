# Run 163943 forensics → exit-quality + C1 completion — design (round 4)

**Date:** 2026-07-04
**Trigger:** A/B "Run B" backtest **163943** (June-2026, $100k, **gpt-5.4-mini**, R3 levers ON) finished **−0.29%**, underwater ~91% of the month, vs baseline **185254** (same window, **Nemotron-3-ultra**, levers OFF) at **+0.73%**. User read it as a large underperformance vs the pre-tweak run and the live account.

## Evidence summary (3 fable agents + solo forensics; full logs pulled via `GET /backtests/{id}/logs`)

- **The comparison is confounded and, at n=1, invalid.** 163943 changed *two* variables vs 185254: the LLM model (Nemotron→gpt-5.4-mini) **and** the levers. Code-memory already records that (a) cross-model backtest comparisons are invalid — the model is the dominant variable, and (b) this strategy's P&L is dominated by *stochastic momentum-winner capture*, so a single run per arm is unreliable (need n≥3). The whole gap (~$1,014) is ~⅓ the size of one name's normal run-to-run swing.
- **Model resolution confirmed (grep `LLM/<role>: provider=/model=`):** 163943 = `openrouter openai/gpt-5.4-mini-MEDIUM` on all decision roles (overlay/overlay_etf/macro/event_maintenance/enhanced_sentiment, ~1009 calls); 185254 = `openrouter nvidia/nemotron-3-ultra-550b-MEDIUM`. `company_article` = `bedrock openai.gpt-oss-120b` in **both**. Clean total swap on the decision roles.
- **The gap is essentially one name — WDC (+$2,973 of the $1,014 gap, 293%).** Baseline entered WDC 6/15 @ $563 with $14.5k → +19% → **+$2,792**; Run B was stuck in the sector-cap queue and only entered 6/19 @ $746 (near the top) with $1.8k → **−$181**. **Ex-WDC, mini's picks beat baseline by ~$1,900** (OUST +2,246, ASYS +1,939, DULL +1,006, AIT +998).
- **Model defect that produced the WDC miss:** gpt-5.4-mini reads news/macro far more bearishly — clamps Direct sentiment to raw=−1.000 on **19% of reads (vs 5%)**, mean raw **+0.54 vs +0.88**, and inflates macro bearish-event scores ~3× → tripped **"Downtrend protection ACTIVE" for 12 consecutive bars (Jun 12–26) that NEVER fired once under Nemotron**. That lockdown force-sold anything <−3%, capped buys to 2/bar, and shaved ~10%/bar off the buy budget — starving+delaying the WDC entry and churning cash through DXCM/ICUI/FORM (−$3.3k of DP-window whipsaw). Mini's LLM trend-maintenance also kept bearish trends alive forever, re-issuing sell_overrides on LGND/PPG/FBIZ/NTLA every bar for 12–19 days while they rallied +34/+7/+6/+13%.
- **The R3 levers are exonerated.** 3 of 5 never touched a trade (rotation graph-gate, anchor-reinforce, slot-min-notional all $0); the conviction-bypass fired on 3 sells (META/DELL/NVDA) for a net **−$243…+$191** wash; `overlay_llm_timeout_sec=420` was never actually applied (`apply_doc179_round3_ab_levers.py` writes only 4 keys). No lever is an inverted/off-by-one bug.
- **Shared strategy pathology in BOTH runs — the real, model-independent leak:** the strategy sells winners too early. Net sell-timing cost **−$4,090 (Run B) vs −$3,275 (baseline)**. UAL was stop-sold in week 2 (Run B 6/08 @105.70 via rotation break-glass; baseline 6/11 @102.73 via fast-loser cut) before a +17–28% month. DELL was cut 6/10 at the **exact monthly bottom** and then rose +13.7%. This is exactly the "−3%..−10% exit dead zone → bottom-tick cut" that R3's C2 lever targeted but did not close.
- **One genuine code defect found (completes R3 C1):** rotation buy-leg pre-validation (`_rotation_incoming_executable`, `graph_nexus_analysis.py:7017`) checks broker gates (price floor, asset class) but **not** the V31 sector-portfolio-cap demotion (`_enforce_sector_portfolio_cap`, :5776, applied at :25102). So a rotation can execute its **sell leg** and then have its **buy leg** demoted → held name sold and not replaced. Same sell-leg-only leak class as run-185254 leak #1, recurring through a new gate. Observed: 06-12 sold DAR $14k → AMD demoted; 06-22 sold winners VIK(+5.5%)/AIT(+6.8%) → SMCI/LRCX demoted; proceeds churned into QCOM/DXCM/ICUI losers. R3's C1 spec listed "sector cap" in scope, but the shipped `get_nexus_buy_block_details` predicate only covers broker gates.

## Decisions (from brainstorm with user)

- **Stay on Nemotron for live.** gpt-5.4-mini is a worse fit for this strategy; its over-bearishness is a documented **non-goal**, not a fix target. (Live alpaca-main already runs Nemotron.)
- **Defer the lever A/B (Track B).** Keep the R3 levers on doc-179 as-is (≈neutral, not harmful); re-test later when Nemotron upstream is healthy and budget allows.
- **Fix the two real, model-independent defects now:** the C1 sector-cap pre-validation hole (Track A) and the winner-cutting exits (Track C).

## Changes

### A — Rotation sell-leg pre-validation: sector-cap dimension (bug fix, completes R3 C1)
Extend the rotation buy-leg pre-validation so a rotation is **blocked before its sell leg** when the incoming buy would be demoted by the V31 sector-portfolio cap.

- New helper `_rotation_incoming_sector_cap_ok(sym, buy_cash, portfolio_emulator, portfolio_total, config, prices, price_history, selling_sym=None) -> (bool, str)`. It reuses the same cap inputs as `_enforce_sector_portfolio_cap` (`max_sector_portfolio_enabled`, `max_sector_portfolio_pct` default 0.40, `_neo4j_stock_sector_cache`) and returns **not-ok** when `held_sector_dollars(after removing selling_sym if same sector) + buy_cash > portfolio_total × cap_pct`.
- **Conservative + fail-open:** returns ok (allow) whenever the cap is disabled, the sector cache/price is unavailable, or inputs are degenerate — it can only ever *newly block* a rotation whose buy provably pushes its sector past the cap. Blocking keeps the held position (no capital at risk; only opportunity cost) in a rare ≥40%-concentration state.
- Wire it into the two rotation lanes that pair a sell with a fund-buy: `:25436` (mw_rotation) and `:25634` (mw_pf portfolio-swap), alongside the existing broker-gate check. The η.G lane (`:5897`) runs *inside* `_enforce_sector_portfolio_cap` and is already cap-aware by construction — left unchanged.
- Gated by `rotation_prevalidate_sector_cap_enabled` (**default True** — this is a correctness fix aligned with "fix live", but a single flag reverts it). Emits a `ROTATION PREVALIDATE sector-cap block` telemetry line when it fires.

### C — Stop cutting would-be-winners on forced-exit paths (new capability, default-preserving)
Give both forced-exit paths a shared "recently-ran-up ⇒ momentum-protect" guard, so a volatile momentum name that dipped after a run-up is not sold at a local bottom.

- New shared helper `_recent_runup_protect(sym, price_history, block_pct, lookback_bars) -> (bool, float)`: True when the recent `lookback_bars` close range ran up more than `block_pct` (i.e. `(hi−lo)/lo×100 > block_pct`). Extracted verbatim from the existing inline V28.7 FLC block (`:17151-17176`), which is refactored to call it (no behavior change).
- **Wire the same guard into the V28.9 losing break-glass sell** (`v28_hc_losing_break_glass`, execution block ~`:24500-24680`): before selling the losing held leg to fund an incoming buy, if `_recent_runup_protect(held_sym, …, rotation_break_glass_recent_runup_block_pct, …)` fires, refuse the break-glass (keep the held position). This is the path that cut UAL in Run B — the FLC guard alone (baseline's path) would not have protected it.
- New lever `rotation_break_glass_recent_runup_block_pct` (**default 0 = off**, behavior-preserving). `fast_loser_cut_recent_runup_block_pct` (already exists, default 0) is unchanged. Both emit a telemetry line when they fire.

**Explicitly NOT changed** (per code-memory: winner-*lock* only shields positive-pnl holds, so it can't save an underwater would-be-winner — fix the cut thresholds, not the lock): `rotation_winner_lock_*`, `fast_loser_cut_pct`, break-glass score/delta gates.

### Documented, not implemented
- **Track B (lever A/B): deferred.** Keep R3 levers on doc-179 as-is.
- **gpt-5.4-mini over-bearishness:** non-goal (staying on Nemotron); recorded for the record. If mini is ever reconsidered for live, `llm_sell_conviction_bypass` must be re-gated — under mini every sell reads raw=−1.000, so the conviction filter (≤−0.5) has zero discriminating power and degenerates to "bypass grace on all sells."
- **`apply_doc179_round3_ab_levers.py` gap:** it writes only 4 keys; `overlay_llm_timeout_sec` is never applied despite the handoff claiming it. Noted; not edited this round.

## Measurement protocol (Track C — required before enabling on doc-179)

Track C ships **default-off**; enabling the runup guards on doc-179 is a real-money change and MUST be validated first:
1. n≥3 backtests per arm — config A (levers-as-is) vs config B (A + `fast_loser_cut_recent_runup_block_pct` and `rotation_break_glass_recent_runup_block_pct` ≈ 25–30, lookback 20) — **model held constant** (mini is acceptable and cheaper; this is an exit mechanism, not model-sensitive), same June window.
2. Adopt only if **median P&L improves AND** the named winner-cut cases (UAL/DELL) are demonstrably no longer bottom-ticked — confirm the guard fired via `V28.7 FLC recent-runup block` / `break-glass recent-runup block` telemetry — **without** creating worse true-loser holds (check realized-loss tail and max drawdown).
3. Spot-check a 2nd window if budget allows. Exit knobs carry documented tuning history — do not tune blind; this protocol is the guardrail.

Track A (deterministic mechanics) needs only unit tests, not the n≥3 protocol.

## Testing
- Unit tests following `tests/test_nexus_allocation.py` patterns: Track A (block a rotation whose buy is over the sector cap; allow same-sector rotation that stays under; fail-open on missing cache/price/flag-off), Track C (`_recent_runup_protect` boundary + break-glass refusal when protected + FLC refactor equivalence + default-off = no behavior change).
- GitNexus `impact` before editing each symbol; `detect_changes` before each commit.
- Full-suite bisect head-vs-base to confirm zero *new* failures (24 pre-existing failures on base: July-4 calendar `broker_session`, `profit_take_v25`, `live_calendar`, rethinkdb-localhost, `BfqWinnerLockBypass::test_disabled_by_default`).

## Risk
Both behaviour-changing pieces default to current live behaviour except Track A's flag (default True) — a conservative, fail-open correctness fix that only ever *blocks* a provably-over-cap rotation (keeps the held position; no capital at risk). Track C is fully default-off; nothing changes until doc-179 config enables it after the n≥3 protocol. The live instance is stopped, so nothing reaches real money until the user restarts after review.
