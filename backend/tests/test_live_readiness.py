from dataclasses import FrozenInstanceError

import pytest


def test_live_start_refuses_an_unmet_check():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        assert_live_start_allowed, required_live_checks,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.RESEARCH,
        checks=tuple(ReadinessCheck(name, name != "secrets", "plaintext", "a" * 64)
                     for name in required_live_checks()),
        artifact_hash="b" * 64,
    )

    with pytest.raises(LiveReadinessError, match="secrets"):
        assert_live_start_allowed(report, deployed_artifact_hash="b" * 64)


def test_live_start_requires_every_passing_check_and_an_eligible_state():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        assert_live_start_allowed, required_live_checks,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.PAPER_ELIGIBLE,
        checks=tuple(ReadinessCheck(name, True, "complete", "a" * 64)
                     for name in required_live_checks()),
        artifact_hash="b" * 64,
    )

    with pytest.raises(LiveReadinessError, match="state"):
        assert_live_start_allowed(report, deployed_artifact_hash="b" * 64)


def test_readiness_records_are_immutable_and_fingerprint_checked():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        report_fingerprint, required_live_checks,
        report_from_mapping,
    )

    report = ReadinessReport(
        instance_id="test-instance",
        state=ReadinessState.LIVE_ELIGIBLE,
        checks=tuple(ReadinessCheck(name, True, "encrypted", "a" * 64)
                     for name in required_live_checks()),
        artifact_hash="b" * 64,
    )
    with pytest.raises(FrozenInstanceError):
        report.artifact_hash = "changed"

    payload = {
        "instance_id": report.instance_id,
        "state": report.state.value,
        "artifact_hash": report.artifact_hash,
        "checks": [c.__dict__ for c in report.checks],
        "fingerprint": report_fingerprint(report),
    }
    assert report_from_mapping(payload, instance_id="test-instance") == report
    payload["fingerprint"] = "not-a-fingerprint"
    with pytest.raises(LiveReadinessError, match="fingerprint"):
        report_from_mapping(payload, instance_id="test-instance")


def test_inactive_verifier_rejects_any_order_delta():
    from scripts.verify_inactive_deployment import (
        AccountInvariant,
        compare_account_invariants,
    )

    before = AccountInvariant.from_docs(positions=[], orders=[])
    after = AccountInvariant.from_docs(positions=[], orders=[{"id": "new"}])

    assert compare_account_invariants(before, after).passed is False


def test_inactive_verifier_has_no_broker_write_imports():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "scripts" /
              "verify_inactive_deployment.py").read_text()
    prohibited = ("import broker", "from broker", "submit_order", "cancel_order",
                  "replace_order", "close_position")
    assert not any(token in source for token in prohibited)


def test_strict_report_rejects_coercions_empty_duplicate_missing_and_bad_hashes():
    from live_readiness import LiveReadinessError, required_live_checks, report_from_mapping

    digest = "a" * 64
    checks = [{"name": name, "passed": True, "reason": "verified",
               "evidence_hash": digest} for name in required_live_checks()]
    payload = {"instance_id": "test-instance", "state": "LIVE_ELIGIBLE",
               "artifact_hash": digest, "checks": checks, "fingerprint": digest}
    for bad in ("false", 0, 1, None):
        broken = {**payload, "checks": [{**checks[0], "passed": bad}, *checks[1:]]}
        with pytest.raises(LiveReadinessError):
            report_from_mapping(broken, instance_id="test-instance")
    for broken_checks in ([], checks[:-1], [*checks, checks[0]]):
        with pytest.raises(LiveReadinessError):
            report_from_mapping({**payload, "checks": broken_checks}, instance_id="test-instance")
    for field, value in (("artifact_hash", ""), ("artifact_hash", "bad"),
                         ("fingerprint", ""), ("fingerprint", "not-a-digest")):
        with pytest.raises(LiveReadinessError):
            report_from_mapping({**payload, field: value}, instance_id="test-instance")


def test_live_gate_requires_the_independent_deployed_artifact_identity():
    from live_readiness import (LiveReadinessError, ReadinessCheck, ReadinessReport,
                                ReadinessState, assert_live_start_allowed, required_live_checks)

    digest = "b" * 64
    report = ReadinessReport("test-instance", ReadinessState.LIVE_ELIGIBLE,
                             tuple(ReadinessCheck(name, True, "verified", digest)
                                   for name in required_live_checks()), digest)
    assert_live_start_allowed(report, deployed_artifact_hash=digest)
    with pytest.raises(LiveReadinessError, match="artifact"):
        assert_live_start_allowed(report, deployed_artifact_hash="c" * 64)


