"""The only module in `self_learning` that touches the database.

Everything else is pure so it unit-tests without a database. Writes are
`conflict="update"` on a content id, which makes both the changefeed path and
the historical backfill idempotent: re-reading a run updates its rows instead of
duplicating them.

Two constraints shape everything here, both learned from this deployment:

* **The database is the bottleneck.** `PriceHistory` at ~2.3M rows drove 17
  restarts in 12 days on a memory-starved VM, and a single `BacktestResults` row
  carries 5-13MB (`interactive_utils.py:5220`). So: no full-table reads, no
  full-document pulls, ordering and limits pushed into the database, and a
  retention sweep that deletes by RANGE rather than by loading the table.
* **Only actionable decisions are persisted as rows.** A 15-minute-bar run emits
  7-15k decisions, overwhelmingly HOLDs. Writing a row per hold is what would
  turn this into a second elephant, so holds live in the per-run aggregate and
  only buys/sells become rows. The variance guard still sees every observation —
  it runs in memory, before this filter.
"""
from __future__ import annotations

import uuid

from db import P, schema, store

OBSERVATIONS = "LearningObservations"
ROLLUPS = "LearningObservationRollups"
FINDINGS = "LearningFindings"
FUNNELS = "LearningFunnels"
CONFIG = "LearningConfig"
# Phase 2
OUTCOMES = "LearningOutcomes"
NOISE_FLOORS = "LearningNoiseFloors"
EXPERIMENTS = "LearningExperiments"
LEASE = "LearningLease"
# Phase 3
HYPOTHESES = "LearningHypotheses"
INTENTS = "LearningIntents"
BUDGET_LEDGER = "LearningBudgetLedger"
ACTIVE_CHANGES = "LearningActiveChanges"
ACTIVITY = "LearningActivity"
ENGINE_STATUS = "LearningEngineStatus"
APPROVALS = "LearningApprovals"
REPORTS = "LearningReports"

LEARNING_TABLES = (OBSERVATIONS, ROLLUPS, FINDINGS, FUNNELS, CONFIG,
                   OUTCOMES, NOISE_FLOORS, EXPERIMENTS, LEASE,
                   HYPOTHESES, APPROVALS, REPORTS,
                   INTENTS, BUDGET_LEDGER, ACTIVE_CHANGES, ACTIVITY,
                   ENGINE_STATUS)

LEASE_DOC_ID = "backtest_lease"

CONFIG_DOC_ID = "LearningConfig"

# A single insert of 15k documents is one oversized request that can fail as a
# unit; chunking keeps each write bounded.
WRITE_CHUNK = 500

DEFAULT_CONFIG = {
    "id": CONFIG_DOC_ID,
    # Phase 1 is observe-only. Later phases widen this; it is a stored value
    # rather than a code constant so widening it is an operator action.
    "mode": "observe",
    "enabled": True,
    "retain_days": 90,
    "variance_threshold": 0.95,
    "variance_min_n": 30,
    # Empty until an operator arms a document. Nothing is promotable on day one
    # anyway: no target has a measured noise floor yet.
    "document_allowlist": [],
    # Persisted watermark. Without it every container restart re-pulls every
    # completed run — multi-GB of document transfer and a multi-million-row
    # rewrite storm on a host that restarts often.
    "processed_run_ids": [],
    # Phase 3. Each role reads its own model, so a cheap one can narrate while
    # a strong one forms hypotheses. Empty = that role is off; it never falls
    # back to an ambient default, because a recorded model id that is not the
    # model that answered would make every attribution here a lie.
    "learning_analyst_llm_model_id": "",
    "learning_generator_llm_model_id": "",
    "learning_coder_llm_model_id": "",
    "learning_judge_llm_model_id": "",
    # Sub-live approvals auto-proceed after this many hours. Live rungs ignore
    # it entirely — silence is never consent for real money.
    "approval_timeout_hours": 4,
    # Phase 4/5. Both ceilings default to ZERO, which `budget.can_afford`
    # treats as "no spending" rather than "no limit" — an unset ceiling is not
    # an infinite one.
    "daily_budget_usd": 0.0,
    "monthly_budget_usd": 0.0,
    # The action-class x rung matrix. Empty means the defaults in
    # `permissions.DEFAULT_MATRIX`, which ask before every live change.
    "permission_matrix": {},
    # The automatic breaker. Zero means it never fires, so it must be set
    # before any live rung is armed.
    "breaker_limit_pct": 0.0,
    "attributable_drawdown_pct": 0.0,
    "demote_after": 3,
    # Which instances the engine observes. EMPTY MEANS ALL — the subsystem is
    # read-only when observing, so watching everything is the useful default and
    # narrowing is the deliberate act. This is separate from
    # `document_allowlist`, which governs WRITING and ships empty.
    "watched_instances": [],
}

