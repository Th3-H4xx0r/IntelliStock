# Task 10 report — coherent freshness, clock, and session controls

## Risk/impact

GitNexus could not resolve the newly integrated `UnifiedOrderGate.evaluate` or
the dynamically invoked `_live_order_dependency_snapshot`; both were treated
as manually CRITICAL. The adapter market-stream addition is read-only and has
no order API.

## Delivered

- Healthy statuses now require recent evidence timestamps for kill switch,
  cash, calendar, persistence, risk state, and watchdog.
- Future-skewed timestamps are distinct blockers; quote and position
  timestamps receive the same skew enforcement.
- Reduce-only intents may ignore stale cash, but still require fresh position,
  quote, and persistence truth.
- The gate binds an intent's reference price and observation timestamp to the
  same authoritative typed market mark.
- `_live_order_dependency_snapshot` no longer reads `intent.reference_price`
  or `_last_prices` as market truth. It evaluates the adapter's `MarketMark`
  against submission/risk-reduction policy.
- The read-only `AlpacaMarkStream` is wired to the adapter's authoritative mark
  book before trading streams start. Subscription overflow is explicit and
  affected symbols fail closed for lack of a mark.
- Strategy, manual, and residual-sleeve intents use the selected mark's actual
  price and observation timestamp.
- Pre-submit position health comes from full Task 9 reconciliation, never a
  simple refresh.
- Market-calendar timeout/exception now marks the dependency unknown, closes
  the tick, and defers executions. The former explicit fail-open branch was
  removed.

## Verification

RED: nine freshness/source tests failed before the contracts existed.

GREEN:

```text
129 passed, 1 warning in 1.31s
```

The batch covered dependency freshness, lifecycle/service, startup
reconciliation, typed marks and stream wiring, NYSE calendar behavior, manual
orders, and residual-sleeve paths. Production modules compiled.

No instance or external service was started.
