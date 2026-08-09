CURRENT RUNS (all cold-state, config below, instance v2-let-run-core, $6,000, 3600s)
  915207  bull/chop 2026-01-01..03-01  +9.70%  17 trades rt=2  XOM+225 AMAT+211 NTR+178 VOYA-75
  542754  bear      2026-03-02..03-30 +11.94%  35 trades rt=3  SQQQ+889 UHS-93 BTC-41 SPY-39
  383778  OOS bull  2026-03-30..04-27  +4.75%  30 trades rt=6  SQQQ-257 AAOI+148 SPY+129 ETH+110
TARGET 1x = +6%/mo. bull/chop is +4.85%/mo. OOS is +4.75%/mo. BOTH BELOW TARGET.

CONFIG ON doc-193 now: momentum_swap_vs_portfolio_enabled=False (rotation OFF),
entry_extension_metric=range, momentum_rank_on_60d=True, momentum_missing_60d_excluded=True,
momentum_scan_cached_bars=True, max_positions_honour_regime_cap=True, min_position_nav_pct=0.06,
backtest_credit_pending_sell_proceeds=True, core_max_pct=0.40, core_target_pct=0.35 (profiles),
total_spend_cap_target_weight_pct=0.14, max_positions=6, residual_sleeve_bear_alloc_pct=0.35,
momentum_breakout_freshness_pct=0, turnover_budget_monthly_pct=0.5.

Pull: python3 scripts/pull_backtest_logs.py <id> --filter '<regex>' --stdout   (free, no credits)
Prior investigations: docs/investigations/*.md and _SYNTHESIS.md — READ THEM, do not redo them.