_MUTABLE_KEYS = frozenset({
    "mode", "enabled", "retain_days", "variance_threshold", "variance_min_n",
    "document_allowlist",
    # Phase 3: per-role model ids, resolved through the Models table by
    # `model_resolver.resolve_model_refs_in_config`.
    "learning_analyst_llm_model_id", "learning_generator_llm_model_id",
    "learning_coder_llm_model_id", "learning_judge_llm_model_id",
    "approval_timeout_hours", "daily_budget_usd", "monthly_budget_usd",
    "permission_matrix", "breaker_limit_pct", "demote_after",
    "watched_instances",
})


def _generated_id(doc) -> dict:
    """RethinkDB minted a UUID primary key for a document that carried none;
    the store requires the key to be in the document, so mint it here."""
    if doc.get("id") is None:
        return {**doc, "id": str(uuid.uuid4())}
    return doc


def ensure_tables(conn=None) -> None:
    """Idempotent DDL for the learning tables.

    The index set lives in `db.schema.TABLES`, which already declares every
    index this module needs: `run_id`/`as_of` on OBSERVATIONS (the per-run read
    and the retention RANGE delete), `run_id`/`observation_id`/`as_of` on
    OUTCOMES, `status`/`registered_at` on EXPERIMENTS, `detected_at` on
    FINDINGS and `observed_at` on FUNNELS.
    """
    schema.ensure_schema(tables=LEARNING_TABLES)
    seed_config(conn)


def _defaulted(value, fallback):
    """ReQL `.default(x)`: the fallback applies to a MISSING or null field,
    never to a present-but-falsy one."""
    return fallback if value is None else value


def _set_union(old, new) -> list:
    """ReQL `set_union`: the elements of `new` that are not already in `old`,
    appended in order. Never a Python set — that would lose the order the UI
    and the trial count both read."""
    out = list(old or [])
    for item in (new or []):
        if item not in out:
            out.append(item)
    return out


def merge_config(doc) -> dict:
    merged = dict(DEFAULT_CONFIG)
    if isinstance(doc, dict):
        merged.update({k: v for k, v in doc.items() if v is not None})
    return merged


def seed_config(conn) -> None:
    """Write the defaults once, so the levers are reachable.

    A config nothing ever writes is a set of inert levers, and this repo has
    shipped thirteen of those.
    """
    try:
        if store.get(CONFIG, CONFIG_DOC_ID) is None:
            store.insert(CONFIG, dict(DEFAULT_CONFIG))
    except Exception:
        pass


def get_config(conn) -> dict:
    """Read the config. Raises on failure — the caller decides how to degrade.

    Swallowing the error and returning defaults meant `enabled: True` during a
    database outage, i.e. the kill switch failed OPEN exactly when back-off was
    wanted.
    """
    doc = store.get(CONFIG, CONFIG_DOC_ID)
    return merge_config(doc)


class ConfigError(ValueError):
    pass


_MODES = ("observe", "propose", "act")


