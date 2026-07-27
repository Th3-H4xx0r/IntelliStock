# Task 9 report — broker-first Alpaca ownership reconciliation

## Impact and warning

GitNexus reports `classify_broker_positions` as **HIGH** risk: 19 direct
dependents (2 adapters and 17 tests). Its compatibility contract was left
unchanged. The promoted Alpaca path now bypasses it until broker-first
lifecycle reconciliation completes.

The graph could not resolve the dynamically invoked broker snapshot-worker
symbols; their risk was treated as manually CRITICAL.

## Delivered

- Immutable authoritative broker position/order snapshots and immutable
  `ReconciliationResult` evidence.
- Two position reads around the all-orders query; any mutation, broker outage,
  or full/truncated order page fails closed.
- Missing acknowledgements/partial/final events are synthesized into the same
  Task 8 event path used by streaming.
- Deterministic second restarts are no-ops for fill accounting.
- Ownership comes only from confirmed lifecycle fill lineage:
  - manual positions remain external;
  - broker excess is split external;
  - broker quantity below lineage is retained only as proven owned while the
    discrepancy is unresolved and new exposure is blocked;
  - unknown order identities are unresolved.
- Alpaca clean-room construction can defer all ownership. The production
  factory uses this mode, binds the lifecycle service, reconciles, publishes
  ownership, then starts the stream.
- The legacy mutable-WAL startup reconciler is skipped for Alpaca and retained
  only for compatibility paths pending final Robinhood removal.
- Continuous broker-first reconciliation runs at a default 60-second cadence.
  Failure marks position health unhealthy; it never infers new ownership from
  a refresh while reconciliation is unhealthy.

## Verification

RED was captured for missing reconciliation contracts and missing adapter
defer/capture/complete methods.

GREEN:

```text
96 passed, 1 warning in 0.83s
```

Coverage included startup reconciliation, clean-room scenarios and adapter
initialization, the HIGH-risk legacy classifier suite, Task 8 lifecycle/crash
tests, Task 7 gate/manual-order tests, and Alpaca market marks. All edited
production files compiled.

No instance, broker, database, Kalshi, crypto, or Robinhood transport was
started or called.
