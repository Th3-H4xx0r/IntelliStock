"""Tests for backtest.settle() + backtest.aggregate() — Task 7 of the Kalshi
backtest replay engine. settle() delegates to reconcile.reconcile_position
for settlement math (single-fill position, so the cost-weighted-average path
collapses to the plain formula). Kalshi charges the trading fee AT EXECUTION
regardless of outcome, so the fee applies on BOTH branches:
win -> (100-entry)*size - fee; loss -> -entry*size - fee."""
from kalshi.backtest import SizedBet, Trade, aggregate, settle
from kalshi.reconcile import _fee_cents, reconcile_position


def _bet(entry_cents=45, size=10, side="home", fused_fair=0.60, league="", kickoff=""):
    return SizedBet(
        side=side, market_ticker="KX-HOME", entry_cents=entry_cents, size=size,
        model_prob=0.55, sharp_prob=0.50, fused_fair=fused_fair, edge=0.05,
        league=league, kickoff=kickoff,
    )


def test_settle_win_math():
    bet = _bet(entry_cents=45, size=10)
    fee = _fee_cents(10, 45, 0.07)
    trade = settle(bet, "home", fee_rate=0.07)
    assert isinstance(trade, Trade)
    assert trade.outcome == "win"
    assert trade.realized_pnl_cents == (100 - 45) * 10 - fee
    # win-branch fee must match reconcile_position's own fee exactly.
    reconciled_win = reconcile_position(
        {"market_ticker": "KX-HOME", "contracts": 10, "avg_entry_cents": 45},
        result="yes", fee_rate=0.07,
    )
    assert trade.realized_pnl_cents == reconciled_win["realized_pnl_cents"]


def test_settle_loss_math():
    bet = _bet(entry_cents=45, size=10)
    fee = _fee_cents(10, 45, 0.07)
    trade = settle(bet, "away", fee_rate=0.07)
    assert trade.outcome == "loss"
    # Kalshi charges the fee on losses too (charged at execution, not at
    # settlement) — this is the Finding 1 fix: -entry*size - fee, not -450.
    assert trade.realized_pnl_cents == -450 - fee
    assert fee > 0
    reconciled_loss = reconcile_position(
        {"market_ticker": "KX-HOME", "contracts": 10, "avg_entry_cents": 45},
        result="no", fee_rate=0.07,
    )
    assert trade.realized_pnl_cents == reconciled_loss["realized_pnl_cents"]


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


def test_settle_copies_league_and_kickoff_onto_trade():
    bet = _bet(entry_cents=45, size=10, league="EPL", kickoff=1735689600)
    trade = settle(bet, "home", fee_rate=0.07)
    assert trade.league == "EPL"
    assert trade.kickoff == 1735689600


def test_aggregate_per_league_buckets_by_real_league_and_equity_orders_by_kickoff():
    # Two leagues, interleaved so kickoff order != insertion order matters.
    epl_bet = _bet(entry_cents=40, size=10, side="home", league="EPL", kickoff=200)
    laliga_bet = _bet(entry_cents=60, size=10, side="away", league="LaLiga", kickoff=100)
    epl_trade = settle(epl_bet, "home", fee_rate=0.0)   # win
    laliga_trade = settle(laliga_bet, "home", fee_rate=0.0)  # loss (result != side)

    # Caller (run_backtest) is responsible for kickoff ordering before calling
    # aggregate(); simulate that by sorting trades by kickoff here.
    trades = sorted([epl_trade, laliga_trade], key=lambda t: t.kickoff)
    result = aggregate(trades, bankroll_cents=100_000)

    assert set(result.per_league.keys()) == {"EPL", "LaLiga"}
    assert "unknown" not in result.per_league
    assert result.per_league["EPL"]["n_bets"] == 1
    assert result.per_league["EPL"]["wins"] == 1
    assert result.per_league["LaLiga"]["n_bets"] == 1
    assert result.per_league["LaLiga"]["wins"] == 0

    # equity curve follows the kickoff-sorted order: laliga (kickoff=100, loss
    # -600) settles before epl (kickoff=200, win +600).
    assert result.equity_curve == [laliga_trade.realized_pnl_cents,
                                   laliga_trade.realized_pnl_cents + epl_trade.realized_pnl_cents]


def test_aggregate_empty_trades():
    result = aggregate([], bankroll_cents=100_000)
    assert result.pnl_cents == 0
    assert result.n_bets == 0
    assert result.win_rate == 0.0
    assert result.equity_curve == []
    assert result.calibration == []
