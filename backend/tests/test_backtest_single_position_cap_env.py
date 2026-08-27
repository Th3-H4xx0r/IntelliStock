"""Per-instance BROKER_MAX_SINGLE_POSITION_PCT injection into the spawned
backtest container.

The value is read from the instance's linked Strategies document
(`strategies[].config.broker_max_single_position_pct`) and forwarded as a
container env var. It must be INERT for every instance that does not declare
the key, because the backtest engine is one shared service process and the
broker's default 0.15 is a real-money failsafe.

No test opens Postgres, Docker, or a broker connection.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest


def _engine():
    original_cwd = os.getcwd()
    try:
        from engines import backtest_engine as engine
    finally:
        os.chdir(original_cwd)
    return engine


def _strategy_doc(config):
    """A doc whose entry is ENABLED unless the test says otherwise.

    The helper honours the cap only on an entry that is actually switched on —
    the env var is process-wide inside the container, so a disabled entry must
    not lift the failsafe for its siblings.
    """
    cfg = {"strategy_x_enabled": True}
    cfg.update(config)
    return {"id": 195, "strategies": [{"strategy": "strategy_x", "config": cfg}]}


# --------------------------------------------------------------------------
# _instance_single_position_pct: value parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.95, 0.95),
        ("0.95", 0.95),
        (1, 1.0),
        (1.0, 1.0),
        (0.0001, 0.0001),
        (0, None),          # 0 disables the failsafe outright
        ("0", None),
        (-1, None),
        (2.0, None),
        ("abc", None),
        ("", None),         # explicit skip
        (None, None),       # explicit skip
        (float("nan"), None),
        (float("inf"), None),
    ],
)
def test_config_value_bounds(monkeypatch, raw, expected):
    engine = _engine()
    monkeypatch.setattr(
        engine.db_store, "get",
        lambda _t, _k: _strategy_doc({"broker_max_single_position_pct": raw}))
    got = engine._instance_single_position_pct(None, {"strategy_id": 195})
    assert got == expected or (got is None and expected is None)


def test_bool_true_is_rejected_rather_than_becoming_a_100_percent_cap(monkeypatch):
    """`float(True) == 1.0` would sail through the (0, 1] band and silently
    disable the trim — the exact outcome the bound exists to prevent."""
    engine = _engine()
    monkeypatch.setattr(
        engine.db_store, "get",
        lambda _t, _k: _strategy_doc({"broker_max_single_position_pct": True}))
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) is None


# --------------------------------------------------------------------------
# _instance_single_position_pct: shape / early-return paths
# --------------------------------------------------------------------------

def test_no_strategy_id_returns_none(monkeypatch):
    engine = _engine()

    def _boom(*_a, **_k):
        raise AssertionError("must not query Strategies without a strategy_id")

    monkeypatch.setattr(engine.db_store, "get", _boom)
    assert engine._instance_single_position_pct(None, {}) is None
    assert engine._instance_single_position_pct(None, None) is None


def test_missing_strategy_doc_returns_none(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(engine.db_store, "get", lambda _t, _k: None)
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) is None


def test_key_absent_from_config_returns_none(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(
        engine.db_store, "get",
        lambda _t, _k: _strategy_doc({"core_weight": 0.9}))
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) is None


def test_strategies_is_a_dict_not_a_list(monkeypatch):
    """A dict iterates as its KEYS (strings), which are not dicts -> skipped."""
    engine = _engine()
    monkeypatch.setattr(
        engine.db_store, "get",
        lambda _t, _k: {"strategies": {"a": {"config": {
            "broker_max_single_position_pct": 0.95}}}})
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) is None


def test_config_is_a_string_not_a_dict(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(
        engine.db_store, "get",
        lambda _t, _k: {"strategies": [{"config": "not-a-dict"}]})
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) is None


def test_first_declaring_entry_wins_and_a_bad_one_masks_a_later_good_one(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(engine.db_store, "get", lambda _t, _k: {"strategies": [
        {"config": {"broker_max_single_position_pct": 5.0}},   # out of band
        {"config": {"broker_max_single_position_pct": 0.95}},  # never reached
    ]})
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) is None


def test_entry_without_the_key_is_skipped_not_terminal(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(engine.db_store, "get", lambda _t, _k: {"strategies": [
        {"config": {"core_weight": 0.9}},
        {"config": {"strategy_x_enabled": True,
                    "broker_max_single_position_pct": 0.95}},
    ]})
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) == 0.95


def test_a_bad_entry_does_not_mask_a_later_good_one(monkeypatch):
    """An out-of-band value in entry 0 must not abort the scan — otherwise one
    malformed sibling silently reverts the whole run to the 0.15 default."""
    engine = _engine()
    monkeypatch.setattr(engine.db_store, "get", lambda _t, _k: {"strategies": [
        {"config": {"strategy_x_enabled": True,
                    "broker_max_single_position_pct": 5.0}},
        {"config": {"strategy_x_enabled": True,
                    "broker_max_single_position_pct": 0.95}},
    ]})
    assert engine._instance_single_position_pct(None, {"strategy_id": 195}) == 0.95


def test_store_error_is_swallowed_so_it_cannot_reach_the_callers_handler(monkeypatch):
    """db_store raises StoreError (not ValueError) on a malformed key or an
    unreachable pool. Letting it escape would be caught by the CALLER, which
    blanks brokerage credentials — so an unrelated cap lookup would strip a
    crypto backtest's keys."""
    engine = _engine()
    from db.errors import StoreError

    def _raise(_t, _k):
        raise StoreError("Strategies.id must be an integer, got 'x'")

    monkeypatch.setattr(engine.db_store, "get", _raise)
    assert engine._instance_single_position_pct(None, {"strategy_id": "x"}) is None


