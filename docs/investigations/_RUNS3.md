RUN UNDER INVESTIGATION: bt 427197 (2026-01-01..03-01, v2-let-run-core, $6,000, 3600s)
Config change vs bt 915207 (+9.70%): core_min_pct 0.25 -> 0.10 (widened the conviction
overflow band from $600 to $1,500 against a $840 clip) and watchlist_priority_slots 0 -> 2.
That removed all 42 SATELLITE CAP skips (now 0) and OVERFLOW fires 9x.

TRAJECTORY — note the drop:
  25.0%  pnl +8.98   13 trades  rt 0
  46.7%  pnl +15.88  13 trades  rt 0     <-- peak, already past the +12% target
  66.7%  pnl +10.12  19 trades  rt 1     <-- lost ~5.7pp
  70.1%  pnl +11.14  19 trades  rt 1
Opening book: SLV/CPER/SBLK/TDY each $840 (14.0%), SPY $2,398 (40.0%), BA $240 (4.0%).

SNDK STILL NOT BOUGHT. Its log lines:
  Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%)     <- found on bar 1
  V32 mw_buy extension-block: SNDK range +73.2% > 25% [bars=97]  <- x2
  Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700, source=direct)  <- x many
SNDK moved $237 -> $641 (+166%) in this window.

Comparison runs: 915207 (+9.70%), 542754 bear (+11.94%), 383778 OOS bull (+4.75%).
Pull: python3 scripts/pull_backtest_logs.py <id> --filter '<regex>' --stdout  (free)
Prior findings: docs/investigations/*.md incl. gap-*.md and _SYNTHESIS.md. READ, do not redo.
