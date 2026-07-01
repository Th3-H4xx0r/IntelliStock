"""Tests for backtest.build_model_fn() — Task 9 of the Kalshi backtest replay
engine. Wires the SAME Elo -> expected-goals -> Dixon-Coles pricing chain the
live engine uses (quant.elo.elo_to_expected_goals ->
intelligence.pricing.model_market_probs's 'winner' group), picking national vs
club Elo per team exactly like engine.py's `_team_elo` closure."""
from kalshi.backtest import build_model_fn


def test_club_teams_three_way_sums_to_one():
    elo_table = {"Real Madrid": 1950.0, "Getafe": 1550.0}
    model_fn = build_model_fn(nat_elo_table={}, elo_table=elo_table)
    fx = {"fixture_id": "f1", "home": "Real Madrid", "away": "Getafe"}
    probs = model_fn(fx)
    assert set(probs.keys()) == {"home", "draw", "away"}
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    # Strong favorite at home -> home should be the most likely outcome.
    assert probs["home"] > probs["away"]
    assert probs["home"] > probs["draw"]


def test_national_teams_use_national_elo_table_and_sum_to_one():
    nat_elo_table = {"Argentina": 2200.0, "Iceland": 1400.0}
    model_fn = build_model_fn(nat_elo_table=nat_elo_table, elo_table={})
    fx = {"fixture_id": "f2", "home": "Argentina", "away": "Iceland"}
    probs = model_fn(fx)
    assert abs(sum(probs.values()) - 1.0) < 1e-9
    assert probs["home"] > probs["away"]


def test_unknown_club_team_falls_back_to_default_elo_and_still_sums_to_one():
    model_fn = build_model_fn(nat_elo_table={}, elo_table={})
    fx = {"fixture_id": "f3", "home": "Some Random FC", "away": "Another FC"}
    probs = model_fn(fx)
    assert abs(sum(probs.values()) - 1.0) < 1e-9
