# Task 8 report — durable Alpaca order lifecycle

## Scope and safety

- Stock/Alpaca only. Robinhood, Kalshi, and crypto behavior was not changed.
- No instance, broker, database, or deployment command was started.
- Existing transport paths were treated as manually CRITICAL because the
  GitNexus index under-reports dynamically wired broker call sites.

## Impact evidence

- `AlpacaAdapter.submit_order`: GitNexus LOW, 2 direct callers (`buy`, `sell`)
  and 1 indirect caller (`execute_signal`); manually CRITICAL.
- `AlpacaAdapter._on_trade_update`: GitNexus LOW/no callers; manually CRITICAL
  because alpaca-py registers it dynamically.
- `AlpacaAdapter.reconcile_wal_with_broker`: GitNexus LOW/no callers; manually
  CRITICAL because broker startup invokes it dynamically.
- `LiveOrderWAL`: GitNexus MEDIUM, 9 direct imports and 15 total affected
  symbols; compatibility contract retained.
- `LiveOrderService`: absent from the stale graph index; manually CRITICAL.
- `nexus_runtime_state.ensure_tables`: GitNexus LOW; lifecycle table added.

## Delivered

- Immutable `LifecycleState`, `BrokerOrderEvent`, and `ConfirmedFill`.
- Append-only lifecycle histories with atomic compare-and-set versions,
  duplicate-event idempotency, immutable identities, and terminal closure.
- Persistence-before-submit, durable ambiguous outcomes, deterministic
  client-ID reconciliation, and bounded terminal retry identities.
- Exactly-once cumulative-to-incremental fill conversion, including partial
  fills, cumulative-average-price deltas, fees, sell proceeds, and reservation
  release only on terminal truth.
- Per-intent gap-fill capacity breach evidence without consuming another
  intent's reservation.
- A RethinkDB lifecycle backend using atomic version-checked appends.
- Alpaca stream events dispatch through one ordered background worker. The
  callback performs no portfolio accounting and no synchronous broker/database
  I/O. Compatibility mirrors update only after lifecycle persistence.
- The trade-update stream starts only after the lifecycle sink is bound.
- Pre-submit client-ID lookup failures now fail closed rather than falling
  through to a POST.

## TDD and verification

- RED: five Task 8 files failed collection because lifecycle APIs did not
  exist; the adapter bridge then failed because no sink binding existed.
- GREEN:

```text
90 passed in 0.61s
```

The batch covered the Task 8 lifecycle/crash/partial/retry suites plus the
existing Task 7 gate, WAL, Alpaca marks, clean-room, classifier, manual-order,
and residual-sleeve suites. `py_compile` passed for every edited production
module.

## Remaining risk handed to Task 9

- Ownership still uses the legacy WAL classifier during adapter construction.
  Task 9 must make broker-first reconciliation mandatory before any strategy
  ownership is exposed.
- The old WAL remains a mutable compatibility summary. The lifecycle event
  history is authoritative; Task 9 will derive ownership from it.
- A transport outcome that remains absent/unqueryable stays UNKNOWN and
  blocked; it is deliberately not auto-resubmitted.
