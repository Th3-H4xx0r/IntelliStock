"""Task 4 (2026-07-28): the single finalization boundary for one backtest run.

The invariant: **only a successful, complete run may seal a fixture.**

Every other way a backtest can end -- an ordinary exception, a critical LLM
abort, a user stop, a pause termination, or a forced `os._exit` -- must persist
an ineligible outcome, clear the process-global evidence session, and leave no
finalized fixture behind. A half-sealed fixture is worse than no fixture: it
looks like replayable evidence, so a later arm would replay a partial request
set and report a "matched" result that never happened.

`try/finally` cannot carry that invariant on its own, because `os._exit`
bypasses it. `install_terminal_hook` wraps the exit call so finalization runs
first, and it deliberately swallows persistence errors -- a broken store must
never turn a forced exit into a hang.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

import model_evidence
from backtest_evidence_options import EvidenceOptionError
from backtest_replay import (
    ExperimentMatrixManifest,
    FixtureBuild,
    ReplayError,
    ReplayReceipt,
)

_RECORD_MODES = frozenset({"record", "record_extend"})


def _same_value(left, right) -> bool:
    """Structural equality over plain JSON values, ignoring immutability form."""
    import json as _json

    def _plain(value):
        if isinstance(value, Mapping):
            return {str(key): _plain(item) for key, item in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [_plain(item) for item in value]
        return value

    try:
        return _json.dumps(_plain(left), sort_keys=True) == _json.dumps(
            _plain(right), sort_keys=True)
    except (TypeError, ValueError):
        return False


def default_replay_store():
    """Build the production replay store over the immutable-record seam.

    `PostgresReplayStore` needs an `insert_record`/`get_record` backend, NOT a
    database connection, and the store takes its own pooled connection per
    operation so an immutable publish is never tied to a request- or
    run-scoped one. Shared by the API and the broker so the two cannot drift.
    """
    from db import schema

    from backtest_replay import (
        BUILD_TABLE,
        CALL_TABLE,
        FIXTURE_TABLE,
        MATRIX_TABLE,
        RECEIPT_TABLE,
        PostgresReplayStore,
    )
    from benchmark_alpha.pg_store import PostgresBackend

    # Create the immutable-record tables on first use. Idempotent, and cheap
    # next to the run that follows; the alternative is a first publish that
    # fails on a missing table long after the work was done.
    schema.ensure_schema(tables=(MATRIX_TABLE, BUILD_TABLE, CALL_TABLE,
                                 FIXTURE_TABLE, RECEIPT_TABLE))

    return PostgresReplayStore(PostgresBackend())


class EvidenceRunLifecycle:
    """Owns evidence activation, PIT capture and terminal finalization."""

    def __init__(
        self,
        *,
        options: Mapping,
        backtest_id: str,
        store,
        window,
        fixture_ordinal: int,
        benchmark_manifest: Mapping,
        rng_seed_manifest: Mapping,
    ) -> None:
        self._options = dict(options or {})
        self._mode = str(self._options.get("evidence_mode") or "off")
        self._backtest_id = str(backtest_id or "")
        self._store = store
        self._window = window
        self._fixture_ordinal = int(fixture_ordinal)
        self._benchmark_manifest = dict(benchmark_manifest or {})
        self._rng_seed_manifest = dict(rng_seed_manifest or {})

        self._matrix: ExperimentMatrixManifest | None = None
        self._replay_fixture = None
        self._arm_name: str | None = None
        self._build: FixtureBuild | None = None
        self._session = None
        self._terminal: dict | None = None
        self._receipt: ReplayReceipt | None = None

    # ------------------------------------------------------------- properties
    @property
    def enabled(self) -> bool:
        return self._mode != "off"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def arm_name(self) -> str | None:
        return self._arm_name

    @property
    def build(self) -> FixtureBuild | None:
        return self._build

    # ---------------------------------------------------------------- preflight
    def begin(self) -> None:
        """Validate the run against its preregistered matrix, then activate.

        Runs BEFORE historic lookback so a mis-declared arm fails while the run
        is still cheap, rather than after a multi-hour window has burned.
        """
        if not self.enabled:
            return
        matrix_id = self._options.get("matrix_manifest_id")
        arm_id = self._options.get("matrix_arm_id")
        scenario = self._options.get("cost_scenario_id")
        try:
            matrix = self._store.require_matrix(matrix_id)
        except Exception as exc:
            raise EvidenceOptionError(
                f"evidence run names an unpublished matrix {matrix_id!r}") from exc
        if not isinstance(matrix, ExperimentMatrixManifest):
            raise EvidenceOptionError("store returned a non-manifest matrix")
        arm_name = next(
            (name for name, value in matrix.arm_ids.items() if value == arm_id), None)
        if arm_name is None:
            raise EvidenceOptionError(
                f"arm {arm_id!r} is not declared by matrix {matrix.matrix_id!r}")
        if scenario not in matrix.cost_scenario_hashes:
            raise EvidenceOptionError(
                f"cost scenario {scenario!r} is not preregistered in the matrix")
        self._matrix = matrix
        self._arm_name = arm_name

        if self._mode in _RECORD_MODES:
            try:
                self._build = FixtureBuild(
                    matrix=matrix, window=self._window,
                    fixture_ordinal=self._fixture_ordinal,
                    cost_scenario_id=scenario,
                    rng_seed_manifest=self._rng_seed_manifest,
                    benchmark_manifest=self._benchmark_manifest,
                    store=self._store,
                )
            except ReplayError as exc:
                raise EvidenceOptionError(str(exc)) from exc

        ledger = self._build.ledger if self._build is not None else None
        declared = None
        if self._mode == "replay":
            try:
                fixture, ledger = self._store.load_replay_fixture(
                    self._options.get("replay_fixture_id"), self._arm_name
                )
            except Exception as exc:
                raise EvidenceOptionError(
                    "replay fixture could not be loaded and verified"
                ) from exc
            # A sealed fixture belongs to one matrix/window/ordinal/cost address.
            # Replaying it under a different run identity is not the same
            # experiment, so bind every join before the session is activated.
            if (fixture.matrix_id != matrix.matrix_id
                    or not _same_value(fixture.window, self._window)
                    or fixture.fixture_ordinal != self._fixture_ordinal
                    or fixture.cost_scenario_id != scenario
                    or fixture.cost_scenario_hash != matrix.cost_scenario_hashes[scenario]):
                raise EvidenceOptionError(
                    "replay fixture does not match this run's preregistered address")
            self._replay_fixture = fixture
            declared = frozenset(fixture.request_set(self._arm_name))
        session = model_evidence.ModelEvidenceSession(
            mode=self._mode,
            ledger=ledger,
            arm_id=arm_id,
            declared_occurrences=declared,
            backtest_id=self._backtest_id,
            build_id=(self._build.build_id if self._build is not None
                      else self._options.get("fixture_build_id")
                      or self._options.get("replay_fixture_id")),
        )
        self._session = model_evidence.activate_model_evidence_session(session)

    def _declared_occurrences(self):
        """Replay must know its sealed request set up front; build modes
        discover theirs as the run proceeds."""
        if self._mode != "replay":
            return None
        fixture_id = self._options.get("replay_fixture_id")
        try:
            fixture, _ledger = self._store.load_replay_fixture(
                fixture_id, self._arm_name
            )
        except Exception as exc:
            raise EvidenceOptionError(
                "replay fixture could not be loaded and verified"
            ) from exc
        return frozenset(fixture.request_set(self._arm_name))

    # ------------------------------------------------------------------ capture
    def record_pit(self, decision_at, manifest) -> None:
        """Bind one resolved point-in-time manifest to its decision."""
        if not self.enabled or self._build is None:
            return
        self._build.record_pit(decision_at, manifest)

    def record_model_row(self, record) -> None:
        if not self.enabled or self._build is None:
            return
        self._build.record_model_row(self._arm_name, record)

    def bind_clean_start_audit(self, audit):
        if not self.enabled or self._session is None:
            return None
        return self._session.bind_clean_start_audit(audit)

    # ---------------------------------------------------------------- terminals
    def succeed(
        self,
        *,
        trade_ledger_hash: str,
        executed_source_tree_hash: str,
        dependency_runtime_digest: str,
        executed_cost_model_hash: str,
        audits: Mapping,
        executed_content_manifest: Mapping | None = None,
    ) -> ReplayReceipt | None:
        """Seal the fixture and publish the receipt. Success only."""
        if not self.enabled:
            return None
        if self._terminal is not None:
            raise EvidenceOptionError(
                f"run already finalized as {self._terminal.get('reason')!r}")
        scenario = self._options.get("cost_scenario_id")
        experiment = self._matrix.experiment_for(self._arm_name, scenario)
        fixture = None
        try:
            session_audit = self._session.finalize()
        except Exception as exc:
            raise EvidenceOptionError(
                "model evidence session did not finalize completely"
            ) from exc
        if self._build is not None:
            # Production publishes rows through the process-global session, so
            # adopt exactly what this arm actually consumed/recorded before the
            # request set is sealed.
            self._build.adopt_session_occurrences(
                self._arm_name, session_audit.get("observed_occurrences") or ())
            fixture = self._build.seal()
        else:
            fixture = self._replay_fixture
            if fixture is None:
                raise EvidenceOptionError("verified replay fixture is unavailable")
        audits = dict(audits or {})
        if self._build is not None:
            # Record modes own the whole ledger they sealed.
            replay_complete = (
                session_audit.get("ledger_content_hash") == fixture.model_ledger_hash
            )
        else:
            # Replay consumes only this arm's declared subset of the union
            # ledger; `finalize()` has already proved declared == observed.
            replay_complete = (
                frozenset(session_audit.get("observed_occurrences") or ())
                == frozenset(fixture.request_set(self._arm_name))
            )
        receipt = ReplayReceipt(
            matrix=self._matrix,
            arm_name=self._arm_name,
            arm_id=self._matrix.arm_id(self._arm_name),
            fixture=fixture,
            experiment=experiment,
            executed_source_tree_hash=executed_source_tree_hash,
            dependency_runtime_digest=dependency_runtime_digest,
            # The hash of the model the EMULATOR actually used. ReplayReceipt
            # rejects it if it differs from the preregistered one, which is the
            # check that stops a receipt claiming a cost basis the fills never
            # applied. Deriving it from `experiment` would always match.
            executed_cost_model_hash=executed_cost_model_hash,
            trade_ledger_hash=trade_ledger_hash,
            replay_audit=(
                {"complete": True, "ledger_content_hash": fixture.model_ledger_hash}
                if replay_complete else {"complete": False}
            ),
            pit_audit=bool(audits.get("pit")),
            execution_audit=bool(audits.get("execution")),
            benchmark_audit=bool(audits.get("benchmark")),
            accounting_audit=bool(audits.get("accounting")),
            executed_content_manifest=executed_content_manifest,
        )
        # Publish only after the receipt validated: a durable fixture must
        # never outlive a failed finalization.
        if self._build is not None:
            self._store.publish_fixture(fixture)
        self._store.publish_receipt(receipt)
        self._receipt = receipt
        self._terminal = {"reason": "success", "eligible": receipt.promotion_eligible,
                          "receipt_id": receipt.receipt_id,
                          "evidence_mode": self._mode}
        self._clear_session()
        return receipt

    def abort(self, reason: str) -> dict:
        """Persist an INELIGIBLE outcome. Never seals, never raises."""
        if self._terminal is not None:
            return dict(self._terminal)
        outcome = {
            "reason": str(reason or "unknown"),
            "eligible": False,
            "evidence_mode": self._mode,
            "backtest_id": self._backtest_id,
            "matrix_id": getattr(self._matrix, "matrix_id", None),
            "arm_name": self._arm_name,
            "finalized_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self._terminal = outcome
        if not self.enabled:
            # An off lifecycle owns no session and must not clear another
            # run's process-global session.
            return dict(outcome)
        try:
            publish = getattr(self._store, "publish_outcome", None)
            if callable(publish):
                publish(dict(outcome))
        except Exception:
            # A broken store must not mask the original failure.
            pass
        self._clear_session()
        return dict(outcome)

    def install_terminal_hook(self, exit_fn):
        """Wrap a forced-exit call so finalization runs before the process dies.

        `os._exit` skips `try/finally`, atexit handlers and destructors, so the
        only reliable place to finalize is immediately before the call itself.
        """
        def _guarded(status=0, *args, **kwargs):
            try:
                self.abort("forced_exit")
            except Exception:
                pass
            return exit_fn(status, *args, **kwargs)
        return _guarded

    # ------------------------------------------------------------------ helpers
    def _clear_session(self) -> None:
        try:
            model_evidence.clear_model_evidence_session()
        except Exception:
            pass
        self._session = None

    def summary_projection(self) -> dict:
        """Read-only fields safe to expose on /backtests/{id}/summary.

        Never includes prompts, model responses or credentials -- only
        identities and audit verdicts.
        """
        receipt = self._receipt
        projection = {
            "evidence_mode": self._mode,
            "matrix_id": getattr(self._matrix, "matrix_id", None),
            "arm_name": self._arm_name,
            "arm_id": (self._matrix.arm_id(self._arm_name)
                       if self._matrix is not None and self._arm_name else None),
            "cost_scenario_id": self._options.get("cost_scenario_id"),
            "pit_mode": self._options.get("pit_mode") or "strict",
            "pit_provenance": (
                "legacy_unverified"
                if str(self._options.get("pit_mode") or "strict") == "research"
                else "strict_verified"
            ),
            "equity_total_cost_bps": self._options.get("equity_total_cost_bps"),
            "nexus_candidate_overrides": dict(
                self._options.get("nexus_candidate_overrides") or {}),
            "terminal": dict(self._terminal) if self._terminal else None,
        }
        if receipt is not None:
            projection.update({
                "receipt_id": receipt.receipt_id,
                "fixture_id": receipt.fixture.fixture_id,
                "trade_ledger_hash": receipt.trade_ledger_hash,
                "executed_source_identity": receipt.executed_source_identity,
                "execution_cost_model_hash": receipt.executed_cost_model_hash,
                "benchmark_manifest": dict(self._benchmark_manifest),
                "promotion_eligible": receipt.promotion_eligible,
                "audits": {
                    "pit": receipt.pit_audit,
                    "execution": receipt.execution_audit,
                    "benchmark": receipt.benchmark_audit,
                    "accounting": receipt.accounting_audit,
                },
            })
        return projection