def _validated(key, value):
    """Coerce and range-check one setting, or raise.

    Validation lives at the write boundary because these values gate real
    money. A `document_allowlist` stored as the STRING "179" iterates as the
    characters 1, 7 and 9 — arming three documents that do not exist while
    refusing the one that does. A negative budget would raise the ceiling
    rather than lower it. Neither is something a UI should be trusted to
    prevent.
    """
    if key == "mode":
        text = str(value or "").strip().lower()
        if text not in _MODES:
            raise ConfigError(f"mode must be one of {list(_MODES)}")
        return text
    if key == "enabled":
        return bool(value)
    if key == "watched_instances":
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ConfigError(
                "watched_instances must be a list of instance ids — a bare "
                "string would watch each character as a separate instance")
        return [str(v).strip() for v in value if str(v).strip()]
    if key == "document_allowlist":
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ConfigError(
                "document_allowlist must be a list of document ids — a bare "
                "string would arm each character as a separate document")
        return [str(v).strip() for v in value if str(v).strip()]
    if key == "permission_matrix":
        from self_learning.permissions import (
            ACTION_CLASSES, MODES as PERM_MODES, RUNGS,
        )
        if not isinstance(value, dict):
            raise ConfigError("permission_matrix must be an object")
        clean = {}
        for action_class, rungs in value.items():
            if action_class not in ACTION_CLASSES:
                raise ConfigError(f"unknown action class {action_class!r}")
            if not isinstance(rungs, dict):
                raise ConfigError(f"{action_class} must map rungs to modes")
            for rung, mode in rungs.items():
                if rung not in RUNGS:
                    raise ConfigError(f"unknown rung {rung!r}")
                if str(mode) not in PERM_MODES:
                    raise ConfigError(
                        f"unknown permission mode {mode!r} — must be one of "
                        f"{sorted(PERM_MODES)}")
            clean[action_class] = {str(k): str(v) for k, v in rungs.items()}
        return clean
    if key in ("daily_budget_usd", "monthly_budget_usd", "breaker_limit_pct",
               "variance_threshold", "approval_timeout_hours"):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} must be a number")
        if number < 0:
            raise ConfigError(f"{key} cannot be negative")
        if key == "variance_threshold" and not 0 < number <= 1:
            raise ConfigError("variance_threshold must be between 0 and 1")
        return number
    if key in ("retain_days", "variance_min_n", "demote_after"):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} must be a whole number")
        if number < 1:
            raise ConfigError(f"{key} must be at least 1")
        return number
    if key.endswith("llm_model_id"):
        return str(value or "").strip()
    return value


def put_config(conn, patch: dict) -> dict:
    """Update the operator-settable keys. Unknown keys are ignored.

    Every accepted key is validated first, and one bad value rejects the WHOLE
    patch rather than writing a half-applied config — a settings form that
    silently applies three of five fields is worse than one that refuses.
    """
    clean = {}
    for key, value in (patch or {}).items():
        if key not in _MUTABLE_KEYS:
            continue
        clean[key] = _validated(key, value)
    if clean:
        clean["id"] = CONFIG_DOC_ID
        store.insert(CONFIG, clean, conflict="update")
    return get_config(conn)


def mark_processed(conn, run_id) -> None:
    """Record a run as handled, so a restart does not re-pull every document.

    `set_insert` becomes a read-modify-write: the store deep-merges, and a deep
    merge REPLACES an array rather than appending to it, so the union is
    computed here. A missing config row stays a no-op, exactly as the ReQL
    `.get(...).update(...)` was.
    """
    doc = store.get(CONFIG, CONFIG_DOC_ID)
    if doc is None:
        return
    seen = list(doc.get("processed_run_ids") or [])
    if str(run_id) in seen:
        return
    seen.append(str(run_id))
    store.update(CONFIG, CONFIG_DOC_ID, {"processed_run_ids": seen})


def persistable(observations) -> list:
    """The observations that become rows.

    Holds are the bulk of a run and carry no refusal signal; they survive in the
    per-run aggregate. This is the write-tier split the design calls for, and it
    is what keeps the table from reaching PriceHistory scale.
    """
    return [o for o in (observations or []) if o.decision != 0]


def observation_payloads(observations) -> list:
    return [o.to_doc() for o in persistable(observations)]


