ACTIVE RUN: bt 571147 (2026-01-01..03-01, v2-let-run-core, $6,000, 3600s)
Config: core_min_pct 0.10 (band $1,500 vs $840 clip), bfq_include_momentum_lane=True,
peak_giveback_min_peak_pnl_pct=30 / peak_giveback_exit_drawdown_pct=25,
watchlist_priority_slots=0 (proven inert), momentum_swap_vs_portfolio_enabled=False,
entry_extension_metric=range, momentum_rank_on_60d=True, momentum_scan_cached_bars=True,
max_positions=6, total_spend_cap_target_weight_pct=0.14, residual_sleeve_bear_alloc_pct=0.35.

TRAJECTORY: 25.9% +7.44 | 46.7% +15.50 | 54.3% +8.37  <-- cliff on 2026-01-30, then FLAT.
Peak equity ~$7,050 -> $6,500.

TWO CONFIRMED DEFECTS IN THIS RUN:
(A) PEAK GIVE-BACK EXIT FIRES BUT NEVER EXECUTES. 10 fires on SLV
    ("PEAK GIVE-BACK EXIT: SLV peaked +60.5% and has handed back 28.2%") and there is
    NO `FILL SELL SLV` anywhere. It sets fresh_score=-1 and fresh_reason at
    graph_nexus_analysis.py:20160-20164. Something downstream turns that into a hold.
    Only SLV gate lines present are 4x "Trailing stop SUPPRESSED (trailing_stop_disabled)".
(B) BFQ FUNDS A HIGH-CONV NAME AT A FLAT $100. Log:
    "Backfill queue BUY: SNDK (queued 6 bars, alloc=$100, score=1.700 HIGH-CONV)"
    -> FILL BUY SNDK 0.2427 @ $414.69 = $100.67 = 1.7% of NAV, against a 14% ($840) clip.
    SNDK moved $237 -> $641 (+166%).

PRIOR RUNS: 915207 +9.70% | 542754 bear +11.94% | 383778 OOS bull +4.75% | 427197 +11.66% (stopped 84%)
TARGET: +12% per 2 months (1x). Pull: python3 scripts/pull_backtest_logs.py <id> --filter 'RE' --stdout
Prior findings: docs/investigations/*.md (gap-*, dd-drop, sndk-priority-block, sweep2, hold-check,
ext-still-blocking, _SYNTHESIS). READ THEM. Do not redo.
