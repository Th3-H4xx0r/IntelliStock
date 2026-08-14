# Parallel log investigation

Operator direction: stop spending backtest credits and investigate the existing logs exhaustively in
parallel instead. Backtests cannot be parallelised (the deployment runs one at a time and a second
launch preempts the first), but log analysis can.

bt 973976 (W3 control) was stopped at ~2% for this purpose. Logs under investigation are the
completed performance runs, not the killed diagnostic probes.