def test_artifact_binding_accepts_non_live_report_but_still_validates_structure():
    from live_readiness import (
        LiveReadinessError,
        ReadinessCheck,
        ReadinessReport,
        ReadinessState,
        assert_artifact_bound,
        required_live_checks,
    )

    digest = "b" * 64
    report = ReadinessReport(
        "test-instance",
        ReadinessState.PAPER_ELIGIBLE,
        tuple(ReadinessCheck(name, name != "paper_observation", "pending", digest)
              for name in required_live_checks()),
        digest,
    )
    assert_artifact_bound(report, deployed_artifact_hash=digest)
    with pytest.raises(LiveReadinessError, match="artifact"):
        assert_artifact_bound(report, deployed_artifact_hash="c" * 64)
    with pytest.raises(LiveReadinessError, match="incomplete"):
        assert_artifact_bound(
            ReadinessReport("test-instance", ReadinessState.PAPER_ELIGIBLE,
                            report.checks[:-1], digest),
            deployed_artifact_hash=digest,
        )


@pytest.mark.parametrize(
    ("brokerage", "environment", "requires_live"),
    [
        ({"brokerage_type": "alpaca", "alpaca_paper": True}, {}, False),
        ({"brokerage_type": "alpaca", "alpaca_paper": False}, {}, True),
        ({"brokerage_type": "robinhood"}, {}, False),
        ({"brokerage_type": "robinhood"}, {"RH_DRY_RUN": "true"}, False),
        ({"brokerage_type": "robinhood"}, {"RH_DRY_RUN": "0"}, True),
    ],
)
def test_equity_brokerage_mode_classifier_is_explicit(
        brokerage, environment, requires_live):
    from live_readiness import brokerage_requires_live_gate

    instance = {"id": "i", "kind": "equities", "brokerage_id": "b"}
    brokerage = {"id": "b", **brokerage}
    assert brokerage_requires_live_gate(
        instance, brokerage, environ=environment) is requires_live


@pytest.mark.parametrize(
    ("instance", "brokerage", "environment"),
    [
        ({"id": "i", "kind": "equities"}, {}, {}),
        ({"id": "i", "kind": "equities", "brokerage_id": "b"},
         {"id": "other", "brokerage_type": "alpaca", "alpaca_paper": True}, {}),
        ({"id": "i", "kind": "equities", "brokerage_id": "b"},
         {"id": "b", "brokerage_type": "alpaca"}, {}),
        ({"id": "i", "kind": "equities", "brokerage_id": "b"},
         {"id": "b", "brokerage_type": "unknown"}, {}),
        ({"id": "i", "kind": "equities", "brokerage_id": "b"},
         {"id": "b", "brokerage_type": "robinhood"}, {"RH_DRY_RUN": "maybe"}),
    ],
)
def test_equity_brokerage_mode_classifier_blocks_ambiguous_state(
        instance, brokerage, environment):
    from live_readiness import LiveReadinessError, brokerage_requires_live_gate

    with pytest.raises(LiveReadinessError):
        brokerage_requires_live_gate(instance, brokerage, environ=environment)


def test_live_gate_rejects_directly_constructed_incomplete_checks():
    from live_readiness import LiveReadinessError, ReadinessCheck, ReadinessReport, ReadinessState, assert_live_start_allowed
    report = ReadinessReport("test-instance", ReadinessState.LIVE_ELIGIBLE,
                             (ReadinessCheck("secrets", True, "verified", "a" * 64),), "b" * 64)
    with pytest.raises(LiveReadinessError, match="incomplete"):
        assert_live_start_allowed(report, deployed_artifact_hash="b" * 64)


def test_position_invariants_ignore_marks_but_detect_stable_changes():
    from scripts.verify_inactive_deployment import AccountInvariant, compare_account_invariants
    base = {"asset_id": "a", "symbol": "A", "side": "long", "qty": "1", "avg_entry_price": "2", "current_price": "3"}
    mark = {**base, "current_price": "99", "market_value": "99", "unrealized_pl": "97"}
    before = AccountInvariant.from_docs(positions=[base], orders=[])
    assert compare_account_invariants(before, AccountInvariant.from_docs(positions=[mark], orders=[])).passed
    for key, value in (("qty", "2"), ("side", "short"), ("avg_entry_price", "3")):
        assert not compare_account_invariants(before, AccountInvariant.from_docs(positions=[{**base, key: value}], orders=[])).passed
    assert compare_account_invariants(before, AccountInvariant.from_docs(positions=[{**base, "qty": "1.0", "avg_entry_price": "2.0"}], orders=[])).passed


