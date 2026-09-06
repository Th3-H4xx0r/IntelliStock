# Funded outlier entries and additions to proven winners

Research objective: seek $30,000 ending value from $6,000 over the existing
2021-11-01–2026-08-27 native API interval. A 25% drawdown remains the screening
target; higher-risk outcomes must be identified separately. No live adoption.

The earlier sleeve recorded ownership before fills and planned with cash
already reserved for other orders. Reconcile ownership, average cost, entry
date and add count from actual fills. Track pending commitments separately,
subtract reservations from available buying power, and retain exit ownership
until the sale fills. Support both native and live adapter cash interfaces.

Winner additions default off. Eligible holdings must have at least a 25% gain
on average filled cost, age of 20 sessions, 20 sessions since the last buy,
top-decile relative strength, and a price within 2% of their trailing high.
Permit at most two additions, each up to 5% of NAV, capped at 20% of NAV per
position using current marks. Additions and new entries share the sleeve's
cost-basis budget and actual available cash. Existing exit and 30% winner-trim
rules remain in force.

Versioned feature rows use a complete downloaded active/inactive provider
archive. Evaluate nominal-price and raw-dollar-volume eligibility separately
on each date; rank only that day's eligible observations. Split-adjusted
prices supply technical ratios. An ever-eligible retention optimization keeps
all later exit history without changing earlier eligibility or ranks. Never
select history from end-of-period liquidity. Immutable publication completes
its manifest only after every row is inserted. Incomplete versions, stale
history and undated Graph confirmation with versioned data refuse trading.

The archive is not a certified historical security master: missing delisted
securities, provider symbol mappings and correction history remain explicit
limitations. It includes exchange-traded instruments as well as companies;
attribution must identify leveraged ETF gains separately. The Graph inspiration
is adding to confirmed winners, not using today's relationship graph as if it
had been known in the past.

Validation: targeted unit tests for reservations, skipped orders, partial
fills, exits, actual add counts, current-mark caps, live adapter compatibility,
prefix isolation, future-prefix invariance and immutable publication. All
historical performance tests run on the deployed IntelliStock API, one at a
time. Verify source fingerprints, finished status, exact configuration, full
logs, fill/cash/share accounting and quote provenance before conclusions.
Save and restore lab201 after terminal status; never edit live doc200.