def _insert_chunked(conn, table, payloads) -> int:
    written = 0
    for start in range(0, len(payloads), WRITE_CHUNK):
        chunk = payloads[start:start + WRITE_CHUNK]
        store.insert(table, chunk, conflict="update")
        written += len(chunk)
    return written


def put_observations(conn, observations) -> int:
    payloads = observation_payloads(observations)
    if not payloads:
        return 0
    return _insert_chunked(conn, OBSERVATIONS, payloads)


def put_findings(conn, findings) -> int:
    payloads = [f.to_doc() for f in (findings or [])]
    if not payloads:
        return 0
    for payload in payloads:
        # Never clobber an operator's acknowledgement. `conflict="update"` on a
        # doc that always carries status="open" would silently reopen every
        # resolved finding on the next detection.
        payload.pop("status", None)
    for payload in payloads:
        old = store.get(FINDINGS, payload.get("id"))
        if old is None:
            # First sighting: the document is written as-is. The conflict
            # resolver never ran on an insert, so no status is stamped here.
            store.insert(FINDINGS, payload)
            continue
        # `new.merge({...})` REPLACES the row with the new document plus the two
        # preserved fields, so old-only keys do not survive -- store.replace,
        # not a merge.
        merged = dict(payload)
        merged["status"] = (old["status"] if old.get("status") is not None
                            else "open")
        first = old.get("first_detected_at")
        merged["first_detected_at"] = (first if first is not None
                                       else payload.get("detected_at"))
        store.replace(FINDINGS, payload["id"], merged)
    return len(payloads)


def put_funnel(conn, run_id, summary, *, origin="backtest", target="",
               observed_at="") -> None:
    """`observed_at` must be the RUN's time, not the processing time — stamping
    "now" makes every restart rewrite the ordering the UI reads."""
    store.insert(FUNNELS, {
        "id": f"{origin}|{run_id}", "run_id": str(run_id), "origin": origin,
        "target": target, "observed_at": observed_at, **(summary or {}),
    }, conflict="update")


def list_findings(conn, limit: int = 100) -> list:
    """Ordered and limited in the DATABASE. Loading the table into Python and
    slicing afterwards is an OOM waiting for the table to grow."""
    limit = max(1, min(int(limit or 100), 1000))
    return store.run(store.limit(
        store.order_by(FINDINGS, index="detected_at", desc=True), limit))


def list_funnels(conn, limit: int = 100) -> list:
    limit = max(1, min(int(limit or 100), 1000))
    return store.run(store.limit(
        store.order_by(FUNNELS, index="observed_at", desc=True), limit))


def list_observations(conn, run_id, limit: int = 500) -> list:
    limit = max(1, min(int(limit or 500), 2000))
    sel = store.filter(OBSERVATIONS, P.field("run_id").eq(str(run_id)))
    return store.run(store.limit(
        store.order_by(sel, fields=(store.asc("as_of"),)), limit))


def counts(conn) -> dict:
    """Totals computed BY the database.

    `overview` used to sum a 500-row slice and present it as a total, so both
    the run count and the decision count silently pinned once the table passed
    the limit.
    """
    open_findings = store.filter(
        FINDINGS, P.field("status").default("open").eq("open"))
    findings_open = store.count(open_findings)
    by_severity = {}
    for row in store.pluck(store.run(open_findings), "severity"):
        key = str(row.get("severity"))
        by_severity[key] = by_severity.get(key, 0) + 1
    runs = store.count(FUNNELS)
    # Streamed, never materialised: the counter must not silently become "the
    # newest 500 runs" once the table grows.
    decided = refused = 0
    for doc in store.iter(store.Selection(FUNNELS)):
        decided += int(doc.get("decided") or 0)
        refused += int(doc.get("refused") or 0)
    return {
        "open_findings": int(findings_open or 0),
        "by_severity": {str(k): int(v) for k, v in by_severity.items()},
        "runs_observed": int(runs or 0),
        "decisions_observed": int(decided),
        "refusals_observed": int(refused),
    }