# --------------------------------------------------------------------------
# End-to-end: does it reach client.containers.run(environment=...)?
# --------------------------------------------------------------------------

def _run_backtest_capturing_env(monkeypatch, instance_doc, strategy_doc_or_exc,
                                row_extra=None, creds_fn=None):
    engine = _engine()

    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(engine, "_remove_row_and_mark_done", lambda _row_id: None)
    monkeypatch.setattr(engine, "_get_network", lambda _client: None)
    monkeypatch.setattr(engine, "_get_instance_doc", lambda _c, _i: instance_doc)
    monkeypatch.setattr(engine, "_resolve_data_brokerage_creds",
                        creds_fn or (lambda _c, _i: (None, None)))
    monkeypatch.setattr(engine, "get_conn",
                        lambda: SimpleNamespace(close=lambda: None))

    def _get(_table, _key):
        if callable(strategy_doc_or_exc):
            return strategy_doc_or_exc(_table, _key)
        return strategy_doc_or_exc

    monkeypatch.setattr(engine.db_store, "get", _get)

    captured = {}

    class _Images:
        def get(self, _image):
            return object()

    class _Containers:
        def get(self, _name):
            raise LookupError("absent")

        def run(self, image, **kwargs):
            captured["image"] = image
            captured.update(kwargs)
            return object()

    monkeypatch.setattr(
        engine, "_get_docker_client",
        lambda: SimpleNamespace(images=_Images(), containers=_Containers()))

    row = {
        "id": 123456,
        "instance": "stock",
        "stocks": ["SPY"],
        "start-date": "2026-01-01",
        "end-date": "2026-02-01",
        "granularity_sec": 3600,
        "initial_cash": 100000,
    }
    row.update(row_extra or {})
    engine.run_one_backtest(row)
    return captured


def test_declared_cap_reaches_the_container_environment(monkeypatch):
    captured = _run_backtest_capturing_env(
        monkeypatch,
        {"id": "stock", "kind": None, "strategy_id": 195},
        _strategy_doc({"broker_max_single_position_pct": 0.95}),
    )
    assert captured["environment"]["BROKER_MAX_SINGLE_POSITION_PCT"] == "0.95"


