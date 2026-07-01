"""run_job orchestration tests — fake store + stubbed replay (no DB/network)."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi.backtest_worker import run_job
from kalshi.backtest import BacktestResult


class FakeStore:
    def __init__(self, run_flag=True):
        self.updates = []      # list of kwargs dicts
        self.saved = None
        self.run_flag = run_flag

    def update_backtest_progress(self, conn, jid, **kw):
        self.updates.append(kw)

    def save_backtest_result(self, conn, jid, result):
        self.saved = result

    def get_backtest(self, conn, jid):
        return {"id": jid, "run": self.run_flag}

    def statuses(self):
        return [u["status"] for u in self.updates if "status" in u]


def _result():
    r = BacktestResult(pnl_cents=550, roi=0.1, n_bets=1, win_rate=1.0, clv_avg=0.02,
                       equity_curve=[], per_league={}, calibration=[], trades=[])
    r.summary = {"api_calls": 3, "cache_hits": 0}
    return r


def test_run_job_finished_saves_result_and_summary():
    store = FakeStore()
    job = {"id": "b1", "config": {"leagues": ["World Cup"], "bankroll_cents": 5400}}

    def fake_run(cfg, provider, model_fn, progress_cb=None):
        progress_cb(0.5); progress_cb(1.0)
        return _result()

    status = run_job(job, conn=None, provider=object(), model_fn=object(),
                     store=store, run_fn=fake_run)
    assert status == "finished"
    assert store.statuses() == ["running", "finished"]
    assert store.saved.pnl_cents == 550
    final = [u for u in store.updates if u.get("status") == "finished"][0]
    assert final["progress"] == 100.0
    assert final["summary"]["pnl_cents"] == 550 and final["summary"]["api_calls"] == 3


def test_run_job_stopped_when_run_flag_cleared():
    store = FakeStore(run_flag=False)   # stop_check() -> True on first progress
    job = {"id": "b2", "config": {}}

    def fake_run(cfg, provider, model_fn, progress_cb=None):
        progress_cb(0.1)   # should raise _Stopped inside
        return _result()   # never reached

    status = run_job(job, conn=None, provider=object(), model_fn=object(),
                     store=store, run_fn=fake_run)
    assert status == "stopped"
    assert "stopped" in store.statuses()
    assert store.saved is None


def test_run_job_error_records_message():
    store = FakeStore()
    job = {"id": "b3", "config": {}}

    def fake_run(cfg, provider, model_fn, progress_cb=None):
        raise ValueError("boom")

    status = run_job(job, conn=None, provider=object(), model_fn=object(),
                     store=store, run_fn=fake_run)
    assert status == "error"
    err = [u for u in store.updates if u.get("status") == "error"][0]
    assert "boom" in err["error"]
