"""Tests for backtest.settle() + backtest.aggregate() — Task 7 of the Kalshi
backtest replay engine. settle() follows reconcile.reconcile_position's
settlement math (single-fill position, so the cost-weighted-average path
collapses to the plain formula): win -> (100-entry)*size - fee;
loss -> -entry*size."""
from kalshi.backtest import SizedBet, Trade, aggregate, settle
from kalshi.fees import fee_cents_for_order


def _bet(entry_cents=45, size=10, side="home", fused_fair=0.60):
    return SizedBet(
        side=side, market_ticker="KX-HOME", entry_cents=entry_cents, size=size,
        model_prob=0.55, sharp_prob=0.50, fused_fair=fused_fair, edge=0.05,
    )


def test_settle_win_math():
    bet = _bet(entry_cents=45, size=10)
    fee = fee_cents_for_order(45, 10, 0.07)
    trade = settle(bet, "home", fee_rate=0.07)
    assert isinstance(trade, Trade)
    assert trade.outcome == "win"
    assert trade.realized_pnl_cents == (100 - 45) * 10 - fee


def test_settle_loss_math():
    bet = _bet(entry_cents=45, size=10)
    trade = settle(bet, "away", fee_rate=0.07)
    assert trade.outcome == "loss"
    assert trade.realized_pnl_cents == -450


def test_aggregate_pnl_winrate_equity_curve():
    win_bet = _bet(entry_cents=45, size=10, side="home")
    loss_bet = _bet(entry_cents=45, size=10, side="home")
    win_trade = settle(win_bet, "home", fee_rate=0.07)
    loss_trade = settle(loss_bet, "away", fee_rate=0.07)
    # Force exact figures per the brief's worked example (win 550, loss -450 —
    # i.e. the zero-fee case) so aggregate()'s arithmetic is checked precisely.
    win_trade = Trade(**{**win_trade.__dict__, "realized_pnl_cents": 550})
    loss_trade = Trade(**{**loss_trade.__dict__, "realized_pnl_cents": -450})

    result = aggregate([win_trade, loss_trade], bankroll_cents=100_000)
    assert result.pnl_cents == 100
    assert result.n_bets == 2
    assert result.win_rate == 0.5
    assert result.equity_curve == [550, 100]
    assert result.roi == 100 / 100_000
    assert isinstance(result.calibration, list)


def test_aggregate_calibration_buckets_predicted_vs_actual():
    high_conf_win = settle(_bet(entry_cents=80, size=5, fused_fair=0.85), "home", fee_rate=0.0)
    high_conf_loss = settle(_bet(entry_cents=80, size=5, fused_fair=0.85), "away", fee_rate=0.0)
    result = aggregate([high_conf_win, high_conf_loss], bankroll_cents=100_000)
    assert len(result.calibration) == 1
    bucket = result.calibration[0]
    assert bucket["n"] == 2
    assert bucket["actual_rate"] == 0.5
    assert abs(bucket["predicted_avg"] - 0.85) < 1e-9


def test_aggregate_empty_trades():
    result = aggregate([], bankroll_cents=100_000)
    assert result.pnl_cents == 0
    assert result.n_bets == 0
    assert result.win_rate == 0.0
    assert result.equity_curve == []
    assert result.calibration == []
