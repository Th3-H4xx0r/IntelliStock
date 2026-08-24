#!/bin/sh
# Poll one backtest until it reaches a terminal status. Emits one line per poll
# so a stall is visible as a repeated identical line rather than as silence.
ID="$1"
INTERVAL="${2:-600}"
while true; do
  ROW=$(python3 scripts/_pgx.py "select doc->>'status' || ' pnl=' || coalesce(doc->>'total_pnl_pct','-') from \"BacktestResults\" where id='$ID'" 2>/dev/null | tail -1)
  echo "bt$ID $ROW"
  case "$ROW" in
    *completed*|*stopped*|*error*|*failed*) break ;;
  esac
  sleep "$INTERVAL"
done
