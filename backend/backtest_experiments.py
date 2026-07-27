"""Immutable preregistration boundary for promotable equity backtests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from experiment_registry import (
    ExperimentRegistry,
    ExperimentSpec,
    RegisteredExperiment,
)


def _iso_date(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


@dataclass(frozen=True)
class BacktestExperimentContext:
    """One successfully persisted registration and its owning registry."""

    registry: ExperimentRegistry
    registration: RegisteredExperiment

    @property
    def experiment_id(self) -> str:
        return self.registration.experiment_id

    @property
    def fingerprint(self) -> str:
        return self.registration.fingerprint

    @property
    def search_scope(self) -> str:
        return self.registration.search_scope

    def trial_count(self) -> int:
        """Count the ledger using the scope on the stored registration."""
        return self.registry.trial_count(scope=self.registration.search_scope)

    def result_identity(self) -> dict[str, str]:
        return {
            "experiment_id": self.experiment_id,
            "experiment_fingerprint": self.fingerprint,
            "experiment_search_scope": self.search_scope,
        }


def preregister_backtest_experiment(
    declaration,
    *,
    effective_config,
    seed,
    start_date,
    end_date,
    registry,
) -> BacktestExperimentContext | None:
    """Persist complete effective inputs before any strategy/model execution.

    ``declaration`` supplies immutable research provenance. Runtime-resolved
    configuration, seed, and exact user window replace any declared copies so
    the stored fingerprint identifies what the worker will actually execute.
    """
    if declaration is None:
        return None
    if not isinstance(declaration, Mapping):
        raise ValueError("experiment_spec must be a mapping")
    if not isinstance(effective_config, Mapping) or not effective_config:
        raise ValueError("effective_config must be a non-empty mapping")
    if not isinstance(registry, ExperimentRegistry):
        raise TypeError("registry must be an ExperimentRegistry")

    payload = dict(declaration)
    payload["effective_config"] = dict(effective_config)
    payload["seed"] = seed
    payload["start_date"] = _iso_date(start_date)
    payload["end_date"] = _iso_date(end_date)
    spec = ExperimentSpec.from_doc(payload)
    registration = registry.register_before_run(spec)
    return BacktestExperimentContext(
        registry=registry,
        registration=registration,
    )