def test_position_and_order_invariants_fail_closed_and_orders_keep_fields():
    from scripts.verify_inactive_deployment import AccountInvariant, compare_account_invariants
    import pytest
    base = {"asset_id": "a", "symbol": "A", "side": "long", "qty": "1", "avg_entry_price": "2"}
    for row in ({**base, "qty": "NaN"}, {**base, "qty": "Infinity"}, {k: v for k, v in base.items() if k != "side"}, {k: v for k, v in base.items() if k != "avg_entry_price"}):
        with pytest.raises(RuntimeError):
            AccountInvariant.from_docs(positions=[row], orders=[])
    before = AccountInvariant.from_docs(positions=[base], orders=[{"id": "o", "symbol": "A", "qty": "1", "status": "open", "filled_qty": "0"}])
    after = AccountInvariant.from_docs(positions=[base], orders=[{"id": "o", "symbol": "A", "qty": "1", "status": "filled", "filled_qty": "1"}])
    assert not compare_account_invariants(before, after).passed


def test_deployed_image_validation_uses_exact_local_image_id():
    from scripts.verify_inactive_deployment import _validate_deployed_image
    import pytest
    client = type("Client", (), {"images": type("Images", (), {"get": lambda self, ref: type("Image", (), {"id": "sha256:" + "a" * 64})()})()})()
    _validate_deployed_image("local", "a" * 64, client=client)
    with pytest.raises(RuntimeError):
        _validate_deployed_image("local", "b" * 64, client=client)


def test_docker_worker_state_matrix_uses_only_fake_client():
    from scripts.verify_inactive_deployment import _docker_worker_state
    def client(state, restart=""):
        class C:
            attrs = {"State": state, "HostConfig": {"RestartPolicy": {"Name": restart}}}
            def reload(self): pass
        return type("D", (), {"containers": type("X", (), {"get": lambda self, name: C()})()})()
    stopped = {"Running": False, "Paused": False, "Restarting": False, "Status": "exited"}
    assert _docker_worker_state("x", client=client(stopped)) == "stopped"
    for state, restart in (({"Running": False, "Paused": False, "Restarting": False, "Status": "created"}, ""),
                           ({"Running": False, "Paused": True, "Restarting": False, "Status": "exited"}, ""),
                           (stopped, "always"), (stopped, "on-failure")):
        assert _docker_worker_state("x", client=client(state, restart)) != "stopped"


def test_inactive_verification_fails_closed_matrix_and_passes_unchanged():
    from scripts.verify_inactive_deployment import AccountInvariant, InactiveSnapshot, verify_inactive_deployment
    account = AccountInvariant.from_docs(positions=[], orders=[])
    good = InactiveSnapshot(False, account, "a" * 64, "stopped")
    assert verify_inactive_deployment(lambda: good, lambda: None).passed
    for bad in (InactiveSnapshot(True, account, "a" * 64, "stopped"), InactiveSnapshot(False, account, "", "stopped"), InactiveSnapshot(False, account, "a" * 64, "unknown")):
        assert not verify_inactive_deployment(lambda bad=bad: bad, lambda: None).passed
    assert not verify_inactive_deployment(lambda: good, lambda: (_ for _ in ()).throw(RuntimeError())).passed


def test_inactive_verification_detects_after_snapshot_deltas():
    from scripts.verify_inactive_deployment import AccountInvariant, InactiveSnapshot, verify_inactive_deployment
    base = InactiveSnapshot(False, AccountInvariant.from_docs(positions=[], orders=[]), "a" * 64, "stopped")
    changed_account = InactiveSnapshot(False, base.account, "b" * 64, "stopped")
    changed_position = InactiveSnapshot(False, AccountInvariant.from_docs(positions=[{"asset_id":"a","symbol":"A","side":"long","qty":"2","avg_entry_price":"1"}], orders=[]), "a" * 64, "stopped")
    changed_order = InactiveSnapshot(False, AccountInvariant.from_docs(positions=[], orders=[{"id":"o","status":"filled"}]), "a" * 64, "stopped")
    for after in (InactiveSnapshot(True, base.account, "a" * 64, "stopped"), InactiveSnapshot(False, base.account, "a" * 64, "running"), changed_account, changed_position, changed_order):
        rows = iter((base, after))
        assert not verify_inactive_deployment(lambda: next(rows), lambda: None).passed


