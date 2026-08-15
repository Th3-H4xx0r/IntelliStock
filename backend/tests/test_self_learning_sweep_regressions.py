"""Regressions for the defects an adversarial sweep found in the first cut.

Each test names the failure it locks out. Several of these are cases where the
buggy version was not merely wrong but INVERTED — it reported a broken thing as
healthy — which is the worst failure mode for a subsystem whose whole job is
telling the operator what to trust.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.findings import finding_from_funnel, findings_for_run
from self_learning.pipeline import process_backtest_document
from self_learning.retention import cutoff_iso, expired_ids, rollup
from self_learning.timeline import to_naive_utc
from self_learning.types import Observation, content_id
from self_learning.variance import assess_variance


# ── Guard 3 must not be inverted by NaN ──────────────────────────────────────

def test_a_field_that_is_all_nan_is_not_reported_as_healthy():
    """Distinct NaN objects never compare equal, so a Counter gave each its own
    key: 50 NaN scores read as '50 distinct values, healthy'. NaN is the ABSENCE
    of a score, not a score.

    Note the fixture uses a comprehension, not [x]*50 — the latter shares one
    object and passes even against the buggy version.
    """
    values = [float("nan") for _ in range(50)]
    report = assess_variance(values, field_name="normalized_score")
    assert report.n == 0
    assert report.distinct == 0
    assert report.saturated is False


def test_nan_does_not_dilute_a_genuinely_saturated_field():
    values = [1.0 for _ in range(40)] + [float("nan") for _ in range(40)]
    report = assess_variance(values, field_name="normalized_score")
    assert report.n == 40 and report.saturated is True


def test_infinity_is_treated_as_missing_too():
    report = assess_variance([float("inf")] * 40, field_name="x")
    assert report.n == 0 and report.saturated is False


def test_bools_do_not_merge_with_one_and_zero():
    """hash(True) == hash(1) == hash(1.0), so a Counter collapsed them: a
    numeric field's top_value rendered as `True` in operator-facing text."""
    report = assess_variance([True] * 40 + [1.0] * 40, field_name="flag")
    assert report.distinct == 2
    assert report.saturated is False


def test_an_unhashable_score_does_not_take_down_the_pass():
    report = assess_variance([[1, 2]] * 5 + [0.5] * 40, field_name="x")
    assert report.n == 40


# ── Deterministic attribution ────────────────────────────────────────────────

def _split_doc(order):
    decisions = []
    for name in order:
        for i in range(20):
            decisions.append({
                "timestamp": f"2026-04-{(i % 28) + 1:02d}T13:30:00",
                "symbol": f"{name}{i}", "action": "buy", "decision": 1,
                "normalized_score": float(i) / 20.0,
                "primary_strategy": name, "strategies": []})
    return {"id": 1, "backtest_decisions": decisions, "backtest_trades": []}


def test_a_tied_attribution_is_order_independent():
    """Counter.most_common returns the FIRST-INSERTED key among ties, and
    broker.py re-sorts symbols by conviction every tick — so the same defect
    produced two different targets, and therefore two finding threads."""
    first = process_backtest_document(_split_doc(["rsi", "nexus"]), detected_at="t")
    second = process_backtest_document(_split_doc(["nexus", "rsi"]), detected_at="t")
    assert first["target"] == second["target"]


def test_unattributed_decisions_are_counted_not_dropped():
    """A single attributed decision out of forty must not win outright."""
    decisions = [{"timestamp": f"2026-04-{(i % 28) + 1:02d}T13:30:00",
                  "symbol": f"S{i}", "action": "buy", "decision": 1,
                  "normalized_score": 1.0, "primary_strategy": None,
                  "strategies": []} for i in range(39)]
    decisions.append({"timestamp": "2026-04-01T13:30:00", "symbol": "Z",
                      "action": "buy", "decision": 1, "normalized_score": 1.0,
                      "primary_strategy": "rsi", "strategies": []})
    out = process_backtest_document({"id": 1, "backtest_decisions": decisions,
                                     "backtest_trades": []}, detected_at="t")
    assert out["target"] == "equity/unattributed"


def test_a_crypto_run_is_not_labelled_equity():
    doc = {"id": 1, "backtest_trades": [],
           "backtest_decisions": [
               {"timestamp": "2026-04-01T13:30:00", "symbol": "BTC/USD",
                "action": "buy", "decision": 1, "normalized_score": 0.5,
                "primary_strategy": "meanrev", "strategies": []}]}
    out = process_backtest_document(doc, detected_at="t", venue="crypto")
    assert out["target"] == "crypto/meanrev"


# ── The join-health gate ─────────────────────────────────────────────────────

def test_a_broken_join_reports_itself_instead_of_a_fake_conversion_finding():
    """The original bug: fills existed, none matched, and the subsystem
    published a confident 'high severity: 0 of 40 buys converted'."""
    finding = finding_from_funnel(
        {"decided": 40, "buy_decided": 40, "buy_executed": 0,
         "trades_available": 38, "trades_matched": 0},
        target="equity/nexus", run_id="1", detected_at="t")
    assert finding is not None
    assert finding.kind == "join_failure"