def test_undeclared_instance_gets_no_env_var_at_all(monkeypatch):
    """Today's 0.15 broker default must be preserved byte-for-byte."""
    captured = _run_backtest_capturing_env(
        monkeypatch,
        {"id": "stock", "kind": None, "strategy_id": 195},
        _strategy_doc({"core_weight": 0.9}),
    )
    assert "BROKER_MAX_SINGLE_POSITION_PCT" not in captured["environment"]


def test_instance_without_strategy_id_gets_no_env_var(monkeypatch):
    captured = _run_backtest_capturing_env(
        monkeypatch, {"id": "stock", "kind": None}, None)
    assert "BROKER_MAX_SINGLE_POSITION_PCT" not in captured["environment"]


def test_env_injection_is_not_clobbered_by_a_later_update(monkeypatch):
    """Guards the ordering: nothing after line ~755 may replace the env dict."""
    captured = _run_backtest_capturing_env(
        monkeypatch,
        {"id": "stock", "kind": None, "strategy_id": 195},
        _strategy_doc({"broker_max_single_position_pct": 0.5}),
    )
    env = captured["environment"]
    assert env["BROKER_MAX_SINGLE_POSITION_PCT"] == "0.5"
    # sanity: the keys written after it are present too, so we really did reach
    # the end of the builder and not an early return.
    assert env["BACKTEST_LOG_DIR"]
    assert env["NEO4J_URI"]


def test_a_disabled_strategy_x_does_not_raise_the_cap_for_its_siblings(monkeypatch):
    """The master switch IS consulted.

    strategies/strategy_x.py ships `strategy_x_enabled: false` alongside
    `broker_max_single_position_pct: 0.95`. The env var is process-wide inside
    the container, not lane-scoped, so honouring it on a disabled entry would
    lift the failsafe from 15% to 95% for every buy from every sibling strategy
    in that document while strategy_x itself does nothing.
    """
    captured = _run_backtest_capturing_env(
        monkeypatch,
        {"id": "stock", "kind": None, "strategy_id": 195},
        {"strategies": [
            {"strategy": "graph_nexus_analysis", "config": {"single_position_max_pct": 25}},
            {"strategy": "strategy_x", "config": {
                "strategy_x_enabled": False,
                "broker_max_single_position_pct": 0.95,
            }},
        ]},
    )
    assert "BROKER_MAX_SINGLE_POSITION_PCT" not in captured["environment"]


def test_classification_failure_does_not_ship_the_raised_cap(monkeypatch):
    """The outer `except` must fail closed on BOTH axes.

    It previously reset key/secret/kind but not _single_position_pct, so a run
    that logged "instance credential mode could not be classified" and dropped
    its credentials still launched with the failsafe lifted to 95% — fail-closed
    and fail-open in the same handler.
    """
    def _boom(_c, _i):
        raise RuntimeError("brokerage down")

    captured = _run_backtest_capturing_env(
        monkeypatch,
        {"id": "crypto-1", "kind": "crypto", "strategy_id": 195},
        _strategy_doc({"broker_max_single_position_pct": 0.95}),
        row_extra={"key": "CRYPTO_KEY", "secret": "CRYPTO_SECRET"},
        creds_fn=_boom,
    )
    assert captured["command"][7:9] == ["NULL", "NULL"]          # failed closed
    assert "BROKER_MAX_SINGLE_POSITION_PCT" not in captured["environment"]


_CRYPTO_ROW = {"instance": "crypto-1", "key": "CRYPTO_KEY", "secret": "CRYPTO_SECRET"}
_CRYPTO_DOC = {"id": "crypto-1", "kind": "crypto", "strategy_id": 195}