def sweep_expired(conn, *, cutoff: str) -> int:
    """Delete expired observations with a server-side RANGE delete.

    The pure `retention.expired_ids` takes a materialised list, which is the one
    thing that must never happen to a multi-million-row table — the remedy for
    the bottleneck would itself OOM. `cutoff` comes from `retention.cutoff_iso`.
    """
    if not cutoff:
        return 0
    result = store.delete(
        OBSERVATIONS,
        store.between(OBSERVATIONS, store.MINVAL, cutoff, index="as_of"))
    return int(result["deleted"] or 0)


# ── Phase 2: outcomes, floors, experiments, lease ─────────────────────────────

def put_outcomes(conn, outcomes) -> int:
    """Outcomes share the observation TTL: they are derived from a raw row and
    become meaningless once it expires."""
    payloads = [o.to_doc() for o in (outcomes or []) if o.resolved]
    if not payloads:
        return 0
    return _insert_chunked(conn, OUTCOMES, payloads)


def put_noise_floor(conn, floor) -> None:
    store.insert(NOISE_FLOORS, floor.to_doc(), conflict="update")


def get_noise_floor(conn, *, target, window_class):
    return store.get(NOISE_FLOORS, f"{target}|{window_class}")


def list_noise_floors(conn, limit: int = 200) -> list:
    limit = max(1, min(int(limit or 200), 1000))
    rows = store.run(store.limit(NOISE_FLOORS, limit))
    rows.sort(key=lambda d: (str(d.get("target") or ""),
                             str(d.get("window_class") or "")))
    return rows


def put_experiment(conn, spec) -> None:
    """Registered specs are immutable; only status and run ids may move.

    A spec that vanishes when it fails turns the ledger into a highlight reel,
    and the trial count that any multiple-comparisons correction depends on
    would silently undercount.
    """
    doc = spec.to_doc() if hasattr(spec, "to_doc") else dict(spec)
    doc.setdefault("status", "registered")
    doc.setdefault("run_ids", [])
    doc.setdefault("refusal_reason", "")
    doc.setdefault("registered_at", "")
    doc = _generated_id(doc)
    old = store.get(EXPERIMENTS, doc["id"])
    if old is None:
        store.insert(EXPERIMENTS, doc)
        return
    # Status is MONOTONIC toward terminal and run_ids only ever grow.
    # Re-registering an identical spec (same content hash) would otherwise
    # reset a `failed` experiment to `registered` and wipe its run ids —
    # which is exactly the "ledger becomes a highlight reel" failure this
    # table exists to prevent, and it would silently undercount the trials
    # any multiple-comparisons correction depends on.
    #
    # The resolver merged onto OLD, so an existing row keeps every field it
    # already had and only these three can move.
    old_status = _defaulted(old.get("status"), "registered")
    new_status = _defaulted(doc.get("status"), "registered")
    new_reason = _defaulted(doc.get("refusal_reason"), "")
    store.replace(EXPERIMENTS, doc["id"], {
        **old,
        "status": new_status if old_status == "registered" else old_status,
        "run_ids": _set_union(old.get("run_ids"), doc.get("run_ids")),
        "refusal_reason": (_defaulted(old.get("refusal_reason"), "")
                           if new_reason == "" else new_reason),
    })


def list_experiments(conn, limit: int = 100) -> list:
    limit = max(1, min(int(limit or 100), 1000))
    return store.run(store.limit(
        store.order_by(EXPERIMENTS, index="registered_at", desc=True), limit))


def get_lease(conn):
    try:
        return store.get(LEASE, LEASE_DOC_ID)
    except Exception:
        return None


def put_lease(conn, lease_doc) -> None:
    store.insert(LEASE, {**(lease_doc or {}), "id": LEASE_DOC_ID},
                 conflict="update")


def clear_lease(conn) -> None:
    store.delete(LEASE, LEASE_DOC_ID)