def test_a_healthy_join_with_genuinely_low_conversion_still_reports_it():
    finding = finding_from_funnel(
        {"decided": 40, "buy_decided": 40, "buy_executed": 2,
         "trades_available": 2, "trades_matched": 2},
        target="equity/nexus", run_id="1", detected_at="t")
    assert finding is not None and finding.kind == "buy_conversion"


def test_the_conversion_finding_says_its_count_is_a_lower_bound():
    """Gate refusals never reach the source table. Claiming completeness would
    be the same overstatement this subsystem exists to prevent."""
    finding = finding_from_funnel(
        {"decided": 40, "buy_decided": 40, "buy_executed": 2,
         "trades_available": 2, "trades_matched": 2},
        target="equity/nexus", run_id="1", detected_at="t")
    assert "refused at a gate" in finding.detail


def test_findings_for_run_honours_the_configured_thresholds():
    """It hardcoded 0.95/30, silently ignoring the operator's config."""
    obs = [Observation(run_id="1", origin="backtest", venue="equity",
                       strategy_id="s", as_of=f"2026-04-{i:02d}", symbol="X",
                       action="buy", decision=1, normalized_score=1.0,
                       executed=False, refusal_reason="unfilled", votes=(),
                       config_hash=None) for i in range(1, 32)]
    out = findings_for_run(obs, {}, target="t", run_id="1", detected_at="t",
                           variance_min_n=1000)
    assert "constant_signal" not in {f.kind for f in out}


def test_the_saturation_detail_never_contradicts_itself():
    """`:.1f` rounded 99.99 to 100.0, yielding '1 of 10000 samples differ:
    100.0% take the single value'."""
    from self_learning.findings import finding_from_variance
    report = assess_variance([1.0] * 9999 + [0.5], field_name="s")
    finding = finding_from_variance(report, target="t", run_id="1", detected_at="t")
    assert "100.0%" not in finding.detail
    assert "1 of 10000 samples differ" in finding.detail


# ── Timestamps ───────────────────────────────────────────────────────────────

def test_the_double_suffix_timestamp_this_codebase_emits_is_parseable():
    """bot_decision_log.build_decision_doc:89 does isoformat() + 'Z' on an
    already-aware datetime. A global .replace('Z','+00:00') made it
    '+00:00+00:00' and raised — so those rows could never expire."""
    assert to_naive_utc("2026-08-15T22:12:26.180893+00:00Z") is not None


def test_an_offset_is_converted_to_utc_not_discarded():
    """Dropping the offset reinterprets local wall time as UTC, which both
    deletes rows that should live and keeps rows that should expire."""
    assert to_naive_utc("2026-05-16T21:00:00-05:00").isoformat() == "2026-05-17T02:00:00"


def test_an_offset_row_just_inside_the_window_is_not_deleted():
    docs = [{"id": "keep", "as_of": "2026-05-16T21:00:00-05:00"}]
    assert expired_ids(docs, now_iso="2026-08-15T00:00:00+00:00",
                       retain_days=90) == []


def test_retain_days_zero_cannot_wipe_the_table():
    docs = [{"id": "today", "as_of": "2026-08-15T00:00:00"}]
    assert expired_ids(docs, now_iso="2026-08-15T00:00:00", retain_days=0) == []


def test_cutoff_iso_gives_the_caller_a_server_side_bound():
    """Retention must be a range delete in the DB, not a full-table load."""
    assert cutoff_iso(now_iso="2026-08-15T00:00:00", retain_days=90) == \
        "2026-05-17T00:00:00"


# ── Rollup interpretability ──────────────────────────────────────────────────

def test_rollup_carries_the_scored_count_beside_the_share():
    """A share of 0.0 (field entirely missing) read BETTER than a healthy
    0.005, and every single-row bucket read as maximum saturation."""
    docs = [{"id": str(i), "as_of": "2026-04-01T00:00:00", "run_id": "1",
             "strategy_id": "rsi", "normalized_score": None} for i in range(200)]
    row = rollup(docs)[0]
    assert row["score_n"] == 0 and row["score_top_share"] == 0.0


def test_rollup_ids_cannot_collide_across_separator_characters():
    a = rollup([{"id": "1", "as_of": "2026-04-01T00:00:00", "run_id": "a|b",
                 "strategy_id": "c"}])[0]["id"]
    b = rollup([{"id": "2", "as_of": "2026-04-01T00:00:00", "run_id": "a",
                 "strategy_id": "b|c"}])[0]["id"]
    assert a != b


# ── Identity hardening ───────────────────────────────────────────────────────

def test_content_id_does_not_collide_across_types():
    from decimal import Decimal
    assert content_id("k", {"v": Decimal("1")}) != content_id("k", {"v": "1"})
    assert content_id("k", {"v": (1, 2)}) != content_id("k", {"v": [1, 2]})


def test_content_id_is_still_order_independent():
    assert content_id("k", {"a": "1", "b": "2"}) == content_id("k", {"b": "2", "a": "1"})