def test_control_crypto_backtest_normally_receives_its_credentials(monkeypatch):
    """Control arm for the regression below: the happy path DOES pass creds."""
    captured = _run_backtest_capturing_env(
        monkeypatch, _CRYPTO_DOC,
        _strategy_doc({"core_weight": 0.9}), row_extra=_CRYPTO_ROW)
    assert captured["command"][7:9] == ["CRYPTO_KEY", "CRYPTO_SECRET"]
    assert captured["environment"]["KEY"] == "CRYPTO_KEY"


def test_strategy_lookup_failure_does_not_drop_crypto_credentials(monkeypatch):
    """REGRESSION GUARD.

    The cap lookup runs BEFORE `_kind` is read, so a StoreError escaping its
    except tuple would land in run_one_backtest's outer handler, reset
    non_equity_compatibility and blank the credentials — a crypto backtest
    losing its keys to an unrelated single-position-cap lookup. The helper
    therefore catches broadly, matching its sibling lookups.
    """
    from db.errors import StoreError

    def _raise(_t, _k):
        raise StoreError("boom")

    captured = _run_backtest_capturing_env(
        monkeypatch, _CRYPTO_DOC, _raise, row_extra=_CRYPTO_ROW)
    env = captured["environment"]
    command = captured["command"]
    assert "BROKER_MAX_SINGLE_POSITION_PCT" not in env
    assert command[7:9] == ["CRYPTO_KEY", "CRYPTO_SECRET"], (
        "a cap-lookup failure must not cost the crypto run its credentials")
    assert env["KEY"] == "CRYPTO_KEY"


# --------------------------------------------------------------------------
# _instance_single_position_pct: the generalised `honour_single_position_cap`
# opt-in
#
# The env var it produces is process-wide inside the container, so the opt-in
# must still require an ENABLED entry — otherwise a dormant lane would lift the
# broker's 15% real-money failsafe for every sibling in the same document.
# --------------------------------------------------------------------------

def _eb_doc(**config):
    return {"id": 200, "strategies": [{"strategy": "strategy_eb",
                                       "config": dict(config)}]}


def _pct(monkeypatch, doc):
    engine = _engine()
    monkeypatch.setattr(engine.db_store, "get", lambda _t, _k: doc)
    return engine._instance_single_position_pct(None, {"strategy_id": 200})


def test_the_new_opt_in_honours_the_cap_without_strategy_x(monkeypatch):
    assert _pct(monkeypatch, _eb_doc(broker_max_single_position_pct=0.95,
                                     honour_single_position_cap=True)) == 0.95


@pytest.mark.parametrize("raw", ["true", "True", "yes", "on", "1"])
def test_a_string_opt_in_is_truthy(monkeypatch, raw):
    assert _pct(monkeypatch, _eb_doc(broker_max_single_position_pct=0.95,
                                     honour_single_position_cap=raw)) == 0.95


@pytest.mark.parametrize("raw", [False, "false", "no", "0", "", None])
def test_a_falsy_opt_in_leaves_the_failsafe_alone(monkeypatch, raw):
    assert _pct(monkeypatch, _eb_doc(broker_max_single_position_pct=0.95,
                                     honour_single_position_cap=raw)) is None


def test_the_strategy_x_opt_in_is_unchanged(monkeypatch):
    assert _pct(monkeypatch, _eb_doc(broker_max_single_position_pct=0.95,
                                     strategy_x_enabled=True)) == 0.95


def test_neither_opt_in_means_the_key_is_still_inert(monkeypatch):
    """Strategy XS's 0.65 has been inert since it shipped; that stays true
    until it declares the new key."""
    assert _pct(monkeypatch, _eb_doc(broker_max_single_position_pct=0.65)) is None


def test_the_bounds_still_apply_under_the_new_opt_in(monkeypatch):
    for raw in (0, -1, 2.0, True, "abc", "", None):
        assert _pct(monkeypatch, _eb_doc(broker_max_single_position_pct=raw,
                                         honour_single_position_cap=True)) is None, raw