def sweep_expired_outcomes(conn, *, cutoff: str) -> int:
    """Outcomes are keyed to observations; expire them on the same clock rather
    than leaving orphans behind the observation TTL.

    A RANGE delete on the `as_of` index, matching the observations sweep. The
    lower bound is the empty string rather than `r.minval`, so an UNDATED row
    (`as_of == ""`) is never swept — the same rule `retention` states: a parse
    failure must not become data loss.
    """
    if not cutoff:
        return 0
    result = store.delete(
        OUTCOMES,
        store.between(OUTCOMES, "", cutoff, index="as_of", left_bound="open"))
    return int(result["deleted"] or 0)


# ── Phase 3: hypotheses, approvals, reports ───────────────────────────────────

def resolved_config(conn) -> dict:
    """The config with each role's `*_llm_model_id` resolved into
    provider/model/key by the existing Models-table resolver.

    A resolution failure is RECORDED on the returned config rather than
    swallowed. Silently returning the unresolved config made every role look
    merely unconfigured, which is indistinguishable from the operator not
    having set one — and sent an hour of debugging in the wrong direction.
    """
    config = get_config(conn)
    try:
        from model_resolver import resolve_model_refs_in_config
        return resolve_model_refs_in_config(conn, config)
    except Exception as exc:
        return {**config,
                "_resolution_error": f"{type(exc).__name__}: {exc}"}


def put_hypothesis(conn, hypothesis) -> None:
    doc = hypothesis.to_doc() if hasattr(hypothesis, "to_doc") else dict(hypothesis)
    doc = _generated_id(doc)
    old = store.get(HYPOTHESES, doc["id"])
    if old is None:
        store.insert(HYPOTHESES, doc)
        return
    # Status is operator/judge-owned; re-proposing must never reopen a
    # closed hypothesis, or the generator's memory resets every round and
    # the loop re-proposes what it already disproved. The resolver merged onto
    # OLD, so nothing else in the new document lands either.
    store.replace(HYPOTHESES, doc["id"], {
        **old,
        "experiment_ids": _set_union(old.get("experiment_ids"),
                                     doc.get("experiment_ids")),
    })


def set_hypothesis_status(conn, hypothesis_id, status, reason="") -> None:
    store.update(HYPOTHESES, str(hypothesis_id),
                 {"status": str(status), "status_reason": str(reason)})


def list_hypotheses(conn, limit: int = 200, target=None) -> list:
    """Filter and order in the DATABASE, then limit.

    Limiting first meant "the 200 most recent for this target" was really "200
    arbitrary rows, then filtered" — which could legitimately return zero for a
    target with hundreds of rows. That feed is what reaches the generator as its
    memory of what has already failed, so a random sample there quietly defeats
    the "do not re-propose" guarantee.
    """
    limit = max(1, min(int(limit or 200), 1000))
    selection = store.Selection(HYPOTHESES)
    if target:
        selection = store.filter(HYPOTHESES, {"target": str(target)})
    return store.run(store.limit(
        store.order_by(selection, fields=(store.desc("created_at"),)), limit))


def put_approval(conn, approval) -> None:
    doc = approval.to_doc() if hasattr(approval, "to_doc") else dict(approval)
    store.insert(APPROVALS, _generated_id(doc), conflict="update")


def get_approval(conn, approval_id):
    return store.get(APPROVALS, str(approval_id))


def list_approvals(conn, limit: int = 200) -> list:
    """Pending first, then newest. An unordered `.limit()` returns an ARBITRARY
    subset, so past 200 approvals a pending LIVE row — the one that waits
    indefinitely for a human — could simply not be in the queue's input."""
    limit = max(1, min(int(limit or 200), 1000))
    return store.run(store.limit(
        store.order_by(APPROVALS, fields=(store.desc("requested_at"),)), limit))


def put_report(conn, report) -> None:
    store.insert(REPORTS, _generated_id(dict(report or {})),
                 conflict="update")


def list_reports(conn, limit: int = 50) -> list:
    limit = max(1, min(int(limit or 50), 500))
    rows = store.run(store.limit(REPORTS, limit))
    rows.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return rows


# ── Phase 4/5: intents, budget ledger, active changes ─────────────────────────

