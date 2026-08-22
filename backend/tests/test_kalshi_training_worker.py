"""Training worker: the promotion gate (never ship a worse model) + self-heal loop."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi import training_worker, db


class FakeConn:
    pass


def _patch_registry(monkeypatch):
    saved, champs = [], []
    monkeypatch.setattr(db, "save_model_version", lambda conn, doc: saved.append(doc) or doc["id"])
    monkeypatch.setattr(db, "set_champion", lambda conn, id, inst, kind="calibrator": champs.append(id))
    return saved, champs


def test_promotes_when_calibration_improves(monkeypatch):
    saved, champs = _patch_registry(monkeypatch)
    # overconfident predictor across two levels: says 0.8 but wins ~50%, says 0.2 but
    # wins ~20% -> isotonic (needs >=2 distinct preds) corrects the 0.8 down.
    def data():
        return ([(0.8, 1.0 if i % 2 == 0 else 0.0) for i in range(60)]
                + [(0.2, 1.0 if i % 5 == 0 else 0.0) for i in range(60)])
    v = training_worker.refit_once(FakeConn(), "__default__", data(), data(),
                                   new_id="v1", now_iso="t", min_total=100)
    assert v["method"] == "isotonic"
    assert v["promoted"] is True and champs == ["v1"]


def test_does_not_promote_identity_or_no_data(monkeypatch):
    saved, champs = _patch_registry(monkeypatch)
    v = training_worker.refit_once(FakeConn(), "__default__", [], [],
                                   new_id="v0", now_iso="t")
    assert v["method"] == "identity" and v["promoted"] is False
    assert champs == []            # nothing promoted
    assert saved and saved[0]["id"] == "v0"   # but the version IS recorded


def test_does_not_promote_when_worse(monkeypatch):
    # Force a doc whose HELD-OUT calibrated log-loss is WORSE than raw (>= min_eval
    # samples so the holdout gate is satisfied and it's the regression that rejects).
    saved, champs = _patch_registry(monkeypatch)
    monkeypatch.setattr(training_worker.training, "fit_calibrator",
                        lambda s, **k: {"method": "shrink", "calibrator": None,
                                        "shrink_strength": 0.9, "n_samples": len(s)})
    monkeypatch.setattr(training_worker.training, "evaluate",
                        lambda s, doc: {"raw_logloss": 0.60, "cal_logloss": 0.80,
                                        "raw_brier": 0.2, "cal_brier": 0.3, "n_eval": len(s)})
    v = training_worker.refit_once(FakeConn(), "__default__", [(0.7, 1.0)] * 40, [(0.7, 1.0)] * 40,
                                   new_id="vbad", now_iso="t")
    assert v["promoted"] is False and champs == []   # worse model rejected


def test_no_promotion_without_holdout(monkeypatch):
    # CRITICAL: real overconfident data fits fine, but with NO held-out split the fit
    # is unvalidated (isotonic trivially "improves" in-sample) -> must NOT promote.
    saved, champs = _patch_registry(monkeypatch)
    train = ([(0.8, 1.0 if i % 2 == 0 else 0.0) for i in range(60)]
             + [(0.2, 1.0 if i % 5 == 0 else 0.0) for i in range(60)])
    v = training_worker.refit_once(FakeConn(), "__default__", train, [],
                                   new_id="v", now_iso="t", min_total=100)
    assert v["method"] == "isotonic" and v["held_out"] is False
    assert v["promoted"] is False and champs == []   # recorded, but NOT promoted
    assert saved and saved[0]["id"] == "v"


def test_no_promotion_when_holdout_too_small(monkeypatch):
    saved, champs = _patch_registry(monkeypatch)
    train = ([(0.8, 1.0 if i % 2 == 0 else 0.0) for i in range(60)]
             + [(0.2, 1.0 if i % 5 == 0 else 0.0) for i in range(60)])
    v = training_worker.refit_once(FakeConn(), "__default__", train, train[:10],  # 10 < min_eval 30
                                   new_id="v", now_iso="t", min_total=100, min_eval=30)
    assert v["promoted"] is False and champs == []


def test_start_worker_self_heals_on_db_outage(monkeypatch):
    # First cycle hits an unreachable DB, second succeeds -> the loop backs off
    # and retries instead of dying. The pool owns reconnection now, so there is
    # no handle to rebuild; UnavailableError is the signal.
    from db.errors import UnavailableError
    calls = {"n": 0}
    def refit(conn):
        calls["n"] += 1
        if calls["n"] == 1:
            raise UnavailableError("postgres unavailable")
        return {"promoted": False}
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)   # skip the 2s backoff
    training_worker.start_worker(lambda: FakeConn(), refit, refresh_secs=0,
                                 run_once_now=True, _max_cycles=2)
    assert calls["n"] == 2
