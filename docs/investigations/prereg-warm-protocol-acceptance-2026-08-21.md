# Preregistration: warm-but-clean protocol acceptance (A/A, window d)

Date: 2026-08-21. Registered BEFORE the run.

## What is being validated
The new `--warmup-start` mode of run_paired_experiment.py: one warmup backtest
(2026-02-01..2026-04-01, base config) builds the discovery pool in-run; the state snapshot is
restored identically for both arms; the measurement window is 2026-04-01..2026-06-01 (window d).
No treatment — this is an A/A of the instrument itself.

## Endpoints
1. **PRIMARY (instrument validity):** the two arms are byte-identical — 100% traded-name
   overlap and identical per-name P&L, same bar as the cold A/A standard (bt 479057/193668).
   PASS = the warm protocol is as trustworthy as the cold one. Any divergence = the snapshot
   misses steering state; enumerate what differs before any warm A/B is trusted.
2. **SECONDARY (the payoff question):** does the warmed control trade a mover-class book?
   Compare the traded set against cold window d (MSFT/NVDA/NVTS/OIH/RIVN/SPY/VDE, +10.16%) and
   contaminated-warm window d (AAOI/AEHR/AXTI/MXL..., +20.53%). If the warmed book surfaces
   small-cap movers absent from the cold book, the "cold understates" gap is CLOSED by the
   harness and the discovery-pool question no longer requires weeks of forward paper.
3. Also record: warmup wall-time, snapshot size, restore digest — the protocol's cost sheet.

## What this replaces (and what it does not)
If PRIMARY and SECONDARY both land, backtest validation covers: representative discovery-pool
behavior, lever A/Bs on a warm book, and drawdown-path analysis on realistic books. Forward
paper remains the only source for: real broker execution (fills/latency/halts), the formal
`paper_observation` promotion gate, and PIT-certified claims (research mode has lookahead).
Paper keeps running in parallel either way — but stops being the bottleneck for lever decisions.

## Result (appended after the runs)
_pending_