def put_intent(conn, intent_doc, *, at="") -> None:
    """Record what the loop decided, so its reasoning is readable afterwards."""
    store.insert(INTENTS, _generated_id({**(intent_doc or {}), "at": str(at)}))


def list_intents(conn, limit: int = 200) -> list:
    limit = max(1, min(int(limit or 200), 1000))
    rows = store.run(store.limit(INTENTS, limit))
    rows.sort(key=lambda d: str(d.get("at") or ""), reverse=True)
    return rows


def list_budget_ledger(conn, limit: int = 1000) -> list:
    limit = max(1, min(int(limit or 1000), 5000))
    return store.run(store.limit(BUDGET_LEDGER, limit))


def put_budget_row(conn, row) -> None:
    store.insert(BUDGET_LEDGER, _generated_id(dict(row or {})),
                 conflict="update")


def list_active_changes(conn, limit: int = 200) -> list:
    """Changes currently applied at some rung."""
    limit = max(1, min(int(limit or 200), 1000))
    return store.run(store.limit(ACTIVE_CHANGES, limit))


def put_active_change(conn, change) -> None:
    store.insert(ACTIVE_CHANGES, _generated_id(dict(change or {})),
                 conflict="update")


def running_backtests(conn) -> list:
    """In-flight runs, for the single-flight lease.

    `BacktestResults` has no `origin` field, so everything here reads as
    human-launched unless the lease itself recognises the run id — which is
    exactly how the lease is written.
    """
    try:
        sel = store.filter("BacktestResults",
                           P.field("status").default("").eq("running"))
        rows = store.pluck(store.run(store.limit(sel, 50)), "id", "status")
    except Exception:
        return []
    return [{"id": row.get("id"), "origin": "human"} for row in rows]


# ── LLM usage: what the subsystem's own thinking cost ─────────────────────────

LLM_USAGE_TABLE = "LLMUsage"
LEARNING_TAG = "self_learning"


def llm_usage(conn, *, limit: int = 500) -> dict:
    """Tokens and cost for THIS subsystem's calls, split by role.

    Reads the same `LLMUsage` table the rest of the app writes, filtered to the
    `self_learning` tag `llm.call_role` sets. That means the figures here are
    the real recorded cost of the calls, not an estimate reconstructed from
    prices — and a role's spend is attributable to the role.
    """
    try:
        rows = store.get_all(LLM_USAGE_TABLE, LEARNING_TAG, index="instance_id")
    except Exception:
        return {"available": False, "by_role": {}, "totals": {},
                "reason": "no LLM usage recorded for the subsystem yet"}

    def _blank():
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "cost_usd": 0.0, "errors": 0}

    totals, by_role = _blank(), {}
    for row in rows:
        site = str(row.get("call_site") or "")
        role = site.split(".")[-1] if site.startswith("self_learning.") else "other"
        bucket = by_role.setdefault(role, _blank())
        for target in (totals, bucket):
            target["calls"] += 1
            target["input_tokens"] += int(row.get("input_tokens") or 0)
            target["output_tokens"] += int(row.get("output_tokens") or 0)
            target["cost_usd"] += float(row.get("cost_usd") or 0.0)
            if not row.get("ok", True):
                target["errors"] += 1
    for bucket in [totals, *by_role.values()]:
        bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)

    recent = sorted(rows, key=lambda d: str(d.get("ts") or ""), reverse=True)[:20]
    return {
        "available": True,
        "totals": totals,
        "by_role": by_role,
        "recent": [{
            "ts": str(row.get("ts") or ""),
            "role": (str(row.get("call_site") or "").split(".")[-1]),
            "model": row.get("model"), "provider": row.get("provider"),
            "input_tokens": int(row.get("input_tokens") or 0),
            "output_tokens": int(row.get("output_tokens") or 0),
            "cost_usd": round(float(row.get("cost_usd") or 0.0), 6),
            "ok": bool(row.get("ok", True)),
        } for row in recent],
    }


# ── Live activity beacon ──────────────────────────────────────────────────────
# One row per in-flight role call. The tab polls it to show a spinner on the
# specific finding and ladder step being worked on, so "thinking" is visible
# rather than inferred from a screen that has not changed.

