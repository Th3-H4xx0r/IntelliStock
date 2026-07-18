"""Benchmark-alpha package: typed forecasts, durable ledgers, allocation,
risk, execution, research, and promotion for the controlled SPY-relative
portfolio system. RethinkDB is the sole application persistence database;
Alpaca is authoritative for broker state and is reconciled into it."""

from benchmark_alpha.types import (  # noqa: F401
    ABSOLUTE_ACTIVE_CEILING,
    AuthorizationContext,
    EventKind,
    ExecutionMode,
    PromotionTier,
    RunOrigin,
    RunPhase,
    SchedulerTickMode,
    authorized_active_cap,
    live_invariants_expected,
    require_execution_mode,
)
from benchmark_alpha.rethink_store import (  # noqa: F401
    AlphaIntegrityError,
    AlphaRethinkStore,
    AlphaStateConflictError,
    AlphaUnavailableError,
)
