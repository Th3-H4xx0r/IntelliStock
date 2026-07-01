"""Pure doc-builder tests for backtest job/result rows (no DB)."""
import sys, types

# Stub socketio like the other kalshi tests (import side-effect guard).
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi.db import backtest_job_doc, backtest_result_doc


def test_backtest_job_doc_defaults_pending_running_zero_progress():
    d = backtest_job_doc(id="b1", brokerage_id="brk", instance_id="inst",
                         name="My BT", config={"edge_threshold": 0.03},
                         leagues=["World Cup"], start_date="2026-06-01",
                         end_date="2026-06-30", bankroll_cents=5400,
                         created_at="2026-07-01T00:00:00Z")
    assert d["id"] == "b1"
    assert d["status"] == "pending"
    assert d["run"] is True
    assert d["progress"] == 0.0
    assert d["config"] == {"edge_threshold": 0.03}
    assert d["leagues"] == ["World Cup"]
    assert d["bankroll_cents"] == 5400
    assert d["error"] is None
    assert d["summary"] == {}


def test_backtest_job_doc_empty_safe():
    d = backtest_job_doc(id="b2", brokerage_id="brk")
    assert d["leagues"] == [] and d["config"] == {} and d["bankroll_cents"] == 0


def test_backtest_result_doc_from_dataclass():
    from kalshi.backtest import Trade, BacktestResult
    tr = Trade(side="home", market_ticker="KX-A", entry_cents=45, size=10,
               model_prob=0.55, sharp_prob=0.5, fused_fair=0.52, edge=0.07,
               outcome="win", realized_pnl_cents=550, clv=0.01,
               market_type="winner", fixture_id="fx1", league="World Cup", kickoff=100)
    res = BacktestResult(pnl_cents=550, roi=0.1, n_bets=1, win_rate=1.0, clv_avg=0.01,
                         equity_curve=[{"ts": 100, "cum_pnl_cents": 550}],
                         per_league={"World Cup": {"n": 1, "pnl_cents": 550}},
                         calibration=[{"bucket_lo": 0.5, "bucket_hi": 0.6, "predicted": 0.55, "actual": 1.0, "n": 1}],
                         trades=[tr])
    res.summary = {"api_calls": 3, "cache_hits": 0}
    d = backtest_result_doc("b1", res)
    assert d["id"] == "b1"
    assert d["pnl_cents"] == 550 and d["n_bets"] == 1
    assert d["trades"][0]["side"] == "home" and d["trades"][0]["realized_pnl_cents"] == 550
    assert d["per_league"]["World Cup"]["pnl_cents"] == 550
    assert d["summary"] == {"api_calls": 3, "cache_hits": 0}


def test_backtest_result_doc_from_dict():
    d = backtest_result_doc("b3", {"pnl_cents": -100, "trades": [{"side": "away"}]})
    assert d["pnl_cents"] == -100 and d["trades"][0]["side"] == "away"
    assert d["roi"] == 0.0 and d["equity_curve"] == []
