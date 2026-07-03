"""Background training worker: continuously refit the probability calibrator from
settled outcomes and promote it to champion ONLY when it beats the raw model on a
held-out split. Mirrors the backtest worker's self-heal loop (reconnect on a
dropped DB connection instead of dying) — PR #84 pattern.

`refit_once` is the pure-ish core (inject conn + samples + id/timestamp) so the
promotion gate is unit-tested without a DB. `start_worker` wraps it in the
resilient loop the runner starts as a daemon thread.
"""
from __future__ import annotations

import logging

from kalshi import db as _db, training

log = logging.getLogger("kalshi.training_worker")


def refit_once(conn, instance_id, train_samples, test_samples, *, new_id, now_iso,
               min_total: int = 100, promote: bool = True) -> dict:
    """Fit a calibrator on `train_samples`, score it on held-out `test_samples`,
    persist the version, and promote to champion IFF calibrated log-loss does not
    regress vs the raw model. Returns the version doc with a `promoted` flag.

    Falls back to train_samples for evaluation when no test split is given (thin
    data) — the promotion gate then just guards against an actively harmful fit."""
    doc = training.fit_calibrator(train_samples, min_total=min_total)
    eval_set = test_samples or train_samples
    metrics = training.evaluate(eval_set, doc)
    version = {
        "id": new_id, "instance_id": instance_id, "kind": "calibrator",
        "created_at": now_iso, "is_champion": False,
        "method": doc["method"], "calibrator": doc["calibrator"],
        "shrink_strength": doc["shrink_strength"], "n_samples": doc["n_samples"],
        "metrics": metrics,
        "reliability": training.reliability_buckets(eval_set, doc),
    }
    _db.save_model_version(conn, version)
    # Never ship a worse model: promote only if calibration helped (or is neutral)
    # AND we actually have data. Identity fits are neutral and harmless to promote,
    # but there's no point — require a real (isotonic/shrink) fit with samples.
    improved = (metrics["cal_logloss"] <= metrics["raw_logloss"] + 1e-9)
    promoted = bool(promote and doc["n_samples"] > 0 and doc["method"] != "identity" and improved)
    if promoted:
        _db.set_champion(conn, new_id, instance_id, "calibrator")
    version["promoted"] = promoted
    return version


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.utcnow().isoformat() + "Z"


def _new_id(instance_id: str) -> str:
    import uuid as _uuid
    return f"cal|{instance_id}|{_uuid.uuid4().hex[:12]}"


def build_refit_fn(*, provider_factory, model_fn, leagues, start_date,
                   end_date_fn, instance_id: str = "__default__", min_total: int = 100):
    """Return a `refit(conn) -> dict|None` that fetches settled fixtures, gathers
    per-side calibration samples, splits train/test by fixture (no leakage), and
    calls `refit_once`. `end_date_fn()` returns today's date string so the window
    rolls forward. Degrade-safe: returns None on any data failure."""
    def refit(conn):
        try:
            provider = provider_factory(conn)
            fixtures = [f for f in provider.fixtures(leagues, start_date, end_date_fn())
                        if (f or {}).get("result") in ("home", "draw", "away")]
        except Exception:
            log.exception("training: fixture fetch failed")
            return None
        if not fixtures:
            return None
        fixtures.sort(key=lambda f: str(f.get("kickoff_ts") or f.get("fixture_id") or ""))
        train_fx, test_fx = fixtures[::2], fixtures[1::2]
        train = training.gather_samples_from_settled(train_fx, model_fn)
        test = training.gather_samples_from_settled(test_fx, model_fn)
        return refit_once(conn, instance_id, train, test,
                          new_id=_new_id(instance_id), now_iso=_now_iso(),
                          min_total=min_total)
    return refit


def start_worker(conn_factory, refit_fn, *, refresh_secs: int = 3600, notify=None,
                 run_once_now: bool = True, _max_cycles=None):
    """Daemon loop: periodically call `refit_fn(conn)`; reconnect + retry on a
    dropped connection; alert the operator when a new champion is promoted.
    `_max_cycles` bounds the loop for tests."""
    import threading
    import time as _time

    def _alert(v):
        if not notify or not v or not v.get("promoted"):
            return
        m = v.get("metrics", {})
        try:
            notify(category="kalshi_runtime", instance_id=v.get("instance_id", ""),
                   title=f"KALSHI model recalibrated [{v.get('instance_id')}]",
                   body=(f"New champion calibrator ({v.get('method')}, {v.get('n_samples')} "
                         f"samples): held-out log-loss {m.get('raw_logloss'):.3f} -> "
                         f"{m.get('cal_logloss'):.3f}."),
                   discord_channel="notifications",
                   push_title="Kalshi model recalibrated",
                   push_body=f"log-loss {m.get('raw_logloss'):.3f} -> {m.get('cal_logloss'):.3f}")
        except Exception:
            pass

    def _loop():
        backoff = 2
        cycles = 0
        conn = None
        first = run_once_now
        while _max_cycles is None or cycles < _max_cycles:
            if not first:
                _time.sleep(refresh_secs)
            first = False
            cycles += 1
            try:
                if conn is None:
                    conn = conn_factory()
                v = refit_fn(conn)
                _alert(v)
                if v is not None:
                    log.info("training: refit ok (promoted=%s)", v.get("promoted"))
                backoff = 2
            except Exception as e:
                if _db.is_conn_error(e):
                    log.warning("training: DB connection lost (%s); reconnecting", type(e).__name__)
                    try:
                        conn = _db.reconnect(conn)
                    except Exception:
                        conn = None
                    _time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
                else:
                    log.exception("training: refit cycle failed")

    if _max_cycles is not None:  # synchronous for tests
        _loop()
        return None
    t = threading.Thread(target=_loop, name="kalshi-training-worker", daemon=True)
    t.start()
    log.info("kalshi training worker started")
    return t