ACTIVITY_STALE_SECONDS = 600


def begin_activity(conn, *, role, target="", finding_id="", step="", at="") -> None:
    try:
        store.insert(ACTIVITY,
                     {"id": str(role), "role": str(role), "target": str(target),
                      "finding_id": str(finding_id), "step": str(step),
                      "started_at": str(at), "active": True},
                     conflict="update")
    except Exception:
        pass


def end_activity(conn, *, role) -> None:
    try:
        store.update(ACTIVITY, str(role), {"active": False})
    except Exception:
        pass


def list_activity(conn, *, now_iso="") -> list:
    """In-flight role calls.

    A row older than the stale window is reported inactive rather than left
    spinning forever: a crashed engine must not leave the UI claiming it is
    still thinking.
    """
    from self_learning.timeline import to_naive_utc
    try:
        rows = store.run(store.Selection(ACTIVITY))
    except Exception:
        return []
    now = to_naive_utc(now_iso)
    out = []
    for row in rows:
        active = bool(row.get("active"))
        started = to_naive_utc(row.get("started_at"))
        if active and now and started and \
                (now - started).total_seconds() > ACTIVITY_STALE_SECONDS:
            active = False
        out.append({"role": row.get("role"), "target": row.get("target"),
                    "finding_id": row.get("finding_id"), "step": row.get("step"),
                    "started_at": row.get("started_at"), "active": active})
    return out


# ── Purge ─────────────────────────────────────────────────────────────────────

# Everything the subsystem DERIVED. Deliberately excludes CONFIG — wiping it
# would take the operator's mode, budgets, allowlist and four configured role
# models with it, which is not what "delete the learning data" means. Also
# excludes LLMUsage, which is shared with the rest of the app.
PURGEABLE_TABLES = (OBSERVATIONS, ROLLUPS, FINDINGS, FUNNELS, OUTCOMES,
                    NOISE_FLOORS, EXPERIMENTS, LEASE, HYPOTHESES, APPROVALS,
                    REPORTS, INTENTS, BUDGET_LEDGER, ACTIVE_CHANGES, ACTIVITY,
                   ENGINE_STATUS)


def purge(conn, *, confirm: bool = False) -> dict:
    """Delete everything the subsystem derived, so it can observe from scratch.

    `confirm` must be passed explicitly. The watermark in CONFIG is cleared too
    — without that the engine would consider every run already processed and
    the purge would leave it permanently idle rather than re-observing.
    """
    if not confirm:
        raise ValueError("purge requires confirm=True")
    deleted = {}
    for table in PURGEABLE_TABLES:
        try:
            result = store.delete(table, store.Selection(table))
            deleted[table] = int(result["deleted"] or 0)
        except Exception as exc:
            deleted[table] = f"error: {type(exc).__name__}"
    try:
        store.update(CONFIG, CONFIG_DOC_ID, {"processed_run_ids": []})
        deleted["_watermark_cleared"] = True
    except Exception:
        deleted["_watermark_cleared"] = False
    return {"deleted": deleted,
            "kept": [CONFIG, "LLMUsage"],
            "note": ("settings, role models and the LLM usage ledger were kept; "
                     "the processed-run watermark was cleared so every "
                     "completed run is observed again")}


# ── Engine self-report ────────────────────────────────────────────────────────
# The deploy check compares the API container against the working tree. The
# ENGINE runs in its own container from the `intellistock-backend` image tag,
# which that check never touches — so "is the engine running my code" was
# unanswerable, and a stale engine looked identical to a broken one. It now
# reports its own source hash and the time of its last turn.

ENGINE_STATUS_ID = "engine"


def put_engine_status(conn, **fields) -> None:
    try:
        store.insert(ENGINE_STATUS, {"id": ENGINE_STATUS_ID, **fields},
                     conflict="update")
    except Exception:
        pass


def get_engine_status(conn) -> dict:
    try:
        return store.get(ENGINE_STATUS, ENGINE_STATUS_ID) or {}
    except Exception:
        return {}