def test_snapshot_reader_happy_path_uses_only_fake_rethink_and_broker(monkeypatch):
    import scripts.verify_inactive_deployment as verifier
    from live_readiness import ReadinessCheck, ReadinessReport, ReadinessState, report_fingerprint, required_live_checks
    digest = "a" * 64
    report = ReadinessReport(
        "i",
        ReadinessState.PAPER_ELIGIBLE,
        tuple(ReadinessCheck(n, n != "paper_observation", "pending", digest)
              for n in required_live_checks()),
        digest,
    )
    payload = {"instance_id":"i", "state":report.state.value, "artifact_hash":digest, "checks":[c.__dict__ for c in report.checks], "fingerprint":report_fingerprint(report)}
    rows = {
        "Instances": {
            "id": "i", "runCommand": False, "brokerage_id": "b",
            "live_readiness_report": payload,
        },
        "BrokerageAccounts": {
            "id": "b", "brokerage_type": "alpaca", "alpaca_paper": False,
            "alpaca_base_url": "https://api.alpaca.markets",
            "alpaca_account_number": "acct",
        },
    }
    class Chain:
        def __init__(self): self.name = None
        def table(self, name): self.name=name; return self
        def get(self, _): return self
        def run(self, _): return rows[self.name]
    monkeypatch.setattr(verifier, "RethinkDB", lambda: type("R", (), {"db": lambda self, _: Chain()})())
    snap = verifier._snapshot_reader(object(), "i", lambda *_: {"account_id":"acct", "positions":[], "open_orders":[], "recent_orders":[], "recent_trades":[]}, lambda _: "stopped", digest)
    assert snap.run_command is False and snap.worker_state == "stopped" and snap.account_hash and not snap.account.positions and not snap.account.orders


def test_inactive_account_identity_binds_instance_link_provider_and_environment():
    from scripts.verify_inactive_deployment import _account_identity_hash

    brokerage = {
        "id": "b1",
        "brokerage_type": "alpaca",
        "alpaca_paper": False,
        "alpaca_base_url": "https://api.alpaca.markets",
        "alpaca_account_number": "acct",
    }
    baseline = _account_identity_hash("i", "b1", brokerage, "acct")
    assert baseline != _account_identity_hash(
        "i", "b2", {**brokerage, "id": "b2"}, "acct")
    assert baseline != _account_identity_hash(
        "other", "b1", brokerage, "acct")
    with pytest.raises(RuntimeError):
        _account_identity_hash(
            "i", "b1",
            {**brokerage,
             "alpaca_base_url": "https://paper-api.alpaca.markets"},
            "acct",
        )


def test_direct_readiness_construction_never_type_errors():
    from live_readiness import LiveReadinessError, ReadinessReport, ReadinessState, assert_live_start_allowed
    import pytest
    cases = [None, object(), ReadinessReport("", ReadinessState.LIVE_ELIGIBLE, (), "a"*64),
             ReadinessReport(1, ReadinessState.LIVE_ELIGIBLE, (), "a"*64),
             ReadinessReport("i", "LIVE_ELIGIBLE", (), "a"*64),
             ReadinessReport("i", ReadinessState.LIVE_ELIGIBLE, [], "a"*64),
             ReadinessReport("i", ReadinessState.LIVE_ELIGIBLE, (object(),), "a"*64)]
    for report in cases:
        with pytest.raises(LiveReadinessError):
            assert_live_start_allowed(report, deployed_artifact_hash="a"*64)


def test_direct_readiness_rejects_each_malformed_field_at_valid_length():
    from dataclasses import replace
    from live_readiness import LiveReadinessError, ReadinessCheck, ReadinessReport, ReadinessState, assert_live_start_allowed, required_live_checks
    import pytest
    digest = "a" * 64
    checks = tuple(ReadinessCheck(n, True, "ok", digest) for n in required_live_checks())
    base = ReadinessReport("i", ReadinessState.LIVE_ELIGIBLE, checks, digest)
    bad_reports = [replace(base, checks=(object(), *checks[1:])), replace(base, checks=(replace(checks[0], name=1), *checks[1:])), replace(base, checks=(replace(checks[0], passed=1), *checks[1:])), replace(base, checks=(replace(checks[0], reason=""), *checks[1:])), replace(base, artifact_hash="bad")]
    for report in bad_reports:
        with pytest.raises(LiveReadinessError): assert_live_start_allowed(report, deployed_artifact_hash=digest)
    for deployed in (1, "bad"):
        with pytest.raises(LiveReadinessError): assert_live_start_allowed(base, deployed_artifact_hash=deployed)
