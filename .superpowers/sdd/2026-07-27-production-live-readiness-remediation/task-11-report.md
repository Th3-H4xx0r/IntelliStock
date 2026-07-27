# Task 11 report — durable risk and operational recovery

## Scope

Alpaca stock trading only. No Kalshi or crypto behavior was changed. No broker
write API, order cancellation, instance start, or funded-account read occurred.

## GitNexus impact

- `claim_next_pending`: LOW, no indexed upstream callers.
- `halt_live_trading`: MEDIUM, 10 direct callers across tests/API/critical-abort.
  Its behavior was retained; the broker consumer was hardened.
- `AlphaWatchdog.poll_once`: LOW.
- `watchdog_main._build_runtime`: LOW, one direct caller.
- `AlpacaAdapter.refresh_account`: LOW, one direct constructor caller.
- `live_state.ensure_tables`: LOW, four direct callers and two API flows.
- `instance._maybe_start_alpha_watchdog`: LOW, one direct caller.
- `instance.start_broker`: LOW, four direct callers.
- Broker module-level command/shutdown functions and
  `_apply_portfolio_drawdown_halt` were not resolved by the stale index; manual
  review treated their execution-order blast radius as critical and ran the
  affected regression suites.

## Implemented

- Added versioned account/sleeve risk state with CAS persistence, monotonic
  equity high-water marks, continuous soft/hard/kill evaluation, leveraged-ETF
  caps, confirmed-fill-only sleeve basis/peak/cooldown updates, and fail-closed
  missing/corrupt state.
- Paper accounts may initialize risk state; funded accounts with missing state
  remain blocked.
- Wired durable risk refresh to fresh Alpaca account/position reconciliation
  and wired confirmed lifecycle fills to risk persistence.
- Made account kill portfolio-wide, including SQQQ/SPY sleeve holdings.
- Removed the watchdog subprocess's direct Alpaca order-submission authority.
- Added hashed durable watchdog health evidence and made the broker consume it;
  missing, stale, degraded, or invalid evidence blocks exposure.
- Funded Alpaca startup now requires watchdog prerequisites before broker
  `Popen`; a watchdog start failure terminates the just-created broker.
- Added atomic command leases and attempt counters. Expired order-affecting
  commands enter `reconciliation_required` instead of being retried; an
  idempotent halt may be reclaimed.
- Kill-switch/watchdog read timeouts now clear the execution list and fail
  closed.
- Added deterministic shutdown of the Alpaca mark stream, trade-update stream,
  and ordered event worker.

## Verification

- Focused operational/live-order suite: **173 passed**.
- Risk/drawdown/watchdog follow-up: **64 passed**.
- Modified Python modules compile successfully.
- `git diff --check` passed.

The engineering controls do not waive Task 12's evidence requirements. The
system remains ineligible for funded trading until immutable promotion
evidence, including at least 60 trading days of paper observation, passes.
