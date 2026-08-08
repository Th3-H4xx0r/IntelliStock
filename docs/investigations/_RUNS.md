KEY RUNS (instance v2-let-run-core, window 2026-01-01..2026-03-01 unless noted, $6,000, 3600s):
  455506  +6.02%   early baseline, few levers
  498816 +15.04%   5 levers BUT config could not run live (live_mode_overrides would change it)
  264179  +9.31%   first live-faithful config
  820236 +12.33%   BEST live-faithful.  + turnover conviction bypass
  718249  +4.23%   + max_positions_exclude_sleeve_legs + rank_band_momentum_exempt + turnover ceiling
  613166  +9.17%   same as 718249 but after clearing GraphNexusActiveEvents (cold event state)
  725146  NEGATIVE + satellite_conviction_reserve_pct=0.15   (STOPPED ~78%)
  342380 +18.71%   BEAR window 2026-03-02..03-30, SQQQ +$791 — this one is GOOD, do not break it
Pull logs with:  python3 scripts/pull_backtest_logs.py <id> --filter '<regex>' --stdout
